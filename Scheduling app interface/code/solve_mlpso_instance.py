#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solve ONE frozen AL13 benchmark instance with permutation-based MLPSO.

This solver intentionally reuses the existing ``FrozenInstanceDecoder`` without
modifying it. Therefore all AL13 feasibility logic remains exactly the same as in
``benchmark_common.py``: release/component readiness, precedence and transfer
lags, eligible workstations, effective working windows, planned downtime,
workstation non-overlap, and reusable-resource checks when they are binding.

Primary objective
-----------------
MINIMIZE MAKESPAN (C_max) ONLY.

Particle representation
-----------------------
Each particle position is a discrete permutation of Job_ID values. Workstation
assignment and operation timing are NOT encoded in the particle; they are produced
by the unchanged ``FrozenInstanceDecoder``.

MLPSO adaptation
----------------
The implementation follows the discrete MLPSO ideas of Zou et al. (2025):
* 1-2 NEH-based seed permutations + random particles;
* equal-sized subpopulations formed from initial Cmax-ranked particles;
* swap mutation with MR;
* learning from P_best with PCR and from subpopulation G_best with GCR;
* MBX when the two crossover inputs are identical, otherwise PMX;
* collaborative migration between superior and inferior subpopulations;
* local search applied only to the overall historical G_best.

Important AL13 adaptation
-------------------------
The paper's initialization/local search use limited-buffer blocking time and the
first blocked job. AL13's frozen decoder does not model that paper-specific buffer
state, and this file MUST NOT alter the decoder. Consequently, NEH is evaluated by
Cmax through the existing decoder, and G_best local search uses bounded Insert/Swap
moves. This changes only the metaheuristic search engine, not the scheduling model.

The hot loops use parallel arrays rather than per-particle dict/object instances.
Fitness caching stores only scalar Cmax values. Full metric dictionaries and the
schedule are produced only for the final best solution.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from benchmark_common import FrozenInstanceDecoder, OP_SEQUENCE, load_instance

EPS = 1e-9


# ---------------------------------------------------------------------------
# Objective / evaluation helpers
# ---------------------------------------------------------------------------

def total_min_workload(decoder: FrozenInstanceDecoder, job: str) -> float:
    """Minimum total processing workload used only to build NEH priority orders."""
    total = 0.0
    for op in OP_SEQUENCE:
        total += min(p for _station, p in decoder.options[(job, op)])
    return total


def eval_cmax(
    permutation: Sequence[str],
    decoder: FrozenInstanceDecoder,
    cache: Dict[Tuple[str, ...], float],
) -> float:
    """Evaluate Cmax only, using the unchanged decoder and a scalar cache.

    ``validate_chromosome=False`` also permits partial permutations during NEH
    construction. The decoder itself is not changed.
    """
    key = tuple(permutation)
    value = cache.get(key)
    if value is not None:
        return value
    metrics, _ = decoder.decode(
        permutation,
        keep_schedule=False,
        validate_chromosome=False,
    )
    value = float(metrics["makespan_min"])
    cache[key] = value
    return value


def strictly_better(a: float, b: float) -> bool:
    return a < b - EPS


# ---------------------------------------------------------------------------
# Discrete permutation operators: swap, insert, MBX, PMX
# ---------------------------------------------------------------------------

def swap_mutation_inplace(ch: List[str], rng: random.Random) -> None:
    n = len(ch)
    if n < 2:
        return
    i, j = rng.sample(range(n), 2)
    ch[i], ch[j] = ch[j], ch[i]


def insert_move(ch: Sequence[str], i: int, j: int) -> List[str]:
    """Move gene at i to insertion index j and return a new permutation."""
    out = list(ch)
    if len(out) < 2 or i == j:
        return out
    gene = out.pop(i)
    # Python list.insert interprets j after removal; this is fine for a discrete
    # relocation neighborhood as long as j is sampled from the current index set.
    out.insert(j, gene)
    return out


def moving_based_crossover(parent: Sequence[str], rng: random.Random) -> List[str]:
    """Moving-Based Crossover (MBX) for identical crossover inputs.

    The MLPSO paper's Fig. 3 uses three cut boundaries x < y < z. From an
    identical parent [1..10], one offspring moves block [x:y] to the front and
    the other moves block [y:z] to the end. We generate those two MBX candidates
    and return one at random.

    This guarantees movement even when current position == P_best/G_best.
    """
    n = len(parent)
    if n < 2:
        return list(parent)
    if n < 4:
        out = list(parent)
        swap_mutation_inplace(out, rng)
        return out

    # Boundaries lie between genes: 1..n-1. Need three distinct boundaries.
    x, y, z = sorted(rng.sample(range(1, n), 3))
    p = list(parent)

    # Fig. 3 pattern 1: move [x:y] to the front.
    c1 = p[x:y] + p[:x] + p[y:]
    # Fig. 3 pattern 2: move [y:z] to the end.
    c2 = p[:y] + p[z:] + p[y:z]

    child = c1 if rng.random() < 0.5 else c2
    if child == p:  # defensive fallback, although the selected cuts should move it
        swap_mutation_inplace(child, rng)
    return child


def pmx_crossover(
    parent1: Sequence[str],
    parent2: Sequence[str],
    rng: random.Random,
) -> List[str]:
    """Return one standard Partially Matched Crossover (PMX) child.

    The segment comes from parent1; remaining positions are reconciled against
    parent2 through the PMX mapping. Both inputs and the output are permutations.
    """
    n = len(parent1)
    if n < 2:
        return list(parent1)
    if parent1 == parent2:
        return moving_based_crossover(parent1, rng)

    lo, hi = sorted(rng.sample(range(n), 2))
    hi += 1  # make upper cut exclusive

    child: List[str | None] = [None] * n
    child[lo:hi] = parent1[lo:hi]
    in_segment = set(parent1[lo:hi])
    p2_index = {gene: idx for idx, gene in enumerate(parent2)}

    # Map parent2 segment genes that are not already inherited from parent1.
    for i in range(lo, hi):
        gene = parent2[i]
        if gene in in_segment:
            continue
        pos = i
        while lo <= pos < hi:
            mapped_gene = parent1[pos]
            pos = p2_index[mapped_gene]
        child[pos] = gene

    # Fill remaining holes directly from parent2.
    for i in range(n):
        if child[i] is None:
            child[i] = parent2[i]

    return [str(g) for g in child]


def discrete_crossover(
    current: Sequence[str],
    guide: Sequence[str],
    rng: random.Random,
) -> List[str]:
    """Paper-based crossover choice: MBX if identical; PMX otherwise."""
    if current == guide:
        return moving_based_crossover(current, rng)
    return pmx_crossover(current, guide, rng)


# ---------------------------------------------------------------------------
# NEH-based initialization
# ---------------------------------------------------------------------------

def generalized_neh(
    decoder: FrozenInstanceDecoder,
    ordered_jobs: Sequence[str],
    cache: Dict[Tuple[str, ...], float],
) -> List[str]:
    """Generalized NEH insertion using the existing AL13 decoder as evaluator.

    Standard NEH incrementally inserts one job into every possible position of
    the current partial sequence. Here each partial sequence is decoded directly
    by ``FrozenInstanceDecoder`` with validation disabled. The model/constraints
    are therefore identical; only the sequence-construction heuristic is new.
    """
    seq: List[str] = []
    for job in ordered_jobs:
        best_seq = None
        best_fit = math.inf
        for pos in range(len(seq) + 1):
            candidate = seq[:pos] + [job] + seq[pos:]
            fit = eval_cmax(candidate, decoder, cache)
            if strictly_better(fit, best_fit):
                best_fit = fit
                best_seq = candidate
        seq = best_seq if best_seq is not None else seq + [job]
    return seq


def _effective_population_size(n_jobs: int, requested: int, subpops: int) -> int:
    if requested <= 0:
        raise ValueError("population must be > 0")
    if subpops <= 0:
        raise ValueError("subpops must be > 0")

    effective = int(requested)
    if n_jobs <= 10:
        effective = min(effective, math.factorial(n_jobs))

    if effective < subpops:
        raise ValueError(
            f"Effective population={effective} is smaller than subpops={subpops}. "
            "Reduce --subpops or increase the problem/population size."
        )
    if effective % subpops != 0:
        raise ValueError(
            f"Equal subpopulations require population divisible by d: "
            f"effective population={effective}, d={subpops}. "
            "Example: use PS=60,d=3 (paper setting) or PS=200,d=4."
        )
    return effective


def create_initial_swarm(
    decoder: FrozenInstanceDecoder,
    rng: random.Random,
    population: int,
    subpops: int,
    neh_seeds: int,
    cache: Dict[Tuple[str, ...], float],
) -> Tuple[List[List[str]], List[float]]:
    """Create 1-2 NEH-based particles and fill the rest with unique random permutations."""
    jobs = decoder.job_ids[:]
    ps = _effective_population_size(len(jobs), population, subpops)
    neh_seeds = max(0, min(2, int(neh_seeds)))

    workloads = {j: total_min_workload(decoder, j) for j in jobs}
    desc = sorted(jobs, key=lambda j: (-workloads[j], decoder.release[j], j))
    asc = sorted(jobs, key=lambda j: (workloads[j], decoder.release[j], j))

    swarm: List[List[str]] = []
    seen = set()

    def add(ch: Sequence[str]) -> None:
        key = tuple(ch)
        if key not in seen and len(swarm) < ps:
            seen.add(key)
            swarm.append(list(ch))

    if neh_seeds >= 1:
        add(generalized_neh(decoder, desc, cache))
    if neh_seeds >= 2:
        add(generalized_neh(decoder, asc, cache))

    attempts = 0
    max_attempts = max(2000, ps * 500)
    while len(swarm) < ps and attempts < max_attempts:
        attempts += 1
        ch = jobs[:]
        rng.shuffle(ch)
        add(ch)

    # Exact fallback for tiny spaces only.
    if len(swarm) < ps and len(jobs) <= 8:
        perms = list(itertools.permutations(jobs))
        rng.shuffle(perms)
        for p in perms:
            add(p)
            if len(swarm) >= ps:
                break

    if len(swarm) != ps:
        raise RuntimeError(f"Could create only {len(swarm)} unique particles; target={ps}")

    fitness = [eval_cmax(ch, decoder, cache) for ch in swarm]
    return swarm, fitness


def divide_equal_subpopulations(
    positions: Sequence[Sequence[str]],
    fitness: Sequence[float],
    d: int,
) -> List[List[int]]:
    """Form equal, initially comparable subpopulations from Cmax-ranked particles.

    Particles are sorted by initial Cmax and distributed round-robin across d
    groups. This uses Cmax for division while avoiding an immediate split into one
    all-good and one all-bad group; the groups start at approximately the same level,
    matching the paper's stated intent.
    """
    ps = len(positions)
    if ps % d != 0:
        raise ValueError("Population must be divisible by the number of subpopulations")
    order = sorted(range(ps), key=lambda i: (fitness[i], tuple(positions[i])))
    groups = [[] for _ in range(d)]
    for rank, idx in enumerate(order):
        groups[rank % d].append(idx)
    return groups


# ---------------------------------------------------------------------------
# Collaborative migration and local search
# ---------------------------------------------------------------------------

def _copy_particle_state(
    src: int,
    positions: Sequence[Sequence[str]],
    fitness: Sequence[float],
    pbest_pos: Sequence[Sequence[str]],
    pbest_fit: Sequence[float],
):
    return (
        list(positions[src]),
        float(fitness[src]),
        list(pbest_pos[src]),
        float(pbest_fit[src]),
    )


def _assign_particle_state(
    dst: int,
    state,
    positions: List[List[str]],
    fitness: List[float],
    pbest_pos: List[List[str]],
    pbest_fit: List[float],
) -> None:
    pos, fit, pbp, pbf = state
    positions[dst] = pos
    fitness[dst] = fit
    pbest_pos[dst] = pbp
    pbest_fit[dst] = pbf


def collaborative_migration(
    groups: Sequence[Sequence[int]],
    positions: List[List[str]],
    fitness: List[float],
    pbest_pos: List[List[str]],
    pbest_fit: List[float],
) -> None:
    """Exchange information between superior and inferior subpopulations.

    Subpopulations are ranked by current best Cmax and then mean Cmax. For each
    mirrored superior/inferior pair:
      * superior's worst particle replaces inferior's worst particle;
      * inferior's best particle replaces superior's worst particle.

    Source states are snapshotted before replacement so the exchange is simultaneous.
    """
    if len(groups) < 2:
        return

    ranking = []
    for gidx, group in enumerate(groups):
        best = min(fitness[i] for i in group)
        mean = sum(fitness[i] for i in group) / len(group)
        ranking.append((best, mean, gidx))
    ranking.sort()

    half = len(ranking) // 2
    for k in range(half):
        superior = groups[ranking[k][2]]
        inferior = groups[ranking[-1 - k][2]]

        sup_worst = max(superior, key=lambda i: fitness[i])
        inf_worst = max(inferior, key=lambda i: fitness[i])
        inf_best = min(inferior, key=lambda i: fitness[i])

        sup_worst_state = _copy_particle_state(
            sup_worst, positions, fitness, pbest_pos, pbest_fit
        )
        inf_best_state = _copy_particle_state(
            inf_best, positions, fitness, pbest_pos, pbest_fit
        )

        _assign_particle_state(
            inf_worst, sup_worst_state, positions, fitness, pbest_pos, pbest_fit
        )
        _assign_particle_state(
            sup_worst, inf_best_state, positions, fitness, pbest_pos, pbest_fit
        )


def local_search_gbest(
    gbest: Sequence[str],
    gbest_fit: float,
    decoder: FrozenInstanceDecoder,
    cache: Dict[Tuple[str, ...], float],
    rng: random.Random,
    candidates: int,
    rounds: int,
) -> Tuple[List[str], float]:
    """Bounded best-improvement Insert/Swap search applied only to overall G_best."""
    current = list(gbest)
    current_fit = float(gbest_fit)
    n = len(current)
    if n < 2 or candidates <= 0 or rounds <= 0:
        return current, current_fit

    for _ in range(rounds):
        round_best = current
        round_best_fit = current_fit

        for c in range(candidates):
            i, j = rng.sample(range(n), 2)
            if c % 2 == 0:
                cand = current[:]
                cand[i], cand[j] = cand[j], cand[i]
            else:
                cand = insert_move(current, i, j)

            fit = eval_cmax(cand, decoder, cache)
            if strictly_better(fit, round_best_fit):
                round_best = cand
                round_best_fit = fit

        if strictly_better(round_best_fit, current_fit):
            current, current_fit = round_best, round_best_fit
        else:
            break

    return current, current_fit


# ---------------------------------------------------------------------------
# One MLPSO run
# ---------------------------------------------------------------------------

def run_one_mlpso(
    decoder: FrozenInstanceDecoder,
    seed: int,
    population: int,
    iterations: int,
    subpops: int,
    mr: float,
    pcr: float,
    gcr: float,
    neh_seeds: int,
    migration_every: int,
    ls_every: int,
    ls_candidates: int,
    ls_rounds: int,
    no_improve: int,
    collect_convergence: bool,
    progress_every: int,
    progress_label: str,
) -> dict:
    if not (0.0 <= mr <= 1.0 and 0.0 <= pcr <= 1.0 and 0.0 <= gcr <= 1.0):
        raise ValueError("MR, PCR, and GCR must be in [0, 1]")
    if iterations < 0:
        raise ValueError("iterations must be >= 0")

    rng = random.Random(seed)
    cache: Dict[Tuple[str, ...], float] = {}
    t0 = time.perf_counter()

    positions, fitness = create_initial_swarm(
        decoder=decoder,
        rng=rng,
        population=population,
        subpops=subpops,
        neh_seeds=neh_seeds,
        cache=cache,
    )
    ps = len(positions)
    groups = divide_equal_subpopulations(positions, fitness, subpops)

    # Personal best is initially the current position, exactly as in discrete PSO.
    pbest_pos = [p[:] for p in positions]
    pbest_fit = fitness[:]

    # Historical G_best for each subpopulation.
    sub_gbest_pos: List[List[str]] = []
    sub_gbest_fit: List[float] = []
    for group in groups:
        idx = min(group, key=lambda i: fitness[i])
        sub_gbest_pos.append(positions[idx][:])
        sub_gbest_fit.append(float(fitness[idx]))

    global_idx = min(range(ps), key=lambda i: fitness[i])
    global_best_pos = positions[global_idx][:]
    global_best_fit = float(fitness[global_idx])
    stall = 0
    convergence = []  # tuples to avoid dict allocation inside the hot loop

    def record(iteration: int) -> None:
        pop_best = min(fitness)
        pop_mean = sum(fitness) / len(fitness)
        convergence.append(
            (int(iteration), float(global_best_fit), float(pop_best), float(pop_mean), int(len(cache)))
        )

    if collect_convergence:
        record(0)

    if progress_every > 0:
        print(
            f"[{progress_label}] start | seed={seed} | PS={ps} | d={subpops} | "
            f"iter={iterations} | MR={mr:.2f} PCR={pcr:.2f} GCR={gcr:.2f} | "
            f"Gbest={global_best_fit:.3f} | evals={len(cache)}",
            flush=True,
        )

    last_iter = 0
    for it in range(1, iterations + 1):
        improved_this_iter = False

        # Particle update: mutation -> learn from P_best -> learn from subgroup G_best.
        for gidx, group in enumerate(groups):
            guide_g = sub_gbest_pos[gidx]
            for idx in group:
                candidate = positions[idx][:]

                if rng.random() < mr:
                    swap_mutation_inplace(candidate, rng)
                if rng.random() < pcr:
                    candidate = discrete_crossover(candidate, pbest_pos[idx], rng)
                if rng.random() < gcr:
                    candidate = discrete_crossover(candidate, guide_g, rng)

                fit = eval_cmax(candidate, decoder, cache)
                positions[idx] = candidate
                fitness[idx] = fit

                if strictly_better(fit, pbest_fit[idx]):
                    pbest_fit[idx] = fit
                    pbest_pos[idx] = candidate[:]

                if strictly_better(fit, sub_gbest_fit[gidx]):
                    sub_gbest_fit[gidx] = fit
                    sub_gbest_pos[gidx] = candidate[:]

                if strictly_better(fit, global_best_fit):
                    global_best_fit = fit
                    global_best_pos = candidate[:]
                    global_idx = idx
                    improved_this_iter = True

        # Collaborative migration after update, as required by MLPSO.
        if migration_every > 0 and it % migration_every == 0:
            collaborative_migration(groups, positions, fitness, pbest_pos, pbest_fit)

            # Refresh historical subgroup bests after migration (never worsen history).
            for gidx, group in enumerate(groups):
                idx = min(group, key=lambda i: fitness[i])
                if strictly_better(fitness[idx], sub_gbest_fit[gidx]):
                    sub_gbest_fit[gidx] = float(fitness[idx])
                    sub_gbest_pos[gidx] = positions[idx][:]

                # A migrated P_best can also carry a stronger historical solution.
                pb_idx = min(group, key=lambda i: pbest_fit[i])
                if strictly_better(pbest_fit[pb_idx], sub_gbest_fit[gidx]):
                    sub_gbest_fit[gidx] = float(pbest_fit[pb_idx])
                    sub_gbest_pos[gidx] = pbest_pos[pb_idx][:]

        # Refresh global historical best from all P_bests after migration.
        best_pb_idx = min(range(ps), key=lambda i: pbest_fit[i])
        if strictly_better(pbest_fit[best_pb_idx], global_best_fit):
            global_best_fit = float(pbest_fit[best_pb_idx])
            global_best_pos = pbest_pos[best_pb_idx][:]
            global_idx = best_pb_idx
            improved_this_iter = True

        # Exploitation is applied ONLY to the overall G_best.
        if ls_every > 0 and it % ls_every == 0:
            ls_pos, ls_fit = local_search_gbest(
                global_best_pos,
                global_best_fit,
                decoder,
                cache,
                rng,
                candidates=ls_candidates,
                rounds=ls_rounds,
            )
            if strictly_better(ls_fit, global_best_fit):
                global_best_pos = ls_pos
                global_best_fit = ls_fit
                improved_this_iter = True

                # Put the improved G_best back into the swarm at the current
                # global-best particle slot, so future subgroup learning can use it.
                positions[global_idx] = ls_pos[:]
                fitness[global_idx] = ls_fit
                pbest_pos[global_idx] = ls_pos[:]
                pbest_fit[global_idx] = ls_fit
                for gidx, group in enumerate(groups):
                    if global_idx in group:
                        if strictly_better(ls_fit, sub_gbest_fit[gidx]):
                            sub_gbest_fit[gidx] = ls_fit
                            sub_gbest_pos[gidx] = ls_pos[:]
                        break

        if improved_this_iter:
            stall = 0
        else:
            stall += 1

        last_iter = it
        if collect_convergence:
            record(it)

        if progress_every > 0 and (it == 1 or it == iterations or it % progress_every == 0):
            elapsed = time.perf_counter() - t0
            pct = 100.0 * it / max(1, iterations)
            eta = (elapsed / it * (iterations - it)) if it > 0 else 0.0
            print(
                f"[{progress_label}] iter {it:>5}/{iterations} ({pct:5.1f}%) | "
                f"Gbest={global_best_fit:.3f} | pop_best={min(fitness):.3f} | "
                f"stall={stall} | evals={len(cache)} | elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
                flush=True,
            )

        if no_improve > 0 and stall >= no_improve:
            break

    runtime = time.perf_counter() - t0
    if progress_every > 0:
        print(
            f"[{progress_label}] done | Gbest={global_best_fit:.3f} | "
            f"iterations={last_iter} | time={runtime:.2f}s | evals={len(cache)}",
            flush=True,
        )

    # Full supporting metrics only once per run, outside the inner loop.
    best_metrics, _ = decoder.decode(
        global_best_pos,
        keep_schedule=False,
        validate_chromosome=True,
    )

    return {
        "seed": int(seed),
        "best_permutation": global_best_pos,
        "best_metrics": best_metrics,
        "runtime_sec": float(runtime),
        "iterations_completed": int(last_iter),
        "unique_evaluations": int(len(cache)),
        "effective_population": int(ps),
        "convergence": convergence,
    }


# ---------------------------------------------------------------------------
# Multi-run wrapper (Windows-safe multiprocessing)
# ---------------------------------------------------------------------------

def _worker_run(payload: dict) -> dict:
    data = load_instance(payload["instance_path"])
    decoder = FrozenInstanceDecoder(data)
    return run_one_mlpso(
        decoder=decoder,
        seed=payload["seed"],
        population=payload["population"],
        iterations=payload["iterations"],
        subpops=payload["subpops"],
        mr=payload["mr"],
        pcr=payload["pcr"],
        gcr=payload["gcr"],
        neh_seeds=payload["neh_seeds"],
        migration_every=payload["migration_every"],
        ls_every=payload["ls_every"],
        ls_candidates=payload["ls_candidates"],
        ls_rounds=payload["ls_rounds"],
        no_improve=payload["no_improve"],
        collect_convergence=payload["collect_convergence"],
        progress_every=payload["progress_every"],
        progress_label=payload["progress_label"],
    )


def _convergence_to_dicts(rows):
    return [
        {
            "Iteration": r[0],
            "Gbest_Cmax_Min": r[1],
            "Population_Best_Min": r[2],
            "Population_Mean_Min": r[3],
            "Unique_Evaluations": r[4],
        }
        for r in rows
    ]


def solve_instance(
    instance_path: Path | str,
    population: int = 60,
    iterations: int = 1500,
    runs: int = 5,
    seed_base: int = 42,
    subpops: int = 3,
    mr: float = 0.80,
    pcr: float = 0.80,
    gcr: float = 0.40,
    neh_seeds: int = 2,
    migration_every: int = 1,
    ls_every: int = 1,
    ls_candidates: int = 12,
    ls_rounds: int = 1,
    no_improve: int = 0,
    collect_convergence: bool = False,
    parallel_runs: bool = True,
    max_workers: int = 0,
    progress_every: int = 100,
) -> dict:
    instance_path = Path(instance_path)
    data = load_instance(instance_path)
    base_decoder = FrozenInstanceDecoder(data)

    # Validate the equal-subpopulation condition before launching workers.
    effective_population = _effective_population_size(
        len(base_decoder.job_ids), population, subpops
    )

    if runs <= 0:
        raise ValueError("runs must be > 0")

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
        f"[MLPSO] {base_decoder.name} | objective=MIN_CMAX | "
        f"decoder=FrozenInstanceDecoder(UNCHANGED) | runs={runs} | "
        f"mode={'PARALLEL' if use_parallel else 'SEQUENTIAL'} | "
        f"workers={max_workers if use_parallel else 1} | PS={effective_population} | "
        f"d={subpops} | iterations={iterations}",
        flush=True,
    )

    common = {
        "instance_path": str(instance_path),
        "population": population,
        "iterations": iterations,
        "subpops": subpops,
        "mr": mr,
        "pcr": pcr,
        "gcr": gcr,
        "neh_seeds": neh_seeds,
        "migration_every": migration_every,
        "ls_every": ls_every,
        "ls_candidates": ls_candidates,
        "ls_rounds": ls_rounds,
        "no_improve": no_improve,
        "collect_convergence": collect_convergence,
        "progress_every": progress_every,
    }

    if use_parallel:
        payloads = []
        for i, seed in enumerate(seeds, 1):
            p = dict(common)
            p.update(
                {
                    "seed": seed,
                    "progress_label": f"MLPSO run {i}/{runs}",
                }
            )
            payloads.append(p)

        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_worker_run, p): p for p in payloads}
            for fut in as_completed(futures):
                run_results.append(fut.result())
    else:
        for i, seed in enumerate(seeds, 1):
            run_results.append(
                run_one_mlpso(
                    decoder=base_decoder,
                    seed=seed,
                    population=population,
                    iterations=iterations,
                    subpops=subpops,
                    mr=mr,
                    pcr=pcr,
                    gcr=gcr,
                    neh_seeds=neh_seeds,
                    migration_every=migration_every,
                    ls_every=ls_every,
                    ls_candidates=ls_candidates,
                    ls_rounds=ls_rounds,
                    no_improve=no_improve,
                    collect_convergence=collect_convergence,
                    progress_every=progress_every,
                    progress_label=f"MLPSO run {i}/{runs}",
                )
            )

    total_runtime = time.perf_counter() - total_start
    run_results.sort(key=lambda r: r["seed"])

    # Objective comparison uses Cmax ONLY; no tardiness/flow tie-break is used.
    best = min(run_results, key=lambda r: float(r["best_metrics"]["makespan_min"]))
    best_metrics, schedule = base_decoder.decode(
        best["best_permutation"],
        keep_schedule=True,
        validate_chromosome=True,
    )

    run_summaries = []
    for i, r in enumerate(run_results, 1):
        m = r["best_metrics"]
        run_summaries.append(
            {
                "run": i,
                "seed": int(r["seed"]),
                "makespan_min": float(m["makespan_min"]),
                "total_tardiness_min": float(m.get("total_tardiness_min", 0.0)),
                "total_flow_time_min": float(m.get("total_flow_time_min", 0.0)),
                "runtime_sec": float(r["runtime_sec"]),
                "iterations_completed": int(r["iterations_completed"]),
                "unique_evaluations": int(r["unique_evaluations"]),
            }
        )

    return {
        "instance_name": base_decoder.name,
        "size": len(base_decoder.job_ids),
        "algorithm": "Discrete permutation MLPSO + unchanged FrozenInstanceDecoder",
        "objective": "MIN_MAKESPAN",
        "fitness_used_for_search": "makespan_min only",
        "decoder_unchanged": True,
        "particle_representation": "permutation of Job_ID values",
        "population_requested": int(population),
        "population": int(effective_population),
        "iterations": int(iterations),
        "runs": int(runs),
        "parallel_runs": bool(use_parallel),
        "max_workers": int(max_workers if use_parallel else 1),
        "resources_redundant": bool(base_decoder.resources_redundant),
        "resource_checks_used": bool(base_decoder.use_resources),
        "seed_base": int(seed_base),
        "subpopulations": int(subpops),
        "subpopulation_size": int(effective_population // subpops),
        "MR": float(mr),
        "PCR": float(pcr),
        "GCR": float(gcr),
        "neh_seeds": int(max(0, min(2, neh_seeds))),
        "migration_every": int(migration_every),
        "local_search_target": "overall historical G_best only",
        "local_search_moves": "bounded randomized Insert/Swap",
        "local_search_every": int(ls_every),
        "local_search_candidates": int(ls_candidates),
        "local_search_rounds": int(ls_rounds),
        "no_improve": int(no_improve),
        "progress_every": int(progress_every),
        "paper_specific_blocking_logic_reused": False,
        "paper_adaptation_note": (
            "AL13 keeps the existing FrozenInstanceDecoder and does not add the paper's "
            "limited-buffer/blocking model. MLPSO search operators are adapted to the existing "
            "AL13 permutation-decoder architecture."
        ),
        "best_makespan_min": float(best_metrics["makespan_min"]),
        "best_total_tardiness_min": float(best_metrics.get("total_tardiness_min", 0.0)),
        "best_total_flow_time_min": float(best_metrics.get("total_flow_time_min", 0.0)),
        "best_permutation": best["best_permutation"],
        # Compatibility alias for existing plotting/report scripts that expect this name.
        "best_chromosome": best["best_permutation"],
        "best_seed": int(best["seed"]),
        "total_runtime_sec": float(total_runtime),
        "mean_run_runtime_sec": float(
            sum(r["runtime_sec"] for r in run_results) / len(run_results)
        ),
        "run_summaries": run_summaries,
        "schedule": schedule,
        "convergence_by_run": (
            [_convergence_to_dicts(r["convergence"]) for r in run_results]
            if collect_convergence
            else None
        ),
    }


def write_convergence_csv(result: dict, path: Path) -> None:
    conv = result.get("convergence_by_run")
    if not conv:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    seeds = [r["seed"] for r in result["run_summaries"]]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "Run",
                "Seed",
                "Iteration",
                "Gbest_Cmax_Min",
                "Population_Best_Min",
                "Population_Mean_Min",
                "Unique_Evaluations",
            ],
        )
        writer.writeheader()
        for run_idx, rows in enumerate(conv, 1):
            for row in rows:
                writer.writerow(
                    {
                        "Run": run_idx,
                        "Seed": seeds[run_idx - 1],
                        **row,
                    }
                )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Solve one frozen AL13 instance by discrete Multi-Level Subpopulation "
            "Particle Swarm Optimization (MLPSO), keeping FrozenInstanceDecoder unchanged."
        )
    )
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)

    ap.add_argument("--population", "--swarm-size", dest="population", type=int, default=60)
    ap.add_argument(
        "--iterations",
        "--generations",
        "--epochs",
        dest="iterations",
        type=int,
        default=1500,
    )
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed-base", type=int, default=42)

    # Paper-calibrated defaults: PS=60, MR=.8, PCR=.8, GCR=.4, d=3.
    ap.add_argument("--subpops", "-d", dest="subpops", type=int, default=3)
    ap.add_argument("--mr", type=float, default=0.80, help="Swap mutation probability")
    ap.add_argument("--pcr", type=float, default=0.80, help="P_best learning probability")
    ap.add_argument("--gcr", type=float, default=0.40, help="Subpopulation G_best learning probability")
    ap.add_argument("--neh-seeds", type=int, choices=[0, 1, 2], default=2)

    ap.add_argument(
        "--migration-every",
        type=int,
        default=1,
        help="Collaborative migration interval; 1 = every iteration, 0 = disabled",
    )
    ap.add_argument(
        "--ls-every",
        type=int,
        default=1,
        help="Apply local search to overall G_best every N iterations; 0 = disabled",
    )
    ap.add_argument(
        "--ls-candidates",
        type=int,
        default=12,
        help="Bounded Insert/Swap candidates checked per local-search round",
    )
    ap.add_argument("--ls-rounds", type=int, default=1)
    ap.add_argument(
        "--no-improve",
        type=int,
        default=0,
        help="0 = run all requested iterations; positive value enables early stopping",
    )

    ap.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel independent-run workers; 0=auto, 1=sequential",
    )
    ap.add_argument("--sequential-runs", action="store_true")
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--save-convergence", action="store_true")
    ap.add_argument(
        "--convergence-output",
        type=Path,
        default=None,
        help="Optional convergence CSV path; used only with --save-convergence",
    )
    args = ap.parse_args()

    result = solve_instance(
        instance_path=args.instance,
        population=args.population,
        iterations=args.iterations,
        runs=args.runs,
        seed_base=args.seed_base,
        subpops=args.subpops,
        mr=args.mr,
        pcr=args.pcr,
        gcr=args.gcr,
        neh_seeds=args.neh_seeds,
        migration_every=max(0, args.migration_every),
        ls_every=max(0, args.ls_every),
        ls_candidates=max(0, args.ls_candidates),
        ls_rounds=max(0, args.ls_rounds),
        no_improve=max(0, args.no_improve),
        collect_convergence=args.save_convergence,
        parallel_runs=not args.sequential_runs,
        max_workers=args.workers,
        progress_every=max(0, args.progress_every),
    )

    out = args.output or args.instance.with_name(args.instance.stem + "__mlpso_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if args.save_convergence:
        conv_out = args.convergence_output or out.with_name(
            out.stem.replace("__mlpso_result", "__mlpso_convergence") + ".csv"
        )
        write_convergence_csv(result, conv_out)
        print(f"Convergence CSV: {conv_out}")

    print(
        f"{result['instance_name']} | MLPSO Cmax={result['best_makespan_min']:.3f} min | "
        f"wall time={result['total_runtime_sec']:.2f}s | iterations={result['iterations']} | "
        f"runs={result['runs']} | PS={result['population']} | d={result['subpopulations']} | "
        f"workers={result['max_workers']}"
    )
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
