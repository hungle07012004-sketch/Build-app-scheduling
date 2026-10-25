import pytest
from backend.models.schemas import SolverSettings
from backend.services.algorithms import v5_adapter


@pytest.mark.parametrize('seconds',[60,120,300,600,900])
def test_presets_reach_existing_solver_budget(monkeypatch, seconds):
    from backend.solver_v5 import solve_milp_instance, solve_ss_micro_ga_fast_instance
    seen=[]
    def capture(path, **kwargs):
        seen.append(kwargs)
        return {'schedule':[]}
    monkeypatch.setattr(solve_milp_instance,'solve_instance',capture)
    monkeypatch.setattr(solve_ss_micro_ga_fast_instance,'solve_instance',capture)
    settings=SolverSettings(exact_time_limit=seconds,micro_time_limit=seconds).model_dump()
    v5_adapter.solve_exact('unchanged.json',settings)
    v5_adapter.solve_micro('unchanged.json',settings)
    assert [s['time_limit'] for s in seen]==[seconds,seconds]
    assert seen[1]['no_improve']==settings['micro_no_improve']
