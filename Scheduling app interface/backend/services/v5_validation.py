"""Application-boundary checks for the fixed V5 model; no scheduling logic."""
import math
from collections import Counter, defaultdict

from backend.solver_v5.benchmark_common import SETUP_RESOURCES

ROUTE = ("2B", "PRE", "FINAL", "LASER", "SCALE", "OIL", "SEAL", "VISUAL")
EDGES = dict.fromkeys(zip(ROUTE, ROUTE[1:]), "FS")
EDGES["PRE", "FINAL"] = "SS_FIRST_BELT"


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def input_issues(data):
    issues = []

    def error(entity, field, message):
        issues.append(dict(entity=str(entity), field=field, message=message))

    jobs = {j["job_id"] for j in data["jobs"]}
    seen = Counter()
    for index, edge in enumerate(data["precedence"]):
        field = f"precedence[{index}]"
        j, pred, succ = (edge.get(k) for k in ("job_id", "pred", "succ"))
        if j not in jobs:
            error(j, field + ".job_id", "Unknown job in precedence.")
        if (pred, succ) not in EDGES:
            error(j, field, f"Unsupported or cyclic precedence: {pred} -> {succ}. Only the V5 route is supported.")
        elif edge.get("relation_type") != EDGES[pred, succ]:
            error(j, field + ".relation_type", f"{pred} -> {succ} requires {EDGES[pred, succ]}.")
        if not finite(edge.get("lag_min")) or edge["lag_min"] < 0:
            error(j, field + ".lag_min", "Precedence lag must be finite and nonnegative.")
        seen[j, pred, succ] += 1
        if seen[j, pred, succ] > 1:
            error(j, field, f"Duplicate precedence: {pred} -> {succ}.")
    for j in sorted(jobs):
        for pred, succ in EDGES:
            if not seen[j, pred, succ]:
                error(j, "precedence", f"Missing mandatory precedence: {pred} -> {succ}.")

    products = {j["product_code"] for j in data["jobs"]}
    matrix = data["setup_matrix"]
    for product in sorted(products):
        for successor in sorted(products):
            if product not in matrix or successor not in matrix[product]:
                error(product, f"setup_matrix.{product}.{successor}", "Setup time is required for this product transition.")
    for product, row in matrix.items():
        for successor, duration in row.items():
            if not finite(duration) or duration < 0:
                error(product, f"setup_matrix.{product}.{successor}", "Setup time must be finite and nonnegative; zero is allowed.")
    duration = data["batching"].get("processing_min_per_batch")
    if not finite(duration) or duration <= 0:
        error("2C", "batching.processing_min_per_batch", "2C batch processing duration must be finite and strictly positive.")
    return issues


def expected_resources(data, kind, operation):
    if kind == "OIL_CYCLE":
        if operation != "OIL":
            raise ValueError("OIL_CYCLE Operation_Code must be OIL.")
        return ("WS_OIL", "OP_2A(load)", "OP_PACK(unload)")
    if kind == "SETUP":
        if operation not in (None, "SETUP"):
            raise ValueError("SETUP event has an invalid Operation_Code.")
        return tuple(data.get("setup_resources", SETUP_RESOURCES))
    if kind == "2C_BATCH":
        if operation not in (None, "2C_BATCH"):
            raise ValueError("2C_BATCH event has an invalid Operation_Code.")
        return tuple(data["resource_by_operation"]["2C_BATCH"])
    if kind == "JOB_OP" and operation in ROUTE and operation != "OIL":
        return tuple(data["resource_by_operation"][operation])
    raise ValueError(f"Unsupported event/operation: {kind}/{operation}.")


def check_resources(value, expected, label):
    # Counter also rejects duplicates; phase-qualified OIL resources are intentional.
    if not isinstance(value, str) or Counter(value.split(";")) != Counter(expected):
        raise ValueError(f"{label}: expected {';'.join(expected)}, got {value!r}.")


def check_raw_assignment(data, row):
    expected = expected_resources(data, row["Event_Type"], row.get("Operation_Code"))
    label = f"{row.get('Job_ID')}/{row.get('Operation_Code', row['Event_Type'])}"
    # Validate every representation supplied, including contradictory redundant fields.
    if "Resources" in row:
        check_resources(row["Resources"], expected, label + ".Resources")
    if "Station_ID" in row or "Resource_Units" in row or "Resources" not in row:
        check_resources(row.get("Station_ID"), [r for r in expected if r.startswith("WS_")], label + ".Station_ID")
        check_resources(row.get("Resource_Units"), [r for r in expected if not r.startswith("WS_")], label + ".Resource_Units")


def check_precedence(data, operations):
    """Recheck every accepted edge, including aggregate OIL and PRE/FINAL streaming."""
    by_op = defaultdict(list)
    for op in operations:
        if op["eventType"] in ("JOB_OP", "OIL_CYCLE"):
            by_op[op["jobId"], op["operation"]].append(op)
    for edge in data["precedence"]:
        j, pred, succ = edge["job_id"], edge["pred"], edge["succ"]
        a, b = by_op[j, pred], by_op[j, succ]
        if not a or not b:
            raise ValueError(f"{j}.precedence: missing events for {pred} -> {succ}.")
        start = min(o["startMin"] for o in a)
        finish = max(o["endMin"] for o in a)
        ready = start if edge["relation_type"] == "SS_FIRST_BELT" else finish
        if min(o["startMin"] for o in b) + .005 < ready + edge["lag_min"]:
            raise ValueError(f"{j}.precedence: {pred} -> {succ} lag violated.")
        if edge["relation_type"] == "SS_FIRST_BELT" and max(o["endMin"] for o in b) + .005 < finish:
            raise ValueError(f"{j}.precedence: FINAL completes before PRE.")


def saved_result_issues(data, result):
    """Recheck QA-01..05 at Accept/Excel without replaying or mutating saved runs.

    Saved standardized events omit some raw phase/composition fields. The full
    physical validator remains at the raw-result boundary; this guard checks
    the constraints and assignments that are retained in the saved format.
    """
    errors = input_issues(data)
    if errors:
        return errors
    try:
        operations = result.get("operations") or []
        if not operations:
            raise ValueError("No saved operations to validate.")
        for op in operations:
            expected = expected_resources(data, op["eventType"], op["operation"])
            check_resources(op["workstation"], [r for r in expected if r.startswith("WS_")], f"{op['jobId']}.workstation")
            check_resources(op["resourceUnits"], expected, f"{op['jobId']}.resourceUnits")
            if not all(finite(op.get(k)) for k in ("startMin", "endMin", "processingMin")):
                raise ValueError(f"{op['jobId']}: non-finite saved event times.")
            if op["endMin"] < op["startMin"] or abs(op["endMin"] - op["startMin"] - op["processingMin"]) > .005:
                raise ValueError(f"{op['jobId']}: invalid saved event duration.")
        check_precedence(data, operations)
    except (KeyError, ValueError, TypeError) as exc:
        errors.append(dict(entity="V5 schedule", field="physical_validation", message=str(exc)))
    return errors
