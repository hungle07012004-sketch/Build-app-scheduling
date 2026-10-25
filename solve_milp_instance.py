#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact AL13 V5 CP-SAT reference solver with validated GA warm start.

The filename is retained for compatibility with the thesis project, but the
backend is OR-Tools CP-SAT, NOT a MILP backend.  The model objective and hard
constraints follow the confirmed AL13 V5 physical model used by
``benchmark_common.V5ScheduleDecoder``:

* objective: MIN_CMAX_FINAL_2C only;
* 2B auxiliary inspection -> PRE prerequisite;
* PRE -> FINAL SS_FIRST_BELT streaming and C_FINAL >= C_PRE;
* LASER/SCALE share OP_2A;
* explicit non-preemptive OIL cycles, capacity <= 6 belts/cycle, with OP_2A on
  load prefix and OP_PACK on unload suffix;
* SEAL/VISUAL share OP_PACK;
* exact 2C batches of 10; Q mod 10 = 5 remainders must pair 5+5 with the same
  Batch_Key; no partial batches;
* sequence-dependent Product_Code setup before successor PRE; positive setup
  simultaneously blocks WS_PRE_JIG + OP_PRE + OP_2A + OP_PACK and can start
  only after the predecessor job clears its final 2C batch (clean pipeline);
* resource calendars, breaks, and explicit downtime are hard non-preemptive
  availability constraints;
* all unary workstations/workers use NoOverlap.

Warm-start support
------------------
``--ga-result`` accepts a GA/SS-Micro-GA JSON result containing
``best_chromosome``.  The chromosome is decoded again by the validated V5
reference decoder.  Before it is used, this script independently validates the
resulting schedule against the same physical rules (calendar, resources,
precedence, setup, OIL, and exact 2C batching).  Only a validated schedule may:

1) provide CP-SAT hints; and
2) provide a safe objective upper bound Cmax <= GA_Cmax.

This does NOT change the objective or feasible set.  It only gives the exact
solver a known feasible incumbent and removes objective values that are already
known to be worse than that incumbent.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from benchmark_common import EPS, V5ScheduleDecoder, load_instance

try:
    from ortools.sat.python import cp_model
except Exception as _ORTOOLS_IMPORT_ERROR:  # pragma: no cover
    cp_model = None

SCALE = 1000  # 0.001 minute = 0.06 second; V5 values are represented at <= 3 decimals.
VALIDATION_TOL = 2.5 / SCALE  # allow at most 0.0025 min around scaled rounding.


def I(x: float) -> int:
    return int(round(float(x) * SCALE))


def F(x: int | float) -> float:
    return float(x) / SCALE


def _merged_windows(rows: Sequence[Mapping], horizon: int) -> List[Tuple[int, int]]:
    arr = sorted((max(0, I(r["start_min"])), min(horizon, I(r["end_min"]))) for r in rows)
    out: List[List[int]] = []
    for s, e in arr:
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]


def _blocked_complement(rows: Sequence[Mapping], horizon: int) -> List[Tuple[int, int]]:
    windows = _merged_windows(rows, horizon)
    blocks: List[Tuple[int, int]] = []
    cursor = 0
    for s, e in windows:
        if s > cursor:
            blocks.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < horizon:
        blocks.append((cursor, horizon))
    return [(s, e) for s, e in blocks if e > s]


def _fits_one_window(dec: V5ScheduleDecoder, rid: str, start: float, end: float, tol: float) -> bool:
    for w in dec.windows[rid]:
        if start + tol >= w.start and end <= w.end + tol:
            return True
    return False


def _assert_close(a: float, b: float, label: str, tol: float) -> None:
    if abs(float(a) - float(b)) > tol:
        raise ValueError(f"warm-start validation failed: {label}: {a} != {b}")


def _validate_warm_schedule(
    dec: V5ScheduleDecoder,
    chromosome: Sequence[str],
    metrics: Mapping[str, float],
    rows: Sequence[Mapping],
    tol: float = VALIDATION_TOL,
) -> dict:
    """Independent structural validator for a decoder-produced GA schedule.

    The purpose is to prevent a mismatched/invalid heuristic result from being
    injected into the exact model as an upper bound.
    """
    if len(chromosome) != len(dec.jobs) or set(chromosome) != dec.job_set:
        raise ValueError("warm-start validation failed: best_chromosome is not a full job permutation")

    job_ops: Dict[Tuple[str, str], Mapping] = {}
    oils: Dict[str, Mapping] = {}
    setups: Dict[Tuple[str, str], Mapping] = {}
    batches: List[Mapping] = []
    resource_use: MutableMapping[str, List[Tuple[float, float, str]]] = defaultdict(list)

    for r in rows:
        typ = str(r.get("Event_Type", ""))
        if typ == "JOB_OP":
            j, code = str(r["Job_ID"]), str(r["Operation_Code"])
            key = (j, code)
            if key in job_ops:
                raise ValueError(f"warm-start validation failed: duplicate operation {key}")
            job_ops[key] = r
            s, e = float(r["Start_Min"]), float(r["Completion_Min"])
            _assert_close(e - s, dec.proc[key], f"duration {j}/{code}", tol)
            for rid in dec.resource_by_op[code]:
                resource_use[rid].append((s, e, f"{j}:{code}"))
        elif typ == "OIL_CYCLE":
            cid = str(r["Oil_Cycle_ID"])
            if cid in oils:
                raise ValueError(f"warm-start validation failed: duplicate OIL cycle {cid}")
            oils[cid] = r
            s, e = float(r["Start_Min"]), float(r["Completion_Min"])
            load, unload = float(r["W4_Load_Min"]), float(r["W5_Unload_Min"])
            resource_use["WS_OIL"].append((s, e, cid))
            resource_use["OP_2A"].append((s, s + load, cid + ":LOAD"))
            resource_use["OP_PACK"].append((e - unload, e, cid + ":UNLOAD"))
        elif typ == "SETUP":
            i, j = str(r["Predecessor_Job_ID"]), str(r["Job_ID"])
            key = (i, j)
            if key in setups:
                raise ValueError(f"warm-start validation failed: duplicate setup {i}->{j}")
            setups[key] = r
            s, e = float(r["Start_Min"]), float(r["Completion_Min"])
            d = float(dec.setup[dec.product[i]][dec.product[j]])
            _assert_close(e - s, d, f"setup duration {i}->{j}", tol)
            for rid in dec.setup_resources:
                resource_use[rid].append((s, e, f"SETUP:{i}->{j}"))
        elif typ == "2C_BATCH":
            batches.append(r)
            s, e = float(r["Start_Min"]), float(r["Completion_Min"])
            _assert_close(e - s, dec.batch_processing_min, "2C batch duration", tol)
            for rid in dec.batch_resources:
                resource_use[rid].append((s, e, str(r.get("Event_ID", r.get("Batch_ID", "2C")))))

    expected_job_ops = {(j, op) for j in dec.jobs for op in ("2B", "PRE", "FINAL", "LASER", "SCALE", "SEAL", "VISUAL")}
    if set(job_ops) != expected_job_ops:
        missing = sorted(expected_job_ops - set(job_ops))[:10]
        extra = sorted(set(job_ops) - expected_job_ops)[:10]
        raise ValueError(f"warm-start validation failed: JOB_OP mismatch missing={missing} extra={extra}")

    expected_oils = {str(c["oil_cycle_id"]) for j in dec.jobs for c in dec.oil_cycles[j]}
    if set(oils) != expected_oils:
        missing = sorted(expected_oils - set(oils))[:10]
        extra = sorted(set(oils) - expected_oils)[:10]
        raise ValueError(f"warm-start validation failed: OIL mismatch missing={missing} extra={extra}")

    # Calendar containment and no-overlap on every physical unary resource.
    for rid, ivs in resource_use.items():
        for s, e, label in ivs:
            if e < s - tol:
                raise ValueError(f"warm-start validation failed: negative interval {rid}/{label}")
            if not _fits_one_window(dec, rid, s, e, tol):
                raise ValueError(f"warm-start validation failed: {rid}/{label} crosses calendar/downtime [{s},{e}]")
        ivs_sorted = sorted(ivs, key=lambda x: (x[0], x[1], x[2]))
        for a, b in zip(ivs_sorted, ivs_sorted[1:]):
            if b[0] < a[1] - tol:
                raise ValueError(
                    f"warm-start validation failed: resource overlap on {rid}: {a[2]} [{a[0]},{a[1]}] vs {b[2]} [{b[0]},{b[1]}]"
                )

    # Job precedence and releases.
    visual = {}
    for j in dec.jobs:
        r2b = job_ops[(j, "2B")]
        pre = job_ops[(j, "PRE")]
        fin = job_ops[(j, "FINAL")]
        las = job_ops[(j, "LASER")]
        sca = job_ops[(j, "SCALE")]
        sea = job_ops[(j, "SEAL")]
        vis = job_ops[(j, "VISUAL")]

        if float(r2b["Start_Min"]) + tol < max(dec.release[j], dec.loop[j]):
            raise ValueError(f"warm-start validation failed: 2B release violation {j}")
        lag = float(dec.prec[(j, "2B", "PRE")]["lag_min"])
        if float(pre["Start_Min"]) + tol < max(dec.release[j], dec.element[j], float(r2b["Completion_Min"]) + lag):
            raise ValueError(f"warm-start validation failed: PRE readiness violation {j}")

        stream = float(dec.prec[(j, "PRE", "FINAL")]["lag_min"])
        if float(fin["Start_Min"]) + tol < float(pre["Start_Min"]) + stream:
            raise ValueError(f"warm-start validation failed: SS_FIRST_BELT violation {j}")
        if float(fin["Completion_Min"]) + tol < float(pre["Completion_Min"]):
            raise ValueError(f"warm-start validation failed: FINAL completes before PRE {j}")

        if float(las["Start_Min"]) + tol < float(fin["Completion_Min"]) + float(dec.prec[(j, "FINAL", "LASER")]["lag_min"]):
            raise ValueError(f"warm-start validation failed: FINAL->LASER violation {j}")
        if float(sca["Start_Min"]) + tol < float(las["Completion_Min"]) + float(dec.prec[(j, "LASER", "SCALE")]["lag_min"]):
            raise ValueError(f"warm-start validation failed: LASER->SCALE violation {j}")

        cyc_rows = [oils[str(c["oil_cycle_id"])] for c in dec.oil_cycles[j]]
        prev = float(sca["Completion_Min"]) + float(dec.prec[(j, "SCALE", "OIL")]["lag_min"])
        for cdef, rr in zip(dec.oil_cycles[j], cyc_rows):
            s, e = float(rr["Start_Min"]), float(rr["Completion_Min"])
            if s + tol < prev:
                raise ValueError(f"warm-start validation failed: OIL sequence/readiness violation {cdef['oil_cycle_id']}")
            _assert_close(e - s, float(cdef["w4_load_min"]) + float(cdef["auto_dip_min"]) + float(cdef["w5_unload_min"]), f"OIL duration {cdef['oil_cycle_id']}", tol)
            prev = e
        if float(sea["Start_Min"]) + tol < prev + float(dec.prec[(j, "OIL", "SEAL")]["lag_min"]):
            raise ValueError(f"warm-start validation failed: OIL->SEAL violation {j}")
        if float(vis["Start_Min"]) + tol < float(sea["Completion_Min"]) + float(dec.prec[(j, "SEAL", "VISUAL")]["lag_min"]):
            raise ValueError(f"warm-start validation failed: SEAL->VISUAL violation {j}")
        visual[j] = float(vis["Completion_Min"])

    # Exact 2C composition/readiness and job final completion.
    full_count = defaultdict(int)
    rem_count = defaultdict(int)
    job_done = {j: visual[j] for j in dec.jobs}
    for b in batches:
        contributors = [x for x in str(b["Job_ID"]).split(";") if x]
        kind = str(b.get("Batch_Composition", ""))
        key = str(b["Batch_Key"])
        s, e = float(b["Start_Min"]), float(b["Completion_Min"])
        if kind == "FULL":
            if len(contributors) != 1:
                raise ValueError("warm-start validation failed: FULL 2C batch must have one contributor")
            j = contributors[0]
            if key != dec.batch_key[j]:
                raise ValueError(f"warm-start validation failed: FULL batch key mismatch {j}")
            full_count[j] += 1
        elif kind == "POOLED_5+5":
            if len(contributors) != 2 or contributors[0] == contributors[1]:
                raise ValueError("warm-start validation failed: pooled 5+5 must have two distinct contributors")
            a, c = contributors
            if dec.qty[a] % 10 != 5 or dec.qty[c] % 10 != 5:
                raise ValueError("warm-start validation failed: pooled contributor has no 5-belt remainder")
            if dec.batch_key[a] != dec.batch_key[c] or key != dec.batch_key[a]:
                raise ValueError("warm-start validation failed: pooled 5+5 Batch_Key mismatch")
            rem_count[a] += 1
            rem_count[c] += 1
        else:
            raise ValueError(f"warm-start validation failed: unknown 2C composition {kind!r}")
        if s + tol < max(visual[j] for j in contributors):
            raise ValueError(f"warm-start validation failed: 2C batch starts before VISUAL completion {contributors}")
        for j in contributors:
            job_done[j] = max(job_done[j], e)

    for j in dec.jobs:
        if full_count[j] != dec.qty[j] // 10:
            raise ValueError(f"warm-start validation failed: full 2C count mismatch {j}: {full_count[j]} vs {dec.qty[j] // 10}")
        expected_rem = 1 if dec.qty[j] % 10 == 5 else 0
        if rem_count[j] != expected_rem:
            raise ValueError(f"warm-start validation failed: 5+5 remainder count mismatch {j}: {rem_count[j]} vs {expected_rem}")

    # Chromosome/PRE predecessor and clean-pipeline setup semantics.
    for pos, j in enumerate(chromosome):
        if pos == 0:
            continue
        i = chromosome[pos - 1]
        d = float(dec.setup[dec.product[i]][dec.product[j]])
        pre_i = job_ops[(i, "PRE")]
        pre_j = job_ops[(j, "PRE")]
        if d <= EPS:
            if float(pre_j["Start_Min"]) + tol < float(pre_i["Completion_Min"]):
                raise ValueError(f"warm-start validation failed: zero-setup PRE order violation {i}->{j}")
            if (i, j) in setups:
                raise ValueError(f"warm-start validation failed: unexpected positive setup row {i}->{j}")
        else:
            sr = setups.get((i, j))
            if sr is None:
                raise ValueError(f"warm-start validation failed: missing setup {i}->{j}")
            ss, se = float(sr["Start_Min"]), float(sr["Completion_Min"])
            if ss + tol < max(float(pre_i["Completion_Min"]), job_done[i]):
                raise ValueError(f"warm-start validation failed: clean-pipeline setup starts too early {i}->{j}")
            if float(pre_j["Start_Min"]) + tol < se:
                raise ValueError(f"warm-start validation failed: successor PRE starts before setup ends {i}->{j}")

    expected_setup_pairs = {
        (chromosome[k - 1], chromosome[k])
        for k in range(1, len(chromosome))
        if float(dec.setup[dec.product[chromosome[k - 1]]][dec.product[chromosome[k]]]) > EPS
    }
    if set(setups) != expected_setup_pairs:
        raise ValueError(
            f"warm-start validation failed: setup pair set mismatch expected={sorted(expected_setup_pairs)} got={sorted(setups)}"
        )

    cmax = max(job_done.values()) if job_done else 0.0
    _assert_close(cmax, float(metrics["makespan_min"]), "GA decoded Cmax", max(tol, 1e-6))
    if cmax > dec.horizon + tol:
        raise ValueError("warm-start validation failed: Cmax exceeds planning horizon")

    return {
        "validated": True,
        "cmax_min": float(cmax),
        "job_completion_min": {j: float(job_done[j]) for j in dec.jobs},
        "job_ops": job_ops,
        "oils": oils,
        "setups": setups,
        "batches": batches,
    }


def _load_validated_ga_warm_start(
    ga_result_path: Path | str,
    dec: V5ScheduleDecoder,
) -> dict:
    p = Path(ga_result_path)
    obj = json.loads(p.read_text(encoding="utf-8"))
    if str(obj.get("instance_name", "")) and str(obj["instance_name"]) != dec.name:
        raise ValueError(f"GA result instance mismatch: {obj.get('instance_name')} vs {dec.name}")
    if str(obj.get("objective", "MIN_CMAX_FINAL_2C")) != "MIN_CMAX_FINAL_2C":
        raise ValueError(f"GA result objective mismatch: {obj.get('objective')}")
    chromosome = [str(x) for x in obj.get("best_chromosome", [])]
    if not chromosome:
        raise ValueError("GA result does not contain best_chromosome")
    metrics, rows = dec.decode(chromosome, keep_schedule=True, validate_chromosome=True)
    validation = _validate_warm_schedule(dec, chromosome, metrics, rows or [])
    reported = obj.get("best_makespan_min")
    if reported is not None:
        _assert_close(float(reported), float(metrics["makespan_min"]), "reported GA best vs re-decoded GA best", 0.005)
    return {
        "source": str(p),
        "chromosome": chromosome,
        "metrics": dict(metrics),
        "rows": list(rows or []),
        "validation": validation,
    }


def solve_instance(
    instance_path: Path | str,
    time_limit: float = 600.0,
    mip_rel_gap: float = 0.0,
    verbose: bool = False,
    keep_schedule: bool = True,
    workers: int = 0,
    random_seed: int = 1,
    ga_result_path: Optional[Path | str] = None,
    use_ga_upper_bound: bool = True,
) -> dict:
    if cp_model is None:
        raise RuntimeError(
            "OR-Tools is required by the AL13 V5 exact solver. Install it with: pip install ortools"
        ) from _ORTOOLS_IMPORT_ERROR

    data = load_instance(instance_path)
    dec = V5ScheduleDecoder(data)
    name = dec.name
    jobs = dec.jobs
    n = len(jobs)
    H = I(dec.horizon)

    warm = _load_validated_ga_warm_start(ga_result_path, dec) if ga_result_path else None

    model = cp_model.CpModel()
    resource_intervals: Dict[str, List] = defaultdict(list)

    # Fixed blocks are exactly the complement of each resource's feasible windows.
    for rid, rows in data["resource_windows"].items():
        for k, (s, e) in enumerate(_blocked_complement(rows, H), 1):
            resource_intervals[str(rid)].append(
                model.NewFixedSizeIntervalVar(s, e - s, f"BLOCK[{rid},{k}]")
            )

    S, C, intervals = {}, {}, {}

    def add_task(j: str, code: str, resources: Sequence[str]):
        dur = I(dec.proc[(j, code)])
        s = model.NewIntVar(0, H - dur, f"S[{j},{code}]")
        e = model.NewIntVar(dur, H, f"C[{j},{code}]")
        iv = model.NewIntervalVar(s, dur, e, f"IV[{j},{code}]")
        S[(j, code)], C[(j, code)], intervals[(j, code)] = s, e, iv
        for rid in resources:
            resource_intervals[rid].append(iv)
        return s, e, iv

    # Job-level tasks excluding event-expanded OIL and pooled 2C.
    for j in jobs:
        for code in ("2B", "PRE", "FINAL", "LASER", "SCALE", "SEAL", "VISUAL"):
            add_task(j, code, dec.resource_by_op[code])

    # Release/material/auxiliary and main-flow precedence.
    for j in jobs:
        model.Add(S[(j, "2B")] >= I(max(dec.release[j], dec.loop[j])))
        lag_2b = I(dec.prec[(j, "2B", "PRE")]["lag_min"])
        model.Add(S[(j, "PRE")] >= I(max(dec.release[j], dec.element[j])))
        model.Add(S[(j, "PRE")] >= C[(j, "2B")] + lag_2b)

        stream_lag = I(dec.prec[(j, "PRE", "FINAL")]["lag_min"])
        model.Add(S[(j, "FINAL")] >= S[(j, "PRE")] + stream_lag)
        model.Add(C[(j, "FINAL")] >= C[(j, "PRE")])

        model.Add(S[(j, "LASER")] >= C[(j, "FINAL")] + I(dec.prec[(j, "FINAL", "LASER")]["lag_min"]))
        model.Add(S[(j, "SCALE")] >= C[(j, "LASER")] + I(dec.prec[(j, "LASER", "SCALE")]["lag_min"]))

    # OIL explicit cycles.  WS_OIL owns the whole non-preemptive cycle; OP_2A
    # owns only the load prefix; OP_PACK owns only the unload suffix.
    oil_meta = []
    oil_vars_by_id = {}
    oil_last_end = {}
    for j in jobs:
        prev_end = None
        for cyc in dec.oil_cycles[j]:
            cid = str(cyc["oil_cycle_id"])
            load_d = I(cyc["w4_load_min"])
            auto_d = I(cyc["auto_dip_min"])
            unload_d = I(cyc["w5_unload_min"])
            total_d = I(cyc["machine_occupancy_min"])
            if abs(total_d - (load_d + auto_d + unload_d)) > 2:
                raise ValueError(f"{name}: OIL duration mismatch in {cid}")
            total_d = load_d + auto_d + unload_d

            s = model.NewIntVar(0, H - total_d, f"S[{cid}]")
            e = model.NewIntVar(total_d, H, f"C[{cid}]")
            machine_iv = model.NewIntervalVar(s, total_d, e, f"IV[{cid}]")
            load_e = model.NewIntVar(load_d, H, f"LOAD_C[{cid}]")
            model.Add(load_e == s + load_d)
            load_iv = model.NewIntervalVar(s, load_d, load_e, f"LOAD_IV[{cid}]")
            unload_s = model.NewIntVar(0, H - unload_d, f"UNLOAD_S[{cid}]")
            model.Add(unload_s == e - unload_d)
            unload_iv = model.NewIntervalVar(unload_s, unload_d, e, f"UNLOAD_IV[{cid}]")

            resource_intervals["WS_OIL"].append(machine_iv)
            resource_intervals["OP_2A"].append(load_iv)
            resource_intervals["OP_PACK"].append(unload_iv)

            if prev_end is None:
                model.Add(s >= C[(j, "SCALE")] + I(dec.prec[(j, "SCALE", "OIL")]["lag_min"]))
            else:
                model.Add(s >= prev_end)
            prev_end = e
            oil_meta.append((j, cyc, s, e, load_d, auto_d, unload_d))
            oil_vars_by_id[cid] = (s, e)
        oil_last_end[j] = prev_end
        model.Add(S[(j, "SEAL")] >= prev_end + I(dec.prec[(j, "OIL", "SEAL")]["lag_min"]))
        model.Add(S[(j, "VISUAL")] >= C[(j, "SEAL")] + I(dec.prec[(j, "SEAL", "VISUAL")]["lag_min"]))

    # Exact job completion is the max of VISUAL and all selected 2C completions.
    # It is referenced by clean-pipeline product-change setup.
    job_done = {j: model.NewIntVar(0, H, f"JOB_DONE[{j}]") for j in jobs}
    completion_terms: Dict[str, List] = {j: [C[(j, "VISUAL")]] for j in jobs}

    # PRE Hamiltonian path gives the exact predecessor used by sequence-dependent setup.
    node = {j: i + 1 for i, j in enumerate(jobs)}
    arcs = []
    arc_lit = {}
    for j in jobs:
        a = model.NewBoolVar(f"arc[START->{j}]")
        arcs.append((0, node[j], a))
        arc_lit[(None, j)] = a
        z = model.NewBoolVar(f"arc[{j}->END]")
        arcs.append((node[j], 0, z))
        arc_lit[(j, None)] = z

    setup_meta = []
    setup_lit_by_pair = {}
    for i in jobs:
        for j in jobs:
            if i == j:
                continue
            lit = model.NewBoolVar(f"arc[{i}->{j}]")
            arcs.append((node[i], node[j], lit))
            arc_lit[(i, j)] = lit
            setup_min = float(dec.setup[dec.product[i]][dec.product[j]])
            if setup_min <= EPS:
                # Zero setup still preserves the PRE predecessor order.
                model.Add(S[(j, "PRE")] >= C[(i, "PRE")]).OnlyEnforceIf(lit)
            else:
                d = I(setup_min)
                ss = model.NewIntVar(0, H - d, f"SETUP_S[{i}->{j}]")
                se = model.NewIntVar(d, H, f"SETUP_C[{i}->{j}]")
                iv = model.NewOptionalIntervalVar(ss, d, se, lit, f"SETUP_IV[{i}->{j}]")
                # Clean pipeline: setup cannot begin until predecessor's FINAL 2C clear.
                # job_done >= PRE completion by physical precedence, but keeping the
                # explicit PRE term documents the confirmed rule and improves propagation.
                model.Add(ss >= job_done[i]).OnlyEnforceIf(lit)
                model.Add(ss >= C[(i, "PRE")]).OnlyEnforceIf(lit)
                model.Add(S[(j, "PRE")] >= se).OnlyEnforceIf(lit)
                for rid in dec.setup_resources:
                    resource_intervals[rid].append(iv)
                setup_meta.append((i, j, setup_min, lit, ss, se))
                setup_lit_by_pair[(i, j)] = (lit, ss, se)
    model.AddCircuit(arcs)

    # Exact 2C batching. Full 10-belt batches are mandatory.
    batch_p = I(dec.batch_processing_min)
    batch_meta = []
    full_by_job: Dict[str, List[Tuple]] = defaultdict(list)
    for j in jobs:
        full = dec.qty[j] // 10
        for b in range(1, full + 1):
            s = model.NewIntVar(0, H - batch_p, f"B_S[{j},{b}]")
            e = model.NewIntVar(batch_p, H, f"B_C[{j},{b}]")
            iv = model.NewIntervalVar(s, batch_p, e, f"B_IV[{j},{b}]")
            model.Add(s >= C[(j, "VISUAL")])
            for rid in dec.batch_resources:
                resource_intervals[rid].append(iv)
            completion_terms[j].append(e)
            item = ("FULL", [j], dec.batch_key[j], None, s, e, iv, b)
            batch_meta.append(item)
            full_by_job[j].append(item)
        # Safe symmetry breaking: full batches of the same job are indistinguishable.
        for prev_item, next_item in zip(full_by_job[j], full_by_job[j][1:]):
            model.Add(prev_item[4] <= next_item[4])

    # Every 5-belt remainder must be matched exactly once with one same-Batch_Key remainder.
    rem_by_key = defaultdict(list)
    for j in jobs:
        if dec.qty[j] % 10 == 5:
            rem_by_key[dec.batch_key[j]].append(j)

    pair_meta = []
    pair_by_key = {}
    for key, js in rem_by_key.items():
        if len(js) % 2:
            raise ValueError(f"{name}: odd 5-belt remainder count for {key}")
        incident = defaultdict(list)
        for a in range(len(js)):
            for b in range(a + 1, len(js)):
                i, j = js[a], js[b]
                lit = model.NewBoolVar(f"PAIR[{i},{j}]")
                s = model.NewIntVar(0, H - batch_p, f"PAIR_S[{i},{j}]")
                e = model.NewIntVar(batch_p, H, f"PAIR_C[{i},{j}]")
                iv = model.NewOptionalIntervalVar(s, batch_p, e, lit, f"PAIR_IV[{i},{j}]")
                model.Add(s >= C[(i, "VISUAL")]).OnlyEnforceIf(lit)
                model.Add(s >= C[(j, "VISUAL")]).OnlyEnforceIf(lit)
                for rid in dec.batch_resources:
                    resource_intervals[rid].append(iv)

                # Effective completion is zero when this candidate pair is inactive;
                # this allows an exact AddMaxEquality for job_done.
                eff = model.NewIntVar(0, H, f"PAIR_EFF_C[{i},{j}]")
                model.Add(eff == e).OnlyEnforceIf(lit)
                model.Add(eff == 0).OnlyEnforceIf(lit.Not())
                completion_terms[i].append(eff)
                completion_terms[j].append(eff)

                incident[i].append(lit)
                incident[j].append(lit)
                item = (i, j, key, lit, s, e, iv, eff)
                pair_meta.append(item)
                pair_by_key[frozenset((i, j))] = item
        for j in js:
            model.Add(sum(incident[j]) == 1)

    # Exact job completion semantics, not just lower bounds.
    for j in jobs:
        model.AddMaxEquality(job_done[j], completion_terms[j])

    # Unary resources plus fixed calendar blocks.
    for rid, ivs in resource_intervals.items():
        if ivs:
            model.AddNoOverlap(ivs)

    cmax = model.NewIntVar(0, H, "CMAX")
    model.AddMaxEquality(cmax, [job_done[j] for j in jobs])
    model.Minimize(cmax)

    # -------------------------- validated GA warm start --------------------------
    warm_info = None
    if warm is not None:
        warm_cmax = float(warm["metrics"]["makespan_min"])
        warm_ub = I(warm_cmax)
        if use_ga_upper_bound:
            # Safe because the independently validated GA schedule is feasible.
            # This cannot remove any solution better than the known incumbent.
            model.Add(cmax <= warm_ub)

        v = warm["validation"]
        # Job-level operation hints.
        for key, rr in v["job_ops"].items():
            model.AddHint(S[key], I(rr["Start_Min"]))
            model.AddHint(C[key], I(rr["Completion_Min"]))

        # OIL cycle hints.
        for cid, rr in v["oils"].items():
            s, e = oil_vars_by_id[cid]
            model.AddHint(s, I(rr["Start_Min"]))
            model.AddHint(e, I(rr["Completion_Min"]))

        # PRE sequence arc hints: one Hamiltonian path implied by the GA chromosome.
        ch = warm["chromosome"]
        selected_arcs = {(None, ch[0]), (ch[-1], None)} | {(ch[k - 1], ch[k]) for k in range(1, len(ch))}
        for pair, lit in arc_lit.items():
            model.AddHint(lit, 1 if pair in selected_arcs else 0)

        # Positive setup interval hints.
        for pair, (lit, ss, se) in setup_lit_by_pair.items():
            rr = v["setups"].get(pair)
            if rr is not None:
                model.AddHint(ss, I(rr["Start_Min"]))
                model.AddHint(se, I(rr["Completion_Min"]))

        # 2C hints.  Full batches from one job are indistinguishable, so map them
        # in chronological order to the symmetry-broken CP-SAT batch variables.
        warm_full = defaultdict(list)
        warm_pairs = {}
        for rr in v["batches"]:
            contributors = [x for x in str(rr["Job_ID"]).split(";") if x]
            if str(rr.get("Batch_Composition")) == "FULL":
                warm_full[contributors[0]].append(rr)
            else:
                warm_pairs[frozenset(contributors)] = rr
        for j in jobs:
            warm_full[j].sort(key=lambda r: (float(r["Start_Min"]), float(r["Completion_Min"])))
            items = full_by_job[j]
            if len(warm_full[j]) != len(items):
                raise ValueError(f"warm-start full batch count mismatch during hinting for {j}")
            for rr, item in zip(warm_full[j], items):
                _kind, _contributors, _key, _lit, s, e, _iv, _b = item
                model.AddHint(s, I(rr["Start_Min"]))
                model.AddHint(e, I(rr["Completion_Min"]))

        for item in pair_meta:
            i, j, _key, lit, s, e, _iv, eff = item
            rr = warm_pairs.get(frozenset((i, j)))
            chosen = rr is not None
            model.AddHint(lit, 1 if chosen else 0)
            if chosen:
                model.AddHint(s, I(rr["Start_Min"]))
                model.AddHint(e, I(rr["Completion_Min"]))
                model.AddHint(eff, I(rr["Completion_Min"]))
            else:
                model.AddHint(eff, 0)

        for j in jobs:
            model.AddHint(job_done[j], I(v["job_completion_min"][j]))
        model.AddHint(cmax, warm_ub)

        warm_info = {
            "source": warm["source"],
            "validated": True,
            "cmax_min": warm_cmax,
            "upper_bound_added": bool(use_ga_upper_bound),
            "chromosome": list(warm["chromosome"]),
        }

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.relative_gap_limit = float(max(0.0, mip_rel_gap))
    if workers and int(workers) > 0:
        solver.parameters.num_search_workers = int(workers)
    else:
        solver.parameters.num_search_workers = min(8, max(1, __import__("os").cpu_count() or 1))
    solver.parameters.random_seed = int(random_seed)
    solver.parameters.log_search_progress = bool(verbose)

    t0 = time.perf_counter()
    status = solver.Solve(model)
    runtime = time.perf_counter() - t0
    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    proven_optimal = status == cp_model.OPTIMAL

    schedule = []
    total_tard = None
    total_flow = None
    tardy_jobs = None
    makespan = None
    if feasible:
        makespan = F(solver.Value(cmax))
        completions = {j: F(solver.Value(job_done[j])) for j in jobs}
        total_tard = sum(max(0.0, completions[j] - dec.due[j]) for j in jobs)
        tardy_jobs = sum(int(completions[j] > dec.due[j] + EPS) for j in jobs)
        total_flow = sum(completions[j] - dec.release[j] for j in jobs)

        if keep_schedule:
            for j in jobs:
                for code in ("2B", "PRE", "FINAL", "LASER", "SCALE", "SEAL", "VISUAL"):
                    schedule.append({
                        "Event_Type": "JOB_OP", "Job_ID": j, "Operation_Code": code,
                        "Start_Min": F(solver.Value(S[(j, code)])),
                        "Completion_Min": F(solver.Value(C[(j, code)])),
                        "Processing_Min": dec.proc[(j, code)],
                        "Resources": ";".join(dec.resource_by_op[code]),
                    })
            for j, cyc, s, e, ld, ad, ud in oil_meta:
                schedule.append({
                    "Event_Type": "OIL_CYCLE", "Job_ID": j, "Operation_Code": "OIL",
                    "Oil_Cycle_ID": cyc["oil_cycle_id"], "Cycle_No": int(cyc["cycle_no"]),
                    "Cycle_Qty": int(cyc["cycle_qty"]),
                    "Start_Min": F(solver.Value(s)), "Completion_Min": F(solver.Value(e)),
                    "Processing_Min": F(ld + ad + ud), "W4_Load_Min": F(ld),
                    "Auto_Dip_Min": F(ad), "W5_Unload_Min": F(ud),
                    "Resources": "WS_OIL;OP_2A(load);OP_PACK(unload)",
                })
            for i, j, d, lit, ss, se in setup_meta:
                if solver.Value(lit):
                    schedule.append({
                        "Event_Type": "SETUP", "Predecessor_Job_ID": i, "Job_ID": j,
                        "From_Product": dec.product[i], "To_Product": dec.product[j],
                        "Start_Min": F(solver.Value(ss)), "Completion_Min": F(solver.Value(se)),
                        "Processing_Min": d, "Resources": ";".join(dec.setup_resources),
                    })
            batch_no = 0
            for kind, contributors, key, lit, s, e, iv, _b in batch_meta:
                batch_no += 1
                schedule.append({
                    "Event_Type": "2C_BATCH", "Batch_ID": f"B{batch_no:05d}",
                    "Job_ID": ";".join(contributors), "Batch_Key": key,
                    "Batch_Composition": kind, "Start_Min": F(solver.Value(s)),
                    "Completion_Min": F(solver.Value(e)), "Processing_Min": dec.batch_processing_min,
                    "Resources": ";".join(dec.batch_resources),
                })
            for i, j, key, lit, s, e, iv, _eff in pair_meta:
                if solver.Value(lit):
                    batch_no += 1
                    schedule.append({
                        "Event_Type": "2C_BATCH", "Batch_ID": f"B{batch_no:05d}",
                        "Job_ID": f"{i};{j}", "Batch_Key": key,
                        "Batch_Composition": "POOLED_5+5", "Start_Min": F(solver.Value(s)),
                        "Completion_Min": F(solver.Value(e)), "Processing_Min": dec.batch_processing_min,
                        "Resources": ";".join(dec.batch_resources),
                    })
            schedule.sort(key=lambda r: (float(r.get("Start_Min", 0.0)), str(r.get("Event_Type", "")), str(r.get("Job_ID", ""))))

    # CP-SAT's best objective bound is a valid lower bound on the optimum.
    best_bound = F(solver.BestObjectiveBound()) if feasible else None
    rel_gap = None
    if feasible and makespan is not None and makespan > 1e-12 and best_bound is not None:
        rel_gap = max(0.0, (makespan - best_bound) / makespan)

    return {
        "instance_name": name,
        "size": n,
        "schema_version": data["metadata"]["schema_version"],
        "solver": "OR-Tools CP-SAT exact interval scheduling",
        "legacy_filename": "solve_milp_instance.py",
        "is_milp_backend": False,
        "objective": "MIN_CMAX_FINAL_2C",
        "objective_terms": ["Cmax only; tardiness/flow are diagnostics"],
        "feasible": bool(feasible),
        "proven_optimal": bool(proven_optimal),
        "status": int(status),
        "status_name": solver.StatusName(status),
        "makespan_min": makespan,
        "best_bound_min": best_bound,
        "relative_gap": rel_gap,
        "runtime_sec": float(runtime),
        "num_conflicts": int(solver.NumConflicts()),
        "num_branches": int(solver.NumBranches()),
        "total_tardiness_min": total_tard,
        "tardy_jobs": tardy_jobs,
        "total_flow_time_min": total_flow,
        "time_scale_per_minute": SCALE,
        "warm_start": warm_info,
        "setup_sequence_model": "AddCircuit exact PRE predecessor + optional composite setup intervals; clean-pipeline setup waits exact predecessor final-2C completion",
        "oil_model": "explicit nonpreemptive cycles; WS_OIL whole cycle; OP_2A load prefix; OP_PACK unload suffix",
        "batching_model": "mandatory full 10-batches + exact same-key 5+5 perfect matching; no partial batch",
        "job_completion_model": "AddMaxEquality(VISUAL, all mandatory full batches, selected 5+5 batch)",
        "calendar_model": "fixed blocking intervals = complement of resource availability windows",
        "schedule": schedule,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Solve one AL13 V5 instance by exact CP-SAT; optionally warm-start from a validated GA result"
    )
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--time-limit", type=float, default=600.0)
    ap.add_argument("--mip-gap", "--relative-gap", dest="mip_gap", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--random-seed", type=int, default=1)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--ga-result", type=Path, default=None,
        help="GA/SS-Micro-GA result JSON containing best_chromosome; re-decoded and validated before use as CP-SAT warm start",
    )
    ap.add_argument(
        "--no-ga-upper-bound", action="store_true",
        help="Use GA schedule only as hints, without adding the safe Cmax <= GA_Cmax incumbent bound",
    )
    args = ap.parse_args()

    result = solve_instance(
        args.instance,
        time_limit=args.time_limit,
        mip_rel_gap=args.mip_gap,
        verbose=args.verbose,
        keep_schedule=True,
        workers=args.workers,
        random_seed=args.random_seed,
        ga_result_path=args.ga_result,
        use_ga_upper_bound=not args.no_ga_upper_bound,
    )
    out = args.output or args.instance.with_name(args.instance.stem + "__exact_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if result.get("warm_start"):
        ws = result["warm_start"]
        print(
            f"[warm-start] VALIDATED GA Cmax={ws['cmax_min']:.3f} | "
            f"upper_bound={'ON' if ws['upper_bound_added'] else 'OFF'} | source={ws['source']}",
            flush=True,
        )
    print(
        f"{result['instance_name']} | EXACT Cmax={result['makespan_min']} min | "
        f"time={result['runtime_sec']:.2f}s | optimal={result['proven_optimal']} | "
        f"bound={result['best_bound_min']} | gap={result['relative_gap']} | backend=CP-SAT"
    )
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
