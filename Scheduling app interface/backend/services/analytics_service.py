import json
import hashlib
from pathlib import Path
from collections import Counter
from backend.database.db import connect
from backend.services.input_service import dataset, jobs
from backend.errors import AppError

def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _context(run, request):
    try:
        frozen = json.loads(Path(run["instance_path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"calendar": None, "comparison_key": None}
    meta = frozen.get("metadata", {})
    calendar = {key: frozen.get(key) for key in ("station_windows", "resource_windows")}
    calendar.update(start=meta.get("origin_datetime"), end=meta.get("horizon_end_datetime"))
    return {"calendar": {**{k: calendar[k] for k in ("start", "end")}, "signature": _fingerprint(calendar)},
            # Conservative: exact frozen input and request settings must match.
            "comparison_key": _fingerprint([frozen, {k:v for k,v in request.items() if k != "dataset_id"},
                (json.loads(run["result"]).get("selected") or {}).get("algorithm")])}

def overview():
    with connect() as db:
        history = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()]
        execution = [dict(r) for r in db.execute("SELECT * FROM execution").fetchall()]
    accepted = next((r for r in sorted(history,key=lambda r:r["accepted_at"] or "",reverse=True) if r["accepted_at"]),None)
    run = accepted
    result = json.loads(run["result"]) if run else {}
    selected = result.get("selected") or {}
    try:
        current = dataset()
        current_jobs = jobs(current["id"])
        data = current["instance"]
    except AppError:
        current = None
        current_jobs,data = [],{"station_master":[],"station_windows":{}}
    # Utilization refers to the selected schedule's frozen windows, not today's wall clock.
    if run:
        data = json.loads(Path(run["instance_path"]).read_text(encoding="utf-8"))
    operations = selected.get("operations",[])
    workstations = []
    for s in data["station_master"]:
        sid = s["station_id"]
        station_ops = [o for o in operations if o["workstation"]==sid]
        available = sum(w["end_min"]-w["start_min"] for w in data["station_windows"].get(sid,[]))
        load = sum(o["processingMin"] for o in station_ops)
        workstations.append(dict(s,id=sid,stage=s["station_group"],currentJob=None,
            availability="Planned load" if station_ops else "No accepted operations",utilization=round(load/available*100,1) if run and available > 0 else None,
            status="Available" if available else "Downtime",upcomingJobs=list(dict.fromkeys(o["jobId"] for o in station_ops))))
    counts = Counter(r["status"] for r in execution)
    history_rows = []
    for r in history:
        saved = json.loads(r["result"])
        chosen = saved.get("selected") or {}
        request = json.loads(r["request"])
        attempted = [a["algorithm"] for a in saved.get("algorithms", []) if a.get("algorithm")]
        history_rows.append({"id":r["id"],"created_at":r["created_at"],"finished_at":r["finished_at"],
            "status":r["status"],"dataset_id":r["dataset_id"],"algo":chosen.get("algorithm") or ", ".join(attempted) or None,
            "makespan":chosen.get("makespan_min"),"runtime":chosen.get("runtime_sec"),
            "jobs":len(request["job_ids"]),"accepted":bool(r["accepted_at"]),"applied":bool(r["applied_at"]),
            "accepted_at":r["accepted_at"],"applied_at":r["applied_at"],
            "search_settings":request.get("settings", {}), **_context(r, request)})
    return {"total_jobs":len(current_jobs),"scheduled_jobs":len(execution),
            "completed_jobs":counts["Completed"],"in_progress_jobs":counts["In Progress"],"delayed_jobs":counts["Delayed"],
            "makespan_min":selected.get("makespan_min"),"total_tardiness_min":selected.get("total_tardiness_min"),
            "total_flow_time_min":selected.get("total_flow_time_min"),"tardy_jobs":selected.get("tardy_jobs"),
            "average_flow_time_min":selected["total_flow_time_min"]/len(json.loads(run["request"])["job_ids"]) if run and selected.get("total_flow_time_min") is not None and json.loads(run["request"])["job_ids"] else None,
            "current_dataset_id":current["id"] if current else None,
            "accepted_dataset_id":run["dataset_id"] if run else None,
            "accepted_calendar_signature":_fingerprint(data.get("station_windows", {})) if run else None,
            "accepted_applied":bool(run and run["applied_at"]),
            "solver":selected.get("algorithm"),"latest_accepted_schedule":run["id"] if run else None,
            "selection_reason":result.get("selection_reason","No accepted schedule."),
            "algorithms":result.get("algorithms",[]),"history":history_rows,"workstations":workstations,"jobs":current_jobs}
