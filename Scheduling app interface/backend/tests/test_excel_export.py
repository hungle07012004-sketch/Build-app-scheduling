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
