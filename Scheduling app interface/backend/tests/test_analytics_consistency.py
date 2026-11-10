import json
from contextlib import contextmanager
import sqlite3
import pytest
from backend.services import analytics_service as service
from backend.api import input_data

@pytest.fixture
def audit(monkeypatch, tmp_path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE runs(id, dataset_id, created_at, finished_at, status, request, instance_path, result, accepted_at, applied_at)")
    db.execute("CREATE TABLE execution(status)")
    frozen = {"metadata":{"origin_datetime":"2026-10-01", "horizon_end_datetime":"2026-11-30"},
              "station_master":[{"station_id":"OLD", "station_group":"Scale"}, {"station_id":"MISSING", "station_group":"Other"}],
              "station_windows":{"OLD":[{"start_min":0,"end_min":100}]}}
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(frozen))
    selected = {"algorithm":"CP-SAT", "makespan_min":10, "runtime_sec":1, "total_flow_time_min":10,
                "operations":[{"workstation":"OLD","processingMin":10,"jobId":"J1"}]}
    request = {"job_ids":["J1"],"settings":{"exact_time_limit":600}}
    for id, status, result, accepted in [("accepted","completed",{"selected":selected},"2026-10-02"),("failed","failed",{},None)]:
        db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",(id,"old","2026-10-01","2026-10-02",status,json.dumps(request),str(path),json.dumps(result),accepted,None))
    @contextmanager
    def connect():
        yield db
    current = {"id":"new", "instance":{"station_master":[{"station_id":"NEW","station_group":"New"}],"station_windows":{"NEW":[{"start_min":0,"end_min":200}]}}}
    monkeypatch.setattr(service,"connect",connect)
    monkeypatch.setattr(service,"dataset",lambda:current)
    monkeypatch.setattr(service,"jobs",lambda id:[])
    monkeypatch.setattr(input_data.input_service,"dataset",lambda id=None:current)
    yield db, path, frozen, current
    db.close()

def test_accepted_resources_and_calendar_are_not_current_dataset(audit):
    result=service.overview()
    assert [w["id"] for w in result["workstations"]] == ["OLD","MISSING"]
    assert [w["utilization"] for w in result["workstations"]] == [10, None]
    assert result["current_dataset_id"] == "new"
    assert result["accepted_dataset_id"] == "old"
    assert result["accepted_applied"] is False
    assert input_data.stations()[0]["utilization"] is None

def test_history_preserves_failed_runs_finish_time_and_budget(audit):
    rows={r["id"]:r for r in service.overview()["history"]}
    assert len(rows)==2
    assert rows["failed"]["makespan"] is None
    assert rows["failed"]["status"]=="failed"
    assert rows["accepted"]["finished_at"]=="2026-10-02"
    assert rows["accepted"]["created_at"]=="2026-10-01"
    assert rows["accepted"]["search_settings"]["exact_time_limit"]==600

@pytest.mark.parametrize("change",["calendar","jobs","budget"])
def test_comparison_keys_require_identical_inputs_and_settings(audit, change):
    db,path,frozen,current=audit
    before=service.overview()["history"][0]["comparison_key"]
    if change=="calendar":
        frozen["station_windows"]["OLD"][0]["end_min"]=200
        path.write_text(json.dumps(frozen))
    else:
        request={"job_ids":["J2"] if change=="jobs" else ["J1"],"settings":{"exact_time_limit":900 if change=="budget" else 600}}
        db.execute("UPDATE runs SET request=?",(json.dumps(request),))
    assert service.overview()["history"][0]["comparison_key"] != before

def test_same_dataset_different_calendar_is_unknown(audit):
    db,path,frozen,current=audit
    current["id"]="old"
    current["instance"]=dict(frozen,station_windows={"OLD":[{"start_min":0,"end_min":200}]})
    assert input_data.stations()[0]["utilization"] is None

def test_valid_zero_load_remains_zero(audit):
    db,path,frozen,current=audit
    frozen["station_windows"]["MISSING"]=[{"start_min":0,"end_min":100}]
    path.write_text(json.dumps(frozen))
    assert service.overview()["workstations"][1]["utilization"]==0

def test_missing_snapshot_keeps_history_without_comparison(audit):
    db,path,frozen,current=audit
    db.execute("UPDATE runs SET instance_path='missing.json' WHERE id='failed'")
    row=next(r for r in service.overview()["history"] if r["id"]=="failed")
    assert row["comparison_key"] is None
    assert row["calendar"] is None

def test_failed_run_retains_attempted_algorithm(audit):
    db, *_ = audit
    db.execute("UPDATE runs SET result=? WHERE id='failed'", (json.dumps({"algorithms":[{"algorithm":"SS Micro-GA Fast", "feasible":False}]}),))
    row=next(r for r in service.overview()["history"] if r["id"]=="failed")
    assert row["algo"] == "SS Micro-GA Fast"
    assert row["makespan"] is None

def test_identical_context_has_stable_key_but_changed_algorithm_does_not(audit):
    db, *_ = audit
    before=service.overview()["history"][0]["comparison_key"]
    assert service.overview()["history"][0]["comparison_key"] == before
    result=json.loads(db.execute("SELECT result FROM runs WHERE id='accepted'").fetchone()[0])
    result["selected"]["algorithm"]="SS Micro-GA Fast"
    db.execute("UPDATE runs SET result=? WHERE id='accepted'",(json.dumps(result),))
    assert service.overview()["history"][0]["comparison_key"] != before
