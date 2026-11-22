import copy
import gzip
import hashlib
import json
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
import pytest
from backend.database.db import connect, dump, now
from backend.services import v5_service
from backend.services.deadline_service import completions
from backend.errors import AppError
from backend.tests.test_integration import wait_result

ROOT=Path(__file__).resolve().parents[2]


def seed_reference(client):
    content=(ROOT/'experiments/app_grouped_lns_verification_180_v5/results/frozen180.json').read_bytes()
    response=client.post('/api/input/upload',files={'file':('original180.json',content,'application/json')})
    assert response.status_code==200,response.text
    dataset_id=response.json()['id']
    with connect() as db:
        data=json.loads(db.execute('SELECT instance FROM datasets WHERE id=?',(dataset_id,)).fetchone()[0])
    raw=json.loads(gzip.decompress((ROOT/'experiments/cmax_terminal_lns_v5/known-warm-results/winner_raw.json.gz').read_bytes()))
    selected=v5_service.standardize('SS Micro-GA Fast',dict(schedule=raw,makespan_min=69117.91699999997),data)
    assert selected['feasible'] and selected['tardy_jobs']==123
    from backend.services import input_service
    snapshot=input_service.DATA_DIR/'reference.json'
    snapshot.write_text(dump(data),encoding='utf-8')
    request=dict(dataset_id=dataset_id,job_ids=[j['job_id'] for j in data['jobs']],
                 horizon_start=data['metadata']['origin_datetime'],horizon_end=data['metadata']['horizon_end_datetime'],
                 algorithms=['SS Micro-GA Fast'],settings={})
    result=dict(selected=selected,algorithms=[selected],selection_reason='Retained validated test witness')
    with connect() as db:
        db.execute('INSERT INTO runs(id,dataset_id,created_at,status,request,instance_path,instance_sha256,result) VALUES(?,?,?,?,?,?,?,?)',
                   ('reference',dataset_id,now(),'completed',dump(request),str(snapshot),hashlib.sha256(snapshot.read_bytes()).hexdigest(),dump(result)))
    return data,request,snapshot


def test_exact180_preview_create_rerun_export(client):
    original,request,snapshot=seed_reference(client)
    saved=snapshot.read_bytes()
    preview=client.get('/api/schedules/reference/relaxed-deadlines')
    assert preview.status_code==200,preview.text
    assert preview.json()['changed_jobs']==123
    with connect() as db:
        assert db.execute('SELECT count(*) FROM datasets').fetchone()[0]==1
    created=client.post('/api/schedules/reference/relaxed-deadlines')
    assert created.status_code==200,created.text
    new_id=created.json()['dataset']['id']
    with connect() as db:
        adjusted=json.loads(db.execute('SELECT instance FROM datasets WHERE id=?',(new_id,)).fetchone()[0])
        old=json.loads(db.execute('SELECT instance FROM datasets WHERE id=?',(request['dataset_id'],)).fetchone()[0])
    assert old==original and snapshot.read_bytes()==saved
    assert sum(j['due_min']!=o['due_min'] for j,o in zip(adjusted['jobs'],original['jobs']))==123
    restored=copy.deepcopy(adjusted)
    del restored['metadata']['deadline_relaxation']
    for a,b in zip(restored['jobs'],original['jobs']):
        a.pop('original_due_min');a.pop('original_due_datetime')
        a['due_min']=b['due_min']
        if 'due_datetime' in b:a['due_datetime']=b['due_datetime']
    assert restored==original
    jobs=client.get('/api/jobs',params={'dataset_id':new_id}).json()
    assert len(jobs)==180
    request['dataset_id']=new_id
    request['settings']=dict(runs=1,micro_max_decodes=100,micro_generations=1,micro_time_limit=3)
    valid=client.post('/api/schedules/validate',json=request)
    assert valid.status_code==200 and valid.json()['valid'],valid.text
    started=client.post('/api/schedules/run',json=request)
    assert started.status_code==202,started.text
    run_id=started.json()['run_id']
    result=wait_result(client,run_id)
    assert result['selected']['feasible'],result
    due=result['selected']['metadata']['deadline_comparison']
    assert due['source_run_id']=='reference' and len(due['jobs'])==180
    assert due['adjusted_tardy_jobs']==result['selected']['tardy_jobs']
    assert due['original_tardy_jobs']>=due['adjusted_tardy_jobs']
    assert client.post(f'/api/schedules/{run_id}/relaxed-deadlines').status_code==409
    exported=client.get(f'/api/schedules/{run_id}/export.xlsx')
    assert exported.status_code==200,exported.text
    book=load_workbook(BytesIO(exported.content))
    assert book['Deadline Comparison'].max_row==181
    assert book['Deadline Comparison']['B2'].value==original['jobs'][0]['due_min']
    assert snapshot.read_bytes()==saved


def test_completion_uses_all_batches_and_rejects_missing_contributors():
    ops=[dict(eventType='2C_BATCH',jobIds=['A','B'],endMin=10),
         dict(eventType='2C_BATCH',jobIds=['A'],endMin=20),
         dict(eventType='JOB_OP',jobIds=['B'],endMin=1000)]
    assert completions(ops,['A','B'])=={'A':20,'B':10}
    with pytest.raises(AppError):completions(ops,['A','B','C'])


def test_on_time_reference_warm_start_and_retention(client):
    from backend.services.deadline_service import prepare,register_on_time_reference,load_on_time_reference,keep_on_time_result
    from backend.services.schedule_service import get_run
    _,request,_=seed_reference(client)
    data,_=prepare(get_run('reference'))
    raw=json.loads(gzip.decompress((ROOT/'experiments/cmax_terminal_lns_v5/known-warm-results/winner_raw.json.gz').read_bytes()))
    register_on_time_reference(data,raw)
    reference=load_on_time_reference(data)
    bad=dict(feasible=True,tardy_jobs=49,total_tardiness_min=200,makespan_min=70555.417,runtime_sec=1,metadata={})
    retained=keep_on_time_result('MIN_TARDY_GROUPED_LNS_V5',bad,data,reference)
    assert retained['tardy_jobs']==0 and retained['makespan_min']==69117.91699999997
    assert retained['metadata']['deadline_reference_retained']
    assert retained['metadata']['search_produced_metrics']['tardy_jobs']==49
    assert not retained['proven_optimal']
    invalid=copy.deepcopy(data)
    invalid['jobs'][0]['due_min']=0
    with pytest.raises(ValueError):load_on_time_reference(invalid)
    uploaded=client.post('/api/input/upload',files={'file':('protected.json',dump(data),'application/json')})
    assert uploaded.status_code==200,uploaded.text
    request['dataset_id']=uploaded.json()['id']
    request['algorithms']=['MIN_TARDY_GROUPED_LNS_V5']
    request['settings']=dict(grouped_max_decodes=32,due_group_days=1,runs=1)
    started=client.post('/api/schedules/run',json=request)
    assert started.status_code==202,started.text
    run_id=started.json()['run_id']
    result=wait_result(client,run_id)
    selected=result['selected']
    assert selected['feasible'] and selected['tardy_jobs']==0,result
    assert selected['makespan_min']<=69117.917
    assert selected['metadata']['on_time_reference_policy']
    assert selected['metadata']['on_time_reference_warm_start']
    assert selected['metadata']['unique_full_decoder_calls']==32
    assert client.get(f'/api/schedules/{run_id}/export.xlsx').status_code==200
