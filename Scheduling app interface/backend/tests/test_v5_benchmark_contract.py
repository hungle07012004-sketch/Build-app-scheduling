"""Bounded benchmark-contract checks; no app DB or historical outputs are used."""
import hashlib
import json

import pytest

from backend.models.schemas import SolverSettings
from backend.services.algorithms import v5_adapter
from backend.services.result_standardizer import standardize
from backend.solver_v5 import solve_milp_instance as exact
from backend.solver_v5 import solve_ss_micro_ga_fast_instance as micro
from backend.tests.test_v5 import instance


@pytest.mark.parametrize("budget", [300, 600, 900])
def test_app_forwards_budget_seed_and_configuration(tmp_path, monkeypatch, budget):
    # Dispatch test only: these long budgets are NOT actually run.
    calls = {}
    def capture(name):
        def solve(path, **kwargs):
            calls[name] = (path, kwargs)
            return {"called": name}
        return solve
    monkeypatch.setattr(exact, "solve_instance", capture("CP-SAT"))
    monkeypatch.setattr(micro, "solve_instance", capture("SS Micro-GA Fast"))
    settings = SolverSettings(exact_time_limit=budget, micro_time_limit=budget,
                              seed_base=1051, exact_workers=1).model_dump()
    path = tmp_path / "same-frozen-input.json"
    assert v5_adapter.solve_exact(path, settings) == {"called": "CP-SAT"}
    assert v5_adapter.solve_micro(path, settings) == {"called": "SS Micro-GA Fast"}
    for recorded_path, config in calls.values():
        assert recorded_path == path
        assert config["time_limit"] == budget
    cp, ga = calls["CP-SAT"][1], calls["SS Micro-GA Fast"][1]
    assert cp["random_seed"] == ga["seed_base"] == 1051
    assert cp["workers"] == 1
    assert ga["runs"] == settings["runs"] == 1
    assert ga["parallel_runs"] is False
    for param, field in [("population", "micro_population"), ("generations", "micro_generations"),
                         ("max_decodes", "micro_max_decodes"), ("mutation_rate", "micro_mutation_rate"),
                         ("crossover_rate", "micro_crossover_rate"), ("no_improve", "micro_no_improve"),
                         ("min_epochs", "micro_min_epochs")]:
        assert ga[param] == settings[field]


def test_nonzero_gap_tolerance_is_not_proof_of_optimality(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(instance()))
    raw = exact.solve_instance(path, time_limit=5, workers=1, random_seed=42, mip_rel_gap=.9)
    assert raw["feasible"]
    assert raw["relative_gap"] > 0
    assert not raw["proven_optimal"]
    assert raw["relative_gap_limit"] == .9


def test_same_frozen_input_feasibility_gap_and_repeatability(tmp_path):
    data = instance()
    path = tmp_path / "same-input.json"
    path.write_text(json.dumps(data))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    settings = SolverSettings(exact_time_limit=5, exact_workers=1, micro_time_limit=5,
                              micro_population=4, micro_generations=7, seed_base=42).model_dump()
    results = {}
    for algorithm, solve in [("CP-SAT", v5_adapter.solve_exact), ("SS Micro-GA Fast", v5_adapter.solve_micro)]:
        attempts = []
        for _ in range(2):
            raw = solve(path, settings)
            result = standardize(algorithm, raw, data)
            assert result["feasible"], result["validation_issues"]
            assert result["objective"] == "MIN_CMAX_FINAL_2C"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            attempts.append((raw, result))
        for metric in ("makespan_min", "total_tardiness_min", "total_flow_time_min"):
            assert attempts[0][1][metric] == attempts[1][1][metric]
        assert attempts[0][0]["schedule"] == attempts[1][0]["schedule"]
        results[algorithm] = attempts[0]
    cp, cp_std = results["CP-SAT"]
    ga, ga_std = results["SS Micro-GA Fast"]
    upper, lower = cp["makespan_min"], cp["best_bound_min"]
    assert lower <= upper
    assert cp["relative_gap"] == pytest.approx((upper - lower) / upper)
    assert cp["proven_optimal"]
    assert cp["relative_gap"] == 0
    # Gap to a proven optimum is meaningful only for this identical frozen input.
    ga_gap_percent = 100 * (ga_std["makespan_min"] - upper) / upper
    assert ga_gap_percent >= -1e-6
    (tmp_path / "comparison.json").write_text(json.dumps({
        "instance_sha256": digest, "budget_seconds_each": 5, "seed": 42,
        "cp_cmax": cp_std["makespan_min"], "cp_bound": lower, "cp_gap_ratio": cp["relative_gap"],
        "ga_cmax": ga_std["makespan_min"], "ga_gap_to_proven_optimum_percent": ga_gap_percent,
        "ga_stop_reason": ga["search_stop_reason"],
    }, indent=2))
