import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from backend.config import DATA_DIR

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "scheduling.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()

def init_db():
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS datasets(
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT NOT NULL,
            sha256 TEXT NOT NULL, path TEXT NOT NULL, instance TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
            created_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            request TEXT NOT NULL, instance_path TEXT NOT NULL, instance_sha256 TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '{}', accepted_at TEXT, applied_at TEXT);
        CREATE TABLE IF NOT EXISTS algorithm_results(
            run_id TEXT NOT NULL REFERENCES runs(id), algorithm TEXT NOT NULL,
            status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(run_id, algorithm));
        CREATE TABLE IF NOT EXISTS execution(
            run_id TEXT NOT NULL REFERENCES runs(id), job_id TEXT NOT NULL,
            status TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(run_id,job_id));
        CREATE TABLE IF NOT EXISTS execution_events(
            id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, job_id TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(
            email TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
            token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at REAL NOT NULL);
        """)
        # Background processes do not survive an API restart; never report them as running.
        db.execute("UPDATE runs SET status='interrupted', finished_at=? WHERE status IN ('queued','running')", (now(),))
        db.execute("UPDATE algorithm_results SET status='interrupted' WHERE status IN ('queued','running')")

def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
