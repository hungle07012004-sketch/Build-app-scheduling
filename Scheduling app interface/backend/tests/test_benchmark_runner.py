import copy
import json

import pytest

from backend import benchmark_v5 as runner
from backend.solver_v5.benchmark_common_fast import V5ScheduleDecoder
from backend.tests.test_v5 import instance


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.json"
    path.write_text(json.dumps(instance()))
    return path


@pytest.fixture
def captured(monkeypatch):
    data = instance()
    decoder = V5ScheduleDecoder(data)
    metrics, schedule = decoder.decode(decoder.jobs, keep_schedule=True)
    calls = []
    def solve(path, settings):
        calls.append((path, copy.deepcopy(settings)))
        return {**metrics, "schedule": schedule, "objective": runner.OBJECTIVE,
                "status_name": "FEASIBLE", "runtime_sec": .1}
    monkeypatch.setattr(runner.v5_adapter, "solve_exact", solve)
    monkeypatch.setattr(runner.v5_adapter, "solve_micro", solve)
    return calls


def test_same_input_budget_settings_and_manifest(source, tmp_path, captured):
    before = source.read_bytes()
    out = tmp_path / "new"
    result = runner.run_benchmark(source, out, seconds=300, seeds=[42, 1051])
    assert len(captured) == 4
    assert {p for p, _ in captured} == {out / "frozen.json"}
    for _, config in captured:
        assert config["micro_time_limit"] == config["exact_time_limit"] == 300
        assert config["runs"] == config["exact_workers"] == 1
    assert [r["algorithm"] for r in result["runs"]] == [*runner.ALGORITHMS, *reversed(runner.ALGORITHMS)]
    assert all(r["feasible"] for r in result["runs"])
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["input_sha256"] == runner.digest(before)
    assert source.read_bytes() == (out / "frozen.json").read_bytes() == before
    assert manifest["code"]["entrypoints"]["SS Micro-GA Fast"] == "backend.solver_v5.solve_ss_micro_ga_fast_instance"
    assert manifest["code"]["packages"]["ortools"]
    assert manifest["code"]["source_sha256"]
    assert all(c["ga_gap_to_proven_optimum_percent"] is None for c in result["comparisons"])


@pytest.mark.parametrize("change", ["none", "input", "settings", "seed", "code"])
def test_never_reuses_or_overwrites_existing_results(source, tmp_path, captured, monkeypatch, change):
    out = tmp_path / "existing"
    runner.run_benchmark(source, out, seconds=5, seeds=[42])
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    captured.clear()
    if change == "input":
        data = json.loads(source.read_text())
        data["metadata"]["instance_name"] = "changed"
        source.write_text(json.dumps(data))
    if change == "code":
        monkeypatch.setattr(runner, "source_hashes", lambda: {"solver.py": "different"})
    with pytest.raises(FileExistsError):
        runner.run_benchmark(source, out, seconds=5, seeds=[1051 if change == "seed" else 42],
                             settings={"micro_generations": 5} if change == "settings" else None)
    assert not captured
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


def test_one_solver_failure_preserves_other_result(source, tmp_path, captured, monkeypatch):
    def fail(*args):
        raise RuntimeError("controlled failure")
    monkeypatch.setattr(runner.v5_adapter, "solve_exact", fail)
    result = runner.run_benchmark(source, tmp_path / "failure", seconds=5, seeds=[42])
    cp, ga = result["runs"]
    assert not cp["feasible"] and cp["makespan_min"] is None
    assert cp["evaluations"] is None and "controlled failure" in cp["error"]
    assert ga["feasible"]
    assert not result["comparisons"][0]["comparable"]


@pytest.mark.parametrize("kind", ["input", "code"])
def test_changed_provenance_aborts_comparison(source, tmp_path, captured, monkeypatch, kind):
    solve = runner.v5_adapter.solve_exact
    def changed(path, settings):
        result = solve(path, settings)
        if kind == "input":
            path.write_text("{}")  # Only the test's disposable frozen copy.
        else:
            monkeypatch.setattr(runner, "source_hashes", lambda: {"changed": "hash"})
        return result
    monkeypatch.setattr(runner.v5_adapter, "solve_exact", changed)
    with pytest.raises(RuntimeError, match="changed during benchmark"):
        runner.run_benchmark(source, tmp_path / "changed", seconds=5, seeds=[42])
    assert not (tmp_path / "changed" / "results.json").exists()


@pytest.mark.parametrize("bad", [{"seconds": 0}, {"seconds": float("nan")}, {"seeds": []},
                                  {"seeds": [42, 42]}, {"repeats": 0}])
def test_invalid_protocol_rejected(source, tmp_path, bad):
    kwargs = {"seconds": 5, "seeds": [42], **bad}
    with pytest.raises(ValueError):
        runner.run_benchmark(source, tmp_path / "bad", **kwargs)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("change", ["input_sha256", "experiment_fingerprint", "seed", "repeat", "feasible", "makespan_min"])
def test_invalid_pair_has_no_gap(change):
    cp = dict(input_sha256="a", experiment_fingerprint="b", seed=42, repeat=1,
              feasible=True, makespan_min=100, proven_optimal=True)
    ga = {**cp, "makespan_min": 120, "proven_optimal": False}
    ga[change] = None
    result = runner.compare(cp, ga)
    assert not result["comparable"]
    assert result["ga_gap_to_proven_optimum_percent"] is None


def test_gap_only_to_proven_optimum():
    cp = dict(input_sha256="a", experiment_fingerprint="b", seed=42, repeat=1,
              feasible=True, makespan_min=100, proven_optimal=True)
    ga = {**cp, "makespan_min": 120, "proven_optimal": False}
    assert runner.compare(cp, ga)["ga_gap_to_proven_optimum_percent"] == 20
    cp["proven_optimal"] = False
    result = runner.compare(cp, ga)
    assert result["comparable"] and result["difference_to_cp_incumbent_percent"] == 20
    assert result["ga_gap_to_proven_optimum_percent"] is None


def test_real_small_benchmark_reproducible(source, tmp_path):
    result = runner.run_benchmark(source, tmp_path / "real", seconds=5, seeds=[42, 1051], repeats=2,
                                 settings={"micro_population": 4, "micro_generations": 7})
    assert len(result["runs"]) == 8
    assert all(row["feasible"] for row in result["runs"])
    assert all(pair["comparable"] for pair in result["comparisons"])
    for seed in (42, 1051):
        for algorithm, suffix in [("CP-SAT", "cp"), ("SS Micro-GA Fast", "micro")]:
            a, b = [r for r in result["runs"] if r["seed"] == seed and r["algorithm"] == algorithm]
            for key in ("makespan_min", "total_tardiness_min", "total_flow_time_min", "evaluations", "stop_reason"):
                assert a[key] == b[key]
            artifacts = [json.loads((tmp_path / "real" / f"seed-{seed}-repeat-{rep}-{suffix}.json").read_text())
                         for rep in (1, 2)]
            assert artifacts[0]["raw"]["schedule"] == artifacts[1]["raw"]["schedule"]
