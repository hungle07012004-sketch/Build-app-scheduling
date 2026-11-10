"""Integration boundary for the supplied V5 physical model (no solver rewrites)."""
import copy
import math
from collections import Counter
from datetime import datetime, timedelta
from backend.errors import AppError
from backend.services.working_calendar import apply_to_new_instance
from backend.services.v5_validation import input_issues, check_raw_assignment, check_precedence

SCHEMA = "AL13_BENCHMARK_V5"


def is_v5(data):
    return data.get("metadata", {}).get("schema_version") == SCHEMA


def display_fields(data):
    """Add UI lookup tables; solver-owned V5 structures remain unchanged."""
    data["station_master"] = [
        {"station_id": rid, "station_group": rid.removeprefix("WS_"), "capacity": 1}
        for rid in data["resource_windows"] if rid.startswith("WS_")]
    data["station_windows"] = {
        r["station_id"]: data["resource_windows"][r["station_id"]]
        for r in data["station_master"]}
    return data


def workbook(path, filename, digest):
    from backend.solver_v5 import generate_benchmark_instances as gen
    tables = {k: gen.clean_df(v) for k, v in gen.load_source(path).items()}
    validation = tables["validation"]
    if "PASS" in validation and not validation["PASS"].astype(str).str.upper().eq("PASS").all():
        raise ValueError("V5 workbook contains non-PASS validation rows.")
    origin = min(gen.excel_datetime(x) for x in tables["shift"]["Shift_Start"].dropna())
    # Match the benchmark's default: repeat the staffed shift pattern to day 60,
    # retaining breaks and only explicitly recorded downtime dates.
    end = origin + timedelta(days=60)
    return display_fields(apply_to_new_instance(gen.build_instance(
        tables, tables["jobs"], filename, path, digest,
        gen.build_resource_windows(tables, origin, end), origin, end)))


def validate(data):
    from backend.solver_v5.benchmark_common import validate_instance_schema, V5ScheduleDecoder, JOB_LEVEL_OPS
    try:
        validate_instance_schema(data)
        errors = input_issues(data)
        if errors:
            return errors
        def numeric(v):
            return isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v)
        meta=data["metadata"]
        origin=datetime.fromisoformat(meta["origin_datetime"])
        end=datetime.fromisoformat(meta["horizon_end_datetime"])
        if origin.tzinfo or end.tzinfo or not numeric(meta["horizon_end_min"]) or end<=origin:
            raise ValueError("Invalid factory-local V5 horizon.")
        if abs((end-origin).total_seconds()/60-meta["horizon_end_min"])>1e-5:
            raise ValueError("V5 horizon datetime/minute values disagree.")
        jobs={j["job_id"]:j for j in data["jobs"]}
        for j in jobs.values():
            for field in ("release_min","element_ready_min","loopset_release_min","due_min"):
                if not numeric(j[field]): raise ValueError("Invalid job time: "+field)
            if not isinstance(j["batch_qty"],int) or j["batch_qty"]<=0:
                raise ValueError("Job quantity must be a positive integer.")
            if not isinstance(j["priority"],int) or j["priority"]<1:
                raise ValueError("Priority must be a positive integer.")
        counts=Counter((r["job_id"],r["operation_code"]) for r in data["job_operations"])
        if counts!=Counter({(j,op):1 for j in jobs for op in JOB_LEVEL_OPS}):
            raise ValueError("Every V5 job operation must occur exactly once.")
        for r in data["job_operations"]:
            if not numeric(r["processing_min"]) or r["processing_min"]<=0:
                raise ValueError("Invalid processing time.")
        cycles=set(); quantities=Counter()
        for r in data["oil_cycles"]:
            if r["job_id"] not in jobs or r["oil_cycle_id"] in cycles:
                raise ValueError("Unknown job or duplicate OIL cycle.")
            cycles.add(r["oil_cycle_id"])
            if not isinstance(r["cycle_qty"],int) or not 1<=r["cycle_qty"]<=6:
                raise ValueError("OIL cycle quantity must be 1..6.")
            durations=[r[k] for k in ("w4_load_min","auto_dip_min","w5_unload_min")]
            if not all(numeric(v) and v>=0 for v in durations) or sum(durations)<=0:
                raise ValueError("Invalid OIL durations.")
            quantities[r["job_id"]]+=r["cycle_qty"]
        if any(quantities[j]!=row["batch_qty"] for j,row in jobs.items()):
            raise ValueError("OIL cycles must conserve each job quantity.")
        for rid,windows in data["resource_windows"].items():
            last=-math.inf; ids=set()
            for w in sorted(windows,key=lambda w:w["start_min"]):
                a,b=w["start_min"],w["end_min"]
                if not numeric(a) or not numeric(b) or b<=a or a<last-1e-6 or w["window_id"] in ids:
                    raise ValueError("Invalid/overlapping/duplicate resource window: "+rid)
                last=b;ids.add(w["window_id"])
        V5ScheduleDecoder(data)
        return []
    except (KeyError,ValueError,TypeError,RuntimeError,OverflowError) as exc:
        return [{"entity":"V5 dataset","field":"schema","message":str(exc)}]


def freeze(source, request):
    from backend.services.instance_service import parse_datetime
    errors = input_issues(source)
    if errors:
        raise AppError("VALIDATION_FAILED", "Fix V5 input constraints before scheduling.", errors)
    data=copy.deepcopy(source)
    ids=request.job_ids
    if len(ids)!=len(set(ids)):
        raise AppError("DUPLICATE_JOB","Selected Job IDs must be unique.")
    if not set(ids)<={j["job_id"] for j in data["jobs"]}:
        raise AppError("UNKNOWN_JOB","Selected Job IDs are not in this dataset.")
    origin=parse_datetime(data["metadata"]["origin_datetime"])
    start,end=map(parse_datetime,(request.horizon_start,request.horizon_end))
    if not origin<=start<end<=parse_datetime(data["metadata"]["horizon_end_datetime"]):
        raise AppError("INVALID_HORIZON","Selection must stay inside the uploaded V5 calendar.")
    lo,hi=(start-origin).total_seconds()/60,(end-origin).total_seconds()/60
    for key in ("jobs","job_operations","precedence","oil_cycles"):
        data[key]=[r for r in data[key] if r["job_id"] in ids]
    data["metadata"].update(size=len(ids),selected_job_ids=ids,horizon_start_min=lo,horizon_end_min=hi,
        horizon_start_datetime=start.isoformat(sep=" "),horizon_end_datetime=end.isoformat(sep=" "))
    return display_fields(apply_to_new_instance(data))


def standardize(algorithm, raw, data):
    from backend.services.result_standardizer import json_metadata
    from backend.solver_v5.benchmark_common import V5ScheduleDecoder
    from backend.solver_v5.solve_milp_instance import _validate_warm_schedule
    rows=raw.get("schedule") or []
    cmax=raw.get("makespan_min",raw.get("best_makespan_min"))
    errors=[]; completion={}
    try:
        if not rows or raw.get("feasible") is False:
            raise ValueError("Solver returned no feasible schedule.")
        input_errors = validate(data)
        if input_errors:
            raise ValueError("Invalid frozen input: " + "; ".join(f"{e['entity']}.{e['field']}: {e['message']}" for e in input_errors))
        dec=V5ScheduleDecoder(data)
        oil={r["oil_cycle_id"]:r for r in data["oil_cycles"]}
        for r in rows:
            if r.get("Event_Type") not in ("JOB_OP","OIL_CYCLE","SETUP","2C_BATCH"):
                raise ValueError("Unknown V5 schedule event.")
            check_raw_assignment(data, r)
            values=[r.get(k) for k in ("Start_Min","Completion_Min","Processing_Min")]
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
                raise ValueError("Non-finite event times.")
            s,e,p=values
            if e<s or abs(e-s-p)>0.005 or s<data["metadata"].get("horizon_start_min",0)-0.005:
                raise ValueError("Invalid event duration or scheduling start.")
            if r["Event_Type"]=="OIL_CYCLE":
                c=oil[r["Oil_Cycle_ID"]]
                if r["Job_ID"]!=c["job_id"] or r["Cycle_Qty"]!=c["cycle_qty"]:
                    raise ValueError("OIL cycle identity/quantity mismatch.")
                for output,source in (("W4_Load_Min","w4_load_min"),("Auto_Dip_Min","auto_dip_min"),("W5_Unload_Min","w5_unload_min")):
                    if abs(r[output]-c[source])>0.005: raise ValueError("OIL phase duration mismatch.")
        # PRE order also validates CP-SAT's circuit predecessor / clean-pipeline setup.
        order=[r["Job_ID"] for r in sorted(rows,key=lambda r:r["Start_Min"]) if r.get("Operation_Code")=="PRE"]
        checked=_validate_warm_schedule(dec,order,{"makespan_min":cmax},rows)
        check_precedence(data, [dict(eventType=r["Event_Type"],jobId=r["Job_ID"],
            operation=r.get("Operation_Code"),startMin=r["Start_Min"],endMin=r["Completion_Min"]) for r in rows])
        completion=checked["job_completion_min"]
    except (ValueError,KeyError,TypeError,RuntimeError,OverflowError) as exc:
        errors=[{"entity":"V5 schedule","field":"physical_validation","message":str(exc)}]
    feasible=not errors
    operations=[];origin=datetime.fromisoformat(data["metadata"]["origin_datetime"])
    jobs={j["job_id"]:j for j in data["jobs"]}
    if feasible:
        for index,r in enumerate(rows):
            contributors=r["Job_ID"].split(";")
            kind=r["Event_Type"]
            op=r.get("Operation_Code") or ("2C_BATCH" if kind=="2C_BATCH" else "SETUP")
            resource_text=r.get("Resources") or ";".join(filter(None,[r.get("Station_ID"),r.get("Resource_Units")]))
            resources=resource_text.split(";")
            sid=next((x for x in resources if x.startswith("WS_")),"")
            w=next((w for w in data["resource_windows"][sid] if w["start_min"]<=r["Start_Min"]+0.005 and w["end_min"]>=r["Completion_Min"]-0.005),None)
            operations.append({
                "eventId":r.get("Event_ID") or f"event-{index}", "eventType":kind,
                "jobId":r["Job_ID"],"jobIds":contributors,
                "product":" / ".join(dict.fromkeys(jobs[j].get("product_code","") for j in contributors)),
                "operation":op,"workstation":sid,"windowId":w["window_id"] if w else None,
                "startMin":r["Start_Min"],"endMin":r["Completion_Min"],"processingMin":r["Processing_Min"],
                "startTime":(origin+timedelta(minutes=r["Start_Min"])).isoformat(sep=" "),
                "finishTime":(origin+timedelta(minutes=r["Completion_Min"])).isoformat(sep=" "),
                "resourceUnits":resource_text,"status":"Scheduled"})
    optimal=feasible and algorithm=="CP-SAT" and raw.get("proven_optimal") is True
    return {"algorithm":algorithm,"feasible":feasible,"proven_optimal":optimal,
        "objective":"MIN_CMAX_FINAL_2C","status":"Optimal" if optimal else "Feasible" if feasible else "Invalid result" if rows else raw.get("status_name","No feasible solution"),
        "makespan_min":cmax if feasible else None,
        "total_tardiness_min":sum(max(0,c-jobs[j]["due_min"]) for j,c in completion.items()) if feasible else None,
        "total_flow_time_min":sum(c-jobs[j]["release_min"] for j,c in completion.items()) if feasible else None,
        "tardy_jobs":sum(c>jobs[j]["due_min"]+1e-6 for j,c in completion.items()) if feasible else None,
        "runtime_sec":raw.get("runtime_sec",raw.get("total_runtime_sec")),
        "operations":operations,"validation_issues":errors,
        **({"message":raw["message"]} if not feasible and raw.get("message") else {}),
        "metadata":json_metadata({**{k:v for k,v in raw.items() if k!="schedule"},
            **({"working_calendar_policy":data["metadata"]["working_calendar_policy"]} if data["metadata"].get("working_calendar_policy") else {})})}
