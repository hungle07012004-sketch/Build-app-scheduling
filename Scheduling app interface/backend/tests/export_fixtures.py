"""Generate clearly labelled synthetic integration fixtures, never production results."""
import json
from pathlib import Path
from backend.tests.fixtures import instance, make_workbook

if __name__ == "__main__":
    folder=Path("tests/fixtures")
    folder.mkdir(parents=True,exist_ok=True)
    for n in (10,50):
        (folder/f"TEST_AL13_{n}.json").write_text(json.dumps(instance(n),indent=2),encoding="utf-8")
    (folder/"TEST_AL13_10.xlsx").write_bytes(make_workbook())
    from backend.tests.test_v5 import instance as v5_instance
    (folder/"v5-integration.json").write_text(json.dumps(v5_instance()),encoding="utf-8")
    print("Created synthetic AL13 test inputs in tests/fixtures.")
