#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solve ONE frozen AL13 benchmark instance with a continuous-time MILP.

The script reads only a JSON instance produced by ``generate_benchmark_instances.py``.
It does not read Excel and does not generate/sampling any jobs.

Model used for the benchmark
----------------------------
* one assignment to an eligible workstation and one effective working window;
* deterministic machine-dependent processing time;
* release/component availability;
* technological precedence and transfer lags;
* unit-capacity workstation non-overlap;
* non-preemptive operations confined to effective windows;
* makespan minimization.

For the BASE resource scenario in the current AL13 dataset, reusable-resource
capacities are mathematically dominated by workstation capacities.  The data
generator proves this per frozen instance.  To avoid thousands of symmetric
unit-resource binaries, this MILP intentionally removes only those proven
redundant constraints.  If the proof is false, this solver refuses to run so
that MILP and GA are not benchmarked on different feasible regions.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from benchmark_common import FrozenInstanceDecoder, OP_SEQUENCE, PREDECESSOR, instance_name, instance_size, load_instance


class VarRegistry:
    def __init__(self):
        self.names: List[str] = []
        self.lb: List[float] = []
        self.ub: List[float] = []
        self.integrality: List[int] = []

    def add(self, name: str, lb: float = 0.0, ub: float = math.inf, integer: bool = False) -> int:
        idx = len(self.names)
        self.names.append(name)
        self.lb.append(float(lb))
        self.ub.append(float(ub))
        self.integrality.append(1 if integer else 0)
        return idx


class ConstraintBuilder:
    def __init__(self):
        self.row: List[int] = []
        self.col: List[int] = []
        self.data: List[float] = []
        self.lb: List[float] = []
        self.ub: List[float] = []

    def add(self, coeffs: Mapping[int, float], lb: float = -math.inf, ub: float = math.inf) -> None:
        r = len(self.lb)
        for c, v in coeffs.items():
            if abs(v) > 1e-14:
                self.row.append(r)
                self.col.append(int(c))
                self.data.append(float(v))
        self.lb.append(float(lb))
        self.ub.append(float(ub))

    def linear_constraint(self, nvars: int) -> LinearConstraint:
        A = coo_matrix((self.data, (self.row, self.col)), shape=(len(self.lb), nvars)).tocsr()
        return LinearConstraint(A, np.asarray(self.lb), np.asarray(self.ub))


def build_indices(data: Mapping):
    jobs = [str(j["job_id"]) for j in data["jobs"]]
    ops = [(j, op) for j in jobs for op in OP_SEQUENCE]

    release = {str(j["job_id"]): float(j["release_min"]) for j in data["jobs"]}
    element = {str(j["job_id"]): float(j["element_ready_min"]) for j in data["jobs"]}
    loop = {str(j["job_id"]): float(j["loopset_release_min"]) for j in data["jobs"]}

    options = defaultdict(list)
    for r in data["operation_options"]:
        if int(r.get("eligible", 1)) == 1:
            options[(str(r["job_id"]), str(r["operation_code"]))].append(
                (str(r["station_id"]), float(r["processing_min"]))
            )

    lag = {}
    for r in data["precedence"]:
        lag[(str(r["job_id"]), str(r["pred"]), str(r["succ"]))] = float(r["lag_min"])

    windows = {
        str(sid): [dict(w) for w in ws]
        for sid, ws in data["station_windows"].items()
    }
    return jobs, ops, release, element, loop, options, lag, windows


def earliest_start_bounds(jobs, release, element, loop, options, lag):
    """Safe operation-specific lower bounds used only to prune impossible windows."""
    es = {}
    for j in jobs:
        for op in OP_SEQUENCE:
            minp = min(p for _s, p in options[(j, op)])
            if op == "2B":
                es[(j, op)] = max(release[j], loop[j])
            elif op == "PRE":
                pred = "2B"
                minpp = min(p for _s, p in options[(j, pred)])
                es[(j, op)] = max(
                    release[j],
                    element[j],
                    es[(j, pred)] + minpp + lag[(j, pred, op)],
                )
            else:
                pred = PREDECESSOR[op]
                minpp = min(p for _s, p in options[(j, pred)])
                es[(j, op)] = es[(j, pred)] + minpp + lag[(j, pred, op)]
    return es


def latest_finish_bounds(jobs, options, lag, cmax_upper_bound):
    """Safe latest-finish bounds implied by a known feasible Cmax upper bound."""
    minp = {(j, op): min(p for _s, p in options[(j, op)]) for j in jobs for op in OP_SEQUENCE}
    lc = {}
    for j in jobs:
        lc[(j, "2C")] = float(cmax_upper_bound)
        for succ in reversed(OP_SEQUENCE[1:]):
            pred = PREDECESSOR[succ]
            latest_start_succ = lc[(j, succ)] - minp[(j, succ)]
            lc[(j, pred)] = latest_start_succ - lag[(j, pred, succ)]
    return lc, minp


def solve_instance(
    instance_path: Path | str,
    time_limit: float = 600.0,
    mip_rel_gap: float = 0.0,
    verbose: bool = False,
    keep_schedule: bool = True,
) -> dict:
    data = load_instance(instance_path)
    name = instance_name(data)
    n = instance_size(data)

    if not bool(data["metadata"].get("resource_constraints_redundant_under_station_capacity", False)):
        raise RuntimeError(
            f"{name}: reusable-resource constraints are NOT proven redundant. "
            "This benchmark MILP deliberately refuses to omit binding resource constraints. "
            "Use BASE instances or extend the MILP with explicit resource-unit disjunctions."
        )

    jobs, ops, release, element, loop, options, lag, windows = build_indices(data)
    es = earliest_start_bounds(jobs, release, element, loop, options, lag)

    # Deterministic feasible FIFO schedule gives a valid primal upper bound.
    # It does NOT change the optimum; it only tightens variable bounds / Big-M
    # and removes station windows that cannot be needed by any better solution.
    decoder = FrozenInstanceDecoder(data)
    fifo = sorted(decoder.job_ids, key=lambda j: (decoder.release[j], decoder.priority[j], j))
    fifo_metrics, _ = decoder.decode(fifo, keep_schedule=False)
    heuristic_ub = float(fifo_metrics["makespan_min"])
    original_horizon = float(data["metadata"]["horizon_end_min"])
    horizon = min(original_horizon, heuristic_ub + 1e-5)

    max_p = max(float(r["processing_min"]) for r in data["operation_options"])
    lc, minp = latest_finish_bounds(jobs, options, lag, heuristic_ub)

    vr = VarRegistry()
    S, C = {}, {}
    for j, op in ops:
        s_lb = max(0.0, es[(j, op)])
        c_ub = min(horizon, lc[(j, op)])
        s_ub = min(horizon, c_ub - minp[(j, op)])
        if s_ub + 1e-8 < s_lb:
            raise RuntimeError(f"{name}: inconsistent bounds for {j}/{op}: ES={s_lb}, LS={s_ub}")
        S[(j, op)] = vr.add(f"S[{j},{op}]", s_lb, s_ub, False)
        C[(j, op)] = vr.add(f"C[{j},{op}]", s_lb + minp[(j, op)], c_ub, False)
    cmax_idx = vr.add("Cmax", 0.0, horizon, False)

    # q[(job, op, station, window_id)] = 1 iff operation uses that station/window.
    q = {}
    q_by_op = defaultdict(list)
    q_by_op_station = defaultdict(list)
    q_meta = {}

    for j, op in ops:
        for sid, p in options[(j, op)]:
            for w in windows.get(sid, []):
                ws, we = float(w["start_min"]), float(w["end_min"])
                # Window must be long enough and not end before the basic earliest start.
                if ws >= horizon - 1e-9:
                    continue
                if we + 1e-9 < es[(j, op)] + p:
                    continue
                if we - ws + 1e-9 < p:
                    continue
                # The operation must also be able to finish before its safe latest-finish bound.
                if max(ws, es[(j, op)]) + p > min(we, lc[(j, op)]) + 1e-9:
                    continue
                idx = vr.add(f"q[{j},{op},{sid},{w['window_id']}]", 0.0, 1.0, True)
                key = (j, op, sid, str(w["window_id"]))
                q[key] = idx
                q_by_op[(j, op)].append(idx)
                q_by_op_station[(j, op, sid)].append(idx)
                q_meta[idx] = (j, op, sid, str(w["window_id"]), ws, we, p, w)
        if not q_by_op[(j, op)]:
            raise RuntimeError(
                f"{name}: no feasible station/window candidate remains for {j}/{op}; "
                "increase generator horizon-days-after-last-release."
            )

    # Ordering variable per pair and common station.
    y = {}
    station_ops = defaultdict(set)
    for (j, op, sid), qlist in q_by_op_station.items():
        if qlist:
            station_ops[sid].add((j, op))
    for sid, uops in station_ops.items():
        ordered = sorted(uops)
        for a in range(len(ordered)):
            for b in range(a + 1, len(ordered)):
                u, v = ordered[a], ordered[b]
                y[(u, v, sid)] = vr.add(
                    f"y[{u[0]},{u[1]}<{v[0]},{v[1]}@{sid}]", 0.0, 1.0, True
                )

    cb = ConstraintBuilder()

    # Exactly one station/window per operation + processing-time equality.
    for u in ops:
        cb.add({idx: 1.0 for idx in q_by_op[u]}, lb=1.0, ub=1.0)
        coeff = {C[u]: 1.0, S[u]: -1.0}
        for idx in q_by_op[u]:
            coeff[idx] = coeff.get(idx, 0.0) - q_meta[idx][6]
        cb.add(coeff, lb=0.0, ub=0.0)

    # Effective-window boundaries.
    for idx, (j, op, sid, wid, ws, we, p, w) in q_meta.items():
        # Tight, operation-specific Big-M values derived from variable bounds.
        s_lb = vr.lb[S[(j, op)]]
        c_ub = vr.ub[C[(j, op)]]
        m_start = max(0.0, ws - s_lb)
        m_end = max(0.0, c_ub - we)
        # S >= ws - m_start(1-q)
        cb.add({S[(j, op)]: 1.0, idx: -m_start}, lb=ws - m_start)
        # C <= we + m_end(1-q)
        cb.add({C[(j, op)]: 1.0, idx: m_end}, ub=we + m_end)

    # Release/component availability and precedence.
    for j in jobs:
        cb.add({S[(j, "2B")]: 1.0}, lb=max(release[j], loop[j]))
        cb.add({S[(j, "PRE")]: 1.0}, lb=max(release[j], element[j]))
        for succ in OP_SEQUENCE[1:]:
            pred = PREDECESSOR[succ]
            # S_succ - C_pred >= lag
            cb.add({S[(j, succ)]: 1.0, C[(j, pred)]: -1.0}, lb=lag[(j, pred, succ)])

    # Workstation unit-capacity disjunctions.
    for (u, v, sid), yidx in y.items():
        xu = q_by_op_station[(u[0], u[1], sid)]
        xv = q_by_op_station[(v[0], v[1], sid)]

        # Tight pair-specific disjunction constants.
        m_uv = max(0.0, vr.ub[C[u]] - vr.lb[S[v]])
        m_vu = max(0.0, vr.ub[C[v]] - vr.lb[S[u]])

        # u before v when y=1 and both operations select sid.
        coeff1 = {S[v]: 1.0, C[u]: -1.0, yidx: -m_uv}
        for idx in xu:
            coeff1[idx] = coeff1.get(idx, 0.0) - m_uv
        for idx in xv:
            coeff1[idx] = coeff1.get(idx, 0.0) - m_uv
        cb.add(coeff1, lb=-3.0 * m_uv)

        # v before u when y=0 and both operations select sid.
        coeff2 = {S[u]: 1.0, C[v]: -1.0, yidx: m_vu}
        for idx in xu:
            coeff2[idx] = coeff2.get(idx, 0.0) - m_vu
        for idx in xv:
            coeff2[idx] = coeff2.get(idx, 0.0) - m_vu
        cb.add(coeff2, lb=-2.0 * m_vu)

    # Makespan over final 2C operations.
    for j in jobs:
        cb.add({cmax_idx: 1.0, C[(j, "2C")]: -1.0}, lb=0.0)

    nvars = len(vr.names)
    objective = np.zeros(nvars, dtype=float)
    objective[cmax_idx] = 1.0
    bounds = Bounds(np.asarray(vr.lb), np.asarray(vr.ub))
    constraints = cb.linear_constraint(nvars)

    if verbose:
        print(
            f"{name}: jobs={n}, vars={nvars}, binaries={sum(vr.integrality)}, "
            f"constraints={len(cb.lb)}, tightened_horizon={horizon:.1f}",
            flush=True,
        )

    t0 = time.perf_counter()
    res = milp(
        c=objective,
        integrality=np.asarray(vr.integrality, dtype=int),
        bounds=bounds,
        constraints=constraints,
        options={
            "time_limit": float(time_limit),
            "mip_rel_gap": float(mip_rel_gap),
            "disp": bool(verbose),
        },
    )
    runtime = time.perf_counter() - t0

    feasible = res.x is not None and np.all(np.isfinite(res.x))
    makespan = float(res.x[cmax_idx]) if feasible else None
    gap = getattr(res, "mip_gap", None)
    dual_bound = getattr(res, "mip_dual_bound", None)
    node_count = getattr(res, "mip_node_count", None)
    proven_optimal = bool(res.status == 0 and feasible and (gap is None or float(gap) <= 1e-8))

    schedule = []
    if feasible and keep_schedule:
        x = res.x
        for u in ops:
            chosen = [idx for idx in q_by_op[u] if x[idx] > 0.5]
            if len(chosen) != 1:
                raise RuntimeError(f"{name}: MILP returned {len(chosen)} assignments for {u}")
            idx = chosen[0]
            j, op, sid, wid, ws, we, p, w = q_meta[idx]
            schedule.append(
                {
                    "Job_ID": j,
                    "Operation_Code": op,
                    "Station_ID": sid,
                    "Window_ID": wid,
                    "Calendar_Date": w["calendar_date"],
                    "Shift_ID": w["shift_id"],
                    "Start_Min": float(x[S[u]]),
                    "Completion_Min": float(x[C[u]]),
                    "Processing_Min": float(p),
                }
            )

    return {
        "instance_name": name,
        "size": n,
        "solver": "scipy.optimize.milp (HiGHS)",
        "objective": "MIN_MAKESPAN",
        "feasible": bool(feasible),
        "proven_optimal": proven_optimal,
        "status": int(res.status),
        "message": str(res.message),
        "makespan_min": makespan,
        "runtime_sec": float(runtime),
        "mip_gap": None if gap is None else float(gap),
        "mip_dual_bound": None if dual_bound is None else float(dual_bound),
        "mip_node_count": None if node_count is None else int(node_count),
        "n_vars": nvars,
        "n_binary": int(sum(vr.integrality)),
        "n_constraints": len(cb.lb),
        "big_m": "operation/pair-specific tightened values",
        "heuristic_upper_bound_min": float(heuristic_ub),
        "original_horizon_min": float(original_horizon),
        "tightened_horizon_min": float(horizon),
        "resource_modeling": "omitted_only_after_generator_proved_redundant_under_station_capacity",
        "schedule": schedule,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Solve one frozen AL13 instance by MILP.")
    ap.add_argument("--instance", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--time-limit", type=float, default=600.0)
    ap.add_argument("--mip-gap", type=float, default=0.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    result = solve_instance(
        args.instance,
        time_limit=args.time_limit,
        mip_rel_gap=args.mip_gap,
        verbose=args.verbose,
        keep_schedule=True,
    )
    out = args.output or args.instance.with_name(args.instance.stem + "__milp_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(
        f"{result['instance_name']} | MILP Cmax={result['makespan_min']} min | "
        f"time={result['runtime_sec']:.2f}s | optimal={result['proven_optimal']} | gap={result['mip_gap']}"
    )
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
