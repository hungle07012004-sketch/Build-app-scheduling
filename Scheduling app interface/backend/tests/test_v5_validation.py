"""Regression cases from SCHEDULING_TEST_REPORT.md QA-01 through QA-05."""
import copy
import json
from pathlib import Path

import pytest

from backend.database.db import connect
from backend.services import optimization_service, schedule_service, v5_service
from backend.services.v5_validation import check_precedence
from backend.solver_v5.benchmark_common import V5ScheduleDecoder
from backend.tests.test_v5 import instance


def decoded(data):
    metrics, rows = V5ScheduleDecoder(data).decode([j["job_id"] for j in data["jobs"]], keep_schedule=True)
    return dict(metrics, schedule=rows, feasible=True)


def invalid(data, case):
    if case == "missing_edge": data["precedence"].pop()
    elif case == "duplicate_edge": data["precedence"].append(copy.deepcopy(data["precedence"][0]))
    elif case == "unknown_job": data["precedence"][0]["job_id"] = "MISSING"
    elif case == "unknown_operation": data["precedence"][0]["pred"] = "MISSING"
    elif case == "unsupported_edge": data["precedence"].append(dict(job_id="J001", pred="PRE", succ="LASER", lag_min=0, relation_type="FS"))
    elif case == "cyclic_edge": data["precedence"].append(dict(job_id="J001", pred="VISUAL", succ="2B", lag_min=1, relation_type="FS"))
    elif case == "self_edge": data["precedence"].append(dict(job_id="J001", pred="PRE", succ="PRE", lag_min=0, relation_type="FS"))
    elif case == "wrong_relation": data["precedence"][1]["relation_type"] = "FS"
    elif case == "missing_relation": data["precedence"][0].pop("relation_type")
    elif case.startswith("lag_"): data["precedence"][0]["lag_min"] = VALUES[case[4:]]
    elif case == "missing_setup": del data["setup_matrix"]["A"]["B"]
    elif case.startswith("setup_"): data["setup_matrix"]["A"]["B"] = VALUES[case[6:]]
    elif case.startswith("batch_"): data["batching"]["processing_min_per_batch"] = VALUES[case[6:]]
    else: raise AssertionError(case)


VALUES = dict(negative=-1, nan=float("nan"), inf=float("inf"), neginf=-float("inf"), boolean=True, string="3", zero=0, missing=None)
CASES = [(c, "precedence") for c in (
    "missing_edge", "duplicate_edge", "unknown_job", "unknown_operation", "unsupported_edge", "cyclic_edge", "self_edge", "wrong_relation", "missing_relation",
    *["lag_" + k for k in VALUES if k != "zero"])] + [("missing_setup", "setup_matrix")] + [
    ("setup_" + k, "setup_matrix") for k in VALUES if k != "zero"] + [("batch_" + k, "batching.processing_min_per_batch") for k in VALUES]


@pytest.mark.parametrize("case,field", CASES)
def test_invalid_constraints_rejected_before_save_or_queue(client, monkeypatch, case, field):
    good = instance()
    bad = copy.deepcopy(good)
    invalid(bad, case)
    errors = v5_service.validate(bad)
    assert any(e["field"].startswith(field) for e in errors), errors
    # JSON bytes deliberately exercise NaN/Infinity too, without TestClient's encoder.
    response = client.post("/api/input/upload", files={"file": ("invalid.json", json.dumps(bad), "application/json")})
    assert response.status_code == 400, response.text
    assert field in response.text
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    called = []
    monkeypatch.setattr(optimization_service, "dataset", lambda _: {"instance": bad})
    monkeypatch.setattr(optimization_service.queue, "submit", lambda *args: called.append(args))
    for endpoint in ("validate", "run"):
        response = client.post("/api/schedules/" + endpoint, json=request("test"))
        assert response.status_code == 400, response.text
        assert field in response.text
    assert not called
    result = v5_service.standardize("SS Micro-GA Fast", decoded(good), bad)
    assert result["feasible"] is False and result["operations"] == []


def request(dataset_id):
    return dict(dataset_id=dataset_id, job_ids=[j["job_id"] for j in instance()["jobs"]],
                horizon_start="2026-10-01T06:00", horizon_end="2026-10-02T06:00", algorithms=["SS Micro-GA Fast"])


@pytest.mark.parametrize("kind", ["JOB_OP", "SETUP", "OIL_CYCLE", "2C_BATCH"])
@pytest.mark.parametrize("corruption", ["station", "operator", "combined", "contradictory", "missing", "duplicate"])
def test_raw_assignments_rejected_for_every_event_type(kind, corruption):
    data = instance()
    raw = decoded(data)
    row = next(r for r in raw["schedule"] if r["Event_Type"] == kind and (kind != "JOB_OP" or r["Operation_Code"] == "PRE"))
    if corruption == "station": row["Station_ID"] = "WS_FINAL"
    elif corruption == "operator": row["Resource_Units"] = "OP_FINAL"
    elif corruption == "combined":
        row.pop("Station_ID"); row.pop("Resource_Units"); row["Resources"] = "WS_FINAL;OP_FINAL"
    elif corruption == "contradictory":
        row["Resources"] = row["Station_ID"] + ";" + row["Resource_Units"]
        row["Resource_Units"] = "OP_FINAL"
    elif corruption == "missing": row.pop("Resource_Units")
    elif corruption == "duplicate": row["Resource_Units"] += ";" + row["Resource_Units"]
    before = copy.deepcopy(raw)
    result = v5_service.standardize("SS Micro-GA Fast", raw, data)
    assert not result["feasible"] and not result["operations"], result
    assert any("expected" in e["message"] for e in result["validation_issues"])
    assert raw == before


def test_valid_zero_setup_and_both_assignment_formats_are_unchanged():
    data = instance()
    data["setup_matrix"]["A"]["B"] = 0
    assert v5_service.validate(data) == []
    raw = decoded(data)
    original = copy.deepcopy(raw)
    ga = v5_service.standardize("SS Micro-GA Fast", raw, data)
    assert ga["feasible"], ga
    assert raw == original
    for row in raw["schedule"]:
        row["Resources"] = row.pop("Station_ID") + ";" + row.pop("Resource_Units")
    cp = v5_service.standardize("CP-SAT", raw, data)
    assert cp["feasible"] and cp["operations"] == ga["operations"]


@pytest.mark.parametrize("pred,succ", list(zip(("2B", "PRE", "FINAL", "LASER", "SCALE", "OIL", "SEAL"), ("PRE", "FINAL", "LASER", "SCALE", "OIL", "SEAL", "VISUAL"))))
def test_every_accepted_edge_is_rechecked(pred, succ):
    data = instance()
    selected = v5_service.standardize("SS Micro-GA Fast", decoded(data), data)
    edge = next(e for e in data["precedence"] if e["job_id"] == "J001" and e["pred"] == pred and e["succ"] == succ)
    edge["lag_min"] = 1000  # Schema-valid, but the previous output violates the new lag.
    with pytest.raises(ValueError, match="lag violated"):
        check_precedence(data, selected["operations"])
    result = v5_service.standardize("SS Micro-GA Fast", decoded(instance()), data)
    assert not result["feasible"]


@pytest.mark.parametrize("case", ["valid", "missing_edge", "cyclic_edge", "setup_negative", "batch_negative", "station", "operator", "lag"])
def test_accept_and_excel_revalidate_without_changing_history(client, monkeypatch, case):
    data = instance()
    uploaded = client.post("/api/input/upload", files={"file": ("good.json", json.dumps(data), "application/json")})
    assert uploaded.status_code == 200
    monkeypatch.setattr(optimization_service.queue, "submit", lambda *args: None)
    started = client.post("/api/schedules/run", json=request(uploaded.json()["id"]))
    assert started.status_code == 202, started.text
    rid = started.json()["run_id"]
    run = schedule_service.get_run(rid)
    path = Path(run["instance_path"])
    frozen = json.loads(path.read_text())
    selected = v5_service.standardize("SS Micro-GA Fast", decoded(frozen), frozen)
    assert selected["feasible"]
    if case in ("missing_edge", "cyclic_edge", "setup_negative", "batch_negative"):
        invalid(frozen, case)
        path.write_text(json.dumps(frozen), encoding="utf-8")
    elif case in ("station", "operator"):
        pre = next(o for o in selected["operations"] if o["operation"] == "PRE")
        pre["workstation" if case == "station" else "resourceUnits"] = "WS_FINAL" if case == "station" else "WS_PRE_JIG;OP_FINAL"
    elif case == "lag":
        frozen["precedence"][0]["lag_min"] = 1000
        path.write_text(json.dumps(frozen), encoding="utf-8")
    result = dict(selected=selected, algorithms=[selected])
    with connect() as db:
        db.execute("UPDATE runs SET status='completed',result=? WHERE id=?", (json.dumps(result), rid))
    before = path.read_bytes()
    for response in (client.post(f"/api/schedules/{rid}/accept"), client.get(f"/api/schedules/{rid}/export.xlsx"),
                     client.get(f"/api/schedules/{rid}/result?validate_export=true")):
        assert response.status_code == (200 if case == "valid" else 409), response.text[:1000]
    after = schedule_service.get_run(rid)
    assert after["result"] == result
    assert path.read_bytes() == before
    assert bool(after["accepted_at"]) == (case == "valid")
    assert client.get(f"/api/schedules/{rid}/result").status_code == 200  # History remains inspectable.
