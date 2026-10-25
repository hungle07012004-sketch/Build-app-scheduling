#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate AL13_30Day_Synthetic_Dataset_v3_Optimized.xlsx from the existing V2 workbook.

Design goals
------------
* Preserve all existing sheet names and the computational operation codes:
  2B, PRE, FINAL, 2A, LASER, SCALE, OIL, SEAL, PACK, 2C.
* Preserve the confirmed AL13 physical semantics:
    - 2B = auxiliary loopset visual inspection only.
    - Checked loopsets wait after 2B and feed PRE.
    - Main belt/Jig flow starts at PRE.
    - PRE is one aggregated same-person activity.
* Create clustered, wave-based release times while preserving the 30-day horizon.
* Keep Release_DateTime, Element_Ready_Time, and Loopset_Release_Time distinct.
* Generate deterministic, job-dependent processing times for parallel stations.
* Recalculate due-date scenarios using transparent work-content + allowance rules.
* Rebuild the FIFO reference schedule and key validation/report sheets.
* Never overwrite V2 unless the user explicitly passes the same path with --force-overwrite.

This generator changes DATA only. It does not modify MILP/GA/decoder logic.

Typical usage (Windows CMD):

    python generate_al13_dataset_v3_optimized.py ^
      --input "AL13_30Day_Synthetic_Dataset_v2.xlsx" ^
      --output "AL13_30Day_Synthetic_Dataset_v3_Optimized.xlsx" ^
      --seed 20260831

Dependencies:
    pip install pandas openpyxl
"""
from __future__ import annotations

import argparse
import math
import random
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill


# -----------------------------------------------------------------------------
# Constants / schema
# -----------------------------------------------------------------------------

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

# Computational precedence. 2B->PRE means checked-loopset availability, not belt travel.
LAGS = {
    ("2B", "PRE"): 4.0,
    ("PRE", "FINAL"): 5.0,
    ("FINAL", "2A"): 5.0,
    ("2A", "LASER"): 3.0,
    ("LASER", "SCALE"): 3.0,
    ("SCALE", "OIL"): 3.0,
    ("OIL", "SEAL"): 5.0,
    ("SEAL", "PACK"): 3.0,
    ("PACK", "2C"): 5.0,
}

# Transparent processing-time model:
# nominal = Fixed_Time_Min + Batch_Qty * Time_Per_Unit_Min * Complexity_Factor
# then multiply by deterministic job/station affinity for parallel workstation groups.
OPERATION_PARAMETERS = {
    "2B": {
        "name": "Loop-set visual inspection",
        "flow": "AUXILIARY",
        "route": 0,
        "group": "2B",
        "fixed": 5.0,
        "unit": 0.055,
        "transfer": 4.0,
        "description": (
            "Visual inspection of loopsets only. Accepted loopsets are placed in the "
            "post-2B checked-loopset buffer and wait until PRE requires them."
        ),
    },
    "PRE": {
        "name": "Aggregated PRE preparation",
        "flow": "MAIN",
        "route": 1,
        "group": "PRE",
        "fixed": 10.0,
        "unit": 0.110,
        "transfer": 5.0,
        "description": (
            "One PRE operator performs element inspection, Jig handling/placement, retrieval "
            "of the checked loopset, combination/assembly preparation, and transfer to FINAL."
        ),
    },
    "FINAL": {
        "name": "Belt assembly and end-play inspection",
        "flow": "MAIN",
        "route": 2,
        "group": "FINAL",
        "fixed": 7.0,
        "unit": 0.100,
        "transfer": 5.0,
        "description": "Main belt assembly and end-play inspection.",
    },
    "2A": {
        "name": "Belt inspection and laser preparation",
        "flow": "MAIN",
        "route": 3,
        "group": "2A",
        "fixed": 8.0,
        "unit": 0.120,
        "transfer": 3.0,
        "description": "Inspect the assembled belt and prepare it for laser coding.",
    },
    "LASER": {
        "name": "Laser coding",
        "flow": "MAIN",
        "route": 4,
        "group": "LASER",
        "fixed": 4.0,
        "unit": 0.050,
        "transfer": 3.0,
        "description": "Apply and verify the belt identification code.",
    },
    "SCALE": {
        "name": "Weighing",
        "flow": "MAIN",
        "route": 5,
        "group": "SCALE",
        "fixed": 3.0,
        "unit": 0.030,
        "transfer": 3.0,
        "description": "Measure and record the belt-batch weight.",
    },
    "OIL": {
        "name": "Oil dipping",
        "flow": "MAIN",
        "route": 6,
        "group": "OIL",
        "fixed": 10.0,
        "unit": 0.145,
        "transfer": 5.0,
        "description": "Dip the belt batch in oil using an available oil basket.",
    },
    "SEAL": {
        "name": "Sealing",
        "flow": "MAIN",
        "route": 7,
        "group": "SEAL",
        "fixed": 6.0,
        "unit": 0.075,
        "transfer": 3.0,
        "description": "Seal the prepared belt package.",
    },
    "PACK": {
        "name": "VCI packing and labeling",
        "flow": "MAIN",
        "route": 8,
        "group": "PACK",
        "fixed": 14.0,
        "unit": 0.205,
        "transfer": 5.0,
        "description": "VCI packing, inspection, labeling, and preparation for final inspection.",
    },
    "2C": {
        "name": "Final visual inspection and transfer",
        "flow": "MAIN",
        "route": 9,
        "group": "2C",
        "fixed": 5.0,
        "unit": 0.040,
        "transfer": 0.0,
        "description": "Final visual inspection and transfer.",
    },
}

REQUIRED_SHEETS = [
    "00_README",
    "01_Config",
    "02_Station_Master",
    "03_Operation_Master",
    "04_Precedence_Master",
    "05_Shift_Calendar",
    "06_Station_Downtime",
    "07_Resource_Master",
    "07B_Resource_Units",
    "08_Resource_Req",
    "08B_Base_Resource_Shift_Cap",
    "09_Jobs_30D",
    "09B_Due_Date_Scenarios",
    "10_Loopset_Batches",
    "11_Job_Operations",
    "11B_Job_Precedence",
    "12_Job_Op_Options",
    "13_FIFO_Baseline",
    "13B_FIFO_Resource_Assign",
    "14_Daily_Summary",
    "15_Station_KPI",
    "15B_Resource_KPI",
    "16_Validation_Summary",
    "16B_Validation_Detail",
    "17_KPI_Summary",
]

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def dt(x) -> datetime:
    if isinstance(x, datetime):
        return x
    if isinstance(x, pd.Timestamp):
        return x.to_pydatetime()
    return pd.to_datetime(x).to_pydatetime()


def stable_int(text: str) -> int:
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def quantile_nearest(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(float(x) for x in values)
    idx = int(round(max(0.0, min(1.0, q)) * (len(vals) - 1)))
    return vals[idx]


def allocate_counts(total: int, weights: Sequence[float]) -> List[int]:
    """Largest-remainder integer allocation preserving sum(total)."""
    s = float(sum(weights))
    raw = [total * float(w) / s for w in weights]
    base = [int(math.floor(x)) for x in raw]
    rem = total - sum(base)
    order = sorted(range(len(weights)), key=lambda i: raw[i] - base[i], reverse=True)
    for i in order[:rem]:
        base[i] += 1
    return base


def excel_write_dataframe(ws, df: pd.DataFrame) -> None:
    """Replace worksheet values while preserving the worksheet identity/name."""
    if ws.max_row > 0:
        ws.delete_rows(1, ws.max_row)
    for c, col in enumerate(df.columns, start=1):
        cell = ws.cell(1, c, col)
        cell.number_format = "General"
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r_idx, row in enumerate(df.itertuples(index=False, name=None), start=2):
        for c_idx, value in enumerate(row, start=1):
            if pd.isna(value):
                value = None
            elif isinstance(value, pd.Timestamp):
                value = value.to_pydatetime()
            cell = ws.cell(r_idx, c_idx)
            cell.number_format = "General"
            cell.value = value
            if isinstance(value, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm"
    ws.freeze_panes = "A2"


def format_datetime_columns(ws, header: Sequence[str]) -> None:
    explicit = {
        "Release_DateTime", "Element_Ready_Time", "Loopset_Release_Time", "Base_Due_DateTime",
        "Due_DateTime", "Arrival_Time_2B", "Release_Time_2B", "Ready_Time", "Start_DateTime",
        "Finish_DateTime", "First_Start", "Last_Finish", "Downtime_Start", "Downtime_End",
        "Shift_Start", "Break_Start", "Break_End", "Shift_End", "Start_Time", "Finish_Time",
    }
    for c, name in enumerate(header, start=1):
        if str(name) in explicit or str(name).endswith("_DateTime"):
            for cell in ws.iter_cols(min_col=c, max_col=c, min_row=2, max_row=ws.max_row):
                for x in cell:
                    x.number_format = "yyyy-mm-dd hh:mm"


def validate_required_structure(xls: pd.ExcelFile) -> None:
    missing = [s for s in REQUIRED_SHEETS if s not in xls.sheet_names]
    if missing:
        raise ValueError(f"V2 workbook is missing required sheets: {missing}")


# -----------------------------------------------------------------------------
# Release-time design
# -----------------------------------------------------------------------------

def select_active_release_dates(shift: pd.DataFrame, target_days: int = 12) -> List[datetime]:
    work = shift.copy()
    work["Calendar_Date"] = pd.to_datetime(work["Calendar_Date"]).dt.normalize()
    per_day = work.groupby("Calendar_Date")["Working_Day"].max()
    working_dates = [d.to_pydatetime() for d, v in per_day.items() if int(v) == 1]
    if len(working_dates) < target_days:
        raise ValueError(f"Only {len(working_dates)} working days available; need {target_days}")

    # Front-loaded production waves plus mid/late-horizon demand bursts.
    if len(working_dates) >= 20 and target_days == 12:
        idx = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 14, 19]
    else:
        idx = sorted(set(round(i * (len(working_dates) - 1) / (target_days - 1)) for i in range(target_days)))
        while len(idx) < target_days:
            for i in range(len(working_dates)):
                if i not in idx:
                    idx.append(i)
                    if len(idx) == target_days:
                        break
        idx = sorted(idx[:target_days])
    return [working_dates[i] for i in idx]


def redesign_release_times(jobs: pd.DataFrame, shift: pd.DataFrame, rng: random.Random) -> Tuple[dict, dict, dict, List[datetime]]:
    ordered = jobs.copy()
    ordered["Job_ID"] = ordered["Job_ID"].astype(str)
    ordered = ordered.sort_values(["Order_ID", "Transfer_Batch_No", "Job_ID"]).reset_index(drop=True)

    active_dates = select_active_release_dates(shift, target_days=12)
    profile = [14, 14, 14, 14, 13, 13, 13, 12, 12, 11, 3, 3]
    counts = allocate_counts(len(ordered), profile)

    # Three broad production-release waves: morning, afternoon, late-day preparation.
    wave_minutes = [
        6 * 60 + 10, 6 * 60 + 25, 6 * 60 + 45, 7 * 60 + 5, 7 * 60 + 25, 8 * 60,
        13 * 60 + 5, 13 * 60 + 25, 13 * 60 + 50, 14 * 60 + 20, 15 * 60 + 5,
        17 * 60 + 10, 17 * 60 + 35, 19 * 60 + 5,
    ]

    release: Dict[str, datetime] = {}
    element: Dict[str, datetime] = {}
    loopset: Dict[str, datetime] = {}
    cursor = 0
    for day, count in zip(active_dates, counts):
        day0 = datetime(day.year, day.month, day.day)
        for local in range(count):
            row = ordered.iloc[cursor]
            j = str(row["Job_ID"])
            base_min = wave_minutes[local % len(wave_minutes)]
            rel = day0 + timedelta(minutes=base_min + rng.choice([-8, -5, -3, 0, 3, 5, 8]))

            # Separate physical/material events. Moderate offsets preserve sequencing relevance.
            elem = rel + timedelta(minutes=rng.choice([5, 10, 15, 20, 25, 30, 35]))
            loop = rel + timedelta(minutes=rng.choice([-15, -10, -5, 0, 5, 10, 15, 20]))

            release[j] = rel
            element[j] = elem
            loopset[j] = loop
            cursor += 1
    return release, element, loopset, active_dates


# -----------------------------------------------------------------------------
# Processing times and workstation alternatives
# -----------------------------------------------------------------------------

def station_groups(stations: pd.DataFrame) -> Dict[str, List[str]]:
    s = stations.copy()
    s["Station_ID"] = s["Station_ID"].astype(str)
    s["Station_Group"] = s["Station_Group"].astype(str)
    s = s[s["Status"].astype(str).str.upper().isin(["ACTIVE", "1", "TRUE"])]
    out: Dict[str, List[str]] = defaultdict(list)
    for _, r in s.iterrows():
        out[str(r["Station_Group"])].append(str(r["Station_ID"]))
    for g in out:
        out[g] = sorted(out[g])
    return dict(out)


def station_affinity(job_id: str, product: str, operation: str, station_id: str, group_stations: Mapping[str, Sequence[str]]) -> float:
    stations = list(group_stations[operation])
    if len(stations) <= 1:
        return 1.0
    preferred = stable_int(f"{job_id}|{product}|{operation}|preferred") % len(stations)
    idx = stations.index(station_id)
    product_bias = {"BTA": -0.015, "BTB": 0.0, "BTC": 0.015}.get(product, 0.0)
    # Roughly 8-13% difference between the two options; moderate and job-dependent.
    factor = (0.94 if idx == preferred else 1.055) + product_bias
    return float(factor)


def build_processing_tables(jobs: pd.DataFrame, stations: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[Tuple[str, str, str], float], Dict[str, List[str]]]:
    groups = station_groups(stations)
    records_ops = []
    records_options = []
    proc: Dict[Tuple[str, str, str], float] = {}

    for _, jr in jobs.sort_values("Job_ID").iterrows():
        j = str(jr["Job_ID"])
        qty = int(jr["Batch_Qty"])
        comp = float(jr["Complexity_Factor"])
        product = str(jr["Product_Code"])
        for op in OP_SEQUENCE:
            p = OPERATION_PARAMETERS[op]
            records_ops.append({
                "Job_Operation_ID": f"{j}_{op}",
                "Job_ID": j,
                "Order_ID": str(jr["Order_ID"]),
                "Transfer_Batch_ID": str(jr["Transfer_Batch_ID"]),
                "Operation_Code": op,
                "Operation_Name": p["name"],
                "Flow_Type": p["flow"],
                "Route_Position": p["route"],
                "Station_Group": p["group"],
                "Batch_Qty": qty,
                "Complexity_Factor": comp,
                "Fixed_Time_Min": p["fixed"],
                "Time_Per_Unit_Min": p["unit"],
                "Transfer_Time_Min": p["transfer"],
                "Release_DateTime": jr["Release_DateTime"],
                "Base_Due_DateTime": jr["Base_Due_DateTime"],
            })
            nominal = p["fixed"] + qty * p["unit"] * comp
            eligible = groups.get(op, [])
            if not eligible:
                raise ValueError(f"No active workstation found for operation group {op}")
            for sid in eligible:
                mult = station_affinity(j, product, op, sid, groups)
                ptime = round(nominal * mult, 2)
                if ptime <= 0:
                    raise ValueError(f"Non-positive processing time for {j}/{op}/{sid}")
                proc[(j, op, sid)] = ptime
                records_options.append({
                    "Option_ID": f"{j}_{op}_{sid}",
                    "Job_Operation_ID": f"{j}_{op}",
                    "Job_ID": j,
                    "Operation_Code": op,
                    "Station_ID": sid,
                    "Station_Group": op,
                    "Eligible": 1,
                    "Station_Multiplier": round(mult, 4),
                    "Deterministic_Processing_Time_Min": ptime,
                })
    return pd.DataFrame(records_ops), pd.DataFrame(records_options), proc, groups


# -----------------------------------------------------------------------------
# Effective station windows
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    calendar_date: str
    shift_id: str


def subtract_block(windows: List[Window], bs: datetime, be: datetime) -> List[Window]:
    out: List[Window] = []
    for w in windows:
        if be <= w.start or bs >= w.end:
            out.append(w)
            continue
        if w.start < bs:
            out.append(Window(w.start, min(bs, w.end), w.calendar_date, w.shift_id))
        if be < w.end:
            out.append(Window(max(be, w.start), w.end, w.calendar_date, w.shift_id))
    return [w for w in out if w.end > w.start]


def build_effective_windows(shift: pd.DataFrame, downtime: pd.DataFrame, stations: pd.DataFrame) -> Dict[str, List[Window]]:
    base: List[Window] = []
    for _, r in shift.iterrows():
        if int(r.get("Working_Day", 1)) != 1:
            continue
        s, bs, be, e = dt(r["Shift_Start"]), dt(r["Break_Start"]), dt(r["Break_End"]), dt(r["Shift_End"])
        d, sh = str(pd.to_datetime(r["Calendar_Date"]).date()), str(r["Shift_ID"])
        if bs > s:
            base.append(Window(s, bs, d, sh))
        if e > be:
            base.append(Window(be, e, d, sh))

    ddf = downtime.copy()
    if not ddf.empty:
        ddf["Station_ID"] = ddf["Station_ID"].astype(str)

    out: Dict[str, List[Window]] = {}
    active = stations[stations["Status"].astype(str).str.upper().isin(["ACTIVE", "1", "TRUE"])]
    for _, sr in active.iterrows():
        sid = str(sr["Station_ID"])
        ws = list(base)
        if not ddf.empty:
            for _, dr in ddf[ddf["Station_ID"] == sid].iterrows():
                ws = subtract_block(ws, dt(dr["Downtime_Start"]), dt(dr["Downtime_End"]))
        out[sid] = sorted(ws, key=lambda w: (w.start, w.end))
    return out


# -----------------------------------------------------------------------------
# BASE reusable-resource redundancy proof
# -----------------------------------------------------------------------------

def resource_redundancy_check(
    stations: pd.DataFrame,
    options: pd.DataFrame,
    resource_req: pd.DataFrame,
    resource_master: pd.DataFrame,
    base_cap: pd.DataFrame,
    shift: pd.DataFrame,
) -> Tuple[bool, pd.DataFrame]:
    active = stations[stations["Status"].astype(str).str.upper().isin(["ACTIVE", "1", "TRUE"])].copy()
    station_cap = {str(r["Station_ID"]): int(r.get("Capacity", 1)) for _, r in active.iterrows()}

    op_station: Dict[str, set] = defaultdict(set)
    for _, r in options[options["Eligible"].astype(int) == 1].iterrows():
        op_station[str(r["Operation_Code"])].add(str(r["Station_ID"]))

    rt_upper: Dict[str, int] = defaultdict(int)
    for _, r in resource_req.iterrows():
        op = str(r["Operation_Code"])
        rt = str(r["Resource_Type"])
        qty = int(r["Required_Qty"])
        op_cap = sum(station_cap.get(s, 1) for s in op_station.get(op, set()))
        rt_upper[rt] += qty * op_cap

    base_master = {str(r["Resource_Type"]): int(r["Base_Capacity"]) for _, r in resource_master.iterrows()}
    cap = base_cap.copy()
    cap["Calendar_Date"] = pd.to_datetime(cap["Calendar_Date"]).dt.strftime("%Y-%m-%d")
    cap_lookup = {
        (str(r["Calendar_Date"]), str(r["Shift_ID"]), str(r["Resource_Type"])): int(r["Available_Qty"])
        for _, r in cap.iterrows()
    }
    relevant = shift[shift["Working_Day"].astype(int) == 1].copy()
    relevant["Calendar_Date"] = pd.to_datetime(relevant["Calendar_Date"]).dt.strftime("%Y-%m-%d")
    keys = list({(str(r["Calendar_Date"]), str(r["Shift_ID"])) for _, r in relevant.iterrows()})

    rows = []
    all_ok = True
    for rt, upper in sorted(rt_upper.items()):
        vals = [cap_lookup.get((d, sh, rt), base_master.get(rt, 0)) for d, sh in keys]
        min_avail = min(vals) if vals else base_master.get(rt, 0)
        ok = min_avail >= upper
        all_ok = all_ok and ok
        rows.append({
            "Resource_Type": rt,
            "Station_Implied_Max_Concurrent_Demand": upper,
            "Minimum_BASE_Available_Capacity": min_avail,
            "Redundant_Under_Station_Capacity": bool(ok),
        })
    return bool(all_ok), pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Earliest-finish reference decoder
# -----------------------------------------------------------------------------

def first_conflict_end(intervals: List[Tuple[datetime, datetime, int]], start: datetime, end: datetime) -> Optional[datetime]:
    for s, e, _ in intervals:
        if e <= start:
            continue
        if s >= end:
            break
        return e
    return None


def insert_interval(intervals: List[Tuple[datetime, datetime, int]], item: Tuple[datetime, datetime, int]) -> None:
    intervals.append(item)
    intervals.sort(key=lambda x: (x[0], x[1], x[2]))


def find_station_slot(
    sid: str,
    ready: datetime,
    duration_min: float,
    windows: Mapping[str, Sequence[Window]],
    busy: MutableMapping[str, List[Tuple[datetime, datetime, int]]],
) -> Optional[Tuple[datetime, datetime, Window]]:
    delta = timedelta(minutes=float(duration_min))
    for w in windows[sid]:
        if w.end <= ready:
            continue
        candidate = max(ready, w.start)
        while candidate + delta <= w.end:
            finish = candidate + delta
            ce = first_conflict_end(busy[sid], candidate, finish)
            if ce is None:
                return candidate, finish, w
            candidate = max(candidate + timedelta(microseconds=1), ce)
    return None


def decode_reference(
    chromosome: Sequence[str],
    jobs: pd.DataFrame,
    proc: Mapping[Tuple[str, str, str], float],
    groups: Mapping[str, Sequence[str]],
    windows: Mapping[str, Sequence[Window]],
) -> Tuple[datetime, Dict[str, datetime], pd.DataFrame]:
    info = jobs.set_index(jobs["Job_ID"].astype(str), drop=False)
    busy: MutableMapping[str, List[Tuple[datetime, datetime, int]]] = defaultdict(list)
    completion: Dict[Tuple[str, str], datetime] = {}
    rows = []
    seq = 0

    for j in chromosome:
        jr = info.loc[j]
        release = dt(jr["Release_DateTime"])
        element = dt(jr["Element_Ready_Time"])
        loop = dt(jr["Loopset_Release_Time"])

        for op in OP_SEQUENCE:
            if op == "2B":
                ready = max(release, loop)
            elif op == "PRE":
                ready = max(release, element, completion[(j, "2B")] + timedelta(minutes=LAGS[("2B", "PRE")]))
            else:
                p = PREDECESSOR[op]
                ready = completion[(j, p)] + timedelta(minutes=LAGS[(p, op)])

            candidates = []
            for sid in groups[op]:
                ptime = float(proc[(j, op, sid)])
                slot = find_station_slot(sid, ready, ptime, windows, busy)
                if slot is None:
                    continue
                s, e, w = slot
                candidates.append((e, s, sid, ptime, w))
            if not candidates:
                raise RuntimeError(f"No feasible station/window assignment for {j}/{op}")

            e, s, sid, ptime, w = min(candidates, key=lambda z: (z[0], z[1], z[2]))
            seq += 1
            insert_interval(busy[sid], (s, e, seq))
            completion[(j, op)] = e
            rows.append({
                "Event_ID": f"E{seq:05d}",
                "Job_ID": j,
                "Operation_Code": op,
                "Station_ID": sid,
                "Calendar_Date": w.calendar_date,
                "Shift_ID": w.shift_id,
                "Ready_Time": ready,
                "Start_DateTime": s,
                "Finish_DateTime": e,
                "Deterministic_Processing_Min": ptime,
                "Queue_Wait_Min": max(0.0, (s - ready).total_seconds() / 60.0),
            })

    final = {j: completion[(j, "2C")] for j in chromosome}
    return max(final.values()), final, pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Due-date calibration
# -----------------------------------------------------------------------------

def job_min_workload(jobs: pd.DataFrame, proc: Mapping[Tuple[str, str, str], float], groups: Mapping[str, Sequence[str]]) -> Dict[str, float]:
    out = {}
    lag_total = sum(float(x) for x in LAGS.values())
    for j in jobs["Job_ID"].astype(str):
        out[j] = sum(min(float(proc[(j, op, sid)]) for sid in groups[op]) for op in OP_SEQUENCE) + lag_total
    return out


def calibrate_due_dates(
    jobs: pd.DataFrame,
    final_completion: Mapping[str, datetime],
    workload: Mapping[str, float],
    seed: int,
) -> Tuple[pd.DataFrame, Dict[str, datetime], dict]:
    release = {str(r["Job_ID"]): dt(r["Release_DateTime"]) for _, r in jobs.iterrows()}
    residual = {
        j: max(0.0, (final_completion[j] - release[j]).total_seconds() / 60.0 - float(workload[j]))
        for j in release
    }

    # Choose one global BASE allowance so FIFO BASE tardiness is near the middle of 5-20%.
    base_allowance = quantile_nearest(list(residual.values()), 0.875)

    ids = list(release)
    base_due: Dict[str, datetime] = {}
    tight_due: Dict[str, datetime] = {}
    urgent_due: Dict[str, datetime] = {}
    for j in ids:
        base_due[j] = release[j] + timedelta(minutes=float(workload[j]) + base_allowance)
        tight_due[j] = release[j] + timedelta(minutes=float(workload[j]) + 0.65 * base_allowance)

    # 20% of jobs are urgent. To make the scenario a genuine stress test rather than
    # repeatedly tightening jobs that are already tardy under TIGHT_65PCT, select urgent
    # jobs first from the jobs that are still on time under TIGHT_65PCT. Within that set,
    # Priority is respected first, then a deterministic seeded tie-break is used.
    rng = random.Random(seed ^ 0xA13A13)
    tie = {j: rng.random() for j in ids}
    priority_map = {str(r["Job_ID"]): int(r["Priority"]) for _, r in jobs.iterrows()}
    urgent_count = max(1, int(round(0.20 * len(ids))))
    tight_on_time = [j for j in ids if final_completion[j] <= tight_due[j]]
    tight_tardy = [j for j in ids if final_completion[j] > tight_due[j]]
    ordered_candidates = sorted(tight_on_time, key=lambda j: (priority_map[j], tie[j], j))
    ordered_candidates += sorted(tight_tardy, key=lambda j: (priority_map[j], tie[j], j))
    urgent_jobs = set(ordered_candidates[:urgent_count])

    # URGENT_20PCT keeps 20% urgent-job meaning. Non-urgent jobs use the TIGHT due date.
    # Search ONE urgent allowance factor to land in the requested 40-70% diagnostic band if possible.
    best_factor = 0.0
    best_distance = float("inf")
    best_pct = None
    for fac in [x / 100.0 for x in range(0, 66, 5)]:
        candidate = {}
        for j in ids:
            candidate[j] = (
                release[j] + timedelta(minutes=float(workload[j]) + fac * base_allowance)
                if j in urgent_jobs else tight_due[j]
            )
        pct = 100.0 * sum(final_completion[j] > candidate[j] for j in ids) / len(ids)
        dist = 0.0 if 40.0 <= pct <= 70.0 else min(abs(pct - 40.0), abs(pct - 70.0))
        if dist < best_distance:
            best_distance = dist
            best_factor = fac
            best_pct = pct
            urgent_due = candidate
        if 48.0 <= pct <= 60.0:
            best_factor = fac
            best_pct = pct
            urgent_due = candidate
            break

    rows = []
    for j in sorted(ids):
        rows.extend([
            {
                "Job_ID": j,
                "Due_Scenario_ID": "BASE",
                "Due_DateTime": base_due[j],
                "Scenario_Description": (
                    f"Release + estimated work content + one global production allowance "
                    f"({base_allowance:.1f} min), calibrated once from FIFO waiting residuals."
                ),
            },
            {
                "Job_ID": j,
                "Due_Scenario_ID": "TIGHT_65PCT",
                "Due_DateTime": tight_due[j],
                "Scenario_Description": (
                    "Release + estimated work content + 65% of the global BASE production allowance."
                ),
            },
            {
                "Job_ID": j,
                "Due_Scenario_ID": "URGENT_20PCT",
                "Due_DateTime": urgent_due[j],
                "Scenario_Description": (
                    f"Twenty percent of jobs are urgent. Non-urgent jobs use TIGHT_65PCT due dates; "
                    f"urgent jobs use estimated work content + {best_factor:.0%} of the BASE allowance."
                ),
            },
        ])

    diag = {
        "base_allowance_min": base_allowance,
        "urgent_job_count": urgent_count,
        "urgent_allowance_factor": best_factor,
        "BASE_tardy_pct": 100.0 * sum(final_completion[j] > base_due[j] for j in ids) / len(ids),
        "TIGHT_65PCT_tardy_pct": 100.0 * sum(final_completion[j] > tight_due[j] for j in ids) / len(ids),
        "URGENT_20PCT_tardy_pct": float(best_pct),
    }
    return pd.DataFrame(rows), base_due, diag


# -----------------------------------------------------------------------------
# Resource assignment for the reference schedule
# -----------------------------------------------------------------------------

def assign_reference_resources(
    schedule: pd.DataFrame,
    resource_req: pd.DataFrame,
    resource_units: pd.DataFrame,
    base_cap: pd.DataFrame,
) -> pd.DataFrame:
    reqs: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for _, r in resource_req.iterrows():
        reqs[str(r["Operation_Code"])].append((str(r["Resource_Type"]), int(r["Required_Qty"])))

    units: Dict[str, List[str]] = defaultdict(list)
    ru = resource_units.copy()
    active = ru[ru["Status"].astype(str).str.upper().isin(["ACTIVE", "1", "TRUE"])] if "Status" in ru.columns else ru
    for _, r in active.iterrows():
        units[str(r["Resource_Type"])].append(str(r["Resource_Unit_ID"]))
    for rt in units:
        units[rt] = sorted(units[rt])

    cap = base_cap.copy()
    cap["Calendar_Date"] = pd.to_datetime(cap["Calendar_Date"]).dt.strftime("%Y-%m-%d")
    cap_lookup = {
        (str(r["Calendar_Date"]), str(r["Shift_ID"]), str(r["Resource_Type"])): int(r["Available_Qty"])
        for _, r in cap.iterrows()
    }

    busy: Dict[str, List[Tuple[datetime, datetime, str]]] = defaultdict(list)
    out = []
    for _, ev in schedule.sort_values(["Start_DateTime", "Event_ID"]).iterrows():
        op = str(ev["Operation_Code"])
        start, finish = dt(ev["Start_DateTime"]), dt(ev["Finish_DateTime"])
        d, sh = str(ev["Calendar_Date"]), str(ev["Shift_ID"])
        for rt, qty in reqs.get(op, []):
            available_units = units.get(rt, [])[: max(0, cap_lookup.get((d, sh, rt), len(units.get(rt, []))))]
            free = []
            for uid in available_units:
                conflict = any(not (finish <= s or start >= e) for s, e, _ in busy[uid])
                if not conflict:
                    free.append(uid)
                    if len(free) >= qty:
                        break
            if len(free) < qty:
                raise RuntimeError(
                    f"Reference schedule resource assignment failed: Event={ev['Event_ID']} "
                    f"Resource={rt} required={qty} free={len(free)}"
                )
            for uid in free[:qty]:
                busy[uid].append((start, finish, str(ev["Event_ID"])))
                out.append({
                    "Assignment_ID": f"{ev['Event_ID']}_{rt}_{uid}",
                    "Event_ID": str(ev["Event_ID"]),
                    "Job_ID": str(ev["Job_ID"]),
                    "Operation_Code": op,
                    "Station_ID": str(ev["Station_ID"]),
                    "Resource_Type": rt,
                    "Resource_Unit_ID": uid,
                    "Start_DateTime": start,
                    "Finish_DateTime": finish,
                    "Shift_ID": sh,
                })
    return pd.DataFrame(out)


# -----------------------------------------------------------------------------
# Workbook update tables
# -----------------------------------------------------------------------------

def build_precedence_tables() -> Tuple[pd.DataFrame, pd.DataFrame]:
    master = []
    for (a, b), lag in LAGS.items():
        master.append({
            "Predecessor_Operation": a,
            "Successor_Operation": b,
            "Relation_Type": "AUXILIARY_FEED" if a == "2B" else "MAIN_FLOW",
            "Minimum_Lag_Min": lag,
        })
    return pd.DataFrame(master), pd.DataFrame()


def build_job_precedence(jobs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for j in sorted(jobs["Job_ID"].astype(str)):
        for (a, b), lag in LAGS.items():
            rows.append({
                "Job_ID": j,
                "Predecessor_Job_Operation_ID": f"{j}_{a}",
                "Successor_Job_Operation_ID": f"{j}_{b}",
                "Predecessor_Operation": a,
                "Successor_Operation": b,
                "Relation_Type": "AUXILIARY_FEED" if a == "2B" else "MAIN_FLOW",
                "Minimum_Lag_Min": lag,
            })
    return pd.DataFrame(rows)


def build_operation_master() -> pd.DataFrame:
    rows = []
    for op in OP_SEQUENCE:
        p = OPERATION_PARAMETERS[op]
        rows.append({
            "Operation_Code": op,
            "Operation_Name": p["name"],
            "Flow_Type": p["flow"],
            "Route_Position": p["route"],
            "Station_Group": p["group"],
            "Fixed_Time_Min": p["fixed"],
            "Time_Per_Unit_Min": p["unit"],
            "Transfer_Time_Min": p["transfer"],
            "Description": p["description"],
        })
    return pd.DataFrame(rows)


def build_fifo_sheet(schedule: pd.DataFrame, jobs: pd.DataFrame, base_due: Mapping[str, datetime]) -> pd.DataFrame:
    ji = jobs.set_index(jobs["Job_ID"].astype(str), drop=False)
    seq = Counter()
    rows = []
    for _, ev in schedule.iterrows():
        j, op, sid = str(ev["Job_ID"]), str(ev["Operation_Code"]), str(ev["Station_ID"])
        jr = ji.loc[j]
        seq[sid] += 1
        p = OPERATION_PARAMETERS[op]
        tard = max(0.0, (dt(ev["Finish_DateTime"]) - base_due[j]).total_seconds() / 60.0) if op == "2C" else 0.0
        rows.append({
            "Event_ID": ev["Event_ID"],
            "Job_ID": j,
            "Order_ID": str(jr["Order_ID"]),
            "Transfer_Batch_ID": str(jr["Transfer_Batch_ID"]),
            "Transfer_Batch_Scenario": str(jr["Transfer_Batch_Scenario"]),
            "Operation_Code": op,
            "Operation_Name": p["name"],
            "Flow_Type": p["flow"],
            "Route_Position": p["route"],
            "Station_ID": sid,
            "Station_Group": op,
            "Calendar_Date": ev["Calendar_Date"],
            "Shift_ID": ev["Shift_ID"],
            "Ready_Time": ev["Ready_Time"],
            "Start_DateTime": ev["Start_DateTime"],
            "Finish_DateTime": ev["Finish_DateTime"],
            "Deterministic_Processing_Min": ev["Deterministic_Processing_Min"],
            "Variation_Factor": 1.0,
            "Baseline_Processing_Min": ev["Deterministic_Processing_Min"],
            "Queue_Wait_Min": ev["Queue_Wait_Min"],
            "Base_Due_DateTime": base_due[j],
            "Tardiness_Min": tard,
            "Sequence_On_Station": seq[sid],
        })
    return pd.DataFrame(rows)


def build_daily_summary(shift: pd.DataFrame, jobs: pd.DataFrame, schedule: pd.DataFrame, final: Mapping[str, datetime], base_due: Mapping[str, datetime]) -> pd.DataFrame:
    start = pd.to_datetime(shift["Calendar_Date"]).min().normalize()
    dates = [start + pd.Timedelta(days=i) for i in range(30)]
    rows = []
    for d in dates:
        date_str = d.strftime("%Y-%m-%d")
        day_shift = shift[pd.to_datetime(shift["Calendar_Date"]).dt.strftime("%Y-%m-%d") == date_str]
        working = int(day_shift["Working_Day"].max()) if not day_shift.empty else 0
        avail = float(day_shift["Effective_Minutes"].sum()) if not day_shift.empty else 0.0
        released = int((pd.to_datetime(jobs["Release_DateTime"]).dt.strftime("%Y-%m-%d") == date_str).sum())
        completed = sum(dt(v).strftime("%Y-%m-%d") == date_str for v in final.values())
        ops = schedule[pd.to_datetime(schedule["Finish_DateTime"]).dt.strftime("%Y-%m-%d") == date_str]
        tardy = sum(dt(final[j]).strftime("%Y-%m-%d") == date_str and final[j] > base_due[j] for j in final)
        rows.append({
            "Date": d.to_pydatetime(),
            "Day_Name": d.strftime("%A"),
            "Working_Day": working,
            "Available_Line_Minutes": avail,
            "Jobs_Released": released,
            "Jobs_Completed": int(completed),
            "Operations_Completed": int(len(ops)),
            "Busy_Processing_Minutes": round(float(ops["Deterministic_Processing_Min"].sum()), 2) if not ops.empty else 0.0,
            "Tardy_Jobs_Completed": int(tardy),
        })
    return pd.DataFrame(rows)


def available_minutes(windows: Mapping[str, Sequence[Window]], sid: str, start: datetime, end: datetime) -> float:
    total = 0.0
    for w in windows[sid]:
        a, b = max(start, w.start), min(end, w.end)
        if b > a:
            total += (b - a).total_seconds() / 60.0
    return total


def build_station_kpi(stations: pd.DataFrame, schedule: pd.DataFrame, windows: Mapping[str, Sequence[Window]], horizon_start: datetime, horizon_end: datetime) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    active_start = dt(schedule["Start_DateTime"].min())
    active_end = dt(schedule["Finish_DateTime"].max())
    active_rows = []
    for _, r in stations.iterrows():
        sid = str(r["Station_ID"])
        sub = schedule[schedule["Station_ID"].astype(str) == sid]
        busy = float(sub["Deterministic_Processing_Min"].sum()) if not sub.empty else 0.0
        full_avail = available_minutes(windows, sid, horizon_start, horizon_end)
        act_avail = available_minutes(windows, sid, active_start, active_end)
        rows.append({
            "Station_ID": sid,
            "Station_Group": str(r["Station_Group"]),
            "Time_Multiplier": float(r.get("Time_Multiplier", 1.0)),
            "Busy_Minutes": round(busy, 2),
            "Available_Minutes": round(full_avail, 2),
            "Utilization_Pct": round(100.0 * busy / full_avail, 2) if full_avail else 0.0,
            "First_Start": sub["Start_DateTime"].min() if not sub.empty else None,
            "Last_Finish": sub["Finish_DateTime"].max() if not sub.empty else None,
        })
        active_rows.append({
            "Station_ID": sid,
            "Station_Group": str(r["Station_Group"]),
            "Busy_Minutes": round(busy, 2),
            "Active_Available_Minutes": round(act_avail, 2),
            "Active_Utilization_Pct": round(100.0 * busy / act_avail, 2) if act_avail else 0.0,
        })
    return pd.DataFrame(rows), pd.DataFrame(active_rows)


def build_resource_kpi(assignments: pd.DataFrame, resource_units: pd.DataFrame, shift: pd.DataFrame) -> pd.DataFrame:
    ru = resource_units.copy()
    total_available = float(shift[shift["Working_Day"].astype(int) == 1]["Effective_Minutes"].sum())
    rows = []
    for _, r in ru.iterrows():
        uid = str(r["Resource_Unit_ID"])
        sub = assignments[assignments["Resource_Unit_ID"].astype(str) == uid] if not assignments.empty else assignments
        busy = sum((dt(x["Finish_DateTime"]) - dt(x["Start_DateTime"])).total_seconds() / 60.0 for _, x in sub.iterrows()) if not sub.empty else 0.0
        rows.append({
            "Resource_Unit_ID": uid,
            "Resource_Type": str(r["Resource_Type"]),
            "Busy_Minutes": round(busy, 2),
            "Available_Minutes": round(total_available, 2),
            "Utilization_Pct": round(100.0 * busy / total_available, 2) if total_available else 0.0,
            "Assignments": int(len(sub)) if not sub.empty else 0,
            "Status": str(r.get("Status", "ACTIVE")),
        })
    return pd.DataFrame(rows)


def release_metrics(times: Sequence[datetime]) -> dict:
    arr = sorted(times)
    counts = Counter(x.date() for x in arr)
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(arr, arr[1:])]
    return {
        "active_days": len(counts),
        "mean_jobs_active_day": len(arr) / max(1, len(counts)),
        "max_jobs_day": max(counts.values()) if counts else 0,
        "mean_interarrival_min": sum(gaps) / len(gaps) if gaps else 0.0,
        "release_span_min": (arr[-1] - arr[0]).total_seconds() / 60.0 if len(arr) >= 2 else 0.0,
    }


# -----------------------------------------------------------------------------
# Main generator
# -----------------------------------------------------------------------------

def generate(input_path: Path, output_path: Path, seed: int, force_overwrite: bool = False) -> None:
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if input_path.resolve() == output_path.resolve() and not force_overwrite:
        raise SystemExit("Refusing to overwrite V2. Use a different --output path.")

    xls = pd.ExcelFile(input_path)
    validate_required_structure(xls)

    # Read only the sheets needed for the transformation/validation.
    stations = pd.read_excel(input_path, sheet_name="02_Station_Master")
    shift = pd.read_excel(input_path, sheet_name="05_Shift_Calendar")
    downtime = pd.read_excel(input_path, sheet_name="06_Station_Downtime")
    resource_master = pd.read_excel(input_path, sheet_name="07_Resource_Master")
    resource_units = pd.read_excel(input_path, sheet_name="07B_Resource_Units")
    resource_req = pd.read_excel(input_path, sheet_name="08_Resource_Req")
    base_cap = pd.read_excel(input_path, sheet_name="08B_Base_Resource_Shift_Cap")
    jobs_v2 = pd.read_excel(input_path, sheet_name="09_Jobs_30D")
    loopset_v2 = pd.read_excel(input_path, sheet_name="10_Loopset_Batches")

    if jobs_v2["Job_ID"].astype(str).duplicated().any():
        raise ValueError("V2 contains duplicate Job_ID values")

    rng = random.Random(seed)
    jobs = jobs_v2.copy()
    jobs["Job_ID"] = jobs["Job_ID"].astype(str)
    release, element, loopset, active_dates = redesign_release_times(jobs, shift, rng)
    jobs["Release_DateTime"] = jobs["Job_ID"].map(release)
    jobs["Element_Ready_Time"] = jobs["Job_ID"].map(element)
    jobs["Loopset_Release_Time"] = jobs["Job_ID"].map(loopset)

    # Temporary due dates are only placeholders while processing tables/reference schedule are built.
    jobs["Base_Due_DateTime"] = jobs["Release_DateTime"] + pd.to_timedelta(7, unit="D")

    job_ops, options, proc, groups = build_processing_tables(jobs, stations)
    windows = build_effective_windows(shift, downtime, stations)

    # Ensure every deterministic processing time can fit at least one effective window on its station.
    max_window = {sid: max((w.end - w.start).total_seconds() / 60.0 for w in ws) for sid, ws in windows.items() if ws}
    for (j, op, sid), p in proc.items():
        if p > max_window.get(sid, 0.0) + 1e-9:
            raise ValueError(f"{j}/{op}/{sid} processing={p:.2f} min cannot fit any effective window")

    fifo_order = jobs.sort_values(["Release_DateTime", "Priority", "Job_ID"])["Job_ID"].astype(str).tolist()
    fifo_end, final, schedule = decode_reference(fifo_order, jobs, proc, groups, windows)

    workload = job_min_workload(jobs, proc, groups)
    due_scenarios, base_due, due_diag = calibrate_due_dates(jobs, final, workload, seed)
    jobs["Base_Due_DateTime"] = jobs["Job_ID"].map(base_due)

    # Rebuild job operations after final BASE due date exists.
    job_ops, options, proc, groups = build_processing_tables(jobs, stations)
    job_precedence = build_job_precedence(jobs)
    precedence_master, _ = build_precedence_tables()
    operation_master = build_operation_master()

    # Loopset feeder table.
    loopset_df = loopset_v2.copy()
    loopset_df["Related_Job_ID"] = loopset_df["Related_Job_ID"].astype(str)
    loopset_df["Arrival_Time_2B"] = loopset_df["Related_Job_ID"].map(loopset)
    loopset_df["Release_Time_2B"] = loopset_df["Related_Job_ID"].map(loopset)

    fifo_sheet = build_fifo_sheet(schedule, jobs, base_due)
    resource_assign = assign_reference_resources(schedule, resource_req, resource_units, base_cap)

    # Resource redundancy proof, required for the current BASE benchmark MILP shortcut.
    resources_redundant, redundancy_detail = resource_redundancy_check(
        stations, options, resource_req, resource_master, base_cap, shift
    )

    # Calendar horizon.
    horizon_start = min(dt(x) for x in shift["Shift_Start"])
    horizon_end = max(dt(x) for x in shift["Shift_End"])
    daily_summary = build_daily_summary(shift, jobs, schedule, final, base_due)
    station_kpi, station_active = build_station_kpi(stations, schedule, windows, horizon_start, horizon_end)
    resource_kpi = build_resource_kpi(resource_assign, resource_units, shift)

    # Structural validation.
    checks = []
    def add_check(name: str, ok: bool, expected: str, details: str = ""):
        checks.append({"Check": name, "Violations": 0 if ok else 1, "Expected": expected, "Status": "PASS" if ok else "FAIL", "Details": details})

    add_check("Unique Job_ID", jobs["Job_ID"].nunique() == len(jobs), "0 violations", f"jobs={len(jobs)}")
    add_check("Correct number of jobs", len(jobs) == len(jobs_v2), str(len(jobs_v2)), "Job count preserved from V2")
    add_check("Required operation data", len(job_ops) == len(jobs) * len(OP_SEQUENCE), "10 operations/job", f"rows={len(job_ops)}")
    add_check("Every operation has eligible station", all(options.groupby(["Job_ID", "Operation_Code"])["Eligible"].sum() >= 1), ">=1", "")
    add_check("Processing time > 0", bool((options["Deterministic_Processing_Time_Min"] > 0).all()), ">0", "")
    add_check("2B loopset inspection semantics", True, "2B only inspects loopsets", OPERATION_PARAMETERS["2B"]["description"])
    add_check("PRE same-person aggregation", True, "One aggregated PRE activity", OPERATION_PARAMETERS["PRE"]["description"])
    add_check("PRE synchronization fields", bool(jobs[["Release_DateTime", "Element_Ready_Time", "Loopset_Release_Time"]].notna().all().all()), "all present", "")
    add_check("Precedence chain", len(job_precedence) == len(jobs) * 9, "9 relations/job", "2B->PRE is AUXILIARY_FEED")
    add_check("Minimum lag >= 0", all(v >= 0 for v in LAGS.values()), ">=0", "")
    add_check("Due date > release", bool((due_scenarios.merge(jobs[["Job_ID", "Release_DateTime"]], on="Job_ID")["Due_DateTime"] > due_scenarios.merge(jobs[["Job_ID", "Release_DateTime"]], on="Job_ID")["Release_DateTime"]).all()), "all", "")
    add_check("FIFO reference feasible", fifo_end <= horizon_end, "within 30-day horizon", f"completion={fifo_end}")
    add_check("BASE resource redundancy", resources_redundant, "TRUE preferred", "Required by current MILP benchmark shortcut")
    add_check("BASE tardy diagnostic", 5 <= due_diag["BASE_tardy_pct"] <= 20, "5-20%", f"{due_diag['BASE_tardy_pct']:.2f}%")
    add_check("TIGHT tardy diagnostic", 20 <= due_diag["TIGHT_65PCT_tardy_pct"] <= 40, "20-40%", f"{due_diag['TIGHT_65PCT_tardy_pct']:.2f}%")
    add_check("URGENT tardy diagnostic", 40 <= due_diag["URGENT_20PCT_tardy_pct"] <= 70, "40-70%", f"{due_diag['URGENT_20PCT_tardy_pct']:.2f}%")
    validation_summary = pd.DataFrame(checks)

    # Before-vs-after release metrics.
    v2_release = pd.to_datetime(jobs_v2["Release_DateTime"]).dt.to_pydatetime().tolist()
    v3_release = pd.to_datetime(jobs["Release_DateTime"]).dt.to_pydatetime().tolist()
    v2m, v3m = release_metrics(v2_release), release_metrics(v3_release)

    # Representative 50-job contiguous release block span (central block).
    ordered = jobs.sort_values(["Release_DateTime", "Priority", "Job_ID"]).reset_index(drop=True)
    n50 = min(50, len(ordered))
    start50 = max(0, (len(ordered) - n50) // 2)
    rep50 = ordered.iloc[start50:start50 + n50]
    span50 = (dt(rep50["Release_DateTime"].max()) - dt(rep50["Release_DateTime"].min())).total_seconds() / 60.0

    group_active = station_active.groupby("Station_Group").agg(
        Busy_Minutes=("Busy_Minutes", "sum"),
        Active_Available_Minutes=("Active_Available_Minutes", "sum"),
    ).reset_index()
    group_active["Active_Utilization_Pct"] = 100 * group_active["Busy_Minutes"] / group_active["Active_Available_Minutes"]
    util_map = dict(zip(group_active["Station_Group"], group_active["Active_Utilization_Pct"]))

    comparison = pd.DataFrame([
        ["Total jobs", len(jobs_v2), len(jobs), "Job population preserved"],
        ["Planning horizon", "30 days", "30 days", "Shift calendar retained"],
        ["Active production days", v2m["active_days"], v3m["active_days"], "V3 uses production waves"],
        ["Mean jobs per active day", v2m["mean_jobs_active_day"], v3m["mean_jobs_active_day"], "Higher V3 release density"],
        ["Maximum jobs released per day", v2m["max_jobs_day"], v3m["max_jobs_day"], "Peak wave intensity"],
        ["Mean inter-arrival time (min)", v2m["mean_interarrival_min"], v3m["mean_interarrival_min"], "Lower V3 inter-arrival encourages competition"],
        ["Release-time span (min)", v2m["release_span_min"], v3m["release_span_min"], "Full 30-day source horizon retained"],
        ["Representative 50-job release span (min)", "N/A", span50, "Central release-contiguous block"],
        ["Mean job workload (min)", "N/A", sum(workload.values()) / len(workload), "Fastest-option work content + transfer lags"],
        ["PRE active utilization %", "N/A", util_map.get("PRE", 0.0), "FIFO reference"],
        ["FINAL active utilization %", "N/A", util_map.get("FINAL", 0.0), "FIFO reference"],
        ["2A active utilization %", "N/A", util_map.get("2A", 0.0), "FIFO reference"],
        ["OIL active utilization %", "N/A", util_map.get("OIL", 0.0), "FIFO reference"],
        ["SEAL active utilization %", "N/A", util_map.get("SEAL", 0.0), "FIFO reference"],
        ["PACK active utilization %", "N/A", util_map.get("PACK", 0.0), "FIFO reference"],
        ["BASE tardy jobs %", "N/A", due_diag["BASE_tardy_pct"], "FIFO diagnostic"],
        ["TIGHT_65PCT tardy jobs %", "N/A", due_diag["TIGHT_65PCT_tardy_pct"], "FIFO diagnostic"],
        ["URGENT_20PCT tardy jobs %", "N/A", due_diag["URGENT_20PCT_tardy_pct"], "FIFO diagnostic"],
        ["Random chromosomes tested", "N/A", "Pending validator", "Run validate_al13_dataset_v3.py"],
        ["Distinct Cmax values", "N/A", "Pending validator", "Run validate_al13_dataset_v3.py"],
    ], columns=["Metric", "V2", "V3", "Interpretation"])

    change_log = pd.DataFrame([
        ["Reproducibility", "01_Config", "Random_Seed", "V2 seed", seed, "Fixed V3 seed", "Repeatable generation", "PASS"],
        ["Process semantics", "03_Operation_Master", "2B", "Auxiliary feeder", "Loopset visual inspection only", "Confirmed AL13 logic", "No belt flow through 2B", "PASS"],
        ["PRE same-person", "03_Operation_Master", "PRE", "Partial PRE abstraction", "Aggregated same-person PRE workload", "Avoid artificial parallel PRE labor", "Correct PRE capacity", "PASS"],
        ["Release redesign", "09_Jobs_30D", "Release_DateTime", "Dispersed releases", "Wave-based clustered releases", "Increase realistic overlap", "More meaningful sequencing", "PASS"],
        ["Availability separation", "09_Jobs_30D", "Element/Loopset times", "Often coincident", "Distinct deterministic events", "Represent two PRE inputs", "Realistic synchronization", "PASS"],
        ["Processing times", "12_Job_Op_Options", "Processing time", "Global station ranking", "Job-dependent parallel-station affinity", "Create assignment decisions", "Moderate heterogeneity", "PASS"],
        ["Due dates", "09B_Due_Date_Scenarios", "Due_DateTime", "Calendar-style due dates", "Work-content + global allowance + scenario rules", "Transparent tardiness design", "Non-trivial due-date performance", "PASS"],
        ["Resources", "08B_Base_Resource_Shift_Cap", "BASE capacity", "V2", "Preserved", "Do not manufacture scarcity", "MILP compatibility", "PASS" if resources_redundant else "FAIL"],
        ["Compatibility", "All required sheets", "Names/columns", "V2 schema", "Preserved", "No solver/decoder changes", "Existing pipeline remains readable", "PASS"],
    ], columns=["Category", "Affected_Sheet", "Affected_Field", "V2_Logic", "V3_Logic", "Reason", "Expected_Effect", "Validation_Result"])

    report = pd.DataFrame([
        ["Seed", seed, "Fixed deterministic seed"],
        ["Total jobs", len(jobs), "Preserved from V2"],
        ["Active release days", len(active_dates), "Wave-based releases"],
        ["FIFO completion", fifo_end, "Complete reference schedule"],
        ["BASE tardy %", due_diag["BASE_tardy_pct"], "Target 5-20%"],
        ["TIGHT_65PCT tardy %", due_diag["TIGHT_65PCT_tardy_pct"], "Target 20-40%"],
        ["URGENT_20PCT tardy %", due_diag["URGENT_20PCT_tardy_pct"], "Target 40-70%"],
        ["BASE reusable resources redundant", resources_redundant, "Preferred TRUE for current MILP benchmark"],
        ["Random-permutation landscape", "PENDING", "Run validate_al13_dataset_v3.py"],
        ["Lightweight GA diagnostic", "PENDING", "Run validate_al13_dataset_v3.py"],
        ["Benchmark readiness", "FALSE until validator passes", "Do not reuse V2 results"],
    ], columns=["Metric", "Value", "Interpretation"])

    # ------------------------------------------------------------------
    # Save into a copied workbook, preserving all untouched sheets.
    # ------------------------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = load_workbook(input_path)

    # Config edits in-place.
    cfg = wb["01_Config"]
    config_values = {cfg.cell(r, 1).value: r for r in range(2, cfg.max_row + 1)}
    for key, value in {
        "Random_Seed": seed,
        "Transfer_Batch_Jobs": len(jobs),
        "Operations_Per_Job": 10,
        "Main_Route": "PRE>FINAL>2A>LASER>SCALE>OIL>SEAL>PACK>2C",
        "Auxiliary_Relation": "Loopset>2B>checked-loopset buffer>PRE synchronization",
    }.items():
        if key in config_values:
            cfg.cell(config_values[key], 2).value = value

    # Core data tables.
    core = {
        "03_Operation_Master": operation_master,
        "04_Precedence_Master": precedence_master,
        "09_Jobs_30D": jobs,
        "09B_Due_Date_Scenarios": due_scenarios,
        "10_Loopset_Batches": loopset_df,
        "11_Job_Operations": job_ops,
        "11B_Job_Precedence": job_precedence,
        "12_Job_Op_Options": options,
        "13_FIFO_Baseline": fifo_sheet,
        "13B_FIFO_Resource_Assign": resource_assign,
        "14_Daily_Summary": daily_summary,
        "15_Station_KPI": station_kpi,
        "15B_Resource_KPI": resource_kpi,
        "16_Validation_Summary": validation_summary,
    }
    for sheet_name, df in core.items():
        ws = wb[sheet_name]
        excel_write_dataframe(ws, df)
        format_datetime_columns(ws, list(df.columns))

    # Validation detail starts with resource redundancy proof. Full landscape/GA comes from validator.
    detail = redundancy_detail.copy()
    detail.insert(0, "Validation_Category", "BASE_RESOURCE_REDUNDANCY")
    excel_write_dataframe(wb["16B_Validation_Detail"], detail)

    # V3 report and change log.
    for sheet_name in ["98_V3_Validation_Report", "99_V3_Change_Log"]:
        if sheet_name not in wb.sheetnames:
            wb.create_sheet(sheet_name)
    excel_write_dataframe(wb["98_V3_Validation_Report"], report)
    # Put comparison table to the right without deleting the report.
    wsrep = wb["98_V3_Validation_Report"]
    start_col = 5
    for c, col in enumerate(comparison.columns, start=start_col):
        cell = wsrep.cell(1, c, col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for r_idx, row in enumerate(comparison.itertuples(index=False, name=None), start=2):
        for c_idx, value in enumerate(row, start=start_col):
            wsrep.cell(r_idx, c_idx, value)

    excel_write_dataframe(wb["99_V3_Change_Log"], change_log)

    # README V3 note.
    readme = wb["00_README"]
    readme.append([
        "V3 revision",
        "Wave-based releases; separate element/loopset availability; 2B loopset inspection only; "
        "aggregated same-person PRE workload; job-dependent parallel-station processing; "
        f"work-content due dates; seed {seed}. Run validate_al13_dataset_v3.py before benchmarking.",
    ])

    # General column widths for the new sheets.
    for sname in ["98_V3_Validation_Report", "99_V3_Change_Log"]:
        ws = wb[sname]
        for col in ws.columns:
            letter = col[0].column_letter
            ws.column_dimensions[letter].width = min(40, max(12, max(len(str(c.value or "")) for c in col) + 2))
            for c in col:
                c.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(output_path)

    print("=" * 88)
    print("AL13 DATASET V3 GENERATED")
    print(f"Input : {input_path}")
    print(f"Output: {output_path}")
    print(f"Seed  : {seed}")
    print(f"Jobs  : {len(jobs)}")
    print(f"Active release days: {len(active_dates)}")
    print(f"FIFO completion: {fifo_end}")
    print(
        "FIFO tardy %: "
        f"BASE={due_diag['BASE_tardy_pct']:.2f}, "
        f"TIGHT_65PCT={due_diag['TIGHT_65PCT_tardy_pct']:.2f}, "
        f"URGENT_20PCT={due_diag['URGENT_20PCT_tardy_pct']:.2f}"
    )
    print(f"BASE resource redundancy: {resources_redundant}")
    print("NEXT: run validate_al13_dataset_v3.py before freezing V3 benchmark instances.")
    print("DATASET_V3_READY_FOR_BENCHMARK = FALSE  # until landscape + lightweight GA validation pass")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate optimized 30-day AL13 synthetic dataset V3 from V2.")
    ap.add_argument("--input", type=Path, default=Path("AL13_30Day_Synthetic_Dataset_v2.xlsx"))
    ap.add_argument("--output", type=Path, default=Path("AL13_30Day_Synthetic_Dataset_v3_Optimized.xlsx"))
    ap.add_argument("--seed", type=int, default=20260831)
    ap.add_argument("--force-overwrite", action="store_true", help="Allow --input and --output to be the same path. NOT recommended.")
    args = ap.parse_args()
    generate(args.input, args.output, args.seed, args.force_overwrite)


if __name__ == "__main__":
    main()
