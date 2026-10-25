#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AL13 V5 Improved GA: V5-aware seeding + multi-crossover + budgeted VNS.

This solver is intentionally built on the SAME physical decoder as the validated
baseline V5 GA.  It does not introduce a second scheduling model.

Physical/model rules remain in benchmark_common.V5ScheduleDecoder:
- 2B is auxiliary; main route is PRE->FINAL->LASER->SCALE->OIL->SEAL->VISUAL.
- PRE->FINAL is streaming (SS_FIRST_BELT + final synchronization).
- OIL is explicit <=6-belt cycles with W4 load / automatic dip / W5 unload.
- 2C is exact-10 batching with same-key 5+5 cross-job pooling.
- Product changeover is the composite clean-pipeline setup before successor PRE.

Algorithmic changes versus the baseline GA are search-side only:
1) integer/bytes chromosomes and a per-run full-decoder cache;
2) V5-aware insertion-refined seed (complete permutations only; no partial decode);
3) OX/PMX/PBX multi-crossover;
4) partial generational replacement (``offspring_fraction``), so one epoch needs
   fewer NEW expensive V5 decodes and a larger epoch budget is practical;
5) budgeted, plateau-triggered VNS using only complete permutations;
6) all offspring/local-search candidates pass the exact same V5 structural repair
   used by the baseline GA for the clean-pipeline 5-belt remainder interaction.

IMPORTANT: objective is exactly the current thesis objective: MIN Cmax at final 2C.
Tardiness/flow are reported only as diagnostics and never compensate for Cmax.

Python 3.10+
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
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .benchmark_common import V5ScheduleDecoder, compact_metrics_dict, fitness_key, load_instance

EPS = 1e-9


# ---------------------------------------------------------------------------
# Compact permutation representation and V5-safe structural repair
# ---------------------------------------------------------------------------


def _pack(seq: Sequence[int], byte_mode: bool):
    return bytes(seq) if byte_mode else tuple(int(x) for x in seq)


def repair_product_change_remainders(ch, decoder: V5ScheduleDecoder):
    """Repair the exact V5 clean-pipeline 2C/setup structural interaction.

    If a product change follows a 5-belt-remainder job while its Batch_Key has an
    unmatched remainder in the prefix, pull the next matching 5-belt job forward
    before the product change.  This preserves a permutation and avoids spending
    decoder time on a chromosome that is structurally blocked by final-2C clear.
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
    return bytes(vals) if isinstance(ch, bytes) else tuple(vals)


def _metric_cmax(metrics: Sequence[float]) -> float:
    return float(metrics[0])


def evaluate_one(ch, decoder: V5ScheduleDecoder, cache: MutableMapping):
    """Evaluate one COMPLETE permutation; return (Cmax-key, metrics, new_decode)."""
    entry = cache.get(ch)
    if entry is not None:
        return entry[0], entry[1], 0
    m = decoder.decode_indices(ch)
    key = fitness_key(m)
    cache[ch] = (key, m)
    return key, m, 1


def evaluate_population(pop, decoder: V5ScheduleDecoder, cache: MutableMapping):
    keys, metrics = [None] * len(pop), [None] * len(pop)
    new_decodes = 0
    for i, ch in enumerate(pop):
        key, m, d = evaluate_one(ch, decoder, cache)
        keys[i] = key
        metrics[i] = m
        new_decodes += d
    return keys, metrics, new_decodes


# ---------------------------------------------------------------------------
# Cheap V5-aware surrogate used ONLY to shortlist complete candidates
# ---------------------------------------------------------------------------


def structural_surrogate(ch: Sequence[int], decoder: V5ScheduleDecoder) -> float:
    """Cheap ordering score; NEVER replaces the real V5 decoder for acceptance.

    Lower is better.  The score rewards low setup burden, reasonable release order,
    early placement of heavy jobs, and remainder-pair compatibility.  It is used
    only to decide which few insertion candidates deserve an expensive full decode.
    """
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
        if pos == 0:
            continue
        gp = vals[pos - 1]
        p = decoder.jobs[gp]
        setup_sum += float(decoder.setup[decoder.product[p]][decoder.product[j]])
        release_penalty += max(0.0, decoder.release[j] - decoder.release[p])
        if decoder.product[p] != decoder.product[j] and decoder.qty[p] % 10 == 5:
            keyp = decoder.batch_key[p]
            if rem_count.get(keyp, 0) % 2 == 1:
                unmatched_penalty += 1.0

    # Setup and impossible-at-change structure dominate.  Other terms only break ties.
    return (
        1_000_000.0 * unmatched_penalty
        + 100.0 * setup_sum
        + 0.02 * release_penalty
        + 0.002 * weighted_work
    )


def _shortlist_insert_positions(
    base_without: Sequence[int],
    gene: int,
    decoder: V5ScheduleDecoder,
    max_positions: int,
) -> List[int]:
    """Rank ALL cheap insertion positions, return only top-k for full decoding."""
    vals = list(base_without)
    scored = []
    for pos in range(len(vals) + 1):
        cand = vals[:]
        cand.insert(pos, gene)
        scored.append((structural_surrogate(cand, decoder), pos))
    scored.sort(key=lambda x: (x[0], x[1]))
    k = max(1, min(int(max_positions), len(scored)))
    return [pos for _score, pos in scored[:k]]


# ---------------------------------------------------------------------------
# V5-aware complete-permutation insertion seed (NEH-inspired, decoder-safe)
# ---------------------------------------------------------------------------


def _deterministic_seeds(decoder: V5ScheduleDecoder, byte_mode: bool):
    ids = list(range(len(decoder.jobs)))
    fifo = sorted(ids, key=lambda i: (decoder.release[decoder.jobs[i]], decoder.priority[decoder.jobs[i]], decoder.jobs[i]))
    spt = sorted(ids, key=lambda i: (decoder.min_workload[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    edd = sorted(ids, key=lambda i: (decoder.due[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    family = sorted(ids, key=lambda i: (decoder.product[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    batch_key = sorted(ids, key=lambda i: (decoder.batch_key[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    rel_family = sorted(ids, key=lambda i: (round(decoder.release[decoder.jobs[i]] / 120.0), decoder.product[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]))
    out = []
    seen = set()
    for seq in (fifo, spt, edd, family, batch_key, rel_family):
        ch = repair_product_change_remainders(_pack(seq, byte_mode), decoder)
        if ch not in seen:
            seen.add(ch)
            out.append(ch)
    return out


def build_insertion_refined_seed(
    decoder: V5ScheduleDecoder,
    decode_budget: int = 60,
    positions_per_job: int = 4,
    deadline: float | None = None,
):
    """Build one strong seed with a HARD full-decode budget.

    Unlike the old solver, this never decodes partial chromosomes (invalid under the
    V5 exact-10/clean-pipeline logic).  Start from the best cheap deterministic full
    seed, then relocate high-workload jobs.  All tested candidates are COMPLETE and
    accepted only after the real V5 decoder evaluates them.
    """
    n = len(decoder.jobs)
    byte_mode = n <= 255
    cache: Dict[object, object] = {}
    seeds = _deterministic_seeds(decoder, byte_mode)
    best = seeds[0]
    best_key, _best_m, used = evaluate_one(best, decoder, cache)
    budget_used = used
    for ch in seeds[1:]:
        if budget_used >= decode_budget or (deadline is not None and time.perf_counter() >= deadline):
            break
        key, _m, d = evaluate_one(ch, decoder, cache)
        budget_used += d
        if key < best_key:
            best, best_key = ch, key

    ranked_genes = sorted(
        range(n),
        key=lambda i: (-decoder.min_workload[decoder.jobs[i]], decoder.release[decoder.jobs[i]], decoder.jobs[i]),
    )
    for gene in ranked_genes:
        if budget_used >= decode_budget or (deadline is not None and time.perf_counter() >= deadline):
            break
        vals = list(best)
        old_pos = vals.index(gene)
        vals.pop(old_pos)
        positions = _shortlist_insert_positions(vals, gene, decoder, positions_per_job)
        # Always allow the current position so the move cannot force degradation.
        if old_pos not in positions:
            positions.append(min(old_pos, len(vals)))
        local_best = best
        local_key = best_key
        for pos in positions:
            if budget_used >= decode_budget or (deadline is not None and time.perf_counter() >= deadline):
                break
            cand_list = vals[:]
            cand_list.insert(pos, gene)
            cand = repair_product_change_remainders(_pack(cand_list, byte_mode), decoder)
            key, _m, d = evaluate_one(cand, decoder, cache)
            budget_used += d
            if key < local_key:
                local_best, local_key = cand, key
        if local_key < best_key:
            best, best_key = local_best, local_key

    return best, {
        "decode_budget": int(decode_budget),
        "new_decodes": int(budget_used),
        "cmax_min": float(best_key[0]),
    }


def create_initial_population(
    decoder: V5ScheduleDecoder,
    rng: random.Random,
    pop_size: int,
    refined_seed=None,
    allow_partial: bool = False,
    deadline: float | None = None,
):
    n = len(decoder.jobs)
    byte_mode = n <= 255
    ids = list(range(n))
    max_unique = math.factorial(n) if n <= 10 else None
    target = min(pop_size, max_unique) if max_unique is not None else pop_size
    target = max(2 if n > 1 else 1, int(target))

    pop, seen = [], set()

    def add(ch):
        if not isinstance(ch, (bytes, tuple)):
            ch = _pack(ch, byte_mode)
        ch = repair_product_change_remainders(ch, decoder)
        if ch not in seen and len(pop) < target:
            seen.add(ch)
            pop.append(ch)

    if refined_seed is not None:
        add(refined_seed)
    for ch in _deterministic_seeds(decoder, byte_mode):
        add(ch)

    attempts = 0
    raw_seen = set() if allow_partial and n <= 8 else None
    while len(pop) < target and attempts < target * 500:
        if pop and deadline is not None and time.perf_counter() >= deadline:
            break
        attempts += 1
        seq = ids[:]
        rng.shuffle(seq)
        packed = _pack(seq, byte_mode)
        add(packed)
        if raw_seen is not None:
            raw_seen.add(packed)
            if len(raw_seen) == max_unique:
                break
    if len(pop) != target and not (allow_partial and pop):
        raise RuntimeError(f"Could create only {len(pop)} unique chromosomes, target={target}")
    return pop


# ---------------------------------------------------------------------------
# Permutation crossover and mutation
# ---------------------------------------------------------------------------


def ox_crossover(p1, p2, rng: random.Random):
    n = len(p1)
    if n < 2:
        return p1, p2
    a, b = sorted(rng.sample(range(n), 2))
    byte_mode = isinstance(p1, bytes)

    def child(pa, pb):
        out = [-1] * n
        seg = list(pa[a:b + 1])
        out[a:b + 1] = seg
        used = set(seg)
        pos = (b + 1) % n
        for g in pb:
            if g in used:
                continue
            out[pos] = g
            pos = (pos + 1) % n
            if pos == a:
                break
        return _pack(out, byte_mode)

    return child(p1, p2), child(p2, p1)


def pmx_crossover(p1, p2, rng: random.Random):
    n = len(p1)
    if n < 2:
        return p1, p2
    a, b = sorted(rng.sample(range(n), 2))
    byte_mode = isinstance(p1, bytes)

    def child(pa, pb):
        pa, pb = list(pa), list(pb)
        out = [-1] * n
        out[a:b + 1] = pa[a:b + 1]
        pos_in_pb = [0] * n
        in_seg = [False] * n
        for pos, gene in enumerate(pb):
            pos_in_pb[gene] = pos
        for gene in pa[a:b + 1]:
            in_seg[gene] = True
        for i in range(a, b + 1):
            gene = pb[i]
            if in_seg[gene]:
                continue
            pos = i
            while a <= pos <= b:
                pos = pos_in_pb[pa[pos]]
            out[pos] = gene
        for i in range(n):
            if out[i] < 0:
                out[i] = pb[i]
        return _pack(out, byte_mode)

    return child(p1, p2), child(p2, p1)


def pbx_crossover(p1, p2, rng: random.Random):
    n = len(p1)
    if n < 2:
        return p1, p2
    byte_mode = isinstance(p1, bytes)
    k = max(1, n // 2)
    positions = rng.sample(range(n), k)

    def child(pa, pb):
        out = [-1] * n
        used = [False] * n
        for pos in positions:
            g = pa[pos]
            out[pos] = g
            used[g] = True
        fill = (g for g in pb if not used[g])
        for i in range(n):
            if out[i] < 0:
                out[i] = next(fill)
        return _pack(out, byte_mode)

    return child(p1, p2), child(p2, p1)


def apply_crossover(p1, p2, rng: random.Random, crossover_type: str, weights: Sequence[float]):
    if crossover_type == "multi":
        names = ("ox", "pmx", "pbx")
        total = float(sum(weights))
        x = rng.random() * total
        acc = 0.0
        op = names[-1]
        for name, w in zip(names, weights):
            acc += float(w)
            if x <= acc:
                op = name
                break
    else:
        op = crossover_type
    if op == "ox":
        c1, c2 = ox_crossover(p1, p2, rng)
    elif op == "pmx":
        c1, c2 = pmx_crossover(p1, p2, rng)
    elif op == "pbx":
        c1, c2 = pbx_crossover(p1, p2, rng)
    else:
        raise ValueError(f"Unknown crossover type: {op}")
    return c1, c2, op


def swap_mutation(ch, rng):
    if len(ch) < 2:
        return ch
    a, b = rng.sample(range(len(ch)), 2)
    out = bytearray(ch) if isinstance(ch, bytes) else list(ch)
    out[a], out[b] = out[b], out[a]
    return bytes(out) if isinstance(ch, bytes) else tuple(out)


def insert_mutation(ch, rng):
    if len(ch) < 2:
        return ch
    a, b = rng.sample(range(len(ch)), 2)
    out = bytearray(ch) if isinstance(ch, bytes) else list(ch)
    g = out.pop(a)
    out.insert(b, g)
    return bytes(out) if isinstance(ch, bytes) else tuple(out)


def reverse_mutation(ch, rng):
    if len(ch) < 2:
        return ch
    a, b = sorted(rng.sample(range(len(ch)), 2))
    out = bytearray(ch) if isinstance(ch, bytes) else list(ch)
    out[a:b + 1] = out[a:b + 1][::-1]
    return bytes(out) if isinstance(ch, bytes) else tuple(out)


def mixed_mutation(ch, rng):
    r = rng.random()
    if r < 0.50:
        return insert_mutation(ch, rng)
    if r < 0.80:
        return swap_mutation(ch, rng)
    return reverse_mutation(ch, rng)


def tournament(pop, keys, rng, k):
    idxs = rng.sample(range(len(pop)), min(k, len(pop)))
    return pop[min(idxs, key=lambda i: keys[i])]


# ---------------------------------------------------------------------------
# Diversity and adaptive mutation
# ---------------------------------------------------------------------------


def population_diversity(pop, rng: random.Random, samples: int = 64) -> float:
    if len(pop) < 2 or not pop[0]:
        return 0.0
    n = len(pop[0])
    max_pairs = len(pop) * (len(pop) - 1) // 2
    if max_pairs <= samples:
        pairs: Iterable[Tuple[int, int]] = itertools.combinations(range(len(pop)), 2)
    else:
        pairs = (tuple(rng.sample(range(len(pop)), 2)) for _ in range(samples))
    total = 0.0
    count = 0
    for i, j in pairs:
        total += sum(a != b for a, b in zip(pop[i], pop[j])) / n
        count += 1
    return total / count if count else 0.0


def unique_ratio(pop) -> float:
    return len(set(pop)) / max(1, len(pop))


def adaptive_mutation(base: float, maximum: float, diversity: float, stall: int, restart_after: int) -> float:
    d_pressure = max(0.0, min(1.0, (0.40 - diversity) / 0.40))
    s_pressure = max(0.0, min(1.0, stall / max(1, restart_after)))
    pressure = max(d_pressure, s_pressure)
    return min(maximum, base + (maximum - base) * pressure)


# ---------------------------------------------------------------------------
# Budgeted VNS: full V5 decoder only, no keep_schedule critical-path decoding
# ---------------------------------------------------------------------------


def _product_block_move(ch, decoder: V5ScheduleDecoder, rng: random.Random):
    vals = list(ch)
    if len(vals) < 3:
        return insert_mutation(ch, rng)
    blocks = []
    s = 0
    for i in range(1, len(vals) + 1):
        if i == len(vals) or decoder.product[decoder.jobs[vals[i]]] != decoder.product[decoder.jobs[vals[s]]]:
            blocks.append((s, i))
            s = i
    if len(blocks) <= 1:
        return insert_mutation(ch, rng)
    a, b = rng.choice(blocks)
    block = vals[a:b]
    rest = vals[:a] + vals[b:]
    target = rng.randrange(0, len(rest) + 1)
    out = rest[:target] + block + rest[target:]
    return _pack(out, isinstance(ch, bytes))


def _batch_partner_move(ch, decoder: V5ScheduleDecoder, rng: random.Random):
    vals = list(ch)
    rem_positions = [
        i for i, g in enumerate(vals)
        if decoder.qty[decoder.jobs[g]] % 10 == 5
    ]
    if not rem_positions:
        return insert_mutation(ch, rng)
    a = rng.choice(rem_positions)
    ja = decoder.jobs[vals[a]]
    key = decoder.batch_key[ja]
    partners = [
        i for i, g in enumerate(vals)
        if i != a and decoder.qty[decoder.jobs[g]] % 10 == 5 and decoder.batch_key[decoder.jobs[g]] == key
    ]
    if not partners:
        return insert_mutation(ch, rng)
    b = min(partners, key=lambda p: abs(p - a))
    gene = vals.pop(b)
    if b < a:
        a -= 1
    vals.insert(a + 1, gene)
    return _pack(vals, isinstance(ch, bytes))


def apply_budgeted_vns(
    elite_ch,
    decoder: V5ScheduleDecoder,
    cache: MutableMapping,
    rng: random.Random,
    decode_budget: int = 24,
    rounds: int = 2,
):
    """VNS with a hard NEW-decoder-call budget.

    Cache hits do not consume the budget.  Acceptance uses only the real Cmax key.
    """
    current = elite_ch
    current_key, current_metrics, _ = evaluate_one(current, decoder, cache)
    best = current
    best_key = current_key
    best_metrics = current_metrics
    new_decodes = 0
    improvements = 0
    tried = 0

    ops = ("insert", "swap", "reverse", "product_block", "batch_partner")
    for _round in range(max(1, int(rounds))):
        local_improved = False
        for op in ops:
            attempts = 0
            while attempts < 4 and new_decodes < decode_budget:
                attempts += 1
                tried += 1
                if op == "insert":
                    cand = insert_mutation(current, rng)
                elif op == "swap":
                    cand = swap_mutation(current, rng)
                elif op == "reverse":
                    cand = reverse_mutation(current, rng)
                elif op == "product_block":
                    cand = _product_block_move(current, decoder, rng)
                else:
                    cand = _batch_partner_move(current, decoder, rng)
                cand = repair_product_change_remainders(cand, decoder)
                key, metrics, d = evaluate_one(cand, decoder, cache)
                new_decodes += d
                if key < best_key:
                    best, best_key, best_metrics = cand, key, metrics
                    current = cand
                    current_key = key
                    improvements += 1
                    local_improved = True
                    break
            if new_decodes >= decode_budget:
                break
        if not local_improved or new_decodes >= decode_budget:
            break

    return best, best_metrics, {
        "new_decodes": int(new_decodes),
        "candidates_tried": int(tried),
        "improvements": int(improvements),
    }


# ---------------------------------------------------------------------------
# One improved GA run
# ---------------------------------------------------------------------------


def run_one_improved_ga(
    decoder: V5ScheduleDecoder,
    seed: int,
    population: int,
    generations: int,
    crossover_rate: float,
    crossover_type: str,
    crossover_weights: Sequence[float],
    mutation_rate: float,
    mutation_max: float,
    elite: int,
    tournament_k: int,
    no_improve: int,
    injection_fraction: float,
    diversity_threshold: float,
    restart_after: int,
    restart_fraction: float,
    offspring_fraction: float,
    use_vns: bool,
    vns_stagnation_trigger: int,
    vns_frequency: int,
    vns_decode_budget: int,
    vns_rounds: int,
    refined_seed,
    collect_convergence: bool,
    convergence_every: int,
    diversity_every: int,
    progress_every: int,
    progress_label: str,
) -> dict:
    rng = random.Random(seed)
    diag_rng = random.Random(seed ^ 0x5DEECE66D)
    vns_rng = random.Random(seed ^ 0x9E3779B9)
    pop = create_initial_population(decoder, rng, population, refined_seed=refined_seed)
    cache: Dict[object, object] = {}

    best_ch = None
    best_metrics = None
    best_key = (math.inf,)
    best_gen = 0
    stall = 0
    restart_count = 0
    injection_count = 0
    vns_calls = 0
    vns_new_decodes = 0
    vns_improvements = 0
    convergence = []
    crossover_usage = {"ox": 0, "pmx": 0, "pbx": 0}
    t0 = time.perf_counter()

    last_div = population_diversity(pop, diag_rng)
    last_ur = unique_ratio(pop)
    n_jobs = len(decoder.jobs)
    byte_mode = n_jobs <= 255
    base_genes = list(range(n_jobs))

    if progress_every > 0:
        print(
            f"[{progress_label}] start | seed={seed} | pop={len(pop)} | epochs={generations} | "
            f"offspring_fraction={offspring_fraction:.2f} | VNS={'ON' if use_vns else 'OFF'} | "
            f"objective=MIN_CMAX_FINAL_2C",
            flush=True,
        )

    for gen in range(generations + 1):
        keys, metrics, _new = evaluate_population(pop, decoder, cache)
        order = sorted(range(len(pop)), key=lambda i: keys[i])
        bi = order[0]
        improved = False
        if keys[bi] < best_key:
            best_key = keys[bi]
            best_ch = pop[bi]
            best_metrics = metrics[bi]
            best_gen = gen
            stall = 0
            improved = True
        else:
            stall += 1

        vns_applied = False
        vns_info = {"new_decodes": 0, "candidates_tried": 0, "improvements": 0}
        vns_due = (
            use_vns
            and vns_stagnation_trigger > 0
            and vns_frequency > 0
            and stall >= vns_stagnation_trigger
            and (stall - vns_stagnation_trigger) % vns_frequency == 0
        )
        if vns_due:
            vns_applied = True
            vns_calls += 1
            elite_idx = order[0]
            old_key = keys[elite_idx]
            vch, vm, vns_info = apply_budgeted_vns(
                pop[elite_idx], decoder, cache, vns_rng,
                decode_budget=vns_decode_budget, rounds=vns_rounds,
            )
            vkey = fitness_key(vm)
            vns_new_decodes += int(vns_info["new_decodes"])
            vns_improvements += int(vns_info["improvements"])
            if vkey < old_key:
                worst_idx = order[-1]
                pop[worst_idx] = vch
                keys[worst_idx] = vkey
                metrics[worst_idx] = vm
                order = sorted(range(len(pop)), key=lambda i: keys[i])
                bi = order[0]
                if keys[bi] < best_key:
                    best_key = keys[bi]
                    best_ch = pop[bi]
                    best_metrics = metrics[bi]
                    best_gen = gen
                    stall = 0
                    improved = True

        if gen == 0 or gen == generations or gen % max(1, diversity_every) == 0:
            last_div = population_diversity(pop, diag_rng)
            last_ur = unique_ratio(pop)
        pm = adaptive_mutation(mutation_rate, mutation_max, last_div, stall, restart_after)

        assert best_ch is not None and best_metrics is not None
        if collect_convergence and (gen == 0 or gen == generations or gen % max(1, convergence_every) == 0):
            cvals = [_metric_cmax(m) for m in metrics]
            convergence.append({
                "Generation": int(gen),
                "Best_Cmax_Min": float(best_metrics[0]),
                "Population_Best_Min": float(min(cvals)),
                "Population_Mean_Min": float(sum(cvals) / len(cvals)),
                "Population_Worst_Min": float(max(cvals)),
                "Population_Diversity": float(last_div),
                "Unique_Chromosome_Ratio": float(last_ur),
                "Mutation_Rate": float(pm),
                "Stall": int(stall),
                "Restarts": int(restart_count),
                "Unique_Evaluations": int(len(cache)),
                "VNS_Applied": bool(vns_applied),
                "VNS_New_Decodes": int(vns_info["new_decodes"]),
            })

        if progress_every > 0 and (gen == 0 or gen == generations or gen % progress_every == 0):
            elapsed = time.perf_counter() - t0
            eta = elapsed / gen * (generations - gen) if gen else 0.0
            print(
                f"[{progress_label}] gen {gen:>5}/{generations} | best={best_metrics[0]:.3f} | "
                f"stall={stall} | div={last_div:.3f} | unique={last_ur:.3f} | pm={pm:.3f} | "
                f"restarts={restart_count} | evals={len(cache)} | VNS_calls={vns_calls} | "
                f"elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
                flush=True,
            )

        if gen >= generations:
            break
        if no_improve > 0 and stall >= no_improve:
            break

        # Partial generational replacement: keep the best survivors, create only a
        # configured fraction of NEW offspring.  This is the main epoch-throughput
        # improvement because survivor fitness is already cached.
        keep_elite = min(max(1, elite), len(pop))
        replace_target = max(1, int(round(len(pop) * max(0.05, min(1.0, offspring_fraction)))))
        survivor_count = max(keep_elite, len(pop) - replace_target)
        survivor_count = min(len(pop), survivor_count)
        new_pop = [pop[i] for i in order[:survivor_count]]
        seen = set(new_pop)

        # Stall restart: retain elites only, inject a controlled fresh fraction.
        if restart_after > 0 and stall >= restart_after:
            restart_count += 1
            stall = 0
            survivor_count = keep_elite
            new_pop = [pop[i] for i in order[:keep_elite]]
            seen = set(new_pop)
            restart_n = max(1, int(math.ceil(restart_fraction * len(pop))))
            while len(new_pop) < min(len(pop), keep_elite + restart_n):
                seq = base_genes[:]
                rng.shuffle(seq)
                imm = repair_product_change_remainders(_pack(seq, byte_mode), decoder)
                if imm not in seen:
                    seen.add(imm)
                    new_pop.append(imm)

        # Low diversity injection if needed.
        if last_div < diversity_threshold:
            injection_count += 1
            target = min(len(pop), len(new_pop) + max(1, int(math.ceil(injection_fraction * len(pop)))))
            attempts = 0
            while len(new_pop) < target and attempts < target * 100:
                attempts += 1
                seq = base_genes[:]
                rng.shuffle(seq)
                imm = repair_product_change_remainders(_pack(seq, byte_mode), decoder)
                if imm not in seen:
                    seen.add(imm)
                    new_pop.append(imm)

        attempts = 0
        max_attempts = len(pop) * 500
        while len(new_pop) < len(pop) and attempts < max_attempts:
            attempts += 1
            p1 = tournament(pop, keys, rng, tournament_k)
            p2 = tournament(pop, keys, rng, tournament_k)
            if rng.random() < crossover_rate:
                c1, c2, op = apply_crossover(p1, p2, rng, crossover_type, crossover_weights)
                crossover_usage[op] += 1
            else:
                c1, c2 = p1, p2
            if rng.random() < pm:
                c1 = mixed_mutation(c1, rng)
            if rng.random() < pm:
                c2 = mixed_mutation(c2, rng)
            for child in (c1, c2):
                child = repair_product_change_remainders(child, decoder)
                if child not in seen:
                    seen.add(child)
                    new_pop.append(child)
                    if len(new_pop) >= len(pop):
                        break

        while len(new_pop) < len(pop):
            seq = base_genes[:]
            rng.shuffle(seq)
            ch = repair_product_change_remainders(_pack(seq, byte_mode), decoder)
            if ch not in seen or len(seen) >= len(pop):
                seen.add(ch)
                new_pop.append(ch)
        pop = new_pop

    runtime = time.perf_counter() - t0
    best_ids = [decoder.jobs[i] for i in best_ch]
    if progress_every > 0:
        print(
            f"[{progress_label}] done | best={best_metrics[0]:.3f} | best_gen={best_gen} | "
            f"time={runtime:.2f}s | evals={len(cache)} | VNS_calls={vns_calls}",
            flush=True,
        )

    return {
        "seed": int(seed),
        "best_chromosome": best_ids,
        "best_metrics": compact_metrics_dict(best_metrics),
        "best_generation": int(best_gen),
        "runtime_sec": float(runtime),
        "generations_completed": int(gen),
        "unique_evaluations": int(len(cache)),
        "restart_count": int(restart_count),
        "low_diversity_injection_count": int(injection_count),
        "final_diversity": float(last_div),
        "final_unique_ratio": float(last_ur),
        "vns_calls": int(vns_calls),
        "vns_new_decodes": int(vns_new_decodes),
        "vns_improvements": int(vns_improvements),
        "crossover_usage": crossover_usage,
        "convergence": convergence,
    }


# ---------------------------------------------------------------------------
# Multi-run solver and output
# ---------------------------------------------------------------------------


def _worker_run(payload: dict) -> dict:
    data = load_instance(payload["instance_path"])
    decoder = V5ScheduleDecoder(data)
    return run_one_improved_ga(decoder=decoder, refined_seed=payload.get("refined_seed"), **payload["ga_args"])


def solve_instance(
    instance_path: Path | str,
    population: int = 60,
    generations: int = 1200,
    runs: int = 5,
    seed_base: int = 42,
    crossover_rate: float = 0.90,
    crossover_type: str = "multi",
    crossover_weights: Sequence[float] = (1.0, 1.0, 1.0),
    mutation_rate: float = 0.15,
    mutation_max: float = 0.55,
    elite: int = 2,
    tournament_k: int = 3,
    no_improve: int = 0,
    injection_fraction: float = 0.25,
    diversity_threshold: float = 0.25,
    restart_after: int = 300,
    restart_fraction: float = 0.50,
    offspring_fraction: float = 0.50,
    use_refined_seed: bool = True,
    seed_decode_budget: int = 60,
    seed_positions_per_job: int = 4,
    use_vns: bool = True,
    vns_stagnation_trigger: int = 120,
    vns_frequency: int = 80,
    vns_decode_budget: int = 24,
    vns_rounds: int = 2,
    collect_convergence: bool = True,
    convergence_every: int = 10,
    parallel_runs: bool = True,
    max_workers: int = 5,
    diversity_every: int = 20,
    progress_every: int = 100,
) -> dict:
    instance_path = Path(instance_path)
    data = load_instance(instance_path)
    base_decoder = V5ScheduleDecoder(data)

    if not (0.05 <= offspring_fraction <= 1.0):
        raise ValueError("offspring_fraction must be in [0.05, 1.0]")
    if crossover_type not in {"ox", "pmx", "pbx", "multi"}:
        raise ValueError("crossover_type must be one of ox,pmx,pbx,multi")
    if len(crossover_weights) != 3 or sum(crossover_weights) <= 0:
        raise ValueError("crossover_weights must have 3 non-negative values with positive sum")

    seed_t0 = time.perf_counter()
    refined_seed = None
    seed_info = None
    if use_refined_seed:
        refined_seed, seed_info = build_insertion_refined_seed(
            base_decoder,
            decode_budget=seed_decode_budget,
            positions_per_job=seed_positions_per_job,
        )
    seed_runtime = time.perf_counter() - seed_t0
    if refined_seed is not None:
        print(
            f"[V5 refined seed] Cmax={seed_info['cmax_min']:.3f} | "
            f"new_decodes={seed_info['new_decodes']} | time={seed_runtime:.2f}s",
            flush=True,
        )

    seeds = [seed_base + i * 1009 for i in range(runs)]
    if max_workers <= 0:
        cpu = os.cpu_count() or 2
        max_workers = min(runs, max(1, cpu - 1))
    else:
        max_workers = max(1, min(runs, int(max_workers)))
    use_parallel = bool(parallel_runs and runs > 1 and max_workers > 1)

    print(
        f"[Improved GA V5] {base_decoder.name} | n={len(base_decoder.jobs)} | runs={runs} | "
        f"mode={'PARALLEL' if use_parallel else 'SEQUENTIAL'} | workers={max_workers if use_parallel else 1} | "
        f"pop={population} | epochs={generations} | offspring_fraction={offspring_fraction:.2f} | "
        f"VNS={'ON' if use_vns else 'OFF'} | objective=MIN_CMAX_FINAL_2C",
        flush=True,
    )

    total_start = time.perf_counter()
    run_results = []
    common = dict(
        population=population,
        generations=generations,
        crossover_rate=crossover_rate,
        crossover_type=crossover_type,
        crossover_weights=crossover_weights,
        mutation_rate=mutation_rate,
        mutation_max=mutation_max,
        elite=elite,
        tournament_k=tournament_k,
        no_improve=no_improve,
        injection_fraction=injection_fraction,
        diversity_threshold=diversity_threshold,
        restart_after=restart_after,
        restart_fraction=restart_fraction,
        offspring_fraction=offspring_fraction,
        use_vns=use_vns,
        vns_stagnation_trigger=vns_stagnation_trigger,
        vns_frequency=vns_frequency,
        vns_decode_budget=vns_decode_budget,
        vns_rounds=vns_rounds,
        collect_convergence=collect_convergence,
        convergence_every=convergence_every,
        diversity_every=diversity_every,
        progress_every=progress_every,
    )

    if use_parallel:
        payloads = []
        for i, seed in enumerate(seeds, 1):
            args = dict(common)
            args.update(seed=seed, progress_label=f"Improved GA V5 run {i}/{runs}")
            payloads.append({"instance_path": str(instance_path), "refined_seed": refined_seed, "ga_args": args})
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_worker_run, p) for p in payloads]
            for fut in as_completed(futures):
                run_results.append(fut.result())
    else:
        for i, seed in enumerate(seeds, 1):
            args = dict(common)
            args.update(seed=seed, progress_label=f"Improved GA V5 run {i}/{runs}")
            run_results.append(run_one_improved_ga(base_decoder, refined_seed=refined_seed, **args))

    total_runtime = time.perf_counter() - total_start + seed_runtime
    run_results.sort(key=lambda r: r["seed"])
    best = min(run_results, key=lambda r: fitness_key(r["best_metrics"]))
    best_metrics, schedule = base_decoder.decode(best["best_chromosome"], keep_schedule=True, validate_chromosome=True)

    return {
        "instance_name": base_decoder.name,
        "size": len(base_decoder.jobs),
        "schema_version": data["metadata"]["schema_version"],
        "algorithm": "Improved Permutation GA V5 + insertion-refined seed + multi crossover + budgeted plateau VNS",
        "objective": "MIN_CMAX_FINAL_2C",
        "population": int(population),
        "generations": int(generations),
        "runs": int(runs),
        "parallel_runs": bool(use_parallel),
        "max_workers": int(max_workers if use_parallel else 1),
        "offspring_fraction": float(offspring_fraction),
        "seed_base": int(seed_base),
        "crossover_rate": float(crossover_rate),
        "crossover_type": crossover_type,
        "crossover_weights_ox_pmx_pbx": [float(x) for x in crossover_weights],
        "mutation_rate": float(mutation_rate),
        "mutation_max": float(mutation_max),
        "elite": int(elite),
        "tournament_k": int(tournament_k),
        "no_improve": int(no_improve),
        "diversity_threshold": float(diversity_threshold),
        "restart_after": int(restart_after),
        "restart_fraction": float(restart_fraction),
        "use_refined_seed": bool(use_refined_seed),
        "refined_seed_runtime_sec": float(seed_runtime),
        "refined_seed_info": seed_info,
        "use_vns": bool(use_vns),
        "vns_stagnation_trigger": int(vns_stagnation_trigger),
        "vns_frequency": int(vns_frequency),
        "vns_decode_budget": int(vns_decode_budget),
        "vns_rounds": int(vns_rounds),
        "best_makespan_min": float(best_metrics["makespan_min"]),
        "best_total_tardiness_min": float(best_metrics["total_tardiness_min"]),
        "best_total_flow_time_min": float(best_metrics["total_flow_time_min"]),
        "best_chromosome": best["best_chromosome"],
        "best_seed": int(best["seed"]),
        "best_generation": int(best["best_generation"]),
        "total_runtime_sec": float(total_runtime),
        "mean_run_runtime_sec": float(sum(r["runtime_sec"] for r in run_results) / len(run_results)),
        "run_summaries": [
            {
                "run": i + 1,
                "seed": r["seed"],
                "makespan_min": r["best_metrics"]["makespan_min"],
                "runtime_sec": r["runtime_sec"],
                "best_generation": r["best_generation"],
                "generations_completed": r["generations_completed"],
                "unique_evaluations": r["unique_evaluations"],
                "restart_count": r["restart_count"],
                "low_diversity_injection_count": r["low_diversity_injection_count"],
                "final_diversity": r["final_diversity"],
                "final_unique_ratio": r["final_unique_ratio"],
                "vns_calls": r["vns_calls"],
                "vns_new_decodes": r["vns_new_decodes"],
                "vns_improvements": r["vns_improvements"],
                "ox_crossovers": r["crossover_usage"]["ox"],
                "pmx_crossovers": r["crossover_usage"]["pmx"],
                "pbx_crossovers": r["crossover_usage"]["pbx"],
            }
            for i, r in enumerate(run_results)
        ],
        "convergence_by_run": [
            {"run": i + 1, "seed": r["seed"], "trace": r["convergence"]}
            for i, r in enumerate(run_results)
        ] if collect_convergence else None,
        "schedule": schedule,
    }


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
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def save_result_files(result: Mapping, instance_path: Path, output_dir: Path) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = instance_path.stem
    json_path = output_dir / f"{stem}__improved_ga_v5_result.json"
    summary_csv = output_dir / f"{stem}__improved_ga_v5_run_summary.csv"
    convergence_csv = output_dir / f"{stem}__improved_ga_v5_convergence.csv"
    schedule_csv = output_dir / f"{stem}__improved_ga_v5_schedule.csv"
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
    vals = tuple(float(x.strip()) for x in text.split(",") if x.strip())
    if len(vals) != 3 or any(x < 0 for x in vals) or sum(vals) <= 0:
        raise argparse.ArgumentTypeError("Expected non-negative OX,PMX,PBX weights, e.g. 1,1,1")
    return vals  # type: ignore[return-value]


def main() -> None:
    ap = argparse.ArgumentParser(description="AL13 V5 Improved GA with decode-budgeted local search")
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--population", type=int, default=60)
    ap.add_argument("--generations", "--epochs", dest="generations", type=int, default=1200)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed-base", type=int, default=42)
    ap.add_argument("--crossover-rate", type=float, default=0.90)
    ap.add_argument("--crossover-type", choices=["ox", "pmx", "pbx", "multi"], default="multi")
    ap.add_argument("--crossover-weights", type=_parse_weights, default=(1.0, 1.0, 1.0))
    ap.add_argument("--mutation-rate", type=float, default=0.15)
    ap.add_argument("--mutation-max", type=float, default=0.55)
    ap.add_argument("--elite", type=int, default=2)
    ap.add_argument("--tournament-k", type=int, default=3)
    ap.add_argument("--no-improve", type=int, default=0, help="0 disables early stop")
    ap.add_argument("--injection-fraction", type=float, default=0.25)
    ap.add_argument("--diversity-threshold", type=float, default=0.25)
    ap.add_argument("--restart-after", type=int, default=300)
    ap.add_argument("--restart-fraction", type=float, default=0.50)
    ap.add_argument("--offspring-fraction", type=float, default=0.50,
                    help="Fraction of population replaced each epoch; 0.5 roughly halves new GA decodes/epoch")
    ap.add_argument("--use-refined-seed", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--seed-decode-budget", type=int, default=60)
    ap.add_argument("--seed-positions-per-job", type=int, default=4)
    ap.add_argument("--use-vns", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--vns-stagnation-trigger", type=int, default=120)
    ap.add_argument("--vns-frequency", type=int, default=80)
    ap.add_argument("--vns-decode-budget", type=int, default=24)
    ap.add_argument("--vns-rounds", type=int, default=2)
    ap.add_argument("--convergence-every", type=int, default=10)
    ap.add_argument("--diversity-every", type=int, default=20)
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--sequential-runs", action="store_true")
    ap.add_argument("--save-convergence", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()

    result = solve_instance(
        args.instance,
        population=args.population,
        generations=args.generations,
        runs=args.runs,
        seed_base=args.seed_base,
        crossover_rate=args.crossover_rate,
        crossover_type=args.crossover_type,
        crossover_weights=args.crossover_weights,
        mutation_rate=args.mutation_rate,
        mutation_max=args.mutation_max,
        elite=args.elite,
        tournament_k=args.tournament_k,
        no_improve=args.no_improve,
        injection_fraction=args.injection_fraction,
        diversity_threshold=args.diversity_threshold,
        restart_after=args.restart_after,
        restart_fraction=args.restart_fraction,
        offspring_fraction=args.offspring_fraction,
        use_refined_seed=args.use_refined_seed,
        seed_decode_budget=args.seed_decode_budget,
        seed_positions_per_job=args.seed_positions_per_job,
        use_vns=args.use_vns,
        vns_stagnation_trigger=args.vns_stagnation_trigger,
        vns_frequency=args.vns_frequency,
        vns_decode_budget=args.vns_decode_budget,
        vns_rounds=args.vns_rounds,
        collect_convergence=args.save_convergence,
        convergence_every=args.convergence_every,
        parallel_runs=not args.sequential_runs,
        max_workers=args.workers,
        diversity_every=args.diversity_every,
        progress_every=args.progress_every,
    )
    outdir = args.output_dir or args.instance.parent
    files = save_result_files(result, args.instance, outdir)
    print("=" * 92)
    print(
        f"{result['instance_name']} | Improved GA V5 Cmax={result['best_makespan_min']:.3f} min | "
        f"wall={result['total_runtime_sec']:.2f}s | best_gen={result['best_generation']}"
    )
    for k, v in files.items():
        print(f"{k:16}: {v}")


if __name__ == "__main__":
    main()
