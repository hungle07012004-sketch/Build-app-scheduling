#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Performance-optimized permutation GA for ONE frozen AL13 benchmark instance.

The GA methodology is unchanged:
* tournament selection;
* OX crossover;
* mixed insert/swap/reverse mutation;
* elitism;
* adaptive mutation;
* controlled diversity injection;
* multiprocessing only across independent runs.

Runtime improvements are implementation-only.  For instances up to 255 jobs,
GA chromosomes are represented internally as compact ``bytes`` permutations of
integer job indices.  This removes repeated 50-element list->tuple conversions,
shrinks cache keys substantially, and lets the decoder use its numeric hot path.
Public results still report normal Job_ID strings and the same objective fields.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List, Sequence

from benchmark_common import (
    FrozenInstanceDecoder,
    OP_SEQUENCE,
    chromosome_key,
    compact_chromosome_key,
    compact_metrics_dict,
    load_instance,
)


def total_min_workload(decoder: FrozenInstanceDecoder, job: str) -> float:
    """Compatibility helper; initial-population code uses precomputed numeric values."""
    ji = decoder.job_index[job]
    return decoder.min_workload_by_index[ji]


def _pack_chromosome(values: Sequence[int], byte_mode: bool):
    return bytes(values) if byte_mode else tuple(values)


def ox_crossover(p1: Sequence[int], p2: Sequence[int], rng: random.Random):
    """Order crossover with the exact same segment/fill semantics as the old code."""
    n = len(p1)
    if n < 2:
        return p1, p2
    a, b = sorted(rng.sample(range(n), 2))
    byte_mode = isinstance(p1, bytes)

    def child(a_parent, b_parent):
        out = [-1] * n
        segment = a_parent[a : b + 1]
        out[a : b + 1] = segment
        used = set(segment)

        # Same fill position order as:
        # list(range(b+1,n)) + list(range(0,a))
        pos = b + 1
        if pos == n:
            pos = 0
        for gene in b_parent:
            if gene in used:
                continue
            out[pos] = gene
            pos += 1
            if pos == n:
                pos = 0
            if pos == a:
                break
        return bytes(out) if byte_mode else tuple(out)

    return child(p1, p2), child(p2, p1)


def swap_mutation(ch: Sequence[int], rng: random.Random):
    n = len(ch)
    if n < 2:
        return ch
    a, b = rng.sample(range(n), 2)
    if isinstance(ch, bytes):
        out = bytearray(ch)
        out[a], out[b] = out[b], out[a]
        return bytes(out)
    out = list(ch)
    out[a], out[b] = out[b], out[a]
    return tuple(out)


def insert_mutation(ch: Sequence[int], rng: random.Random):
    n = len(ch)
    if n < 2:
        return ch
    a, b = rng.sample(range(n), 2)
    if isinstance(ch, bytes):
        out = bytearray(ch)
        gene = out.pop(a)
        out.insert(b, gene)
        return bytes(out)
    out = list(ch)
    gene = out.pop(a)
    out.insert(b, gene)
    return tuple(out)


def reverse_mutation(ch: Sequence[int], rng: random.Random):
    n = len(ch)
    if n < 2:
        return ch
    a, b = sorted(rng.sample(range(n), 2))
    if isinstance(ch, bytes):
        out = bytearray(ch)
        out[a : b + 1] = out[a : b + 1][::-1]
        return bytes(out)
    out = list(ch)
    out[a : b + 1] = reversed(out[a : b + 1])
    return tuple(out)


def mixed_mutation(ch: Sequence[int], rng: random.Random):
    r = rng.random()
    if r < 0.50:
        return insert_mutation(ch, rng)
    if r < 0.80:
        return swap_mutation(ch, rng)
    return reverse_mutation(ch, rng)


def tournament(pop, keys, rng: random.Random, k: int):
    idxs = rng.sample(range(len(pop)), min(k, len(pop)))
    return pop[min(idxs, key=lambda i: keys[i])]


def population_diversity(pop: Sequence[Sequence[int]], diag_rng: random.Random, samples: int = 64) -> float:
    if len(pop) < 2 or not pop[0]:
        return 0.0
    n = len(pop[0])
    total = 0.0
    count = 0
    max_pairs = len(pop) * (len(pop) - 1) // 2
    if max_pairs <= samples:
        pairs = itertools.combinations(range(len(pop)), 2)
    else:
        pairs = (tuple(diag_rng.sample(range(len(pop)), 2)) for _ in range(samples))
    for i, j in pairs:
        total += sum(a != b for a, b in zip(pop[i], pop[j])) / n
        count += 1
    return total / count if count else 0.0


def unique_ratio(pop: Sequence[Sequence[int]]) -> float:
    # Internal chromosomes are immutable/hashable (bytes or tuple[int,...]).
    return len(set(pop)) / max(1, len(pop))


def create_initial_population(decoder: FrozenInstanceDecoder, rng: random.Random, pop_size: int):
    n_jobs = len(decoder.job_ids)
    byte_mode = n_jobs <= 255
    jobs = list(range(n_jobs))

    max_unique = math.factorial(n_jobs) if n_jobs <= 10 else None
    effective = min(pop_size, max_unique) if max_unique is not None else pop_size
    effective = max(2 if n_jobs > 1 else 1, effective)

    # Exact old tie-breaking: release/priority/Job_ID, workload/release/Job_ID,
    # due/release/priority/Job_ID.
    fifo = sorted(
        jobs,
        key=lambda i: (decoder._release[i], decoder._priority[i], decoder.job_ids[i]),
    )
    spt = sorted(
        jobs,
        key=lambda i: (decoder.min_workload_by_index[i], decoder._release[i], decoder.job_ids[i]),
    )
    edd = sorted(
        jobs,
        key=lambda i: (
            decoder._due[i],
            decoder._release[i],
            decoder._priority[i],
            decoder.job_ids[i],
        ),
    )

    pop = []
    seen = set()

    def add(ch):
        packed = _pack_chromosome(ch, byte_mode)
        if packed not in seen and len(pop) < effective:
            seen.add(packed)
            pop.append(packed)

    add(fifo)
    add(spt)
    add(edd)

    attempts = 0
    max_attempts = max(1000, effective * 300)
    while len(pop) < effective and attempts < max_attempts:
        attempts += 1
        ch = jobs[:]
        rng.shuffle(ch)
        add(ch)

    if len(pop) < effective and n_jobs <= 8:
        perms = list(itertools.permutations(jobs))
        rng.shuffle(perms)
        for p in perms:
            add(p)
            if len(pop) >= effective:
                break

    if len(pop) != effective:
        raise RuntimeError(f"Could create only {len(pop)} unique chromosomes, target={effective}")
    return pop


def evaluate_population(pop, decoder: FrozenInstanceDecoder, cache):
    """Evaluate one population with compact hash keys and compact metric cache.

    cache value = (fitness_key, compact_metrics).  No chromosome conversion is
    required in the hot loop because chromosomes are already hashable bytes or
    integer tuples.
    """
    n = len(pop)
    keys = [None] * n
    metrics = [None] * n
    cache_get = cache.get
    decode = decoder.decode_indexed_compact
    make_key = compact_chromosome_key

    for i, ch in enumerate(pop):
        entry = cache_get(ch)
        if entry is None:
            m = decode(ch, validate_chromosome=False)
            entry = (make_key(m), m)
            cache[ch] = entry
        keys[i], metrics[i] = entry
    return keys, metrics


def adaptive_rate(base: float, maximum: float, diversity: float, stall: int, stall_trigger: int) -> float:
    d_component = max(0.0, min(1.0, (0.35 - diversity) / 0.35))
    s_component = max(0.0, min(1.0, stall / max(1, stall_trigger)))
    pressure = max(d_component, s_component)
    return min(maximum, base + (maximum - base) * pressure)


def run_one_ga(
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
    collect_convergence: bool = False,
    diversity_every: int = 20,
    progress_every: int = 100,
    progress_label: str = "GA",
) -> dict:
    rng = random.Random(seed)
    # Diagnostics do not consume the evolutionary RNG stream.
    diag_rng = random.Random(seed ^ 0x5DEECE66D)
    pop = create_initial_population(decoder, rng, population)
    cache = {}

    best_ch = None
    best_metrics_compact = None
    best_key = (math.inf, math.inf, math.inf)
    stall = 0
    convergence = []
    t0 = time.perf_counter()

    last_div = population_diversity(pop, diag_rng)
    last_ur = unique_ratio(pop)

    if progress_every > 0:
        print(
            f"[{progress_label}] start | seed={seed} | pop={len(pop)} | epochs={generations} | "
            f"resources={'ON' if decoder.use_resources else 'SKIPPED(redundant)'}",
            flush=True,
        )

    n_jobs = len(decoder.job_ids)
    byte_mode = n_jobs <= 255
    base_indices = list(range(n_jobs))

    for gen in range(generations + 1):
        keys, metrics = evaluate_population(pop, decoder, cache)
        order = sorted(range(len(pop)), key=keys.__getitem__)
        best_i = order[0]

        if keys[best_i] < best_key:
            best_key = keys[best_i]
            best_ch = pop[best_i]
            best_metrics_compact = metrics[best_i]
            stall = 0
        else:
            stall += 1

        if gen == 0 or gen % max(1, diversity_every) == 0 or gen == generations:
            last_div = population_diversity(pop, diag_rng)
            last_ur = unique_ratio(pop)

        pm = adaptive_rate(
            mutation_rate,
            mutation_max,
            last_div,
            stall,
            max(20, no_improve or 100),
        )

        if collect_convergence:
            cvals = [m[0] for m in metrics]
            convergence.append(
                {
                    "Generation": gen,
                    "Best_Cmax_Min": float(best_metrics_compact[0]),
                    "Population_Best_Min": min(cvals),
                    "Population_Mean_Min": sum(cvals) / len(cvals),
                    "Population_Worst_Min": max(cvals),
                    "Population_Diversity": last_div,
                    "Unique_Chromosome_Ratio": last_ur,
                    "Mutation_Rate": pm,
                    "Unique_Evaluations": len(cache),
                }
            )

        if progress_every > 0 and (gen == 0 or gen == generations or gen % progress_every == 0):
            elapsed = time.perf_counter() - t0
            pct = 100.0 * gen / max(1, generations)
            eta = (elapsed / gen * (generations - gen)) if gen > 0 else 0.0
            print(
                f"[{progress_label}] epoch {gen:>5}/{generations} ({pct:5.1f}%) | "
                f"best={best_metrics_compact[0]:.3f} | stall={stall} | "
                f"div={last_div:.3f} | evals={len(cache)} | "
                f"elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
                flush=True,
            )

        if gen >= generations:
            break
        if no_improve > 0 and stall >= no_improve:
            break

        keep = min(max(1, elite), len(pop))
        # Chromosomes are immutable; no defensive list copies are needed.
        new_pop = [pop[i] for i in order[:keep]]
        seen = set(new_pop)

        if last_div < diversity_threshold:
            target = min(
                len(pop),
                len(new_pop) + max(1, int(math.ceil(injection_fraction * len(pop)))),
            )
            attempts = 0
            while len(new_pop) < target and attempts < target * 100:
                attempts += 1
                immigrant = base_indices[:]
                rng.shuffle(immigrant)
                packed = _pack_chromosome(immigrant, byte_mode)
                if packed not in seen:
                    seen.add(packed)
                    new_pop.append(packed)

        attempts = 0
        max_attempts = len(pop) * 500
        while len(new_pop) < len(pop) and attempts < max_attempts:
            attempts += 1
            p1 = tournament(pop, keys, rng, tournament_k)
            p2 = tournament(pop, keys, rng, tournament_k)
            for _ in range(5):
                if p2 != p1:
                    break
                p2 = tournament(pop, keys, rng, tournament_k)

            if rng.random() < crossover_rate:
                c1, c2 = ox_crossover(p1, p2, rng)
            else:
                c1, c2 = p1, p2

            if rng.random() < pm:
                c1 = mixed_mutation(c1, rng)
            if rng.random() < pm:
                c2 = mixed_mutation(c2, rng)

            for child in (c1, c2):
                if child not in seen:
                    seen.add(child)
                    new_pop.append(child)
                    if len(new_pop) >= len(pop):
                        break

        # Preserve original fallback semantics: random fill does not require
        # uniqueness after max_attempts is exhausted.
        while len(new_pop) < len(pop):
            ch = base_indices[:]
            rng.shuffle(ch)
            new_pop.append(_pack_chromosome(ch, byte_mode))
        pop = new_pop

    runtime = time.perf_counter() - t0
    if progress_every > 0:
        print(
            f"[{progress_label}] done | best={best_metrics_compact[0]:.3f} | "
            f"time={runtime:.2f}s | evals={len(cache)}",
            flush=True,
        )

    # Convert compact internal representation only once at run completion.
    best_ids = [decoder.job_ids[i] for i in best_ch]
    best_metrics = compact_metrics_dict(best_metrics_compact)

    return {
        "seed": int(seed),
        "best_chromosome": best_ids,
        "best_metrics": best_metrics,
        "runtime_sec": float(runtime),
        "generations_completed": int(gen),
        "unique_evaluations": int(len(cache)),
        "convergence": convergence,
    }


def _worker_run(payload: dict) -> dict:
    """Windows-safe process worker: rebuild decoder inside each process."""
    data = load_instance(payload["instance_path"])
    decoder = FrozenInstanceDecoder(data)
    return run_one_ga(
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
        collect_convergence=payload["collect_convergence"],
        diversity_every=payload["diversity_every"],
        progress_every=payload["progress_every"],
        progress_label=payload["progress_label"],
    )


def solve_instance(
    instance_path: Path | str,
    population: int = 80,
    generations: int = 1500,
    runs: int = 5,
    seed_base: int = 42,
    crossover_rate: float = 0.90,
    mutation_rate: float = 0.12,
    mutation_max: float = 0.45,
    elite: int = 1,
    tournament_k: int = 3,
    no_improve: int = 0,
    injection_fraction: float = 0.25,
    diversity_threshold: float = 0.20,
    collect_convergence: bool = False,
    parallel_runs: bool = True,
    max_workers: int = 0,
    diversity_every: int = 20,
    progress_every: int = 100,
) -> dict:
    instance_path = Path(instance_path)
    data = load_instance(instance_path)
    base_decoder = FrozenInstanceDecoder(data)

    seeds = [seed_base + i * 1009 for i in range(runs)]
    total_start = time.perf_counter()
    run_results = []

    if max_workers <= 0:
        cpu = os.cpu_count() or 2
        max_workers = min(runs, max(1, cpu - 1))
    else:
        max_workers = max(1, min(runs, int(max_workers)))

    use_parallel = bool(parallel_runs and runs > 1 and max_workers > 1)
    print(
        f"[GA] {base_decoder.name} | runs={runs} | mode={'PARALLEL' if use_parallel else 'SEQUENTIAL'} "
        f"| workers={max_workers if use_parallel else 1} | pop={population} | epochs={generations}",
        flush=True,
    )

    if use_parallel:
        payloads = []
        for i, seed in enumerate(seeds, 1):
            payloads.append(
                {
                    "instance_path": str(instance_path),
                    "seed": seed,
                    "population": population,
                    "generations": generations,
                    "crossover_rate": crossover_rate,
                    "mutation_rate": mutation_rate,
                    "mutation_max": mutation_max,
                    "elite": elite,
                    "tournament_k": tournament_k,
                    "no_improve": no_improve,
                    "injection_fraction": injection_fraction,
                    "diversity_threshold": diversity_threshold,
                    "collect_convergence": collect_convergence,
                    "diversity_every": diversity_every,
                    "progress_every": progress_every,
                    "progress_label": f"GA run {i}/{runs}",
                }
            )

        # Only independent runs are parallelized. No nested population/chromosome
        # multiprocessing is introduced.
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_worker_run, p): p for p in payloads}
            for fut in as_completed(futures):
                run_results.append(fut.result())
    else:
        for i, seed in enumerate(seeds, 1):
            rr = run_one_ga(
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
                collect_convergence=collect_convergence,
                diversity_every=diversity_every,
                progress_every=progress_every,
                progress_label=f"GA run {i}/{runs}",
            )
            run_results.append(rr)

    total_runtime = time.perf_counter() - total_start
    run_results.sort(key=lambda r: r["seed"])
    best = min(run_results, key=lambda r: chromosome_key(r["best_metrics"]))
    best_metrics, schedule = base_decoder.decode(
        best["best_chromosome"], keep_schedule=True, validate_chromosome=True
    )

    run_summaries = [
        {
            "run": i + 1,
            "seed": r["seed"],
            "makespan_min": float(r["best_metrics"]["makespan_min"]),
            "total_tardiness_min": float(r["best_metrics"]["total_tardiness_min"]),
            "total_flow_time_min": float(r["best_metrics"]["total_flow_time_min"]),
            "runtime_sec": float(r["runtime_sec"]),
            "generations_completed": int(r["generations_completed"]),
            "unique_evaluations": int(r["unique_evaluations"]),
        }
        for i, r in enumerate(run_results)
    ]

    return {
        "instance_name": base_decoder.name,
        "size": len(base_decoder.job_ids),
        "algorithm": "FAST Permutation GA + feasibility-aware earliest-finish decoder",
        "objective": "MIN_MAKESPAN",
        "population": int(population),
        "generations": int(generations),
        "runs": int(runs),
        "parallel_runs": bool(use_parallel),
        "max_workers": int(max_workers if use_parallel else 1),
        "resources_redundant": bool(base_decoder.resources_redundant),
        "resource_checks_used": bool(base_decoder.use_resources),
        "seed_base": int(seed_base),
        "crossover_rate": float(crossover_rate),
        "mutation_rate": float(mutation_rate),
        "mutation_max": float(mutation_max),
        "elite": int(elite),
        "tournament_k": int(tournament_k),
        "no_improve": int(no_improve),
        "diversity_every": int(diversity_every),
        "progress_every": int(progress_every),
        "best_makespan_min": float(best_metrics["makespan_min"]),
        "best_total_tardiness_min": float(best_metrics["total_tardiness_min"]),
        "best_total_flow_time_min": float(best_metrics["total_flow_time_min"]),
        "best_chromosome": best["best_chromosome"],
        "best_seed": int(best["seed"]),
        "total_runtime_sec": float(total_runtime),
        "mean_run_runtime_sec": float(sum(r["runtime_sec"] for r in run_results) / len(run_results)),
        "run_summaries": run_summaries,
        "schedule": schedule,
        "convergence_by_run": [r["convergence"] for r in run_results] if collect_convergence else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Solve one frozen AL13 instance by optimized Genetic Algorithm.")
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--population", type=int, default=80)
    ap.add_argument("--generations", "--epochs", dest="generations", type=int, default=1500)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed-base", type=int, default=42)
    ap.add_argument("--crossover-rate", type=float, default=0.90)
    ap.add_argument("--mutation-rate", type=float, default=0.12)
    ap.add_argument("--mutation-max", type=float, default=0.45)
    ap.add_argument("--elite", type=int, default=1)
    ap.add_argument("--tournament-k", type=int, default=3)
    ap.add_argument(
        "--no-improve",
        type=int,
        default=0,
        help="0 = force all epochs; positive value enables early stopping",
    )
    ap.add_argument("--injection-fraction", type=float, default=0.25)
    ap.add_argument("--diversity-threshold", type=float, default=0.20)
    ap.add_argument(
        "--diversity-every",
        type=int,
        default=20,
        help="Recompute diversity every N epochs instead of every epoch",
    )
    ap.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N epochs; 0 disables progress output",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel GA run workers; 0=auto, 1=sequential",
    )
    ap.add_argument(
        "--sequential-runs",
        action="store_true",
        help="Disable multiprocessing across independent runs",
    )
    ap.add_argument("--save-convergence", action="store_true")
    args = ap.parse_args()

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
        collect_convergence=args.save_convergence,
        parallel_runs=not args.sequential_runs,
        max_workers=args.workers,
        diversity_every=args.diversity_every,
        progress_every=args.progress_every,
    )

    out = args.output or args.instance.with_name(args.instance.stem + "__ga_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"{result['instance_name']} | GA Cmax={result['best_makespan_min']:.3f} min | "
        f"wall time={result['total_runtime_sec']:.2f}s | epochs={result['generations']} | "
        f"runs={result['runs']} | workers={result['max_workers']}"
    )
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
