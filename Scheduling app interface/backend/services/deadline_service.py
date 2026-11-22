"""Completion-based deadline scenarios; never rewrite source runs or datasets."""
import copy
import gzip
import hashlib
import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from backend.database.db import dump
from backend.errors import AppError


def register_on_time_reference(data, raw):
    """Store a physical witness separately; input due dates remain untouched."""
    from backend.services import input_service
    from backend.services.algorithms.grouped_lns_adapter import checked_raw
    metrics = checked_raw(raw, data)
    if metrics['tardy_jobs'] or metrics['total_tardiness_min']:
        raise AppError('REFERENCE_NOT_ON_TIME', 'Reference must meet every current deadline.', status=409)
    content = gzip.compress(dump(raw).encode('utf-8'))
    identifier = uuid.uuid4().hex
    folder = input_service.DATA_DIR/'deadline_references'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/(identifier+'.json.gz')).write_bytes(content)
    data['metadata']['on_time_reference'] = dict(id=identifier,sha256=hashlib.sha256(content).hexdigest(),
        policy='KEEP_VALIDATED_ZERO_TARDY_INCUMBENT',makespan_min=metrics['makespan_min'])
    return metrics


def load_on_time_reference(data, storage_root=None):
    reference = data.get('metadata', {}).get('on_time_reference')
    if not reference:
        return None
    import json
    import re
    from backend.services import input_service
    from backend.services.algorithms.grouped_lns_adapter import checked_raw
    identifier=reference.get('id','')
    if not re.fullmatch('[0-9a-f]{32}', identifier):
        raise ValueError('Invalid on-time reference identifier')
    content=((Path(storage_root) if storage_root else input_service.DATA_DIR)/'deadline_references'/(identifier+'.json.gz')).read_bytes()
    if hashlib.sha256(content).hexdigest()!=reference['sha256']:
        raise ValueError('On-time reference hash mismatch')
    raw=json.loads(gzip.decompress(content))
    metrics=checked_raw(raw,data)
    if metrics['tardy_jobs'] or metrics['total_tardiness_min']:
        raise ValueError('Reference no longer satisfies this frozen input and deadlines')
    return raw,metrics


def keep_on_time_result(algorithm, result, data, reference):
    if reference is None:
        return result
    from backend.services.v5_service import standardize
    raw, metrics = reference
    # User-selected on-time policy also retains the reference Cmax if search is worse.
    retain = (not result.get('feasible') or result.get('tardy_jobs') != 0
              or result.get('total_tardiness_min',float('inf')) > metrics['total_tardiness_min']
              or result['makespan_min'] > metrics['makespan_min'])
    search = dict(feasible=result.get('feasible'),tardy_jobs=result.get('tardy_jobs'),
                  makespan_min=result.get('makespan_min'),total_tardiness_min=result.get('total_tardiness_min'))
    if retain:
        previous=result
        result=standardize(algorithm,dict(schedule=raw,feasible=True,**metrics,
                                         runtime_sec=previous.get('runtime_sec')),data)
        result['metadata']['search_metadata']=previous.get('metadata',{})
        for key in ('unique_full_decoder_calls','completed_runs','runs','due_group_days'):
            if key in previous.get('metadata',{}):result['metadata'][key]=previous['metadata'][key]
        result['status']='Feasible — validated reference retained'
    result['metadata'].update(on_time_reference_policy=True,deadline_reference_retained=retain,
        returned_origin='VALIDATED_REFERENCE' if retain else 'SEARCH',search_produced_metrics=search,
        reference_cmax_min=metrics['makespan_min'])
    assert result['feasible'] and result['tardy_jobs']==0
    return result


def completions(operations, job_ids):
    done = {}
    for op in operations:
        if op.get('eventType') != '2C_BATCH':
            continue
        end = op.get('endMin')
        if not isinstance(end, (int, float)) or not math.isfinite(end):
            raise AppError('INVALID_SCHEDULE', 'Final 2C completion must be finite.', status=409)
        for jid in op.get('jobIds') or op['jobId'].split(';'):
            done[jid] = max(done.get(jid, float('-inf')), end)
    if set(done) != set(job_ids):
        raise AppError('INVALID_SCHEDULE', 'Final 2C contributors must cover exactly the frozen jobs.', status=409)
    return done


def comparison(data, operations):
    """Both commitment vectors, using all actual final-2C contributor completions."""
    done = completions(operations, [j['job_id'] for j in data['jobs']])
    rows = []
    for j in data['jobs']:
        c = done[j['job_id']]
        old = j.get('original_due_min', j['due_min'])
        rows.append(dict(job_id=j['job_id'], original_due_min=old, due_min=j['due_min'],
                         completion_min=c, original_tardy=c > old+1e-6,
                         adjusted_tardy=c > j['due_min']+1e-6))
    return dict(source_run_id=data['metadata'].get('deadline_relaxation', {}).get('source_run_id'),
                original_tardy_jobs=sum(r['original_tardy'] for r in rows),
                adjusted_tardy_jobs=sum(r['adjusted_tardy'] for r in rows),
                original_total_tardiness_min=sum(max(0,r['completion_min']-r['original_due_min']) for r in rows),
                adjusted_total_tardiness_min=sum(max(0,r['completion_min']-r['due_min']) for r in rows),
                jobs=rows)


def prepare(run):
    from backend.services.schedule_service import validate_saved_selection
    from backend.services.v5_service import is_v5, validate
    import json
    if run['status'] != 'completed':
        raise AppError('RUN_NOT_COMPLETED', 'Choose a completed feasible run.', status=409)
    validate_saved_selection(run)
    data = json.loads(Path(run['instance_path']).read_text(encoding='utf-8'))
    if not is_v5(data):
        raise AppError('UNSUPPORTED_SCHEMA', 'Completion-based deadlines require a V5 schedule.', status=409)
    if data['metadata'].get('deadline_relaxation'):
        raise AppError('DEADLINES_ALREADY_RELAXED', 'Deadlines are already fixed from a reference schedule. Use the original run to create another scenario.', status=409)
    selected = run['result']['selected']
    done = completions(selected['operations'], [j['job_id'] for j in data['jobs']])
    adjusted = copy.deepcopy(data)
    origin = datetime.fromisoformat(data['metadata']['origin_datetime'])
    changes = []
    for job in adjusted['jobs']:
        jid, old = job['job_id'], job['due_min']
        new = done[jid] if done[jid] > old+1e-6 else old
        job['original_due_min'] = old
        job['original_due_datetime'] = job.get('due_datetime', (origin+timedelta(minutes=old)).isoformat(sep=' '))
        if new != old:
            job['due_min'] = new
            job['due_datetime'] = (origin+timedelta(minutes=new)).isoformat(sep=' ', timespec='microseconds')
        changes.append(dict(job_id=jid, original_due_min=old, adjusted_due_min=new,
                            completion_min=done[jid], extension_min=new-old))
    adjusted['metadata']['deadline_relaxation'] = dict(policy='TARDY_TO_REFERENCE_FINAL_2C',
        source_run_id=run['id'], source_dataset_id=run['dataset_id'],
        source_instance_sha256=run['instance_sha256'], source_algorithm=selected['algorithm'],
        reference_cmax_min=max(done.values()), changed_jobs=sum(r['extension_min']>0 for r in changes))
    issues = validate(adjusted)
    if issues:
        raise AppError('INVALID_DATASET', 'Relaxed deadline scenario failed input validation.', issues)
    preview = dict(changed_jobs=sum(r['extension_min']>0 for r in changes), total_jobs=len(changes),
                   reference_cmax_min=max(done.values()), reference_original_tardy_jobs=sum(r['extension_min']>0 for r in changes),
                   reference_adjusted_tardy_jobs=0, changes=changes,
                   message='Only deadlines change. This fixed reference schedule becomes on time; a new optimization run may still have tardy jobs.')
    return adjusted, preview


def create(run):
    from backend.services import input_service
    data, preview = prepare(run)
    content = dump(data).encode('utf-8')
    folder = input_service.DATA_DIR / 'uploads'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex+'.json')
    path.write_bytes(content)
    dataset = input_service.persist('relaxed-deadlines-'+run['id'][:12]+'.json', path,
                                    hashlib.sha256(content).hexdigest(), data)
    return dict(dataset=dataset, **preview)
