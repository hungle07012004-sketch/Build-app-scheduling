#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Improved GA (GA + NEH + Elite VNS) for ONE frozen AL13 benchmark instance.

Thesis-locked components intentionally preserved
-------------------------------------------------
* Input is one frozen benchmark JSON instance.
* Scheduling feasibility is delegated to benchmark_common.FrozenInstanceDecoder.
* FrozenInstanceDecoder.decode() is NOT modified.
* Primary objective is makespan; total tardiness and total flow time are
  strict lexicographic tie-breakers using a 4-decimal thesis fitness key.
* Independent GA runs may execute in parallel; VNS itself is single-process.

Paper-inspired improvements adapted to the AL13 chromosome
-----------------------------------------------------------
The reference GA-VNS paper uses an NEH-based initialisation strategy, multiple
permutation crossover operators and variable-neighbourhood local search.  Its
original chromosome also contains batch/sublot layers that do not exist in the
frozen AL13 thesis architecture.  Therefore this file transfers only the
operators that are meaningful for AL13's job-permutation chromosome:

1) NEH-based initialisation evaluated by the frozen AL13 decoder.
2) Elite VNS with Insert -> Swap -> Reverse neighbourhoods.
3) Multi-crossover: OX, PMX and PBX.

The genetic representation uses integer job indices in the hot evolutionary
loop.  Cache keys are bytes when n <= 256, reducing hashing/allocation cost.
A chromosome is converted to Job_ID strings only when calling the unchanged
FrozenInstanceDecoder.

Python: 3.10+
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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from benchmark_common import FrozenInstanceDecoder, OP_SEQUENCE, load_instance

EPS = 1e-9


# ---------------------------------------------------------------------------
# Integer-index chromosome codec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobIndexCodec:
    """Translate integer-index chromosomes to the decoder's frozen Job_IDs."""

    job_ids: Tuple[str, ...]

    @classmethod
    def from_decoder(cls, decoder: FrozenInstanceDecoder) -> "JobIndexCodec":
        return cls(tuple(decoder.job_ids))

    @property
    def n(self) -> int:
        return len(self.job_ids)

    def to_job_ids(self, chromosome: Sequence[int]) -> List[str]:
        ids = self.job_ids
        return [ids[i] for i in chromosome]

    def key(self, chromosome: Sequence[int]):
        # AL13 thesis instances are <= 50 jobs, so bytes is the fast path.
        if self.n <= 256:
            return bytes(chromosome)
        return tuple(chromosome)


# ---------------------------------------------------------------------------
# Objective/evaluation helpers
# ---------------------------------------------------------------------------


def fitness_key(metrics: Mapping[str, float]) -> Tuple[float, float, float]:
    """Thesis-locked lexicographic objective with deterministic 4-decimal ties.

    Priority is STRICTLY:
      1) makespan Cmax
      2) total tardiness
      3) total flow time

    Flow time can therefore never compensate for worse tardiness when Cmax ties.
    """
    return (
        round(float(metrics["makespan_min"]), 4),
        round(float(metrics.get("total_tardiness_min", 0.0)), 4),
        round(float(metrics.get("total_flow_time_min", 0.0)), 4),
    )


def evaluate_fitness(
    chromosome: Sequence[int],
    decoder: FrozenInstanceDecoder,
    codec: JobIndexCodec,
    cache: Optional[MutableMapping] = None,
) -> Tuple[Tuple[float, float, float], Mapping[str, float]]:
    """Evaluate one integer chromosome using the unchanged frozen decoder."""
    ck = codec.key(chromosome)
    if cache is not None:
        metrics = cache.get(ck)
        if metrics is not None:
            return fitness_key(metrics), metrics

    job_sequence = codec.to_job_ids(chromosome)
    metrics, _ = decoder.decode(
        job_sequence,
        keep_schedule=False,
        validate_chromosome=False,
    )
    if cache is not None:
        cache[ck] = metrics
    return fitness_key(metrics), metrics


# Backward-compatible internal name used by NEH and older helper code.
def _evaluate_index_chromosome(
    chromosome: Sequence[int],
    decoder: FrozenInstanceDecoder,
    codec: JobIndexCodec,
    cache: Optional[MutableMapping] = None,
) -> Tuple[Tuple[float, float, float], Mapping[str, float]]:
    return evaluate_fitness(chromosome, decoder, codec, cache)

def evaluate_population(
    pop: Sequence[Sequence[int]],
    decoder: FrozenInstanceDecoder,
    codec: JobIndexCodec,
    cache: MutableMapping,
):
    keys: List[Tuple[float, float, float]] = []
    metrics: List[Mapping[str, float]] = []
    for ch in pop:
        key, m = _evaluate_index_chromosome(ch, decoder, codec, cache)
        keys.append(key)
        metrics.append(m)
    return keys, metrics


def _cmax(metrics: Mapping[str, float]) -> float:
    return float(metrics["makespan_min"])


# ---------------------------------------------------------------------------
# Heuristic workload and NEH-based initialisation
# ---------------------------------------------------------------------------


def average_stage_workload(decoder: FrozenInstanceDecoder, job_id: str) -> float:
    """Sum the mean eligible-machine processing time over the 10 AL13 stages."""
    total = 0.0
    for op in OP_SEQUENCE:
        opts = decoder.options[(job_id, op)]
        total += sum(p for _station, p in opts) / len(opts)
    return total


def minimum_stage_workload(decoder: FrozenInstanceDecoder, job_id: str) -> float:
    """SPT score retained from the baseline GA: sum of fastest eligible times."""
    return sum(min(p for _station, p in decoder.options[(job_id, op)]) for op in OP_SEQUENCE)


def generate_neh_individual(
    instance: Mapping,
    decoder: FrozenInstanceDecoder,
    codec: Optional[JobIndexCodec] = None,
) -> List[int]:
    """Generate one exhaustive-insertion NEH chromosome for frozen AL13.

    This implements the AL13 adaptation requested for the thesis:
    1. Sort jobs descending by the sum of *average* eligible-machine processing
       times across the 10 operations.
    2. Evaluate both orders of the first two jobs using the unchanged decoder.
    3. Insert every next job into every possible position and keep the
       lexicographically best partial sequence (Cmax, tardiness, flow time).

    FrozenInstanceDecoder normally validates that a chromosome contains all
    jobs.  NEH necessarily evaluates partial sequences, therefore those calls
    explicitly set validate_chromosome=False; decode logic itself is untouched.

    ``instance`` is accepted to keep the public function aligned with the
    thesis specification and to make future instance-specific extensions easy.
    """
    del instance  # frozen data are already materialised inside decoder
    codec = codec or JobIndexCodec.from_decoder(decoder)
    n = codec.n
    if n == 0:
        return []
    if n == 1:
        return [0]

    workloads = [average_stage_workload(decoder, codec.job_ids[i]) for i in range(n)]
    ordered = sorted(
        range(n),
        key=lambda i: (
            -workloads[i],
            decoder.release[codec.job_ids[i]],
            decoder.due[codec.job_ids[i]],
            codec.job_ids[i],
        ),
    )

    partial_cache: Dict[object, Mapping[str, float]] = {}

    a = [ordered[0], ordered[1]]
    b = [ordered[1], ordered[0]]
    ka, _ = _evaluate_index_chromosome(a, decoder, codec, partial_cache)
    kb, _ = _evaluate_index_chromosome(b, decoder, codec, partial_cache)
    partial = a if ka <= kb else b

    for idx in ordered[2:]:
        best_candidate: Optional[List[int]] = None
        best_key = (math.inf, math.inf, math.inf)
        # Exhaustive NEH insertion: all k positions are evaluated.
        for pos in range(len(partial) + 1):
            cand = partial[:]
            cand.insert(pos, idx)
            key, _metrics = _evaluate_index_chromosome(cand, decoder, codec, partial_cache)
            if key < best_key:
                best_key = key
                best_candidate = cand
        partial = best_candidate  # type: ignore[assignment]

    return partial


def _create_initial_population(
    data: Mapping,
    decoder: FrozenInstanceDecoder,
    codec: JobIndexCodec,
    rng: random.Random,
    pop_size: int,
    use_neh: bool,
    precomputed_neh: Optional[Sequence[int]] = None,
) -> Tuple[List[List[int]], Dict[str, object]]:
    """FIFO + SPT + EDD + one NEH + random permutations."""
    n = codec.n
    if n == 0:
        raise ValueError("Frozen instance contains no jobs")

    max_unique = math.factorial(n) if n <= 10 else None
    effective = min(pop_size, max_unique) if max_unique is not None else pop_size
    effective = max(2 if n > 1 else 1, effective)

    ids = codec.job_ids
    base = list(range(n))
    fifo = sorted(base, key=lambda i: (decoder.release[ids[i]], decoder.priority[ids[i]], ids[i]))
    spt = sorted(
        base,
        key=lambda i: (
            minimum_stage_workload(decoder, ids[i]),
            decoder.release[ids[i]],
            ids[i],
        ),
    )
    edd = sorted(
        base,
        key=lambda i: (decoder.due[ids[i]], decoder.release[ids[i]], decoder.priority[ids[i]], ids[i]),
    )

    pop: List[List[int]] = []
    seen = set()
    sources: List[str] = []

    def add(ch: Sequence[int], source: str) -> None:
        if len(pop) >= effective:
            return
        key = codec.key(ch)
        if key in seen:
            return
        seen.add(key)
        pop.append(list(ch))
        sources.append(source)

    add(fifo, "FIFO")
    add(spt, "SPT")
    add(edd, "EDD")

    neh = None
    if use_neh:
        neh = list(precomputed_neh) if precomputed_neh is not None else generate_neh_individual(data, decoder, codec)
        add(neh, "NEH")

    attempts = 0
    max_attempts = max(1000, effective * 300)
    while len(pop) < effective and attempts < max_attempts:
        attempts += 1
        ch = base[:]
        rng.shuffle(ch)
        add(ch, "RANDOM")

    if len(pop) < effective and n <= 8:
        perms = list(itertools.permutations(base))
        rng.shuffle(perms)
        for p in perms:
            add(p, "ENUMERATED")
            if len(pop) >= effective:
                break

    if len(pop) != effective:
        raise RuntimeError(f"Could create only {len(pop)} unique chromosomes, target={effective}")

    return pop, {
        "effective_population": effective,
        "seed_sources": sources,
        "neh_included": bool(use_neh and neh is not None),
        "neh_chromosome_idx": neh,
        "neh_chromosome": codec.to_job_ids(neh) if neh is not None else None,
    }


# ---------------------------------------------------------------------------
# Permutation crossover operators: OX, PMX, PBX
# ---------------------------------------------------------------------------


def ox_crossover(p1: Sequence[int], p2: Sequence[int], rng: random.Random):
    n = len(p1)
    if n < 2:
        return list(p1), list(p2)
    a, b = sorted(rng.sample(range(n), 2))

    def child(pa: Sequence[int], pb: Sequence[int]) -> List[int]:
        out: List[Optional[int]] = [None] * n
        segment = pa[a : b + 1]
        out[a : b + 1] = segment
        used = set(segment)
        fill = (g for g in pb if g not in used)
        for pos in list(range(b + 1, n)) + list(range(0, a)):
            out[pos] = next(fill)
        return [int(x) for x in out]

    return child(p1, p2), child(p2, p1)


def pmx_crossover(p1: Sequence[int], p2: Sequence[int], rng: random.Random):
    """Partially Mapped Crossover (PMX), permutation-safe for integer genes."""
    n = len(p1)
    if n < 2:
        return list(p1), list(p2)
    a, b = sorted(rng.sample(range(n), 2))

    def child(pa: Sequence[int], pb: Sequence[int]) -> List[int]:
        out = [-1] * n
        out[a : b + 1] = pa[a : b + 1]

        # Genes are 0..n-1, so arrays are faster than dict mappings.
        pos_in_pb = [0] * n
        in_segment = [False] * n
        for pos, gene in enumerate(pb):
            pos_in_pb[gene] = pos
        for gene in pa[a : b + 1]:
            in_segment[gene] = True

        for i in range(a, b + 1):
            gene = pb[i]
            if in_segment[gene]:
                continue
            pos = i
            while a <= pos <= b:
                mapped_gene = pa[pos]
                pos = pos_in_pb[mapped_gene]
            out[pos] = gene

        for i in range(n):
            if out[i] < 0:
                out[i] = pb[i]
        return out

    return child(p1, p2), child(p2, p1)


def pbx_crossover(p1: Sequence[int], p2: Sequence[int], rng: random.Random):
    """Position-Based Crossover (PBX): preserve selected positions, fill in order."""
    n = len(p1)
    if n < 2:
        return list(p1), list(p2)

    k = max(1, n // 2)
    positions = rng.sample(range(n), k)

    def child(pa: Sequence[int], pb: Sequence[int]) -> List[int]:
        out = [-1] * n
        used = [False] * n
        for pos in positions:
            gene = pa[pos]
            out[pos] = gene
            used[gene] = True

        fill_iter = (g for g in pb if not used[g])
        for i in range(n):
            if out[i] < 0:
                out[i] = next(fill_iter)
        return out

    return child(p1, p2), child(p2, p1)


def _weighted_operator_choice(
    rng: random.Random,
    operator_names: Sequence[str],
    weights: Sequence[float],
) -> str:
    total = sum(weights)
    if total <= 0:
        raise ValueError("Crossover weights must sum to a positive value")
    x = rng.random() * total
    c = 0.0
    for name, w in zip(operator_names, weights):
        c += w
        if x <= c:
            return name
    return operator_names[-1]


def apply_crossover(
    p1: Sequence[int],
    p2: Sequence[int],
    rng: random.Random,
    crossover_type: str = "multi",
    crossover_weights: Sequence[float] = (1.0, 1.0, 1.0),
):
    if crossover_type == "multi":
        op = _weighted_operator_choice(rng, ("ox", "pmx", "pbx"), crossover_weights)
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


# ---------------------------------------------------------------------------
# Mutations retained from baseline GA
# ---------------------------------------------------------------------------


def swap_mutation(ch: Sequence[int], rng: random.Random) -> List[int]:
    out = list(ch)
    if len(out) >= 2:
        a, b = rng.sample(range(len(out)), 2)
        out[a], out[b] = out[b], out[a]
    return out


def insert_mutation(ch: Sequence[int], rng: random.Random) -> List[int]:
    out = list(ch)
    if len(out) >= 2:
        a, b = rng.sample(range(len(out)), 2)
        gene = out.pop(a)
        out.insert(b, gene)
    return out


def reverse_mutation(ch: Sequence[int], rng: random.Random) -> List[int]:
    out = list(ch)
    if len(out) >= 2:
        a, b = sorted(rng.sample(range(len(out)), 2))
        out[a : b + 1] = reversed(out[a : b + 1])
    return out


def mixed_mutation(ch: Sequence[int], rng: random.Random) -> List[int]:
    r = rng.random()
    if r < 0.50:
        return insert_mutation(ch, rng)
    if r < 0.80:
        return swap_mutation(ch, rng)
    return reverse_mutation(ch, rng)


# ---------------------------------------------------------------------------
# Elite VNS: N1 Insert -> N2 Swap -> N3 Reverse -> N4 Critical Block
# ---------------------------------------------------------------------------


def _critical_job_genes(
    chromosome: Sequence[int],
    decoder: FrozenInstanceDecoder,
    codec: JobIndexCodec,
    top_k: int = 8,
) -> Tuple[List[int], dict]:
    """Return an AL13-specific proxy set for jobs on/near the critical tail.

    This is deliberately a *critical-job proxy*, not an exact CPM critical-path
    reconstruction.  It uses the frozen decoder schedule and prioritises:
      * latest completions at final operation 2C; and
      * jobs using the most loaded OIL/SEAL station, with late completion first.

    One keep_schedule decode is performed per refresh of the critical set.
    """
    ids = codec.to_job_ids(chromosome)
    _metrics, schedule = decoder.decode(
        ids,
        keep_schedule=True,
        validate_chromosome=False,
    )
    schedule = schedule or []

    final_rows = [r for r in schedule if r.get("Operation_Code") == "2C"]
    final_rows.sort(key=lambda r: float(r.get("Completion_Min", 0.0)), reverse=True)

    os_rows = [r for r in schedule if r.get("Operation_Code") in {"OIL", "SEAL"}]
    station_load: Dict[str, float] = {}
    station_latest: Dict[str, float] = {}
    for r in os_rows:
        st = str(r.get("Station_ID", ""))
        station_load[st] = station_load.get(st, 0.0) + float(r.get("Processing_Min", 0.0))
        station_latest[st] = max(station_latest.get(st, 0.0), float(r.get("Completion_Min", 0.0)))

    bottleneck_station = None
    if station_load:
        bottleneck_station = max(
            station_load,
            key=lambda st: (station_load[st], station_latest.get(st, 0.0), st),
        )

    bottleneck_rows = [r for r in os_rows if str(r.get("Station_ID", "")) == bottleneck_station]
    bottleneck_rows.sort(key=lambda r: float(r.get("Completion_Min", 0.0)), reverse=True)

    selected_ids: List[str] = []
    seen = set()
    quota_final = max(2, top_k // 2)
    for r in final_rows[:quota_final] + bottleneck_rows:
        jid = str(r.get("Job_ID"))
        if jid and jid not in seen:
            seen.add(jid)
            selected_ids.append(jid)
        if len(selected_ids) >= max(2, top_k):
            break

    # Fallback if OIL/SEAL rows are sparse for any reason.
    if len(selected_ids) < min(top_k, len(chromosome)):
        for r in final_rows:
            jid = str(r.get("Job_ID"))
            if jid and jid not in seen:
                seen.add(jid)
                selected_ids.append(jid)
            if len(selected_ids) >= min(top_k, len(chromosome)):
                break

    id_to_gene = {jid: i for i, jid in enumerate(codec.job_ids)}
    genes = [id_to_gene[jid] for jid in selected_ids if jid in id_to_gene]
    return genes, {
        "bottleneck_station": bottleneck_station,
        "critical_job_ids": selected_ids,
    }


def _critical_block_neighbor(
    current: Sequence[int],
    critical_genes: Sequence[int],
    rng: random.Random,
) -> List[int]:
    """N4: targeted critical-block insertion / critical-job swap."""
    cand = list(current)
    n = len(cand)
    if n < 2:
        return cand

    critical_set = set(critical_genes)
    critical_positions = [i for i, g in enumerate(cand) if g in critical_set]
    if not critical_positions:
        return insert_mutation(cand, rng)

    anchor = rng.choice(critical_positions)

    # 65%: move a small block containing a critical job earlier in the order.
    if rng.random() < 0.65 and n >= 3:
        block_len = rng.randint(1, min(3, n))
        lo = max(0, anchor - block_len + 1)
        start = rng.randint(lo, min(anchor, n - block_len))
        block = cand[start : start + block_len]
        del cand[start : start + block_len]
        if not cand:
            return block
        # Prefer moving the critical block earlier, but retain some exploration.
        if start > 0 and rng.random() < 0.80:
            target = rng.randrange(0, start)
        else:
            target = rng.randrange(0, len(cand) + 1)
        cand[target:target] = block
        return cand

    # 35%: swap the critical job with another position, usually an earlier one.
    if anchor > 0 and rng.random() < 0.80:
        other = rng.randrange(0, anchor)
    else:
        choices = [i for i in range(n) if i != anchor]
        other = rng.choice(choices)
    cand[anchor], cand[other] = cand[other], cand[anchor]
    return cand


def _vns_neighbor(
    current: Sequence[int],
    neighborhood: int,
    rng: random.Random,
    critical_genes: Sequence[int] = (),
) -> List[int]:
    n = len(current)
    cand = list(current)
    if n < 2:
        return cand

    if neighborhood == 1:  # Insert
        a, b = rng.sample(range(n), 2)
        gene = cand.pop(a)
        cand.insert(b, gene)
        return cand

    if neighborhood == 2:  # Swap
        a, b = rng.sample(range(n), 2)
        cand[a], cand[b] = cand[b], cand[a]
        return cand

    if neighborhood == 3:  # Reverse a short subsequence, length 3..5
        if n < 3:
            cand.reverse()
            return cand
        seg_len = rng.randint(3, min(5, n))
        start = rng.randrange(0, n - seg_len + 1)
        cand[start : start + seg_len] = reversed(cand[start : start + seg_len])
        return cand

    if neighborhood == 4:  # Critical block insertion / swap
        return _critical_block_neighbor(cand, critical_genes, rng)

    raise ValueError(f"Unknown VNS neighborhood: {neighborhood}")


def _shake_chromosome(
    chromosome: Sequence[int],
    rng: random.Random,
    min_block: int = 3,
    max_block: int = 5,
) -> List[int]:
    """Macro-mutation: shuffle one contiguous block of 3--5 jobs."""
    out = list(chromosome)
    n = len(out)
    if n < 2:
        return out
    lo = min(max(2, min_block), n)
    hi = min(max(lo, max_block), n)
    seg_len = rng.randint(lo, hi)
    start = rng.randrange(0, n - seg_len + 1)
    block = out[start : start + seg_len]
    original = block[:]
    rng.shuffle(block)
    if block == original and len(block) > 1:
        block = block[1:] + block[:1]
    out[start : start + seg_len] = block
    return out


def apply_elite_vns(
    elite_chromosome: Sequence[int],
    decoder: FrozenInstanceDecoder,
    max_neighborhood_steps: int = 5,
    *,
    codec: Optional[JobIndexCodec] = None,
    cache: Optional[MutableMapping] = None,
    rng: Optional[random.Random] = None,
    moves_per_neighborhood: int = 30,
    max_shakes: int = 0,
    critical_top_k: int = 8,
    progress_label: str = "VNS",
    log_improvements: bool = True,
    return_details: bool = False,
):
    """Elite VNS with four neighborhoods plus bounded shaking.

    N1 Insert -> N2 Swap -> N3 Reverse(3..5) -> N4 AL13 critical block.

    A worse shaken solution is allowed only as a *temporary search base*; the
    incumbent elite is never replaced unless the strict thesis fitness key
    improves.  This permits valley crossing while preserving elitism.
    """
    codec = codec or JobIndexCodec.from_decoder(decoder)
    rng = rng or random.Random()
    cache = cache if cache is not None else {}

    incumbent = list(elite_chromosome)
    incumbent_key, incumbent_metrics = evaluate_fitness(incumbent, decoder, codec, cache)
    search = incumbent[:]
    search_key = incumbent_key
    search_metrics = incumbent_metrics

    critical_genes, critical_meta = _critical_job_genes(incumbent, decoder, codec, critical_top_k)
    critical_schedule_evaluations = 1

    neighborhood = 1
    incumbent_improvements = 0
    local_improvements = 0
    evaluations = 0
    shakes_used = 0
    shake_events: List[dict] = []
    events: List[dict] = []
    max_local_steps = max(8, max(1, max_neighborhood_steps) * 8)

    while (
        incumbent_improvements < max(0, max_neighborhood_steps)
        and local_improvements < max_local_steps
    ):
        if neighborhood <= 4:
            best_neighbor = None
            best_neighbor_key = search_key
            best_neighbor_metrics = None

            for _ in range(max(1, moves_per_neighborhood)):
                cand = _vns_neighbor(search, neighborhood, rng, critical_genes)
                key, metrics = evaluate_fitness(cand, decoder, codec, cache)
                evaluations += 1
                if key < best_neighbor_key:
                    best_neighbor = cand
                    best_neighbor_key = key
                    best_neighbor_metrics = metrics

            if best_neighbor is None:
                neighborhood += 1
                continue

            used_neighborhood = neighborhood
            search = best_neighbor
            search_key = best_neighbor_key
            search_metrics = best_neighbor_metrics  # type: ignore[assignment]
            local_improvements += 1
            neighborhood = 1

            # Only a strict lexicographic improvement may replace the elite.
            if search_key < incumbent_key:
                old_metrics = incumbent_metrics
                old_cmax = _cmax(old_metrics)
                new_cmax = _cmax(search_metrics)
                incumbent = search[:]
                incumbent_key = search_key
                incumbent_metrics = search_metrics
                incumbent_improvements += 1
                shakes_used = 0

                event = {
                    "step": incumbent_improvements,
                    "neighborhood": f"N{used_neighborhood}",
                    "old_makespan_min": old_cmax,
                    "new_makespan_min": new_cmax,
                    "old_total_tardiness_min": float(old_metrics.get("total_tardiness_min", 0.0)),
                    "new_total_tardiness_min": float(incumbent_metrics.get("total_tardiness_min", 0.0)),
                    "old_total_flow_time_min": float(old_metrics.get("total_flow_time_min", 0.0)),
                    "new_total_flow_time_min": float(incumbent_metrics.get("total_flow_time_min", 0.0)),
                }
                events.append(event)

                if log_improvements:
                    suffix = ""
                    if round(new_cmax, 4) == round(old_cmax, 4):
                        suffix = " (lexicographic tie-break improvement)"
                    print(
                        f"[{progress_label}] improved elite: "
                        f"{old_cmax:.2f} -> {new_cmax:.2f} min{suffix}",
                        flush=True,
                    )

                # Refresh N4 priorities only when the incumbent truly improves.
                critical_genes, critical_meta = _critical_job_genes(
                    incumbent, decoder, codec, critical_top_k
                )
                critical_schedule_evaluations += 1
                search = incumbent[:]
                search_key = incumbent_key
                search_metrics = incumbent_metrics
            continue

        # N1..N4 all failed.  Shake only when the caller enables plateau mode.
        if shakes_used >= max(0, max_shakes):
            break
        shakes_used += 1
        shaken = _shake_chromosome(incumbent, rng, 3, 5)
        shaken_key, shaken_metrics = evaluate_fitness(shaken, decoder, codec, cache)
        evaluations += 1
        shake_events.append(
            {
                "shake": shakes_used,
                "base_key": list(incumbent_key),
                "shaken_key": list(shaken_key),
            }
        )
        search = shaken
        search_key = shaken_key
        search_metrics = shaken_metrics
        neighborhood = 1

        # A shake may occasionally be an immediate improvement.
        if search_key < incumbent_key:
            old_metrics = incumbent_metrics
            incumbent = search[:]
            incumbent_key = search_key
            incumbent_metrics = search_metrics
            incumbent_improvements += 1
            events.append(
                {
                    "step": incumbent_improvements,
                    "neighborhood": "SHAKE",
                    "old_makespan_min": float(old_metrics["makespan_min"]),
                    "new_makespan_min": float(incumbent_metrics["makespan_min"]),
                    "old_total_tardiness_min": float(old_metrics.get("total_tardiness_min", 0.0)),
                    "new_total_tardiness_min": float(incumbent_metrics.get("total_tardiness_min", 0.0)),
                    "old_total_flow_time_min": float(old_metrics.get("total_flow_time_min", 0.0)),
                    "new_total_flow_time_min": float(incumbent_metrics.get("total_flow_time_min", 0.0)),
                }
            )
            critical_genes, critical_meta = _critical_job_genes(
                incumbent, decoder, codec, critical_top_k
            )
            critical_schedule_evaluations += 1
            search = incumbent[:]
            search_key = incumbent_key
            search_metrics = incumbent_metrics

    if return_details:
        return incumbent, dict(incumbent_metrics), {
            "accepted_improvements": incumbent_improvements,
            "local_improvements": local_improvements,
            "evaluations": evaluations,
            "shakes_used": shakes_used,
            "critical_schedule_evaluations": critical_schedule_evaluations,
            "critical_meta": critical_meta,
            "events": events,
            "shake_events": shake_events,
        }
    return incumbent


# ---------------------------------------------------------------------------
# Selection/diversity/adaptive mutation
# ---------------------------------------------------------------------------


def tournament(
    pop: Sequence[Sequence[int]],
    keys: Sequence[Tuple[float, float, float]],
    rng: random.Random,
    k: int,
) -> List[int]:
    idxs = rng.sample(range(len(pop)), min(k, len(pop)))
    return list(pop[min(idxs, key=lambda i: keys[i])])


def population_diversity(
    pop: Sequence[Sequence[int]],
    diag_rng: random.Random,
    samples: int = 64,
) -> float:
    """Sampled normalized Hamming distance across chromosomes."""
    if len(pop) < 2 or not pop[0]:
        return 0.0
    n = len(pop[0])
    total = 0.0
    count = 0
    max_pairs = len(pop) * (len(pop) - 1) // 2
    if max_pairs <= samples:
        pairs: Iterable[Tuple[int, int]] = itertools.combinations(range(len(pop)), 2)
    else:
        pairs = (tuple(diag_rng.sample(range(len(pop)), 2)) for _ in range(samples))
    for i, j in pairs:
        total += sum(a != b for a, b in zip(pop[i], pop[j])) / n
        count += 1
    return total / count if count else 0.0


def unique_ratio(pop: Sequence[Sequence[int]], codec: JobIndexCodec) -> float:
    return len({codec.key(ch) for ch in pop}) / max(1, len(pop))


def adaptive_rate(
    base: float,
    maximum: float,
    diversity: float,
    stall: int,
    stall_trigger: int,
) -> float:
    d_component = max(0.0, min(1.0, (0.35 - diversity) / 0.35))
    s_component = max(0.0, min(1.0, stall / max(1, stall_trigger)))
    pressure = max(d_component, s_component)
    return min(maximum, base + (maximum - base) * pressure)


# ---------------------------------------------------------------------------
# One Improved-GA run
# ---------------------------------------------------------------------------


def run_one_improved_ga(
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
    precomputed_neh: Optional[Sequence[int]] = None,
    collect_convergence: bool = True,
    diversity_every: int = 20,
    progress_every: int = 100,
    progress_label: str = "Improved GA",
) -> dict:
    rng = random.Random(seed)
    diag_rng = random.Random(seed ^ 0x5DEECE66D)
    # Keep local-search randomness independent from GA operator randomness.
    vns_rng = random.Random(seed ^ 0x9E3779B9)
    codec = JobIndexCodec.from_decoder(decoder)

    pop, init_info = _create_initial_population(
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
    best_key = (math.inf, math.inf, math.inf)
    stall = 0
    cmax_stall = 0
    convergence: List[dict] = []
    crossover_usage = {"ox": 0, "pmx": 0, "pbx": 0}
    vns_total_improvements = 0
    vns_total_evaluations = 0
    vns_total_shakes = 0
    vns_events: List[dict] = []
    t0 = time.perf_counter()

    last_div = population_diversity(pop, diag_rng)
    last_ur = unique_ratio(pop, codec)

    if progress_every > 0:
        print(
            f"[{progress_label}] start | seed={seed} | pop={len(pop)} | epochs={generations} | "
            f"NEH={'ON' if use_neh else 'OFF'} | VNS={'ON' if use_vns else 'OFF'} | "
            f"cross={crossover_type} | resources={'ON' if decoder.use_resources else 'SKIPPED(redundant)'}",
            flush=True,
        )

    for gen in range(generations + 1):
        previous_best_cmax = best_key[0]
        keys, metrics = evaluate_population(pop, decoder, codec, cache)
        order = sorted(range(len(pop)), key=lambda i: keys[i])

        improved_global = False
        pop_best_idx = order[0]
        if keys[pop_best_idx] < best_key:
            best_key = keys[pop_best_idx]
            best_ch = pop[pop_best_idx][:]
            best_metrics = dict(metrics[pop_best_idx])
            improved_global = True

        # Elite VNS is applied after fitness evaluation.  Once Cmax has been
        # flat for vns_stagnation_trigger generations, the move budget grows
        # and shaking is enabled.  Frequency remains bounded to avoid an
        # accidental VNS call on every generation during a long plateau.
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

            improved_elite, improved_metrics, vns_info = apply_elite_vns(
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
            new_elite_key = fitness_key(improved_metrics)
            if new_elite_key < old_elite_key:
                # Explicitly inject the improved elite back into the population
                # by replacing the current worst individual.  The old elite is
                # retained, increasing the probability that improved building
                # blocks participate in the next crossover generation.
                worst_idx = order[-1]
                pop[worst_idx] = improved_elite[:] 
                keys[worst_idx] = new_elite_key
                metrics[worst_idx] = dict(improved_metrics)
                order = sorted(range(len(pop)), key=lambda i: keys[i])

            vns_improvements_this_gen = int(vns_info["accepted_improvements"])
            vns_evals_this_gen = int(vns_info["evaluations"])
            vns_shakes_this_gen = int(vns_info.get("shakes_used", 0))
            vns_total_improvements += vns_improvements_this_gen
            vns_total_evaluations += vns_evals_this_gen
            vns_total_shakes += vns_shakes_this_gen
            for ev in vns_info["events"]:
                ev2 = dict(ev)
                ev2["generation"] = gen
                vns_events.append(ev2)

            pop_best_idx = order[0]
            if keys[pop_best_idx] < best_key:
                best_key = keys[pop_best_idx]
                best_ch = pop[pop_best_idx][:]
                best_metrics = dict(metrics[pop_best_idx])
                improved_global = True

        if improved_global:
            stall = 0
        else:
            stall += 1

        # Track the PRIMARY objective separately.  Secondary improvements must
        # not hide a long Cmax plateau from the VNS intensification logic.
        if best_key[0] < previous_best_cmax:
            cmax_stall = 0
        else:
            cmax_stall += 1

        if gen == 0 or gen % max(1, diversity_every) == 0 or gen == generations:
            last_div = population_diversity(pop, diag_rng)
            last_ur = unique_ratio(pop, codec)

        pm = adaptive_rate(
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
                f"div={last_div:.3f} | stall={stall} | cmax_stall={cmax_stall} | evals={len(cache)} | "
                f"elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
                flush=True,
            )

        if gen >= generations:
            break
        if no_improve > 0 and stall >= no_improve:
            break

        # Elitist replacement (retained from baseline).
        keep = min(max(1, elite), len(pop))
        new_pop = [pop[i][:] for i in order[:keep]]
        seen = {codec.key(ch) for ch in new_pop}

        # Controlled diversity injection retained from baseline GA.
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
            p1 = tournament(pop, keys, rng, tournament_k)
            p2 = tournament(pop, keys, rng, tournament_k)
            for _ in range(5):
                if p2 != p1:
                    break
                p2 = tournament(pop, keys, rng, tournament_k)

            if rng.random() < crossover_rate:
                c1, c2, op = apply_crossover(
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
                c1 = mixed_mutation(c1, rng)
            if rng.random() < pm:
                c2 = mixed_mutation(c2, rng)

            for child in (c1, c2):
                child_key = codec.key(child)
                if child_key not in seen:
                    seen.add(child_key)
                    new_pop.append(child)
                    if len(new_pop) >= len(pop):
                        break

        # Safety fallback: duplicates are acceptable only if uniqueness attempts
        # are exhausted; feasibility is still guaranteed by permutation-safe ops.
        while len(new_pop) < len(pop):
            ch = list(range(codec.n))
            rng.shuffle(ch)
            new_pop.append(ch)
        pop = new_pop

    runtime = time.perf_counter() - t0
    assert best_ch is not None and best_metrics is not None

    if progress_every > 0:
        print(
            f"[{progress_label}] done | best={best_metrics['makespan_min']:.3f} | "
            f"time={runtime:.2f}s | evals={len(cache)} | VNS improvements={vns_total_improvements}",
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
        "convergence": convergence,
    }


# ---------------------------------------------------------------------------
# Multiprocessing worker and multi-run solver
# ---------------------------------------------------------------------------


def _worker_run(payload: dict) -> dict:
    """Windows-safe process worker; no nested multiprocessing inside VNS."""
    data = load_instance(payload["instance_path"])
    decoder = FrozenInstanceDecoder(data)
    return run_one_improved_ga(
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
    collect_convergence: bool = True,
    parallel_runs: bool = True,
    max_workers: int = 5,
    diversity_every: int = 50,
    progress_every: int = 100,
) -> dict:
    instance_path = Path(instance_path)
    data = load_instance(instance_path)
    base_decoder = FrozenInstanceDecoder(data)
    codec = JobIndexCodec.from_decoder(base_decoder)

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

    total_start = time.perf_counter()

    # NEH is deterministic for one frozen instance.  Build it once in the parent
    # process and share the integer permutation with all independent runs.
    neh_start = time.perf_counter()
    precomputed_neh = generate_neh_individual(data, base_decoder, codec) if use_neh else None
    neh_runtime = time.perf_counter() - neh_start if use_neh else 0.0
    if use_neh:
        neh_key, neh_metrics = _evaluate_index_chromosome(
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
        f"[Improved GA] {base_decoder.name} | n={codec.n} | runs={runs} | "
        f"mode={'PARALLEL' if use_parallel else 'SEQUENTIAL'} | "
        f"workers={max_workers if use_parallel else 1} | pop={population} | epochs={generations} | "
        f"NEH={use_neh} | VNS={use_vns} | vns_freq={vns_freq} | cross={crossover_type}",
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
            p.update({"seed": seed, "progress_label": f"Improved GA run {i}/{runs}"})
            payloads.append(p)
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_worker_run, p): p for p in payloads}
            for fut in as_completed(futures):
                run_results.append(fut.result())
    else:
        for i, seed in enumerate(seeds, 1):
            rr = run_one_improved_ga(
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
                precomputed_neh=precomputed_neh,
                collect_convergence=collect_convergence,
                diversity_every=diversity_every,
                progress_every=progress_every,
                progress_label=f"Improved GA run {i}/{runs}",
            )
            run_results.append(rr)

    run_results.sort(key=lambda r: r["seed"])
    best = min(run_results, key=lambda r: fitness_key(r["best_metrics"]))
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
                "ox_crossovers": int(r["crossover_usage"]["ox"]),
                "pmx_crossovers": int(r["crossover_usage"]["pmx"]),
                "pbx_crossovers": int(r["crossover_usage"]["pbx"]),
            }
        )

    best_run_index = next(i for i, r in enumerate(run_results) if r["seed"] == best["seed"])
    convergence_by_run = [
        {
            "run": i + 1,
            "seed": int(r["seed"]),
            "trace": r["convergence"],
        }
        for i, r in enumerate(run_results)
    ] if collect_convergence else None

    return {
        "instance_name": base_decoder.name,
        "size": codec.n,
        "algorithm": "Improved GA (NEH + multi OX/PMX/PBX + elite VNS) + FrozenInstanceDecoder",
        "objective": "LEXICOGRAPHIC_MIN(Cmax, TotalTardiness, TotalFlowTime)",
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
        "diversity_every": int(diversity_every),
        "progress_every": int(progress_every),
        # Exact output names requested in the thesis prompt:
        "makespan_min": float(best_metrics["makespan_min"]),
        "total_tardiness_min": float(best_metrics["total_tardiness_min"]),
        "total_flow_time_min": float(best_metrics["total_flow_time_min"]),
        "runtime_sec": float(total_runtime),
        "convergence_trace": best["convergence"] if collect_convergence else None,
        # Compatibility aliases and richer diagnostics:
        "best_makespan_min": float(best_metrics["makespan_min"]),
        "best_total_tardiness_min": float(best_metrics["total_tardiness_min"]),
        "best_total_flow_time_min": float(best_metrics["total_flow_time_min"]),
        "best_chromosome_idx": best["best_chromosome_idx"],
        "best_chromosome": best["best_chromosome"],
        "best_seed": int(best["seed"]),
        "best_run": int(best_run_index + 1),
        "total_runtime_sec": float(total_runtime),
        "mean_run_runtime_sec": float(sum(r["runtime_sec"] for r in run_results) / len(run_results)),
        "run_summaries": run_summaries,
        "convergence_by_run": convergence_by_run,
        "best_run_vns_events": best["vns_events"],
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
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = instance_path.stem
    json_path = output_dir / f"{stem}__improved_ga_result.json"
    summary_csv = output_dir / f"{stem}__improved_ga_run_summary.csv"
    convergence_csv = output_dir / f"{stem}__improved_ga_convergence.csv"
    schedule_csv = output_dir / f"{stem}__improved_ga_schedule.csv"

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
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("Expected three comma-separated weights: OX,PMX,PBX")
    try:
        vals = tuple(float(x) for x in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Crossover weights must be numeric") from exc
    if any(x < 0 for x in vals) or sum(vals) <= 0:
        raise argparse.ArgumentTypeError("Crossover weights must be non-negative and not all zero")
    return vals  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Solve one frozen AL13 instance with Improved GA = GA + NEH + elite VNS."
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
        help="Weights used only by --crossover-type multi; default 1,1,1",
    )

    ap.add_argument("--mutation-rate", type=float, default=0.12)
    ap.add_argument("--mutation-max", type=float, default=0.45)
    ap.add_argument("--elite", type=int, default=1)
    ap.add_argument("--tournament-k", type=int, default=3)
    ap.add_argument(
        "--no-improve",
        type=int,
        default=0,
        help="0 forces all generations (recommended for final thesis comparison); positive enables early stop",
    )
    ap.add_argument("--injection-fraction", type=float, default=0.25)
    ap.add_argument("--diversity-threshold", type=float, default=0.20)

    # Explicit feature switches requested by the thesis prompt.  BooleanOptionalAction
    # provides both --use-neh/--no-use-neh and --use-vns/--no-use-vns.
    ap.add_argument("--use-neh", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--use-vns", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument(
        "--vns-freq",
        type=int,
        default=10,
        help="Run VNS every k generations; 0 disables frequency trigger",
    )
    ap.add_argument("--vns-steps", type=int, default=8)
    ap.add_argument("--vns-moves", type=int, default=30,
                    help="Base random moves tested per VNS neighborhood")
    ap.add_argument("--vns-stall-moves", type=int, default=45,
                    help="Moves per neighborhood after a Cmax plateau is detected")
    ap.add_argument("--vns-shakes", type=int, default=2,
                    help="3-5 job macro-shakes allowed per plateau VNS call")
    ap.add_argument("--vns-critical-top-k", type=int, default=8,
                    help="Number of late/bottleneck jobs prioritised by N4")
    ap.add_argument(
        "--vns-stagnation-trigger",
        type=int,
        default=150,
        help="Cmax-stall generations before deeper VNS + shaking is enabled",
    )

    ap.add_argument("--diversity-every", type=int, default=50)
    ap.add_argument("--progress-every", type=int, default=100)
    ap.add_argument("--workers", type=int, default=5, help="Parallel independent-run workers; 1=sequential")
    ap.add_argument("--sequential-runs", action="store_true")
    ap.add_argument(
        "--save-convergence",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Store best-so-far and population trace for every run",
    )
    args = ap.parse_args()

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
        collect_convergence=args.save_convergence,
        parallel_runs=not args.sequential_runs,
        max_workers=args.workers,
        diversity_every=args.diversity_every,
        progress_every=args.progress_every,
    )
    files = save_result_files(result, args.instance, output_dir)

    print("=" * 92)
    print(
        f"{result['instance_name']} | Improved GA Cmax={result['makespan_min']:.3f} min | "
        f"Tard={result['total_tardiness_min']:.3f} | Flow={result['total_flow_time_min']:.3f} | "
        f"wall={result['runtime_sec']:.2f}s"
    )
    print(f"JSON            : {files['json']}")
    print(f"Run summary CSV : {files['run_summary_csv']}")
    print(f"Convergence CSV : {files['convergence_csv']}")
    print(f"Schedule CSV    : {files['schedule_csv']}")


if __name__ == "__main__":
    main()
