from copy import deepcopy
from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from backend.services import excel_service


@pytest.mark.parametrize("algorithm,count", [("CP-SAT", 12), ("SS Micro-GA Fast", 11000)])
def test_saved_export_preserves_rows_without_repeated_sheet_scans(monkeypatch, tmp_path, algorithm, count):
    operations = [dict(jobId=f"J{i:05}", product="P1", operation="OIL", workstation="WS_OIL",
                       startTime="2026-10-01 06:00:00.125", finishTime="2026-10-01 06:01:00.125",
                       processingMin=1.0, status="Scheduled", eventId=f"E{i}", eventType="OIL_CYCLE",
                       resourceUnits="WS_OIL", windowId="W1") for i in range(count)]
    selected = dict(algorithm=algorithm, feasible=True, status="Feasible", operations=operations)
    snapshot = tmp_path / "instance.json"
    snapshot.write_text('{"resource_windows":{"WS_OIL":[{"window_id":"W1","shift_id":"Shift 1"}]}}')
    run = dict(result=dict(selected=selected, algorithms=[selected]), request={},
               instance_path=str(snapshot), status="completed")
    original = deepcopy(run)
    snapshot_before = snapshot.read_bytes()
    monkeypatch.setattr(excel_service, "get_run", lambda run_id: run)
    scans = 0
    original_max_row = Worksheet.max_row.fget

    def counted_max_row(sheet):
        nonlocal scans
        scans += 1
        # Whole-sheet scans must stay bounded as operation count grows.
        assert scans <= 20, "Excel export repeatedly scans all previously written cells"
        return original_max_row(sheet)

    monkeypatch.setattr(Worksheet, "max_row", property(counted_max_row))
    workbook = load_workbook(BytesIO(excel_service.export("saved-run")), read_only=True)
    assert workbook.sheetnames == ["Schedule Summary", "Detailed Schedule", "Algorithm Comparison"]
    rows = list(workbook["Detailed Schedule"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == count
    for row, operation in zip(rows, operations):
        assert row[:4] == (operation["jobId"], "P1", "OIL", "WS_OIL")
        assert row[4:9] == (datetime.fromisoformat(operation["startTime"]),
                            datetime.fromisoformat(operation["finishTime"]), 1, "Shift 1", "Scheduled")
        assert row[9:11] == (operation["eventId"], "OIL_CYCLE")
    assert run == original
    assert snapshot.read_bytes() == snapshot_before


@pytest.mark.parametrize("start,finish,expected", [
    ("06:00", "14:00", "Shift 1"),
    ("14:00", "22:00", "Shift 2"),
    ("00:00", "06:00", "Shift 3"),
    ("13:30", "14:15", "Shift 1 → Shift 2"),
    ("21:56:29.400", "22:39:24", "Shift 2 → Shift 3"),
    ("05:59:59.999999", "06:00:00.000001", "Shift 3 → Shift 1"),
    ("13:30", "14:00:00.000001", "Shift 1 → Shift 2"),
    ("22:00", "2026-10-02 06:00", "Shift 3"),
    ("23:30", "2026-10-02 00:30", "Shift 3"),
    ("23:30", "2026-10-02 06:15", "Shift 3 → Shift 1"),
    ("13:30", "2026-10-02 14:15", "Shift 1 → Shift 2 → Shift 3 → Shift 1 → Shift 2"),
])
def test_export_shift_labels_use_timestamps_not_window_or_stale_shift(monkeypatch, start, finish, expected):
    start = datetime.fromisoformat("2026-10-01 " + start)
    finish = datetime.fromisoformat(finish if " " in finish else "2026-10-01 " + finish)
    operation = dict(jobId="J023", operation="LASER", workstation="WS_LASER",
                     startTime=start.isoformat(), finishTime=finish.isoformat(),
                     processingMin=(finish-start).total_seconds()/60, status="Scheduled",
                     windowId=None, shift="Unavailable")
    selected = dict(algorithm="SS Micro-GA Fast", feasible=True, operations=[operation])
    run = dict(result=dict(selected=selected), request={})
    before = deepcopy(run)
    monkeypatch.setattr(excel_service, "get_run", lambda _: run)
    workbook = load_workbook(BytesIO(excel_service.export("cross-shift")), read_only=True)
    row = next(workbook["Detailed Schedule"].iter_rows(min_row=2, values_only=True))
    assert row[7] == expected
    assert row[13] is None
    assert run == before
    workbook.close()


def test_calendar_violation_still_fails_validation_and_export(monkeypatch):
    from backend.errors import AppError
    from backend.services import v5_service
    from backend.tests.test_v5_validation import instance, decoded

    data = instance()
    raw = decoded(data)
    selected = v5_service.standardize("SS Micro-GA Fast", raw, data)
    assert selected["feasible"]
    operation = next(o for o in selected["operations"] if o["operation"] == "SCALE")
    # Remove availability beneath an otherwise valid saved operation.
    data["resource_windows"][operation["workstation"]] = []
    selected = v5_service.standardize("SS Micro-GA Fast", raw, data)
    assert not selected["feasible"]
    run = dict(result=dict(selected=selected))
    before = deepcopy(run)
    monkeypatch.setattr(excel_service, "get_run", lambda _: run)
    with pytest.raises(AppError) as error:
        excel_service.export("invalid-calendar")
    assert error.value.code == "NO_FEASIBLE_SCHEDULE"
    assert run == before
