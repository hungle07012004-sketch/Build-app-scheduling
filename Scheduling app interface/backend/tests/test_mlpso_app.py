"""V5 MLPSO adapter and request contracts; production storage is never used."""
import hashlib
import json
from pathlib import Path

import pytest

from backend.models.schemas import SolverSettings
from backend.services.algorithms import mlpso_adapter
from backend.services.optimization_service import worker
from backend.tests.test_v5 import instance


def test_v5_adapter_passes_settings_without_legacy_import(tmp_path,monkeypatch):
    from backend.solver_v5 import solve_mlpso_v3_instance as v3
    path=tmp_path/'instance.json';path.write_text(json.dumps(instance()))
    settings=SolverSettings(mlpso_population=6,mlpso_iterations=7,mlpso_max_decodes=19,
        seed_base=1051,runs=2,mlpso_mr=.3,mlpso_pcr=.4,mlpso_gcr=.5).model_dump()
    captured={}
    def solve(p,**kw):captured.update(kw);assert p==path;return {'sentinel':True}
    monkeypatch.setattr(v3,'solve_instance',solve)
    def forbidden(*args):raise AssertionError('V5 must not import legacy MLPSO')
    monkeypatch.setattr(mlpso_adapter,'solver_module',forbidden)
    assert mlpso_adapter.solve(path,settings)=={'sentinel':True}
    assert captured==dict(population=6,iterations=7,max_decodes=19,mr=.3,pcr=.4,gcr=.5,
        seed_base=1051,runs=2,parallel_runs=False,collect_convergence=True,progress_every=100)


@pytest.mark.parametrize('population',[4,60])
def test_bad_v5_swarm_rejected_before_creating_run(client,population):
    from backend.database.db import connect
    data=instance()
    ds=client.post('/api/input/upload',files={'file':('v5.json',json.dumps(data),'application/json')}).json()['id']
    body=dict(dataset_id=ds,job_ids=[j['job_id'] for j in data['jobs']],
        horizon_start='2026-10-01T06:00',horizon_end='2026-10-02T06:00',algorithms=['MLPSO'],
        settings={'mlpso_population':population})
    # Allow reaching Advanced settings before checking the swarm size at run time.
    assert client.post('/api/schedules/validate',json=body).json()['valid']
    response=client.post('/api/schedules/run',json=body)
    assert response.status_code==400 and 'Advanced settings' in response.text
    with connect() as db:assert db.execute('select count(*) from runs').fetchone()[0]==0


def test_worker_mlpso_no_feasible_is_not_saved_as_feasible(tmp_path):
    data=instance();data['resource_windows']['WS_OIL']=[]
    path=tmp_path/'instance.json';path.write_text(json.dumps(data))
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    result=worker('MLPSO',str(path),digest,SolverSettings(
        mlpso_population=6,mlpso_iterations=1,mlpso_max_decodes=6).model_dump())
    assert result['feasible'] is False and result['makespan_min'] is None
    assert result['operations']==[] and result['status']!='Failed'
    assert json.loads((tmp_path/'mlpso.result.json').read_text())==result
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
