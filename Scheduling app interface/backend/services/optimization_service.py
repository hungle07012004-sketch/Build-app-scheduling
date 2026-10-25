import hashlib
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from backend.config import DATA_DIR
from backend.database.db import connect, dump, now
from backend.errors import AppError
from backend.services.input_service import dataset
from backend.services.instance_service import freeze, route
from backend.services.validation_service import validate_instance
from backend.services.result_standardizer import standardize, select_best

logger = logging.getLogger(__name__)
queue = ThreadPoolExecutor(max_workers=1,thread_name_prefix="schedule")

def prepare(request, preflight=True):
    source = dataset(request.dataset_id)
    try:
        instance = freeze(source["instance"],request)
    except (ValueError,KeyError,TypeError) as exc:
        raise AppError("INVALID_INPUT","Selection or horizon is invalid.",str(exc)) from exc
    errors = validate_instance(instance,preflight=True) if preflight else []
    routing = route(len(request.job_ids),instance,request.settings.model_dump())
    if request.algorithms:
        from backend.services.v5_service import is_v5
        supported={"CP-SAT","SS Micro-GA Fast"} if is_v5(instance) else {"MILP","Improved GA","MLPSO"}
        if len(request.algorithms)!=len(set(request.algorithms)) or not set(request.algorithms)<=supported:
            raise AppError("UNSUPPORTED_ALGORITHM","Choose algorithms compatible with this dataset.")
        unavailable = [a for a in request.algorithms if a in routing["availability"] and not routing["availability"][a]["available"]]
        if unavailable:
            raise AppError("UNAVAILABLE_ALGORITHM","Selected algorithm is unavailable.",unavailable)
        routing["algorithms"]=request.algorithms
        routing["selection_mode"]="Manual"
        routing["routing_reason"]="Explicit user selection: " + ", ".join(request.algorithms) + ". Automatic rules were not applied."
    if not routing["algorithms"]:
        errors.append({"entity":"Algorithm","field":"availability","message":"No compatible scheduling engine is available."})
    if "MILP" in routing["algorithms"] and not instance["metadata"]["resource_constraints_redundant_under_station_capacity"]:
        errors.append({"entity":"Resources","field":"MILP","message":"Existing MILP requires resource redundancy proven under workstation capacity."})
    return instance,errors,routing

def start(request):
    instance,errors,routing = prepare(request)
    if errors:
        raise AppError("VALIDATION_FAILED","Fix validation issues before running a solver.",errors)
    run_id = uuid.uuid4().hex
    folder = DATA_DIR / "runs" / run_id
    folder.mkdir(parents=True,exist_ok=True)
    path = folder / "instance.json"
    content = dump(instance)
    path.write_text(content,encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    with connect() as db:
        db.execute("INSERT INTO runs(id,dataset_id,created_at,status,request,instance_path,instance_sha256) VALUES(?,?,?,?,?,?,?)",
            (run_id,request.dataset_id,now(),"queued",dump(request.model_dump()),str(path),digest))
        for algorithm in routing["algorithms"]:
            db.execute("INSERT INTO algorithm_results(run_id,algorithm,status) VALUES(?,?,?)",(run_id,algorithm,"queued"))
    queue.submit(execute,run_id,str(path),digest,request.settings.model_dump(),routing)
    return {"run_id":run_id,"status":"queued",**routing}

def worker(algorithm,path,digest,settings):
    # Top-level picklable function: safe with Windows spawn. All workers read the same immutable file.
    from pathlib import Path
    import time
    started = time.perf_counter()
    started_at = now()
    try:
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest:
            raise RuntimeError("Frozen instance hash mismatch.")
        if algorithm=="MILP":
            from backend.services.algorithms.milp_adapter import solve
        elif algorithm=="Improved GA":
            from backend.services.algorithms.improved_ga_adapter import solve
        elif algorithm=="MLPSO":
            from backend.services.algorithms.mlpso_adapter import solve
        elif algorithm=="CP-SAT":
            from backend.services.algorithms.v5_adapter import solve_exact as solve
        elif algorithm=="SS Micro-GA Fast":
            from backend.services.algorithms.v5_adapter import solve_micro as solve
        else:
            raise ValueError("Unsupported algorithm: "+algorithm)
        from contextlib import redirect_stdout
        from backend.services.progress_service import log_path
        with log_path(path,algorithm).open("w",encoding="utf-8") as log, redirect_stdout(log):
            raw = solve(path,settings)
        instance = json.loads(Path(path).read_text(encoding="utf-8"))
        result = standardize(algorithm,raw,instance)
        result.update(instance_sha256=digest,started_at=started_at,finished_at=now(),wall_runtime_sec=time.perf_counter()-started)
        # Keep validated results even if the parent process fails to persist them.
        checkpoint=log_path(path,algorithm).with_suffix('.result.json')
        temporary=checkpoint.with_suffix('.tmp')
        temporary.write_text(dump(result),encoding='utf-8')
        temporary.replace(checkpoint)
        return result
    except Exception as exc:
        logger.exception("Solver %s failed",algorithm)
        # Persist the traceback with this run, not only in the server console.
        import traceback
        from backend.services.progress_service import log_path
        try:
            with log_path(path,algorithm).open("a",encoding="utf-8") as log:
                traceback.print_exc(file=log)
        except OSError:
            logger.warning("Could not persist solver traceback",exc_info=True)
        environment = isinstance(exc,(ImportError,SystemError)) or "Application Control" in str(exc) or "DLL load" in str(exc)
        return {"algorithm":algorithm,"feasible":False,"status":"Environment blocked" if environment else "Failed",
                "error_code":"ENVIRONMENT_BLOCKED" if environment else "SOLVER_FAILED",
                "message":"Solver dependencies could not load. Check the backend environment." if environment else "Solver failed. See backend logs for technical details.",
                "runtime_sec":time.perf_counter()-started,"operations":[],"instance_sha256":digest}

def execute(run_id,path,digest,settings,routing):
    results = []
    try:
        with connect() as db:
            db.execute("UPDATE runs SET status='running' WHERE id=?",(run_id,))
            db.execute("UPDATE algorithm_results SET status='running' WHERE run_id=?",(run_id,))
        with ProcessPoolExecutor(max_workers=len(routing["algorithms"]),mp_context=get_context("spawn")) as pool:
            pending = {pool.submit(worker,algo,path,digest,settings):algo for algo in routing["algorithms"]}
            for future in as_completed(pending):
                try:
                    result = future.result()
                except Exception:
                    logger.exception("Solver worker exited")
                    result = {"algorithm":pending[future],"feasible":False,"status":"Failed","message":"Solver process exited unexpectedly.","operations":[]}
                try:
                    serialized=dump(result)
                except (ValueError,TypeError):
                    logger.exception("Solver result could not be serialized")
                    result={"algorithm":pending[future],"feasible":False,"status":"Failed",
                            "message":"Result could not be saved. Check the run logs.","operations":[]}
                    serialized=dump(result)
                results.append(result)
                with connect() as db:
                    db.execute("UPDATE algorithm_results SET status=?,result=? WHERE run_id=? AND algorithm=?",
                               (result["status"],serialized,run_id,result["algorithm"]))
        results.sort(key=lambda r:routing["algorithms"].index(r["algorithm"]))
        best,reason = select_best(results)
        result = {"run_id":run_id,**routing,"algorithms":results,"selected":best,"selection_reason":reason,"instance_sha256":digest}
        with connect() as db:
            db.execute("UPDATE runs SET status=?,finished_at=?,result=? WHERE id=?",
                       ("completed" if best else "failed",now(),dump(result),run_id))
    except Exception:
        logger.exception("Scheduling run %s failed",run_id)
        with connect() as db:
            db.execute("UPDATE runs SET status='failed',finished_at=?,result=? WHERE id=?",
                       (now(),dump({"run_id":run_id,"algorithms":[],"selected":None,"selection_reason":"Run failed while saving results; see backend logs."}),run_id))
            db.execute("UPDATE algorithm_results SET status='Failed' WHERE run_id=? AND status IN ('running','queued')",(run_id,))
