"""Incumbent and chromosome regressions; all solver inputs use tmp_path."""
import copy
import itertools
import json
import math
import random

import pytest

from backend.services.result_standardizer import standardize
from backend.solver_v5 import solve_improved_ga_instance as base
from backend.solver_v5 import solve_ss_micro_ga_fast_instance as micro
from backend.solver_v5.benchmark_common import V5ScheduleDecoder as Reference
from backend.solver_v5.benchmark_common_fast import V5ScheduleDecoder as Fast
from backend.tests.test_v5 import instance


@pytest.mark.parametrize("rescue", ["immigrant", "restart", "diversity"])
@pytest.mark.parametrize("initial_cost", [100.0, math.inf])
def test_last_rescue_decode_is_returned(monkeypatch, rescue, initial_cost):
    # Isolate stop/replacement control flow from physical schedule construction.
    perms = list(itertools.permutations(range(4)))
    initial, better, fresh = perms[:4], perms[4], perms[5]
    evaluated = []

    class Decoder:
        jobs = ["J001", "J002", "J003", "J004"]

        def valid_chromosome_indices(self, ch):
            return sorted(ch) == list(range(4))

        def decode_indices(self, ch):
            cost = 1.0 if ch == better else initial_cost
            evaluated.append(cost)
            return cost, 0.0, 0, 0.0

    proposals = iter([[initial[0]], [fresh], [fresh]])
    monkeypatch.setattr(base, "create_initial_population", lambda *a, **k: initial[:])
    monkeypatch.setattr(base, "population_diversity", lambda *a: 0.0 if rescue == "diversity" else 1.0)
    monkeypatch.setattr(micro, "_inject_immigrants", lambda *a, **k: [better])
    monkeypatch.setattr(micro, "_generate_candidate_pool", lambda **k: next(proposals))
    monkeypatch.setattr(micro, "_screen_candidates", lambda candidates, *a, **k: candidates)
    result = micro.run_one_ss_micro_ga(
        decoder=Decoder(), seed=42, population=4, offspring_count=1, candidate_pool=1,
        generations=10, max_decodes=5, archive_size=2, screen_explore_fraction=.33,
        crossover_rate=.9, crossover_type="multi", crossover_weights=(1, 1, 1),
        mutation_rate=.15, mutation_max=.6, tournament_k=2,
        immigrant_after=1 if rescue == "immigrant" else 0, immigrant_count=1,
        restart_after=1 if rescue == "restart" else 0, restart_fraction=.6,
        diversity_threshold=.25, refined_seed=None, collect_convergence=True,
        convergence_every=1, diversity_every=1, progress_every=0, progress_label="test",
    )
    assert result["stop_reason"] == "decoder_budget"
    assert result["unique_evaluations"] == 5
    assert result["best_metrics"]["makespan_min"] == min(evaluated) == 1.0
    assert result["best_chromosome"] == [Decoder.jobs[i] for i in better]
    terminal = result["convergence"][-1]
    assert terminal["Best_Cmax_Min"] == 1.0
    assert terminal["Decoder_Evaluations"] == 5
    assert terminal["Epoch"] == result["epochs_completed"]
    assert terminal["Stop_Reason"] == result["stop_reason"]


INVALID = [
    (), (0, 1, 2), (0, 0, 1, 2, 3), (0, 0, 2, 3), (-1, 0, 1, 2),
    (0, 1, 2, 4), (0, 1, 2, 99), (0.0, 1, 2, 3), (False, 1, 2, 3),
    ("0", 1, 2, 3), (None, 1, 2, 3), (float("nan"), 1, 2, 3),
    (float("inf"), 1, 2, 3), ([0], 1, 2, 3), None,
]


@pytest.mark.parametrize("decoder_class", [Reference, Fast])
@pytest.mark.parametrize("chromosome", INVALID)
def test_invalid_indices_never_reach_physical_decoder(monkeypatch, decoder_class, chromosome):
    decoder = decoder_class(instance())
    monkeypatch.setattr(decoder, "decode", lambda *a, **k: pytest.fail("Invalid permutation reached decoder"))
    metrics = decoder.decode_indices(chromosome)
    assert math.isinf(metrics[0])


@pytest.mark.parametrize("chromosome", INVALID)
def test_invalid_fitness_cannot_reuse_valid_cache(monkeypatch, chromosome):
    decoder = Fast(instance())
    cache = {(0, 1, 2, 3): ((123.0,), (123.0, 0.0, 0, 123.0))}
    before = dict(cache)
    monkeypatch.setattr(decoder, "decode_indices", lambda *a: pytest.fail("Invalid candidate was evaluated"))
    key, metrics, decoded = base.evaluate_one(chromosome, decoder, cache)
    assert math.isinf(key[0]) and math.isinf(metrics[0])
    assert decoded == 0 and cache == before


@pytest.mark.parametrize("crossover", [base.ox_crossover, base.pmx_crossover, base.pbx_crossover])
@pytest.mark.parametrize("mutation", [base.swap_mutation, base.insert_mutation, base.reverse_mutation,
                                      base._product_block_move, base._batch_partner_move, micro._v5_aware_mutation])
def test_operator_repair_decoder_fitness_pipeline(crossover, mutation):
    data = instance()
    unchanged = copy.deepcopy(data)
    fast, reference = Fast(data), Reference(data)
    rng = random.Random(42)
    pop = [bytes(p) for p in itertools.permutations(range(4))]
    cache = {}
    keys = [base.evaluate_one(ch, fast, cache)[0] for ch in pop]
    parents = [base.tournament(pop, keys, rng, 3) for _ in range(2)]
    for child in crossover(*parents, rng):
        if mutation in (base._product_block_move, base._batch_partner_move, micro._v5_aware_mutation):
            child = mutation(child, fast, rng)
        else:
            child = mutation(child, rng)
        child = base.repair_product_change_remainders(child, fast)
        assert sorted(child) == list(range(4))
        key, metrics, _ = base.evaluate_one(child, fast, cache)
        ids = [fast.jobs[i] for i in child]
        full, rows = fast.decode(ids, keep_schedule=True)
        assert (full, rows) == reference.decode(ids, keep_schedule=True)
        result = standardize("SS Micro-GA Fast", {"best_makespan_min": full["makespan_min"], "schedule": rows}, data)
        assert result["feasible"], result["validation_issues"]
        assert key == (round(result["makespan_min"], 9),)
        for field, value in zip(("makespan_min", "total_tardiness_min", "tardy_jobs", "total_flow_time_min"), metrics):
            assert result[field] == pytest.approx(value)
    assert data == unchanged


def test_real_search_keeps_best_evaluation_and_repeats(tmp_path, monkeypatch):
    path = tmp_path / "instance.json"
    data = instance()
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    decode = Fast.decode_indices
    evaluated = []

    def observe(self, ch):
        metrics = decode(self, ch)
        evaluated.append(metrics[0])
        return metrics

    monkeypatch.setattr(Fast, "decode_indices", observe)
    outputs = []
    for _ in range(2):
        evaluated.clear()
        raw = micro.solve_instance(path, population=4, generations=6, max_decodes=100,
            runs=1, seed_base=42, use_refined_seed=False, parallel_runs=False,
            immigrant_after=1, restart_after=2, progress_every=0)
        assert raw["best_makespan_min"] == min(evaluated)
        result = standardize("SS Micro-GA Fast", raw, data)
        assert result["feasible"], result["validation_issues"]
        outputs.append(raw)
    for field in ("best_chromosome", "best_makespan_min", "best_total_tardiness_min",
                  "best_total_flow_time_min", "schedule"):
        assert outputs[0][field] == outputs[1][field]
    assert path.read_bytes() == original


def test_final_rescue_recovers_first_real_feasible_schedule(tmp_path, monkeypatch):
    data = instance()
    # This fixture has both feasible and infeasible permutations in the same calendar.
    for windows in data["resource_windows"].values():
        windows[0]["end_min"] = 122.0
    decoder = Fast(data)
    candidates = list(dict.fromkeys(base.repair_product_change_remainders(p, decoder)
                                   for p in itertools.permutations(range(4))))
    bad = [ch for ch in candidates if math.isinf(decoder.decode_indices(ch)[0])]
    good = next(ch for ch in candidates if math.isfinite(decoder.decode_indices(ch)[0]))
    initial = bad[:4]
    assert len(initial) == 4
    fresh = next(ch for ch in itertools.permutations(range(4)) if ch not in initial and ch != good)
    proposals = iter([[initial[0]], [fresh]])
    monkeypatch.setattr(base, "create_initial_population", lambda *a, **k: initial[:])
    monkeypatch.setattr(base, "population_diversity", lambda *a: 1.0)
    monkeypatch.setattr(micro, "_inject_immigrants", lambda *a, **k: [good])
    monkeypatch.setattr(micro, "_generate_candidate_pool", lambda **k: next(proposals))
    monkeypatch.setattr(micro, "_screen_candidates", lambda candidates, *a, **k: candidates)
    path = tmp_path / "frozen.json"
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    raw = micro.solve_instance(path, population=4, generations=5, max_decodes=5,
        runs=1, seed_base=42, use_refined_seed=False, parallel_runs=False,
        immigrant_after=1, immigrant_count=1, restart_after=0, progress_every=0)
    assert raw["search_stop_reason"] == "decoder_budget"
    assert raw["run_summaries"][0]["unique_evaluations"] == 5
    result = standardize("SS Micro-GA Fast", raw, data)
    assert result["feasible"], result["validation_issues"]
    assert result["operations"]
    assert result["makespan_min"] == decoder.decode_indices(good)[0]
    assert path.read_bytes() == original
