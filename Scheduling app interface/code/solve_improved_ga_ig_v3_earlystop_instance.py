#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Improved AL13 GA + NEH + Elite VNS + Two-Step IG + target-polish early stopping.

This module EXTENDS the validated ``solve_improved_ga_instance.py`` solver.
It intentionally reuses the original AL13 model, benchmark data, chromosome,
fitness function, GA operators, VNS and FrozenInstanceDecoder without changing
any scheduling constraint or decoder rule.

New contribution (V3 ablation)
------------------------------
A plateau-triggered Iterated Greedy (IG) destroy-and-repair mechanism adapted
from the destruction/construction principle in:

    Li et al. (2025), "Graph Knowledge-Enhanced Iterated Greedy Algorithm for
    Hybrid Flowshop Scheduling Problem", Mathematics 13(15), 2401.
    DOI: 10.3390/math13152401

The permutation-level multi-point destruction + greedy reconstruction principle
is retained from V1.  V3 additionally introduces a two-step escape mechanism:
a non-improving reconstructed solution may be used TEMPORARILY as a bridge for
one more destroy/repair step.  Worse solutions are never permanently injected
into the GA population.  Final acceptance remains strict lexicographic
improvement under the frozen AL13 objective.

The bridge gate is inspired by the two-step search and poor-solution acceptance
ideas in Li et al. (2025), but it is adapted conservatively for AL13: no new
decoder, no graph representation, no forward/backward decoding, and no rapid
evaluation replacement are introduced.

Ablation supported by this file:

    V0 = current Improved GA + NEH + Elite VNS              (--no-use-ig)
    V1 = V0 + Random Multi-Point IG                         (--use-ig --no-use-two-step)
    V3 = V1 + Two-Step Controlled Inferior Bridge           (--use-two-step)

V2 critical-guided destruction remains a separate experimental file and is NOT
used as the base for V3 because the V2 debug experiment produced no IG gains.

Runtime dependency
------------------
Place this file in the SAME project folder as:

    solve_improved_ga_instance.py
    benchmark_common.py

Early-stopping extension
------------------------
This variant adds an OPTIONAL best-known-target stopping rule.  The target is
NOT treated as a mathematically proven optimum.  Once the best makespan reaches
``target_cmax`` (within tolerance), the solver keeps searching for a polishing
window and resets that window whenever the full lexicographic objective
(Cmax, tardiness, flow time) improves.  It stops only after the polishing
window expires without further lexicographic improvement.

Python: 3.10+
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

try:
    import solve_improved_ga_instance as base
except ImportError as exc:  # pragma: no cover - user-facing project check
    raise ImportError(
        "solve_improved_ga_ig_v3_earlystop_instance.py must be placed beside "
        "solve_improved_ga_instance.py in the AL13 project folder."
    ) from exc

from benchmark_common import FrozenInstanceDecoder, load_instance


FitnessKey = Tuple[float, float, float]


# ---------------------------------------------------------------------------
# Iterated Greedy V3: random destroy/repair + two-step controlled inferior bridge
# ---------------------------------------------------------------------------


def choose_ig_destroy_size(
    chromosome_length: int,
    rng: random.Random,
    destroy_min: int,
    destroy_max: int,
) -> int:
    """Choose a valid random multi-point destruction size.

    Li et al. (2025) dynamically select the number of deleted jobs within a
    bounded range.  For the AL13 V1 ablation we keep that principle simple and
    reproducible: the per-run seeded IG RNG chooses an integer in the configured
    range.  For a 50-job instance the recommended starting range is 4..8.
    """
    if chromosome_length <= 1:
        return 0
    lo = max(1, int(destroy_min))
    hi = max(lo, int(destroy_max))
    # Leave at least one job in the partial sequence whenever possible.
    hi = min(hi, chromosome_length - 1)
    lo = min(lo, hi)
    return rng.randint(lo, hi)


def ig_random_destroy(
    chromosome: Sequence[int],
    rng: random.Random,
    destroy_size: int,
) -> Tuple[List[int], List[int], List[int]]:
    """Remove ``destroy_size`` distinct genes from a permutation.

    Returns ``(partial, removed_jobs, removed_positions)``.  Removed jobs are
    stored in their original left-to-right order.  The operator never creates
    duplicates and never changes gene identity.
    """
    parent = list(chromosome)
    n = len(parent)
    if n <= 1 or destroy_size <= 0:
        return parent[:], [], []

    d = min(max(1, int(destroy_size)), n - 1)
    positions = sorted(rng.sample(range(n), d))
    pos_set = set(positions)
    removed = [parent[i] for i in positions]
    partial = [gene for i, gene in enumerate(parent) if i not in pos_set]
    return partial, removed, positions



def _controlled_two_step_bridge(
    candidate_key: FitnessKey,
    incumbent_key: FitnessKey,
    rng: random.Random,
    *,
    temperature_pct: float,
    max_cmax_gap_pct: float,
    cooling: float,
    attempt_index: int,
) -> Tuple[bool, float, float, str]:
    """Decide whether a non-improving candidate may be used as a temporary bridge.

    IMPORTANT:
    - This function does NOT change the thesis fitness function.
    - This function does NOT permanently accept a worse solution.
    - It only decides whether to launch ONE second destroy/repair step from the
      intermediate candidate.
    - Any final IG solution must still be strictly lexicographically better than
      the current IG incumbent.

    Gate logic:
    1) Same Cmax but lexicographically non-improving -> always allow a second step.
       This directly targets the decoder-induced Cmax plateau.
    2) Worse Cmax -> allow only if the Cmax gap is within a configurable percentage
       and a seeded SA-like probability test passes.
    3) Large Cmax deterioration -> reject the bridge immediately.

    Temperature is scale-aware:
        T = incumbent_Cmax * temperature_pct/100 * cooling^(attempt_index-1)
    """
    inc_cmax = float(incumbent_key[0])
    cand_cmax = float(candidate_key[0])
    eps = 1e-9

    # Strict improvements do not need bridge gating; caller accepts them normally.
    if candidate_key < incumbent_key:
        return False, 0.0, 1.0, "strict_improvement"

    cmax_gap = max(0.0, cand_cmax - inc_cmax)

    # Same-primary-objective plateau: allow a second structural perturbation.
    if cmax_gap <= eps:
        return True, 0.0, 1.0, "same_cmax_bridge"

    allowed_gap = max(0.0, inc_cmax * float(max_cmax_gap_pct) / 100.0)
    if cmax_gap > allowed_gap + eps:
        return False, cmax_gap, 0.0, "cmax_gap_too_large"

    base_temperature = max(eps, inc_cmax * float(temperature_pct) / 100.0)
    effective_temperature = max(
        eps,
        base_temperature * (float(cooling) ** max(0, int(attempt_index) - 1)),
    )
    probability = math.exp(-cmax_gap / effective_temperature)
    accepted = rng.random() < probability
    return (
        bool(accepted),
        float(cmax_gap),
        float(probability),
        "worse_cmax_bridge" if accepted else "worse_cmax_rejected",
    )



def _evaluate_with_count(
    chromosome: Sequence[int],
    decoder: FrozenInstanceDecoder,
    codec: "base.JobIndexCodec",
    cache: MutableMapping,
) -> Tuple[FitnessKey, Mapping[str, float], int]:
    """Evaluate through the frozen decoder and report whether a new decode ran."""
    ck = codec.key(chromosome)
    was_cached = ck in cache
    key, metrics = base.evaluate_fitness(chromosome, decoder, codec, cache)
    return key, metrics, 0 if was_cached else 1


def ig_greedy_repair(
    partial: Sequence[int],
    removed_jobs: Sequence[int],
    decoder: FrozenInstanceDecoder,
    codec: "base.JobIndexCodec",
    cache: MutableMapping,
) -> Tuple[List[int], FitnessKey, Mapping[str, float], int]:
    """Greedily reinsert deleted jobs at lexicographically best positions.

    For each deleted job, every insertion position in the current partial
    permutation is evaluated by the SAME ``base.evaluate_fitness`` helper used
    by the Improved GA/NEH.  That helper calls the unchanged
    FrozenInstanceDecoder with ``validate_chromosome=False`` for partial
    sequences.  No alternative scheduling evaluator is introduced.

    The AL13 thesis objective remains strict lexicographic minimization of:
        (Cmax, total tardiness, total flow time).
    """
    current = list(partial)
    decoder_evaluations = 0
    current_key: FitnessKey = (math.inf, math.inf, math.inf)
    current_metrics: Mapping[str, float] = {
        "makespan_min": math.inf,
        "total_tardiness_min": math.inf,
        "total_flow_time_min": math.inf,
    }

    for job in removed_jobs:
        best_candidate: Optional[List[int]] = None
        best_key: FitnessKey = (math.inf, math.inf, math.inf)
        best_metrics: Optional[Mapping[str, float]] = None

        for pos in range(len(current) + 1):
            cand = current[:]
            cand.insert(pos, int(job))
            key, metrics, new_eval = _evaluate_with_count(cand, decoder, codec, cache)
            decoder_evaluations += new_eval
            if key < best_key:
                best_candidate = cand
                best_key = key
                best_metrics = metrics

        if best_candidate is None or best_metrics is None:  # defensive only
            raise RuntimeError("IG greedy reconstruction failed to select an insertion position")
        current = best_candidate
        current_key = best_key
        current_metrics = best_metrics

    # If removed_jobs is empty, evaluate the unchanged partial once.
    if not removed_jobs:
        current_key, current_metrics, new_eval = _evaluate_with_count(
            current, decoder, codec, cache
        )
        decoder_evaluations += new_eval

    return current, current_key, current_metrics, decoder_evaluations


def _assert_complete_permutation(chromosome: Sequence[int], n_jobs: int) -> None:
    if len(chromosome) != n_jobs:
        raise AssertionError(
            f"IG chromosome length mismatch: got {len(chromosome)}, expected {n_jobs}"
        )
    if len(set(chromosome)) != n_jobs:
        raise AssertionError("IG chromosome contains duplicate genes")
    if set(chromosome) != set(range(n_jobs)):
        raise AssertionError("IG chromosome is missing or contains unknown genes")


def apply_elite_ig(
    elite_chromosome: Sequence[int],
    decoder: FrozenInstanceDecoder,
    *,
    codec: Optional["base.JobIndexCodec"] = None,
    cache: Optional[MutableMapping] = None,
    rng: Optional[random.Random] = None,
    iterations: int = 10,
    destroy_min: int = 4,
    destroy_max: int = 8,
    use_two_step: bool = True,
    inferior_temperature_pct: float = 0.30,
    inferior_max_cmax_gap_pct: float = 1.00,
    inferior_cooling: float = 0.90,
    cmax_stall: int = 0,
    generation: int = -1,
    progress_label: str = "IG",
    log_progress: bool = True,
) -> Tuple[List[int], Mapping[str, float], dict]:
    """Apply V3 plateau-escape IG to one elite permutation.

    The persistent IG incumbent remains strictly improving.  When ``use_two_step``
    is enabled, a non-improving first reconstruction may be used TEMPORARILY as
    an intermediate bridge for one additional destroy/repair operation.

    The bridge is never injected into the GA population and never replaces
    ``ig_best`` by itself.  Only a second-step solution that is strictly better
    than the current ``ig_best`` can be accepted.  Thus the frozen lexicographic
    objective and elitist GA behavior remain unchanged.
    """
    codec = codec or base.JobIndexCodec.from_decoder(decoder)
    cache = cache if cache is not None else {}
    rng = rng or random.Random()

    parent = list(elite_chromosome)
    _assert_complete_permutation(parent, codec.n)
    parent_key, parent_metrics, parent_eval = _evaluate_with_count(parent, decoder, codec, cache)

    ig_best = parent[:]
    ig_best_key = parent_key
    ig_best_metrics: Mapping[str, float] = parent_metrics

    decoder_evaluations = parent_eval
    attempts = max(1, int(iterations))
    accepted_improvements = 0
    destroy_sizes: List[int] = []
    attempt_events: List[dict] = []

    # V3 two-step diagnostics.
    two_step_attempts = 0
    bridge_accepted = 0
    neutral_same_cmax_bridges = 0
    worse_cmax_bridges = 0
    two_step_successes = 0
    max_intermediate_cmax_gap = 0.0

    for attempt in range(1, attempts + 1):
        search_base = ig_best[:]  # persistent path is still strict-improvement only

        # ------------------------- First destroy/repair ---------------------
        d1 = choose_ig_destroy_size(len(search_base), rng, destroy_min, destroy_max)
        destroy_sizes.append(d1)
        partial1, removed1, removed_positions1 = ig_random_destroy(search_base, rng, d1)

        candidate1, candidate1_key, candidate1_metrics, evals1 = ig_greedy_repair(
            partial1,
            removed1,
            decoder,
            codec,
            cache,
        )
        decoder_evaluations += evals1
        _assert_complete_permutation(candidate1, codec.n)

        incumbent_before_key = ig_best_key
        first_improved = candidate1_key < ig_best_key
        event = {
            "attempt": attempt,
            "first_destroy_size": int(d1),
            "first_removed_positions": removed_positions1,
            "before_key": list(incumbent_before_key),
            "first_candidate_key": list(candidate1_key),
            "first_step_improved": bool(first_improved),
            "bridge_allowed": False,
            "bridge_reason": "",
            "bridge_probability": 0.0,
            "intermediate_cmax_gap_min": 0.0,
            "second_step_attempted": False,
            "second_destroy_size": 0,
            "second_candidate_key": None,
            "second_step_improved": False,
            "accepted": False,
        }

        if first_improved:
            ig_best = candidate1
            ig_best_key = candidate1_key
            ig_best_metrics = candidate1_metrics
            accepted_improvements += 1
            event["accepted"] = True
            event["accepted_via"] = "first_step"

        elif use_two_step:
            # ----------------- Temporary controlled inferior bridge --------
            allow_bridge, cmax_gap, bridge_prob, bridge_reason = _controlled_two_step_bridge(
                candidate1_key,
                ig_best_key,
                rng,
                temperature_pct=inferior_temperature_pct,
                max_cmax_gap_pct=inferior_max_cmax_gap_pct,
                cooling=inferior_cooling,
                attempt_index=attempt,
            )
            event["bridge_allowed"] = bool(allow_bridge)
            event["bridge_reason"] = bridge_reason
            event["bridge_probability"] = float(bridge_prob)
            event["intermediate_cmax_gap_min"] = float(cmax_gap)
            max_intermediate_cmax_gap = max(max_intermediate_cmax_gap, float(cmax_gap))

            if allow_bridge:
                bridge_accepted += 1
                if cmax_gap <= 1e-9:
                    neutral_same_cmax_bridges += 1
                else:
                    worse_cmax_bridges += 1

                # Candidate1 is used only as a bridge.  It is NOT assigned to ig_best.
                two_step_attempts += 1
                event["second_step_attempted"] = True

                d2 = choose_ig_destroy_size(
                    len(candidate1), rng, destroy_min, destroy_max
                )
                partial2, removed2, removed_positions2 = ig_random_destroy(
                    candidate1, rng, d2
                )
                candidate2, candidate2_key, candidate2_metrics, evals2 = ig_greedy_repair(
                    partial2,
                    removed2,
                    decoder,
                    codec,
                    cache,
                )
                decoder_evaluations += evals2
                _assert_complete_permutation(candidate2, codec.n)

                second_improved = candidate2_key < ig_best_key
                event["second_destroy_size"] = int(d2)
                event["second_removed_positions"] = removed_positions2
                event["second_candidate_key"] = list(candidate2_key)
                event["second_step_improved"] = bool(second_improved)

                if second_improved:
                    ig_best = candidate2
                    ig_best_key = candidate2_key
                    ig_best_metrics = candidate2_metrics
                    accepted_improvements += 1
                    two_step_successes += 1
                    event["accepted"] = True
                    event["accepted_via"] = "two_step_bridge"

        attempt_events.append(event)

    # Full, normal frozen-decoder validation only for a genuinely improved IG
    # output.  This does not replace the evaluator; it verifies the complete
    # candidate exactly as requested by the thesis architecture.
    success = ig_best_key < parent_key
    validation_decode = 0
    if success:
        _assert_complete_permutation(ig_best, codec.n)
        validated_metrics, _ = decoder.decode(
            codec.to_job_ids(ig_best),
            keep_schedule=False,
            validate_chromosome=True,
        )
        validation_decode = 1
        decoder_evaluations += 1
        validated_key = base.fitness_key(validated_metrics)
        if validated_key != ig_best_key:
            raise AssertionError(
                "Frozen decoder validation disagrees with cached IG fitness: "
                f"cached={ig_best_key}, validated={validated_key}"
            )
        ig_best_metrics = validated_metrics

    before_cmax = float(parent_metrics["makespan_min"])
    after_cmax = float(ig_best_metrics["makespan_min"])
    cmax_gain = max(0.0, before_cmax - after_cmax) if success else 0.0
    cmax_improved = bool(success and after_cmax < before_cmax - 1e-9)
    secondary_only = bool(success and not cmax_improved)

    info = {
        "generation": int(generation),
        "cmax_stall": int(cmax_stall),
        "iterations": attempts,
        "destroy_sizes": destroy_sizes,
        "last_destroy_size": int(destroy_sizes[-1]) if destroy_sizes else 0,
        "decoder_evaluations": int(decoder_evaluations),
        "validation_decode": int(validation_decode),
        "accepted_internal_improvements": int(accepted_improvements),
        "two_step_enabled": bool(use_two_step),
        "two_step_attempts": int(two_step_attempts),
        "bridge_accepted": int(bridge_accepted),
        "neutral_same_cmax_bridges": int(neutral_same_cmax_bridges),
        "worse_cmax_bridges": int(worse_cmax_bridges),
        "two_step_successes": int(two_step_successes),
        "max_intermediate_cmax_gap_min": float(max_intermediate_cmax_gap),
        "inferior_temperature_pct": float(inferior_temperature_pct),
        "inferior_max_cmax_gap_pct": float(inferior_max_cmax_gap_pct),
        "inferior_cooling": float(inferior_cooling),
        "improved": bool(success),
        "cmax_improved": cmax_improved,
        "secondary_only_improvement": secondary_only,
        "before_key": list(parent_key),
        "after_key": list(ig_best_key),
        "before_cmax_min": before_cmax,
        "after_cmax_min": after_cmax,
        "cmax_gain_min": float(cmax_gain),
        "attempt_events": attempt_events,
    }

    if log_progress:
        if success:
            print(
                f"[{progress_label}] gen={generation} stall={cmax_stall} "
                f"destroy={info['last_destroy_size']} | "
                f"before={parent_key} | after={ig_best_key} | "
                f"Cmax gain={cmax_gain:.3f} | "
                f"bridges={bridge_accepted} | two-step wins={two_step_successes} | "
                f"evals={decoder_evaluations}",
                flush=True,
            )
        else:
            print(
                f"[{progress_label}] gen={generation} stall={cmax_stall} | "
                f"no improvement | bridges={bridge_accepted} | "
                f"two-step wins={two_step_successes} | evals={decoder_evaluations}",
                flush=True,
            )

    return ig_best, dict(ig_best_metrics), info


# ---------------------------------------------------------------------------
# One GA run extended with plateau-triggered IG
# ---------------------------------------------------------------------------


def run_one_improved_ga_ig(
    data: Mapping,
    decoder: FrozenInstanceDecoder,
    seed: int,
    population: int,
    generations: int,
    crossover_rate: float,
    mutation_rate: float,
    mutation_max: float,
    elite: int,
    tournament_k: int,
    no_improve: int,
    injection_fraction: float,
    diversity_threshold: float,
    use_neh: bool,
    use_vns: bool,
    vns_freq: int,
    vns_steps: int,
    vns_moves: int,
    vns_stall_moves: int,
    vns_shakes: int,
    vns_critical_top_k: int,
    vns_stagnation_trigger: int,
    crossover_type: str,
    crossover_weights: Sequence[float],
    use_ig: bool,
    ig_stagnation_trigger: int,
    ig_frequency: int,
    ig_iterations: int,
    ig_destroy_min: int,
    ig_destroy_max: int,
    use_two_step: bool,
    ig_inferior_temperature_pct: float,
    ig_inferior_max_cmax_gap_pct: float,
    ig_inferior_cooling: float,
    target_cmax: Optional[float] = None,
    target_tolerance: float = 0.01,
    target_polish_generations: int = 500,
    precomputed_neh: Optional[Sequence[int]] = None,
    collect_convergence: bool = True,
    diversity_every: int = 20,
    progress_every: int = 100,
    progress_label: str = "Improved GA + IG",
) -> dict:
    """One independent GA run with unchanged GA/VNS plus two-step IG V3."""
    rng = random.Random(seed)
    diag_rng = random.Random(seed ^ 0x5DEECE66D)
    vns_rng = random.Random(seed ^ 0x9E3779B9)
    # Keep IG randomness independent from the GA and VNS streams so enabling IG
    # does not silently consume GA crossover/mutation random numbers.
    ig_rng = random.Random(seed ^ 0xD1B54A32)

    codec = base.JobIndexCodec.from_decoder(decoder)
    pop, init_info = base._create_initial_population(
        data,
        decoder,
        codec,
        rng,
        population,
        use_neh=use_neh,
        precomputed_neh=precomputed_neh,
    )
    cache: Dict[object, Mapping[str, float]] = {}

    best_ch: Optional[List[int]] = None
    best_metrics: Optional[Mapping[str, float]] = None
    best_key: FitnessKey = (math.inf, math.inf, math.inf)
    stall = 0
    cmax_stall = 0
    convergence: List[dict] = []
    crossover_usage = {"ox": 0, "pmx": 0, "pbx": 0}

    vns_total_improvements = 0
    vns_total_evaluations = 0
    vns_total_shakes = 0
    vns_events: List[dict] = []

    ig_calls = 0
    ig_total_evaluations = 0
    ig_successful_calls = 0
    ig_cmax_improvements = 0
    ig_secondary_only_improvements = 0
    ig_best_cmax_gain = 0.0
    ig_two_step_attempts = 0
    ig_bridge_accepted = 0
    ig_neutral_bridges = 0
    ig_worse_cmax_bridges = 0
    ig_two_step_successes = 0
    ig_max_intermediate_cmax_gap = 0.0
    ig_events: List[dict] = []

    # Optional best-known-target early stopping.  The target is NOT assumed to
    # be a proven optimum; it only activates a lexicographic polishing window.
    target_reached = False
    target_reached_generation: Optional[int] = None
    target_last_improvement_generation: Optional[int] = None
    target_best_key: Optional[FitnessKey] = None
    target_polish_stall = 0
    early_stop_reason = "generation_limit"

    t0 = time.perf_counter()
    last_div = base.population_diversity(pop, diag_rng)
    last_ur = base.unique_ratio(pop, codec)

    if progress_every > 0:
        print(
            f"[{progress_label}] start | seed={seed} | pop={len(pop)} | epochs={generations} | "
            f"NEH={'ON' if use_neh else 'OFF'} | VNS={'ON' if use_vns else 'OFF'} | "
            f"IG={'ON' if use_ig else 'OFF'} | cross={crossover_type} | "
            f"target={target_cmax if target_cmax is not None else 'OFF'} | "
            f"polish={target_polish_generations if target_cmax is not None else '-'} | "
            f"resources={'ON' if decoder.use_resources else 'SKIPPED(redundant)'}",
            flush=True,
        )

    for gen in range(generations + 1):
        previous_best_key = best_key
        previous_best_cmax = best_key[0]

        keys, metrics = base.evaluate_population(pop, decoder, codec, cache)
        order = sorted(range(len(pop)), key=lambda i: keys[i])

        improved_global = False
        pop_best_idx = order[0]
        if keys[pop_best_idx] < best_key:
            best_key = keys[pop_best_idx]
            best_ch = pop[pop_best_idx][:]
            best_metrics = dict(metrics[pop_best_idx])
            improved_global = True

        # ----------------------------- Existing Elite VNS ------------------
        vns_applied = False
        vns_improvements_this_gen = 0
        vns_evals_this_gen = 0
        vns_shakes_this_gen = 0
        plateau_mode = (
            use_vns
            and vns_stagnation_trigger > 0
            and cmax_stall >= vns_stagnation_trigger
        )
        freq_due = use_vns and vns_freq > 0 and (gen % vns_freq == 0)
        stall_due = (
            use_vns
            and vns_stagnation_trigger > 0
            and cmax_stall == vns_stagnation_trigger
        )
        if use_vns and (freq_due or stall_due):
            vns_applied = True
            elite_idx = order[0]
            old_elite_key = keys[elite_idx]
            effective_vns_moves = max(vns_moves, vns_stall_moves) if plateau_mode else vns_moves
            effective_shakes = vns_shakes if plateau_mode else 0

            improved_elite, improved_metrics, vns_info = base.apply_elite_vns(
                pop[elite_idx],
                decoder,
                max_neighborhood_steps=vns_steps,
                codec=codec,
                cache=cache,
                rng=vns_rng,
                moves_per_neighborhood=effective_vns_moves,
                max_shakes=effective_shakes,
                critical_top_k=vns_critical_top_k,
                progress_label=f"VNS {progress_label} gen {gen}",
                log_improvements=True,
                return_details=True,
            )
            new_elite_key = base.fitness_key(improved_metrics)
            if new_elite_key < old_elite_key:
                worst_idx = order[-1]
                pop[worst_idx] = improved_elite[:]
                keys[worst_idx] = new_elite_key
                metrics[worst_idx] = dict(improved_metrics)
                order = sorted(range(len(pop)), key=lambda i: keys[i])

                vns_improvements_this_gen = int(vns_info["accepted_improvements"])
                vns_total_improvements += vns_improvements_this_gen
                vns_events.append(
                    {
                        "generation": int(gen),
                        "seed": int(seed),
                        **vns_info,
                    }
                )

                pop_best_idx = order[0]
                if keys[pop_best_idx] < best_key:
                    best_key = keys[pop_best_idx]
                    best_ch = pop[pop_best_idx][:]
                    best_metrics = dict(metrics[pop_best_idx])
                    improved_global = True

            vns_evals_this_gen = int(vns_info.get("evaluations", 0))
            vns_shakes_this_gen = int(vns_info.get("shakes_used", 0))
            vns_total_evaluations += vns_evals_this_gen
            vns_total_shakes += vns_shakes_this_gen

        # Update stagnation after GA/VNS evaluation.  Primary Cmax stagnation is
        # separate from lexicographic stagnation by design.
        if improved_global:
            stall = 0
        else:
            stall += 1

        if best_key[0] < previous_best_cmax:
            cmax_stall = 0
        else:
            cmax_stall += 1

        # ---------------------- Plateau-triggered two-step IG V3 ------------
        ig_applied = False
        ig_improved_this_gen = False
        ig_evals_this_gen = 0
        ig_destroy_size_this_gen = 0
        ig_two_step_attempts_this_gen = 0
        ig_bridge_accepted_this_gen = 0
        ig_two_step_successes_this_gen = 0
        ig_max_intermediate_gap_this_gen = 0.0
        ig_best_cmax_this_gen = (
            float(best_metrics["makespan_min"]) if best_metrics is not None else math.inf
        )
        ig_cmax_gain_this_gen = 0.0

        ig_due = (
            use_ig
            and ig_stagnation_trigger > 0
            and ig_frequency > 0
            and cmax_stall >= ig_stagnation_trigger
            and (cmax_stall - ig_stagnation_trigger) % ig_frequency == 0
        )

        if ig_due:
            ig_applied = True
            ig_calls += 1
            elite_idx = order[0]
            old_elite_key = keys[elite_idx]
            old_elite_cmax = float(metrics[elite_idx]["makespan_min"])

            ig_solution, ig_metrics, ig_info = apply_elite_ig(
                pop[elite_idx],
                decoder,
                codec=codec,
                cache=cache,
                rng=ig_rng,
                iterations=ig_iterations,
                destroy_min=ig_destroy_min,
                destroy_max=ig_destroy_max,
                use_two_step=use_two_step,
                inferior_temperature_pct=ig_inferior_temperature_pct,
                inferior_max_cmax_gap_pct=ig_inferior_max_cmax_gap_pct,
                inferior_cooling=ig_inferior_cooling,
                cmax_stall=cmax_stall,
                generation=gen,
                progress_label=f"IG {progress_label}",
                log_progress=True,
            )
            ig_key = base.fitness_key(ig_metrics)
            ig_evals_this_gen = int(ig_info["decoder_evaluations"])
            ig_total_evaluations += ig_evals_this_gen
            ig_destroy_size_this_gen = int(ig_info["last_destroy_size"])
            ig_two_step_attempts_this_gen = int(ig_info.get("two_step_attempts", 0))
            ig_bridge_accepted_this_gen = int(ig_info.get("bridge_accepted", 0))
            ig_two_step_successes_this_gen = int(ig_info.get("two_step_successes", 0))
            ig_max_intermediate_gap_this_gen = float(
                ig_info.get("max_intermediate_cmax_gap_min", 0.0)
            )

            ig_two_step_attempts += ig_two_step_attempts_this_gen
            ig_bridge_accepted += ig_bridge_accepted_this_gen
            ig_neutral_bridges += int(ig_info.get("neutral_same_cmax_bridges", 0))
            ig_worse_cmax_bridges += int(ig_info.get("worse_cmax_bridges", 0))
            ig_two_step_successes += ig_two_step_successes_this_gen
            ig_max_intermediate_cmax_gap = max(
                ig_max_intermediate_cmax_gap,
                ig_max_intermediate_gap_this_gen,
            )

            ig_best_cmax_this_gen = float(ig_metrics["makespan_min"])
            ig_cmax_gain_this_gen = float(ig_info["cmax_gain_min"])
            ig_events.append({"seed": int(seed), **ig_info})

            if ig_key < old_elite_key:
                ig_improved_this_gen = True
                ig_successful_calls += 1
                if ig_info["cmax_improved"]:
                    ig_cmax_improvements += 1
                    ig_best_cmax_gain = max(ig_best_cmax_gain, ig_cmax_gain_this_gen)
                elif ig_info["secondary_only_improvement"]:
                    ig_secondary_only_improvements += 1

                # Preserve old elite; replace current worst non-elite.
                worst_idx = order[-1]
                if worst_idx == elite_idx and len(order) > 1:
                    worst_idx = order[-2]
                pop[worst_idx] = ig_solution[:]
                keys[worst_idx] = ig_key
                metrics[worst_idx] = dict(ig_metrics)
                order = sorted(range(len(pop)), key=lambda i: keys[i])

                # Update global best using the unchanged lexicographic objective.
                pop_best_idx = order[0]
                if keys[pop_best_idx] < best_key:
                    old_global_cmax = best_key[0]
                    best_key = keys[pop_best_idx]
                    best_ch = pop[pop_best_idx][:]
                    best_metrics = dict(metrics[pop_best_idx])
                    stall = 0
                    if best_key[0] < old_global_cmax:
                        cmax_stall = 0

        # ----------------------- Target-polish early stopping -------------
        # A target hit alone does NOT stop the run.  We continue searching and
        # reset the polishing clock on ANY strict lexicographic improvement.
        if target_cmax is not None and best_key[0] <= target_cmax + target_tolerance:
            if not target_reached:
                target_reached = True
                target_reached_generation = int(gen)
                target_last_improvement_generation = int(gen)
                target_best_key = best_key
                target_polish_stall = 0
                if progress_every > 0:
                    print(
                        f"[{progress_label}] target reached at gen {gen} | "
                        f"Cmax={best_key[0]:.3f} <= {target_cmax:.3f} "
                        f"(+tol {target_tolerance:.3f}) | "
                        f"start polishing window={target_polish_generations}",
                        flush=True,
                    )
            else:
                assert target_best_key is not None
                assert target_last_improvement_generation is not None
                if best_key < target_best_key:
                    target_best_key = best_key
                    target_last_improvement_generation = int(gen)
                    target_polish_stall = 0
                else:
                    target_polish_stall = int(gen - target_last_improvement_generation)
        else:
            # Before the target is reached, the polishing clock is inactive.
            target_polish_stall = 0

        target_stop_due = bool(
            target_cmax is not None
            and target_reached
            and target_polish_stall >= target_polish_generations
        )

        if gen == 0 or gen % max(1, diversity_every) == 0 or gen == generations:
            last_div = base.population_diversity(pop, diag_rng)
            last_ur = base.unique_ratio(pop, codec)

        pm = base.adaptive_rate(
            mutation_rate,
            mutation_max,
            last_div,
            stall,
            max(20, no_improve or 100),
        )

        cvals = [float(m["makespan_min"]) for m in metrics]
        mean_cmax = sum(cvals) / len(cvals)
        assert best_metrics is not None and best_ch is not None

        if collect_convergence:
            convergence.append(
                {
                    "Generation": int(gen),
                    "Best_Cmax_Min": float(best_metrics["makespan_min"]),
                    "Best_Total_Tardiness_Min": float(best_metrics.get("total_tardiness_min", 0.0)),
                    "Best_Total_Flow_Time_Min": float(best_metrics.get("total_flow_time_min", 0.0)),
                    "Population_Best_Min": min(cvals),
                    "Population_Mean_Min": mean_cmax,
                    "Population_Worst_Min": max(cvals),
                    "Population_Diversity": float(last_div),
                    "Unique_Chromosome_Ratio": float(last_ur),
                    "Mutation_Rate": float(pm),
                    "Stall": int(stall),
                    "Cmax_Stall": int(cmax_stall),
                    "Unique_Evaluations": int(len(cache)),
                    "VNS_Applied": bool(vns_applied),
                    "VNS_Improvements": int(vns_improvements_this_gen),
                    "VNS_Evaluations": int(vns_evals_this_gen),
                    "VNS_Shakes": int(vns_shakes_this_gen),
                    "VNS_Plateau_Mode": bool(plateau_mode),
                    "IG_Applied": bool(ig_applied),
                    "IG_Improved": bool(ig_improved_this_gen),
                    "IG_Destroy_Size": int(ig_destroy_size_this_gen),
                    "IG_TwoStep_Attempts": int(ig_two_step_attempts_this_gen),
                    "IG_Bridge_Accepted": int(ig_bridge_accepted_this_gen),
                    "IG_TwoStep_Successes": int(ig_two_step_successes_this_gen),
                    "IG_Max_Intermediate_Cmax_Gap": float(ig_max_intermediate_gap_this_gen),
                    "IG_Evaluations": int(ig_evals_this_gen),
                    "IG_Best_Cmax": float(ig_best_cmax_this_gen),
                    "IG_Cmax_Gain": float(ig_cmax_gain_this_gen),
                    "Target_Cmax": (
                        float(target_cmax) if target_cmax is not None else None
                    ),
                    "Target_Reached": bool(target_reached),
                    "Target_Reached_Generation": target_reached_generation,
                    "Target_Polish_Stall": int(target_polish_stall),
                    "Target_Early_Stop_Due": bool(target_stop_due),
                }
            )

        if progress_every > 0 and (
            gen == 0 or gen == generations or gen % progress_every == 0
        ):
            elapsed = time.perf_counter() - t0
            pct = 100.0 * gen / max(1, generations)
            eta = (elapsed / gen * (generations - gen)) if gen > 0 else 0.0
            print(
                f"[{progress_label}] generation {gen:>5}/{generations} ({pct:5.1f}%) | "
                f"best={best_metrics['makespan_min']:.3f} | mean={mean_cmax:.3f} | "
                f"div={last_div:.3f} | stall={stall} | cmax_stall={cmax_stall} | "
                f"target_polish={target_polish_stall if target_reached else '-'} | "
                f"evals={len(cache)} | elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
                flush=True,
            )

        if gen >= generations:
            early_stop_reason = "generation_limit"
            break
        if target_stop_due:
            early_stop_reason = "target_polish_stall"
            if progress_every > 0:
                print(
                    f"[{progress_label}] EARLY STOP | target Cmax reached at gen "
                    f"{target_reached_generation} | no lexicographic improvement for "
                    f"{target_polish_stall} generations | best={best_key}",
                    flush=True,
                )
            break
        if no_improve > 0 and stall >= no_improve:
            early_stop_reason = "legacy_no_improve"
            break

        # --------------------------- Existing GA reproduction --------------
        keep = min(max(1, elite), len(pop))
        new_pop = [pop[i][:] for i in order[:keep]]
        seen = {codec.key(ch) for ch in new_pop}

        if last_div < diversity_threshold:
            target = min(
                len(pop),
                len(new_pop) + max(1, int(math.ceil(injection_fraction * len(pop)))),
            )
            attempts = 0
            while len(new_pop) < target and attempts < target * 100:
                attempts += 1
                immigrant = list(range(codec.n))
                rng.shuffle(immigrant)
                ikey = codec.key(immigrant)
                if ikey not in seen:
                    seen.add(ikey)
                    new_pop.append(immigrant)

        attempts = 0
        max_attempts = len(pop) * 500
        while len(new_pop) < len(pop) and attempts < max_attempts:
            attempts += 1
            p1 = base.tournament(pop, keys, rng, tournament_k)
            p2 = base.tournament(pop, keys, rng, tournament_k)
            for _ in range(5):
                if p2 != p1:
                    break
                p2 = base.tournament(pop, keys, rng, tournament_k)

            if rng.random() < crossover_rate:
                c1, c2, op = base.apply_crossover(
                    p1,
                    p2,
                    rng,
                    crossover_type=crossover_type,
                    crossover_weights=crossover_weights,
                )
                crossover_usage[op] += 1
            else:
                c1, c2 = p1[:], p2[:]

            if rng.random() < pm:
                c1 = base.mixed_mutation(c1, rng)
            if rng.random() < pm:
                c2 = base.mixed_mutation(c2, rng)

            for child in (c1, c2):
                child_key = codec.key(child)
                if child_key not in seen:
                    seen.add(child_key)
                    new_pop.append(child)
                    if len(new_pop) >= len(pop):
                        break

        while len(new_pop) < len(pop):
            ch = list(range(codec.n))
            rng.shuffle(ch)
            new_pop.append(ch)
        pop = new_pop

    runtime = time.perf_counter() - t0
    assert best_ch is not None and best_metrics is not None
    _assert_complete_permutation(best_ch, codec.n)

    if progress_every > 0:
        print(
            f"[{progress_label}] done | best={best_metrics['makespan_min']:.3f} | "
            f"time={runtime:.2f}s | evals={len(cache)} | "
            f"VNS improvements={vns_total_improvements} | "
            f"IG calls={ig_calls} | IG successes={ig_successful_calls} | "
            f"stop={early_stop_reason}",
            flush=True,
        )

    return {
        "seed": int(seed),
        "best_chromosome_idx": best_ch,
        "best_chromosome": codec.to_job_ids(best_ch),
        "best_metrics": dict(best_metrics),
        "runtime_sec": float(runtime),
        "generations_completed": int(gen),
        "unique_evaluations": int(len(cache)),
        "initialization": init_info,
        "crossover_usage": crossover_usage,
        "vns_total_improvements": int(vns_total_improvements),
        "vns_total_evaluations": int(vns_total_evaluations),
        "vns_total_shakes": int(vns_total_shakes),
        "vns_events": vns_events,
        "ig_calls": int(ig_calls),
        "ig_total_evaluations": int(ig_total_evaluations),
        "ig_successful_calls": int(ig_successful_calls),
        "ig_cmax_improvements": int(ig_cmax_improvements),
        "ig_secondary_only_improvements": int(ig_secondary_only_improvements),
        "ig_best_cmax_gain_min": float(ig_best_cmax_gain),
        "ig_two_step_attempts": int(ig_two_step_attempts),
        "ig_bridge_accepted": int(ig_bridge_accepted),
        "ig_neutral_bridges": int(ig_neutral_bridges),
        "ig_worse_cmax_bridges": int(ig_worse_cmax_bridges),
        "ig_two_step_successes": int(ig_two_step_successes),
        "ig_max_intermediate_cmax_gap_min": float(ig_max_intermediate_cmax_gap),
        "ig_events": ig_events,
        "target_cmax": float(target_cmax) if target_cmax is not None else None,
        "target_tolerance": float(target_tolerance),
        "target_polish_generations": int(target_polish_generations),
        "target_reached": bool(target_reached),
        "target_reached_generation": target_reached_generation,
        "target_polish_stall_final": int(target_polish_stall),
        "early_stop_reason": early_stop_reason,
        "convergence": convergence,
    }


# ---------------------------------------------------------------------------
# Windows-safe worker and multi-run solver
# ---------------------------------------------------------------------------


def _worker_run_ig(payload: dict) -> dict:
    data = load_instance(payload["instance_path"])
    decoder = FrozenInstanceDecoder(data)
    return run_one_improved_ga_ig(
        data=data,
        decoder=decoder,
        seed=payload["seed"],
        population=payload["population"],
        generations=payload["generations"],
        crossover_rate=payload["crossover_rate"],
        mutation_rate=payload["mutation_rate"],
        mutation_max=payload["mutation_max"],
        elite=payload["elite"],
        tournament_k=payload["tournament_k"],
        no_improve=payload["no_improve"],
        injection_fraction=payload["injection_fraction"],
        diversity_threshold=payload["diversity_threshold"],
        use_neh=payload["use_neh"],
        use_vns=payload["use_vns"],
        vns_freq=payload["vns_freq"],
        vns_steps=payload["vns_steps"],
        vns_moves=payload["vns_moves"],
        vns_stall_moves=payload["vns_stall_moves"],
        vns_shakes=payload["vns_shakes"],
        vns_critical_top_k=payload["vns_critical_top_k"],
        vns_stagnation_trigger=payload["vns_stagnation_trigger"],
        crossover_type=payload["crossover_type"],
        crossover_weights=payload["crossover_weights"],
        use_ig=payload["use_ig"],
        ig_stagnation_trigger=payload["ig_stagnation_trigger"],
        ig_frequency=payload["ig_frequency"],
        ig_iterations=payload["ig_iterations"],
        ig_destroy_min=payload["ig_destroy_min"],
        ig_destroy_max=payload["ig_destroy_max"],
        use_two_step=payload["use_two_step"],
        ig_inferior_temperature_pct=payload["ig_inferior_temperature_pct"],
        ig_inferior_max_cmax_gap_pct=payload["ig_inferior_max_cmax_gap_pct"],
        ig_inferior_cooling=payload["ig_inferior_cooling"],
        target_cmax=payload["target_cmax"],
        target_tolerance=payload["target_tolerance"],
        target_polish_generations=payload["target_polish_generations"],
        precomputed_neh=payload.get("precomputed_neh"),
        collect_convergence=payload["collect_convergence"],
        diversity_every=payload["diversity_every"],
        progress_every=payload["progress_every"],
        progress_label=payload["progress_label"],
    )


def solve_instance(
    instance_path: Path | str,
    population: int = 200,
    generations: int = 5000,
    runs: int = 10,
    seed_base: int = 42,
    crossover_rate: float = 0.90,
    mutation_rate: float = 0.12,
    mutation_max: float = 0.45,
    elite: int = 1,
    tournament_k: int = 3,
    no_improve: int = 0,
    injection_fraction: float = 0.25,
    diversity_threshold: float = 0.20,
    use_neh: bool = True,
    use_vns: bool = True,
    vns_freq: int = 10,
    vns_steps: int = 8,
    vns_moves: int = 30,
    vns_stall_moves: int = 45,
    vns_shakes: int = 2,
    vns_critical_top_k: int = 8,
    vns_stagnation_trigger: int = 150,
    crossover_type: str = "multi",
    crossover_weights: Sequence[float] = (1.0, 1.0, 1.0),
    use_ig: bool = True,
    ig_stagnation_trigger: int = 300,
    ig_frequency: int = 300,
    ig_iterations: int = 10,
    ig_destroy_min: int = 4,
    ig_destroy_max: int = 8,
    use_two_step: bool = True,
    ig_inferior_temperature_pct: float = 0.30,
    ig_inferior_max_cmax_gap_pct: float = 1.00,
    ig_inferior_cooling: float = 0.90,
    target_cmax: Optional[float] = None,
    target_tolerance: float = 0.01,
    target_polish_generations: int = 500,
    collect_convergence: bool = True,
    parallel_runs: bool = True,
    max_workers: int = 5,
    diversity_every: int = 50,
    progress_every: int = 100,
) -> dict:
    instance_path = Path(instance_path)
    data = load_instance(instance_path)
    base_decoder = FrozenInstanceDecoder(data)
    codec = base.JobIndexCodec.from_decoder(base_decoder)

    if runs <= 0:
        raise ValueError("runs must be >= 1")
    if population <= 0:
        raise ValueError("population must be >= 1")
    if generations < 0:
        raise ValueError("generations must be >= 0")
    if vns_freq < 0:
        raise ValueError("vns_freq must be >= 0")
    if vns_moves <= 0 or vns_stall_moves <= 0:
        raise ValueError("vns move budgets must be >= 1")
    if vns_shakes < 0:
        raise ValueError("vns_shakes must be >= 0")
    if vns_critical_top_k <= 0:
        raise ValueError("vns_critical_top_k must be >= 1")
    if crossover_type not in {"ox", "pmx", "pbx", "multi"}:
        raise ValueError("crossover_type must be one of: ox, pmx, pbx, multi")
    if len(crossover_weights) != 3:
        raise ValueError("crossover_weights must contain 3 values for OX, PMX, PBX")
    if ig_stagnation_trigger < 0:
        raise ValueError("ig_stagnation_trigger must be >= 0")
    if ig_frequency <= 0:
        raise ValueError("ig_frequency must be >= 1")
    if ig_iterations <= 0:
        raise ValueError("ig_iterations must be >= 1")
    if ig_destroy_min <= 0 or ig_destroy_max <= 0:
        raise ValueError("IG destruction bounds must be >= 1")
    if ig_destroy_min > ig_destroy_max:
        raise ValueError("ig_destroy_min cannot exceed ig_destroy_max")
    if ig_inferior_temperature_pct <= 0:
        raise ValueError("ig_inferior_temperature_pct must be > 0")
    if ig_inferior_max_cmax_gap_pct < 0:
        raise ValueError("ig_inferior_max_cmax_gap_pct must be >= 0")
    if not (0.0 < ig_inferior_cooling <= 1.0):
        raise ValueError("ig_inferior_cooling must be in (0, 1]")
    if target_cmax is not None and target_cmax <= 0:
        raise ValueError("target_cmax must be > 0 when provided")
    if target_tolerance < 0:
        raise ValueError("target_tolerance must be >= 0")
    if target_polish_generations < 0:
        raise ValueError("target_polish_generations must be >= 0")

    total_start = time.perf_counter()

    neh_start = time.perf_counter()
    precomputed_neh = (
        base.generate_neh_individual(data, base_decoder, codec) if use_neh else None
    )
    neh_runtime = time.perf_counter() - neh_start if use_neh else 0.0
    if use_neh:
        neh_key, neh_metrics = base._evaluate_index_chromosome(
            precomputed_neh, base_decoder, codec, cache={}
        )
        print(
            f"[NEH] {base_decoder.name} | Cmax={neh_metrics['makespan_min']:.3f} min | "
            f"Tard={neh_metrics['total_tardiness_min']:.3f} | time={neh_runtime:.2f}s",
            flush=True,
        )
    else:
        neh_key, neh_metrics = None, None

    seeds = [seed_base + i * 1009 for i in range(runs)]
    if max_workers <= 0:
        cpu = os.cpu_count() or 2
        max_workers = min(runs, max(1, cpu - 1))
    else:
        max_workers = max(1, min(runs, int(max_workers)))
    use_parallel = bool(parallel_runs and runs > 1 and max_workers > 1)

    print(
        f"[Improved GA + IG V3 + TargetStop] {base_decoder.name} | n={codec.n} | runs={runs} | "
        f"mode={'PARALLEL' if use_parallel else 'SEQUENTIAL'} | "
        f"workers={max_workers if use_parallel else 1} | pop={population} | "
        f"epochs={generations} | NEH={use_neh} | VNS={use_vns} | IG={use_ig} | "
        f"IG trigger={ig_stagnation_trigger} | IG freq={ig_frequency} | "
        f"IG destroy={ig_destroy_min}-{ig_destroy_max} | "
        f"two-step={'ON' if use_two_step else 'OFF'} | "
        f"T={ig_inferior_temperature_pct:.2f}% | "
        f"max-gap={ig_inferior_max_cmax_gap_pct:.2f}% | cross={crossover_type} | "
        f"target={target_cmax if target_cmax is not None else 'OFF'} | "
        f"tol={target_tolerance:.3f} | polish={target_polish_generations}",
        flush=True,
    )

    common_payload = {
        "instance_path": str(instance_path),
        "population": int(population),
        "generations": int(generations),
        "crossover_rate": float(crossover_rate),
        "mutation_rate": float(mutation_rate),
        "mutation_max": float(mutation_max),
        "elite": int(elite),
        "tournament_k": int(tournament_k),
        "no_improve": int(no_improve),
        "injection_fraction": float(injection_fraction),
        "diversity_threshold": float(diversity_threshold),
        "use_neh": bool(use_neh),
        "use_vns": bool(use_vns),
        "vns_freq": int(vns_freq),
        "vns_steps": int(vns_steps),
        "vns_moves": int(vns_moves),
        "vns_stall_moves": int(vns_stall_moves),
        "vns_shakes": int(vns_shakes),
        "vns_critical_top_k": int(vns_critical_top_k),
        "vns_stagnation_trigger": int(vns_stagnation_trigger),
        "crossover_type": str(crossover_type),
        "crossover_weights": [float(x) for x in crossover_weights],
        "use_ig": bool(use_ig),
        "ig_stagnation_trigger": int(ig_stagnation_trigger),
        "ig_frequency": int(ig_frequency),
        "ig_iterations": int(ig_iterations),
        "ig_destroy_min": int(ig_destroy_min),
        "ig_destroy_max": int(ig_destroy_max),
        "use_two_step": bool(use_two_step),
        "ig_inferior_temperature_pct": float(ig_inferior_temperature_pct),
        "ig_inferior_max_cmax_gap_pct": float(ig_inferior_max_cmax_gap_pct),
        "ig_inferior_cooling": float(ig_inferior_cooling),
        "target_cmax": float(target_cmax) if target_cmax is not None else None,
        "target_tolerance": float(target_tolerance),
        "target_polish_generations": int(target_polish_generations),
        "precomputed_neh": precomputed_neh,
        "collect_convergence": bool(collect_convergence),
        "diversity_every": int(diversity_every),
        "progress_every": int(progress_every),
    }

    run_results: List[dict] = []
    if use_parallel:
        payloads = []
        for i, seed in enumerate(seeds, 1):
            p = dict(common_payload)
            p.update({"seed": seed, "progress_label": f"Improved GA+IG-V3-ES run {i}/{runs}"})
            payloads.append(p)
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_worker_run_ig, p): p for p in payloads}
            for fut in as_completed(futures):
                run_results.append(fut.result())
    else:
        for i, seed in enumerate(seeds, 1):
            rr = run_one_improved_ga_ig(
                data=data,
                decoder=base_decoder,
                seed=seed,
                population=population,
                generations=generations,
                crossover_rate=crossover_rate,
                mutation_rate=mutation_rate,
                mutation_max=mutation_max,
                elite=elite,
                tournament_k=tournament_k,
                no_improve=no_improve,
                injection_fraction=injection_fraction,
                diversity_threshold=diversity_threshold,
                use_neh=use_neh,
                use_vns=use_vns,
                vns_freq=vns_freq,
                vns_steps=vns_steps,
                vns_moves=vns_moves,
                vns_stall_moves=vns_stall_moves,
                vns_shakes=vns_shakes,
                vns_critical_top_k=vns_critical_top_k,
                vns_stagnation_trigger=vns_stagnation_trigger,
                crossover_type=crossover_type,
                crossover_weights=crossover_weights,
                use_ig=use_ig,
                ig_stagnation_trigger=ig_stagnation_trigger,
                ig_frequency=ig_frequency,
                ig_iterations=ig_iterations,
                ig_destroy_min=ig_destroy_min,
                ig_destroy_max=ig_destroy_max,
                use_two_step=use_two_step,
                ig_inferior_temperature_pct=ig_inferior_temperature_pct,
                ig_inferior_max_cmax_gap_pct=ig_inferior_max_cmax_gap_pct,
                ig_inferior_cooling=ig_inferior_cooling,
                target_cmax=target_cmax,
                target_tolerance=target_tolerance,
                target_polish_generations=target_polish_generations,
                precomputed_neh=precomputed_neh,
                collect_convergence=collect_convergence,
                diversity_every=diversity_every,
                progress_every=progress_every,
                progress_label=f"Improved GA+IG-V3-ES run {i}/{runs}",
            )
            run_results.append(rr)

    run_results.sort(key=lambda r: r["seed"])
    best = min(run_results, key=lambda r: base.fitness_key(r["best_metrics"]))
    best_metrics, schedule = base_decoder.decode(
        best["best_chromosome"],
        keep_schedule=True,
        validate_chromosome=True,
    )
    total_runtime = time.perf_counter() - total_start

    run_summaries = []
    for i, r in enumerate(run_results, 1):
        run_summaries.append(
            {
                "run": i,
                "seed": int(r["seed"]),
                "makespan_min": float(r["best_metrics"]["makespan_min"]),
                "total_tardiness_min": float(r["best_metrics"]["total_tardiness_min"]),
                "total_flow_time_min": float(r["best_metrics"]["total_flow_time_min"]),
                "runtime_sec": float(r["runtime_sec"]),
                "generations_completed": int(r["generations_completed"]),
                "unique_evaluations": int(r["unique_evaluations"]),
                "vns_improvements": int(r["vns_total_improvements"]),
                "vns_evaluations": int(r["vns_total_evaluations"]),
                "vns_shakes": int(r.get("vns_total_shakes", 0)),
                "ig_calls": int(r.get("ig_calls", 0)),
                "ig_evaluations": int(r.get("ig_total_evaluations", 0)),
                "ig_successful_calls": int(r.get("ig_successful_calls", 0)),
                "ig_cmax_improvements": int(r.get("ig_cmax_improvements", 0)),
                "ig_secondary_improvements": int(r.get("ig_secondary_only_improvements", 0)),
                "ig_best_cmax_gain_min": float(r.get("ig_best_cmax_gain_min", 0.0)),
                "ig_two_step_attempts": int(r.get("ig_two_step_attempts", 0)),
                "ig_bridge_accepted": int(r.get("ig_bridge_accepted", 0)),
                "ig_neutral_bridges": int(r.get("ig_neutral_bridges", 0)),
                "ig_worse_cmax_bridges": int(r.get("ig_worse_cmax_bridges", 0)),
                "ig_two_step_successes": int(r.get("ig_two_step_successes", 0)),
                "ig_max_intermediate_cmax_gap_min": float(
                    r.get("ig_max_intermediate_cmax_gap_min", 0.0)
                ),
                "target_reached": bool(r.get("target_reached", False)),
                "target_reached_generation": r.get("target_reached_generation"),
                "target_polish_stall_final": int(r.get("target_polish_stall_final", 0)),
                "early_stop_reason": str(r.get("early_stop_reason", "generation_limit")),
                "ox_crossovers": int(r["crossover_usage"]["ox"]),
                "pmx_crossovers": int(r["crossover_usage"]["pmx"]),
                "pbx_crossovers": int(r["crossover_usage"]["pbx"]),
            }
        )

    best_run_index = next(i for i, r in enumerate(run_results) if r["seed"] == best["seed"])
    convergence_by_run = (
        [
            {"run": i + 1, "seed": int(r["seed"]), "trace": r["convergence"]}
            for i, r in enumerate(run_results)
        ]
        if collect_convergence
        else None
    )

    ig_statistics = {
        "calls": int(sum(r.get("ig_calls", 0) for r in run_results)),
        "evaluations": int(sum(r.get("ig_total_evaluations", 0) for r in run_results)),
        "successful_calls": int(sum(r.get("ig_successful_calls", 0) for r in run_results)),
        "cmax_improvements": int(sum(r.get("ig_cmax_improvements", 0) for r in run_results)),
        "secondary_improvements": int(
            sum(r.get("ig_secondary_only_improvements", 0) for r in run_results)
        ),
        "best_cmax_gain_min": float(
            max((r.get("ig_best_cmax_gain_min", 0.0) for r in run_results), default=0.0)
        ),
        "two_step_attempts": int(
            sum(r.get("ig_two_step_attempts", 0) for r in run_results)
        ),
        "bridge_accepted": int(
            sum(r.get("ig_bridge_accepted", 0) for r in run_results)
        ),
        "neutral_same_cmax_bridges": int(
            sum(r.get("ig_neutral_bridges", 0) for r in run_results)
        ),
        "worse_cmax_bridges": int(
            sum(r.get("ig_worse_cmax_bridges", 0) for r in run_results)
        ),
        "two_step_successes": int(
            sum(r.get("ig_two_step_successes", 0) for r in run_results)
        ),
        "max_intermediate_cmax_gap_min": float(
            max(
                (r.get("ig_max_intermediate_cmax_gap_min", 0.0) for r in run_results),
                default=0.0,
            )
        ),
    }

    early_stop_statistics = {
        "enabled": bool(target_cmax is not None),
        "target_cmax": float(target_cmax) if target_cmax is not None else None,
        "target_tolerance": float(target_tolerance),
        "target_polish_generations": int(target_polish_generations),
        "target_reached_runs": int(sum(bool(r.get("target_reached", False)) for r in run_results)),
        "target_early_stopped_runs": int(
            sum(r.get("early_stop_reason") == "target_polish_stall" for r in run_results)
        ),
        "mean_generations_completed": float(
            sum(r["generations_completed"] for r in run_results) / len(run_results)
        ),
        "min_generations_completed": int(min(r["generations_completed"] for r in run_results)),
        "max_generations_completed": int(max(r["generations_completed"] for r in run_results)),
    }

    return {
        "instance_name": base_decoder.name,
        "size": codec.n,
        "algorithm": (
            "Improved Permutation GA + NEH + multi OX/PMX/PBX + Elite VNS + "
            "Two-Step Plateau-Triggered IG V3 + Target-Polish Early Stop + FrozenInstanceDecoder"
        ),
        "objective": "LEXICOGRAPHIC_MIN(Cmax, TotalTardiness, TotalFlowTime)",
        "ig_version": "V3_TWO_STEP_CONTROLLED_BRIDGE",
        "early_stop_version": "TARGET_POLISH_V1",
        "population": int(population),
        "generations": int(generations),
        "runs": int(runs),
        "parallel_runs": bool(use_parallel),
        "max_workers": int(max_workers if use_parallel else 1),
        "resources_redundant": bool(base_decoder.resources_redundant),
        "resource_checks_used": bool(base_decoder.use_resources),
        "seed_base": int(seed_base),
        "crossover_rate": float(crossover_rate),
        "crossover_type": crossover_type,
        "crossover_weights_ox_pmx_pbx": [float(x) for x in crossover_weights],
        "mutation_rate": float(mutation_rate),
        "mutation_max": float(mutation_max),
        "elite": int(elite),
        "tournament_k": int(tournament_k),
        "no_improve": int(no_improve),
        "use_neh": bool(use_neh),
        "neh_initialization_runtime_sec": float(neh_runtime),
        "neh_metrics": dict(neh_metrics) if neh_metrics is not None else None,
        "neh_chromosome_idx": precomputed_neh,
        "neh_chromosome": codec.to_job_ids(precomputed_neh) if precomputed_neh is not None else None,
        "use_vns": bool(use_vns),
        "vns_freq": int(vns_freq),
        "vns_steps": int(vns_steps),
        "vns_moves_per_neighborhood": int(vns_moves),
        "vns_stall_moves_per_neighborhood": int(vns_stall_moves),
        "vns_shakes": int(vns_shakes),
        "vns_critical_top_k": int(vns_critical_top_k),
        "vns_stagnation_trigger": int(vns_stagnation_trigger),
        "use_ig": bool(use_ig),
        "ig_config": {
            "enabled": bool(use_ig),
            "stagnation_trigger": int(ig_stagnation_trigger),
            "frequency": int(ig_frequency),
            "iterations_per_call": int(ig_iterations),
            "destroy_min": int(ig_destroy_min),
            "destroy_max": int(ig_destroy_max),
            "destroy_operator": "random_multi_point",
            "repair_operator": "greedy_best_position_lexicographic",
            "two_step_enabled": bool(use_two_step),
            "inferior_bridge_temperature_pct": float(ig_inferior_temperature_pct),
            "inferior_bridge_max_cmax_gap_pct": float(ig_inferior_max_cmax_gap_pct),
            "inferior_bridge_cooling": float(ig_inferior_cooling),
            "acceptance": (
                "strict_final_lexicographic_improvement; "
                "temporary_same-Cmax_or_SA-gated_worse-Cmax_bridge_for_second_step_only"
            ),
            "reference": "Li et al. (2025), DOI 10.3390/math13152401",
        },
        "ig_statistics": ig_statistics,
        "early_stop_config": {
            "enabled": bool(target_cmax is not None),
            "target_cmax": float(target_cmax) if target_cmax is not None else None,
            "target_tolerance": float(target_tolerance),
            "target_polish_generations": int(target_polish_generations),
            "meaning": (
                "best-known target only; not a proof of optimality; stop after target is reached "
                "and no strict lexicographic improvement occurs during the polishing window"
            ),
        },
        "early_stop_statistics": early_stop_statistics,
        "diversity_every": int(diversity_every),
        "progress_every": int(progress_every),
        # Existing output names preserved:
        "makespan_min": float(best_metrics["makespan_min"]),
        "total_tardiness_min": float(best_metrics["total_tardiness_min"]),
        "total_flow_time_min": float(best_metrics["total_flow_time_min"]),
        "runtime_sec": float(total_runtime),
        "convergence_trace": best["convergence"] if collect_convergence else None,
        # Existing compatibility aliases preserved:
        "best_makespan_min": float(best_metrics["makespan_min"]),
        "best_total_tardiness_min": float(best_metrics["total_tardiness_min"]),
        "best_total_flow_time_min": float(best_metrics["total_flow_time_min"]),
        "best_chromosome_idx": best["best_chromosome_idx"],
        "best_chromosome": best["best_chromosome"],
        "best_seed": int(best["seed"]),
        "best_run": int(best_run_index + 1),
        "total_runtime_sec": float(total_runtime),
        "mean_run_runtime_sec": float(
            sum(r["runtime_sec"] for r in run_results) / len(run_results)
        ),
        "run_summaries": run_summaries,
        "convergence_by_run": convergence_by_run,
        "best_run_vns_events": best["vns_events"],
        "best_run_ig_events": best["ig_events"],
        "schedule": schedule,
    }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                fields.append(str(key))
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def save_result_files(result: Mapping, instance_path: Path, output_dir: Path) -> Dict[str, str]:
    """Save target-polish V3 results without overwriting prior experiments."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = instance_path.stem
    json_path = output_dir / f"{stem}__improved_ga_ig_v3_earlystop_result.json"
    summary_csv = output_dir / f"{stem}__improved_ga_ig_v3_earlystop_run_summary.csv"
    convergence_csv = output_dir / f"{stem}__improved_ga_ig_v3_earlystop_convergence.csv"
    schedule_csv = output_dir / f"{stem}__improved_ga_ig_v3_earlystop_schedule.csv"

    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_csv(summary_csv, result.get("run_summaries", []))

    conv_rows: List[dict] = []
    for run_info in result.get("convergence_by_run") or []:
        for row in run_info.get("trace", []):
            out = {"Run": run_info["run"], "Seed": run_info["seed"]}
            out.update(row)
            conv_rows.append(out)
    _write_csv(convergence_csv, conv_rows)
    _write_csv(schedule_csv, result.get("schedule", []))

    return {
        "json": str(json_path),
        "run_summary_csv": str(summary_csv),
        "convergence_csv": str(convergence_csv),
        "schedule_csv": str(schedule_csv),
    }


def _parse_crossover_weights(text: str) -> Tuple[float, float, float]:
    # Reuse the validated parser from the current Improved GA when available.
    parser = getattr(base, "_parse_crossover_weights", None)
    if parser is not None:
        return parser(text)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("Expected three comma-separated weights: OX,PMX,PBX")
    vals = tuple(float(x) for x in parts)
    if any(x < 0 for x in vals) or sum(vals) <= 0:
        raise argparse.ArgumentTypeError("Weights must be non-negative and not all zero")
    return vals  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Solve one frozen AL13 instance with Improved GA + NEH + Elite VNS + "
            "two-step plateau-triggered IG (V3) with optional target-polish early stopping."
        )
    )
    ap.add_argument("--instance", type=Path, required=True, help="Frozen AL13 benchmark JSON")
    ap.add_argument("--output-dir", type=Path, default=None)

    ap.add_argument("--population", type=int, default=200)
    ap.add_argument("--generations", "--epochs", dest="generations", type=int, default=5000)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--seed-base", type=int, default=42)

    ap.add_argument("--crossover-rate", type=float, default=0.90)
    ap.add_argument(
        "--crossover-type",
        choices=["ox", "pmx", "pbx", "multi"],
        default="multi",
        help="multi randomly selects OX/PMX/PBX",
    )
    ap.add_argument(
        "--crossover-weights",
        type=_parse_crossover_weights,
        default=(1.0, 1.0, 1.0),
        metavar="OX,PMX,PBX",
    )

    ap.add_argument("--mutation-rate", type=float, default=0.12)
    ap.add_argument("--mutation-max", type=float, default=0.45)
    ap.add_argument("--elite", type=int, default=1)
    ap.add_argument("--tournament-k", type=int, default=3)
    ap.add_argument(
        "--no-improve",
        type=int,
        default=0,
        help="0 forces all generations; positive enables early stopping",
    )
    ap.add_argument("--injection-fraction", type=float, default=0.25)
    ap.add_argument("--diversity-threshold", type=float, default=0.20)

    ap.add_argument("--use-neh", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--use-vns", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--vns-freq", type=int, default=10)
    ap.add_argument("--vns-steps", type=int, default=8)
    ap.add_argument("--vns-moves", type=int, default=30)
    ap.add_argument("--vns-stall-moves", type=int, default=45)
    ap.add_argument("--vns-shakes", type=int, default=2)
    ap.add_argument("--vns-critical-top-k", type=int, default=8)
    ap.add_argument("--vns-stagnation-trigger", type=int, default=150)

    # IG V3 options.
    ap.add_argument("--use-ig", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--ig-stagnation-trigger", type=int, default=300)
    ap.add_argument("--ig-frequency", type=int, default=300)
    ap.add_argument("--ig-iterations", type=int, default=10)
    ap.add_argument("--ig-destroy-min", type=int, default=4)
    ap.add_argument("--ig-destroy-max", type=int, default=8)
    ap.add_argument(
        "--use-two-step",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Enable V3 temporary inferior bridge + second destroy/repair. "
            "Use --no-use-two-step to reproduce V1 behavior as closely as possible."
        ),
    )
    ap.add_argument(
        "--ig-inferior-temperature-pct",
        type=float,
        default=0.30,
        help=(
            "SA-like bridge temperature as percentage of incumbent Cmax. "
            "Used only for temporary worse-Cmax intermediates."
        ),
    )
    ap.add_argument(
        "--ig-inferior-max-cmax-gap-pct",
        type=float,
        default=1.00,
        help="Maximum worse-Cmax percentage allowed for a temporary bridge.",
    )
    ap.add_argument(
        "--ig-inferior-cooling",
        type=float,
        default=0.90,
        help="Cooling multiplier across IG attempts within one trigger.",
    )

    # Optional practical early stopping around a BEST-KNOWN Cmax target.
    ap.add_argument(
        "--target-cmax",
        type=float,
        default=None,
        help=(
            "Best-known Cmax target in minutes. Reaching it does NOT imply proven optimality; "
            "it starts a polishing window. Omit this option to disable target stopping."
        ),
    )
    ap.add_argument(
        "--target-tolerance",
        type=float,
        default=0.01,
        help="Tolerance in minutes for considering the Cmax target reached.",
    )
    ap.add_argument(
        "--target-polish-generations",
        type=int,
        default=500,
        help=(
            "After target Cmax is reached, stop only after this many generations without "
            "a strict lexicographic improvement in (Cmax, tardiness, flow)."
        ),
    )

    ap.add_argument("--diversity-every", type=int, default=50)
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--sequential-runs", action="store_true")
    ap.add_argument(
        "--save-convergence",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    args = ap.parse_args()

    if not args.instance.exists():
        raise FileNotFoundError(args.instance)

    output_dir = args.output_dir or args.instance.parent
    result = solve_instance(
        args.instance,
        population=args.population,
        generations=args.generations,
        runs=args.runs,
        seed_base=args.seed_base,
        crossover_rate=args.crossover_rate,
        mutation_rate=args.mutation_rate,
        mutation_max=args.mutation_max,
        elite=args.elite,
        tournament_k=args.tournament_k,
        no_improve=args.no_improve,
        injection_fraction=args.injection_fraction,
        diversity_threshold=args.diversity_threshold,
        use_neh=args.use_neh,
        use_vns=args.use_vns,
        vns_freq=args.vns_freq,
        vns_steps=args.vns_steps,
        vns_moves=args.vns_moves,
        vns_stall_moves=args.vns_stall_moves,
        vns_shakes=args.vns_shakes,
        vns_critical_top_k=args.vns_critical_top_k,
        vns_stagnation_trigger=args.vns_stagnation_trigger,
        crossover_type=args.crossover_type,
        crossover_weights=args.crossover_weights,
        use_ig=args.use_ig,
        ig_stagnation_trigger=args.ig_stagnation_trigger,
        ig_frequency=args.ig_frequency,
        ig_iterations=args.ig_iterations,
        ig_destroy_min=args.ig_destroy_min,
        ig_destroy_max=args.ig_destroy_max,
        use_two_step=args.use_two_step,
        ig_inferior_temperature_pct=args.ig_inferior_temperature_pct,
        ig_inferior_max_cmax_gap_pct=args.ig_inferior_max_cmax_gap_pct,
        ig_inferior_cooling=args.ig_inferior_cooling,
        target_cmax=args.target_cmax,
        target_tolerance=args.target_tolerance,
        target_polish_generations=args.target_polish_generations,
        collect_convergence=args.save_convergence,
        parallel_runs=not args.sequential_runs,
        max_workers=args.workers,
        diversity_every=args.diversity_every,
        progress_every=args.progress_every,
    )
    files = save_result_files(result, args.instance, output_dir)

    print("=" * 92)
    print(
        f"{result['instance_name']} | Improved GA+IG-V3-ES Cmax={result['makespan_min']:.3f} min | "
        f"Tard={result['total_tardiness_min']:.3f} | "
        f"Flow={result['total_flow_time_min']:.3f} | wall={result['runtime_sec']:.2f}s"
    )
    print(f"IG statistics    : {result['ig_statistics']}")
    print(f"Early stop stats : {result['early_stop_statistics']}")
    print(f"JSON             : {files['json']}")
    print(f"Run summary CSV  : {files['run_summary_csv']}")
    print(f"Convergence CSV  : {files['convergence_csv']}")
    print(f"Schedule CSV     : {files['schedule_csv']}")


if __name__ == "__main__":
    main()
