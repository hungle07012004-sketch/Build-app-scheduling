import json
from concurrent.futures import Future
from backend.database.db import connect, dump, now
from backend.services import optimization_service as optimization
from backend.services.result_standardizer import json_metadata


def test_search_statistics_are_json_safe_without_mutating_raw():
    raw = {"trace": [{"best": 12.5, "mean": float('inf'), "worst": float('nan')}]}
    cleaned = json_metadata(raw)
    assert cleaned == {"trace": [{"best": 12.5, "mean": None, "worst": None}]}
    assert raw['trace'][0]['mean'] == float('inf')
    json.dumps(cleaned, allow_nan=False)


def test_unserializable_result_does_not_leave_run_running(client, monkeypatch):
    class Pool:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def submit(self, *args):
            future = Future()
            future.set_result({'algorithm':'SS Micro-GA Fast','status':'Feasible',
                               'feasible':True,'metadata':{'worst':float('inf')}})
            return future
    monkeypatch.setattr(optimization, 'ProcessPoolExecutor', Pool)
    with connect() as db:
        db.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?)',('d','test',now(),'hash','unused','{}'))
        db.execute('INSERT INTO runs(id,dataset_id,created_at,status,request,instance_path,instance_sha256) VALUES(?,?,?,?,?,?,?)',
                   ('r','d',now(),'queued','{}','unused','hash'))
        db.execute('INSERT INTO algorithm_results VALUES(?,?,?,?)',('r','SS Micro-GA Fast','queued','{}'))
    optimization.execute('r','unused','hash',{}, {'algorithms':['SS Micro-GA Fast']})
    with connect() as db:
        row=db.execute("SELECT status,finished_at,result FROM runs WHERE id='r'").fetchone()
        assert row['status']=='failed'
        assert row['finished_at']
        assert json.loads(row['result'])['algorithms'][0]['status']=='Failed'
