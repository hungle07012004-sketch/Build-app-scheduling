import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from backend.database.db import init_db
from backend.errors import AppError
from backend.api import auth, input_data, scheduling, analytics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app):
    init_db()
    auth.init_user()
    yield

app = FastAPI(title="AL13 Production Scheduling",lifespan=lifespan)
for router in (auth.router,input_data.router,scheduling.router,analytics.router):
    app.include_router(router)

@app.get("/api/health")
def health():
    return {"status":"ok","service":"AL13 scheduling"}

@app.exception_handler(AppError)
async def app_error(request: Request, exc: AppError):
    if exc.__cause__:
        logger.error("Request failed: %s",exc.code,exc_info=exc.__cause__)
    return JSONResponse(status_code=exc.status,content={"success":False,"error_code":exc.code,"message":exc.message,"details":exc.details})

@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422,content={"success":False,"error_code":"INVALID_REQUEST","message":"Request fields are invalid.",
        "details":[{"field":".".join(str(x) for x in e["loc"]),"message":e["msg"]} for e in exc.errors()]})

@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    logger.exception("Unhandled request error")
    return JSONResponse(status_code=500,content={"success":False,"error_code":"INTERNAL_ERROR","message":"The request failed. Check backend logs."})

@app.get("/api/config")
def config():
    from backend.config import SMALL_SCALE_MAX_JOBS
    return {"small_scale_max_jobs": SMALL_SCALE_MAX_JOBS}

# Optional production mode: compiled UI and API share the same origin.
import os
if os.environ.get("SERVE_FRONTEND") == "1":
    from backend.config import ROOT
    from backend.frontend import mount_frontend
    mount_frontend(app, ROOT / "dist")
