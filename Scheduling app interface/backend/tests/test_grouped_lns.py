import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import pytest
from backend.models.schemas import SolverSettings
from backend.services.algorithms import grouped_lns_adapter as adapter
from backend.services import optimization_service as optimization
from backend.services.result_standardizer import standardize,select_best
from backend.services.instance_service import route
from backend.tests.test_v5 import instance
from backend.tests.test_integration import wait_result


def eight_jobs():
    data=instance();original=copy.deepcopy(data)
    for field in ('jobs','job_operations','precedence','oil_cycles'):
        for entry in original[field]:
            item=copy.deepcopy(entry);old=item['job_id'];item['job_id']=old+'X'
            if 'oil_cycle_id' in item:item['oil_cycle_id']=item['oil_cycle_id']+'X'
            data[field].append(item)
    data['metadata']['size']=8
    return data


def test_opt_in_settings_and_legacy_default():
    data=eight_jobs()
    assert route(8,data)['algorithms']==['CP-SAT']
    assert route(8,data)['availability'][adapter.MODE]['available']
    assert SolverSettings().due_group_days==3
    assert not SolverSettings().grouped_use_incumbent
    with pytest.raises(ValueError):SolverSettings(due_group_days=2)


def test_generic_solver_replay_original_dues_and_validator(tmp_path):
    data=eight_jobs();path=tmp_path/'input.json';path.write_text(json.dumps(data));before=path.read_bytes()
    settings=SolverSettings(grouped_max_decodes=32,due_group_days=3).model_dump()
    a=adapter.solve(path,settings,evidence_dir=tmp_path/'a')
    b=adapter.solve(path,settings,evidence_dir=tmp_path/'b')
    assert a['schedule']==b['schedule'] and a['best_chromosome']==b['best_chromosome']
    assert a['unique_full_decoder_calls']==32 and a['audit_decoder_calls']==2
    assert path.read_bytes()==before
    checked=standardize(adapter.MODE,a,data)
    assert checked['feasible'] and checked['objective']=='MIN_TARDY_LEX'
    assert checked['tardy_jobs']==adapter.diagnostics(a['schedule'],data)['tardy_jobs']
    invalid=copy.deepcopy(a['schedule']);batch=next(r for r in invalid if r['Event_Type']=='2C_BATCH');batch['Batch_Key']='WRONG'
    with pytest.raises(ValueError):adapter.checked_raw(invalid,data)
    invalid=copy.deepcopy(a['schedule']);next(r for r in invalid if r.get('Operation_Code')=='PRE')['Start_Min']=-1
    with pytest.raises(ValueError):adapter.checked_raw(invalid,data)
    digest=hashlib.sha256(before).hexdigest();cache=tmp_path/'cache'/'known.json.gz'
    assert adapter.store_incumbent(cache,data,digest,a['schedule'],adapter.diagnostics(a['schedule'],data))
    assert not adapter.store_incumbent(cache,data,digest,a['schedule'],adapter.diagnostics(a['schedule'],data))
    assert adapter.load_incumbent(cache,data,'different-input') is None
    changed=copy.deepcopy(data);changed['jobs'][0]['due_min']+=1
    assert adapter.load_incumbent(cache,changed,digest) is None
    assert adapter.load_incumbent(cache,data,digest)['metrics']==a['returned_metrics']


def test_api_selection_persistence_and_export(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(optimization,'ProcessPoolExecutor',lambda **kw:ThreadPoolExecutor(max_workers=1))
    data=eight_jobs()
    ds=client.post('/api/input/upload',files={'file':('grouped.json',json.dumps(data),'application/json')}).json()['id']
    body=dict(dataset_id=ds,job_ids=[j['job_id'] for j in data['jobs']],horizon_start='2026-10-01T06:00',
              horizon_end='2026-10-02T06:00',algorithms=[adapter.MODE],settings={'grouped_max_decodes':32,'due_group_days':1})
    preview=client.post('/api/schedules/preview',json=body)
    assert preview.status_code==200 and preview.json()['algorithms']==[adapter.MODE]
    response=client.post('/api/schedules/run',json=body)
    assert response.status_code==202,response.text
    result=wait_result(client,response.json()['run_id'])
    assert result['selected']['algorithm']==adapter.MODE and result['selected']['feasible']
    assert result['selected']['metadata']['total_jobs']==8
    assert result['selected']['metadata']['unique_full_decoder_calls']==32
    saved=client.get('/api/schedules/'+result['run_id']+'/result?validate_export=true')
    assert saved.status_code==200
    excel=client.get('/api/schedules/'+result['run_id']+'/export.xlsx')
    assert excel.status_code==200 and excel.content.startswith(b'PK')
    from io import BytesIO
    from openpyxl import load_workbook
    book=load_workbook(BytesIO(excel.content),read_only=True)
    summary=dict(book['Schedule Summary'].values)
    assert summary['Selected scheduling method']=='Min Tardy - Due-Date Grouping + LNS'
    assert summary['Original due grouping (calendar days)']==1
    assert summary['Unique full decoder calls']==32
    book.close()


def test_mixed_objectives_explicit_selection():
    legacy=dict(algorithm='SS Micro-GA Fast',feasible=True,objective='MIN_CMAX_FINAL_2C',makespan_min=10,tardy_jobs=3,total_tardiness_min=50)
    new=dict(algorithm=adapter.MODE,feasible=True,objective='MIN_TARDY_LEX',makespan_min=20,tardy_jobs=2,total_tardiness_min=100)
    assert select_best([legacy,new])[0] is new
    assert select_best([legacy])[0] is legacy


def test_soft_group_boundaries_readiness_and_slot_guidance():
    from experiments.due_group_guided_lns_v5.guidance import GroupGuidedRepair
    from experiments.min_tardy_lns_improvement_v5.feasibility_aware_repair import FeasibilityAwareLNSRepair
    from experiments.due_date_grouping_ga_v5.due_date_grouping import Estimates
    import random
    data=eight_jobs()
    for i,j in enumerate(data['jobs']):j['due_min']=i*1440.0
    # An early-due but unavailable job must not impose a group precedence.
    data['jobs'][0]['release_min']=100000
    before=copy.deepcopy(data)
    seq=list(range(8));block=seq[3:5];rest=[g for g in seq if g not in block]
    base=FeasibilityAwareLNSRepair(data)
    for days in (1,3,7):
        repair=GroupGuidedRepair(data,days)
        assert all(r['due_max']-r['due_min']<=days*1440 for r in repair.groups.records())
        assert set(base.slots(rest,block,seq))<=set(repair.slots(rest,block,seq))
        order=Estimates(data).sequence(repair.groups,'batch',random.Random(42))
        assert set(order)=={j['job_id'] for j in data['jobs']}
        assert order[0]!=data['jobs'][0]['job_id']
    assert data==before


def test_actual_spawn_worker_contract(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context
    path=tmp_path/'instance.json';path.write_text(json.dumps(eight_jobs()),encoding='utf-8')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    settings=SolverSettings(grouped_max_decodes=32,due_group_days=7).model_dump()
    with ProcessPoolExecutor(max_workers=1,mp_context=get_context('spawn')) as pool:
        result=pool.submit(optimization.worker,adapter.MODE,str(path),digest,settings).result(timeout=60)
    assert result['feasible'],result
    assert result['instance_sha256']==digest
    assert result['metadata']['settings_sha256']==adapter.object_hash(settings)
    assert result['metadata']['unique_full_decoder_calls']==32


def test_progress_reports_min_tardy_and_preserves_legacy(tmp_path):
    from backend.services.progress_service import log_path,read_progress
    path=tmp_path/'instance.json'
    log_path(path,adapter.MODE).write_text('progress seed=42 calls=256/1024 best=(119, 2969429.68, 69928.5) archive=12 restarts=0\n')
    progress=read_progress(path,adapter.MODE)
    assert progress['iteration']==256 and progress['limit']==1024
    assert progress['best_tardy_jobs']==119 and progress['best_makespan_min']==69928.5
    log_path(path,'SS Micro-GA Fast').write_text('epoch 12/24 best=100.5\n')
    assert read_progress(path,'SS Micro-GA Fast')['best_makespan_min']==100.5
