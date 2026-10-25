#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast, semantics-preserving AL13 V5 decoder.

This module is a drop-in companion to ``benchmark_common.py``.  It deliberately
keeps the reference decoder untouched and optimizes only the implementation of
resource busy-interval handling and repeated dictionary lookups.

IMPORTANT
---------
The physical model is NOT simplified.  This decoder preserves the same:
* 2B auxiliary prerequisite stream;
* PRE -> FINAL SS_FIRST_BELT rule and final synchronization;
* shared OP_2A / OP_PACK worker constraints;
* explicit <=6-belt OIL cycles and non-preemptive resource calendars;
* exact 2C batches of 10 with same Batch_Key 5+5 pooling;
* sequence-dependent clean-pipeline setup rule;
* release times, calendars, downtime/break windows and Cmax objective.

The reference ``benchmark_common.V5ScheduleDecoder`` should remain available as
an oracle for regression-equivalence checks.  Use ``check_fast_decoder.py``
before adopting this decoder in production experiments.
"""
from __future__ import annotations

import bisect
import math
from collections import defaultdict
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .benchmark_common import (
    EPS,
    DEFAULT_RESOURCE_BY_OP,
    ScheduledInterval,
    V5ScheduleDecoder as ReferenceV5ScheduleDecoder,
    compact_metrics_dict,
    fitness_key,
    load_instance,
)


class _FastTimeline:
    """Sorted busy intervals with a persistent parallel starts array.

    The reference implementation reconstructs ``[x.start for x in intervals]``
    on every conflict query and every insertion.  This class stores that array
    once and updates it together with the interval list.  Scheduling semantics
    are unchanged.
    """

    __slots__ = ("intervals", "starts")

    def __init__(self) -> None:
        self.intervals: List[ScheduledInterval] = []
        self.starts: List[float] = []

    def conflict_end(self, start: float, end: float) -> Optional[float]:
        if not self.intervals:
            return None
        pos = bisect.bisect_right(self.starts, start) - 1
        if pos >= 0 and self.intervals[pos].end > start + EPS:
            return self.intervals[pos].end
        pos += 1
        if pos < len(self.intervals) and self.intervals[pos].start < end - EPS:
            return self.intervals[pos].end
        return None

    def insert(self, item: ScheduledInterval) -> None:
        pos = bisect.bisect_right(self.starts, item.start)
        self.starts.insert(pos, item.start)
        self.intervals.insert(pos, item)


class V5ScheduleDecoder(ReferenceV5ScheduleDecoder):
    """Semantically equivalent V5 decoder with faster busy-calendar handling."""

    def __init__(self, data: Mapping):
        super().__init__(data)

        # Cache hot-path resource tuples so decode does not repeatedly resolve
        # mappings/defaults.  Values are identical to the reference decoder.
        self._res_2b = tuple(self.resource_by_op.get("2B", DEFAULT_RESOURCE_BY_OP["2B"]))
        self._res_pre = tuple(self.resource_by_op.get("PRE", DEFAULT_RESOURCE_BY_OP["PRE"]))
        self._res_final = tuple(self.resource_by_op.get("FINAL", DEFAULT_RESOURCE_BY_OP["FINAL"]))
        self._res_laser = tuple(self.resource_by_op.get("LASER", DEFAULT_RESOURCE_BY_OP["LASER"]))
        self._res_scale = tuple(self.resource_by_op.get("SCALE", DEFAULT_RESOURCE_BY_OP["SCALE"]))
        self._res_seal = tuple(self.resource_by_op.get("SEAL", DEFAULT_RESOURCE_BY_OP["SEAL"]))
        self._res_visual = tuple(self.resource_by_op.get("VISUAL", DEFAULT_RESOURCE_BY_OP["VISUAL"]))

        # Cache immutable precedence lags used on every decode.
        self._lag_2b_pre = {j: float(self.prec[(j, "2B", "PRE")]["lag_min"]) for j in self.jobs}
        self._lag_pre_final = {j: float(self.prec[(j, "PRE", "FINAL")]["lag_min"]) for j in self.jobs}
        self._lag_final_laser = {j: float(self.prec[(j, "FINAL", "LASER")]["lag_min"]) for j in self.jobs}
        self._lag_laser_scale = {j: float(self.prec[(j, "LASER", "SCALE")]["lag_min"]) for j in self.jobs}
        self._lag_scale_oil = {j: float(self.prec[(j, "SCALE", "OIL")]["lag_min"]) for j in self.jobs}
        self._lag_oil_seal = {j: float(self.prec[(j, "OIL", "SEAL")]["lag_min"]) for j in self.jobs}
        self._lag_seal_visual = {j: float(self.prec[(j, "SEAL", "VISUAL")]["lag_min"]) for j in self.jobs}

        # Product setup lookup flattened for a faster hot-path access while
        # preserving every setup-matrix value exactly.
        self._setup_pair = {
            (a, b): float(v)
            for a, row in self.setup.items()
            for b, v in row.items()
        }

    @staticmethod
    def _timeline(busy: Mapping[str, _FastTimeline], rid: str) -> Optional[_FastTimeline]:
        return busy.get(rid)

    def _next_feasible_start(
        self,
        rid: str,
        ready: float,
        duration: float,
        busy: Mapping[str, _FastTimeline],
    ) -> Optional[Tuple[float, object]]:
        """Same algorithm as reference, avoiding repeated starts-list rebuilds."""
        windows = self.windows[rid]
        ends = self.window_ends[rid]
        wi = bisect.bisect_right(ends, ready + EPS)
        timeline = busy.get(rid)
        while wi < len(windows):
            w = windows[wi]
            s = max(ready, w.start)
            while s + duration <= w.end + EPS:
                ce = None if timeline is None else timeline.conflict_end(s, s + duration)
                if ce is None:
                    return s, w
                # Keep the reference decoder's exact advancement rule.
                s = max(s + 1e-6, ce)
            wi += 1
        return None

    def _find_joint_slot(
        self,
        resources: Sequence[str],
        ready: float,
        duration: float,
        busy: Mapping[str, _FastTimeline],
    ) -> Tuple[float, float]:
        # Intentionally mirrors the reference implementation exactly.
        candidate = max(0.0, float(ready))
        for _ in range(200000):
            if candidate + duration > self.horizon + EPS:
                break
            starts: List[float] = []
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
            return s, s + duration
        raise RuntimeError(
            f"{self.name}: no joint slot resources={tuple(resources)} "
            f"ready={ready:.3f} duration={duration:.3f}"
        )

    def _reserve(
        self,
        resources: Sequence[str],
        start: float,
        end: float,
        label: str,
        busy: MutableMapping[str, _FastTimeline],
    ) -> None:
        for rid in resources:
            busy[rid].insert(ScheduledInterval(start, end, label))

    def _find_oil_cycle_slot(
        self,
        ready: float,
        load_min: float,
        auto_min: float,
        unload_min: float,
        busy: Mapping[str, _FastTimeline],
    ) -> Tuple[float, float]:
        # Intentionally mirrors the reference implementation exactly.
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
        raise RuntimeError(
            f"{self.name}: no feasible OIL cycle slot ready={ready:.3f} duration={duration:.3f}"
        )

    def decode(
        self,
        chromosome: Sequence[str],
        keep_schedule: bool = False,
        validate_chromosome: bool = True,
    ):
        """Reference-equivalent decode with fast busy timelines.

        The body follows ``benchmark_common.V5ScheduleDecoder.decode`` in the
        same event order.  Only data access and busy-interval storage differ.
        """
        if validate_chromosome:
            if len(chromosome) != len(self.jobs) or set(chromosome) != self.job_set:
                raise ValueError("Chromosome must be a permutation of every selected Job_ID")

        busy: MutableMapping[str, _FastTimeline] = defaultdict(_FastTimeline)
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
            ready_2b = max(self.release[j], self.loop[j])
            s2b, c2b = self._find_joint_slot(self._res_2b, ready_2b, p2b, busy)
            self._reserve(self._res_2b, s2b, c2b, f"{j}:2B", busy)
            self._append_row(
                rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                Operation_Code="2B", Ready_Min=ready_2b, Start_Min=s2b,
                Completion_Min=c2b, Processing_Min=p2b, Station_ID="WS_2B_INSPECTION",
                Resource_Units="OP_2B",
            )

            # Composite sequence-dependent setup before successor PRE.
            setup_end = 0.0
            if previous_job is not None:
                setup_min = self._setup_pair[(self.product[previous_job], self.product[j])]
                if setup_min > EPS:
                    # Keep the reference prefix 2C reconstruction unchanged.
                    _partial_cmax, partial_done, _ = self._schedule_batches(
                        visual_completion,
                        keep_schedule=False,
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
                    setup_ready = max(pre_completion[previous_job], prev_clear)
                    ss, se = self._find_joint_slot(self.setup_resources, setup_ready, setup_min, busy)
                    self._reserve(self.setup_resources, ss, se, f"SETUP:{previous_job}->{j}", busy)
                    setup_end = se
                    self._append_row(
                        rows, Event_Type="SETUP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                        Predecessor_Job_ID=previous_job, From_Product=self.product[previous_job],
                        To_Product=self.product[j], Ready_Min=setup_ready,
                        Start_Min=ss, Completion_Min=se, Processing_Min=setup_min,
                        Station_ID="WS_PRE_JIG", Resource_Units="OP_PRE;OP_2A;OP_PACK",
                    )
                else:
                    setup_end = pre_completion[previous_job]

            ready_pre = max(self.release[j], self.element[j], c2b + self._lag_2b_pre[j], setup_end)
            ppre = self.proc[(j, "PRE")]
            event_seq += 1
            spre, cpre = self._find_joint_slot(self._res_pre, ready_pre, ppre, busy)
            self._reserve(self._res_pre, spre, cpre, f"{j}:PRE", busy)
            pre_start[j], pre_completion[j] = spre, cpre
            self._append_row(
                rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                Operation_Code="PRE", Ready_Min=ready_pre, Start_Min=spre,
                Completion_Min=cpre, Processing_Min=ppre, Station_ID="WS_PRE_JIG",
                Resource_Units="OP_PRE",
            )

            # FINAL with SS_FIRST_BELT + final synchronization.
            pfinal = self.proc[(j, "FINAL")]
            ready_final = max(spre + self._lag_pre_final[j], cpre - pfinal)
            event_seq += 1
            sfin, cfin = self._find_joint_slot(self._res_final, ready_final, pfinal, busy)
            self._reserve(self._res_final, sfin, cfin, f"{j}:FINAL", busy)
            self._append_row(
                rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                Operation_Code="FINAL", Ready_Min=ready_final, Start_Min=sfin,
                Completion_Min=cfin, Processing_Min=pfinal, Station_ID="WS_FINAL",
                Resource_Units="OP_FINAL", Relation_Type="SS_FIRST_BELT",
            )

            # LASER then SCALE on shared OP_2A.
            prev_c = cfin
            p = self.proc[(j, "LASER")]
            ready = prev_c + self._lag_final_laser[j]
            event_seq += 1
            s, c = self._find_joint_slot(self._res_laser, ready, p, busy)
            self._reserve(self._res_laser, s, c, f"{j}:LASER", busy)
            self._append_row(
                rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                Operation_Code="LASER", Ready_Min=ready, Start_Min=s, Completion_Min=c,
                Processing_Min=p, Station_ID="WS_LASER", Resource_Units="OP_2A",
            )
            prev_c = c

            p = self.proc[(j, "SCALE")]
            ready = prev_c + self._lag_laser_scale[j]
            event_seq += 1
            s, c = self._find_joint_slot(self._res_scale, ready, p, busy)
            self._reserve(self._res_scale, s, c, f"{j}:SCALE", busy)
            self._append_row(
                rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                Operation_Code="SCALE", Ready_Min=ready, Start_Min=s, Completion_Min=c,
                Processing_Min=p, Station_ID="WS_SCALE", Resource_Units="OP_2A",
            )
            prev_c = c

            # OIL cycles: full WS_OIL interval + OP_2A load + OP_PACK unload.
            ready_oil = prev_c + self._lag_scale_oil[j]
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
                self._append_row(
                    rows, Event_Type="OIL_CYCLE", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                    Operation_Code="OIL", Oil_Cycle_ID=cid, Cycle_No=int(cyc["cycle_no"]),
                    Cycle_Qty=int(cyc["cycle_qty"]), Ready_Min=oil_end, Start_Min=s,
                    Completion_Min=e, Processing_Min=e - s, W4_Load_Min=load,
                    Auto_Dip_Min=auto, W5_Unload_Min=unload, Station_ID="WS_OIL",
                    Resource_Units="OP_2A(load);OP_PACK(unload)",
                )
                oil_end = e

            # SEAL then VISUAL on shared OP_PACK.
            ready_seal = oil_end + self._lag_oil_seal[j]
            pseal = self.proc[(j, "SEAL")]
            event_seq += 1
            sseal, cseal = self._find_joint_slot(self._res_seal, ready_seal, pseal, busy)
            self._reserve(self._res_seal, sseal, cseal, f"{j}:SEAL", busy)
            self._append_row(
                rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                Operation_Code="SEAL", Ready_Min=ready_seal, Start_Min=sseal,
                Completion_Min=cseal, Processing_Min=pseal, Station_ID="WS_SEAL",
                Resource_Units="OP_PACK",
            )

            ready_vis = cseal + self._lag_seal_visual[j]
            pvis = self.proc[(j, "VISUAL")]
            event_seq += 1
            svis, cvis = self._find_joint_slot(self._res_visual, ready_vis, pvis, busy)
            self._reserve(self._res_visual, svis, cvis, f"{j}:VISUAL", busy)
            self._append_row(
                rows, Event_Type="JOB_OP", Event_ID=f"E{event_seq:05d}", Job_ID=j,
                Operation_Code="VISUAL", Ready_Min=ready_vis, Start_Min=svis,
                Completion_Min=cvis, Processing_Min=pvis, Station_ID="WS_PACK_VISUAL",
                Resource_Units="OP_PACK",
            )
            visual_completion[j] = cvis
            previous_job = j

        # Exact same 2C scheduler from the reference class.
        cmax, job_completion, batch_rows = self._schedule_batches(visual_completion, keep_schedule)
        if rows is not None:
            rows.extend(batch_rows)

        total_tard = 0.0
        tardy = 0
        total_flow = 0.0
        for j in self.jobs:
            completion = job_completion[j]
            t = max(0.0, completion - self.due[j])
            total_tard += t
            tardy += int(t > EPS)
            total_flow += completion - self.release[j]

        metrics = {
            "makespan_min": float(cmax),
            "total_tardiness_min": float(total_tard),
            "tardy_jobs": int(tardy),
            "total_flow_time_min": float(total_flow),
        }
        return metrics, rows


# Export the same public helper names as benchmark_common.py.
__all__ = [
    "V5ScheduleDecoder",
    "compact_metrics_dict",
    "fitness_key",
    "load_instance",
]
