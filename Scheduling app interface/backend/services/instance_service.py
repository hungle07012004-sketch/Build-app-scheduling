import copy
from datetime import datetime
from backend.config import SMALL_SCALE_MAX_JOBS
from backend.errors import AppError

OPS = ["2B", "PRE", "FINAL", "2A", "LASER", "SCALE", "OIL", "SEAL", "PACK", "2C"]

def parse_datetime(value):
    result = datetime.fromisoformat(str(value))
    if result.tzinfo is not None:
        raise ValueError("Use factory local date/time without a timezone offset.")
    return result

def route(size, data=None, settings=None):
    from backend.services.algorithm_selection import select
    return select(size,data,settings)


def resource_proof(data):
    from backend.services.v5_service import is_v5
    if is_v5(data): return
    stations = {r["station_id"]: int(r.get("capacity", 1)) for r in data["station_master"]}
    options = {}
    for row in data["operation_options"]:
        if int(row.get("eligible", 1)) == 1:
            options.setdefault(row["operation_code"], set()).add(row["station_id"])
    demand = {}
    for op, reqs in data["resource_requirements"].items():
        for req in reqs:
            rt = req["resource_type"]
            demand[rt] = demand.get(rt, 0) + int(req["required_qty"]) * sum(stations.get(s, 1) for s in options.get(op, []))
    caps = {(r["calendar_date"], r["shift_id"], r["resource_type"]): int(r["available_qty"])
            for r in data["resource_capacity_by_shift"]}
    shifts = {(w["calendar_date"], w["shift_id"]) for ws in data["station_windows"].values() for w in ws}
    details = {}
    for rt, upper in demand.items():
        base = int(data.get("base_resource_capacity", {}).get(rt, len(data["resource_units_by_type"].get(rt, []))))
        minimum = min([caps.get((d,s,rt), base) for d,s in shifts] or [base])
        minimum = min(minimum, len(data["resource_units_by_type"].get(rt, [])))
        details[rt] = {"station_implied_max_concurrent_demand": upper, "minimum_available_capacity": minimum,
                       "redundant": minimum >= upper}
    data["metadata"]["resource_constraints_redundant_under_station_capacity"] = all(r["redundant"] for r in details.values())
    data["metadata"]["resource_redundancy_details"] = details

def freeze(source, request):
    from backend.services import v5_service
    if v5_service.is_v5(source): return v5_service.freeze(source,request)
    data = copy.deepcopy(source)
    ids = request.job_ids
    if len(ids) != len(set(ids)):
        raise AppError("DUPLICATE_JOB", "Selected Job IDs must be unique.")
    if not set(ids) <= {j["job_id"] for j in data["jobs"]}:
        raise AppError("UNKNOWN_JOB", "One or more selected jobs do not exist in this dataset.")
    origin = parse_datetime(data["metadata"]["origin_datetime"])
    start, end = parse_datetime(request.horizon_start), parse_datetime(request.horizon_end)
    if end <= start or start < origin:
        raise AppError("INVALID_HORIZON", "Horizon must start at or after the dataset origin and end after its start.")
    if end > parse_datetime(data["metadata"]["horizon_end_datetime"]):
        raise AppError("INVALID_HORIZON", "Horizon exceeds the uploaded dataset calendar.")
    lo, hi = (start-origin).total_seconds()/60, (end-origin).total_seconds()/60
    data["jobs"] = [j for j in data["jobs"] if j["job_id"] in ids]
    for key in ("operation_options", "precedence"):
        data[key] = [r for r in data[key] if r["job_id"] in ids]
    for sid, windows in data["station_windows"].items():
        data["station_windows"][sid] = [
            dict(w, start_min=max(lo, float(w["start_min"])), end_min=min(hi, float(w["end_min"])))
            for w in windows if float(w["end_min"]) > lo and float(w["start_min"]) < hi]
    data["metadata"].update(size=len(ids), horizon_start_min=lo, horizon_end_min=hi,
        horizon_start_datetime=start.isoformat(sep=" "), horizon_end_datetime=end.isoformat(sep=" "),
        selection_method="explicit_user_selection", selected_job_ids=ids)
    resource_proof(data)
    return data
