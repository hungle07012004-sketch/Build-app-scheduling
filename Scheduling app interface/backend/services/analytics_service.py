import json
from collections import Counter
from backend.database.db import connect
from backend.services.input_service import dataset, jobs
from backend.errors import AppError

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
        current_jobs,data = [],{"station_master":[],"station_windows":{}}
    # Utilization refers to the selected schedule's frozen windows, not today's wall clock.
    if run:
        from pathlib import Path
        data = json.loads(Path(run["instance_path"]).read_text(encoding="utf-8"))
    operations = selected.get("operations",[])
    workstations = []
    for s in data["station_master"]:
        sid = s["station_id"]
        station_ops = [o for o in operations if o["workstation"]==sid]
        available = sum(w["end_min"]-w["start_min"] for w in data["station_windows"].get(sid,[]))
        load = sum(o["processingMin"] for o in station_ops)
        workstations.append(dict(s,id=sid,stage=s["station_group"],currentJob=None,
            availability="Planned load" if station_ops else "No accepted operations",utilization=round(load/available*100,1) if available else 0,
            status="Available" if available else "Downtime",upcomingJobs=list(dict.fromkeys(o["jobId"] for o in station_ops))))
    counts = Counter(r["status"] for r in execution)
    history_rows = []
    for r in history:
        chosen = json.loads(r["result"]).get("selected")
        if chosen:
            history_rows.append({"id":r["id"],"created_at":r["created_at"],"algo":chosen["algorithm"],
                "makespan":chosen["makespan_min"],"runtime":chosen["runtime_sec"],
                "jobs":len(json.loads(r["request"])["job_ids"]),"accepted":bool(r["accepted_at"])})
    return {"total_jobs":len(current_jobs),"scheduled_jobs":len(execution),
            "completed_jobs":counts["Completed"],"in_progress_jobs":counts["In Progress"],"delayed_jobs":counts["Delayed"],
            "makespan_min":selected.get("makespan_min"),"total_tardiness_min":selected.get("total_tardiness_min"),
            "total_flow_time_min":selected.get("total_flow_time_min"),"tardy_jobs":selected.get("tardy_jobs"),
            "average_flow_time_min":selected.get("total_flow_time_min",0)/len(json.loads(run["request"])["job_ids"]) if run else None,
            "solver":selected.get("algorithm"),"latest_accepted_schedule":run["id"] if run else None,
            "selection_reason":result.get("selection_reason","No accepted schedule."),
            "algorithms":result.get("algorithms",[]),"history":history_rows,"workstations":workstations,"jobs":current_jobs}
