import copy
import hashlib
import json
import pytest
from backend.models.schemas import SolverSettings,ScheduleRequest
from backend.services import v5_service
from backend.services.algorithms.v5_adapter import solve_exact,solve_micro
from backend.services.instance_service import freeze,route
from backend.services.result_standardizer import standardize
from backend.tests.test_integration import wait_result


def instance():
    from backend.solver_v5.benchmark_common import DEFAULT_RESOURCE_BY_OP,SETUP_RESOURCES,JOB_LEVEL_OPS
    resources=set(SETUP_RESOURCES)|{"WS_OIL","OP_2A","OP_PACK"}
    for values in DEFAULT_RESOURCE_BY_OP.values(): resources.update(values)
    jobs=[];ops=[];edges=[];cycles=[]
    for i,(qty,product) in enumerate([(10,"A"),(5,"A"),(5,"A"),(10,"B")],1):
        j=f"J{i:03}"
        jobs.append(dict(job_id=j,product_code=product,batch_key=product,batch_qty=qty,priority=1,
                         release_min=0,element_ready_min=0,loopset_release_min=0,due_min=500))
        for op in JOB_LEVEL_OPS:
            ops.append(dict(job_id=j,operation_code=op,processing_min=5,first_belt_completion_lag_min=1,
                            workstation_id=DEFAULT_RESOURCE_BY_OP[op][0],primary_operator=DEFAULT_RESOURCE_BY_OP[op][1]))
        for a,b in zip(["2B","PRE","FINAL","LASER","SCALE","OIL","SEAL"],["PRE","FINAL","LASER","SCALE","OIL","SEAL","VISUAL"]):
            edges.append(dict(job_id=j,pred=a,succ=b,lag_min=1,relation_type="SS_FIRST_BELT" if a=="PRE" else "FS"))
        remaining=qty;idx=0
        while remaining:
            idx+=1;q=min(6,remaining);remaining-=q
            cycles.append(dict(job_id=j,oil_cycle_id=f"{j}_OC{idx}",cycle_no=idx,cycle_qty=q,
                               w4_load_min=1,auto_dip_min=2,w5_unload_min=1,machine_occupancy_min=4))
    return dict(metadata=dict(schema_version=v5_service.SCHEMA,instance_name="V5_INTEGRATION",size=4,
            origin_datetime="2026-10-01 06:00:00",horizon_end_datetime="2026-10-02 06:00:00",horizon_end_min=1440),
        jobs=jobs,job_operations=ops,precedence=edges,oil_cycles=cycles,
        setup_matrix={"A":{"A":0,"B":3},"B":{"A":3,"B":0}},setup_resources=list(SETUP_RESOURCES),
        resource_by_operation={k:list(v) for k,v in DEFAULT_RESOURCE_BY_OP.items()},
        resource_windows={r:[dict(window_id=r+"_W1",calendar_date="2026-10-01",shift_id="A",start_min=0,end_min=1440)] for r in sorted(resources)},
        batching=dict(batch_size=10,processing_min_per_batch=4,cross_job_pooling=True))


def test_v5_routing_and_snapshot():
    data=instance(); original=copy.deepcopy(data)
    req=ScheduleRequest(dataset_id="test",job_ids=["J002","J003"],horizon_start="2026-10-01T07:00",horizon_end="2026-10-02T06:00")
    frozen=freeze(data,req)
    assert data==original
    assert v5_service.validate(frozen)==[]
    assert all(w["start_min"]>=60 for ws in frozen["resource_windows"].values() for w in ws)
    assert route(4,data)["algorithms"]==["CP-SAT"]
    assert route(40,data)["algorithms"]==["SS Micro-GA Fast"]
    req.job_ids=["J002"]
    assert v5_service.validate(freeze(data,req))


@pytest.mark.parametrize("algorithm,solve",[("CP-SAT",solve_exact),("SS Micro-GA Fast",solve_micro)])
def test_real_v5_adapters(tmp_path,algorithm,solve):
    data=instance()
    path=tmp_path/"instance.json";path.write_text(json.dumps(data))
    before=path.read_bytes()
    settings=SolverSettings(exact_time_limit=5,exact_workers=1,micro_population=4,micro_generations=2,micro_max_decodes=100).model_dump()
    raw=solve(path,settings)
    result=standardize(algorithm,raw,data)
    assert result["feasible"],result["validation_issues"]
    assert result["objective"]=="MIN_CMAX_FINAL_2C"
    assert len(result["operations"])==len(raw["schedule"])
    assert any(len(o["jobIds"])==2 for o in result["operations"])
    assert {"OIL_CYCLE","SETUP","2C_BATCH","JOB_OP"}=={o["eventType"] for o in result["operations"]}
    assert path.read_bytes()==before
    bad=copy.deepcopy(raw)
    bad["schedule"]=bad["schedule"][:-1]
    assert not standardize(algorithm,bad,data)["feasible"]


@pytest.mark.parametrize("chosen",["CP-SAT","SS Micro-GA Fast"])
def test_v5_app_flow(client,chosen):
    uploaded=client.post("/api/input/upload",files={"file":("v5.json",json.dumps(instance()),"application/json")})
    assert uploaded.status_code==200,uploaded.text
    ds=uploaded.json()["id"]
    for endpoint in ["jobs","workstations","calendar","resources","processing-times","analytics/overview"]:
        assert client.get("/api/"+endpoint).status_code==200
    body=dict(dataset_id=ds,job_ids=[j["job_id"] for j in instance()["jobs"]],
              horizon_start="2026-10-01T06:00",horizon_end="2026-10-02T06:00",
              algorithms=[chosen],settings={"exact_time_limit":5,"exact_workers":1,"micro_population":4,"micro_generations":2,"micro_max_decodes":100})
    valid=client.post("/api/schedules/validate",json=body).json()
    assert valid["valid"] and valid["algorithms"]==[chosen],valid
    started=client.post("/api/schedules/run",json=body)
    assert started.status_code==202,started.text
    rid=started.json()["run_id"];result=wait_result(client,rid)
    assert result["selected"]["feasible"],result
    assert result["selected"]["algorithm"]==chosen
    response=client.get(f"/api/schedules/{rid}/export.xlsx")
    assert response.status_code==200
    from io import BytesIO
    from openpyxl import load_workbook
    wb=load_workbook(BytesIO(response.content))
    assert wb.sheetnames==["Schedule Summary","Detailed Schedule","Algorithm Comparison"]
    if chosen=="SS Micro-GA Fast":
        summary=dict(wb["Schedule Summary"].iter_rows(min_row=2,values_only=True))
        assert "120" in summary["Optimization time limit"]
        assert summary["Search stop reason"]=="generation_limit"
    rows=list(wb["Detailed Schedule"].iter_rows(min_row=2,values_only=True))
    assert len(rows)==len(result["selected"]["operations"])
    from datetime import datetime
    for row,op in zip(rows,result["selected"]["operations"]):
        assert row[0]==op["jobId"]
        assert row[2:4]==(op["operation"],op["workstation"])
        assert abs((row[4]-datetime.fromisoformat(op["startTime"])).total_seconds())<=0.001
        assert row[6]==op["processingMin"]
        from backend.services.excel_service import shift_label
        assert row[7]==shift_label(datetime.fromisoformat(op["startTime"]),datetime.fromisoformat(op["finishTime"]))
    assert client.post(f"/api/schedules/{rid}/apply").status_code==409
    assert client.post(f"/api/schedules/{rid}/accept").status_code==200
    assert client.post(f"/api/schedules/{rid}/apply").status_code==200
    assert len(client.get("/api/execution").json())==4
    assert client.patch("/api/execution/jobs/J001",json={"run_id":rid,"status":"Completed"}).status_code==200
    assert client.patch(f"/api/input/{ds}/jobs/J001",json={"priority":2}).status_code==200
    assert client.get(f"/api/schedules/{rid}/result").json()["selected"]==result["selected"]
    assert client.get("/api/analytics/overview").status_code==200

@pytest.mark.parametrize("choices",[["MILP"],["CP-SAT","CP-SAT"],["MLPSO"]])
def test_reject_incompatible_selection(client,choices):
    ds=client.post("/api/input/upload",files={"file":("v5.json",json.dumps(instance()),"application/json")}).json()["id"]
    body=dict(dataset_id=ds,job_ids=[j["job_id"] for j in instance()["jobs"]],
              horizon_start="2026-10-01T06:00",horizon_end="2026-10-02T06:00",algorithms=choices)
    response=client.post("/api/schedules/validate",json=body)
    assert response.status_code==400,response.text

def test_excel_strings_never_become_formulas(monkeypatch):
    from backend.services import excel_service
    from io import BytesIO
    from openpyxl import load_workbook
    op=dict(jobId="=1+1",product="=HYPERLINK(1)",operation="PRE",workstation="WS_PRE",
            startTime="2026-10-01 06:00:00",finishTime="2026-10-01 06:05:00",
            startMin=0,endMin=5,processingMin=5,status="Scheduled")
    selected=dict(algorithm="CP-SAT",status="Feasible",feasible=True,operations=[op],
                  makespan_min=5,total_tardiness_min=0,total_flow_time_min=5,runtime_sec=1)
    monkeypatch.setattr(excel_service,"get_run",lambda _:dict(result=dict(selected=selected),request={}))
    wb=load_workbook(BytesIO(excel_service.export("test")),data_only=False)
    assert wb["Detailed Schedule"]["A2"].value=="=1+1"
    assert wb["Detailed Schedule"]["B2"].data_type=="s"
    assert wb["Detailed Schedule"]["A2"].data_type=="s"

def test_excel_parallel_comparison_and_missing_shift(monkeypatch):
    from backend.services import excel_service
    from openpyxl import load_workbook
    from io import BytesIO
    selected=dict(algorithm="SS Micro-GA Fast",status="Feasible",feasible=True,operations=[
        dict(jobId="J1;J2",jobIds=["J1","J2"],eventType="2C_BATCH",eventId="B1",product="A",
             operation="2C_BATCH",workstation="WS_2C",startTime="2026-10-01 06:00:00",
             finishTime="2026-10-01 06:05:00",processingMin=5,status="Scheduled")
    ],makespan_min=5,total_tardiness_min=0,total_flow_time_min=10,tardy_jobs=0,runtime_sec=1)
    # Export reports only recorded algorithms, including a failed participant.
    run=dict(status="completed",request=dict(algorithms=["SS Micro-GA Fast","MLPSO"],job_ids=["J1","J2"]),
             result=dict(selected=selected,selection_reason="Only feasible result.",
                         algorithms=[selected,dict(algorithm="MLPSO",status="Failed",feasible=False,runtime_sec=2)]))
    monkeypatch.setattr(excel_service,"get_run",lambda _:run)
    wb=load_workbook(BytesIO(excel_service.export("test")))
    summary=dict(wb["Schedule Summary"].iter_rows(min_row=2,values_only=True))
    assert summary["Selected scheduling method"]=="Parallel Optimization"
    assert summary["Number of jobs"]==2
    assert summary["Selection reason"]=="Only feasible result."
    assert summary["Run status"]=="completed"
    assert "no wall-clock limit" in summary["Optimization time limit"]
    comparison=list(wb["Algorithm Comparison"].iter_rows(min_row=2,values_only=True))
    assert comparison[0][7]=="Selected"
    assert comparison[1][0]=="MLPSO" and comparison[1][1] is False and comparison[1][7]=="Not Selected"
    assert wb["Detailed Schedule"].max_row==2
    assert wb["Detailed Schedule"]["H2"].value=="Shift 1"
    for sheet in wb:
        assert sheet.freeze_panes=="A2"
        assert sheet.auto_filter.ref==sheet.dimensions
        assert sheet["A1"].font.bold
