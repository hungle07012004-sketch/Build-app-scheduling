#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared V5 physical-model utilities for the AL13 scheduling benchmark.

This module is the single feasibility definition used by the GA decoder.
The exact reference solver consumes the same frozen JSON instance.

V5 physical logic implemented here
----------------------------------
* 2B is an AUXILIARY loop-set inspection stream feeding PRE.
* Main job route: PRE -> FINAL -> LASER -> SCALE -> OIL -> SEAL -> VISUAL.
* There are no 2A or PACK operations; OP_2A and OP_PACK are unary workers.
* PRE -> FINAL uses SS_FIRST_BELT plus final synchronization C_FINAL >= C_PRE.
* OIL is expanded into <=6-belt cycles.  Each cycle occupies WS_OIL for the
  complete load+automatic-dip+unload interval; OP_2A is held only for LOAD and
  OP_PACK only for UNLOAD.  A cycle must fit one effective (break/downtime-free)
  WS_OIL window and is therefore non-preemptive.
* 2C forms exact batches of 10.  The V5 dataset has Qty_Mod_10 in {0,5}; full
  10-belt batches are released by each job and 5-belt remainders are paired by
  Batch_Key.  The decoder pairs remainder jobs by VISUAL-ready time, which gives
  the earliest possible release multiset for the single 2C server and therefore
  minimizes final Cmax for a fixed upstream schedule.
* Sequence-dependent Product_Code changeover is one composite setup event before
  successor PRE, simultaneously occupying WS_PRE_JIG + OP_PRE + OP_2A + OP_PACK.
* Primary objective is makespan at the final 2C batch completion.
"""
from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

EPS = 1e-9

AUX_OP = "2B"
MAIN_OPS = ("PRE", "FINAL", "LASER", "SCALE", "OIL", "SEAL", "VISUAL")
JOB_LEVEL_OPS = ("2B", "PRE", "FINAL", "LASER", "SCALE", "SEAL", "VISUAL")

DEFAULT_RESOURCE_BY_OP = {
    "2B": ("WS_2B_INSPECTION", "OP_2B"),
    "PRE": ("WS_PRE_JIG", "OP_PRE"),
    "FINAL": ("WS_FINAL", "OP_FINAL"),
    "LASER": ("WS_LASER", "OP_2A"),
    "SCALE": ("WS_SCALE", "OP_2A"),
    "SEAL": ("WS_SEAL", "OP_PACK"),
    "VISUAL": ("WS_PACK_VISUAL", "OP_PACK"),
    "2C_BATCH": ("WS_2C_BATCH", "OP_2C"),
}
SETUP_RESOURCES = ("WS_PRE_JIG", "OP_PRE", "OP_2A", "OP_PACK")


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    window_id: str
    calendar_date: str = ""
    shift_id: str = ""


@dataclass
class ScheduledInterval:
    start: float
    end: float
    label: str


def load_instance(path: Path | str) -> dict:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    validate_instance_schema(data, source=p)
    return data


def validate_instance_schema(data: Mapping, source: Optional[Path] = None) -> None:
    required = [
        "metadata", "jobs", "job_operations", "precedence", "oil_cycles",
        "setup_matrix", "resource_windows", "resource_by_operation", "batching",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        loc = f" in {source}" if source else ""
        raise ValueError(f"AL13 V5 instance{loc} missing keys: {missing}")
    if str(data.get("metadata", {}).get("schema_version", "")).upper() != "AL13_BENCHMARK_V5":
        raise ValueError("Expected schema_version=AL13_BENCHMARK_V5. Regenerate instances with the V5 generator.")
    n = int(data["metadata"]["size"])
    if len(data["jobs"]) != n:
        raise ValueError(f"metadata.size={n}, jobs={len(data['jobs'])}")
    ids = [str(j["job_id"]) for j in data["jobs"]]
    if len(set(ids)) != n:
        raise ValueError("Duplicate job_id in instance")
    batch_size = int(data["batching"].get("batch_size", 10))
    if batch_size != 10:
        raise ValueError(f"V5 decoder expects exact 2C batch size 10, got {batch_size}")
    bad_mod = [j for j in data["jobs"] if int(j["batch_qty"]) % 10 not in (0, 5)]
    if bad_mod:
        raise ValueError("This V5 implementation requires Qty mod 10 in {0,5}; regenerate/check dataset")
    by_key = defaultdict(int)
    for j in data["jobs"]:
        by_key[str(j["batch_key"])] += int(j["batch_qty"])
    bad_keys = {k: q for k, q in by_key.items() if q % 10 != 0}
    if bad_keys:
        raise ValueError(f"Exact-10 infeasible selected instance; key totals not divisible by 10: {bad_keys}")


def instance_name(data: Mapping) -> str:
    return str(data["metadata"]["instance_name"])


def instance_size(data: Mapping) -> int:
    return int(data["metadata"]["size"])


def _merge_windows(windows: Sequence[Window]) -> List[Window]:
    if not windows:
        return []
    arr = sorted(windows, key=lambda w: (w.start, w.end, w.window_id))
    out: List[Window] = []
    for w in arr:
        if w.end <= w.start + EPS:
            continue
        if out and w.start <= out[-1].end + EPS:
            prev = out[-1]
            out[-1] = Window(prev.start, max(prev.end, w.end), prev.window_id, prev.calendar_date, prev.shift_id)
        else:
            out.append(w)
    return out


def _interval_conflict_end(intervals: Sequence[ScheduledInterval], start: float, end: float) -> Optional[float]:
    """Return an overlapping busy interval end, else None. intervals sorted by start."""
    if not intervals:
        return None
    starts = [x.start for x in intervals]
    pos = bisect.bisect_right(starts, start) - 1
    if pos >= 0 and intervals[pos].end > start + EPS:
        return intervals[pos].end
    pos += 1
    if pos < len(intervals) and intervals[pos].start < end - EPS:
        return intervals[pos].end
    return None


def _insert_busy(intervals: List[ScheduledInterval], item: ScheduledInterval) -> None:
    starts = [x.start for x in intervals]
    pos = bisect.bisect_right(starts, item.start)
    intervals.insert(pos, item)


class V5ScheduleDecoder:
    """Serial schedule-generation decoder for the confirmed AL13 V5 model."""

    def __init__(self, data: Mapping):
        validate_instance_schema(data)
        self.data = data
        self.name = instance_name(data)
        self.horizon = float(data["metadata"]["horizon_end_min"])

        self.jobs = [str(j["job_id"]) for j in data["jobs"]]
        self.job_set = frozenset(self.jobs)
        self.job_index = {j: i for i, j in enumerate(self.jobs)}
        self.info = {str(j["job_id"]): dict(j) for j in data["jobs"]}

        self.release = {j: float(self.info[j]["release_min"]) for j in self.jobs}
        self.element = {j: float(self.info[j]["element_ready_min"]) for j in self.jobs}
        self.loop = {j: float(self.info[j]["loopset_release_min"]) for j in self.jobs}
        self.due = {j: float(self.info[j]["due_min"]) for j in self.jobs}
        self.priority = {j: int(self.info[j].get("priority", 999)) for j in self.jobs}
        self.qty = {j: int(self.info[j]["batch_qty"]) for j in self.jobs}
        self.product = {j: str(self.info[j]["product_code"]) for j in self.jobs}
        self.batch_key = {j: str(self.info[j]["batch_key"]) for j in self.jobs}

        self.op = {}
        for r in data["job_operations"]:
            self.op[(str(r["job_id"]), str(r["operation_code"]))] = dict(r)
        for j in self.jobs:
            for code in JOB_LEVEL_OPS:
                if (j, code) not in self.op:
                    raise ValueError(f"{self.name}: missing job operation {j}/{code}")

        self.proc = {
            (j, code): float(self.op[(j, code)]["processing_min"])
            for j in self.jobs for code in JOB_LEVEL_OPS
        }
        self.first_belt_lag = {j: float(self.op[(j, "PRE")].get("first_belt_completion_lag_min", 0.0)) for j in self.jobs}

        self.prec = {}
        for r in data["precedence"]:
            self.prec[(str(r["job_id"]), str(r["pred"]), str(r["succ"]))] = dict(r)

        self.oil_cycles: Dict[str, List[dict]] = defaultdict(list)
        for r in data["oil_cycles"]:
            self.oil_cycles[str(r["job_id"])].append(dict(r))
        for j in self.jobs:
            self.oil_cycles[j].sort(key=lambda r: int(r["cycle_no"]))
            if not self.oil_cycles[j]:
                raise ValueError(f"{self.name}: no OIL cycles for {j}")

        self.setup = {
            str(a): {str(b): float(v) for b, v in row.items()}
            for a, row in data["setup_matrix"].items()
        }

        self.resource_by_op = {
            str(k): tuple(str(x) for x in v)
            for k, v in data.get("resource_by_operation", DEFAULT_RESOURCE_BY_OP).items()
        }
        self.setup_resources = tuple(str(x) for x in data.get("setup_resources", SETUP_RESOURCES))

        self.windows: Dict[str, List[Window]] = {}
        self.window_ends: Dict[str, List[float]] = {}
        for rid, rows in data["resource_windows"].items():
            arr = [
                Window(
                    start=float(w["start_min"]), end=float(w["end_min"]),
                    window_id=str(w.get("window_id", "")),
                    calendar_date=str(w.get("calendar_date", "")), shift_id=str(w.get("shift_id", "")),
                )
                for w in rows
            ]
            arr = _merge_windows(arr)
            self.windows[str(rid)] = arr
            self.window_ends[str(rid)] = [w.end for w in arr]

        needed_resources = set(self.setup_resources)
        for vals in self.resource_by_op.values():
            needed_resources.update(vals)
        needed_resources.update(("WS_OIL", "OP_2A", "OP_PACK"))
        missing_res = sorted(r for r in needed_resources if r not in self.windows)
        if missing_res:
            raise ValueError(f"{self.name}: resource windows missing for {missing_res}")

        self.batch_size = int(data["batching"].get("batch_size", 10))
        self.batch_processing_min = float(data["batching"].get("processing_min_per_batch", 4.5))
        self.batch_resources = tuple(self.resource_by_op.get("2C_BATCH", DEFAULT_RESOURCE_BY_OP["2C_BATCH"]))
        self.batch_windows = self._intersect_resource_windows(self.batch_resources)
        if not self.batch_windows:
            raise ValueError(f"{self.name}: no common 2C availability windows")

        # Cheap workload heuristics for initial GA seeds.
        self.min_workload = {}
        for j in self.jobs:
            oil = sum(float(c["machine_occupancy_min"]) for c in self.oil_cycles[j])
            self.min_workload[j] = sum(self.proc[(j, op)] for op in JOB_LEVEL_OPS) + oil + (self.qty[j] / 10.0) * self.batch_processing_min

    def _intersect_resource_windows(self, resources: Sequence[str]) -> List[Window]:
        if not resources:
            return [Window(0.0, self.horizon, "ALL")]
        current = list(self.windows[resources[0]])
        for rid in resources[1:]:
            other = self.windows[rid]
            out: List[Window] = []
            i = j = 0
            while i < len(current) and j < len(other):
                s = max(current[i].start, other[j].start)
                e = min(current[i].end, other[j].end)
                if e > s + EPS:
                    out.append(Window(s, e, f"INT::{resources[0]}::{rid}::{len(out)+1}"))
                if current[i].end < other[j].end - EPS:
                    i += 1
                else:
                    j += 1
            current = out
            if not current:
                break
        return _merge_windows(current)

    def _next_feasible_start(
        self,
        rid: str,
        ready: float,
        duration: float,
        busy: Mapping[str, List[ScheduledInterval]],
    ) -> Optional[Tuple[float, Window]]:
        windows = self.windows[rid]
        ends = self.window_ends[rid]
        wi = bisect.bisect_right(ends, ready + EPS)
        intervals = busy.get(rid, [])
        while wi < len(windows):
            w = windows[wi]
            s = max(ready, w.start)
            while s + duration <= w.end + EPS:
                ce = _interval_conflict_end(intervals, s, s + duration)
                if ce is None:
                    return s, w
                s = max(s + 1e-6, ce)
            wi += 1
        return None

    def _find_joint_slot(
        self,
        resources: Sequence[str],
        ready: float,
        duration: float,
        busy: Mapping[str, List[ScheduledInterval]],
    ) -> Tuple[float, float]:
        candidate = max(0.0, float(ready))
        for _ in range(200000):
            if candidate + duration > self.horizon + EPS:
                break
            starts = []
            failed = False
            for rid in resources:
                nxt = self._next_feasible_start(rid, candidate, duration, busy)
                if nxt is None:
                    failed = True
                    break
                starts.append(nxt[0])
            if failed:
                break
            s = max(starts) if starts else candidate
            if s > candidate + EPS:
                candidate = s
                continue
            # Candidate is individually feasible from same start for every resource.
            return s, s + duration
        raise RuntimeError(
            f"{self.name}: no joint slot resources={tuple(resources)} ready={ready:.3f} duration={duration:.3f}"
        )

    def _reserve(
        self,
        resources: Sequence[str],
        start: float,
        end: float,
        label: str,
        busy: MutableMapping[str, List[ScheduledInterval]],
    ) -> None:
        for rid in resources:
            _insert_busy(busy[rid], ScheduledInterval(start, end, label))

    def _find_oil_cycle_slot(
        self,
        ready: float,
        load_min: float,
        auto_min: float,
        unload_min: float,
        busy: Mapping[str, List[ScheduledInterval]],
    ) -> Tuple[float, float]:
        duration = load_min + auto_min + unload_min
        candidate = float(ready)
        for _ in range(200000):
            m = self._next_feasible_start("WS_OIL", candidate, duration, busy)
            if m is None:
                break
            s = m[0]
            e = s + duration

            load = self._next_feasible_start("OP_2A", s, load_min, busy)
            if load is None:
                break
            if load[0] > s + EPS:
                candidate = load[0]
                continue

            unload_start = e - unload_min
            unload = self._next_feasible_start("OP_PACK", unload_start, unload_min, busy)
            if unload is None:
                break
            if unload[0] > unload_start + EPS:
                candidate = s + (unload[0] - unload_start)
                continue
            return s, e
        raise RuntimeError(f"{self.name}: no feasible OIL cycle slot ready={ready:.3f} duration={duration:.3f}")

    @staticmethod
    def _append_row(rows: Optional[List[dict]], **kwargs) -> None:
        if rows is not None:
            rows.append(kwargs)

    def _schedule_batches(
        self,
        visual_completion: Mapping[str, float],
        keep_schedule: bool,
        jobs_subset: Optional[Sequence[str]] = None,
        allow_unmatched_remainders: bool = False,
    ) -> Tuple[float, Dict[str, float], List[dict]]:
        """Optimal 2C Cmax schedule for fixed VISUAL completion times.

        All 2C batches have identical processing time on one unary server.  Sorting
        release times and never idling when a released batch exists minimizes Cmax.
        We compress full-batch groups by job for metric-only decoding.
        """
        selected_jobs = list(jobs_subset) if jobs_subset is not None else list(self.jobs)
        events: List[dict] = []
        for j in selected_jobs:
            full = self.qty[j] // 10
            if full:
                events.append({
                    "ready": float(visual_completion[j]), "count": int(full),
                    "contributors": [j], "kind": "FULL", "key": self.batch_key[j],
                    "label": j,
                })

        rem_by_key: Dict[str, List[str]] = defaultdict(list)
        for j in selected_jobs:
            if self.qty[j] % 10 == 5:
                rem_by_key[self.batch_key[j]].append(j)
        for key, js in rem_by_key.items():
            js.sort(key=lambda j: (visual_completion[j], j))
            if len(js) % 2 and not allow_unmatched_remainders:
                raise RuntimeError(f"{self.name}: odd number of 5-belt remainders for key={key}")
            pairable = len(js) - (len(js) % 2)
            for k in range(0, pairable, 2):
                a, b = js[k], js[k + 1]
                events.append({
                    "ready": max(float(visual_completion[a]), float(visual_completion[b])),
                    "count": 1, "contributors": [a, b], "kind": "POOLED_5+5",
                    "key": key, "label": f"{a}+{b}",
                })

        events.sort(key=lambda x: (x["ready"], x["key"], x["kind"], x["label"]))
        job_done = {j: float(visual_completion[j]) for j in selected_jobs}
        if allow_unmatched_remainders:
            for key, js in rem_by_key.items():
                js_sorted = sorted(js, key=lambda j: (visual_completion[j], j))
                if len(js_sorted) % 2:
                    job_done[js_sorted[-1]] = math.inf
        rows: List[dict] = []
        t = 0.0
        win_idx = 0
        batch_seq = 0
        p = self.batch_processing_min
        windows = self.batch_windows

        def place_one(ready: float) -> Tuple[float, float, Window]:
            nonlocal t, win_idx
            candidate = max(t, ready)
            while win_idx < len(windows) and windows[win_idx].end <= candidate + EPS:
                win_idx += 1
            while win_idx < len(windows):
                w = windows[win_idx]
                s = max(candidate, w.start)
                if s + p <= w.end + EPS:
                    e = s + p
                    t = e
                    return s, e, w
                win_idx += 1
                if win_idx < len(windows):
                    candidate = max(candidate, windows[win_idx].start)
            raise RuntimeError(f"{self.name}: 2C horizon exhausted")

        for ev in events:
            for _ in range(int(ev["count"])):
                s, e, w = place_one(float(ev["ready"]))
                batch_seq += 1
                for j in ev["contributors"]:
                    if e > job_done[j]:
                        job_done[j] = e
                if keep_schedule:
                    rows.append({
                        "Event_Type": "2C_BATCH",
                        "Event_ID": f"B{batch_seq:05d}",
                        "Job_ID": ";".join(ev["contributors"]),
                        "Batch_Key": ev["key"],
                        "Batch_Composition": ev["kind"],
                        "Ready_Min": float(ev["ready"]),
                        "Start_Min": s,
                        "Completion_Min": e,
                        "Processing_Min": p,
                        "Station_ID": "WS_2C_BATCH",
                        "Resource_Units": "OP_2C",
                        "Window_ID": w.window_id,
                    })

        if not events:
            return 0.0, job_done, rows
        return float(t), job_done, rows

    def decode(
        self,
        chromosome: Sequence[str],
        keep_schedule: bool = False,
        validate_chromosome: bool = True,
    ) -> Tuple[dict, Optional[List[dict]]]:
        if validate_chromosome:
            if len(chromosome) != len(self.jobs) or set(chromosome) != self.job_set:
                raise ValueError("Chromosome must be a permutation of every selected Job_ID")

        busy: MutableMapping[str, List[ScheduledInterval]] = defaultdict(list)
        rows: Optional[List[dict]] = [] if keep_schedule else None
        visual_completion: Dict[str, float] = {}
        pre_start: Dict[str, float] = {}
        pre_completion: Dict[str, float] = {}

        previous_job: Optional[str] = None
        event_seq = 0

        for j in chromosome:
            # 2B auxiliary inspection.
            event_seq += 1
            p2b = self.proc[(j, "2B")]
            s2b, c2b = self._find_joint_slot(
                self.resource_by_op.get("2B", DEFAULT_RESOURCE_BY_OP["2B"]),
                max(self.release[j], self.loop[j]), p2b, busy,
            )
            self._reserve(self.resource_by_op.get("2B", DEFAULT_RESOURCE_BY_OP["2B"]), s2b, c2b, f"{j}:2B", busy)
            self._append_row(rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                             Operation_Code="2B", Ready_Min=max(self.release[j], self.loop[j]),
                             Start_Min=s2b, Completion_Min=c2b, Processing_Min=p2b,
                             Station_ID="WS_2B_INSPECTION", Resource_Units="OP_2B")

            # Composite sequence-dependent setup before successor PRE.
            # Confirmed clean-pipeline rule: on a Product_Code change, setup may
            # start only after the predecessor job's final belt has cleared 2C.
            setup_end = 0.0
            if previous_job is not None:
                setup_min = float(self.setup[self.product[previous_job]][self.product[j]])
                if setup_min > EPS:
                    _partial_cmax, partial_done, _ = self._schedule_batches(
                        visual_completion, keep_schedule=False,
                        jobs_subset=list(visual_completion.keys()),
                        allow_unmatched_remainders=True,
                    )
                    prev_clear = partial_done.get(previous_job, math.inf)
                    if not math.isfinite(prev_clear):
                        raise RuntimeError(
                            f"{self.name}: product change {previous_job}->{j} blocked: "
                            f"predecessor has an unmatched 5-belt 2C remainder"
                        )
                    event_seq += 1
                    ss, se = self._find_joint_slot(self.setup_resources, max(pre_completion[previous_job], prev_clear), setup_min, busy)
                    self._reserve(self.setup_resources, ss, se, f"SETUP:{previous_job}->{j}", busy)
                    setup_end = se
                    self._append_row(rows, Event_Type="SETUP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                                     Predecessor_Job_ID=previous_job, From_Product=self.product[previous_job],
                                     To_Product=self.product[j], Ready_Min=max(pre_completion[previous_job], prev_clear),
                                     Start_Min=ss, Completion_Min=se, Processing_Min=setup_min,
                                     Station_ID="WS_PRE_JIG", Resource_Units="OP_PRE;OP_2A;OP_PACK")
                else:
                    setup_end = pre_completion[previous_job]

            lag_2b_pre = float(self.prec[(j, "2B", "PRE")]["lag_min"])
            ready_pre = max(self.release[j], self.element[j], c2b + lag_2b_pre, setup_end)
            ppre = self.proc[(j, "PRE")]
            event_seq += 1
            spre, cpre = self._find_joint_slot(
                self.resource_by_op.get("PRE", DEFAULT_RESOURCE_BY_OP["PRE"]), ready_pre, ppre, busy
            )
            self._reserve(self.resource_by_op.get("PRE", DEFAULT_RESOURCE_BY_OP["PRE"]), spre, cpre, f"{j}:PRE", busy)
            pre_start[j], pre_completion[j] = spre, cpre
            self._append_row(rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                             Operation_Code="PRE", Ready_Min=ready_pre, Start_Min=spre,
                             Completion_Min=cpre, Processing_Min=ppre, Station_ID="WS_PRE_JIG",
                             Resource_Units="OP_PRE")

            # FINAL can start after first-belt streaming lag, but its completion may not precede PRE completion.
            pfinal = self.proc[(j, "FINAL")]
            rel = self.prec[(j, "PRE", "FINAL")]
            stream_lag = float(rel["lag_min"])
            ready_final = max(spre + stream_lag, cpre - pfinal)
            event_seq += 1
            sfin, cfin = self._find_joint_slot(
                self.resource_by_op.get("FINAL", DEFAULT_RESOURCE_BY_OP["FINAL"]), ready_final, pfinal, busy
            )
            self._reserve(self.resource_by_op.get("FINAL", DEFAULT_RESOURCE_BY_OP["FINAL"]), sfin, cfin, f"{j}:FINAL", busy)
            self._append_row(rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                             Operation_Code="FINAL", Ready_Min=ready_final, Start_Min=sfin,
                             Completion_Min=cfin, Processing_Min=pfinal, Station_ID="WS_FINAL",
                             Resource_Units="OP_FINAL", Relation_Type="SS_FIRST_BELT")

            # LASER and SCALE on shared OP_2A.
            prev_c = cfin
            for pred, code in (("FINAL", "LASER"), ("LASER", "SCALE")):
                lag = float(self.prec[(j, pred, code)]["lag_min"])
                p = self.proc[(j, code)]
                ready = prev_c + lag
                event_seq += 1
                s, c = self._find_joint_slot(
                    self.resource_by_op.get(code, DEFAULT_RESOURCE_BY_OP[code]), ready, p, busy
                )
                self._reserve(self.resource_by_op.get(code, DEFAULT_RESOURCE_BY_OP[code]), s, c, f"{j}:{code}", busy)
                station = "WS_LASER" if code == "LASER" else "WS_SCALE"
                self._append_row(rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                                 Operation_Code=code, Ready_Min=ready, Start_Min=s, Completion_Min=c,
                                 Processing_Min=p, Station_ID=station, Resource_Units="OP_2A")
                prev_c = c

            # OIL cycles. Entire cycle on WS_OIL; load prefix uses OP_2A and unload suffix OP_PACK.
            ready_oil = prev_c + float(self.prec[(j, "SCALE", "OIL")]["lag_min"])
            oil_end = ready_oil
            for cyc in self.oil_cycles[j]:
                load = float(cyc["w4_load_min"])
                auto = float(cyc["auto_dip_min"])
                unload = float(cyc["w5_unload_min"])
                s, e = self._find_oil_cycle_slot(oil_end, load, auto, unload, busy)
                cid = str(cyc["oil_cycle_id"])
                self._reserve(("WS_OIL",), s, e, cid, busy)
                self._reserve(("OP_2A",), s, s + load, cid + ":LOAD", busy)
                self._reserve(("OP_PACK",), e - unload, e, cid + ":UNLOAD", busy)
                event_seq += 1
                self._append_row(rows, Event_Type="OIL_CYCLE", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                                 Operation_Code="OIL", Oil_Cycle_ID=cid, Cycle_No=int(cyc["cycle_no"]),
                                 Cycle_Qty=int(cyc["cycle_qty"]), Ready_Min=oil_end, Start_Min=s,
                                 Completion_Min=e, Processing_Min=e-s, W4_Load_Min=load,
                                 Auto_Dip_Min=auto, W5_Unload_Min=unload, Station_ID="WS_OIL",
                                 Resource_Units="OP_2A(load);OP_PACK(unload)")
                oil_end = e

            # SEAL then VISUAL on shared OP_PACK.
            ready_seal = oil_end + float(self.prec[(j, "OIL", "SEAL")]["lag_min"])
            pseal = self.proc[(j, "SEAL")]
            event_seq += 1
            sseal, cseal = self._find_joint_slot(
                self.resource_by_op.get("SEAL", DEFAULT_RESOURCE_BY_OP["SEAL"]), ready_seal, pseal, busy
            )
            self._reserve(self.resource_by_op.get("SEAL", DEFAULT_RESOURCE_BY_OP["SEAL"]), sseal, cseal, f"{j}:SEAL", busy)
            self._append_row(rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                             Operation_Code="SEAL", Ready_Min=ready_seal, Start_Min=sseal,
                             Completion_Min=cseal, Processing_Min=pseal, Station_ID="WS_SEAL",
                             Resource_Units="OP_PACK")

            ready_vis = cseal + float(self.prec[(j, "SEAL", "VISUAL")]["lag_min"])
            pvis = self.proc[(j, "VISUAL")]
            event_seq += 1
            svis, cvis = self._find_joint_slot(
                self.resource_by_op.get("VISUAL", DEFAULT_RESOURCE_BY_OP["VISUAL"]), ready_vis, pvis, busy
            )
            self._reserve(self.resource_by_op.get("VISUAL", DEFAULT_RESOURCE_BY_OP["VISUAL"]), svis, cvis, f"{j}:VISUAL", busy)
            self._append_row(rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                             Operation_Code="VISUAL", Ready_Min=ready_vis, Start_Min=svis,
                             Completion_Min=cvis, Processing_Min=pvis, Station_ID="WS_PACK_VISUAL",
                             Resource_Units="OP_PACK")
            visual_completion[j] = cvis
            previous_job = j

        cmax, job_completion, batch_rows = self._schedule_batches(visual_completion, keep_schedule)
        if rows is not None:
            rows.extend(batch_rows)

        total_tard = 0.0
        tardy = 0
        total_flow = 0.0
        for j in self.jobs:
            c = job_completion[j]
            t = max(0.0, c - self.due[j])
            total_tard += t
            tardy += int(t > EPS)
            total_flow += c - self.release[j]

        metrics = {
            "makespan_min": float(cmax),
            "total_tardiness_min": float(total_tard),
            "tardy_jobs": int(tardy),
            "total_flow_time_min": float(total_flow),
        }
        return metrics, rows

    def decode_indices(self, chromosome_indices: Sequence[int]) -> Tuple[float, float, int, float]:
        """Metric-only GA decode. Infeasible permutations receive +inf fitness.

        V5 has a finite 30-day calendar, so deliberately poor product/setup orders
        can exhaust the horizon.  That is a legitimate infeasible chromosome, not
        a solver crash.
        """
        ids = [self.jobs[int(i)] for i in chromosome_indices]
        try:
            m, _ = self.decode(ids, keep_schedule=False, validate_chromosome=False)
        except RuntimeError:
            return (math.inf, math.inf, len(self.jobs), math.inf)
        return (
            float(m["makespan_min"]), float(m["total_tardiness_min"]),
            int(m["tardy_jobs"]), float(m["total_flow_time_min"]),
        )


def fitness_key(metrics: Mapping[str, float] | Sequence[float]) -> Tuple[float]:
    """V5 primary/only GA objective: Cmax."""
    if isinstance(metrics, Mapping):
        return (round(float(metrics["makespan_min"]), 9),)
    return (round(float(metrics[0]), 9),)


def compact_metrics_dict(metrics: Sequence[float]) -> dict:
    return {
        "makespan_min": float(metrics[0]),
        "total_tardiness_min": float(metrics[1]),
        "tardy_jobs": int(metrics[2]),
        "total_flow_time_min": float(metrics[3]),
    }
