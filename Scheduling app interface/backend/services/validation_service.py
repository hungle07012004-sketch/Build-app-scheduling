import math
from collections import Counter, defaultdict
from backend.services.instance_service import OPS, parse_datetime
from backend.services.algorithms.common import solver_module

def issue(entity, field, message):
    return {"entity": str(entity), "field": field, "message": message}

def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def validate_instance(data, preflight=False):
    from backend.services import v5_service
    if v5_service.is_v5(data): return v5_service.validate(data)
    errors = []
    try:
        solver_module("benchmark_common").validate_instance_schema(data)
        meta = data["metadata"]
        parse_datetime(meta["origin_datetime"])
        parse_datetime(meta["horizon_end_datetime"])
        if not finite(meta["horizon_end_min"]) or meta["horizon_end_min"] <= 0:
            errors.append(issue("Dataset", "horizon", "Horizon must be positive and finite."))
        stations = {r["station_id"]: r for r in data["station_master"]}
        if len(stations) != len(data["station_master"]):
            errors.append(issue("Dataset", "station_id", "Duplicate station IDs."))
        for sid, st in stations.items():
            if st.get("capacity", 1) != 1:
                errors.append(issue(sid, "capacity", "Existing solvers require unit-capacity workstations."))
        windows = data["station_windows"]
        for sid, rows in windows.items():
            if sid not in stations:
                errors.append(issue(sid, "station", "Window refers to an unknown workstation."))
            seen = set()
            previous_end = -math.inf
            for w in sorted(rows, key=lambda r: r["start_min"]):
                s, e = w["start_min"], w["end_min"]
                for k in ("window_id", "calendar_date", "shift_id"):
                    if not w.get(k):
                        errors.append(issue(sid, k, "Window field is required."))
                if not finite(s) or not finite(e) or e <= s or s < previous_end - 1e-6:
                    errors.append(issue(sid, "window", "Invalid or overlapping working windows."))
                previous_end = e
                if w["window_id"] in seen:
                    errors.append(issue(sid, "window_id", "Duplicate window ID."))
                seen.add(w["window_id"])
        jobs = {j["job_id"]: j for j in data["jobs"]}
        opts = defaultdict(list)
        unique_options = set()
        for r in data["operation_options"]:
            j, op, sid, p = r["job_id"], r["operation_code"], r["station_id"], r["processing_min"]
            if j not in jobs or op not in OPS or sid not in stations:
                errors.append(issue(j, "option", "Unknown job, operation or workstation."))
            if not finite(p) or p <= 0:
                errors.append(issue(j, "processing_min", "Processing time must be positive and finite."))
            key = (j, op, sid)
            if key in unique_options:
                errors.append(issue(j, "option", "Duplicate job/operation/station option."))
            unique_options.add(key)
            if int(r.get("eligible", 1)) == 1:
                opts[j, op].append(r)
        edges = {}
        allowed = set(zip(OPS, OPS[1:]))
        for r in data["precedence"]:
            key = r["job_id"], r["pred"], r["succ"]
            if key in edges or r["job_id"] not in jobs or (r["pred"], r["succ"]) not in allowed:
                errors.append(issue(r["job_id"], "precedence", "Duplicate or unsupported precedence relation."))
            if not finite(r["lag_min"]) or r["lag_min"] < 0:
                errors.append(issue(r["job_id"], "lag", "Lag must be nonnegative and finite."))
            edges[key] = r["lag_min"]
        for j, row in jobs.items():
            for field in ("release_min", "element_ready_min", "loopset_release_min", "due_min"):
                if not finite(row.get(field)):
                    errors.append(issue(j, field, "A finite numeric time is required."))
            if not isinstance(row.get("priority"), int) or row["priority"] < 1:
                errors.append(issue(j, "priority", "Priority must be a positive integer."))
            for pred, succ in allowed:
                if (j,pred,succ) not in edges:
                    errors.append(issue(j, "precedence", f"Missing {pred} -> {succ}."))
            for op in OPS:
                choices = opts[j,op]
                if not choices:
                    errors.append(issue(j, op, "No eligible processing option."))
                elif not any(finite(r["processing_min"]) and any(w["end_min"]-w["start_min"] >= r["processing_min"]
                         for w in windows.get(r["station_id"], [])) for r in choices):
                    errors.append(issue(j, op, "No working window fits the operation."))
        units = data["resource_units_by_type"]
        all_units = [u for values in units.values() for u in values]
        if len(all_units) != len(set(all_units)):
            errors.append(issue("Resources", "units", "Resource unit IDs must be unique."))
        for op, reqs in data["resource_requirements"].items():
            types = set()
            for r in reqs:
                rt, qty = r["resource_type"], r["required_qty"]
                if op not in OPS or rt in types or not isinstance(qty, int) or qty < 1 or qty > len(units.get(rt, [])):
                    errors.append(issue(op, "resources", "Invalid, duplicate or insufficient resource requirement."))
                types.add(rt)
        for r in data["resource_capacity_by_shift"]:
            if not isinstance(r["available_qty"], int) or r["available_qty"] < 0:
                errors.append(issue(r["resource_type"], "available_qty", "Capacity must be a nonnegative integer."))
        if not errors and preflight:
            decoder = solver_module("benchmark_common").FrozenInstanceDecoder(data)
            order = sorted(decoder.job_ids, key=lambda j: (decoder.release[j], decoder.priority[j], j))
            metrics, rows = decoder.decode(order, keep_schedule=True)
            errors.extend(validate_schedule(data, rows, metrics["makespan_min"]))
    except (KeyError, TypeError, ValueError, RuntimeError, OverflowError) as exc:
        errors.append(issue("Dataset", "schema/decoder", str(exc)))
    return errors

def validate_schedule(data, rows, reported_cmax):
    errors = []
    jobs = {j["job_id"]: j for j in data["jobs"]}
    expected = {(j, op) for j in jobs for op in OPS}
    counts = Counter((r.get("Job_ID"), r.get("Operation_Code")) for r in rows)
    if set(counts) != expected or any(n != 1 for n in counts.values()):
        errors.append(issue("Schedule", "operations", "Every required operation must appear exactly once."))
    options = {(r["job_id"],r["operation_code"],r["station_id"]): r["processing_min"]
               for r in data["operation_options"] if int(r.get("eligible",1)) == 1}
    window_map = {(sid,w["window_id"]):w for sid,ws in data["station_windows"].items() for w in ws}
    by_op, busy, resource_busy = {}, defaultdict(list), defaultdict(list)
    eps = 1e-4
    binding = not data["metadata"].get("resource_constraints_redundant_under_station_capacity", False)
    caps = {(r["calendar_date"],r["shift_id"],r["resource_type"]):r["available_qty"]
            for r in data["resource_capacity_by_shift"]}
    for r in rows:
        j, op, sid = r.get("Job_ID"), r.get("Operation_Code"), r.get("Station_ID")
        s, e, p = r.get("Start_Min"), r.get("Completion_Min"), r.get("Processing_Min")
        if not all(finite(x) for x in (s,e,p)) or e <= s:
            errors.append(issue(j, op, "Invalid operation times."))
            continue
        by_op[j,op] = r
        if (j,op,sid) not in options or abs(options[j,op,sid]-p) > eps or abs(e-s-p) > eps:
            errors.append(issue(j, op, "Station eligibility or processing time mismatch."))
        w = window_map.get((sid,r.get("Window_ID")))
        if not w or s < w["start_min"]-eps or e > w["end_min"]+eps:
            errors.append(issue(j, op, "Operation lies outside its assigned working window."))
        if s < data["metadata"].get("horizon_start_min",0)-eps or e > data["metadata"]["horizon_end_min"]+eps:
            errors.append(issue(j, op, "Operation lies outside the scheduling horizon."))
        if j in jobs:
            ready = jobs[j]["release_min"]
            if op == "2B": ready = max(ready, jobs[j]["loopset_release_min"])
            if op == "PRE": ready = max(ready, jobs[j]["element_ready_min"])
            if s < ready-eps:
                errors.append(issue(j, op, "Release/material readiness violated."))
        busy[sid].append((s,e))
        if binding and w:
            assigned = [u for u in str(r.get("Resource_Units","")).split(";") if u]
            required_count = sum(req["required_qty"] for req in data["resource_requirements"].get(op, []))
            if len(assigned) != len(set(assigned)) or len(assigned) != required_count:
                errors.append(issue(j, op, "Invalid resource assignment count."))
            for req in data["resource_requirements"].get(op, []):
                rt = req["resource_type"]
                units = data["resource_units_by_type"].get(rt, [])
                cap = caps.get((w["calendar_date"],w["shift_id"],rt), data.get("base_resource_capacity",{}).get(rt,len(units)))
                if len(set(assigned) & set(units[:cap])) != req["required_qty"]:
                    errors.append(issue(j, op, "Resource availability violated."))
            for unit in assigned:
                resource_busy[unit].append((s,e))
    for r in data["precedence"]:
        a, b = by_op.get((r["job_id"],r["pred"])), by_op.get((r["job_id"],r["succ"]))
        if a and b and b["Start_Min"] < a["Completion_Min"]+r["lag_min"]-eps:
            errors.append(issue(r["job_id"], "precedence", f"{r['pred']} -> {r['succ']} lag violated."))
    for label, groups in (("station",busy), ("resource",resource_busy)):
        for key, intervals in groups.items():
            ordered = sorted(intervals)
            if any(b[0] < a[1]-eps for a,b in zip(ordered,ordered[1:])):
                errors.append(issue(key,label,"Overlapping operations."))
    cmax = max((r["Completion_Min"] for r in by_op.values()), default=None)
    if cmax is None or not finite(reported_cmax) or abs(cmax-reported_cmax) > eps:
        errors.append(issue("Schedule","makespan","Reported makespan differs from recomputed completion time."))
    return errors
