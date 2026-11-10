#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AL13 V5 discrete MLPSO-V3 solver for the confirmed physical scheduling model.

This file adapts the user's earlier permutation-based PSO/MLPSO concept to the
CURRENT AL13 V5 model. It changes only the METAHEURISTIC SEARCH ENGINE.
Feasibility and scheduling semantics are delegated to the validated V5 decoder.

Physical model inherited unchanged from V5ScheduleDecoder
----------------------------------------------------------
* 2B auxiliary inspection feeding PRE.
* Main route PRE -> FINAL -> LASER -> SCALE -> OIL -> SEAL -> VISUAL -> exact 2C.
* PRE -> FINAL streaming (SS_FIRST_BELT) with C_FINAL >= C_PRE.
* OP_2A shared by LASER, SCALE and OIL-load; OP_PACK shared by OIL-unload,
  SEAL and VISUAL.
* Explicit non-preemptive OIL cycles, capacity represented in the frozen instance.
* Exact 2C batch size 10, including same-Batch_Key 5+5 remainder pooling.
* Sequence-dependent clean-pipeline setup before successor PRE.
* Resource calendars, breaks and downtime.
* PRIMARY/ONLY search objective: MIN_CMAX_FINAL_2C.

Particle representation
-----------------------
A particle is a permutation of job indices. Timing/resource allocation is NOT
encoded in the particle; the V5 schedule-generation decoder constructs a feasible
schedule and returns Cmax.

PSO/MLPSO search concept
------------------------
* population split into equal subpopulations;
* personal-best (Pbest) memory for every particle;
* subgroup-best (Gbest) learning;
* adaptive multi-neighborhood mutation (swap/insert/block relocation);
* guide-directed insertion learning with occasional PMX diversification;
* one-way elite migration that never degrades a superior subpopulation;
* bounded Swap/Insert/Block local search on historical global best;
* reserved late-stage multi-elite polish within the same decoder budget;
* cheap V5 structural candidate screening before expensive full decoding;
* V5 structural repair after every move so product changes are not attempted
  immediately after an unmatched 5-belt remainder;
* hard full-decoder budget per run for fair comparison with the GA experiments.

Important adaptation from the old solver
----------------------------------------
The old file used FrozenInstanceDecoder/OP_SEQUENCE and partial-permutation NEH.
Those belong to the older benchmark and are NOT valid for V5. This version uses
ONLY complete V5 permutations and V5ScheduleDecoder.

Default fairness settings
-------------------------
* 5 runs
* seeds: 42, 1051, 2060, 3069, 4078
* max 12,000 unique full V5 decodes per run
* fast equivalent decoder by default; use --reference-decoder for validation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, MutableMapping, Optional, Sequence, Tuple

EPS = 1e-9


# ---------------------------------------------------------------------------
# Decoder selection
# ---------------------------------------------------------------------------

def _decoder_api(use_fast: bool):
    # Pin the same package class used by SS Micro-GA Fast; no external search path.
    if use_fast:
        from .benchmark_common_fast import V5ScheduleDecoder, load_instance
    else:
        from .benchmark_common import V5ScheduleDecoder, load_instance
    return V5ScheduleDecoder, load_instance


def _json_finite(value):
    """Public diagnostics use null for missing/non-finite metrics, never zero."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_finite(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Compact permutation helpers + V5 structural repair
# ---------------------------------------------------------------------------

def _pack(seq: Sequence[int], byte_mode: bool):
    return bytes(int(x) for x in seq) if byte_mode else tuple(int(x) for x in seq)


def repair_product_change_remainders(ch, decoder):
    """Repair the exact V5 clean-pipeline setup/remainder interaction.

    If a product change follows a remainder-5 job whose Batch_Key currently has
    an unmatched remainder in the prefix, pull the next matching remainder-5 job
    forward before the change. This preserves a permutation and avoids a known
    structural dead-end of the serial V5 decoder.
    """
    vals = list(ch)
    i = 1
    while i < len(vals):
        pj = decoder.jobs[vals[i - 1]]
        cj = decoder.jobs[vals[i]]
        if decoder.product[pj] != decoder.product[cj] and decoder.qty[pj] % 10 == 5:
            key = decoder.batch_key[pj]
            cnt = 0
            for pos in range(i):
                jj = decoder.jobs[vals[pos]]
                if decoder.batch_key[jj] == key and decoder.qty[jj] % 10 == 5:
                    cnt += 1
            if cnt % 2 == 1:
                partner_pos = None
                for k in range(i, len(vals)):
                    jj = decoder.jobs[vals[k]]
                    if decoder.batch_key[jj] == key and decoder.qty[jj] % 10 == 5:
                        partner_pos = k
                        break
                if partner_pos is not None:
                    g = vals.pop(partner_pos)
                    vals.insert(i, g)
                    i += 1
                    continue
        i += 1
    return _pack(vals, isinstance(ch, bytes))


def structural_surrogate(ch: Sequence[int], decoder) -> float:
    """Cheap V5 ordering score used only for seed ranking, never as fitness."""
    vals = list(ch)
    if not vals:
        return 0.0
    setup_sum = 0.0
    release_penalty = 0.0
    weighted_work = 0.0
    unmatched_penalty = 0.0
    rem_count: Dict[str, int] = {}
    n = max(1, len(vals))
    for pos, gi in enumerate(vals):
        j = decoder.jobs[gi]
        weighted_work += ((pos + 1) / n) * float(decoder.min_workload[j])
        if decoder.qty[j] % 10 == 5:
            key = decoder.batch_key[j]
            rem_count[key] = rem_count.get(key, 0) + 1
        if pos:
            p = decoder.jobs[vals[pos - 1]]
            setup_sum += float(decoder.setup[decoder.product[p]][decoder.product[j]])
            release_penalty += max(0.0, decoder.release[j] - decoder.release[p])
            if decoder.product[p] != decoder.product[j] and decoder.qty[p] % 10 == 5:
                keyp = decoder.batch_key[p]
                if rem_count.get(keyp, 0) % 2 == 1:
                    unmatched_penalty += 1.0
    return (
        1_000_000.0 * unmatched_penalty
        + 100.0 * setup_sum
        + 0.02 * release_penalty
        + 0.002 * weighted_work
    )


# ---------------------------------------------------------------------------
# Hard-budget evaluator
# ---------------------------------------------------------------------------

class BudgetEvaluator:
    def __init__(self, decoder, max_decodes: int):
        self.decoder = decoder
        self.max_decodes = int(max_decodes)
        self.cache: MutableMapping[object, Tuple[float, Tuple[float, float, int, float]]] = {}
        self.new_decodes = 0
        self.cache_hits = 0
        self.best_ch = None
        self.best_metrics = None
        self.best_evaluation = 0
        self.best_time = 0.0
        self.best_iteration = 0
        self.best_phase = "initialization"
        self.iteration = 0
        self.phase = "initialization"
        self.started = time.perf_counter()

    def can_decode(self, ch) -> bool:
        if not self.decoder.valid_chromosome_indices(ch):
            return False
        ch = ch if isinstance(ch, (bytes, tuple)) else tuple(ch)
        return ch in self.cache or self.new_decodes < self.max_decodes

    def evaluate(self, ch) -> Optional[Tuple[float, Tuple[float, float, int, float]]]:
        # Validate before cache lookup: False/0.0 can otherwise alias gene 0.
        if not self.decoder.valid_chromosome_indices(ch):
            return (math.inf, (math.inf, math.inf, len(self.decoder.jobs), math.inf))
        ch = ch if isinstance(ch, (bytes, tuple)) else tuple(ch)
        hit = self.cache.get(ch)
        if hit is not None:
            self.cache_hits += 1
            return hit
        if self.new_decodes >= self.max_decodes:
            return None
        metrics = self.decoder.decode_indices(ch)
        cmax = float(metrics[0])
        entry = (cmax, metrics)
        self.cache[ch] = entry
        self.new_decodes += 1
        if math.isfinite(cmax) and (self.best_metrics is None or cmax < self.best_metrics[0] - EPS):
            self.best_ch, self.best_metrics = ch, metrics
            self.best_evaluation = self.new_decodes
            self.best_time = time.perf_counter() - self.started
            self.best_iteration, self.best_phase = self.iteration, self.phase
        return entry


# ---------------------------------------------------------------------------
# Permutation operators
# ---------------------------------------------------------------------------

def swap_mutation(ch, rng: random.Random):
    vals = list(ch)
    if len(vals) >= 2:
        i, j = rng.sample(range(len(vals)), 2)
        vals[i], vals[j] = vals[j], vals[i]
    return _pack(vals, isinstance(ch, bytes))


def insert_mutation(ch, rng: random.Random):
    vals = list(ch)
    if len(vals) >= 2:
        i, j = rng.sample(range(len(vals)), 2)
        gene = vals.pop(i)
        vals.insert(j, gene)
    return _pack(vals, isinstance(ch, bytes))


def block_insert_mutation(ch, rng: random.Random, max_block: int = 4):
    vals = list(ch)
    n = len(vals)
    if n < 3:
        return swap_mutation(ch, rng)
    block_len = rng.randint(2, min(max_block, max(2, n // 5), n - 1))
    start = rng.randrange(0, n - block_len + 1)
    block = vals[start:start + block_len]
    del vals[start:start + block_len]
    dest = rng.randrange(0, len(vals) + 1)
    vals[dest:dest] = block
    return _pack(vals, isinstance(ch, bytes))


def guided_insert_move(current, guide, rng: random.Random, moves: int = 1):
    """Move a few guide genes toward their guide positions without destroying the permutation."""
    vals = list(current)
    tgt = list(guide)
    n = len(vals)
    if n < 2 or vals == tgt:
        return block_insert_mutation(current, rng)
    for _ in range(max(1, moves)):
        mismatches = [k for k in range(n) if vals[k] != tgt[k]]
        if not mismatches:
            break
        k = rng.choice(mismatches)
        gene = tgt[k]
        cur = vals.index(gene)
        vals.pop(cur)
        vals.insert(k, gene)
    return _pack(vals, isinstance(current, bytes))


def mixed_mutation(ch, rng: random.Random):
    r = rng.random()
    if r < 0.35:
        return swap_mutation(ch, rng)
    if r < 0.78:
        return insert_mutation(ch, rng)
    return block_insert_mutation(ch, rng)


def moving_based_crossover(parent, rng: random.Random):
    vals = list(parent)
    n = len(vals)
    if n < 4:
        return swap_mutation(parent, rng)
    x, y, z = sorted(rng.sample(range(1, n), 3))
    c1 = vals[x:y] + vals[:x] + vals[y:]
    c2 = vals[:y] + vals[z:] + vals[y:z]
    out = c1 if rng.random() < 0.5 else c2
    if out == vals:
        i, j = rng.sample(range(n), 2)
        out[i], out[j] = out[j], out[i]
    return _pack(out, isinstance(parent, bytes))


def pmx_crossover(parent1, parent2, rng: random.Random):
    p1, p2 = list(parent1), list(parent2)
    n = len(p1)
    if n < 2:
        return parent1
    if p1 == p2:
        return moving_based_crossover(parent1, rng)

    lo, hi = sorted(rng.sample(range(n), 2))
    hi += 1
    child: List[Optional[int]] = [None] * n
    child[lo:hi] = p1[lo:hi]
    inherited = set(p1[lo:hi])
    p2_index = {gene: idx for idx, gene in enumerate(p2)}

    for i in range(lo, hi):
        gene = p2[i]
        if gene in inherited:
            continue
        pos = i
        guard = 0
        while lo <= pos < hi:
            mapped = p1[pos]
            pos = p2_index[mapped]
            guard += 1
            if guard > n + 2:
                raise RuntimeError("PMX mapping cycle guard triggered")
        child[pos] = gene

    for i in range(n):
        if child[i] is None:
            child[i] = p2[i]
    return _pack([int(x) for x in child], isinstance(parent1, bytes))


def learn_from_guide(current, guide, decoder, rng: random.Random, guided_prob: float = 0.80):
    if current == guide:
        child = mixed_mutation(current, rng)
    elif rng.random() < guided_prob:
        # One or two incremental moves work better than a disruptive crossover on large permutations.
        moves = 1 if rng.random() < 0.72 else 2
        child = guided_insert_move(current, guide, rng, moves=moves)
    else:
        child = pmx_crossover(current, guide, rng)
    return repair_product_change_remainders(child, decoder)


# ---------------------------------------------------------------------------
# V5-safe complete-permutation initialization
# ---------------------------------------------------------------------------

def _deterministic_seeds(decoder, byte_mode: bool):
    ids = list(range(len(decoder.jobs)))
    fifo = sorted(ids, key=lambda i: (decoder.release[decoder.jobs[i]], decoder.priority[decoder.jobs[i]], decoder.jobs[i]))
    spt = sorted(ids, key=lambda i: (decoder.min_workload[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    lpt = sorted(ids, key=lambda i: (-decoder.min_workload[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    edd = sorted(ids, key=lambda i: (decoder.due[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    family = sorted(ids, key=lambda i: (decoder.product[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    batch = sorted(ids, key=lambda i: (decoder.batch_key[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    rel_family = sorted(ids, key=lambda i: (round(decoder.release[decoder.jobs[i]] / 120.0), decoder.product[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))

    out, seen = [], set()
    for seq in (fifo, spt, lpt, edd, family, batch, rel_family):
        ch = repair_product_change_remainders(_pack(seq, byte_mode), decoder)
        if ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out


def _effective_population_size(n_jobs: int, requested: int, subpops: int) -> int:
    if n_jobs < 1:
        raise ValueError("At least one selected job is required")
    if requested <= 0:
        raise ValueError("population must be > 0")
    if subpops <= 0:
        raise ValueError("subpops must be > 0")
    ps = int(requested)
    if n_jobs <= 8 and ps > math.factorial(n_jobs):
        raise ValueError("population exceeds the number of complete job permutations")
    if ps < subpops:
        raise ValueError("population must be >= subpops")
    if ps % subpops != 0:
        raise ValueError(
            f"Equal subpopulations require population divisible by subpops: population={ps}, subpops={subpops}"
        )
    return ps


def create_initial_swarm(decoder, rng: random.Random, population: int, subpops: int, evaluator: BudgetEvaluator):
    n = len(decoder.jobs)
    ps = _effective_population_size(n, population, subpops)
    byte_mode = n <= 255
    seeds = _deterministic_seeds(decoder, byte_mode)
    # Rank deterministic seeds cheaply, then evaluate them first.
    seeds.sort(key=lambda ch: (structural_surrogate(ch, decoder), tuple(ch)))

    positions, fitness = [], []
    seen = set()

    def try_add(ch) -> bool:
        if ch in seen or len(positions) >= ps:
            return True
        ev = evaluator.evaluate(ch)
        if ev is None:
            return False
        seen.add(ch)
        positions.append(ch)
        fitness.append(float(ev[0]))
        return True

    for ch in seeds:
        if not try_add(ch):
            break
        if len(positions) >= ps:
            break

    attempts = 0
    max_attempts = max(5000, ps * 1000)
    base = list(range(n))
    while len(positions) < ps and attempts < max_attempts:
        attempts += 1
        vals = base[:]
        rng.shuffle(vals)
        ch = repair_product_change_remainders(_pack(vals, byte_mode), decoder)
        if not try_add(ch):
            break

    if len(positions) != ps:
        raise RuntimeError(f"Could initialize only {len(positions)} particles; target={ps}")
    return positions, fitness


def divide_equal_subpopulations(positions, fitness, d: int):
    ps = len(positions)
    if ps % d:
        raise ValueError("Population must be divisible by number of subpopulations")
    order = sorted(range(ps), key=lambda i: (fitness[i], tuple(positions[i])))
    groups = [[] for _ in range(d)]
    for rank, idx in enumerate(order):
        groups[rank % d].append(idx)
    return groups


# ---------------------------------------------------------------------------
# Migration + local search
# ---------------------------------------------------------------------------

def collaborative_migration(groups, positions, fitness, pbest_pos, pbest_fit):
    """One-way elite migration from stronger to weaker subpopulations.

    The original reciprocal swap could degrade the best subpopulation. V3 copies
    a historical elite into the worst member of a weaker group and never moves a
    weaker particle back into the superior group.
    """
    if len(groups) < 2:
        return
    ranking = []
    for gidx, group in enumerate(groups):
        best_idx = min(group, key=lambda i: pbest_fit[i])
        mean_fit = sum(fitness[i] for i in group) / len(group)
        ranking.append((pbest_fit[best_idx], mean_fit, gidx, best_idx))
    ranking.sort()
    donors = ranking[:max(1, len(ranking) // 2)]
    receivers = list(reversed(ranking[max(1, len(ranking) // 2):]))
    for k, rec in enumerate(receivers):
        donor = donors[k % len(donors)]
        donor_idx = donor[3]
        recv_group = groups[rec[2]]
        recv_worst = max(recv_group, key=lambda i: fitness[i])
        positions[recv_worst] = pbest_pos[donor_idx]
        fitness[recv_worst] = float(pbest_fit[donor_idx])
        pbest_pos[recv_worst] = pbest_pos[donor_idx]
        pbest_fit[recv_worst] = float(pbest_fit[donor_idx])


def local_search_gbest(gbest, gbest_fit: float, decoder, evaluator: BudgetEvaluator,
                       rng: random.Random, candidates: int, rounds: int):
    current, current_fit = gbest, float(gbest_fit)
    if len(current) < 2 or candidates <= 0 or rounds <= 0:
        return current, current_fit, False, False

    any_improved = False
    budget_stop = False
    for _ in range(rounds):
        round_best, round_best_fit = current, current_fit
        for c in range(candidates):
            cand = (swap_mutation(current, rng) if c % 3 == 0 else insert_mutation(current, rng) if c % 3 == 1 else block_insert_mutation(current, rng))
            cand = repair_product_change_remainders(cand, decoder)
            ev = evaluator.evaluate(cand)
            if ev is None:
                budget_stop = True
                break
            fit = float(ev[0])
            if fit < round_best_fit - EPS:
                round_best, round_best_fit = cand, fit
        if round_best_fit < current_fit - EPS:
            current, current_fit = round_best, round_best_fit
            any_improved = True
        elif not budget_stop:
            break
        if budget_stop:
            break
    return current, current_fit, any_improved, budget_stop



def double_insert_mutation(ch, rng: random.Random):
    """Two successive insertion moves; useful for late-stage permutation polishing."""
    out = insert_mutation(ch, rng)
    return insert_mutation(out, rng)


def segment_reverse_mutation(ch, rng: random.Random, max_len: int = 7):
    vals = list(ch)
    n = len(vals)
    if n < 3:
        return swap_mutation(ch, rng)
    length = rng.randint(2, min(max_len, n))
    start = rng.randrange(0, n - length + 1)
    vals[start:start + length] = reversed(vals[start:start + length])
    return _pack(vals, isinstance(ch, bytes))


def elite_polish_phase(
    global_best_pos,
    global_best_fit: float,
    elite_states,
    decoder,
    evaluator: BudgetEvaluator,
    rng: random.Random,
    cheap_pool: int,
    decode_k: int,
    elite_count: int,
    stall_rounds: int,
    t0: float,
):
    """Spend the reserved tail budget on a decoder-aware elite intensification phase.

    The phase never changes the physical model or objective. It explores only
    complete repaired permutations. A large cheap neighborhood is generated from
    the global best and several historical Pbest elites; only a small mixture of
    structurally promising and exploratory candidates is fully decoded.
    """
    start_evals = evaluator.new_decodes
    start_fit = float(global_best_fit)
    best_pos = global_best_pos
    best_fit = float(global_best_fit)
    best_eval = start_evals
    best_time = time.perf_counter() - t0
    improvements = 0
    rounds = 0
    stall = 0
    cache_only_rounds = 0
    stopped_no_new_decodes = False

    # Keep a compact unique elite set sorted by true decoded Cmax.
    elite_map = {best_pos: best_fit}
    for ch, fit in elite_states:
        f = float(fit)
        prev = elite_map.get(ch)
        if prev is None or f < prev:
            elite_map[ch] = f

    def trim_elites():
        nonlocal elite_map
        ranked = sorted(elite_map.items(), key=lambda kv: (kv[1], tuple(kv[0])))
        elite_map = dict(ranked[:max(2, int(elite_count))])

    trim_elites()

    while evaluator.new_decodes < evaluator.max_decodes:
        round_start_evals = evaluator.new_decodes
        rounds += 1
        ranked = sorted(elite_map.items(), key=lambda kv: (kv[1], tuple(kv[0])))
        elites = [ch for ch, _ in ranked]

        # Intensify around current global best, but periodically use another elite
        # to avoid polishing a single basin only.
        if len(elites) > 1 and rng.random() < min(0.45, 0.12 + 0.05 * stall):
            source = rng.choice(elites[1:])
        else:
            source = best_pos
        guide = rng.choice(elites) if elites else best_pos
        if guide == source and len(elites) > 1:
            guide = rng.choice([e for e in elites if e != source])

        pool = []
        seen = set()
        target_pool = max(int(decode_k), int(cheap_pool))
        attempts = 0
        while len(pool) < target_pool and attempts < target_pool * 12:
            attempts += 1
            op = attempts % 6
            if op == 0:
                cand = insert_mutation(source, rng)
            elif op == 1:
                cand = swap_mutation(source, rng)
            elif op == 2:
                cand = block_insert_mutation(source, rng, max_block=5)
            elif op == 3:
                cand = guided_insert_move(source, guide, rng, moves=1 if rng.random() < 0.70 else 2)
            elif op == 4:
                cand = double_insert_mutation(source, rng)
            else:
                cand = segment_reverse_mutation(source, rng, max_len=6)

            # During a polish stall, occasionally make a larger path-relinking move.
            if stall >= max(2, stall_rounds // 2) and rng.random() < 0.25:
                cand = guided_insert_move(cand, guide, rng, moves=2 if rng.random() < 0.75 else 3)

            cand = repair_product_change_remainders(cand, decoder)
            if cand == source or cand in seen:
                continue
            seen.add(cand)
            pool.append(cand)

        if not pool:
            break

        rng.shuffle(pool)
        ordered = sorted(pool, key=lambda ch: structural_surrogate(ch, decoder))
        k = min(max(1, int(decode_k)), len(ordered))
        exploit_n = max(1, int(round(0.75 * k)))
        selected = ordered[:exploit_n]
        selected_set = set(selected)
        remainder = [ch for ch in pool if ch not in selected_set]
        rng.shuffle(remainder)
        selected.extend(remainder[:max(0, k - len(selected))])

        round_improved = False
        for cand in selected:
            if evaluator.new_decodes >= evaluator.max_decodes and cand not in evaluator.cache:
                break
            ev = evaluator.evaluate(cand)
            if ev is None:
                break
            fit = float(ev[0])

            # Preserve a few genuinely strong alternative basins.
            worst_elite = max(elite_map.values()) if elite_map else math.inf
            if fit < worst_elite - EPS or len(elite_map) < max(2, int(elite_count)):
                elite_map[cand] = fit
                trim_elites()

            if fit < best_fit - EPS:
                best_pos, best_fit = cand, fit
                elite_map[cand] = fit
                trim_elites()
                best_eval = evaluator.new_decodes
                best_time = time.perf_counter() - t0
                improvements += 1
                round_improved = True

        if round_improved:
            stall = 0
        else:
            stall += 1

        # On prolonged local stagnation, refresh secondary elites with guided
        # perturbations rather than terminating early; the hard decoder budget
        # remains the only default stopping condition.
        if stall_rounds > 0 and stall >= stall_rounds and len(elites) > 1:
            base = rng.choice(elites[1:])
            pert = block_insert_mutation(base, rng, max_block=6)
            pert = guided_insert_move(pert, best_pos, rng, moves=1)
            pert = repair_product_change_remainders(pert, decoder)
            if pert not in elite_map and evaluator.can_decode(pert):
                ev = evaluator.evaluate(pert)
                if ev is not None:
                    fit = float(ev[0])
                    elite_map[pert] = fit
                    trim_elites()
                    if fit < best_fit - EPS:
                        best_pos, best_fit = pert, fit
                        best_eval = evaluator.new_decodes
                        best_time = time.perf_counter() - t0
                        improvements += 1
            stall = max(0, stall_rounds // 2)

        # A finite/repaired neighborhood can be fully cached before the cap.
        # Bound cache-only cycling; iterations that do new decoding are unchanged.
        cache_only_rounds = cache_only_rounds + 1 if evaluator.new_decodes == round_start_evals else 0
        if cache_only_rounds >= max(32, int(stall_rounds) * 4):
            stopped_no_new_decodes = True
            break

    return {
        "best_pos": best_pos,
        "best_fit": float(best_fit),
        "best_eval": int(best_eval),
        "best_time": float(best_time),
        "improvements": int(improvements),
        "rounds": int(rounds),
        "evaluations": int(evaluator.new_decodes - start_evals),
        "start_cmax": float(start_fit),
        "end_cmax": float(best_fit),
        "stopped_no_new_decodes": stopped_no_new_decodes,
    }


def screened_particle_candidate(current, pbest, subguide, decoder, rng: random.Random,
                                mr_eff: float, pcr: float, gcr: float, screen_k: int):
    """Generate several cheap V5-aware moves and keep the best structural candidate.

    Only the selected complete permutation is sent to the expensive decoder.
    """
    kmax = max(1, int(screen_k))
    pool = []
    for _ in range(kmax):
        cand = current
        if rng.random() < mr_eff:
            cand = mixed_mutation(cand, rng)
        if rng.random() < pcr:
            cand = learn_from_guide(cand, pbest, decoder, rng)
        if rng.random() < gcr:
            cand = learn_from_guide(cand, subguide, decoder, rng)
        cand = repair_product_change_remainders(cand, decoder)
        pool.append(cand)
    # Preserve exploration by breaking structural-score ties randomly.
    rng.shuffle(pool)
    return min(pool, key=lambda ch: structural_surrogate(ch, decoder))


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------

def run_one_mlpso(
    instance_path: str,
    use_fast_decoder: bool,
    seed: int,
    population: int,
    iterations: int,
    subpops: int,
    mr: float,
    pcr: float,
    gcr: float,
    migration_every: int,
    ls_every: int,
    ls_candidates: int,
    ls_rounds: int,
    max_decodes: int,
    no_improve: int,
    collect_convergence: bool,
    convergence_every: int,
    progress_every: int,
    progress_label: str,
    screen_k: int,
    adaptive_stall: int,
    polish_budget: int,
    polish_pool: int,
    polish_decode_k: int,
    polish_elites: int,
    polish_stall_rounds: int,
) -> dict:
    V5ScheduleDecoder, load_instance = _decoder_api(use_fast_decoder)
    decoder = V5ScheduleDecoder(load_instance(instance_path))

    if not all(0.0 <= x <= 1.0 for x in (mr, pcr, gcr)):
        raise ValueError("MR, PCR, GCR must be in [0,1]")
    if max_decodes < population:
        raise ValueError("max_decodes must be >= population")
    if iterations < 0:
        raise ValueError("iterations must be >= 0")
    polish_budget = max(0, min(int(polish_budget), max(0, int(max_decodes) - int(population))))
    main_decode_limit = max(int(population), int(max_decodes) - polish_budget)

    rng = random.Random(seed)
    evaluator = BudgetEvaluator(decoder, max_decodes=max_decodes)
    t0 = time.perf_counter()
    evaluator.started = t0

    positions, fitness = create_initial_swarm(decoder, rng, population, subpops, evaluator)
    groups = divide_equal_subpopulations(positions, fitness, subpops)
    ps = len(positions)

    pbest_pos = list(positions)
    pbest_fit = list(fitness)

    sub_gbest_pos, sub_gbest_fit = [], []
    for group in groups:
        idx = min(group, key=lambda i: fitness[i])
        sub_gbest_pos.append(positions[idx])
        sub_gbest_fit.append(float(fitness[idx]))

    global_idx = min(range(ps), key=lambda i: fitness[i])
    global_best_pos = positions[global_idx]
    global_best_fit = float(fitness[global_idx])
    best_eval = evaluator.new_decodes
    best_time = time.perf_counter() - t0
    stall = 0
    migration_events = 0
    ls_improvements = 0
    last_iter = 0
    stop_reason = "iteration_limit"
    convergence = []

    def record(it: int, final_reason=""):
        finite = [x for x in fitness if math.isfinite(x)]
        pop_best = min(fitness)
        pop_mean = sum(finite) / len(finite) if finite else math.inf
        row = _json_finite({
            "Iteration": int(it),
            "Gbest_Cmax_Min": float(global_best_fit),
            "Population_Best_Min": float(pop_best),
            "Population_Mean_Min": float(pop_mean),
            "Decoder_Evaluations": int(evaluator.new_decodes),
            "Evaluations_To_Best": int(best_eval),
            "Time_To_Best_Sec": float(best_time),
            "Stall": int(stall),
            "Stop_Reason": final_reason,
        })
        if convergence and convergence[-1]["Iteration"] == it:
            convergence[-1] = row
        else:
            convergence.append(row)

    if collect_convergence:
        record(0)

    if progress_every > 0:
        print(
            f"[{progress_label}] start | seed={seed} | PS={ps} | d={subpops} | "
            f"max_decodes={max_decodes} | main_limit={main_decode_limit} | polish={polish_budget} | "
            f"MR={mr:.2f} PCR={pcr:.2f} GCR={gcr:.2f} | "
            f"Gbest={global_best_fit:.3f} | evals={evaluator.new_decodes}",
            flush=True,
        )

    budget_stop = False
    for it in range(1, iterations + 1):
        evaluator.iteration, evaluator.phase = it, "search"
        if polish_budget > 0 and evaluator.new_decodes >= main_decode_limit:
            stop_reason = "polish_phase"
            last_iter = max(last_iter, it - 1)
            break
        improved_this_iter = False

        for gidx, group in enumerate(groups):
            guide_g = sub_gbest_pos[gidx]
            for idx in group:
                if polish_budget > 0 and evaluator.new_decodes >= main_decode_limit:
                    budget_stop = True
                    stop_reason = "polish_phase"
                    break
                # Increase exploration gradually when global improvement stalls.
                stall_scale = min(1.0, stall / max(1, adaptive_stall)) if adaptive_stall > 0 else 0.0
                mr_eff = min(1.0, mr + 0.18 * stall_scale)
                candidate = screened_particle_candidate(
                    positions[idx], pbest_pos[idx], guide_g, decoder, rng,
                    mr_eff=mr_eff, pcr=pcr, gcr=gcr, screen_k=screen_k,
                )

                ev = evaluator.evaluate(candidate)
                if ev is None:
                    budget_stop = True
                    break
                fit = float(ev[0])
                positions[idx] = candidate
                fitness[idx] = fit

                if fit < pbest_fit[idx] - EPS:
                    pbest_fit[idx] = fit
                    pbest_pos[idx] = candidate
                if fit < sub_gbest_fit[gidx] - EPS:
                    sub_gbest_fit[gidx] = fit
                    sub_gbest_pos[gidx] = candidate
                if fit < global_best_fit - EPS:
                    global_best_fit = fit
                    global_best_pos = candidate
                    global_idx = idx
                    best_eval = evaluator.new_decodes
                    best_time = time.perf_counter() - t0
                    improved_this_iter = True
            if budget_stop:
                break
        if budget_stop:
            if stop_reason != "polish_phase":
                stop_reason = "decoder_budget"
            last_iter = it
            if collect_convergence:
                record(it)
            break

        if migration_every > 0 and it % migration_every == 0:
            collaborative_migration(groups, positions, fitness, pbest_pos, pbest_fit)
            migration_events += 1
            for gidx, group in enumerate(groups):
                idx = min(group, key=lambda i: fitness[i])
                if fitness[idx] < sub_gbest_fit[gidx] - EPS:
                    sub_gbest_fit[gidx] = float(fitness[idx])
                    sub_gbest_pos[gidx] = positions[idx]
                pb_idx = min(group, key=lambda i: pbest_fit[i])
                if pbest_fit[pb_idx] < sub_gbest_fit[gidx] - EPS:
                    sub_gbest_fit[gidx] = float(pbest_fit[pb_idx])
                    sub_gbest_pos[gidx] = pbest_pos[pb_idx]

        best_pb_idx = min(range(ps), key=lambda i: pbest_fit[i])
        if pbest_fit[best_pb_idx] < global_best_fit - EPS:
            global_best_fit = float(pbest_fit[best_pb_idx])
            global_best_pos = pbest_pos[best_pb_idx]
            global_idx = best_pb_idx
            best_eval = evaluator.new_decodes
            best_time = time.perf_counter() - t0
            improved_this_iter = True

        if ls_every > 0 and it % ls_every == 0:
            ls_pos, ls_fit, ls_improved, ls_budget_stop = local_search_gbest(
                global_best_pos, global_best_fit, decoder, evaluator, rng,
                candidates=ls_candidates, rounds=ls_rounds,
            )
            if ls_improved:
                global_best_pos, global_best_fit = ls_pos, ls_fit
                positions[global_idx] = ls_pos
                fitness[global_idx] = ls_fit
                pbest_pos[global_idx] = ls_pos
                pbest_fit[global_idx] = ls_fit
                best_eval = evaluator.new_decodes
                best_time = time.perf_counter() - t0
                improved_this_iter = True
                ls_improvements += 1
                for gidx, group in enumerate(groups):
                    if global_idx in group and ls_fit < sub_gbest_fit[gidx] - EPS:
                        sub_gbest_fit[gidx] = ls_fit
                        sub_gbest_pos[gidx] = ls_pos
                        break
            if ls_budget_stop:
                stop_reason = "decoder_budget"
                last_iter = it
                if collect_convergence:
                    record(it)
                break

        stall = 0 if improved_this_iter else stall + 1
        last_iter = it

        if collect_convergence and (it % max(1, convergence_every) == 0 or it == iterations):
            record(it)

        if progress_every > 0 and (it == 1 or it % progress_every == 0 or it == iterations):
            elapsed = time.perf_counter() - t0
            print(
                f"[{progress_label}] iter {it:>5}/{iterations} | Gbest={global_best_fit:.3f} | "
                f"stall={stall} | evals={evaluator.new_decodes}/{max_decodes} | "
                f"cache_hits={evaluator.cache_hits} | elapsed={elapsed:.1f}s",
                flush=True,
            )

        if no_improve > 0 and stall >= no_improve:
            stop_reason = "no_improve"
            break
        if evaluator.new_decodes >= max_decodes:
            stop_reason = "decoder_budget"
            break

    # Keep every completed improvement, even if a partial local-search batch stopped.
    if evaluator.best_ch is not None and evaluator.best_metrics[0] < global_best_fit - EPS:
        global_best_pos, global_best_fit = evaluator.best_ch, evaluator.best_metrics[0]
        best_eval, best_time = evaluator.best_evaluation, evaluator.best_time

    # Reserved late-stage elite polish. This uses only the remaining portion of the
    # SAME hard decoder budget, so V3 remains evaluation-budget comparable to GA/V1/V2.
    polish_info = {
        "best_pos": global_best_pos,
        "best_fit": float(global_best_fit),
        "best_eval": int(best_eval),
        "best_time": float(best_time),
        "improvements": 0,
        "rounds": 0,
        "evaluations": 0,
        "start_cmax": float(global_best_fit),
        "end_cmax": float(global_best_fit),
    }
    if polish_budget > 0 and evaluator.new_decodes < max_decodes:
        evaluator.iteration, evaluator.phase = last_iter, "polish"
        elite_states = sorted(
            {(pbest_pos[i], float(pbest_fit[i])) for i in range(ps)},
            key=lambda kv: (kv[1], tuple(kv[0])),
        )[:max(2, int(polish_elites))]
        polish_info = elite_polish_phase(
            global_best_pos, global_best_fit, elite_states, decoder, evaluator, rng,
            cheap_pool=max(1, int(polish_pool)),
            decode_k=max(1, int(polish_decode_k)),
            elite_count=max(2, int(polish_elites)),
            stall_rounds=max(1, int(polish_stall_rounds)),
            t0=t0,
        )
        if polish_info["best_fit"] < global_best_fit - EPS:
            global_best_pos = polish_info["best_pos"]
            global_best_fit = float(polish_info["best_fit"])
            best_eval = int(polish_info["best_eval"])
            best_time = float(polish_info["best_time"])
        if evaluator.new_decodes >= max_decodes:
            stop_reason = "decoder_budget"
        elif polish_info.get("stopped_no_new_decodes"):
            stop_reason = "no_new_candidates"

    if evaluator.best_ch is not None and evaluator.best_metrics[0] < global_best_fit - EPS:
        global_best_pos, global_best_fit = evaluator.best_ch, evaluator.best_metrics[0]
    if evaluator.best_ch is not None:
        best_eval, best_time = evaluator.best_evaluation, evaluator.best_time
    if collect_convergence:
        record(last_iter, stop_reason)

    # Re-decode final best once with complete validation for reporting.
    job_ids = [decoder.jobs[int(i)] for i in global_best_pos]
    feasible = math.isfinite(global_best_fit)
    final_metrics = dict(makespan_min=math.inf, total_tardiness_min=math.inf,
                         tardy_jobs=len(decoder.jobs), total_flow_time_min=math.inf)
    if feasible:
        try:
            final_metrics, _ = decoder.decode(job_ids, keep_schedule=False, validate_chromosome=True)
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(f"Historical MLPSO best failed final validation: {exc}") from exc
        if abs(float(final_metrics["makespan_min"]) - global_best_fit) > 1e-6:
            raise RuntimeError("Final validated Cmax differs from cached MLPSO best")
    runtime = time.perf_counter() - t0

    if progress_every > 0:
        print(
            f"[{progress_label}] done | Gbest={global_best_fit:.3f} | iter={last_iter} | "
            f"evals={evaluator.new_decodes} | time={runtime:.2f}s | stop={stop_reason}",
            flush=True,
        )

    return {
        "seed": int(seed),
        "feasible": feasible,
        "best_iteration": int(evaluator.best_iteration),
        "best_phase": evaluator.best_phase if feasible else None,
        "final_validation_decodes": int(feasible),
        "best_chromosome_indices": [int(x) for x in global_best_pos],
        "best_chromosome": job_ids,
        "best_metrics": final_metrics,
        "runtime_sec": float(runtime),
        "iterations_completed": int(last_iter),
        "unique_evaluations": int(evaluator.new_decodes),
        "cache_hits": int(evaluator.cache_hits),
        "evaluations_to_best": int(best_eval),
        "time_to_best_sec": float(best_time),
        "stop_reason": stop_reason,
        "migration_events": int(migration_events),
        "local_search_improvements": int(ls_improvements),
        "polish_start_cmax_min": float(polish_info["start_cmax"]),
        "polish_end_cmax_min": float(polish_info["end_cmax"]),
        "polish_evaluations": int(polish_info["evaluations"]),
        "polish_rounds": int(polish_info["rounds"]),
        "polish_improvements": int(polish_info["improvements"]),
        "convergence": convergence,
    }


def _worker_run(payload: dict) -> dict:
    return run_one_mlpso(**payload)


# ---------------------------------------------------------------------------
# Multi-run wrapper + outputs
# ---------------------------------------------------------------------------

def solve_instance(
    instance_path: Path | str,
    population: int = 60,
    iterations: int = 3000,
    runs: int = 5,
    seed_base: int = 42,
    subpops: int = 3,
    mr: float = 0.80,
    pcr: float = 0.80,
    gcr: float = 0.40,
    migration_every: int = 10,
    ls_every: int = 5,
    ls_candidates: int = 12,
    ls_rounds: int = 1,
    screen_k: int = 2,
    adaptive_stall: int = 40,
    polish_budget: int = 800,
    polish_pool: int = 40,
    polish_decode_k: int = 8,
    polish_elites: int = 6,
    polish_stall_rounds: int = 8,
    max_decodes: int = 12000,
    no_improve: int = 0,
    collect_convergence: bool = True,
    convergence_every: int = 25,
    parallel_runs: bool = True,
    max_workers: int = 5,
    progress_every: int = 100,
    use_fast_decoder: bool = True,
) -> dict:
    instance_path = Path(instance_path)
    input_sha256 = hashlib.sha256(instance_path.read_bytes()).hexdigest()
    V5ScheduleDecoder, load_instance = _decoder_api(use_fast_decoder)
    base_decoder = V5ScheduleDecoder(load_instance(instance_path))
    ps = _effective_population_size(len(base_decoder.jobs), population, subpops)

    if runs <= 0:
        raise ValueError("runs must be > 0")
    seeds = [int(seed_base + i * 1009) for i in range(runs)]

    if max_workers <= 0:
        cpu = os.cpu_count() or 2
        max_workers = min(runs, max(1, cpu - 1))
    else:
        max_workers = max(1, min(runs, int(max_workers)))
    use_parallel = bool(parallel_runs and runs > 1 and max_workers > 1)

    print(
        f"[MLPSO-V3] {base_decoder.name} | objective=MIN_CMAX_FINAL_2C | "
        f"decoder={'FAST_EQUIVALENT' if use_fast_decoder else 'REFERENCE'} | "
        f"runs={runs} | mode={'PARALLEL' if use_parallel else 'SEQUENTIAL'} | "
        f"workers={max_workers if use_parallel else 1} | PS={ps} | d={subpops} | "
        f"max_decodes/run={max_decodes}",
        flush=True,
    )

    common = {
        "instance_path": str(instance_path),
        "use_fast_decoder": bool(use_fast_decoder),
        "population": int(population),
        "iterations": int(iterations),
        "subpops": int(subpops),
        "mr": float(mr),
        "pcr": float(pcr),
        "gcr": float(gcr),
        "migration_every": int(migration_every),
        "ls_every": int(ls_every),
        "ls_candidates": int(ls_candidates),
        "ls_rounds": int(ls_rounds),
        "max_decodes": int(max_decodes),
        "no_improve": int(no_improve),
        "collect_convergence": bool(collect_convergence),
        "convergence_every": int(convergence_every),
        "progress_every": int(progress_every),
        "screen_k": int(screen_k),
        "adaptive_stall": int(adaptive_stall),
        "polish_budget": int(polish_budget),
        "polish_pool": int(polish_pool),
        "polish_decode_k": int(polish_decode_k),
        "polish_elites": int(polish_elites),
        "polish_stall_rounds": int(polish_stall_rounds),
    }

    t0 = time.perf_counter()
    run_results = []
    if use_parallel:
        payloads = []
        for i, seed in enumerate(seeds, 1):
            p = dict(common)
            p.update({"seed": seed, "progress_label": f"MLPSO-V3 run {i}/{runs}"})
            payloads.append(p)
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_worker_run, p) for p in payloads]
            for fut in as_completed(futures):
                run_results.append(fut.result())
    else:
        for i, seed in enumerate(seeds, 1):
            p = dict(common)
            p.update({"seed": seed, "progress_label": f"MLPSO-V3 run {i}/{runs}"})
            run_results.append(_worker_run(p))

    run_results.sort(key=lambda r: r["seed"])
    best = min(run_results, key=lambda r: float(r["best_metrics"]["makespan_min"]))

    # Final schedule produced by selected decoder for the best chromosome.
    feasible = bool(best["feasible"])
    best_metrics, schedule = best["best_metrics"], []
    if feasible:
        final_decoder = V5ScheduleDecoder(load_instance(instance_path))
        best_metrics, schedule = final_decoder.decode(
            best["best_chromosome"], keep_schedule=True, validate_chromosome=True
        )
        if abs(best_metrics["makespan_min"] - best["best_metrics"]["makespan_min"]) > 1e-6:
            raise RuntimeError("Reconstructed schedule Cmax differs from validated MLPSO best")
    total_runtime = time.perf_counter() - t0
    if hashlib.sha256(instance_path.read_bytes()).hexdigest() != input_sha256:
        raise RuntimeError("Frozen instance changed during MLPSO execution")

    run_summaries = []
    for i, r in enumerate(run_results, 1):
        m = r["best_metrics"]
        run_summaries.append({
            "run": i,
            "seed": int(r["seed"]),
            "feasible": bool(r["feasible"]),
            "best_iteration": int(r["best_iteration"]) if r["feasible"] else None,
            "best_phase": r["best_phase"],
            "final_validation_decodes": int(r["final_validation_decodes"]),
            "makespan_min": float(m["makespan_min"]),
            "total_tardiness_min": float(m.get("total_tardiness_min", 0.0)),
            "tardy_jobs": int(m["tardy_jobs"]) if r["feasible"] else None,
            "total_flow_time_min": float(m.get("total_flow_time_min", 0.0)),
            "runtime_sec": float(r["runtime_sec"]),
            "iterations_completed": int(r["iterations_completed"]),
            "unique_evaluations": int(r["unique_evaluations"]),
            "cache_hits": int(r["cache_hits"]),
            "evaluations_to_best": int(r["evaluations_to_best"]),
            "time_to_best_sec": float(r["time_to_best_sec"]),
            "stop_reason": str(r["stop_reason"]),
            "migration_events": int(r["migration_events"]),
            "local_search_improvements": int(r["local_search_improvements"]),
            "polish_start_cmax_min": float(r["polish_start_cmax_min"]),
            "polish_end_cmax_min": float(r["polish_end_cmax_min"]),
            "polish_evaluations": int(r["polish_evaluations"]),
            "polish_rounds": int(r["polish_rounds"]),
            "polish_improvements": int(r["polish_improvements"]),
        })

    cmaxs = [r["makespan_min"] for r in run_summaries]
    mean = sum(cmaxs) / len(cmaxs) if all(math.isfinite(v) for v in cmaxs) else None
    sd = (math.sqrt(sum((x - mean) ** 2 for x in cmaxs) / (len(cmaxs) - 1))
          if len(cmaxs) > 1 else 0.0) if mean is not None else None

    return _json_finite({
        "instance_name": base_decoder.name,
        "size": len(base_decoder.jobs),
        "schema_version": str(base_decoder.data["metadata"].get("schema_version", "")),
        "algorithm": "AL13 V5 discrete MLPSO-V3 with guided insertion/adaptive neighborhoods/elite migration/reserved elite polish",
        "variant_version": "MLPSO_V3_ELITE_POLISH_2026-10-04",
        "objective": "MIN_CMAX_FINAL_2C",
        "feasible": feasible,
        "proven_optimal": False,
        "status_name": "Feasible" if feasible else "No feasible schedule found",
        "message": "Best found schedule; optimality is not proven." if feasible else
                   "MLPSO did not find a feasible schedule within the search limits; this does not prove infeasibility.",
        "input_sha256": input_sha256,
        "decoder_module": V5ScheduleDecoder.__module__,
        "decoder_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ("benchmark_common.py", "benchmark_common_fast.py")},
        "objective_terms": ["Cmax only; tardiness/flow are diagnostics"],
        "decoder": V5ScheduleDecoder.__module__ + ".V5ScheduleDecoder",
        "decoder_semantics_changed": False,
        "particle_representation": "permutation of Job_ID (stored internally as job indices)",
        "population": int(ps),
        "subpopulations": int(subpops),
        "subpopulation_size": int(ps // subpops),
        "iterations_limit": int(iterations),
        "max_decodes_per_run": int(max_decodes),
        "runs": int(runs),
        "seeds": seeds,
        "parallel_runs": bool(use_parallel),
        "max_workers": int(max_workers if use_parallel else 1),
        "MR": float(mr),
        "PCR": float(pcr),
        "GCR": float(gcr),
        "migration_every": int(migration_every),
        "local_search_every": int(ls_every),
        "local_search_candidates": int(ls_candidates),
        "local_search_rounds": int(ls_rounds),
        "screen_k": int(screen_k),
        "adaptive_stall": int(adaptive_stall),
        "polish_budget": int(polish_budget),
        "polish_pool": int(polish_pool),
        "polish_decode_k": int(polish_decode_k),
        "polish_elites": int(polish_elites),
        "polish_stall_rounds": int(polish_stall_rounds),
        "v5_structural_repair": True,
        "partial_permutation_decoding_used": False,
        "best_makespan_min": float(best_metrics["makespan_min"]),
        "best_total_tardiness_min": float(best_metrics.get("total_tardiness_min", 0.0)),
        "best_tardy_jobs": int(best_metrics["tardy_jobs"]) if feasible else None,
        "best_total_flow_time_min": float(best_metrics.get("total_flow_time_min", 0.0)),
        "best_chromosome": list(best["best_chromosome"]) if feasible else [],
        "best_seed": int(best["seed"]) if feasible else None,
        "best_iteration": int(best["best_iteration"]) if feasible else None,
        "best_phase": best["best_phase"],
        "search_stop_reason": best["stop_reason"],
        "completed_runs": len(run_results),
        "feasible_runs": sum(r["feasible"] for r in run_results),
        "unique_evaluations": sum(r["unique_evaluations"] for r in run_results),
        "final_validation_decodes": sum(r["final_validation_decodes"] for r in run_results),
        "schedule_reconstruction_decodes": int(feasible),
        "total_tardiness_min": best_metrics["total_tardiness_min"],
        "tardy_jobs": best_metrics["tardy_jobs"] if feasible else None,
        "total_flow_time_min": best_metrics["total_flow_time_min"],
        "mean_makespan_min": mean,
        "sd_makespan_min": sd,
        "total_runtime_sec": float(total_runtime),
        "mean_run_runtime_sec": float(sum(r["runtime_sec"] for r in run_summaries) / len(run_summaries)),
        "run_summaries": run_summaries,
        "convergence_by_run": [r["convergence"] for r in run_results] if collect_convergence else None,
        "schedule": schedule,
    })


def write_run_summary_csv(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = result["run_summaries"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_convergence_csv(result: dict, path: Path) -> None:
    conv = result.get("convergence_by_run")
    if not conv:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    seeds = result["seeds"]
    fields = [
        "Run", "Seed", "Iteration", "Gbest_Cmax_Min", "Population_Best_Min",
        "Population_Mean_Min", "Decoder_Evaluations", "Evaluations_To_Best",
        "Time_To_Best_Sec", "Stall", "Stop_Reason",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for ri, rows in enumerate(conv, 1):
            for row in rows:
                w.writerow({"Run": ri, "Seed": seeds[ri - 1], **row})


def write_schedule_csv(result: dict, path: Path) -> None:
    rows = result.get("schedule") or []
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Solve one AL13 V5 instance with discrete MLPSO-V3 using the validated V5 decoder."
    )
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--population", "--swarm-size", dest="population", type=int, default=60)
    ap.add_argument("--iterations", type=int, default=3000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed-base", type=int, default=42)
    ap.add_argument("--subpops", "-d", dest="subpops", type=int, default=3)
    ap.add_argument("--mr", type=float, default=0.80)
    ap.add_argument("--pcr", type=float, default=0.80)
    ap.add_argument("--gcr", type=float, default=0.40)
    ap.add_argument("--migration-every", type=int, default=10)
    ap.add_argument("--ls-every", type=int, default=5)
    ap.add_argument("--ls-candidates", type=int, default=12)
    ap.add_argument("--ls-rounds", type=int, default=1)
    ap.add_argument("--screen-k", type=int, default=2)
    ap.add_argument("--adaptive-stall", type=int, default=40)
    ap.add_argument("--polish-budget", type=int, default=800, help="Reserved tail full-decoder evaluations per run for elite polish")
    ap.add_argument("--polish-pool", type=int, default=40, help="Cheap candidate pool size per polish round")
    ap.add_argument("--polish-decode-k", type=int, default=8, help="Candidates fully decoded per polish round")
    ap.add_argument("--polish-elites", type=int, default=6, help="Historical Pbest elites retained for late-stage polishing")
    ap.add_argument("--polish-stall-rounds", type=int, default=8)
    ap.add_argument("--max-decodes", type=int, default=12000)
    ap.add_argument("--no-improve", type=int, default=0)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--sequential-runs", action="store_true")
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--convergence-every", type=int, default=25)
    ap.add_argument("--no-convergence", action="store_true")
    ap.add_argument(
        "--reference-decoder", action="store_true",
        help="Use benchmark_common.V5ScheduleDecoder instead of the validated fast equivalent decoder",
    )
    args = ap.parse_args()

    result = solve_instance(
        instance_path=args.instance,
        population=args.population,
        iterations=max(0, args.iterations),
        runs=args.runs,
        seed_base=args.seed_base,
        subpops=args.subpops,
        mr=args.mr,
        pcr=args.pcr,
        gcr=args.gcr,
        migration_every=max(0, args.migration_every),
        ls_every=max(0, args.ls_every),
        ls_candidates=max(0, args.ls_candidates),
        ls_rounds=max(0, args.ls_rounds),
        screen_k=max(1, args.screen_k),
        adaptive_stall=max(1, args.adaptive_stall),
        polish_budget=max(0, args.polish_budget),
        polish_pool=max(1, args.polish_pool),
        polish_decode_k=max(1, args.polish_decode_k),
        polish_elites=max(2, args.polish_elites),
        polish_stall_rounds=max(1, args.polish_stall_rounds),
        max_decodes=max(1, args.max_decodes),
        no_improve=max(0, args.no_improve),
        collect_convergence=not args.no_convergence,
        convergence_every=max(1, args.convergence_every),
        parallel_runs=not args.sequential_runs,
        max_workers=args.workers,
        progress_every=max(0, args.progress_every),
        use_fast_decoder=not args.reference_decoder,
    )

    out = args.output or args.instance.with_name(args.instance.stem + "__mlpso_v3_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")

    stem = out.stem
    if stem.endswith("_result"):
        base = stem[:-7]
    else:
        base = stem
    write_run_summary_csv(result, out.with_name(base + "_run_summary.csv"))
    write_convergence_csv(result, out.with_name(base + "_convergence.csv"))
    write_schedule_csv(result, out.with_name(base + "_schedule.csv"))

    if not result["feasible"]:
        print(result["message"], flush=True)
        print(f"Saved: {out}", flush=True)
        return

    mean_label = f"{result['mean_makespan_min']:.3f}" if result['mean_makespan_min'] is not None else "not available"
    sd_label = f"{result['sd_makespan_min']:.3f}" if result['sd_makespan_min'] is not None else "not available"

    print(
        f"{result['instance_name']} | MLPSO-V3 best Cmax={result['best_makespan_min']:.3f} min | "
        f"mean={mean_label} | SD={sd_label} | "
        f"wall={result['total_runtime_sec']:.2f}s | runs={result['runs']} | "
        f"max_decodes/run={result['max_decodes_per_run']}",
        flush=True,
    )
    print(f"Saved: {out}", flush=True)


if __name__ == "__main__":
    main()
