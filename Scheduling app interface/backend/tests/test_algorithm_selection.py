import copy
import json
import pytest
from backend.models.schemas import ScheduleRequest
from backend.tests.test_v5 import instance
from backend.tests.test_integration import upload,request,wait_result,decoded
from backend.services.instance_service import route
from backend.services import optimization_service as optimization
from backend.services.result_standardizer import standardize


def test_complexity_and_budget_override_small_job_count(monkeypatch):
    data=instance();before=copy.deepcopy(data)
    assert route(4,data)["algorithms"]==["CP-SAT"]
    assert route(4,data,{"exact_time_limit":10})["algorithms"]==["SS Micro-GA Fast"]
    data["oil_cycles"]*=60
    assert route(4,data)["algorithms"]==["SS Micro-GA Fast"]
    assert route(4,before)["selection_features"]["oil_cycles"]<route(4,data)["selection_features"]["oil_cycles"]
    monkeypatch.setenv("AUTO_CP_MAX_EVENTS","1")
    assert route(4,before)["algorithms"]==["SS Micro-GA Fast"]


def test_availability_and_determinism(monkeypatch):
    from backend.services import algorithm_selection as selector
    data=instance()
    assert route(4,data)==route(4,data)
    assert route(4,data)["availability"]["MLPSO"]["available"]
    original=selector.availability
    def missing_cp(v5):
        value=original(v5);value["CP-SAT"]["available"]=False;return value
    monkeypatch.setattr(selector,"availability",missing_cp)
    assert route(4,data)["algorithms"]==["SS Micro-GA Fast"]


def test_preview_no_runs_and_manual_override(client):
    from backend.database.db import connect
    response=client.post('/api/input/upload',files={'file':('v5.json',json.dumps(instance()),'application/json')})
    body=dict(dataset_id=response.json()['id'],job_ids=[j['job_id'] for j in instance()['jobs']],
        horizon_start='2026-10-01T06:00',horizon_end='2026-10-02T06:00',settings={'exact_time_limit':10})
    preview=client.post('/api/schedules/preview',json=body).json()
    validation=client.post('/api/schedules/validate',json=body).json()
    assert preview['algorithms']==validation['algorithms']==['SS Micro-GA Fast']
    assert preview['routing_reason']==validation['routing_reason']
    body['algorithms']=['CP-SAT']
    manual=client.post('/api/schedules/preview',json=body).json()
    assert manual['algorithms']==['CP-SAT'] and manual['selection_mode']=='Manual'
    body['algorithms']=['MLPSO']
    body['settings']['mlpso_population']=6
    manual=client.post('/api/schedules/preview',json=body)
    assert manual.status_code==200,manual.text
    assert manual.json()['algorithms']==['MLPSO']
    with connect() as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0]==0


@pytest.mark.parametrize('chosen',[[],['Improved GA'],['MLPSO']])
def test_legacy_preview_matches_executed_single_engine(client,monkeypatch,chosen):
    from concurrent.futures import ThreadPoolExecutor
    seen=[]
    def worker(algorithm,path,digest,settings):
        from pathlib import Path
        seen.append(algorithm)
        data=json.loads(Path(path).read_text())
        return standardize(algorithm,decoded(data),data)
    monkeypatch.setattr(optimization,'ProcessPoolExecutor',lambda **kw:ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(optimization,'worker',worker)
    body=request(upload(client,2),2);body['algorithms']=chosen
    preview=client.post('/api/schedules/preview',json=body).json()
    run=client.post('/api/schedules/run',json=body)
    assert run.status_code==202
    result=wait_result(client,run.json()['run_id'])
    assert seen==preview['algorithms']
    assert len(result['algorithms'])==1
    assert result['routing_reason']==preview['routing_reason']
    assert result['selected']['algorithm']==seen[0]
