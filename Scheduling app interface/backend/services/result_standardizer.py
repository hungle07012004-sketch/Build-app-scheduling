from datetime import timedelta
from backend.services.instance_service import parse_datetime
from backend.services.validation_service import validate_schedule

def json_metadata(value):
    """Missing/infinite search statistics are null; scheduling data stays strict."""
    import math
    if isinstance(value,float) and not math.isfinite(value):
        return None
    if isinstance(value,dict):
        return {k:json_metadata(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [json_metadata(v) for v in value]
    return value

def standardize(algorithm, raw, instance):
    from backend.services import v5_service
    if v5_service.is_v5(instance): return v5_service.standardize(algorithm,raw,instance)
    rows = raw.get("schedule") or []
    cmax = raw.get("makespan_min",raw.get("best_makespan_min"))
    errors = validate_schedule(instance,rows,cmax)
    feasible = bool(rows) and raw.get("feasible",True) and not errors
    optimal = algorithm=="MILP" and raw.get("proven_optimal") is True and feasible
    status = "Optimal" if optimal else "Feasible incumbent (time limit)" if feasible and raw.get("status")==1 else "Feasible" if feasible else "Invalid result" if rows else "No feasible solution"
    origin = parse_datetime(instance["metadata"]["origin_datetime"])
    jobs = {j["job_id"]:j for j in instance["jobs"]}
    operations = []
    if feasible:
        for r in rows:
            operations.append({
                "jobId":r["Job_ID"],"product":jobs[r["Job_ID"]].get("product_code",""),
                "operation":r["Operation_Code"],"workstation":r["Station_ID"],"windowId":r["Window_ID"],
                "startMin":r["Start_Min"],"endMin":r["Completion_Min"],"processingMin":r["Processing_Min"],
                "startTime":(origin+timedelta(minutes=r["Start_Min"])).isoformat(sep=" "),
                "finishTime":(origin+timedelta(minutes=r["Completion_Min"])).isoformat(sep=" "),
                "resourceUnits":r.get("Resource_Units",""),"status":"Scheduled"})
    completion = {r["Job_ID"]:r["Completion_Min"] for r in rows if r["Operation_Code"]=="2C"} if feasible else {}
    tardiness = sum(max(0,c-jobs[j]["due_min"]) for j,c in completion.items())
    flow = sum(c-jobs[j]["release_min"] for j,c in completion.items())
    return {"algorithm":algorithm,"feasible":bool(feasible),"proven_optimal":bool(optimal),"status":status,
            "makespan_min":cmax if feasible else None,"total_tardiness_min":tardiness if feasible else None,
            "total_flow_time_min":flow if feasible else None,
            "tardy_jobs":sum(c>jobs[j]["due_min"]+1e-9 for j,c in completion.items()) if feasible else None,
            "runtime_sec":raw.get("runtime_sec",raw.get("total_runtime_sec")),
            "operations":operations,"validation_issues":errors,"metadata":json_metadata({k:v for k,v in raw.items() if k!="schedule"})}

def select_best(results):
    feasible = [r for r in results if r.get("feasible")]
    if not feasible:
        return None, "No valid feasible schedule."
    fields = ("makespan_min",) if all(r.get("objective")=="MIN_CMAX_FINAL_2C" for r in feasible) else ("makespan_min","total_tardiness_min","total_flow_time_min")
    ordered = sorted(feasible,key=lambda r:tuple(round(r[k],9) for k in fields))
    reason = "Only feasible result."
    if len(ordered)>1:
        reason = "Equal objective values; deterministic algorithm order."
        for field,label in zip(fields,("Lower makespan.","Lower total tardiness at equal makespan.","Lower total flow time at equal makespan and tardiness.")):
            if round(ordered[0][field],9) != round(ordered[1][field],9):
                reason = label
                break
    return ordered[0],reason
