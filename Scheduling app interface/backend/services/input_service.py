import copy
import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import Path
from backend.config import DATA_DIR
from backend.database.db import connect, dump, now
from backend.errors import AppError
from backend.services.algorithms.common import solver_module
from backend.services.instance_service import parse_datetime, resource_proof
from backend.services.validation_service import validate_instance
from backend.services import v5_service

def dataset(dataset_id=None):
    with connect() as db:
        row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone() if dataset_id else db.execute("SELECT * FROM datasets ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        raise AppError("DATASET_NOT_FOUND", "Upload an AL13 workbook or frozen JSON instance first.", status=404)
    result = dict(row)
    result["instance"] = json.loads(result["instance"])
    return result

def summary(row):
    data = row["instance"]
    if v5_service.is_v5(data): v5_service.display_fields(data)
    return {"schema_version":data["metadata"].get("schema_version","legacy"),"id":row["id"],"filename":row["filename"],"uploaded_at":row["created_at"],
            "sha256":row["sha256"],"jobs":len(data["jobs"]),"workstations":len(data["station_master"]),
            "origin_datetime":data["metadata"]["origin_datetime"],
            "horizon_end_datetime":data["metadata"]["horizon_end_datetime"],"validation_status":"Valid"}

def persist(filename, path, sha256, data):
    identifier = uuid.uuid4().hex
    data["metadata"].setdefault("dataset_family_id",identifier)
    with connect() as db:
        db.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?)",
                   (identifier,filename,now(),sha256,str(path),dump(data)))
    return summary(dataset(identifier))

def upload(filename, content):
    suffix = Path(filename).suffix.lower()
    if suffix not in (".json",".xlsx"):
        raise AppError("UNSUPPORTED_FILE","Upload an .xlsx or frozen AL13 .json file.")
    folder = DATA_DIR / "uploads"
    folder.mkdir(parents=True,exist_ok=True)
    path = folder / (uuid.uuid4().hex+suffix)
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    try:
        if suffix == ".json":
            data = json.loads(content.decode("utf-8-sig"))
        else:
            from openpyxl import load_workbook
            book=load_workbook(path,read_only=True)
            v5="12_OIL_Cycles" in book.sheetnames and "13_2C_Batching" in book.sheetnames
            book.close()
            if v5:
                data=v5_service.workbook(path,Path(filename).stem,digest)
            else:
                data=legacy_workbook(path,filename,digest)
        if v5_service.is_v5(data): v5_service.display_fields(data)
        errors = validate_instance(data)
        if errors:
            raise AppError("INVALID_DATASET","Input validation failed.",errors)
        resource_proof(data)
        return persist(Path(filename).name,path,digest,data)
    except AppError:
        raise
    except (ImportError, SystemError, OSError) as exc:
        raise AppError("ENVIRONMENT_BLOCKED","Input dependencies could not load. Check backend logs.",status=503) from exc
    except Exception as exc:
        raise AppError("INVALID_DATASET","Could not read the AL13 file.",str(exc)) from exc

def legacy_workbook(path,filename,digest):
    generator = solver_module("generate_benchmark_instances")
    tables = generator.load_source(path)
    last_release = generator.dt(tables["jobs"]["Release_DateTime"].max())
    calendar_end = generator.dt(tables["shift"]["Shift_End"].max())
    days = (calendar_end-last_release).total_seconds()/86400
    data = generator.build_instance(tables,tables["jobs"],Path(filename).stem,path,digest,0,"BASE","BASE",days)
    return data

def jobs(dataset_id=None):
    row = dataset(dataset_id)
    data = row["instance"]
    with connect() as db:
        states = db.execute("""SELECT e.job_id,e.status FROM execution e JOIN runs r ON r.id=e.run_id
            JOIN datasets d ON d.id=r.dataset_id
            WHERE json_extract(d.instance,'$.metadata.dataset_family_id')=? ORDER BY r.applied_at,e.updated_at""",(data["metadata"].get("dataset_family_id",row["id"]),)).fetchall()
    status = {r["job_id"]:r["status"] for r in states}
    origin = parse_datetime(data["metadata"]["origin_datetime"])
    def time(j, key):
        return (origin+timedelta(minutes=j[key])).isoformat(sep=" ")
    return [dict(j, id=j["job_id"],product=j.get("product_code",""),batchQty=j.get("batch_qty",0),
                 priorityValue=j["priority"],priority="High" if j["priority"]==1 else "Medium" if j["priority"]==2 else "Low",
                 releaseTime=time(j,"release_min"),materialReady=time(j,"element_ready_min"),
                 loopsetRelease=time(j,"loopset_release_min"),dueDate=time(j,"due_min"),
                 status=status.get(j["job_id"],"Unscheduled")) for j in data["jobs"]]

def revise_job(dataset_id, job_id, changes=None, clone_id=None, delete=False):
    row = dataset(dataset_id)
    data = copy.deepcopy(row["instance"])
    job = next((j for j in data["jobs"] if j["job_id"]==job_id),None)
    if not job:
        raise AppError("JOB_NOT_FOUND","Job not found.",status=404)
    if v5_service.is_v5(data) and clone_id:
        raise AppError("V5_CLONE_UNSUPPORTED","Create V5 jobs in the source workbook with matching OIL cycles and exact-10 batch partners.")
    if v5_service.is_v5(data) and changes and "product_code" in changes and changes["product_code"]!=job["product_code"]:
        raise AppError("V5_PRODUCT_CHANGE","Edit product and batch compatibility together in the source V5 workbook.")
    if clone_id:
        if any(j["job_id"]==clone_id for j in data["jobs"]):
            raise AppError("DUPLICATE_JOB","Job ID already exists.")
        new_job = dict(job,job_id=clone_id)
        data["jobs"].append(new_job)
        for key in (("job_operations","precedence","oil_cycles") if v5_service.is_v5(data) else ("operation_options","precedence")):
            data[key].extend([dict(r,job_id=clone_id) for r in data[key] if r["job_id"]==job_id])
    elif delete:
        data["jobs"] = [j for j in data["jobs"] if j["job_id"]!=job_id]
        for key in (("job_operations","precedence","oil_cycles") if v5_service.is_v5(data) else ("operation_options","precedence")):
            data[key] = [r for r in data[key] if r["job_id"]!=job_id]
    else:
        origin = parse_datetime(data["metadata"]["origin_datetime"])
        mapping = {"release_datetime":"release_min","element_ready_datetime":"element_ready_min",
                   "loopset_release_datetime":"loopset_release_min","due_datetime":"due_min"}
        for key,value in (changes or {}).items():
            if value is not None:
                job[key] = value
                if key in mapping:
                    job[mapping[key]] = (parse_datetime(value)-origin).total_seconds()/60
    data["metadata"]["size"] = len(data["jobs"])
    errors = validate_instance(data)
    if errors:
        raise AppError("INVALID_DATASET","Changes are invalid.",errors)
    resource_proof(data)
    # Revision preserves the original upload and any previously frozen scheduling runs.
    return persist(row["filename"],row["path"],hashlib.sha256(dump(data).encode()).hexdigest(),data)
