from datetime import datetime, timedelta
import pandas as pd
from backend.services import v5_service
from backend.solver_v5 import generate_benchmark_instances as gen


def test_excel_uses_benchmark_sixty_day_horizon(monkeypatch, tmp_path):
    origin = datetime(2026, 10, 1, 6)
    tables = {
        "validation": pd.DataFrame({"PASS": ["PASS"]}),
        "shift": pd.DataFrame({"Shift_Start": [origin], "Shift_End": [origin + timedelta(days=30)]}),
        "jobs": pd.DataFrame(),
    }
    monkeypatch.setattr(gen, "load_source", lambda path: tables)
    monkeypatch.setattr(gen, "clean_df", lambda frame: frame)
    def windows(data, start, end):
        assert start == origin
        assert end == origin + timedelta(days=60)
        return {"WS_OIL": []}
    monkeypatch.setattr(gen, "build_resource_windows", windows)
    def build(data, jobs, name, path, digest, calendar, start, end):
        assert end == origin + timedelta(days=60)
        return {"resource_windows": calendar,"metadata":{"origin_datetime":start.isoformat(),
                "horizon_end_datetime":end.isoformat(),"horizon_end_min":(end-start).total_seconds()/60}}
    monkeypatch.setattr(gen, "build_instance", build)
    result = v5_service.workbook(tmp_path / "source.xlsx", "source", "digest")
    assert result["station_windows"] == {"WS_OIL": []}
