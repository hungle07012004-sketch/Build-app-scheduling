import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("SCHEDULING_DATA_DIR", ROOT / "backend" / "storage"))
SMALL_SCALE_MAX_JOBS = int(os.environ.get("SMALL_SCALE_MAX_JOBS", "20"))
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
DEMO_EMAIL = os.environ.get("SCHEDULING_EMAIL", "planner@factory.com")
DEMO_PASSWORD = os.environ.get("SCHEDULING_PASSWORD", "planner-demo")
