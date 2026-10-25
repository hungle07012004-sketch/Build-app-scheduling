#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Generate deterministic frozen AL13 V5 benchmark JSON instances.

This generator reads dataset_AL13.xlsx directly and follows Sheet
14_Benchmark_Manifest.  It does NOT resample jobs and it does NOT use the old V2
sheet names/schema.

Example (PowerShell / CMD):
    python generate_benchmark_instances.py ^
      --dataset "D:\back up giga\thesis\flowlien dataset\correct workstation\dataset_AL13.xlsx" ^
      --output-dir "D:\back up giga\thesis\flowlien dataset\correct workstation\benchmark_instances_v5"

Default benchmark sizes come from 14_Benchmark_Manifest:
20, 40, 60, 80, 100, 120, 150, 180 jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import pandas as pd

WINDOWS_DIR = Path(r"D:\back up giga\thesis\flowlien dataset\correct workstation")
DEFAULT_DATASET = WINDOWS_DIR / "dataset_AL13.xlsx"
DEFAULT_OUTPUT = WINDOWS_DIR / "benchmark_instances_v5"

SHEETS = {
    "config": "01_Config",
    "workstations": "03_Workstation_Master",
    "operators": "04_Operator_Master",
    "operation_master": "05_Operation_Master",
    "precedence_master": "06_Precedence_Master",
    "resource_req": "06B_Resource_Req",
    "setup_matrix": "07_Setup_Matrix",
    "setup_rules": "07B_Setup_Rules",
    "shift": "08_Shift_Calendar",
    "machine_downtime": "09_Machine_Downtime",
    "operator_downtime": "09B_Operator_Downtime",
    "jobs": "10_Jobs_30D",
    "job_operations": "11_Job_Operations",
    "job_precedence": "11B_Job_Precedence",
    "oil_cycles": "12_OIL_Cycles",
    "batching": "13_2C_Batching",
    "manifest": "14_Benchmark_Manifest",
    "validation": "16_Validation_Summary",
}

JOB_LEVEL_CODES = {"2B", "PRE", "FINAL", "LASER", "SCALE", "SEAL", "VISUAL"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def excel_datetime(value) -> datetime:
    """Handle Timestamp/datetime/string or raw Excel serial day number."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().replace(tzinfo=None)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError("Missing datetime value")
    if isinstance(value, (int, float)):
        # Excel 1900-date-system serial date; pandas/Excel convention uses 1899-12-30.
        return datetime(1899, 12, 30) + timedelta(days=float(value))
    return pd.to_datetime(value).to_pydatetime().replace(tzinfo=None)


def load_source(path: Path) -> Dict[str, pd.DataFrame]:
    if not path.exists():
        raise FileNotFoundError(path)
    xls = pd.ExcelFile(path)
    missing = [sheet for sheet in SHEETS.values() if sheet not in xls.sheet_names]
    if missing:
        raise ValueError(f"dataset_AL13.xlsx missing required V5 sheets: {missing}")
    return {k: pd.read_excel(path, sheet_name=s, header=1) for k, s in SHEETS.items()}


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the decorative first row if pandas happened to read it as data.

    Normally read_excel uses row 1 as header already.  This helper only removes
    fully empty rows and is intentionally conservative.
    """
    return df.dropna(how="all").reset_index(drop=True)


def subtract_block(windows: List[Tuple[datetime, datetime, str, str]], bs: datetime, be: datetime):
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


def build_resource_windows(data: Mapping[str, pd.DataFrame], origin: datetime, horizon_end: datetime) -> Dict[str, List[dict]]:
    """Build effective resource calendars over the requested planning horizon.

    The source workbook contains a 30-day staffed shift calendar.  For benchmark
    instances whose backlog can finish after day 30, the *shift pattern* is repeated
    deterministically until ``horizon_end``.  Release dates, processing times, setup
    rules, OIL logic, 2C batching, and all source jobs remain unchanged.

    Explicit machine/operator downtime rows are applied only at their source
    timestamps.  We do NOT invent future downtime events beyond the source data.
    """
    shift = data["shift"].copy()

    # First build the exact effective (break-free) windows from the source calendar.
    source_base: List[Tuple[datetime, datetime, str, str]] = []
    staffed_rows = []
    for _, r in shift.iterrows():
        if int(r.get("Staffed", 1)) != 1:
            continue
        s = excel_datetime(r["Shift_Start"])
        e = excel_datetime(r["Shift_End"])
        bs = excel_datetime(r["Break_Start"])
        be = excel_datetime(r["Break_End"])
        d = str(r.get("Date", ""))[:10]
        sh = str(r.get("Shift_Key", r.get("Shift_ID", "")))
        staffed_rows.append((s, e))
        if bs > s:
            source_base.append((s, min(bs, e), d, sh))
        if e > be:
            source_base.append((max(be, s), e, d, sh))

    if not staffed_rows:
        raise ValueError("08_Shift_Calendar has no staffed rows")

    source_calendar_start = min(s for s, _e in staffed_rows)
    source_calendar_end = max(e for _s, e in staffed_rows)
    repeat_span = source_calendar_end - source_calendar_start
    if repeat_span.total_seconds() <= 0:
        raise ValueError("Invalid staffed shift calendar span")

    # Repeat the frozen source shift pattern only as far as needed.  Cycle 0 is the
    # original calendar, so all windows inside the original 30-day horizon are byte-
    # for-byte equivalent in timing to the previous generator.
    base: List[Tuple[datetime, datetime, str, str]] = []
    cycle = 0
    while source_calendar_start + cycle * repeat_span < horizon_end:
        offset = cycle * repeat_span
        for s, e, _d, sh in source_base:
            rs = s + offset
            re = e + offset
            if re <= origin or rs >= horizon_end:
                continue
            # Keep source keys untouched in cycle 0.  Repeated cycles get a suffix so
            # diagnostics remain unambiguous; shift_id is not used by the decoder.
            rsh = sh if cycle == 0 else f"{sh}__R{cycle + 1}"
            base.append((rs, re, rs.date().isoformat(), rsh))
        cycle += 1
    base.sort(key=lambda x: (x[0], x[1], x[3]))

    ws_ids = [str(x) for x in data["workstations"]["Workstation_ID"].dropna().tolist()]
    op_ids = [str(x) for x in data["operators"]["Operator_ID"].dropna().tolist()]
    all_resources = ws_ids + op_ids

    md = data["machine_downtime"].copy()
    od = data["operator_downtime"].copy()
    md["Workstation_ID"] = md["Workstation_ID"].astype(str)
    od["Operator_ID"] = od["Operator_ID"].astype(str)

    out: Dict[str, List[dict]] = {}
    for rid in all_resources:
        windows = list(base)
        if rid.startswith("WS_"):
            rows = md[md["Workstation_ID"] == rid]
            for _, r in rows.iterrows():
                windows = subtract_block(windows, excel_datetime(r["Start"]), excel_datetime(r["End"]))
        else:
            rows = od[od["Operator_ID"] == rid]
            for _, r in rows.iterrows():
                windows = subtract_block(windows, excel_datetime(r["Start"]), excel_datetime(r["End"]))
        rows_out = []
        seq = 0
        for s, e, d, sh in sorted(windows, key=lambda x: (x[0], x[1], x[3])):
            if e <= origin or s >= horizon_end:
                continue
            s = max(s, origin)
            e = min(e, horizon_end)
            if e <= s:
                continue
            seq += 1
            rows_out.append({
                "window_id": f"{rid}_W{seq:04d}",
                "calendar_date": d,
                "shift_id": sh,
                "start_min": (s - origin).total_seconds() / 60.0,
                "end_min": (e - origin).total_seconds() / 60.0,
                "start_datetime": s.isoformat(sep=" "),
                "end_datetime": e.isoformat(sep=" "),
            })
        out[rid] = rows_out
    return out


def parse_setup_matrix(df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    # Expected columns: From \ To, BTA, BTB, BTC, BTD
    first = df.columns[0]
    products = [str(c) for c in df.columns[1:] if str(c) != "nan"]
    out = {}
    for _, r in df.iterrows():
        src = str(r[first])
        if src.lower() == "nan":
            continue
        out[src] = {dst: float(r[dst]) for dst in products}
    return out


def parse_2c_processing_min(resource_req: pd.DataFrame) -> float:
    rows = resource_req[resource_req["Activity"].astype(str).str.upper() == "2C_BATCH"]
    if rows.empty:
        return 4.5
    text = " ".join(str(x) for x in rows.iloc[0].tolist())
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*min", text, flags=re.I)
    return float(m.group(1)) if m else 4.5


def resource_mapping(data: Mapping[str, pd.DataFrame]) -> Tuple[Dict[str, List[str]], List[str]]:
    ws = data["workstations"]
    op_map = {}
    for _, r in ws.iterrows():
        code = str(r.get("Primary_Operation", ""))
        wid = str(r.get("Workstation_ID", ""))
        operator = str(r.get("Operator_Required", ""))
        if code in JOB_LEVEL_CODES:
            # OIL is excluded here; it is event-expanded.
            op_map[code] = [wid, operator]
        elif code == "2C_BATCH":
            op_map[code] = [wid, "OP_2C"]

    # Normalize physical V5 mappings explicitly (Operator_Required for OIL is descriptive text).
    op_map.update({
        "2B": ["WS_2B_INSPECTION", "OP_2B"],
        "PRE": ["WS_PRE_JIG", "OP_PRE"],
        "FINAL": ["WS_FINAL", "OP_FINAL"],
        "LASER": ["WS_LASER", "OP_2A"],
        "SCALE": ["WS_SCALE", "OP_2A"],
        "SEAL": ["WS_SEAL", "OP_PACK"],
        "VISUAL": ["WS_PACK_VISUAL", "OP_PACK"],
        "2C_BATCH": ["WS_2C_BATCH", "OP_2C"],
    })

    rr = data["resource_req"]
    setup_rows = rr[rr["Activity"].astype(str).str.upper() == "LINE_CHANGEOVER"]
    setup_resources = [str(x) for x in setup_rows["Resource"].tolist()]
    expected = ["OP_PRE", "OP_2A", "OP_PACK", "WS_PRE_JIG"]
    if set(setup_resources) != set(expected):
        raise ValueError(f"Unexpected LINE_CHANGEOVER resources: {setup_resources}; expected {expected}")
    return op_map, setup_resources


def build_instance(
    data: Mapping[str, pd.DataFrame],
    selected: pd.DataFrame,
    name: str,
    source_path: Path,
    source_sha256: str,
    resource_windows: Mapping[str, List[dict]],
    origin: datetime,
    horizon_end: datetime,
) -> dict:
    selected = selected.copy()
    selected["Job_ID"] = selected["Job_ID"].astype(str)
    selected = selected.sort_values("Benchmark_Rank").reset_index(drop=True)
    ids = selected["Job_ID"].tolist()
    selected_set = set(ids)

    # Exact-10 feasibility is a hard instance invariant.
    key_totals = selected.groupby("Batch_Key")["Batch_Qty"].sum()
    bad = {str(k): int(v) for k, v in key_totals.items() if int(v) % 10 != 0}
    if bad:
        raise ValueError(f"{name}: selected prefix is not exact-10 feasible: {bad}")
    bad_mod = selected[~selected["Batch_Qty"].astype(int).mod(10).isin([0, 5])]
    if not bad_mod.empty:
        raise ValueError(f"{name}: Qty_Mod_10 outside {{0,5}}; current V5 exact-pair batching optimization not applicable")

    jobs_out = []
    for _, r in selected.iterrows():
        release = excel_datetime(r["Release_DateTime"])
        elem = excel_datetime(r["Element_Ready_Time"])
        loop = excel_datetime(r["Loopset_Release_Time"])
        due = excel_datetime(r["Due_DateTime"])
        jobs_out.append({
            "job_id": str(r["Job_ID"]),
            "order_id": str(r.get("Order_ID", "")),
            "benchmark_rank": int(r.get("Benchmark_Rank", 0)),
            "release_rank": int(r.get("Release_Rank", 0)),
            "product_code": str(r["Product_Code"]),
            "sku_id": str(r.get("SKU_ID", "")),
            "size": str(r.get("Size", "")),
            "type": str(r.get("Type", "")),
            "specification": str(r.get("Specification", "")),
            "batch_key": str(r["Batch_Key"]),
            "batch_qty": int(r["Batch_Qty"]),
            "priority": int(r.get("Priority", 999)),
            "release_min": (release - origin).total_seconds() / 60.0,
            "element_ready_min": (elem - origin).total_seconds() / 60.0,
            "loopset_release_min": (loop - origin).total_seconds() / 60.0,
            "due_min": (due - origin).total_seconds() / 60.0,
            "release_datetime": release.isoformat(sep=" "),
            "due_datetime": due.isoformat(sep=" "),
        })

    jo = data["job_operations"].copy()
    jo["Job_ID"] = jo["Job_ID"].astype(str)
    jo = jo[jo["Job_ID"].isin(selected_set)]
    job_ops = []
    for _, r in jo.iterrows():
        code = str(r["Operation_Code"])
        if code not in JOB_LEVEL_CODES:
            continue
        job_ops.append({
            "job_id": str(r["Job_ID"]),
            "operation_code": code,
            "workstation_id": str(r["Workstation_ID"]),
            "primary_operator": str(r["Primary_Operator"]),
            "processing_min": float(r["Deterministic_Processing_Time_Min"]),
            "first_belt_completion_lag_min": (
                None if pd.isna(r.get("First_Belt_Completion_Lag_Min"))
                else float(r.get("First_Belt_Completion_Lag_Min"))
            ),
        })

    jp = data["job_precedence"].copy()
    jp["Job_ID"] = jp["Job_ID"].astype(str)
    jp = jp[jp["Job_ID"].isin(selected_set)]
    precedence = [
        {
            "job_id": str(r["Job_ID"]),
            "pred": str(r["Predecessor_Op"]),
            "succ": str(r["Successor_Op"]),
            "relation_type": str(r["Relation_Type"]),
            "lag_min": float(r["Min_Lag_Min"]),
        }
        for _, r in jp.iterrows()
    ]

    oc = data["oil_cycles"].copy()
    oc["Job_ID"] = oc["Job_ID"].astype(str)
    oc = oc[oc["Job_ID"].isin(selected_set)].sort_values(["Job_ID", "Cycle_No"])
    oil_cycles = [
        {
            "oil_cycle_id": str(r["Oil_Cycle_ID"]),
            "job_id": str(r["Job_ID"]),
            "cycle_no": int(r["Cycle_No"]),
            "cycle_qty": int(r["Cycle_Qty"]),
            "w4_load_min": float(r["W4_Load_Min"]),
            "auto_dip_min": float(r["Auto_Dip_Min"]),
            "w5_unload_min": float(r["W5_Unload_Min"]),
            "machine_occupancy_min": float(r["Machine_Occupancy_Min"]),
        }
        for _, r in oc.iterrows()
    ]

    setup_matrix = parse_setup_matrix(data["setup_matrix"])
    op_map, setup_resources = resource_mapping(data)
    batch_p = parse_2c_processing_min(data["resource_req"])

    # Validate operation and precedence completeness.
    op_count = defaultdict(int)
    for r in job_ops:
        op_count[(r["job_id"], r["operation_code"])] += 1
    missing_ops = [(j, op) for j in ids for op in sorted(JOB_LEVEL_CODES) if op_count[(j, op)] != 1]
    if missing_ops:
        raise ValueError(f"{name}: missing/duplicate job operations: {missing_ops[:10]}")
    cycle_qty = defaultdict(int)
    for r in oil_cycles:
        if int(r["cycle_qty"]) > 6:
            raise ValueError(f"{name}: OIL cycle >6 belts: {r}")
        cycle_qty[r["job_id"]] += int(r["cycle_qty"])
    bad_oil = {j: (cycle_qty[j], int(selected.loc[selected["Job_ID"] == j, "Batch_Qty"].iloc[0])) for j in ids if cycle_qty[j] != int(selected.loc[selected["Job_ID"] == j, "Batch_Qty"].iloc[0])}
    if bad_oil:
        raise ValueError(f"{name}: OIL cycle quantities do not conserve job quantity: {bad_oil}")

    return {
        "metadata": {
            "schema_version": "AL13_BENCHMARK_V5",
            "instance_name": name,
            "size": len(ids),
            "source_dataset": str(source_path),
            "source_sha256": source_sha256,
            "objective": "MIN_CMAX_FINAL_2C",
            "origin_datetime": origin.isoformat(sep=" "),
            "horizon_end_datetime": horizon_end.isoformat(sep=" "),
            "horizon_end_min": (horizon_end - origin).total_seconds() / 60.0,
            "physical_flow": "2B(aux)->PRE; PRE->FINAL->LASER->SCALE->OIL->SEAL->VISUAL->2C_BATCH",
            "pre_final_streaming": "SS_FIRST_BELT + C_FINAL>=C_PRE",
            "oil_model": "explicit_cycles_max6_sequential_nonpreemptive; OP_2A load only; OP_PACK unload only",
            "batching_model": "exact_10_by_Batch_Key; cross-job 5+5 remainder pooling",
            "setup_model": "composite_once_before_successor_PRE_on_product_transition; starts after predecessor final 2C clearance",
            "setup_requires_predecessor_2c_clear": True,
            "planning_horizon_days": (horizon_end - origin).total_seconds() / 86400.0,
            "calendar_extension_policy": (
                "repeat_source_staffed_shift_pattern; preserve_breaks; "
                "apply_only_explicit_source_downtime_timestamps; no_synthetic_future_downtime"
            ),
        },
        "jobs": jobs_out,
        "job_operations": job_ops,
        "precedence": precedence,
        "oil_cycles": oil_cycles,
        "setup_matrix": setup_matrix,
        "setup_resources": setup_resources,
        "resource_by_operation": op_map,
        "resource_windows": {str(k): list(v) for k, v in resource_windows.items()},
        "batching": {
            "batch_size": 10,
            "processing_min_per_batch": float(batch_p),
            "cross_job_pooling": True,
            "compatibility_key": "Product_Code+Size+Type+Specification",
            "remainder_structure": "Qty_Mod_10 in {0,5}; pair 5+5 inside same Batch_Key",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate frozen AL13 V5 benchmark instances from dataset_AL13.xlsx")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--sizes", nargs="*", type=int, default=None,
                    help="Subset of V5 manifest sizes. Default: every row in 14_Benchmark_Manifest")
    ap.add_argument(
        "--horizon-days", type=int, default=60,
        help=(
            "Planning horizon length in calendar days from the first staffed shift. "
            "Default: 60. The source staffed shift pattern is repeated beyond its "
            "native 30-day calendar; future downtime is not synthesized."
        ),
    )
    args = ap.parse_args()
    if args.horizon_days <= 0:
        raise ValueError("--horizon-days must be a positive integer")

    print(f"Loading V5 dataset: {args.dataset}", flush=True)
    data = {k: clean_df(v) for k, v in load_source(args.dataset).items()}
    source_hash = sha256_file(args.dataset)

    validation = data["validation"]
    if "PASS" in validation.columns and not validation["PASS"].astype(str).str.upper().eq("PASS").all():
        bad = validation[~validation["PASS"].astype(str).str.upper().eq("PASS")]
        raise ValueError(f"Dataset validation sheet contains non-PASS rows:\n{bad}")

    shift = data["shift"]
    origin = min(excel_datetime(x) for x in shift["Shift_Start"].dropna())
    source_calendar_end = max(excel_datetime(x) for x in shift["Shift_End"].dropna())
    horizon_end = origin + timedelta(days=int(args.horizon_days))
    if horizon_end < source_calendar_end:
        print(
            f"WARNING: requested horizon ({horizon_end}) is shorter than source shift calendar "
            f"({source_calendar_end}); source windows will be clipped.",
            flush=True,
        )
    elif horizon_end > source_calendar_end:
        print(
            f"Extending staffed shift pattern: source_end={source_calendar_end} -> "
            f"planning_end={horizon_end} ({args.horizon_days} days). "
            "Source jobs/releases/due dates are unchanged; no future downtime is synthesized.",
            flush=True,
        )
    resource_windows = build_resource_windows(data, origin, horizon_end)

    jobs = data["jobs"].copy()
    jobs["Job_ID"] = jobs["Job_ID"].astype(str)
    jobs["Benchmark_Rank"] = jobs["Benchmark_Rank"].astype(int)
    jobs = jobs.sort_values("Benchmark_Rank").reset_index(drop=True)

    manifest = data["manifest"].copy()
    manifest["N_Jobs"] = manifest["N_Jobs"].astype(int)
    if args.sizes:
        manifest = manifest[manifest["N_Jobs"].isin(args.sizes)]
        missing = sorted(set(args.sizes) - set(manifest["N_Jobs"]))
        if missing:
            raise ValueError(f"Requested sizes not present in V5 manifest: {missing}")
    manifest = manifest.sort_values("N_Jobs").reset_index(drop=True)
    if manifest.empty:
        raise ValueError("No benchmark instances selected")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for _, mr in manifest.iterrows():
        n = int(mr["N_Jobs"])
        name = str(mr["Instance_ID"])
        selected = jobs.iloc[:n].copy()
        inst = build_instance(
            data=data, selected=selected, name=name, source_path=args.dataset,
            source_sha256=source_hash, resource_windows=resource_windows,
            origin=origin, horizon_end=horizon_end,
        )
        out = args.output_dir / f"{name}.json"
        out.write_text(json.dumps(inst, indent=2, ensure_ascii=False), encoding="utf-8")
        total_belts = int(selected["Batch_Qty"].sum())
        rows.append({
            "Instance_Name": name,
            "Size": n,
            "Instance_File": out.name,
            "Job_From": selected.iloc[0]["Job_ID"],
            "Job_To": selected.iloc[-1]["Job_ID"],
            "Total_Belts": total_belts,
            "Exact_2C_Batches": total_belts // 10,
            "Oil_Cycles": len(inst["oil_cycles"]),
            "Source_SHA256": source_hash,
            "Schema_Version": "AL13_BENCHMARK_V5",
            "Planning_Horizon_Days": int(args.horizon_days),
        })
        print(f"Created {name}: jobs={n}, belts={total_belts}, oil_cycles={len(inst['oil_cycles'])}, 2C_batches={total_belts//10}", flush=True)

    pd.DataFrame(rows).to_csv(args.output_dir / "manifest.csv", index=False)
    (args.output_dir / "generator_config.json").write_text(json.dumps({
        "dataset": str(args.dataset),
        "source_sha256": source_hash,
        "schema_version": "AL13_BENCHMARK_V5",
        "planning_horizon_days": int(args.horizon_days),
        "source_calendar_end": source_calendar_end.isoformat(sep=" "),
        "planning_horizon_end": horizon_end.isoformat(sep=" "),
        "calendar_extension_policy": (
            "repeat_source_staffed_shift_pattern; preserve_breaks; "
            "apply_only_explicit_source_downtime_timestamps; no_synthetic_future_downtime"
        ),
        "instances": [r["Instance_Name"] for r in rows],
        "sizes": [r["Size"] for r in rows],
    }, indent=2), encoding="utf-8")
    print(f"DONE -> {args.output_dir}", flush=True)


if __name__ == "__main__":
    main()
