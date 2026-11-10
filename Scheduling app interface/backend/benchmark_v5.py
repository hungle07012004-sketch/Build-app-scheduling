"""Isolated V5 paired benchmarks. Never reads/writes the application database."""
import argparse
import contextlib
import csv
import hashlib
import inspect
import json
import math
import platform
import subprocess
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from backend.models.schemas import SolverSettings
from backend.services import v5_service
from backend.services.algorithms import v5_adapter
from backend.services.result_standardizer import json_metadata, standardize
from backend.solver_v5 import solve_milp_instance as exact
from backend.solver_v5 import solve_ss_micro_ga_fast_instance as micro

ROOT = Path(__file__).resolve().parent.parent
OBJECTIVE = "MIN_CMAX_FINAL_2C"
ALGORITHMS = ("CP-SAT", "SS Micro-GA Fast")
PROTOCOL = {
    "name": "paired-single-worker-search-cap-v1",
    "objective": OBJECTIVE,
    "attempts_per_algorithm_per_seed": 1,
    "workers": 1,
    "execution": "Sequential; algorithm order alternates between seeds/repeats.",
    "budget": "Same nominal search-time cap per algorithm, NOT equal end-to-end time or equal evaluations.",
    "micro_budget": "Includes seed refinement; cooperative stop between decodes; final reconstruction/validation may overrun. Epoch/decode/patience caps remain active.",
    "cp_budget": "CP-SAT Solve() only; excludes model construction and output validation. Relative gap tolerance is zero.",
    "reproducibility": "Same seed alone does not guarantee identical results under wall-clock stopping; record settings, hashes, workers and actual work.",
    "reuse": "No cache/resume. Output directory must not exist; every invocation computes fresh results.",
    "nonfinite_metadata": "Non-finite values are stored as null, never as zero.",
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encode(value):
    return json.dumps(json_metadata(value), sort_keys=True, ensure_ascii=False, allow_nan=False)


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encode(value))


def source_hashes():
    return {str(p.relative_to(ROOT)): digest(p.read_bytes())
            for p in sorted((ROOT / "backend").rglob("*.py"))
            if "tests" not in p.relative_to(ROOT).parts and "__pycache__" not in p.parts}


def provenance():
    def git(*args):
        try:
            return subprocess.check_output(["git", *args], cwd=ROOT, text=True,
                                           stderr=subprocess.DEVNULL, timeout=10).strip()
        except (OSError, subprocess.SubprocessError):
            return None
    defaults = {}
    for name, module in zip(ALGORITHMS, (exact, micro)):
        defaults[name] = {k: p.default for k, p in inspect.signature(module.solve_instance).parameters.items()
                          if p.default is not inspect.Parameter.empty}
    return {
        "source_sha256": source_hashes(), "git_head": git("rev-parse", "HEAD"),
        "git_status": git("status", "--porcelain"),
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {name: version(name) for name in ("ortools", "numpy", "pandas", "scipy", "pydantic")},
        "solver_signature_defaults": defaults,
        "entrypoints": {"CP-SAT": exact.solve_instance.__module__,
                        "SS Micro-GA Fast": micro.solve_instance.__module__},
    }


def compare(cp, ga):
    """Only compare independently validated results belonging to the same pair."""
    out = {"comparable": False, "difference_to_cp_incumbent_percent": None,
           "ga_gap_to_proven_optimum_percent": None, "issue": None}
    if any(cp.get(k) != ga.get(k) for k in ("experiment_fingerprint", "input_sha256", "seed", "repeat")):
        out["issue"] = "Mismatched input, configuration/version, seed or repeat."
        return out
    if not cp.get("feasible") or not ga.get("feasible"):
        out["issue"] = "Both algorithms need validated feasible results."
        return out
    u, g = cp.get("makespan_min"), ga.get("makespan_min")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (u, g)):
        out["issue"] = "Missing/invalid Cmax reference."
        return out
    if cp.get("proven_optimal") and g < u - .0025:
        out["issue"] = "GA beats the claimed CP optimum beyond timestamp tolerance; investigate model/result consistency."
        return out
    out["comparable"] = True
    out["difference_to_cp_incumbent_percent"] = 100 * (g - u) / u
    if cp.get("proven_optimal"):
        out["ga_gap_to_proven_optimum_percent"] = out["difference_to_cp_incumbent_percent"]
    return out


def run_benchmark(instance_path, output_dir, *, seconds, seeds, repeats=1, settings=None):
    if not seeds or len(set(seeds)) != len(seeds) or repeats < 1:
        raise ValueError("Provide unique seeds and at least one repeat.")
    # Protocol overrides are local to this experiment, never persisted in the App.
    configs = [SolverSettings(**{**(settings or {}), "seed_base": seed, "runs": 1,
                                "exact_workers": 1, "exact_time_limit": seconds,
                                "micro_time_limit": seconds}).model_dump() for seed in seeds]
    source = Path(instance_path).resolve()
    payload = source.read_bytes()
    data = json.loads(payload)
    issues = v5_service.validate(data)
    if issues:
        raise ValueError(f"Invalid frozen V5 input: {issues}")
    code = provenance()
    input_hash = digest(payload)
    identity = {"input_sha256": input_hash, "protocol": PROTOCOL, "settings_by_seed": configs,
                "repeats": repeats, "code": code}
    fingerprint = digest(encode(identity).encode("utf-8"))
    folder = Path(output_dir).resolve()
    folder.mkdir(parents=True, exist_ok=False)  # Refuse ALL existing output, including empty directories.
    frozen = folder / "frozen.json"
    with frozen.open("xb") as stream:
        stream.write(payload)
    write_json(folder / "manifest.json", {
        **identity, "experiment_fingerprint": fingerprint,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "source": str(source),
        "calendar_sha256": digest(encode(data["resource_windows"]).encode("utf-8")),
        "instance_metadata": data["metadata"], "job_count": len(data["jobs"]),
        "requested_settings": settings or {},
    })

    def unchanged():
        if digest(frozen.read_bytes()) != input_hash or digest(source.read_bytes()) != input_hash:
            raise RuntimeError("Input changed during benchmark; comparison aborted.")
        if source_hashes() != code["source_sha256"]:
            raise RuntimeError("Source code changed during benchmark; comparison aborted.")

    rows, comparisons = [], []
    pair_number = 0
    for repeat in range(1, repeats + 1):
        for config in configs:
            pair = {}
            order = ALGORITHMS if pair_number % 2 == 0 else tuple(reversed(ALGORITHMS))
            pair_number += 1
            for position, algorithm in enumerate(order, 1):
                unchanged()
                seed = config["seed_base"]
                stem = f"seed-{seed}-repeat-{repeat}-{'cp' if algorithm == 'CP-SAT' else 'micro'}"
                raw, validated, error = {}, {}, None
                started = time.perf_counter()
                try:
                    with (folder / f"{stem}.log").open("x", encoding="utf-8") as log, contextlib.redirect_stdout(log):
                        solver = v5_adapter.solve_exact if algorithm == "CP-SAT" else v5_adapter.solve_micro
                        raw = solver(frozen, config)
                        validated = standardize(algorithm, raw, data)
                        if validated.get("objective") != OBJECTIVE:
                            raise ValueError("Unexpected standardized objective.")
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = time.perf_counter() - started
                unchanged()
                feasible = bool(validated.get("feasible")) and error is None
                cmax = validated.get("makespan_min") if feasible else None
                bound = raw.get("best_bound_min") if algorithm == "CP-SAT" else None
                valid_bound = (feasible and isinstance(bound, (int, float)) and math.isfinite(bound)
                               and 0 <= bound <= cmax + 1e-9)
                optimal = bool(valid_bound and raw.get("proven_optimal") and abs(cmax - bound) <= 1e-9)
                summaries = raw.get("run_summaries") or []
                row = {
                    "algorithm": algorithm, "seed": seed, "repeat": repeat, "execution_position": position,
                    "experiment_fingerprint": fingerprint, "input_sha256": input_hash,
                    "objective": OBJECTIVE, "job_count": len(data["jobs"]), "budget_sec": seconds,
                    "feasible": feasible, "proven_optimal": optimal, "makespan_min": cmax,
                    "total_tardiness_min": validated.get("total_tardiness_min") if feasible else None,
                    "total_flow_time_min": validated.get("total_flow_time_min") if feasible else None,
                    "runtime_end_to_end_sec": elapsed,
                    "runtime_solver_reported_sec": raw.get("runtime_sec", raw.get("total_runtime_sec")),
                    "evaluations": sum(r["unique_evaluations"] for r in summaries) if summaries else None,
                    "seed_refinement_evaluations": (raw.get("refined_seed_info") or {}).get("new_decodes"),
                    "epochs": summaries[0]["epochs_completed"] if summaries else None,
                    "stop_reason": raw.get("search_stop_reason") if algorithm == "SS Micro-GA Fast" else
                        ("optimal_bound_closed" if optimal else f"solver_status:{raw.get('status_name', 'not_reported')};cause_not_exposed"),
                    "status": validated.get("status", raw.get("status_name")),
                    "best_bound_min": bound if valid_bound else None,
                    "cp_gap_ratio": max(0, (cmax - bound) / cmax) if valid_bound and cmax > 0 else None,
                    "cp_branches": raw.get("num_branches"), "cp_conflicts": raw.get("num_conflicts"),
                    "error": error, "validation_issues": validated.get("validation_issues", []),
                }
                write_json(folder / f"{stem}.json", {"summary": row, "settings": config,
                    "raw": raw, "validated": validated})
                pair[algorithm] = row
                rows.append(row)
            comparisons.append({"seed": config["seed_base"], "repeat": repeat,
                                **compare(pair["CP-SAT"], pair["SS Micro-GA Fast"])})
    write_json(folder / "results.json", {"runs": rows, "comparisons": comparisons})
    with (folder / "results.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: encode(v) if isinstance(v, list) else v for k, v in row.items()} for row in rows)
    return {"output_dir": str(folder), "runs": rows, "comparisons": comparisons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Must be a NEW directory; no resume/overwrite.")
    parser.add_argument("--seconds", type=float, required=True, help="Equal nominal search cap, not total elapsed.")
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--settings", type=Path, help="Optional SolverSettings JSON; protocol overrides seed/runs/workers/time.")
    args = parser.parse_args()
    result = run_benchmark(args.instance, args.output_dir, seconds=args.seconds, seeds=args.seeds,
                           repeats=args.repeats, settings=json.loads(args.settings.read_text()) if args.settings else None)
    print(encode({"output_dir": result["output_dir"], "comparisons": result["comparisons"]}))
    if any(r["error"] for r in result["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
