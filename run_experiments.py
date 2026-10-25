#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch runner for AL13 V5 frozen instances: exact CP-SAT reference + GA.

The legacy project used columns named MILP_Result.  V5 replaces the old V4 MILP
backend with an exact CP-SAT interval model because the physical instance now
contains thousands of OIL-cycle and 2C-batch intervals.  This runner therefore
uses explicit EXACT_* naming and never labels a CP-SAT incumbent as MILP.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from solve_ga_instance import solve_instance as solve_ga
from solve_milp_instance import solve_instance as solve_exact

WINDOWS_DIR = Path(r"D:\back up giga\thesis\flowlien dataset")
DEFAULT_INSTANCES = WINDOWS_DIR / "benchmark_instances_v5"
DEFAULT_OUTPUT = WINDOWS_DIR / "benchmark_results_v5"

PROFILES = {
    "quick": {
        20: {"population": 24, "generations": 100, "runs": 2, "exact_time": 30},
        40: {"population": 32, "generations": 150, "runs": 2, "exact_time": 45},
        60: {"population": 40, "generations": 200, "runs": 2, "exact_time": 60},
        80: {"population": 48, "generations": 250, "runs": 2, "exact_time": 75},
        100: {"population": 56, "generations": 300, "runs": 2, "exact_time": 90},
        120: {"population": 64, "generations": 350, "runs": 2, "exact_time": 105},
        150: {"population": 72, "generations": 400, "runs": 2, "exact_time": 120},
        180: {"population": 80, "generations": 450, "runs": 2, "exact_time": 150},
    },
    "thesis": {
        20: {"population": 60, "generations": 1000, "runs": 5, "exact_time": 600},
        40: {"population": 70, "generations": 1200, "runs": 5, "exact_time": 900},
        60: {"population": 80, "generations": 1400, "runs": 5, "exact_time": 1200},
        80: {"population": 90, "generations": 1600, "runs": 5, "exact_time": 1500},
        100: {"population": 100, "generations": 1800, "runs": 5, "exact_time": 1800},
        120: {"population": 110, "generations": 2000, "runs": 5, "exact_time": 2400},
        150: {"population": 125, "generations": 2300, "runs": 5, "exact_time": 3000},
        180: {"population": 140, "generations": 2600, "runs": 5, "exact_time": 3600},
    },
    "heavy": {
        20: {"population": 100, "generations": 2000, "runs": 10, "exact_time": 1800},
        40: {"population": 120, "generations": 2500, "runs": 10, "exact_time": 2700},
        60: {"population": 140, "generations": 3000, "runs": 10, "exact_time": 3600},
        80: {"population": 160, "generations": 3500, "runs": 10, "exact_time": 4500},
        100: {"population": 180, "generations": 4000, "runs": 10, "exact_time": 5400},
        120: {"population": 200, "generations": 4500, "runs": 10, "exact_time": 6300},
        150: {"population": 220, "generations": 5000, "runs": 10, "exact_time": 7200},
        180: {"population": 240, "generations": 5500, "runs": 10, "exact_time": 9000},
    },
}

FINAL_COLUMNS = [
    "Instance_Name", "Size", "Exact_Result", "Exact_Time", "Exact_Proven_Optimal",
    "Exact_Bound", "Exact_Relative_Gap", "GA_Result", "GA_Time",
    "GA_vs_Exact_Percentage", "Comparison_Interpretation",
]


def json_load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def json_save(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def pct_diff(ga: Optional[float], exact_incumbent: Optional[float]) -> Optional[float]:
    if ga is None or exact_incumbent is None:
        return None
    if not math.isfinite(float(exact_incumbent)) or abs(float(exact_incumbent)) < 1e-12:
        return None
    v = (float(ga) - float(exact_incumbent)) / float(exact_incumbent) * 100.0
    return 0.0 if abs(v) < 1e-9 else v


def settings_for(size: int, profile: str, args) -> dict:
    if size in PROFILES[profile]:
        cfg = PROFILES[profile][size].copy()
    else:
        nearest = max(k for k in PROFILES[profile] if k <= size) if any(k <= size for k in PROFILES[profile]) else min(PROFILES[profile])
        cfg = PROFILES[profile][nearest].copy()
    if args.ga_population is not None:
        cfg["population"] = args.ga_population
    if args.ga_generations is not None:
        cfg["generations"] = args.ga_generations
    if args.ga_runs is not None:
        cfg["runs"] = args.ga_runs
    if args.exact_time_limit is not None:
        cfg["exact_time"] = args.exact_time_limit
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(description="Run AL13 V5 exact CP-SAT + GA benchmark")
    ap.add_argument("--instances-dir", type=Path, default=DEFAULT_INSTANCES)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--profile", choices=list(PROFILES), default="thesis")
    ap.add_argument("--sizes", nargs="*", type=int, default=None)
    ap.add_argument("--instance-names", nargs="*", default=None)

    ap.add_argument("--ga-population", type=int, default=None)
    ap.add_argument("--ga-generations", "--ga-epochs", dest="ga_generations", type=int, default=None)
    ap.add_argument("--ga-runs", type=int, default=None)
    ap.add_argument("--ga-seed-base", type=int, default=42)
    ap.add_argument("--ga-workers", type=int, default=0)
    ap.add_argument("--ga-progress-every", type=int, default=100)
    ap.add_argument("--ga-diversity-every", type=int, default=20)
    ap.add_argument("--ga-restart-after", type=int, default=250)
    ap.add_argument("--ga-save-convergence", action="store_true")

    ap.add_argument("--exact-time-limit", type=float, default=None)
    ap.add_argument("--exact-gap", type=float, default=0.0)
    ap.add_argument("--exact-workers", type=int, default=0)
    ap.add_argument("--exact-random-seed", type=int, default=1)
    ap.add_argument("--verbose-exact", action="store_true")

    ap.add_argument("--skip-exact", action="store_true")
    ap.add_argument("--skip-ga", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    if args.skip_exact and args.skip_ga:
        raise ValueError("Cannot use --skip-exact and --skip-ga together")

    manifest_path = args.instances_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}. Run generate_benchmark_instances.py first.")
    manifest = pd.read_csv(manifest_path)
    if args.sizes:
        manifest = manifest[manifest["Size"].astype(int).isin(args.sizes)]
    if args.instance_names:
        manifest = manifest[manifest["Instance_Name"].astype(str).isin(args.instance_names)]
    manifest = manifest.sort_values(["Size", "Instance_Name"]).reset_index(drop=True)
    if manifest.empty:
        raise ValueError("No instances selected")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    per_dir = args.output_dir / "per_instance"
    per_dir.mkdir(parents=True, exist_ok=True)
    final_rows, detailed_rows = [], []
    total_t0 = time.perf_counter()

    for pos, row in manifest.iterrows():
        name = str(row["Instance_Name"])
        size = int(row["Size"])
        inst_file = args.instances_dir / str(row["Instance_File"])
        cfg = settings_for(size, args.profile, args)
        case = per_dir / name
        case.mkdir(parents=True, exist_ok=True)
        exact_file = case / "exact_result.json"
        ga_file = case / "ga_result.json"

        print("=" * 100, flush=True)
        print(
            f"[{pos+1}/{len(manifest)}] {name} | n={size} | EXACT limit={cfg['exact_time']}s | "
            f"GA pop={cfg['population']} gen={cfg['generations']} runs={cfg['runs']}",
            flush=True,
        )

        exact_result = None
        if not args.skip_exact:
            if args.skip_existing and exact_file.exists():
                exact_result = json_load(exact_file)
                print("  -> EXACT loaded existing", flush=True)
            else:
                print("  -> EXACT START (CP-SAT V5)", flush=True)
                t0 = time.perf_counter()
                try:
                    exact_result = solve_exact(
                        inst_file, time_limit=float(cfg["exact_time"]), mip_rel_gap=args.exact_gap,
                        verbose=args.verbose_exact, keep_schedule=True, workers=args.exact_workers,
                        random_seed=args.exact_random_seed,
                    )
                except Exception as exc:
                    exact_result = {
                        "instance_name": name, "size": size, "feasible": False,
                        "proven_optimal": False, "makespan_min": None,
                        "best_bound_min": None, "relative_gap": None,
                        "runtime_sec": time.perf_counter() - t0,
                        "status_name": "ERROR", "message": f"{type(exc).__name__}: {exc}",
                    }
                json_save(exact_file, exact_result)
                print(
                    f"  -> EXACT DONE | Cmax={exact_result.get('makespan_min')} | "
                    f"time={exact_result.get('runtime_sec',0):.2f}s | "
                    f"optimal={exact_result.get('proven_optimal')} | bound={exact_result.get('best_bound_min')} | "
                    f"gap={exact_result.get('relative_gap')}", flush=True,
                )

        ga_result = None
        if not args.skip_ga:
            if args.skip_existing and ga_file.exists():
                ga_result = json_load(ga_file)
                print("  -> GA loaded existing", flush=True)
            else:
                print("  -> GA START", flush=True)
                t0 = time.perf_counter()
                try:
                    ga_result = solve_ga(
                        inst_file, population=int(cfg["population"]), generations=int(cfg["generations"]),
                        runs=int(cfg["runs"]), seed_base=args.ga_seed_base,
                        crossover_rate=0.90, mutation_rate=0.15, mutation_max=0.55,
                        elite=2, tournament_k=3, no_improve=0,
                        injection_fraction=0.25, diversity_threshold=0.25,
                        restart_after=args.ga_restart_after, restart_fraction=0.50,
                        collect_convergence=args.ga_save_convergence,
                        parallel_runs=args.ga_workers != 1, max_workers=args.ga_workers,
                        diversity_every=max(1, args.ga_diversity_every),
                        progress_every=max(0, args.ga_progress_every),
                    )
                except Exception as exc:
                    ga_result = {
                        "instance_name": name, "size": size, "best_makespan_min": None,
                        "total_runtime_sec": time.perf_counter() - t0,
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                json_save(ga_file, ga_result)
                print(
                    f"  -> GA DONE | Cmax={ga_result.get('best_makespan_min')} | "
                    f"wall={ga_result.get('total_runtime_sec',0):.2f}s | best_gen={ga_result.get('best_generation')}",
                    flush=True,
                )

        exact_c = exact_result.get("makespan_min") if exact_result else None
        ga_c = ga_result.get("best_makespan_min") if ga_result else None
        diff = pct_diff(ga_c, exact_c)
        if exact_result and exact_result.get("proven_optimal"):
            interp = "GA difference versus proven exact optimum"
        elif exact_result and exact_result.get("feasible"):
            interp = "GA difference versus exact-solver incumbent; NOT an optimality gap"
        elif args.skip_exact:
            interp = "Exact solver skipped"
        else:
            interp = "No exact incumbent available"

        final = {
            "Instance_Name": name,
            "Size": size,
            "Exact_Result": exact_c,
            "Exact_Time": exact_result.get("runtime_sec") if exact_result else None,
            "Exact_Proven_Optimal": exact_result.get("proven_optimal") if exact_result else None,
            "Exact_Bound": exact_result.get("best_bound_min") if exact_result else None,
            "Exact_Relative_Gap": exact_result.get("relative_gap") if exact_result else None,
            "GA_Result": ga_c,
            "GA_Time": ga_result.get("total_runtime_sec") if ga_result else None,
            "GA_vs_Exact_Percentage": diff,
            "Comparison_Interpretation": interp,
        }
        final_rows.append(final)
        detailed_rows.append({
            **final,
            "Exact_Backend": exact_result.get("solver") if exact_result else None,
            "Exact_Status": exact_result.get("status_name") if exact_result else None,
            "GA_Best_Seed": ga_result.get("best_seed") if ga_result else None,
            "GA_Best_Generation": ga_result.get("best_generation") if ga_result else None,
            "GA_Population": cfg["population"],
            "GA_Generations": cfg["generations"],
            "GA_Runs": cfg["runs"],
            "Instance_File": str(inst_file),
            "Source_SHA256": row.get("Source_SHA256"),
            "Schema_Version": row.get("Schema_Version", "AL13_BENCHMARK_V5"),
        })

        pd.DataFrame(final_rows, columns=FINAL_COLUMNS).to_csv(args.output_dir / "final_comparison_results.csv", index=False)
        pd.DataFrame(detailed_rows).to_csv(args.output_dir / "detailed_comparison_results.csv", index=False)
        print(f"  RESULT | EXACT={exact_c} | GA={ga_c} | diff%={diff} | {interp}", flush=True)

    elapsed = time.perf_counter() - total_t0
    json_save(args.output_dir / "experiment_summary.json", {
        "schema_version": "AL13_BENCHMARK_V5",
        "profile": args.profile,
        "instances": len(final_rows),
        "total_wall_time_sec": elapsed,
        "exact_backend": "OR-Tools CP-SAT exact interval scheduling (not MILP)",
        "comparison_formula": "((GA_Cmax - Exact_Incumbent_Cmax) / Exact_Incumbent_Cmax) * 100",
        "important_note": "This percentage is an optimality gap only when Exact_Proven_Optimal=True.",
        "final_csv": str(args.output_dir / "final_comparison_results.csv"),
        "detailed_csv": str(args.output_dir / "detailed_comparison_results.csv"),
    })
    print("=" * 100)
    print(f"DONE | {len(final_rows)} instances | wall={elapsed:.2f}s")
    print(f"Final table   : {args.output_dir / 'final_comparison_results.csv'}")
    print(f"Detailed table: {args.output_dir / 'detailed_comparison_results.csv'}")


if __name__ == "__main__":
    main()
