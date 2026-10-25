"""Persistence checks reuse a decoded fixture and do not rerun optimization."""
import hashlib
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend.main import app
from backend.database.db import connect
from backend.services import optimization_service
from backend.services.result_standardizer import standardize
from backend.tests.fixtures import instance
from backend.tests.test_integration import upload, request, decoded

def test_restart_reopens_accepted_schedule_and_input_edits_preserve_snapshot(client,monkeypatch):
    dataset_id=upload(client)
    monkeypatch.setattr(optimization_service.queue,"submit",lambda *args:None)
    response=client.post("/api/schedules/run",json=request(dataset_id))
    assert response.status_code==202
    run_id=response.json()["run_id"]
    with connect() as db:
        row=db.execute("SELECT instance_path,instance_sha256 FROM runs WHERE id=?",(run_id,)).fetchone()
    snapshot=Path(row["instance_path"])
    original_bytes=snapshot.read_bytes()
    frozen=json.loads(original_bytes)
    selected=standardize("MILP",decoded(frozen),frozen)
    result={"run_id":run_id,"algorithms":[selected],"selected":selected,"selection_reason":"Only feasible result."}
    with connect() as db:
        db.execute("UPDATE runs SET status='completed',result=? WHERE id=?",(json.dumps(result),run_id))
    assert client.post(f"/api/schedules/{run_id}/apply").status_code==409
    assert client.post(f"/api/schedules/{run_id}/accept").status_code==200
    assert client.post(f"/api/schedules/{run_id}/apply").status_code==200
    before=client.get(f"/api/schedules/{run_id}/result").json()
    updated=client.patch(f"/api/input/{dataset_id}/jobs/J001",json={"priority":3,"due_datetime":"2026-09-03T08:00"})
    assert updated.status_code==200
    assert updated.json()["id"]!=dataset_id
    assert snapshot.read_bytes()==original_bytes
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==row["instance_sha256"]
    with TestClient(app) as reopened:
        reopened.headers.update(client.headers)
        health=reopened.get("/api/health")
        assert health.status_code==200 and health.json()["status"]=="ok"
        after=reopened.get(f"/api/schedules/{run_id}/result")
        assert after.status_code==200
        assert after.json()==before
        assert after.json()["accepted_at"] and after.json()["applied_at"]
        assert len(reopened.get("/api/execution").json())==2
        assert reopened.get("/api/analytics/overview").json()["latest_accepted_schedule"]==run_id
    assert snapshot.read_bytes()==original_bytes
