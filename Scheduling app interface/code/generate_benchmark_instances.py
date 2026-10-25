#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Generate deterministic AL13 benchmark instances from the existing V2 dataset.

This is the ONLY script that reads the Excel source.  MILP and GA never sample
jobs or generate data; they only read the JSON files produced here.

Default source folder requested by the thesis project:
    D:\back up giga\thesis\flowlien dataset

Example
-------
python generate_benchmark_instances.py \
    --dataset "D:\back up giga\thesis\flowlien dataset\AL13_30Day_Synthetic_Dataset_v2.xlsx" \
    --output-dir "D:\back up giga\thesis\flowlien dataset\benchmark_instances" \
    --sizes 3 5 10 20 --instances-per-size 4 --seed 20260815
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd

from benchmark_common import OP_SEQUENCE

WINDOWS_DIR = Path(r"D:\back up giga\thesis\flowlien dataset")
DEFAULT_DATASET = WINDOWS_DIR / "AL13_30Day_Synthetic_Dataset_v2.xlsx"
DEFAULT_OUTPUT = WINDOWS_DIR / "benchmark_instances"

SHEETS = {
    "stations": "02_Station_Master",
    "shift": "05_Shift_Calendar",
    "downtime": "06_Station_Downtime",
    "resource_master": "07_Resource_Master",
    "resource_units": "07B_Resource_Units",
    "resource_req": "08_Resource_Req",
    "base_cap": "08B_Base_Resource_Shift_Cap",
    "disruption_cap": "08C_Disruption_Res_Shift_Cap",
    "jobs": "09_Jobs_30D",
    "due": "09B_Due_Date_Scenarios",
    "precedence": "11B_Job_Precedence",
    "options": "12_Job_Op_Options",
}


def dt(x) -> datetime:
    if isinstance(x, datetime):
        return x
    if isinstance(x, pd.Timestamp):
        return x.to_pydatetime()
    return pd.to_datetime(x).to_pydatetime()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_source(path: Path) -> Dict[str, pd.DataFrame]:
    if not path.exists():
        raise FileNotFoundError(path)
    xls = pd.ExcelFile(path)
    out = {}
    for key, sheet in SHEETS.items():
        if sheet in xls.sheet_names:
            out[key] = pd.read_excel(path, sheet_name=sheet)
        elif key == "disruption_cap":
            alt = "08C_Disruption_Resource_Shift_Cap"
            out[key] = pd.read_excel(path, sheet_name=alt) if alt in xls.sheet_names else pd.DataFrame()
        else:
            raise ValueError(f"Required sheet is missing: {sheet}")
    return out


def subtract_block(windows, bs: datetime, be: datetime):
    out = []
    for s, e, d, sh in windows:
        if be <= s or bs >= e:
            out.append((s, e, d, sh))
            continue
        if s < bs:
            out.append((s, min(bs, e), d, sh))
        if be < e:
            out.append((max(be, s), e, d, sh))
    return [(s, e, d, sh) for s, e, d, sh in out if e > s]


def build_effective_windows(data: Dict[str, pd.DataFrame]) -> Dict[str, List[Tuple[datetime, datetime, str, str]]]:
    """Build break-free and downtime-free windows for every active station."""
    base = []
    for _, r in data["shift"].iterrows():
        if int(r.get("Working_Day", 1)) != 1:
            continue
        s, bs, be, e = dt(r["Shift_Start"]), dt(r["Break_Start"]), dt(r["Break_End"]), dt(r["Shift_End"])
        d, sh = str(r["Calendar_Date"])[:10], str(r["Shift_ID"])
        if bs > s:
            base.append((s, bs, d, sh))
        if e > be:
            base.append((be, e, d, sh))

    downtime = data["downtime"].copy()
    if not downtime.empty:
        downtime["Station_ID"] = downtime["Station_ID"].astype(str)

    result = {}
    for _, sr in data["stations"].iterrows():
        sid = str(sr["Station_ID"])
        if str(sr.get("Status", "ACTIVE")).upper() not in {"ACTIVE", "1", "TRUE"}:
            continue
        ws = list(base)
        if not downtime.empty:
            for _, dr in downtime[downtime["Station_ID"] == sid].iterrows():
                ws = subtract_block(ws, dt(dr["Downtime_Start"]), dt(dr["Downtime_End"]))
        result[sid] = sorted(ws)
    return result


def choose_contiguous_jobs(
    jobs: pd.DataFrame,
    size: int,
    instance_index: int,
    instances_per_size: int,
    seed: int,
) -> pd.DataFrame:
    """Select one deterministic release-time cluster from the existing job master.

    The release-sorted list is divided into strata and one contiguous block is
    sampled from each stratum.  This gives several different instances per size
    without inventing new processing data and avoids pathological samples spread
    over the whole 30-day horizon.
    """
    ordered = jobs.copy()
    ordered["Job_ID"] = ordered["Job_ID"].astype(str)
    ordered["Release_DateTime"] = pd.to_datetime(ordered["Release_DateTime"])
    ordered = ordered.sort_values(["Release_DateTime", "Priority", "Job_ID"]).reset_index(drop=True)

    if size <= 0 or size > len(ordered):
        raise ValueError(f"Invalid benchmark size {size}; dataset contains {len(ordered)} jobs")

    max_start = len(ordered) - size
    if max_start == 0:
        return ordered.copy()

    # Stratify possible block start positions so the instances cover early/mid/late horizon.
    npos = max_start + 1
    lo = math.floor(instance_index * npos / instances_per_size)
    hi = math.floor((instance_index + 1) * npos / instances_per_size) - 1
    hi = max(lo, min(hi, max_start))
    rng = random.Random(seed + size * 1009 + instance_index * 7919)
    start = rng.randint(lo, hi)
    return ordered.iloc[start : start + size].copy()


def resource_redundancy_check(
    data: Dict[str, pd.DataFrame],
    selected_job_ids: Sequence[str],
    relevant_shift_keys: Sequence[Tuple[str, str]],
    resource_scenario: str,
) -> dict:
    """Prove whether reusable resource capacities are dominated by station capacities.

    If TRUE, omitting unit-level resource binaries from the MILP does not change
    the feasible region because station capacity is already tighter.  The GA may
    still assign units explicitly for validation.
    """
    stations = data["stations"].copy()
    stations = stations[stations["Status"].astype(str).str.upper().isin(["ACTIVE", "1", "TRUE"])]
    station_cap = {str(r["Station_ID"]): int(r.get("Capacity", 1)) for _, r in stations.iterrows()}

    options = data["options"].copy()
    options = options[
        options["Job_ID"].astype(str).isin(selected_job_ids)
        & (pd.to_numeric(options.get("Eligible", 1), errors="coerce").fillna(1).astype(int) == 1)
    ]
    op_station = defaultdict(set)
    for _, r in options.iterrows():
        op_station[str(r["Operation_Code"])].add(str(r["Station_ID"]))

    req = data["resource_req"]
    rt_upper = defaultdict(int)
    details = {}
    for _, r in req.iterrows():
        op = str(r["Operation_Code"])
        rt = str(r["Resource_Type"])
        q = int(r["Required_Qty"])
        # Safe concurrency upper bound for this operation type = sum station capacities.
        op_cap = sum(station_cap.get(s, 1) for s in op_station.get(op, set()))
        rt_upper[rt] += q * op_cap

    capdf = data["base_cap"] if resource_scenario.upper() == "BASE" else data["disruption_cap"]
    cap_lookup = {}
    if capdf is not None and not capdf.empty:
        for _, r in capdf.iterrows():
            cap_lookup[(str(r["Calendar_Date"])[:10], str(r["Shift_ID"]), str(r["Resource_Type"]))] = int(r["Available_Qty"])
    base = {str(r["Resource_Type"]): int(r["Base_Capacity"]) for _, r in data["resource_master"].iterrows()}

    all_ok = True
    for rt, upper in sorted(rt_upper.items()):
        vals = [cap_lookup.get((d, sh, rt), base.get(rt, 0)) for d, sh in relevant_shift_keys]
        min_avail = min(vals) if vals else base.get(rt, 0)
        ok = min_avail >= upper
        all_ok = all_ok and ok
        details[rt] = {
            "station_implied_max_concurrent_demand": int(upper),
            "minimum_available_capacity": int(min_avail),
            "redundant": bool(ok),
        }
    return {"all_redundant": bool(all_ok), "details": details}


def build_instance(
    data: Dict[str, pd.DataFrame],
    selected: pd.DataFrame,
    name: str,
    source_path: Path,
    source_sha256: str,
    generator_seed: int,
    due_scenario: str,
    resource_scenario: str,
    horizon_days_after_last_release: float,
) -> dict:
    selected = selected.copy()
    selected["Job_ID"] = selected["Job_ID"].astype(str)
    job_ids = selected["Job_ID"].tolist()
    selected_set = set(job_ids)

    for c in ["Release_DateTime", "Element_Ready_Time", "Loopset_Release_Time", "Base_Due_DateTime"]:
        selected[c] = pd.to_datetime(selected[c], errors="raise")

    origin = selected["Release_DateTime"].min().to_pydatetime()
    last_release = selected["Release_DateTime"].max().to_pydatetime()
    horizon_end = last_release + timedelta(days=float(horizon_days_after_last_release))

    due_df = data["due"].copy()
    due_df = due_df[
        due_df["Job_ID"].astype(str).isin(selected_set)
        & (due_df["Due_Scenario_ID"].astype(str).str.upper() == due_scenario.upper())
    ].copy()
    due_df["Due_DateTime"] = pd.to_datetime(due_df["Due_DateTime"], errors="raise")
    due_map = {str(r["Job_ID"]): r["Due_DateTime"].to_pydatetime() for _, r in due_df.iterrows()}
    if set(due_map) != selected_set:
        raise ValueError(f"Due scenario {due_scenario} does not cover every selected job in {name}")

    jobs_out = []
    for _, r in selected.iterrows():
        j = str(r["Job_ID"])
        jobs_out.append(
            {
                "job_id": j,
                "order_id": str(r.get("Order_ID", "")),
                "transfer_batch_id": str(r.get("Transfer_Batch_ID", "")),
                "transfer_batch_scenario": str(r.get("Transfer_Batch_Scenario", "")),
                "product_code": str(r.get("Product_Code", "")),
                "batch_qty": int(r.get("Batch_Qty", 0)),
                "complexity_factor": float(r.get("Complexity_Factor", 1.0)),
                "priority": int(r.get("Priority", 999)),
                "release_datetime": dt(r["Release_DateTime"]).isoformat(sep=" "),
                "element_ready_datetime": dt(r["Element_Ready_Time"]).isoformat(sep=" "),
                "loopset_release_datetime": dt(r["Loopset_Release_Time"]).isoformat(sep=" "),
                "due_datetime": due_map[j].isoformat(sep=" "),
                "release_min": (dt(r["Release_DateTime"]) - origin).total_seconds() / 60.0,
                "element_ready_min": (dt(r["Element_Ready_Time"]) - origin).total_seconds() / 60.0,
                "loopset_release_min": (dt(r["Loopset_Release_Time"]) - origin).total_seconds() / 60.0,
                "due_min": (due_map[j] - origin).total_seconds() / 60.0,
            }
        )

    options_df = data["options"].copy()
    options_df = options_df[options_df["Job_ID"].astype(str).isin(selected_set)]
    options_out = []
    for _, r in options_df.iterrows():
        if int(r.get("Eligible", 1)) != 1:
            continue
        options_out.append(
            {
                "job_id": str(r["Job_ID"]),
                "operation_code": str(r["Operation_Code"]),
                "station_id": str(r["Station_ID"]),
                "station_group": str(r.get("Station_Group", "")),
                "eligible": 1,
                "processing_min": float(r["Deterministic_Processing_Time_Min"]),
            }
        )

    precedence_df = data["precedence"].copy()
    precedence_df = precedence_df[precedence_df["Job_ID"].astype(str).isin(selected_set)]
    precedence_out = [
        {
            "job_id": str(r["Job_ID"]),
            "pred": str(r["Predecessor_Operation"]),
            "succ": str(r["Successor_Operation"]),
            "lag_min": float(r["Minimum_Lag_Min"]),
            "relation_type": str(r.get("Relation_Type", "")),
        }
        for _, r in precedence_df.iterrows()
    ]

    all_windows = build_effective_windows(data)
    station_windows = {}
    relevant_shift_keys = set()
    for sid, ws in all_windows.items():
        rows = []
        seq = 0
        for s, e, d, sh in ws:
            if e <= origin or s >= horizon_end:
                continue
            seq += 1
            rows.append(
                {
                    "window_id": f"{sid}_W{seq:04d}",
                    "calendar_date": d,
                    "shift_id": sh,
                    "start_datetime": s.isoformat(sep=" "),
                    "end_datetime": e.isoformat(sep=" "),
                    "start_min": (s - origin).total_seconds() / 60.0,
                    "end_min": (e - origin).total_seconds() / 60.0,
                }
            )
            relevant_shift_keys.add((d, sh))
        station_windows[sid] = rows

    resource_requirements = defaultdict(list)
    for _, r in data["resource_req"].iterrows():
        resource_requirements[str(r["Operation_Code"])].append(
            {"resource_type": str(r["Resource_Type"]), "required_qty": int(r["Required_Qty"])}
        )

    units_by_type = defaultdict(list)
    for _, r in data["resource_units"].iterrows():
        if str(r.get("Status", "ACTIVE")).upper() in {"ACTIVE", "1", "TRUE"}:
            units_by_type[str(r["Resource_Type"])].append(str(r["Resource_Unit_ID"]))

    capdf = data["base_cap"] if resource_scenario.upper() == "BASE" else data["disruption_cap"]
    resource_capacity = []
    keyset = set(relevant_shift_keys)
    if capdf is not None and not capdf.empty:
        for _, r in capdf.iterrows():
            key = (str(r["Calendar_Date"])[:10], str(r["Shift_ID"]))
            if key in keyset:
                resource_capacity.append(
                    {
                        "calendar_date": key[0],
                        "shift_id": key[1],
                        "resource_type": str(r["Resource_Type"]),
                        "available_qty": int(r["Available_Qty"]),
                    }
                )

    base_resource_capacity = {
        str(r["Resource_Type"]): int(r["Base_Capacity"]) for _, r in data["resource_master"].iterrows()
    }

    station_master = [
        {
            "station_id": str(r["Station_ID"]),
            "station_group": str(r["Station_Group"]),
            "capacity": int(r.get("Capacity", 1)),
            "time_multiplier": float(r.get("Time_Multiplier", 1.0)),
        }
        for _, r in data["stations"].iterrows()
        if str(r.get("Status", "ACTIVE")).upper() in {"ACTIVE", "1", "TRUE"}
    ]

    redundancy = resource_redundancy_check(
        data, job_ids, sorted(relevant_shift_keys), resource_scenario
    )

    # Count options by job/operation to catch malformed frozen instances immediately.
    count = defaultdict(int)
    for r in options_out:
        count[(r["job_id"], r["operation_code"])] += 1
    missing_opts = [(j, op) for j in job_ids for op in OP_SEQUENCE if count[(j, op)] == 0]
    if missing_opts:
        raise ValueError(f"{name}: missing operation options: {missing_opts[:10]}")

    return {
        "metadata": {
            "schema_version": "AL13_BENCHMARK_V1",
            "instance_name": name,
            "size": len(job_ids),
            "source_dataset": str(source_path),
            "source_sha256": source_sha256,
            "generator_seed": int(generator_seed),
            "selection_method": "release_sorted_stratified_contiguous_block",
            "due_scenario": due_scenario.upper(),
            "resource_scenario": resource_scenario.upper(),
            "objective": "MIN_MAKESPAN",
            "origin_datetime": origin.isoformat(sep=" "),
            "last_release_datetime": last_release.isoformat(sep=" "),
            "horizon_end_datetime": horizon_end.isoformat(sep=" "),
            "horizon_end_min": (horizon_end - origin).total_seconds() / 60.0,
            "resource_constraints_redundant_under_station_capacity": redundancy["all_redundant"],
            "resource_redundancy_details": redundancy["details"],
        },
        "jobs": jobs_out,
        "station_master": station_master,
        "operation_options": options_out,
        "precedence": precedence_out,
        "station_windows": station_windows,
        "resource_requirements": dict(resource_requirements),
        "resource_units_by_type": dict(units_by_type),
        "base_resource_capacity": base_resource_capacity,
        "resource_capacity_by_shift": resource_capacity,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate fixed multi-size AL13 benchmark JSON instances.")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--sizes", nargs="+", type=int, default=[3, 5, 10, 20])
    ap.add_argument("--instances-per-size", type=int, default=4, choices=range(3, 6))
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--due-scenario", choices=["BASE", "TIGHT_65PCT", "URGENT_20PCT"], default="BASE")
    ap.add_argument("--resource-scenario", choices=["BASE", "DISRUPTION"], default="BASE")
    ap.add_argument(
        "--horizon-days-after-last-release",
        type=float,
        default=8.0,
        help="Frozen effective-window horizon after the latest release in each selected instance.",
    )
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading source dataset: {args.dataset}", flush=True)
    data = load_source(args.dataset)
    source_hash = sha256_file(args.dataset)

    manifest_rows = []
    all_names = set()
    for size in args.sizes:
        for idx in range(args.instances_per_size):
            selected = choose_contiguous_jobs(data["jobs"], size, idx, args.instances_per_size, args.seed)
            name = f"AL13_n{size:03d}_i{idx + 1:02d}"
            if name in all_names:
                raise RuntimeError(f"Duplicate instance name: {name}")
            all_names.add(name)

            inst = build_instance(
                data=data,
                selected=selected,
                name=name,
                source_path=args.dataset,
                source_sha256=source_hash,
                generator_seed=args.seed,
                due_scenario=args.due_scenario,
                resource_scenario=args.resource_scenario,
                horizon_days_after_last_release=args.horizon_days_after_last_release,
            )
            path = args.output_dir / f"{name}.json"
            path.write_text(json.dumps(inst, indent=2, ensure_ascii=False), encoding="utf-8")

            releases = pd.to_datetime(selected["Release_DateTime"])
            ids = selected["Job_ID"].astype(str).tolist()
            manifest_rows.append(
                {
                    "Instance_Name": name,
                    "Size": size,
                    "Instance_File": path.name,
                    "Job_IDs": ";".join(ids),
                    "Release_Start": releases.min(),
                    "Release_End": releases.max(),
                    "Release_Span_Hours": (releases.max() - releases.min()).total_seconds() / 3600.0,
                    "Due_Scenario": args.due_scenario,
                    "Resource_Scenario": args.resource_scenario,
                    "Resources_Redundant": inst["metadata"]["resource_constraints_redundant_under_station_capacity"],
                    "Source_SHA256": source_hash,
                }
            )
            print(
                f"Created {name}: jobs={size}, release span={manifest_rows[-1]['Release_Span_Hours']:.1f} h, "
                f"resources_redundant={manifest_rows[-1]['Resources_Redundant']}",
                flush=True,
            )

    manifest = pd.DataFrame(manifest_rows)
    manifest.to_csv(args.output_dir / "manifest.csv", index=False)
    config = {
        "dataset": str(args.dataset),
        "source_sha256": source_hash,
        "sizes": args.sizes,
        "instances_per_size": args.instances_per_size,
        "seed": args.seed,
        "due_scenario": args.due_scenario,
        "resource_scenario": args.resource_scenario,
        "horizon_days_after_last_release": args.horizon_days_after_last_release,
        "number_of_instances": len(manifest),
    }
    (args.output_dir / "generator_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDONE: {len(manifest)} frozen instances -> {args.output_dir}")
    print(f"Manifest: {args.output_dir / 'manifest.csv'}")


if __name__ == "__main__":
    main()
