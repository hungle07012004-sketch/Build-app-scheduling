#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AL13 V5 Adaptive Steady-State Micro-GA with structural screening + fast equivalent decoder.

Purpose
-------
Reduce wall-clock time spent in the expensive AL13 V5 full decoder while keeping
EXACTLY the same physical scheduling concept and objective as the validated V5
baseline/Improved GA.

This file changes only the SEARCH ENGINE around the decoder.  All physical rules
remain delegated to ``benchmark_common_fast.V5ScheduleDecoder`` through the helper
functions in ``solve_improved_ga_instance.py``:

* 2B auxiliary stream feeding PRE;
* PRE -> FINAL streaming (SS_FIRST_BELT + final synchronization);
* LASER/SCALE handled by OP_2A;
* explicit <=6-belt OIL cycles with OP_2A load and OP_PACK unload;
* SEAL/VISUAL handled by OP_PACK;
* exact 2C batches of 10 with same-key 5+5 remainder pooling;
* sequence-dependent clean-pipeline product setup;
* worker/resource calendars and non-preemption rules.

Search concept
--------------
1) Micro population (mu) + small decoded offspring count (lambda).
2) Each epoch GENERATES a larger cheap candidate pool but only a small screened
   subset is sent to the real V5 decoder.
3) Screening uses ``structural_surrogate`` only to rank candidates.  It NEVER
   replaces the real decoder for fitness/acceptance.
4) Immediate steady-state (mu + lambda) survivor selection.
5) Elite archive retained across diversity injections/restarts.
6) Adaptive mutation + AL13-aware product-block and 2C-partner moves.
7) Hard ``max_decodes`` budget per run, with explicit time/evaluation-to-best
   diagnostics for fair thesis comparison.

Primary objective is exactly MIN_CMAX_FINAL_2C.  Tardiness and flow time remain
reported diagnostics only.

Runtime dependency
------------------
Place this file in the SAME folder as:
    solve_improved_ga_instance.py
    benchmark_common.py
    benchmark_common_fast.py

Python 3.10+
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
    from . import solve_improved_ga_instance as base
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "solve_ss_micro_ga_instance.py must be placed beside "
        "solve_improved_ga_instance.py"
    ) from exc

from .benchmark_common_fast import V5ScheduleDecoder, compact_metrics_dict, fitness_key, load_instance


# ---------------------------------------------------------------------------
# Archive / candidate helpers
# ---------------------------------------------------------------------------


def _update_archive(
    archive: List[Tuple[Tuple[float, ...], object]],
    candidates: Sequence[Tuple[Tuple[float, ...], object]],
    archive_size: int,
) -> List[Tuple[Tuple[float, ...], object]]:
    """Keep the best unique chromosomes ever seen."""
    best_by_ch: Dict[object, Tuple[float, ...]] = {ch: key for key, ch in archive}
    for key, ch in candidates:
        old = best_by_ch.get(ch)
        if old is None or key < old:
            best_by_ch[ch] = key
    merged = sorted(((key, ch) for ch, key in best_by_ch.items()), key=lambda x: x[0])
    return merged[: max(1, int(archive_size))]


def _v5_aware_mutation(ch, decoder: V5ScheduleDecoder, rng: random.Random):
    """Mixed ordinary + AL13-aware permutation mutation, no decoder call."""
    r = rng.random()
    if r < 0.55:
        out = base.mixed_mutation(ch, rng)
    elif r < 0.78:
        out = base._product_block_move(ch, decoder, rng)
    else:
        out = base._batch_partner_move(ch, decoder, rng)
    return base.repair_product_change_remainders(out, decoder)


def _generate_candidate_pool(
    pop,
    keys,
    decoder: V5ScheduleDecoder,
    cache: MutableMapping,
    rng: random.Random,
    candidate_pool: int,
    crossover_rate: float,
    crossover_type: str,
    crossover_weights: Sequence[float],
    mutation_probability: float,
    tournament_k: int,
    crossover_usage: MutableMapping[str, int],
):
    """Generate many CHEAP candidates, preferring chromosomes not decoded before."""
    target = max(1, int(candidate_pool))
    seen = set()
    fresh: List[object] = []
    cached_fallback: List[object] = []
    attempts = 0
    max_attempts = max(200, target * 80)

    while len(fresh) < target and attempts < max_attempts:
        attempts += 1
        p1 = base.tournament(pop, keys, rng, tournament_k)
        p2 = base.tournament(pop, keys, rng, tournament_k)

        if rng.random() < crossover_rate:
            c1, c2, op = base.apply_crossover(
                p1, p2, rng, crossover_type, crossover_weights
            )
            crossover_usage[op] = crossover_usage.get(op, 0) + 1
        else:
            c1, c2 = p1, p2

        for child in (c1, c2):
            # Micro-GA needs relatively high per-child perturbation to avoid clone
            # pressure from the small population.  A second light move is allowed
            # under high adaptive mutation pressure.
            if rng.random() < mutation_probability:
                child = _v5_aware_mutation(child, decoder, rng)
            if mutation_probability >= 0.45 and rng.random() < 0.20:
                child = _v5_aware_mutation(child, decoder, rng)

            child = base.repair_product_change_remainders(child, decoder)
            if child in seen or child in pop:
                continue
            seen.add(child)
            if child in cache:
                cached_fallback.append(child)
            else:
                fresh.append(child)
                if len(fresh) >= target:
                    break

    # If search is revisiting known good structures, cached candidates cost no full
    # decode and are still useful as a fallback.  Keep them after fresh candidates.
    return fresh + cached_fallback


def _screen_candidates(
    candidates,
    decoder: V5ScheduleDecoder,
    rng: random.Random,
    offspring_count: int,
    explore_fraction: float,
):
    """Cheap screening only; selected chromosomes still require real V5 fitness."""
    if not candidates:
        return []

    scored = [(base.structural_surrogate(ch, decoder), ch) for ch in candidates]
    scored.sort(key=lambda x: x[0])
    k = min(max(1, int(offspring_count)), len(scored))

    explore_n = min(k - 1 if k > 1 else 0, int(round(k * max(0.0, min(0.8, explore_fraction)))))
    exploit_n = k - explore_n

    selected = [ch for _s, ch in scored[:exploit_n]]
    remainder = [ch for _s, ch in scored[exploit_n:]]
    if explore_n > 0 and remainder:
        selected.extend(rng.sample(remainder, min(explore_n, len(remainder))))

    # Rare corner case: not enough remainder candidates after sampling.
    if len(selected) < k:
        selected_set = set(selected)
        for _s, ch in scored:
            if ch not in selected_set:
                selected.append(ch)
                selected_set.add(ch)
                if len(selected) >= k:
                    break
    return selected[:k]


def _inject_immigrants(
    pop,
    decoder: V5ScheduleDecoder,
    rng: random.Random,
    count: int,
    protected: Sequence[object] = (),
):
    """Replace worst-side slots later by returning unique random immigrants."""
    n_jobs = len(decoder.jobs)
    byte_mode = n_jobs <= 255
    genes = list(range(n_jobs))
    occupied = set(pop) | set(protected)
    out = []
    attempts = 0
    target = max(0, int(count))
    while len(out) < target and attempts < max(500, target * 200):
        attempts += 1
        seq = genes[:]
        rng.shuffle(seq)
        ch = base.repair_product_change_remainders(base._pack(seq, byte_mode), decoder)
        if ch not in occupied:
            occupied.add(ch)
            out.append(ch)
    return out


# ---------------------------------------------------------------------------
# One SS-Micro-GA run
# ---------------------------------------------------------------------------


class _SearchTimeExpired(Exception):
    """Cooperative stop between complete decoder evaluations."""


def run_one_ss_micro_ga(
    decoder: V5ScheduleDecoder,
    seed: int,
    population: int,
    offspring_count: int,
    candidate_pool: int,
    generations: int,
    max_decodes: int,
    archive_size: int,
    screen_explore_fraction: float,
    crossover_rate: float,
    crossover_type: str,
    crossover_weights: Sequence[float],
    mutation_rate: float,
    mutation_max: float,
    tournament_k: int,
    immigrant_after: int,
    immigrant_count: int,
    restart_after: int,
    restart_fraction: float,
    diversity_threshold: float,
    refined_seed,
    collect_convergence: bool,
    convergence_every: int,
    diversity_every: int,
    progress_every: int,
    progress_label: str,
    deadline: float | None = None,
    no_improve: int = 0,
    min_epochs: int = 0,
    restart_grace: int = 100,
) -> dict:
    rng = random.Random(seed)
    diag_rng = random.Random(seed ^ 0x5DEECE66D)
    pop = base.create_initial_population(decoder, rng, population, refined_seed=refined_seed,
                                         allow_partial=True, deadline=deadline)
    cache: Dict[object, object] = {}

    t0 = time.perf_counter()
    best_ch = None
    best_key = (math.inf,)
    best_metrics = (math.inf, math.inf, len(decoder.jobs), math.inf)
    best_epoch = evals_to_best = 0
    time_to_best = 0.0
    archive = []
    epoch = -1  # Initial population is epoch zero; offspring use epoch + 1.
    improved_this_epoch = False

    # Always obtain one incumbent; stop between complete evaluations thereafter.
    def evaluate_one(ch):
        nonlocal best_ch, best_key, best_metrics, best_epoch, evals_to_best
        nonlocal time_to_best, archive, improved_this_epoch
        if ch not in cache and cache and deadline is not None and time.perf_counter() >= deadline:
            raise _SearchTimeExpired()
        entry = base.evaluate_one(ch, decoder, cache)
        key, metrics, _ = entry
        # Record every completed improvement, including rescue evaluations that
        # exhaust the budget or are followed by a partial-batch deadline.
        if best_ch is None or key < best_key:
            best_ch, best_key, best_metrics = ch, key, metrics
            best_epoch = epoch + 1
            evals_to_best = len(cache)
            time_to_best = time.perf_counter() - t0
            improved_this_epoch = True
            archive = _update_archive(archive, [(key, ch)], archive_size)
        return entry

    def evaluate_population(chromosomes):
        entries = [evaluate_one(ch) for ch in chromosomes]
        return [e[0] for e in entries], [e[1] for e in entries], sum(e[2] for e in entries)

    try:
        for ch in pop:
            evaluate_one(ch)
    except _SearchTimeExpired:
        pop = [ch for ch in pop if ch in cache]
    keys, metrics, init_decodes = evaluate_population(pop)
    order = sorted(range(len(pop)), key=lambda i: keys[i])

    archive = _update_archive([], [(keys[i], pop[i]) for i in order], archive_size)
    convergence: List[dict] = []
    crossover_usage = {"ox": 0, "pmx": 0, "pbx": 0}

    stall = 0
    restart_count = 0
    immigrant_events = 0
    screen_generated = 0
    screen_selected = 0
    screen_discarded = 0
    decoded_offspring = 0
    cache_hits = 0
    stop_reason = "generation_limit"

    last_div = base.population_diversity(pop, diag_rng)
    last_ur = base.unique_ratio(pop)

    def record_convergence(final_reason=""):
        if not collect_convergence:
            return
        # A deadline can interrupt population replacement. Report only completed
        # cached evaluations; logging must never decode or consume random draws.
        cvals = [float(cache[ch][1][0]) for ch in pop if ch in cache]
        row = {
            "Epoch": int(epoch),
            "Best_Cmax_Min": float(best_metrics[0]),
            "Population_Best_Min": min(cvals) if cvals else None,
            "Population_Mean_Min": sum(cvals) / len(cvals) if cvals else None,
            "Population_Worst_Min": max(cvals) if cvals else None,
            "Population_Diversity": float(last_div),
            "Unique_Chromosome_Ratio": float(last_ur),
            "Mutation_Rate": float(base.adaptive_mutation(
                mutation_rate, mutation_max, last_div, stall, max(1, restart_after))),
            "Stall": int(stall),
            "Decoder_Evaluations": int(len(cache)),
            "Evaluations_To_Best": int(evals_to_best),
            "Time_To_Best_Sec": float(time_to_best),
            "Archive_Size": int(len(archive)),
            "Restarts": int(restart_count),
            "Immigrant_Events": int(immigrant_events),
            "Stop_Reason": final_reason,
        }
        if convergence and convergence[-1]["Epoch"] == epoch:
            convergence[-1] = row
        else:
            convergence.append(row)

    if progress_every > 0:
        print(
            f"[{progress_label}] start | seed={seed} | mu={len(pop)} | lambda={offspring_count} | "
            f"pool={candidate_pool} | epochs={generations} | max_decodes={max_decodes} | "
            f"objective=MIN_CMAX_FINAL_2C",
            flush=True,
        )

    last_restart_epoch = -restart_grace
    try:
        for epoch in range(generations + 1):
            improved_this_epoch = False
            if deadline is not None and time.perf_counter() >= deadline:
                stop_reason = "time_limit"
                break
            # Independent of adaptive/restart stall: only objective improvement resets it.
            if (no_improve > 0 and epoch >= min_epochs and epoch - best_epoch >= no_improve
                    and (restart_after <= 0 or restart_count > 0)
                    and epoch - last_restart_epoch >= restart_grace):
                stop_reason = "no_improvement"
                break
            # Re-evaluation is almost always cache-only and gives current population keys.
            keys, metrics, _ = evaluate_population(pop)
            order = sorted(range(len(pop)), key=lambda i: keys[i])
    
            if epoch == 0 or epoch % max(1, diversity_every) == 0:
                last_div = base.population_diversity(pop, diag_rng)
                last_ur = base.unique_ratio(pop)
    
            pm = base.adaptive_mutation(
                mutation_rate, mutation_max, last_div, stall, max(1, restart_after)
            )
    
            if collect_convergence and (
                epoch == 0 or epoch == generations or epoch % max(1, convergence_every) == 0
            ):
                record_convergence()
    
            if progress_every > 0 and (
                epoch == 0 or epoch == generations or epoch % max(1, progress_every) == 0
            ):
                elapsed = time.perf_counter() - t0
                if epoch > 0:
                    eta_epoch = elapsed / epoch * max(0, generations - epoch)
                else:
                    eta_epoch = 0.0
                print(
                    f"[{progress_label}] epoch {epoch:>5}/{generations} | best={best_metrics[0]:.3f} | "
                    f"stall={stall} | div={last_div:.3f} | pm={pm:.3f} | evals={len(cache)}/{max_decodes} | "
                    f"best_eval={evals_to_best} | restarts={restart_count} | elapsed={elapsed:.1f}s | "
                    f"epoch_ETA={eta_epoch:.1f}s",
                    flush=True,
                )
    
            if epoch >= generations:
                stop_reason = "generation_limit"
                break
            if max_decodes > 0 and len(cache) >= max_decodes:
                stop_reason = "decoder_budget"
                break
    
            # --- Controlled immigrant injection after a medium plateau -------------
            # Trigger only at the exact stall threshold, avoiding repeated injection on
            # every later epoch.  A full restart has its own larger threshold.
            if immigrant_after > 0 and stall == immigrant_after:
                remaining = (max_decodes - len(cache)) if max_decodes > 0 else immigrant_count
                inject_n = max(0, min(immigrant_count, remaining))
                immigrants = _inject_immigrants(
                    pop, decoder, rng, inject_n, protected=[ch for _k, ch in archive]
                )
                if immigrants:
                    keep = max(1, len(pop) - len(immigrants))
                    pop = [pop[i] for i in order[:keep]] + immigrants[: len(pop) - keep]
                    immigrant_events += 1
                    keys, metrics, _ = evaluate_population(pop)
                    order = sorted(range(len(pop)), key=lambda i: keys[i])
    
            # --- Strong restart after a long plateau -------------------------------
            if restart_after > 0 and stall >= restart_after:
                restart_count += 1
                last_restart_epoch = epoch
                current_ranked = [pop[i] for i in order]
                desired_replace = max(1, int(round(len(pop) * max(0.0, min(0.95, restart_fraction)))))
                remaining = (max_decodes - len(cache)) if max_decodes > 0 else desired_replace
                replace_n = max(0, min(desired_replace, remaining))
                keep_n = max(1, len(pop) - replace_n)
                archive_ch = [ch for _k, ch in archive]
                survivors = []
                seen = set()
                # Prefer archived global elites, then current elites.
                for ch in archive_ch + current_ranked:
                    if ch not in seen:
                        seen.add(ch)
                        survivors.append(ch)
                        if len(survivors) >= keep_n:
                            break
                immigrants = _inject_immigrants(
                    survivors, decoder, rng, replace_n, protected=archive_ch
                )
                new_pop = survivors + immigrants
                # If the hard decode budget prevents a full restart, retain cached
                # current chromosomes rather than exceeding the budget.
                for ch in current_ranked:
                    if len(new_pop) >= len(pop):
                        break
                    if ch not in new_pop:
                        new_pop.append(ch)
                pop = new_pop[:population]
                stall = 0
                keys, metrics, _ = evaluate_population(pop)
                order = sorted(range(len(pop)), key=lambda i: keys[i])
                last_div = base.population_diversity(pop, diag_rng)
                last_ur = base.unique_ratio(pop)
                pm = base.adaptive_mutation(
                    mutation_rate, mutation_max, last_div, stall, max(1, restart_after)
                )
    
            # Low-diversity rescue, independent of objective stall.
            if last_div < diversity_threshold:
                desired = max(1, min(immigrant_count, len(pop) // 3 or 1))
                remaining = (max_decodes - len(cache)) if max_decodes > 0 else desired
                count = max(0, min(desired, remaining))
                immigrants = _inject_immigrants(
                    pop, decoder, rng, count, protected=[ch for _k, ch in archive]
                )
                if immigrants:
                    keep = max(1, len(pop) - len(immigrants))
                    pop = [pop[i] for i in order[:keep]] + immigrants[: len(pop) - keep]
                    immigrant_events += 1
                    keys, metrics, _ = evaluate_population(pop)
                    order = sorted(range(len(pop)), key=lambda i: keys[i])
    
            # --- Generate many cheap candidates, decode only a screened subset ------
            proposed = _generate_candidate_pool(
                pop=pop,
                keys=keys,
                decoder=decoder,
                cache=cache,
                rng=rng,
                candidate_pool=candidate_pool,
                crossover_rate=crossover_rate,
                crossover_type=crossover_type,
                crossover_weights=crossover_weights,
                mutation_probability=pm,
                tournament_k=tournament_k,
                crossover_usage=crossover_usage,
            )
    
            if not proposed:
                # Emergency fresh candidates; still go through normal screening/decode.
                proposed = _inject_immigrants(pop, decoder, rng, candidate_pool)
    
            screen_generated += len(proposed)
            selected = _screen_candidates(
                proposed,
                decoder,
                rng,
                offspring_count=offspring_count,
                explore_fraction=screen_explore_fraction,
            )
            screen_selected += len(selected)
            screen_discarded += max(0, len(proposed) - len(selected))
    
            evaluated = []
            for ch in selected:
                if max_decodes > 0 and len(cache) >= max_decodes and ch not in cache:
                    break
                key, m, d = evaluate_one(ch)
                decoded_offspring += int(d)
                cache_hits += int(d == 0)
                evaluated.append((key, ch, m))
    
            if not evaluated:
                if max_decodes > 0 and len(cache) >= max_decodes:
                    stop_reason = "decoder_budget"
                    break
                stall = 0 if improved_this_epoch else stall + 1
                continue
    
            # Immediate steady-state (mu + lambda) replacement.
            combined: Dict[object, Tuple[Tuple[float, ...], object]] = {}
            for i, ch in enumerate(pop):
                combined[ch] = (keys[i], metrics[i])
            for key, ch, m in evaluated:
                old = combined.get(ch)
                if old is None or key < old[0]:
                    combined[ch] = (key, m)
    
            ranked = sorted(
                ((key, ch, m) for ch, (key, m) in combined.items()),
                key=lambda x: x[0],
            )
            pop = [ch for key, ch, m in ranked[:population]]
    
            # Update archive from every newly decoded candidate, even if it misses the
            # current micro-population cut.
            archive = _update_archive(
                archive,
                [(key, ch) for key, ch, _m in evaluated],
                archive_size,
            )
    
            # Ensure archive leader is never accidentally absent from population.
            if archive:
                leader_key, leader_ch = archive[0]
                if leader_ch not in pop:
                    pop[-1] = leader_ch
    
            # Check global improvement immediately after this small offspring batch.
            keys, metrics, _ = evaluate_population(pop)
            order = sorted(range(len(pop)), key=lambda i: keys[i])
            if improved_this_epoch:
                stall = 0
            else:
                stall += 1
    except _SearchTimeExpired:
        stop_reason = "time_limit"
        # evaluate_one has already retained every completed improvement.

    runtime = time.perf_counter() - t0
    record_convergence(stop_reason)
    best_ids = [decoder.jobs[i] for i in best_ch]

    if progress_every > 0:
        print(
            f"[{progress_label}] done | best={best_metrics[0]:.3f} | best_epoch={best_epoch} | "
            f"time={runtime:.2f}s | evals={len(cache)} | evals_to_best={evals_to_best} | "
            f"stop={stop_reason}",
            flush=True,
        )

    return {
        "seed": int(seed),
        "best_chromosome": best_ids,
        "best_metrics": compact_metrics_dict(best_metrics),
        "best_epoch": int(best_epoch),
        "runtime_sec": float(runtime),
        "epochs_completed": int(epoch),
        "unique_evaluations": int(len(cache)),
        "evaluations_to_best": int(evals_to_best),
        "time_to_best_sec": float(time_to_best),
        "stop_reason": stop_reason,
        "restart_count": int(restart_count),
        "immigrant_events": int(immigrant_events),
        "final_diversity": float(last_div),
        "final_unique_ratio": float(last_ur),
        "screen_candidates_generated": int(screen_generated),
        "screen_candidates_selected": int(screen_selected),
        "screen_candidates_discarded": int(screen_discarded),
        "decoded_offspring": int(decoded_offspring),
        "cache_hits": int(cache_hits),
        "archive_final_size": int(len(archive)),
        "crossover_usage": dict(crossover_usage),
        "convergence": convergence,
    }


# ---------------------------------------------------------------------------
# Multi-run wrapper
# ---------------------------------------------------------------------------


def _worker_run(payload: dict) -> dict:
    data = load_instance(payload["instance_path"])
    decoder = V5ScheduleDecoder(data)
    return run_one_ss_micro_ga(
        decoder=decoder,
        seed=payload["seed"],
        population=payload["population"],
        offspring_count=payload["offspring_count"],
        candidate_pool=payload["candidate_pool"],
        generations=payload["generations"],
        max_decodes=payload["max_decodes"],
        archive_size=payload["archive_size"],
        screen_explore_fraction=payload["screen_explore_fraction"],
        crossover_rate=payload["crossover_rate"],
        crossover_type=payload["crossover_type"],
        crossover_weights=payload["crossover_weights"],
        mutation_rate=payload["mutation_rate"],
        mutation_max=payload["mutation_max"],
        tournament_k=payload["tournament_k"],
        immigrant_after=payload["immigrant_after"],
        immigrant_count=payload["immigrant_count"],
        restart_after=payload["restart_after"],
        restart_fraction=payload["restart_fraction"],
        diversity_threshold=payload["diversity_threshold"],
        refined_seed=payload.get("refined_seed"),
        collect_convergence=payload["collect_convergence"],
        convergence_every=payload["convergence_every"],
        diversity_every=payload["diversity_every"],
        progress_every=payload["progress_every"],
        progress_label=payload["progress_label"],
        deadline=payload.get("deadline"), no_improve=payload.get("no_improve",0),
        min_epochs=payload.get("min_epochs",0), restart_grace=payload.get("restart_grace",100),
    )


def solve_instance(
    instance_path: Path | str,
    population: int = 24,
    offspring_count: int = 6,
    candidate_pool: int = 18,
    generations: int = 3000,
    max_decodes: int = 12000,
    runs: int = 5,
    seed_base: int = 42,
    archive_size: int = 6,
    screen_explore_fraction: float = 0.33,
    crossover_rate: float = 0.90,
    crossover_type: str = "multi",
    crossover_weights: Sequence[float] = (1.0, 1.0, 1.0),
    mutation_rate: float = 0.15,
    mutation_max: float = 0.60,
    tournament_k: int = 3,
    immigrant_after: int = 150,
    immigrant_count: int = 6,
    restart_after: int = 400,
    restart_fraction: float = 0.60,
    diversity_threshold: float = 0.25,
    use_refined_seed: bool = True,
    seed_decode_budget: int = 60,
    seed_positions_per_job: int = 4,
    collect_convergence: bool = True,
    convergence_every: int = 25,
    parallel_runs: bool = True,
    max_workers: int = 5,
    diversity_every: int = 20,
    progress_every: int = 100,
    time_limit: float = 0,
    no_improve: int = 0,
    min_epochs: int = 0,
    restart_grace: int = 100,
) -> dict:
    if time_limit < 0 or no_improve < 0 or min_epochs < 0 or restart_grace < 0:
        raise ValueError("Search limits must be non-negative; zero disables time/patience limits")
    instance_path = Path(instance_path)
    data = load_instance(instance_path)
    base_decoder = V5ScheduleDecoder(data)

    if population < 4:
        raise ValueError("population (mu) must be >= 4")
    if offspring_count < 1:
        raise ValueError("offspring_count (lambda) must be >= 1")
    if candidate_pool < offspring_count:
        raise ValueError("candidate_pool must be >= offspring_count")
    if generations < 0:
        raise ValueError("generations must be >= 0")
    if max_decodes < 0:
        raise ValueError("max_decodes must be >= 0 (0 disables decode budget)")
    if max_decodes > 0 and max_decodes < population:
        raise ValueError("max_decodes must be 0 or at least population so the initial micro-population can be evaluated")
    if runs < 1:
        raise ValueError("runs must be >= 1")
    if archive_size < 1:
        raise ValueError("archive_size must be >= 1")
    if not (0.0 <= screen_explore_fraction <= 0.8):
        raise ValueError("screen_explore_fraction must be in [0, 0.8]")
    if crossover_type not in {"ox", "pmx", "pbx", "multi"}:
        raise ValueError("crossover_type must be ox, pmx, pbx, or multi")
    if len(crossover_weights) != 3 or any(float(x) < 0 for x in crossover_weights) or sum(crossover_weights) <= 0:
        raise ValueError("crossover_weights must be 3 non-negative values with positive sum")
    if restart_after > 0 and immigrant_after > 0 and immigrant_after >= restart_after:
        raise ValueError("immigrant_after should be smaller than restart_after")

    total_start = time.perf_counter()
    deadline = total_start + time_limit if time_limit > 0 else None

    # One deterministic refined seed is built ONCE, outside independent runs.
    refined_seed = None
    seed_info = None
    seed_runtime = 0.0
    if use_refined_seed:
        st = time.perf_counter()
        refined_seed, seed_info = base.build_insertion_refined_seed(
            base_decoder,
            decode_budget=seed_decode_budget,
            positions_per_job=seed_positions_per_job,
            deadline=deadline,
        )
        seed_runtime = time.perf_counter() - st
        print(
            f"[SS-Micro-GA seed] {base_decoder.name} | Cmax={seed_info['cmax_min']:.3f} | "
            f"seed_decodes={seed_info['new_decodes']} | time={seed_runtime:.2f}s",
            flush=True,
        )

    seeds = [seed_base + i * 1009 for i in range(runs)]
    if max_workers <= 0:
        cpu = os.cpu_count() or 2
        max_workers = min(runs, max(1, cpu - 1))
    else:
        max_workers = max(1, min(runs, int(max_workers)))
    use_parallel = bool(parallel_runs and runs > 1 and max_workers > 1 and deadline is None)

    print(
        f"[SS-Micro-GA V5] {base_decoder.name} | n={len(base_decoder.jobs)} | runs={runs} | "
        f"mode={'PARALLEL' if use_parallel else 'SEQUENTIAL'} | workers={max_workers if use_parallel else 1} | "
        f"mu={population} | lambda={offspring_count} | pool={candidate_pool} | epochs={generations} | "
        f"max_decodes={max_decodes}",
        flush=True,
    )

    common = {
        "deadline": deadline, "no_improve": no_improve,
        "min_epochs": min_epochs, "restart_grace": restart_grace,
        "instance_path": str(instance_path),
        "population": int(population),
        "offspring_count": int(offspring_count),
        "candidate_pool": int(candidate_pool),
        "generations": int(generations),
        "max_decodes": int(max_decodes),
        "archive_size": int(archive_size),
        "screen_explore_fraction": float(screen_explore_fraction),
        "crossover_rate": float(crossover_rate),
        "crossover_type": str(crossover_type),
        "crossover_weights": [float(x) for x in crossover_weights],
        "mutation_rate": float(mutation_rate),
        "mutation_max": float(mutation_max),
        "tournament_k": int(tournament_k),
        "immigrant_after": int(immigrant_after),
        "immigrant_count": int(immigrant_count),
        "restart_after": int(restart_after),
        "restart_fraction": float(restart_fraction),
        "diversity_threshold": float(diversity_threshold),
        "refined_seed": refined_seed,
        "collect_convergence": bool(collect_convergence),
        "convergence_every": int(convergence_every),
        "diversity_every": int(diversity_every),
        "progress_every": int(progress_every),
    }

    run_results = []
    if use_parallel:
        payloads = []
        for i, seed in enumerate(seeds, 1):
            p = dict(common)
            p.update({"seed": seed, "progress_label": f"SS-Micro-GA run {i}/{runs}"})
            payloads.append(p)
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_worker_run, p): p for p in payloads}
            for fut in as_completed(futures):
                run_results.append(fut.result())
    else:
        for i, seed in enumerate(seeds, 1):
            if run_results and deadline is not None and time.perf_counter() >= deadline:
                break
            run_results.append(run_one_ss_micro_ga(
                decoder=base_decoder,
                seed=seed,
                population=population,
                offspring_count=offspring_count,
                candidate_pool=candidate_pool,
                generations=generations,
                max_decodes=max_decodes,
                archive_size=archive_size,
                screen_explore_fraction=screen_explore_fraction,
                crossover_rate=crossover_rate,
                crossover_type=crossover_type,
                crossover_weights=crossover_weights,
                mutation_rate=mutation_rate,
                mutation_max=mutation_max,
                tournament_k=tournament_k,
                immigrant_after=immigrant_after,
                immigrant_count=immigrant_count,
                restart_after=restart_after,
                restart_fraction=restart_fraction,
                diversity_threshold=diversity_threshold,
                refined_seed=refined_seed,
                collect_convergence=collect_convergence,
                convergence_every=convergence_every,
                diversity_every=diversity_every,
                progress_every=progress_every,
                progress_label=f"SS-Micro-GA run {i}/{runs}",
                deadline=deadline, no_improve=no_improve,
                min_epochs=min_epochs, restart_grace=restart_grace,
            ))

    run_results.sort(key=lambda r: r["seed"])
    best = min(run_results, key=lambda r: fitness_key(r["best_metrics"]))
    run_summaries = []
    for i, r in enumerate(run_results, 1):
        generated = int(r["screen_candidates_generated"])
        discarded = int(r["screen_candidates_discarded"])
        run_summaries.append({
            "run": i,
            "seed": int(r["seed"]),
            "makespan_min": float(r["best_metrics"]["makespan_min"]),
            "runtime_sec": float(r["runtime_sec"]),
            "best_epoch": int(r["best_epoch"]),
            "epochs_completed": int(r["epochs_completed"]),
            "unique_evaluations": int(r["unique_evaluations"]),
            "evaluations_to_best": int(r["evaluations_to_best"]),
            "time_to_best_sec": float(r["time_to_best_sec"]),
            "stop_reason": str(r["stop_reason"]),
            "restart_count": int(r["restart_count"]),
            "immigrant_events": int(r["immigrant_events"]),
            "final_diversity": float(r["final_diversity"]),
            "final_unique_ratio": float(r["final_unique_ratio"]),
            "screen_candidates_generated": generated,
            "screen_candidates_selected": int(r["screen_candidates_selected"]),
            "screen_candidates_discarded": discarded,
            "screen_rejection_ratio": float(discarded / generated) if generated else 0.0,
            "decoded_offspring": int(r["decoded_offspring"]),
            "cache_hits": int(r["cache_hits"]),
            "archive_final_size": int(r["archive_final_size"]),
            "ox_crossovers": int(r["crossover_usage"]["ox"]),
            "pmx_crossovers": int(r["crossover_usage"]["pmx"]),
            "pbx_crossovers": int(r["crossover_usage"]["pbx"]),
        })

    if not math.isfinite(float(best["best_metrics"]["makespan_min"])):
        # An unsuccessful heuristic search is not a solver crash or a proof of
        # infeasibility. Do not try exporting an infinite-fitness incumbent.
        diagnostic = "No feasible candidate was found."
        try:
            base_decoder.decode(best["best_chromosome"], keep_schedule=False)
        except RuntimeError as exc:
            diagnostic = str(exc)
        return {
            "feasible": False, "status_name": "No feasible schedule found",
            "message": "GA did not find a feasible schedule within the search limits. "
                       "Review the selected planning horizon and staffed calendar, or try fewer jobs. "
                       "This does not prove the problem is infeasible.",
            "diagnostic": diagnostic,
            "total_runtime_sec": time.perf_counter() - total_start,
            "search_stop_reason": best["stop_reason"],
            "unique_evaluations": sum(r["unique_evaluations"] for r in run_results),
            "completed_runs": len(run_results), "schedule": [],
            "run_summaries": run_summaries,
            "convergence_by_run": [
                {"run": i + 1, "seed": int(r["seed"]), "trace": r["convergence"]}
                for i, r in enumerate(run_results)
            ] if collect_convergence else None,
        }
    best_metrics, schedule = base_decoder.decode(
        best["best_chromosome"], keep_schedule=True, validate_chromosome=True
    )
    total_runtime = time.perf_counter() - total_start

    best_run_index = next(i for i, r in enumerate(run_results) if r["seed"] == best["seed"])

    return {
        "instance_name": base_decoder.name,
        "size": len(base_decoder.jobs),
        "schema_version": data["metadata"]["schema_version"],
        "algorithm": "AL13 V5 Adaptive Steady-State Micro-GA + structural screening + elite archive + fast equivalent decoder",
        "objective": "MIN_CMAX_FINAL_2C",
        "population_mu": int(population),
        "offspring_lambda": int(offspring_count),
        "candidate_pool": int(candidate_pool),
        "generations": int(generations),
        "max_decodes_per_run": int(max_decodes),
        "runs": int(runs),
        "completed_runs": len(run_results),
        "search_stop_reason": "time_limit" if len(run_results) < runs or any(r["stop_reason"] == "time_limit" for r in run_results) else run_results[-1]["stop_reason"],
        "search_time_limit_sec": time_limit,
        "early_stop_config": {"no_improve": no_improve, "min_epochs": min_epochs, "restart_grace": restart_grace},
        "parallel_runs": bool(use_parallel),
        "max_workers": int(max_workers if use_parallel else 1),
        "archive_size": int(archive_size),
        "screen_explore_fraction": float(screen_explore_fraction),
        "crossover_rate": float(crossover_rate),
        "crossover_type": crossover_type,
        "crossover_weights_ox_pmx_pbx": [float(x) for x in crossover_weights],
        "mutation_rate": float(mutation_rate),
        "mutation_max": float(mutation_max),
        "tournament_k": int(tournament_k),
        "immigrant_after": int(immigrant_after),
        "immigrant_count": int(immigrant_count),
        "restart_after": int(restart_after),
        "restart_fraction": float(restart_fraction),
        "diversity_threshold": float(diversity_threshold),
        "use_refined_seed": bool(use_refined_seed),
        "refined_seed_runtime_sec": float(seed_runtime),
        "refined_seed_info": seed_info,
        "best_makespan_min": float(best_metrics["makespan_min"]),
        "best_total_tardiness_min": float(best_metrics["total_tardiness_min"]),
        "best_total_flow_time_min": float(best_metrics["total_flow_time_min"]),
        "best_chromosome": best["best_chromosome"],
        "best_seed": int(best["seed"]),
        "best_run": int(best_run_index + 1),
        "best_epoch": int(best["best_epoch"]),
        "best_evaluations_to_best": int(best["evaluations_to_best"]),
        "best_time_to_best_sec": float(best["time_to_best_sec"]),
        "total_runtime_sec": float(total_runtime),
        "mean_run_runtime_sec": float(sum(r["runtime_sec"] for r in run_results) / len(run_results)),
        "run_summaries": run_summaries,
        "convergence_by_run": [
            {"run": i + 1, "seed": int(r["seed"]), "trace": r["convergence"]}
            for i, r in enumerate(run_results)
        ] if collect_convergence else None,
        "schedule": schedule,
    }


# ---------------------------------------------------------------------------
# Outputs / CLI
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
                seen.add(key)
                fields.append(str(key))
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def save_result_files(result: Mapping, instance_path: Path, output_dir: Path) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = instance_path.stem
    json_path = output_dir / f"{stem}__ss_micro_ga_fast_v5_result.json"
    summary_csv = output_dir / f"{stem}__ss_micro_ga_fast_v5_run_summary.csv"
    convergence_csv = output_dir / f"{stem}__ss_micro_ga_fast_v5_convergence.csv"
    schedule_csv = output_dir / f"{stem}__ss_micro_ga_fast_v5_schedule.csv"

    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_csv(summary_csv, result.get("run_summaries", []))

    conv_rows = []
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


def _parse_weights(text: str) -> Tuple[float, float, float]:
    return base._parse_weights(text)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="AL13 V5 Adaptive Steady-State Micro-GA with structural screening"
    )
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, default=None)

    ap.add_argument("--population", "--mu", dest="population", type=int, default=24)
    ap.add_argument("--offspring", "--lambda", dest="offspring_count", type=int, default=6)
    ap.add_argument("--candidate-pool", type=int, default=18)
    ap.add_argument("--generations", "--epochs", dest="generations", type=int, default=3000)
    ap.add_argument("--max-decodes", type=int, default=12000,
                    help="Hard NEW full-V5 decode budget per run; 0 disables budget")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed-base", type=int, default=42)

    ap.add_argument("--archive-size", type=int, default=6)
    ap.add_argument("--screen-explore-fraction", type=float, default=0.33,
                    help="Fraction of decoded offspring sampled from outside the surrogate top set")

    ap.add_argument("--crossover-rate", type=float, default=0.90)
    ap.add_argument("--crossover-type", choices=["ox", "pmx", "pbx", "multi"], default="multi")
    ap.add_argument("--crossover-weights", type=_parse_weights, default=(1.0, 1.0, 1.0))
    ap.add_argument("--mutation-rate", type=float, default=0.15)
    ap.add_argument("--mutation-max", type=float, default=0.60)
    ap.add_argument("--tournament-k", type=int, default=3)

    ap.add_argument("--immigrant-after", type=int, default=150)
    ap.add_argument("--immigrant-count", type=int, default=6)
    ap.add_argument("--restart-after", type=int, default=400)
    ap.add_argument("--restart-fraction", type=float, default=0.60)
    ap.add_argument("--diversity-threshold", type=float, default=0.25)

    ap.add_argument("--use-refined-seed", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--seed-decode-budget", type=int, default=60)
    ap.add_argument("--seed-positions-per-job", type=int, default=4)

    ap.add_argument("--convergence-every", type=int, default=25)
    ap.add_argument("--diversity-every", type=int, default=20)
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--sequential-runs", action="store_true")
    ap.add_argument("--save-convergence", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()

    if not args.instance.exists():
        raise FileNotFoundError(args.instance)

    result = solve_instance(
        args.instance,
        population=args.population,
        offspring_count=args.offspring_count,
        candidate_pool=args.candidate_pool,
        generations=args.generations,
        max_decodes=args.max_decodes,
        runs=args.runs,
        seed_base=args.seed_base,
        archive_size=args.archive_size,
        screen_explore_fraction=args.screen_explore_fraction,
        crossover_rate=args.crossover_rate,
        crossover_type=args.crossover_type,
        crossover_weights=args.crossover_weights,
        mutation_rate=args.mutation_rate,
        mutation_max=args.mutation_max,
        tournament_k=args.tournament_k,
        immigrant_after=args.immigrant_after,
        immigrant_count=args.immigrant_count,
        restart_after=args.restart_after,
        restart_fraction=args.restart_fraction,
        diversity_threshold=args.diversity_threshold,
        use_refined_seed=args.use_refined_seed,
        seed_decode_budget=args.seed_decode_budget,
        seed_positions_per_job=args.seed_positions_per_job,
        collect_convergence=args.save_convergence,
        convergence_every=args.convergence_every,
        parallel_runs=not args.sequential_runs,
        max_workers=args.workers,
        diversity_every=args.diversity_every,
        progress_every=args.progress_every,
    )

    outdir = args.output_dir or args.instance.parent
    files = save_result_files(result, args.instance, outdir)

    print("=" * 96)
    print(
        f"{result['instance_name']} | SS-Micro-GA V5 Cmax={result['best_makespan_min']:.3f} min | "
        f"wall={result['total_runtime_sec']:.2f}s | best_epoch={result['best_epoch']} | "
        f"best_eval={result['best_evaluations_to_best']} | "
        f"time_to_best={result['best_time_to_best_sec']:.2f}s"
    )
    for k, v in files.items():
        print(f"{k:18}: {v}")


if __name__ == "__main__":
    main()
