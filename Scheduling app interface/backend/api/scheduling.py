from fastapi import APIRouter, Depends
from backend.api.auth import require_user
from backend.models.schemas import ScheduleRequest, ExecutionUpdate
from backend.services import optimization_service as optimization, schedule_service as schedules

router = APIRouter(prefix="/api",dependencies=[Depends(require_user)])

@router.post("/schedules/preview")
def preview(body: ScheduleRequest):
    # Freeze in memory only; do not start a solver or create a persisted run.
    _,issues,routing = optimization.prepare(body,preflight=False)
    return {"valid":not issues,"issues":issues,**routing}

@router.post("/schedules/validate")
def validate(body: ScheduleRequest):
    _,issues,routing = optimization.prepare(body)
    return {"valid":not issues,"issues":issues,**routing}

@router.post("/schedules/run",status_code=202)
def run(body: ScheduleRequest):
    return optimization.start(body)

@router.get("/schedules/{run_id}/status")
def status(run_id: str):
    return schedules.status(run_id)

@router.get("/schedules/{run_id}/result")
def result(run_id: str):
    row = schedules.get_run(run_id)
    return {**row["result"],"run_id":run_id,"status":row["status"],"accepted_at":row["accepted_at"],"applied_at":row["applied_at"],"request":row["request"]}

@router.post("/schedules/{run_id}/accept")
def accept(run_id: str):
    return schedules.accept(run_id)

@router.post("/schedules/{run_id}/apply")
def apply(run_id: str):
    return schedules.apply(run_id)

@router.post("/schedules/{run_id}/rerun",status_code=202)
def rerun(run_id: str):
    row = schedules.get_run(run_id)
    return optimization.start(ScheduleRequest(**row["request"]))

@router.get("/execution")
def execution():
    return schedules.execution()

@router.patch("/execution/jobs/{job_id}")
def update(job_id: str,body: ExecutionUpdate):
    return schedules.update_execution(job_id,body)

@router.get("/schedules/{run_id}/export.xlsx")
def export_excel(run_id: str):
    from fastapi.responses import Response
    from backend.services.excel_service import export, filename
    return Response(export(run_id),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition":f'attachment; filename="{filename(run_id)}"'})
