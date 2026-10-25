import copy
import hashlib
import json
import time
import pytest
from backend.tests.fixtures import instance, make_workbook
from backend.services.algorithms.common import solver_module
from backend.services.instance_service import freeze, route
from backend.services.result_standardizer import standardize, select_best
from backend.services.validation_service import validate_instance, validate_schedule
from backend.models.schemas import ScheduleRequest
from backend.database.db import connect
from backend.services import optimization_service

def upload(client,n=2):
    response=client.post("/api/input/upload",files={"file":("fixture.json",json.dumps(instance(n)),"application/json")})
    assert response.status_code==200,response.text
    return response.json()["id"]

def request(dataset_id,n=2):
    return {"dataset_id":dataset_id,"job_ids":[f"J{i+1:03}" for i in range(n)],
            "horizon_start":"2026-09-01T08:00","horizon_end":"2026-09-05T12:00",
            "algorithms":["MILP"] if n<=20 else ["Improved GA","MLPSO"],
            "settings":{"runs":1,"ga_population":6,"ga_generations":2,"mlpso_population":6,"mlpso_iterations":2,"milp_time_limit":30}}

def decoded(data):
    decoder=solver_module("benchmark_common").FrozenInstanceDecoder(data)
    metrics,rows=decoder.decode(decoder.job_ids,keep_schedule=True)
    return {**metrics,"schedule":rows,"runtime_sec":0.1,"proven_optimal":False}

def candidate(name,cmax,tard,flow,runtime=1):
    return dict(algorithm=name,feasible=True,makespan_min=cmax,total_tardiness_min=tard,total_flow_time_min=flow,runtime_sec=runtime)

@pytest.mark.parametrize("n,algos",[(1,["Improved GA"]),(20,["Improved GA"]),(21,["Improved GA"]),(50,["MLPSO"])])
def test_routing(n,algos):
    assert route(n)["algorithms"]==algos

@pytest.mark.parametrize("a,b,winner",[
    (candidate("GA",100,50,1000,50),candidate("MLPSO",101,0,900,1),"GA"),
    (candidate("GA",100,50,1000),candidate("MLPSO",100,40,1100),"MLPSO"),
    (candidate("GA",100,40,1000),candidate("MLPSO",100,40,900),"MLPSO"),
])
def test_selection(a,b,winner):
    assert select_best([a,b])[0]["algorithm"]==winner

@pytest.mark.parametrize("failed",["Improved GA","MLPSO"])
def test_one_solver_failure_keeps_other(failed):
    good=candidate("MLPSO" if failed=="Improved GA" else "Improved GA",100,20,500)
    assert select_best([{"algorithm":failed,"feasible":False},good])[0]==good

def test_standardizer_and_datetime():
    data=instance()
    result=standardize("MILP",decoded(data),data)
    assert result["feasible"] and not result["proven_optimal"] and result["status"]=="Feasible"
    op=result["operations"][0]
    assert (op["jobId"],op["operation"],op["workstation"])==("J001","2B","2B_01")
    assert op["startTime"]=="2026-09-01 08:00:00"
    assert op["finishTime"]=="2026-09-01 08:05:00"
    assert op["processingMin"]==op["endMin"]-op["startMin"]

@pytest.mark.parametrize("failure",["missing","overlap","precedence","window","processing","makespan"])
def test_output_corruption_rejected(failure):
    data=instance();raw=decoded(data);rows=raw["schedule"];cmax=raw["makespan_min"]
    if failure=="missing": rows.pop()
    if failure=="overlap": rows[10]["Start_Min"]=0;rows[10]["Completion_Min"]=6
    if failure=="precedence": rows[1]["Start_Min"]=5;rows[1]["Completion_Min"]=10
    if failure=="window": rows[0]["Window_ID"]="invalid"
    if failure=="processing": rows[0]["Processing_Min"]=100
    if failure=="makespan": cmax+=1
    assert validate_schedule(data,rows,cmax)

def test_invalid_upload_and_selection_prevent_execution(client,monkeypatch):
    data=instance();data["operation_options"][0]["processing_min"]=-1
    response=client.post("/api/input/upload",files={"file":("bad.json",json.dumps(data))})
    assert response.status_code==400
    dataset_id=upload(client)
    called=[]
    monkeypatch.setattr(optimization_service.queue,"submit",lambda *args:called.append(args))
    body=request(dataset_id);body["job_ids"]=["MISSING"]
    assert client.post("/api/schedules/run",json=body).status_code==400
    assert not called
    body=request(dataset_id);body["horizon_end"]="2026-09-01T08:01"
    assert client.post("/api/schedules/run",json=body).status_code==400
    assert not called

def test_instance_snapshot_horizon_and_duplicate_ids():
    source=instance();before=copy.deepcopy(source)
    body=request("test");body["horizon_start"]="2026-09-01T08:30"
    frozen=freeze(source,ScheduleRequest(**body))
    assert source==before
    assert all(w["start_min"]>=30 for ws in frozen["station_windows"].values() for w in ws)
    body["job_ids"]=["J001","J001"]
    with pytest.raises(Exception): freeze(source,ScheduleRequest(**body))

def test_acceptance_and_execution(client,monkeypatch):
    dataset_id=upload(client)
    monkeypatch.setattr(optimization_service.queue,"submit",lambda *args:None)
    started=client.post("/api/schedules/run",json=request(dataset_id)).json()
    rid=started["run_id"]
    assert client.post(f"/api/schedules/{rid}/apply").status_code==409
    assert client.post(f"/api/schedules/{rid}/accept").status_code==400
    selected=standardize("MILP",decoded(instance()),instance())
    result={"run_id":rid,"algorithms":[selected],"selected":selected,"selection_reason":"Only feasible result."}
    with connect() as db:
        db.execute("UPDATE runs SET status='completed',result=? WHERE id=?",(json.dumps(result),rid))
    assert client.post(f"/api/schedules/{rid}/accept").status_code==200
    assert client.post(f"/api/schedules/{rid}/apply").status_code==200
    assert client.post(f"/api/schedules/{rid}/apply").status_code==200
    assert len(client.get("/api/execution").json())==2
    response=client.patch("/api/execution/jobs/J001",json={"run_id":rid,"status":"Completed"})
    assert response.status_code==200
    assert client.get("/api/analytics/overview").json()["completed_jobs"]==1
    assert client.get(f"/api/schedules/{rid}/result").json()["selected"]["operations"]==selected["operations"]

def wait_result(client,rid,timeout=150):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=client.get(f"/api/schedules/{rid}/status").json()
        if state["status"] in ("completed","failed","interrupted"):
            return client.get(f"/api/schedules/{rid}/result").json()
        time.sleep(.25)
    pytest.fail("Solver smoke run timed out.")

@pytest.mark.parametrize("n",[10,50])
def test_real_solver_smoke(client,n):
    dataset_id=upload(client,n)
    body=request(dataset_id,n)
    valid=client.post("/api/schedules/validate",json=body)
    assert valid.status_code==200 and valid.json()["valid"],valid.text
    started=client.post("/api/schedules/run",json=body)
    assert started.status_code==202,started.text
    rid=started.json()["run_id"]
    result=wait_result(client,rid)
    if any(r.get("error_code")=="ENVIRONMENT_BLOCKED" for r in result["algorithms"]):
        pytest.skip("Blocked by Windows Application Control / numerical DLL environment.")
    assert result["selected"] and result["selected"]["feasible"],result
    assert len(result["selected"]["operations"])==n*10
    if n==50:
        assert len(result["algorithms"])==2
        assert all(r["feasible"] for r in result["algorithms"]),result
        assert len({r["instance_sha256"] for r in result["algorithms"]})==1
        assert result["selected"]["makespan_min"]==min(r["makespan_min"] for r in result["algorithms"])
    with connect() as db:
        row=db.execute("SELECT instance_path,instance_sha256 FROM runs WHERE id=?",(rid,)).fetchone()
    from pathlib import Path
    assert hashlib.sha256(Path(row["instance_path"]).read_bytes()).hexdigest()==row["instance_sha256"]

def test_excel_upload(client):
    response=client.post("/api/input/upload",files={"file":("test-al13.xlsx",make_workbook(),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    if response.status_code==503:
        pytest.skip("Blocked by Windows Application Control / pandas DLL environment.")
    assert response.status_code==200,response.text
    assert response.json()["jobs"]==10
    assert len(client.get("/api/processing-times").json())==100

@pytest.mark.parametrize("failed",["Improved GA","MLPSO"])
def test_parallel_orchestration_survives_worker_failure(client,monkeypatch,failed):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    barrier=threading.Barrier(2)
    seen=[]
    def worker(algorithm,path,digest,settings):
        seen.append((algorithm,path,digest))
        barrier.wait(timeout=5)
        if algorithm==failed:
            raise RuntimeError("Deliberate test worker failure")
        data=json.loads(__import__("pathlib").Path(path).read_text(encoding="utf-8"))
        return standardize(algorithm,decoded(data),data)
    monkeypatch.setattr(optimization_service,"ProcessPoolExecutor",lambda **kwargs:ThreadPoolExecutor(max_workers=kwargs["max_workers"]))
    monkeypatch.setattr(optimization_service,"worker",worker)
    started=client.post("/api/schedules/run",json=request(upload(client,50),50))
    assert started.status_code==202
    result=wait_result(client,started.json()["run_id"])
    assert result["selected"]["feasible"]
    assert result["selected"]["algorithm"]!=failed
    assert len(seen)==2 and seen[0][1:]==seen[1][1:]

def test_input_revision_preserves_original(client):
    dataset_id=upload(client)
    response=client.patch(f"/api/input/{dataset_id}/jobs/J001",json={"priority":2,"due_datetime":"2026-09-02T09:00"})
    assert response.status_code==200,response.text
    revised=response.json()["id"]
    assert revised!=dataset_id
    old=client.get("/api/jobs",params={"dataset_id":dataset_id}).json()
    new=client.get("/api/jobs",params={"dataset_id":revised}).json()
    assert old[0]["priorityValue"]==1 and new[0]["priorityValue"]==2
    assert new[0]["dueDate"]=="2026-09-02 09:00:00"
    malformed=request(revised);malformed["horizon_start"]="invalid"
    assert client.post("/api/schedules/validate",json=malformed).status_code==400

def test_actual_progress_log_parser(tmp_path):
    from backend.services.progress_service import log_path,read_progress
    path=tmp_path/"instance.json"
    log_path(path,"MLPSO").write_text("[MLPSO] iter    20/100 (20%) | Gbest=123.500 | stall=1\n",encoding="utf-8")
    assert read_progress(path,"MLPSO")=={"iteration":20,"limit":100,"best_makespan_min":123.5,"scope":"Current independent run"}
