"""Real decoder regressions for cooperative time and stagnation limits."""
import json
import pytest
from backend.tests.test_v5 import instance
from backend.services.result_standardizer import standardize
from backend.solver_v5 import solve_ss_micro_ga_fast_instance as micro


def solve(tmp_path, **kwargs):
    data = instance()
    path = tmp_path / "frozen.json"
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    raw = micro.solve_instance(path, population=kwargs.pop("population",4), runs=kwargs.pop("runs",1),
        generations=100, max_decodes=1000, parallel_runs=False,
        progress_every=0, **kwargs)
    result = standardize("SS Micro-GA Fast", raw, data)
    assert result["feasible"], result["validation_issues"]
    assert path.read_bytes() == before
    assert result["objective"] == "MIN_CMAX_FINAL_2C"
    return raw


def test_early_stop_survives_restarts(tmp_path):
    raw = solve(tmp_path, no_improve=8, min_epochs=10, immigrant_after=1,
                restart_after=3, restart_grace=2)
    run = raw["run_summaries"][0]
    assert run["stop_reason"] == "no_improvement"
    assert 10 <= run["epochs_completed"] < 100
    assert run["epochs_completed"] - run["best_epoch"] >= 8
    assert run["restart_count"] >= 1


def test_disabled_early_stop_keeps_generation_budget(tmp_path):
    raw = solve(tmp_path, no_improve=0)
    assert raw["run_summaries"][0]["stop_reason"] == "generation_limit"
    assert raw["run_summaries"][0]["epochs_completed"] == 100


def test_no_feasible_candidate_returns_result_not_crash(tmp_path):
    data = instance()
    for windows in data["resource_windows"].values():
        windows[:] = [dict(windows[0], start_min=0, end_min=0.01)]
    path = tmp_path / "short-calendar.json"
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    raw = micro.solve_instance(path, population=4, generations=0, runs=1,
                               parallel_runs=False, progress_every=0)
    result = standardize("SS Micro-GA Fast", raw, data)
    assert not result["feasible"]
    assert result["status"] == "No feasible schedule found"
    assert "does not prove" in result["message"]
    assert raw["diagnostic"]
    assert result["operations"] == []
    json.dumps(result, allow_nan=False)
    assert path.read_bytes() == before


def test_expired_budget_returns_incumbent_and_skips_other_runs(tmp_path):
    raw = solve(tmp_path, time_limit=1e-9, runs=3)
    assert raw["completed_runs"] == 1
    assert raw["runs"] == 3
    assert raw["run_summaries"][0]["stop_reason"] == "time_limit"
    assert raw["refined_seed_info"]["new_decodes"] == 1


def test_deadline_during_batch_retains_best_completed_decode(tmp_path, monkeypatch):
    clock = [0.0]
    evaluated = []
    decode = micro.V5ScheduleDecoder.decode_indices
    def timed_decode(self, *args, **kwargs):
        metrics = decode(self, *args, **kwargs)
        evaluated.append(metrics[0])
        clock[0] += 1.0
        return metrics
    monkeypatch.setattr(micro.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(micro.V5ScheduleDecoder, "decode_indices", timed_decode)
    raw = solve(tmp_path, time_limit=6, use_refined_seed=False)
    run = raw["run_summaries"][0]
    assert run["stop_reason"] == "time_limit"
    assert run["unique_evaluations"] == 6
    assert raw["best_makespan_min"] == pytest.approx(min(evaluated))


def test_invalid_search_limit(tmp_path):
    with pytest.raises(ValueError, match="non-negative"):
        micro.solve_instance(tmp_path/"unused.json", time_limit=-1)


def test_small_selection_can_use_default_population(tmp_path):
    # Batch-key repair maps distinct permutations to the same chromosome.
    raw = solve(tmp_path, population=24, time_limit=0.1)
    assert raw["run_summaries"][0]["stop_reason"] == "time_limit"


def test_new_app_defaults():
    from backend.models.schemas import SolverSettings
    settings = SolverSettings()
    assert settings.exact_time_limit == 120
    assert settings.micro_time_limit == 120
    assert settings.micro_min_epochs == 1300
    assert settings.micro_no_improve == 600


def test_final_validation_time_does_not_mislabel_search_stop(tmp_path, monkeypatch):
    clock = [0.0]
    decode = micro.V5ScheduleDecoder.decode
    def final_decode(self, *args, **kwargs):
        result = decode(self, *args, **kwargs)
        if kwargs.get("keep_schedule"):
            clock[0] = 200.0
        return result
    monkeypatch.setattr(micro.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(micro.V5ScheduleDecoder, "decode", final_decode)
    raw = solve(tmp_path, time_limit=120, no_improve=0)
    assert raw["search_stop_reason"] == "generation_limit"
