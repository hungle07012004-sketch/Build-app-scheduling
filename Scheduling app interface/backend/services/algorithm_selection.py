"""Deterministic, configurable heuristics; not claims of best algorithm quality."""
import importlib.util
import os
from pathlib import Path
from backend.config import ROOT, SMALL_SCALE_MAX_JOBS


def rules():
    return {key: float(os.environ.get("AUTO_" + key.upper(), default)) for key, default in {
        "cp_max_jobs": SMALL_SCALE_MAX_JOBS, "cp_max_events": 250,
        "cp_max_resources": 30, "cp_max_windows": 600,
        "cp_max_score": 450, "cp_min_seconds": 60,
        "mlpso_min_operations": 400, "mlpso_min_resources": 30,
        "mlpso_min_windows": 1500,
    }.items()}


def availability(v5):
    def exists(folder, file):
        return (ROOT / folder / file).is_file()
    cp = v5 and exists("backend/solver_v5", "solve_milp_instance.py") and importlib.util.find_spec("ortools") is not None
    ga = exists("backend/solver_v5" if v5 else "code", "solve_ss_micro_ga_fast_instance.py" if v5 else "solve_improved_ga_ig_v3_earlystop_instance.py")
    mlpso = exists("backend/solver_v5" if v5 else "code", "solve_mlpso_v3_instance.py" if v5 else "solve_mlpso_instance.py")
    return {
        "CP-SAT": {"available": bool(cp), "reason": "" if cp else "CP-SAT is available only with compatible V5 data and OR-Tools installed."},
        "SS Micro-GA Fast" if v5 else "Improved GA": {"available": ga, "reason": "" if ga else "The GA implementation is missing."},
        "MLPSO": {"available": mlpso, "reason": "" if mlpso else "No V5-compatible MLPSO implementation is connected." if v5 else "The MLPSO implementation is missing."},
    }


def select(size, data=None, settings=None):
    from backend.services.v5_service import is_v5
    data = data or {}
    settings = settings or {}
    v5 = is_v5(data)
    limits = rules()
    engines = availability(v5)
    windows = data.get("resource_windows", data.get("station_windows", {}))
    if v5:
        job_ops = len(data.get("job_operations", []))
        cycles = len(data.get("oil_cycles", []))
        batches = sum(int(j.get("batch_qty",0)) for j in data.get("jobs", [])) // 10
        setups = size * len(data.get("setup_resources", []))
        resources = len(set(windows) | {r for rs in data.get("resource_by_operation", {}).values() for r in rs})
    else:
        job_ops = len({(o["job_id"],o["operation_code"]) for o in data.get("operation_options",[])}) or size * 10
        cycles = batches = setups = 0
        resources = len(data.get("station_master", [])) + sum(len(rs) for rs in data.get("resource_units_by_type", {}).values())
    events = job_ops + cycles + batches + setups
    window_count = sum(len(ws) for ws in windows.values())
    edges = len(data.get("precedence", []))
    score = events + 0.2 * edges + 3 * resources + 0.05 * window_count
    budget = float(settings.get("exact_time_limit",120))
    features = dict(jobs=size,operations=job_ops,estimated_events=events,oil_cycles=cycles,
        estimated_batches=batches,resources=resources,calendar_windows=window_count,
        precedence_edges=edges,difficulty_score=round(score,2),cp_time_budget_seconds=budget)
    checks = {"job count": size <= limits["cp_max_jobs"], "event count": events <= limits["cp_max_events"],
        "resources": resources <= limits["cp_max_resources"], "calendar complexity": window_count <= limits["cp_max_windows"],
        "difficulty score": score <= limits["cp_max_score"], "runtime budget": budget >= limits["cp_min_seconds"]}
    ga = "SS Micro-GA Fast" if v5 else "Improved GA"
    if engines["CP-SAT"]["available"] and all(checks.values()):
        preferred = "CP-SAT"
        reason = f"{size} jobs, {events} estimated events, {resources} resources and {window_count} calendar windows fit the configured exact-search thresholds with a {budget:g}s search budget."
    elif not v5 and (events >= limits["mlpso_min_operations"] or resources >= limits["mlpso_min_resources"] or window_count >= limits["mlpso_min_windows"]):
        preferred = "MLPSO"
        reason = f"The legacy instance has {events} operations, {resources} resources and {window_count} windows; the configured complexity rule prefers the available MLPSO engine."
    else:
        preferred = ga
        exceeded = ", ".join(k for k,ok in checks.items() if not ok)
        reason = (f"GA is preferred because exact-search thresholds are not met ({exceeded}). " if exceeded else "GA is preferred because CP-SAT is unavailable for this dataset. ") + f"Estimated events: {events}; resources: {resources}; calendar windows: {window_count}."
    available = [a for a,d in engines.items() if d["available"]]
    if not engines[preferred]["available"]:
        original = preferred
        preferred = next((a for a in [ga,"MLPSO","CP-SAT"] if a in available),None)
        reason = f"{original} is unavailable. " + (f"Using the available {preferred} engine." if preferred else "No compatible algorithm is available.")
    return dict(size=size, classification="Small Scale" if size<=SMALL_SCALE_MAX_JOBS else "Large Scale",
        algorithms=[preferred] if preferred else [], small_scale_max_jobs=SMALL_SCALE_MAX_JOBS,
        schema_version="AL13_BENCHMARK_V5" if v5 else "legacy", routing_reason=reason,
        selection_mode="Automatic", selection_features=features, selection_rules=limits,
        availability=engines)
