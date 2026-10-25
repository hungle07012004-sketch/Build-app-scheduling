"""Copy previously solved E2E records into an isolated persistence verification database."""
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

def prepare():
    root=Path.cwd().resolve()
    candidates=sorted((root/".tmp-e2e").glob("backend-*/scheduling.sqlite3"),key=lambda p:p.stat().st_mtime,reverse=True)
    for source in candidates:
        with sqlite3.connect(source) as db:
            db.row_factory=sqlite3.Row
            runs=[dict(r) for r in db.execute("SELECT * FROM runs WHERE status='completed'").fetchall()]
        small=next((r for r in runs if r["applied_at"] and len(json.loads(r["request"])["job_ids"])==10),None)
        large=next((r for r in runs if len(json.loads(r["request"])["job_ids"])==50),None)
        if small and large:
            break
    else:
        raise RuntimeError("No saved 10/50-job E2E results found. Run the workflow E2E test once first.")
    target=root/".tmp-verification"/("reopen-"+uuid.uuid4().hex)
    target.mkdir(parents=True)
    with sqlite3.connect(source) as original, sqlite3.connect(target/"scheduling.sqlite3") as copied:
        original.backup(copied)
        for r in runs:
            src=Path(r["instance_path"]).resolve()
            if not src.is_relative_to(root/".tmp-e2e"):
                raise RuntimeError("Unexpected test snapshot location.")
            dst=target/"runs"/r["id"]/"instance.json"
            dst.parent.mkdir(parents=True)
            shutil.copy2(src,dst)
            copied.execute("UPDATE runs SET instance_path=? WHERE id=?",(str(dst),r["id"]))
    manifest={"backend_dir":str(target),"small_id":small["id"],"large_id":large["id"],
              "small_dataset_id":small["dataset_id"],"snapshot_path":str(target/"runs"/small["id"]/"instance.json"),
              "snapshot_sha256":small["instance_sha256"]}
    (root/".tmp-verification"/"reopen-fixture.json").write_text(json.dumps(manifest),encoding="utf-8")
    print("Prepared isolated persisted-result fixture; no solvers executed.")
if __name__=="__main__":
    prepare()
