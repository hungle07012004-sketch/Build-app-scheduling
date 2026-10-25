#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared utilities for the AL13 fixed-instance benchmark pipeline.

PERFORMANCE-OPTIMIZED VERSION
-----------------------------
This module preserves the frozen-instance schema, feasibility logic, decoding
rule, and objective values of the original implementation while reducing the
hot-loop cost of the GA decoder.

The main acceleration is used for benchmark instances where the generator has
already proved reusable resources redundant under workstation capacity (the
BASE AL13 benchmark case).  In that path the decoder:

* maps jobs, operations, and stations to integer indices once at construction;
* precomputes release/component/due/lag/option arrays;
* stores station windows as parallel numeric lists;
* stores workstation busy intervals as parallel sorted start/end lists;
* performs candidate comparison without allocating candidate tuples/lists;
* avoids string/dictionary lookup inside the 10-operation inner loop;
* exposes ``decode_indexed_compact`` for the GA so 50-job chromosomes can be
  represented by compact integer/byte permutations.

The generic/resource-aware path is retained for compatibility and correctness.
MILP and GA still consume exactly the same frozen JSON instance.
"""
from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

OP_SEQUENCE = ["2B", "PRE", "FINAL", "2A", "LASER", "SCALE", "OIL", "SEAL", "PACK", "2C"]
PREDECESSOR = {
    "PRE": "2B",
    "FINAL": "PRE",
    "2A": "FINAL",
    "LASER": "2A",
    "SCALE": "LASER",
    "OIL": "SCALE",
    "SEAL": "OIL",
    "PACK": "SEAL",
    "2C": "PACK",
}

_EPS = 1e-9
_ADVANCE_EPS = 1e-4
_INF_EVENT = 2**63 - 1
_OP_INDEX = {op: i for i, op in enumerate(OP_SEQUENCE)}


def load_instance(path: Path | str) -> dict:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    validate_instance_schema(data, source=path)
    return data


def validate_instance_schema(data: Mapping, source: Optional[Path] = None) -> None:
    required = [
        "metadata",
        "jobs",
        "operation_options",
        "precedence",
        "station_windows",
        "resource_requirements",
        "resource_units_by_type",
        "resource_capacity_by_shift",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        loc = f" in {source}" if source else ""
        raise ValueError(f"Frozen benchmark instance{loc} is missing keys: {missing}")

    size = int(data["metadata"]["size"])
    if len(data["jobs"]) != size:
        raise ValueError(f"metadata.size={size}, but jobs contains {len(data['jobs'])} records")

    ids = {str(j["job_id"]) for j in data["jobs"]}
    if len(ids) != size:
        raise ValueError("Job_ID values are not unique inside the frozen instance")


def instance_name(data: Mapping) -> str:
    return str(data["metadata"]["instance_name"])


def instance_size(data: Mapping) -> int:
    return int(data["metadata"]["size"])


def job_ids(data: Mapping) -> List[str]:
    return [str(j["job_id"]) for j in data["jobs"]]


@dataclass(frozen=True)
class NWindow:
    start: float
    end: float
    calendar_date: str
    shift_id: str
    station: str
    wid: str


def first_conflict_end(intervals: List[Tuple[float, float, int]], start: float, end: float) -> Optional[float]:
    """Return the end of the first interval overlapping [start, end), else None.

    This public helper is retained for compatibility with the generic/resource
    path.  The BASE fast path uses parallel numeric lists instead.
    """
    if not intervals:
        return None
    i = bisect.bisect_right(intervals, (start, math.inf, _INF_EVENT)) - 1
    if i >= 0 and intervals[i][1] > start + _EPS:
        return intervals[i][1]
    i += 1
    if i < len(intervals) and intervals[i][0] < end - _EPS:
        return intervals[i][1]
    return None


def insert_interval(intervals: List[Tuple[float, float, int]], item: Tuple[float, float, int]) -> None:
    bisect.insort(intervals, item)


class FrozenInstanceDecoder:
    """Feasibility-aware earliest-finish decoder used by GA and MILP UB.

    Public string-keyed attributes are preserved for compatibility.  Numeric
    mirrors prefixed with ``_`` drive the optimized GA hot path.
    """

    def __init__(self, data: Mapping):
        validate_instance_schema(data)
        self.data = data
        self.name = instance_name(data)
        self.job_ids = job_ids(data)
        self.job_id_set = frozenset(self.job_ids)
        self.job_info = {str(j["job_id"]): dict(j) for j in data["jobs"]}

        # Public dictionaries retained for existing callers / MILP code.
        self.release = {j: float(self.job_info[j]["release_min"]) for j in self.job_ids}
        self.element = {j: float(self.job_info[j]["element_ready_min"]) for j in self.job_ids}
        self.loop = {j: float(self.job_info[j]["loopset_release_min"]) for j in self.job_ids}
        self.due = {j: float(self.job_info[j]["due_min"]) for j in self.job_ids}
        self.priority = {j: int(self.job_info[j].get("priority", 999)) for j in self.job_ids}

        # Job-index mirrors: index order is exactly data['jobs'] / self.job_ids.
        self.job_index = {j: i for i, j in enumerate(self.job_ids)}
        self._release = [self.release[j] for j in self.job_ids]
        self._element = [self.element[j] for j in self.job_ids]
        self._loop = [self.loop[j] for j in self.job_ids]
        self._due = [self.due[j] for j in self.job_ids]
        self._priority = [self.priority[j] for j in self.job_ids]

        # Public operation options preserve original JSON insertion order.
        self.options: Dict[Tuple[str, str], List[Tuple[str, float]]] = defaultdict(list)
        for r in data["operation_options"]:
            if int(r.get("eligible", 1)) == 1:
                self.options[(str(r["job_id"]), str(r["operation_code"]))].append(
                    (str(r["station_id"]), float(r["processing_min"]))
                )

        # Public precedence dictionary retained.
        self.lag: Dict[Tuple[str, str, str], float] = {}
        for r in data["precedence"]:
            self.lag[(str(r["job_id"]), str(r["pred"]), str(r["succ"]))] = float(r["lag_min"])

        # Build station windows exactly as before.
        self.station_windows: Dict[str, List[NWindow]] = {}
        self.station_window_ends: Dict[str, List[float]] = {}
        for sid, rows in data["station_windows"].items():
            sid_s = str(sid)
            arr = [
                NWindow(
                    start=float(w["start_min"]),
                    end=float(w["end_min"]),
                    calendar_date=str(w["calendar_date"]),
                    shift_id=str(w["shift_id"]),
                    station=sid_s,
                    wid=str(w["window_id"]),
                )
                for w in rows
            ]
            arr.sort(key=lambda w: (w.start, w.end, w.wid))
            self.station_windows[sid_s] = arr
            self.station_window_ends[sid_s] = [w.end for w in arr]

        # Integer station indexing and parallel numeric window arrays.
        self.station_ids = list(self.station_windows.keys())
        self.station_index = {sid: i for i, sid in enumerate(self.station_ids)}
        self._window_objects: List[List[NWindow]] = [self.station_windows[sid] for sid in self.station_ids]
        self._window_starts: List[List[float]] = [[w.start for w in ws] for ws in self._window_objects]
        self._window_ends: List[List[float]] = [[w.end for w in ws] for ws in self._window_objects]

        # Precompute operation options by [job_index][op_index].
        # Station_ID is converted to its lexicographic rank, which is exactly
        # equivalent to the original final tie-break on station_id strings.
        n_jobs = len(self.job_ids)
        station_lex_rank = {sid: rank for rank, sid in enumerate(sorted(self.station_ids))}
        self._options_idx: List[List[List[Tuple[int, float, int]]]] = [
            [[] for _ in OP_SEQUENCE] for _ in range(n_jobs)
        ]
        for ji, j in enumerate(self.job_ids):
            for oi, op in enumerate(OP_SEQUENCE):
                opts = self.options.get((j, op), ())
                if not opts:
                    raise ValueError(f"Instance {self.name}: no eligible station option for {j}/{op}")
                self._options_idx[ji][oi] = [
                    (self.station_index[sid], float(p), station_lex_rank[sid]) for sid, p in opts
                ]

        # Precompute predecessor lags by [job_index][successor_op_index].
        # index 0 (2B) has no predecessor and remains 0.0.
        self._lags: List[List[float]] = [[0.0] * len(OP_SEQUENCE) for _ in range(n_jobs)]
        for ji, j in enumerate(self.job_ids):
            for oi in range(1, len(OP_SEQUENCE)):
                succ = OP_SEQUENCE[oi]
                pred = PREDECESSOR[succ]
                self._lags[ji][oi] = self.lag[(j, pred, succ)]

        # Precompute SPT workload used by GA initial population.
        self.min_workload_by_index = [
            sum(min(p for _si, p, _rank in self._options_idx[ji][oi]) for oi in range(len(OP_SEQUENCE)))
            for ji in range(n_jobs)
        ]

        self.resources_redundant = bool(
            data.get("metadata", {}).get("resource_constraints_redundant_under_station_capacity", False)
        )
        self.use_resources = not self.resources_redundant

        # Generic resource structures retained for non-redundant scenarios.
        self.reqs: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        self.units_by_type: Dict[str, List[str]] = {}
        self.cap: Dict[Tuple[str, str, str], int] = {}
        self.base_cap: Dict[str, int] = {}
        self.active_by_window_rt: Dict[Tuple[str, str], List[str]] = {}

        if self.use_resources:
            for op, reqs in data["resource_requirements"].items():
                self.reqs[str(op)] = [
                    (str(r["resource_type"]), int(r["required_qty"])) for r in reqs
                ]

            self.units_by_type = {
                str(rt): [str(u) for u in units]
                for rt, units in data["resource_units_by_type"].items()
            }

            for r in data["resource_capacity_by_shift"]:
                self.cap[(str(r["calendar_date"]), str(r["shift_id"]), str(r["resource_type"]))] = int(
                    r["available_qty"]
                )

            self.base_cap = {
                str(k): int(v) for k, v in data.get("base_resource_capacity", {}).items()
            }

            all_rts = list(self.units_by_type)
            for _sid, windows in self.station_windows.items():
                for w in windows:
                    for rt in all_rts:
                        default_cap = self.base_cap.get(rt, len(self.units_by_type[rt]))
                        c = self.cap.get((w.calendar_date, w.shift_id, rt), default_cap)
                        self.active_by_window_rt[(w.wid, rt)] = self.units_by_type[rt][: max(0, c)]

    # ------------------------------------------------------------------
    # Numeric BASE fast path
    # ------------------------------------------------------------------
    def _find_slot_noresources_idx(
        self,
        station_idx: int,
        ready: float,
        duration: float,
        busy_starts: List[List[float]],
        busy_ends: List[List[float]],
    ) -> Optional[Tuple[float, float, int]]:
        """Fast exact equivalent of find_slot for the no-resource BASE path."""
        w_starts = self._window_starts[station_idx]
        w_ends = self._window_ends[station_idx]
        wi = bisect.bisect_right(w_ends, ready + _EPS)

        bs = busy_starts[station_idx]
        be = busy_ends[station_idx]
        nbusy = len(bs)

        # Bind bisect locally in this very hot routine.
        bisect_right = bisect.bisect_right

        while wi < len(w_starts):
            ws = w_starts[wi]
            we = w_ends[wi]
            candidate = ready if ready >= ws else ws

            while candidate + duration <= we + _EPS:
                finish = candidate + duration

                # Equivalent to first_conflict_end on sorted (start,end,event).
                pos = bisect_right(bs, candidate)
                prev = pos - 1
                if prev >= 0 and be[prev] > candidate + _EPS:
                    conflict_end = be[prev]
                elif pos < nbusy and bs[pos] < finish - _EPS:
                    conflict_end = be[pos]
                else:
                    return candidate, finish, wi

                advanced = candidate + _ADVANCE_EPS
                candidate = conflict_end if conflict_end > advanced else advanced

            wi += 1

        return None

    @staticmethod
    def _insert_busy(
        starts: List[float], ends: List[float], start: float, end: float
    ) -> None:
        # On a unit-capacity station two accepted intervals cannot share a start
        # while having positive duration, so ordering by start is sufficient and
        # is equivalent to tuple-insort for all feasible decoder states.
        pos = bisect.bisect_right(starts, start)
        starts.insert(pos, start)
        ends.insert(pos, end)

    def decode_indexed_compact(
        self,
        chromosome_indices: Sequence[int],
        validate_chromosome: bool = False,
    ) -> Tuple[float, float, int, float]:
        """Decode an integer-index permutation and return compact metrics.

        Returns ``(makespan, total_tardiness, tardy_jobs, total_flow_time)``.
        For BASE instances this is the GA hot path and performs no string-keyed
        lookup or Python helper-function call inside the station-option loop.
        """
        n_jobs = len(self.job_ids)
        if validate_chromosome:
            if len(chromosome_indices) != n_jobs or set(chromosome_indices) != set(range(n_jobs)):
                raise ValueError("Chromosome must be a permutation of every job index")

        # Non-BASE fallback remains exact by using the generic decoder.
        if self.use_resources:
            ids = [self.job_ids[i] for i in chromosome_indices]
            m, _ = self._decode_generic(ids, keep_schedule=False)
            return (
                float(m["makespan_min"]),
                float(m["total_tardiness_min"]),
                int(m["tardy_jobs"]),
                float(m["total_flow_time_min"]),
            )

        nstations = len(self.station_ids)
        busy_starts: List[List[float]] = [[] for _ in range(nstations)]
        busy_ends: List[List[float]] = [[] for _ in range(nstations)]

        # Local bindings avoid repeated attribute/global lookup in the 500-op loop.
        release = self._release
        element = self._element
        loop = self._loop
        due = self._due
        lags = self._lags
        options = self._options_idx
        window_starts = self._window_starts
        window_ends = self._window_ends
        bisect_right = bisect.bisect_right
        eps = _EPS
        advance_eps = _ADVANCE_EPS

        cmax = -math.inf
        total_tardiness = 0.0
        tardy_jobs = 0
        total_flow = 0.0

        for ji in chromosome_indices:
            prev_completion = 0.0
            rel = release[ji]
            loop_ready = loop[ji]
            elem_ready = element[ji]
            job_lags = lags[ji]
            job_options = options[ji]

            # AL13 route is fixed at 10 operations.
            for oi in range(10):
                if oi == 0:  # 2B
                    ready = loop_ready if loop_ready >= rel else rel
                elif oi == 1:  # PRE
                    r3 = prev_completion + job_lags[1]
                    ready = elem_ready
                    if rel > ready:
                        ready = rel
                    if r3 > ready:
                        ready = r3
                else:
                    ready = prev_completion + job_lags[oi]

                best_e = math.inf
                best_s = math.inf
                best_si = -1
                best_rank = 2**31 - 1

                # Inline the no-resource find-slot logic.  This removes ~900
                # Python method calls per 50-job chromosome while keeping the
                # exact same window/conflict arithmetic and tie-breaking.
                for si, p, station_rank in job_options[oi]:
                    w_starts = window_starts[si]
                    w_ends = window_ends[si]
                    wi = bisect_right(w_ends, ready + eps)

                    bs = busy_starts[si]
                    be = busy_ends[si]
                    nbusy = len(bs)
                    found = False
                    s = e = 0.0

                    while wi < len(w_starts):
                        ws = w_starts[wi]
                        we = w_ends[wi]
                        candidate = ready if ready >= ws else ws

                        while candidate + p <= we + eps:
                            finish = candidate + p
                            pos = bisect_right(bs, candidate)
                            prev = pos - 1
                            if prev >= 0 and be[prev] > candidate + eps:
                                conflict_end = be[prev]
                            elif pos < nbusy and bs[pos] < finish - eps:
                                conflict_end = be[pos]
                            else:
                                s = candidate
                                e = finish
                                found = True
                                break

                            advanced = candidate + advance_eps
                            candidate = conflict_end if conflict_end > advanced else advanced

                        if found:
                            break
                        wi += 1

                    if not found:
                        continue

                    # Exact equivalent of min(candidates, key=(e,s,station_id)).
                    # station_rank is the lexicographic rank of station_id.
                    if (
                        e < best_e
                        or (e == best_e and (s < best_s or (s == best_s and station_rank < best_rank)))
                    ):
                        best_e = e
                        best_s = s
                        best_si = si
                        best_rank = station_rank

                if best_si < 0:
                    j = self.job_ids[ji]
                    raise RuntimeError(f"Instance {self.name}: no feasible assignment for {j}/{OP_SEQUENCE[oi]}")

                # Inline sorted insertion into the chosen station's busy lists.
                bs = busy_starts[best_si]
                be = busy_ends[best_si]
                pos = bisect_right(bs, best_s)
                bs.insert(pos, best_s)
                be.insert(pos, best_e)
                prev_completion = best_e

            c = prev_completion
            if c > cmax:
                cmax = c
            tard = c - due[ji]
            if tard > 0.0:
                total_tardiness += tard
                if tard > eps:
                    tardy_jobs += 1
            total_flow += c - rel

        return float(cmax), float(total_tardiness), int(tardy_jobs), float(total_flow)

    def decode_compact(
        self,
        chromosome: Sequence[str],
        validate_chromosome: bool = True,
    ) -> Tuple[float, float, int, float]:
        """String-chromosome compact decode; compatibility convenience API."""
        if validate_chromosome:
            if len(chromosome) != len(self.job_ids) or set(chromosome) != self.job_id_set:
                raise ValueError("Chromosome must be a permutation of every Job_ID in the frozen instance")
        idx = [self.job_index[j] for j in chromosome]
        return self.decode_indexed_compact(idx, validate_chromosome=False)

    # ------------------------------------------------------------------
    # Public / generic compatibility path
    # ------------------------------------------------------------------
    def find_slot(
        self,
        station: str,
        ready: float,
        duration: float,
        requirements: Sequence[Tuple[str, int]],
        station_busy: MutableMapping[str, List[Tuple[float, float, int]]],
        resource_busy: MutableMapping[str, List[Tuple[float, float, int]]],
    ):
        windows = self.station_windows[station]
        start_idx = bisect.bisect_right(self.station_window_ends[station], ready + _EPS)

        for w in windows[start_idx:]:
            candidate = max(ready, w.start)
            while candidate + duration <= w.end + _EPS:
                finish = candidate + duration
                ce = first_conflict_end(station_busy[station], candidate, finish)

                if not self.use_resources:
                    if ce is None:
                        return candidate, finish, w, None
                    candidate = max(candidate + _ADVANCE_EPS, ce)
                    continue

                advance: List[float] = []
                if ce is not None:
                    advance.append(ce)

                selected: Dict[str, List[str]] = {}
                resource_ok = True
                for rt, q in requirements:
                    active = self.active_by_window_rt.get((w.wid, rt), self.units_by_type.get(rt, []))
                    free: List[str] = []
                    block_ends: List[float] = []
                    for uid in active:
                        ceu = first_conflict_end(resource_busy[uid], candidate, finish)
                        if ceu is None:
                            free.append(uid)
                            if len(free) >= q:
                                break
                        else:
                            block_ends.append(ceu)
                    if len(free) < q:
                        resource_ok = False
                        if len(active) < q:
                            advance.append(w.end)
                        elif block_ends:
                            advance.append(min(block_ends))
                        else:
                            advance.append(w.end)
                    else:
                        selected[rt] = free[:q]

                if ce is None and resource_ok:
                    return candidate, finish, w, selected

                candidate = max(candidate + _ADVANCE_EPS, max(advance) if advance else candidate + _ADVANCE_EPS)

        raise RuntimeError(
            f"Instance {self.name}: no feasible slot station={station}, ready={ready:.2f}, duration={duration:.2f}"
        )

    def _decode_generic(self, chromosome: Sequence[str], keep_schedule: bool = False):
        """Original decoder logic retained for schedule/resource compatibility."""
        station_busy: MutableMapping[str, List[Tuple[float, float, int]]] = defaultdict(list)
        resource_busy: MutableMapping[str, List[Tuple[float, float, int]]] = defaultdict(list)
        final: Dict[str, float] = {}
        rows: List[dict] = []
        seq = 0

        for job in chromosome:
            prev_completion: Optional[float] = None

            for code in OP_SEQUENCE:
                if code == "2B":
                    ready = max(self.loop[job], self.release[job])
                elif code == "PRE":
                    ready = max(
                        self.element[job],
                        self.release[job],
                        float(prev_completion) + self.lag[(job, "2B", "PRE")],
                    )
                else:
                    pred = PREDECESSOR[code]
                    ready = float(prev_completion) + self.lag[(job, pred, code)]

                candidates = []
                requirements = self.reqs.get(code, ()) if self.use_resources else ()
                for station, p in self.options[(job, code)]:
                    try:
                        s, e, w, res = self.find_slot(
                            station, ready, p, requirements, station_busy, resource_busy
                        )
                    except RuntimeError:
                        continue
                    candidates.append((e, s, station, p, w, res))

                if not candidates:
                    raise RuntimeError(f"Instance {self.name}: no feasible assignment for {job}/{code}")

                e, s, station, p, w, res = min(candidates, key=lambda z: (z[0], z[1], z[2]))
                seq += 1
                event_id = seq
                insert_interval(station_busy[station], (s, e, event_id))

                assigned: List[str] = []
                if self.use_resources and res:
                    for uids in res.values():
                        for uid in uids:
                            insert_interval(resource_busy[uid], (s, e, event_id))
                            if keep_schedule:
                                assigned.append(uid)

                prev_completion = e

                if keep_schedule:
                    rows.append(
                        {
                            "Event_ID": f"E{seq:05d}",
                            "Job_ID": job,
                            "Operation_Code": code,
                            "Station_ID": station,
                            "Window_ID": w.wid,
                            "Calendar_Date": w.calendar_date,
                            "Shift_ID": w.shift_id,
                            "Ready_Min": ready,
                            "Start_Min": s,
                            "Completion_Min": e,
                            "Processing_Min": p,
                            "Resource_Units": ";".join(assigned),
                        }
                    )

            final[job] = float(prev_completion)

        cmax = max(final.values())
        total_tardiness = 0.0
        tardy_jobs = 0
        total_flow = 0.0
        for j, c in final.items():
            t = max(0.0, c - self.due[j])
            total_tardiness += t
            tardy_jobs += int(t > _EPS)
            total_flow += c - self.release[j]

        metrics = {
            "makespan_min": float(cmax),
            "total_tardiness_min": float(total_tardiness),
            "tardy_jobs": int(tardy_jobs),
            "total_flow_time_min": float(total_flow),
        }
        return metrics, rows if keep_schedule else None

    def decode(
        self,
        chromosome: Sequence[str],
        keep_schedule: bool = False,
        validate_chromosome: bool = True,
    ):
        if validate_chromosome:
            if len(chromosome) != len(self.job_ids) or set(chromosome) != self.job_id_set:
                raise ValueError("Chromosome must be a permutation of every Job_ID in the frozen instance")

        # The GA metric-only BASE path is numeric and allocation-light.
        if not keep_schedule and not self.use_resources:
            idx = [self.job_index[j] for j in chromosome]
            cmax, tard, tardy_jobs, flow = self.decode_indexed_compact(idx, validate_chromosome=False)
            return {
                "makespan_min": cmax,
                "total_tardiness_min": tard,
                "tardy_jobs": tardy_jobs,
                "total_flow_time_min": flow,
            }, None

        # Final schedule construction and binding-resource cases use the exact
        # generic implementation so output rows stay byte-for-byte compatible in
        # field semantics with the prior version.
        return self._decode_generic(chromosome, keep_schedule=keep_schedule)


def chromosome_key(metrics: Mapping[str, float]) -> Tuple[float, float, float]:
    """Lexicographic fitness: Cmax first, then tardiness, then flow time."""
    return (
        round(float(metrics["makespan_min"]), 9),
        round(float(metrics.get("total_tardiness_min", 0.0)), 9),
        round(float(metrics.get("total_flow_time_min", 0.0)), 9),
    )


def compact_chromosome_key(metrics: Sequence[float]) -> Tuple[float, float, float]:
    """Same key as chromosome_key for compact metric tuples.

    ``metrics`` = (makespan, total_tardiness, tardy_jobs, total_flow_time).
    """
    return (
        round(float(metrics[0]), 9),
        round(float(metrics[1]), 9),
        round(float(metrics[3]), 9),
    )


def compact_metrics_dict(metrics: Sequence[float]) -> dict:
    return {
        "makespan_min": float(metrics[0]),
        "total_tardiness_min": float(metrics[1]),
        "tardy_jobs": int(metrics[2]),
        "total_flow_time_min": float(metrics[3]),
    }
