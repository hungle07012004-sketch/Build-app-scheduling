"""Opt-in V5 MinTardy integration; reuse validated research search, not its data."""
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import time
from backend.services.v5_service import standardize, validate

MODE='MIN_TARDY_GROUPED_LNS_V5'


def long_path(path):
    path=Path(path).resolve()
    return Path('\\\\?\\'+str(path)) if os.name=='nt' and not str(path).startswith('\\\\?\\') else path


def object_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def model_version():
    from backend.solver_v5 import benchmark_common,benchmark_common_fast
    return object_hash({Path(m.__file__).name:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (benchmark_common,benchmark_common_fast)})


def diagnostics(raw,data):
    jobs={j['job_id']:j for j in data['jobs']};done={};pre={};pre_end={};pre_ready={};two_b={};vis={};pooled=[];setup=[]
    for r in raw:
        if r.get('Operation_Code')=='PRE':
            pre[r['Job_ID']]=r['Start_Min'];pre_end[r['Job_ID']]=r['Completion_Min'];pre_ready[r['Job_ID']]=r['Ready_Min']
        if r.get('Operation_Code')=='2B':two_b[r['Job_ID']]=r['Completion_Min']
        if r.get('Operation_Code')=='VISUAL':vis[r['Job_ID']]=r['Completion_Min']
        if r['Event_Type']=='2C_BATCH':
            for j in r['Job_ID'].split(';'):done[j]=max(done.get(j,0),r['Completion_Min'])
            if r.get('Batch_Composition')=='POOLED_5+5':pooled.append(r)
        if r['Event_Type']=='SETUP':setup.append(r)
    if set(done)!=set(jobs) or set(pre)!=set(jobs):raise ValueError('Missing final2C/PRE coverage')
    tardy=sum(done[j]>jobs[j]['due_min']+1e-6 for j in jobs)
    lag={q['job_id']:q['lag_min'] for q in data['precedence'] if q['pred']=='2B' and q['succ']=='PRE'}
    basic={j:max(jobs[j]['release_min'],jobs[j]['element_ready_min'],two_b[j]+lag[j]) for j in jobs}
    return dict(total_jobs=len(jobs),tardy_jobs=tardy,tardy_percent=100*tardy/len(jobs),
        otd_percent=100*(len(jobs)-tardy)/len(jobs),N_pre_overdue=sum(pre[j]>jobs[j]['due_min']+1e-6 for j in jobs),
        total_tardiness_min=sum(max(0,done[j]-jobs[j]['due_min']) for j in jobs),makespan_min=max(done.values()),
        pooled_batches=len(pooled),partner_visual_gap_min=sum(abs(vis[r['Job_ID'].split(';')[0]]-vis[r['Job_ID'].split(';')[1]]) for r in pooled),
        setup_count=len(setup),setup_processing_min=sum(r['Processing_Min'] for r in setup),
        setup_slot_wait_min=sum(r['Start_Min']-r['Ready_Min'] for r in setup),
        clean_clearance_exposed_min=sum(max(0,r['Ready_Min']-max(basic[r['Job_ID']],pre_end[r['Predecessor_Job_ID']])) for r in setup),
        sequence_setup_gate_exposed_min=sum(max(0,pre_ready[j]-basic[j]) for j in jobs))


def checked_raw(raw,data):
    metrics=diagnostics(raw,data)
    checked=standardize(MODE,dict(schedule=raw,**metrics,objective='MIN_TARDY_LEX'),data)
    if not checked['feasible']:raise ValueError(checked['validation_issues'])
    return metrics


def key(metrics):return (metrics['tardy_jobs'],metrics['total_tardiness_min'],metrics['makespan_min'])


def load_incumbent(path,data,digest):
    if not path.exists():return None
    value=json.loads(gzip.decompress(path.read_bytes()))
    if value.get('input_sha256')!=digest or value.get('model_version')!=model_version():return None
    if value.get('due_sha256')!=object_hash([(j['job_id'],j['due_min']) for j in data['jobs']]):return None
    metrics=checked_raw(value['raw_schedule'],data)
    if metrics!=value['metrics']:raise ValueError('Corrupt incumbent metrics')
    return value


def store_incumbent(path,data,digest,raw,metrics):
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_suffix('.lock')
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:return False
    try:
        os.close(fd)
        old=load_incumbent(path,data,digest)
        if old and key(old['metrics'])<=key(metrics):return False
        value=dict(input_sha256=digest,model_version=model_version(),
            due_sha256=object_hash([(j['job_id'],j['due_min']) for j in data['jobs']]),raw_schedule=raw,metrics=metrics)
        temporary=path.with_suffix('.tmp')
        temporary.write_bytes(gzip.compress(json.dumps(value).encode()));temporary.replace(path)
        return True
    finally:lock.unlink(missing_ok=True)


def solve(path,settings,*,warm_chromosome=None,evidence_dir=None,incumbent_dir=None):
    from experiments.due_group_guided_lns_v5.guidance import search_class,search
    from backend.solver_v5 import solve_improved_ga_instance as ga
    path=Path(path);original=path.read_bytes();digest=hashlib.sha256(original).hexdigest()
    data=json.loads(original);issues=validate(data)
    if issues:raise ValueError(issues)
    reference_warm=False
    if warm_chromosome is None and data.get('metadata',{}).get('on_time_reference'):
        from backend.services.deadline_service import load_on_time_reference
        reference_raw,_ = load_on_time_reference(data, path.parents[2])
        warm_chromosome=[r['Job_ID'] for r in reference_raw if r.get('Operation_Code')=='PRE']
        reference_warm=True
    n=len(data['jobs']);budget=int(settings.get('grouped_max_decodes',12000));days=int(settings.get('due_group_days',3))
    if not 6<=n<=255:raise ValueError('Grouped LNS currently supports 6..255 jobs')
    if days not in (0,1,3,7) or budget<32 or (n<=8 and budget>math.factorial(n)):
        raise ValueError('Invalid grouping window or unique decoder budget')
    root=long_path(Path(evidence_dir) if evidence_dir else path.parent/'glns')
    root.mkdir(parents=True,exist_ok=False)
    cache_root=long_path(Path(incumbent_dir) if incumbent_dir else path.parent.parent.parent/'grouped_lns_incumbents')
    cache_path=cache_root/(digest+'.json.gz')
    use_cache=bool(settings.get('grouped_use_incumbent',False))
    incumbent=load_incumbent(cache_path,data,digest) if use_cache else None
    started=time.perf_counter();runs=[]
    for attempt in range(int(settings.get('runs',1))):
        seed=int(settings.get('seed_base',42))+attempt*1009
        folder=root/f'seed-{seed}';folder.mkdir()
        Class,repair=search_class(data,days)
        # The inherited engine's research validator was specific to166 jobs.
        # Override ONLY admission validation with the original generic App C0
        # validator; keep Search.run, objective, screening, archive and restarts.
        class AppSearch(Class):
            def validate_elite(self,ch,metrics,raw):
                tick=time.perf_counter();verified=checked_raw(raw,self.data)
                if key(verified)!=(metrics[2],metrics[1],metrics[0]):raise ValueError('Optimizer/validator metrics differ')
                self.validation_wall+=time.perf_counter()-tick;self.validations+=1
                return search.Elite(ch,metrics,search.guidance(self.dec,raw),gzip.compress(json.dumps(raw).encode()),dict(status='PASS'))
        # Build input-only seeds when no exact-input incumbent is enabled.
        from experiments.min_tardy_enhanced_v5.search import decoder
        dec=decoder(data)
        candidate=warm_chromosome
        if candidate is None and incumbent:
            candidate=[r['Job_ID'] for r in incumbent['raw_schedule'] if r.get('Operation_Code')=='PRE']
        if candidate is not None:
            if len(candidate)!=n or set(candidate)!=set(dec.jobs):raise ValueError('Warm start does not match exact job IDs')
            warm=bytes(dec.job_index[j] for j in candidate)
        else:
            seeds=ga._deterministic_seeds(dec,True)
            warm=min(seeds,key=lambda ch:ga.structural_surrogate(ch,dec))
        obj=AppSearch(data,warm,seed,budget,folder,lambda message:print(message,flush=True))
        result=obj.run();raw=result['raw_schedule'];metrics=checked_raw(raw,data)
        if result['counts']['unique_full_decodes']!=budget:raise ValueError('Incomplete decoder budget')
        (folder/'winner_raw.json.gz').write_bytes(gzip.compress(json.dumps(raw).encode()))
        (folder/'trace.json.gz').write_bytes(gzip.compress(json.dumps(result['trace']).encode()))
        (folder/'audits.json').write_text(json.dumps(result['audits'],default=str),encoding='utf-8')
        runs.append(dict(raw=raw,metrics=metrics,chromosome=result['chromosome'],seed=seed,counts=result['counts'],runtime_sec=result['runtime_sec']))
    best=min(runs,key=lambda r:key(r['metrics']));search_metrics=best['metrics'];raw=best['raw']
    fallback=bool(incumbent and key(incumbent['metrics'])<key(search_metrics))
    if fallback:raw=incumbent['raw_schedule']
    metrics=checked_raw(raw,data)
    updated=store_incumbent(cache_path,data,digest,raw,metrics) if use_cache else False
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Frozen input changed during optimization')
    return dict(algorithm=MODE,objective='MIN_TARDY_LEX',feasible=True,schedule=raw,**metrics,
        best_chromosome=[r['Job_ID'] for r in raw if r.get('Operation_Code')=='PRE'],
        original_input_sha256=digest,model_version=model_version(),settings_sha256=object_hash(settings),
        due_group_days=days,grouped_use_incumbent=use_cache,incumbent_fallback=fallback,incumbent_updated=updated,
        search_output_metrics=search_metrics,returned_metrics=metrics,
        unique_full_decoder_calls=sum(r['counts']['unique_full_decodes'] for r in runs),
        audit_decoder_calls=sum(r['counts']['audit_decodes'] for r in runs),
        initialization_decoder_calls=24*len(runs),export_validation_decoder_calls=0,
        best_seed=best['seed'],runs=len(runs),completed_runs=len(runs),on_time_reference_warm_start=reference_warm,
        runtime_sec=time.perf_counter()-started,run_summaries=[{k:v for k,v in r.items() if k not in ('raw','chromosome')} for r in runs])
