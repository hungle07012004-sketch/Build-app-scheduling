"""Terminal trace reporting must not change search decisions or saved inputs."""
import csv
import json

import pytest

from backend.solver_v5 import solve_ss_micro_ga_fast_instance as micro
from backend.tests.test_v5 import instance


def solve(tmp_path, **kwargs):
    data = instance()
    if kwargs.pop("infeasible", False):
        for windows in data["resource_windows"].values():
            windows[:] = [dict(windows[0], end_min=.01)]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    settings = dict(population=4, generations=7, max_decodes=100, runs=1,
                    parallel_runs=False, progress_every=0, use_refined_seed=False,
                    collect_convergence=True, convergence_every=25)
    settings.update(kwargs)
    result = micro.solve_instance(path, **settings)
    assert path.read_bytes() == before
    return result


def assert_terminal(result, reason):
    for summary, run in zip(result["run_summaries"], result["convergence_by_run"]):
        trace = run["trace"]
        assert run["seed"] == summary["seed"]
        assert trace
        assert len({p["Epoch"] for p in trace}) == len(trace)
        assert trace[-1]["Epoch"] == summary["epochs_completed"]
        assert trace[-1]["Decoder_Evaluations"] == summary["unique_evaluations"]
        assert trace[-1]["Best_Cmax_Min"] == summary["makespan_min"]
        assert trace[-1]["Stop_Reason"] == summary["stop_reason"] == reason
        assert trace[-1]["Evaluations_To_Best"] == summary["evaluations_to_best"]
        assert trace[-1]["Time_To_Best_Sec"] == summary["time_to_best_sec"]
        assert all(not p["Stop_Reason"] for p in trace[:-1])


@pytest.mark.parametrize("settings,reason", [
    ({}, "generation_limit"),
    ({"generations": 0}, "generation_limit"),
    ({"max_decodes": 5}, "decoder_budget"),
    ({"no_improve": 1, "min_epochs": 2, "restart_after": 0, "restart_grace": 0}, "no_improvement"),
    ({"time_limit": 1e-9}, "time_limit"),
    ({"infeasible": True}, "generation_limit"),
    ({"runs": 2}, "generation_limit"),
])
def test_terminal_trace_matches_summary(tmp_path, settings, reason):
    assert_terminal(solve(tmp_path, **settings), reason)


def test_terminal_trace_after_partial_batch(tmp_path, monkeypatch):
    clock = [0.0]
    decode = micro.V5ScheduleDecoder.decode_indices

    def timed_decode(self, ch):
        result = decode(self, ch)
        clock[0] += 1
        return result

    monkeypatch.setattr(micro.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(micro.V5ScheduleDecoder, "decode_indices", timed_decode)
    result = solve(tmp_path, time_limit=6)
    assert_terminal(result, "time_limit")
    assert result["run_summaries"][0]["unique_evaluations"] == 6


def test_trace_collection_does_not_change_search(tmp_path):
    with_trace = solve(tmp_path, runs=2)
    without_trace = solve(tmp_path, runs=2, collect_convergence=False)
    assert without_trace["convergence_by_run"] is None
    for key in ("best_chromosome", "best_makespan_min", "schedule"):
        assert with_trace[key] == without_trace[key]
    assert [r["seed"] for r in with_trace["run_summaries"]] == [42, 1051]
    for a, b in zip(with_trace["run_summaries"], without_trace["run_summaries"]):
        for key in a:
            if key not in ("runtime_sec", "time_to_best_sec"):
                assert a[key] == b[key], key


def test_terminal_trace_csv(tmp_path):
    result = solve(tmp_path, max_decodes=5)
    path = tmp_path / "trace.csv"
    micro._write_csv(path, result["convergence_by_run"][0]["trace"])
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    summary = result["run_summaries"][0]
    assert int(rows[-1]["Epoch"]) == summary["epochs_completed"]
    assert int(rows[-1]["Decoder_Evaluations"]) == summary["unique_evaluations"]
    assert float(rows[-1]["Best_Cmax_Min"]) == summary["makespan_min"]
    assert rows[-1]["Stop_Reason"] == summary["stop_reason"]
