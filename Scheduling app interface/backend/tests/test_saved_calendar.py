import json
from backend.api.input_data import calendar


def test_calendar_reads_frozen_run_without_current_dataset(tmp_path, monkeypatch):
    from backend.services import schedule_service, input_service
    window = {"window_id": "WS_OIL_W0003", "shift_id": "D01_S2", "calendar_date": "2026-10-01"}
    snapshot = tmp_path / "instance.json"
    snapshot.write_text(json.dumps({"resource_windows": {"WS_OIL": [window]}}), encoding="utf-8")
    before = snapshot.read_bytes()
    monkeypatch.setattr(schedule_service, "get_run", lambda run_id: {"instance_path": str(snapshot)})
    def forbidden(*args):
        raise AssertionError("Saved calendar must not read mutable input data")
    monkeypatch.setattr(input_service, "dataset", forbidden)
    assert calendar(run_id="saved") == [dict(window, station_id="WS_OIL")]
    assert snapshot.read_bytes() == before
