from fastapi import APIRouter, Depends, UploadFile, File
from pydantic import BaseModel, Field
from backend.api.auth import require_user
from backend.config import MAX_UPLOAD_BYTES
from backend.errors import AppError
from backend.models.schemas import JobUpdate
from backend.services import input_service
from backend.services.analytics_service import overview, _fingerprint

router = APIRouter(prefix="/api",dependencies=[Depends(require_user)])

@router.post("/input/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read(MAX_UPLOAD_BYTES+1)
    await file.close()
    if len(content)>MAX_UPLOAD_BYTES:
        raise AppError("FILE_TOO_LARGE","File exceeds 30 MB.")
    # Sync parser runs off the event loop.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(input_service.upload,file.filename or "upload",content)

@router.get("/input/current")
def current():
    try:
        return input_service.summary(input_service.dataset())
    except AppError as exc:
        if exc.status==404: return None
        raise

@router.get("/jobs")
def jobs(dataset_id: str | None=None):
    try: return input_service.jobs(dataset_id)
    except AppError as exc:
        if exc.status==404 and dataset_id is None: return []
        raise

@router.get("/workstations")
def stations(dataset_id: str | None=None):
    try:
        row = input_service.dataset(dataset_id)
    except AppError as exc:
        if exc.status==404 and dataset_id is None: return []
        raise
    data = row["instance"]
    analytics = overview()
    same_calendar = analytics["accepted_calendar_signature"] == _fingerprint(data.get("station_windows", {}))
    planned = {s["id"]:s for s in analytics["workstations"]} if analytics["accepted_dataset_id"] == row["id"] and same_calendar else {}
    return [dict(s,id=s["station_id"],stage=s["station_group"],currentJob=None,
        availability="Calendar available" if data["station_windows"].get(s["station_id"]) else "No working window",
        utilization=planned.get(s["station_id"],{}).get("utilization"),
        status="Available" if data["station_windows"].get(s["station_id"]) else "Downtime",
        upcomingJobs=planned.get(s["station_id"],{}).get("upcomingJobs",[])) for s in data["station_master"]]

@router.get("/processing-times")
def processing(dataset_id: str | None=None):
    data=input_service.dataset(dataset_id)["instance"]
    return data.get("job_operations",data.get("operation_options",[]))

@router.get("/calendar")
def calendar(dataset_id: str | None=None, run_id: str | None=None):
    if run_id:
        import json
        from pathlib import Path
        from backend.services.schedule_service import get_run
        data = json.loads(Path(get_run(run_id)["instance_path"]).read_text(encoding="utf-8"))
    else:
        data = input_service.dataset(dataset_id)["instance"]
    windows = data.get("resource_windows", data.get("station_windows", {}))
    return [dict(w,station_id=s) for s,ws in windows.items() for w in ws]

@router.get("/resources")
def resources(dataset_id: str | None=None):
    data = input_service.dataset(dataset_id)["instance"]
    if data["metadata"].get("schema_version")=="AL13_BENCHMARK_V5":
        return {"requirements":{op:[{"resource_id":r} for r in ids] for op,ids in data["resource_by_operation"].items()},
                "units":{r:[r] for r in data["resource_windows"]},"capacity_by_shift":[]}
    return {"requirements":data["resource_requirements"],"units":data["resource_units_by_type"],
            "base_capacity":data.get("base_resource_capacity",{}),"capacity_by_shift":data["resource_capacity_by_shift"]}

@router.patch("/input/{dataset_id}/jobs/{job_id}")
def edit(dataset_id: str,job_id: str,body: JobUpdate):
    return input_service.revise_job(dataset_id,job_id,body.model_dump(exclude_none=True))

class CloneJob(BaseModel):
    template_job_id: str
    job_id: str = Field(min_length=1,max_length=100)

@router.post("/input/{dataset_id}/jobs")
def clone(dataset_id: str,body: CloneJob):
    return input_service.revise_job(dataset_id,body.template_job_id,clone_id=body.job_id)

@router.delete("/input/{dataset_id}/jobs/{job_id}")
def delete(dataset_id: str,job_id: str):
    return input_service.revise_job(dataset_id,job_id,delete=True)
