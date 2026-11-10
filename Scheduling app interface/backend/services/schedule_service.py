import json
from pathlib import Path
from backend.database.db import connect, now
from backend.errors import AppError

def get_run(run_id):
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row:
        raise AppError("RUN_NOT_FOUND","Scheduling run not found.",status=404)
    result = dict(row)
    for field in ("request","result"):
        result[field] = json.loads(result[field])
    return result

def status(run_id):
    run = get_run(run_id)
    with connect() as db:
        algorithms = db.execute("SELECT algorithm,status,result FROM algorithm_results WHERE run_id=?",(run_id,)).fetchall()
    from backend.services.progress_service import read_progress
    return {"run_id":run_id,"status":run["status"],"created_at":run["created_at"],"finished_at":run["finished_at"],
            "accepted_at":run["accepted_at"],"applied_at":run["applied_at"],
            "algorithms":[dict(algorithm=r["algorithm"],status=r["status"],result=json.loads(r["result"]),progress=read_progress(run["instance_path"],r["algorithm"])) for r in algorithms]}

def accept(run_id):
    run = get_run(run_id)
    if run["status"]!="completed" or not run["result"].get("selected",{}).get("feasible"):
        raise AppError("NO_FEASIBLE_SCHEDULE","Only a completed, validated feasible schedule can be accepted.")
    validate_saved_selection(run)
    with connect() as db:
        db.execute("UPDATE runs SET accepted_at=COALESCE(accepted_at,?) WHERE id=?",(now(),run_id))
    return status(run_id)


def validate_saved_selection(run):
    """Read-only validation against the frozen input; never rewrite historical data."""
    from backend.services.v5_service import is_v5
    from backend.services.v5_validation import saved_result_issues
    selected = run["result"].get("selected")
    if not selected or not selected.get("feasible"):
        raise AppError("NO_FEASIBLE_SCHEDULE", "No validated feasible schedule is available.", status=409)
    # A snapshot is present on every persisted run. In-memory legacy export
    # callers without a snapshot retain their existing behavior.
    if "instance_path" not in run:
        return
    try:
        data = json.loads(Path(run["instance_path"]).read_text(encoding="utf-8"))
        if not is_v5(data):
            return
        errors = saved_result_issues(data, run["result"]["selected"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors = [dict(entity="V5 schedule",field="snapshot",message=str(exc))]
    if errors:
        raise AppError("INVALID_SCHEDULE","Saved schedule failed validation; it has not been changed.",errors,status=409)

def apply(run_id):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
        if not row or not row["accepted_at"]:
            raise AppError("ACCEPT_REQUIRED","Accept the schedule before applying it.",status=409)
        if not row["applied_at"]:
            request = json.loads(row["request"])
            # Do not create competing execution plans for the same dataset/job.
            dataset_row = db.execute("SELECT instance FROM datasets WHERE id=?",(row["dataset_id"],)).fetchone()
            family = json.loads(dataset_row["instance"])["metadata"].get("dataset_family_id",row["dataset_id"])
            active = db.execute("""SELECT e.job_id FROM execution e JOIN runs r ON r.id=e.run_id
                JOIN datasets d ON d.id=r.dataset_id
                WHERE json_extract(d.instance,'$.metadata.dataset_family_id')=? AND e.status!='Completed' AND e.run_id!=?""",(family,run_id)).fetchall()
            overlap = set(request["job_ids"]) & {r["job_id"] for r in active}
            if overlap:
                raise AppError("ACTIVE_EXECUTION_CONFLICT","Jobs already belong to an active execution schedule.",sorted(overlap),409)
            stamp = now()
            db.execute("UPDATE runs SET applied_at=? WHERE id=?",(stamp,run_id))
            db.executemany("INSERT INTO execution VALUES(?,?,?,?)",[(run_id,j,"Scheduled",stamp) for j in request["job_ids"]])
    return status(run_id)

def execution():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM execution ORDER BY updated_at DESC").fetchall()]

def update_execution(job_id, body):
    with connect() as db:
        stamp = now()
        cursor = db.execute("UPDATE execution SET status=?,updated_at=? WHERE run_id=? AND job_id=?",
                            (body.status,stamp,body.run_id,job_id))
        if not cursor.rowcount:
            raise AppError("EXECUTION_NOT_FOUND","Apply this schedule before updating execution.",status=404)
        db.execute("INSERT INTO execution_events(run_id,job_id,status,created_at) VALUES(?,?,?,?)",
                   (body.run_id,job_id,body.status,stamp))
    return {"job_id":job_id,"status":body.status,"run_id":body.run_id}
