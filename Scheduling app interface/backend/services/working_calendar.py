"""Weekly availability correction for newly built V5 instances only."""
from datetime import datetime, timedelta

POLICY = "MON_SAT_SUN00_MON06_V1"


def apply_to_new_instance(data):
    """Intersect existing windows with Monday 06:00 through Sunday 00:00.

    The caller owns this new instance (never a persisted historical snapshot).
    Subtraction only: existing breaks, downtime and staffing are never filled in.
    Numeric and display timestamps are kept identical after horizon clipping.
    """
    meta = data["metadata"]
    origin = datetime.fromisoformat(meta["origin_datetime"])
    lo = float(meta.get("horizon_start_min", 0))
    hi = float(meta["horizon_end_min"])
    first = origin + timedelta(minutes=lo)
    monday = first.replace(hour=6, minute=0, second=0, microsecond=0) - timedelta(days=first.weekday())
    allowed = []
    while monday < origin + timedelta(minutes=hi):
        sunday = monday.replace(hour=0) + timedelta(days=6)
        a = max(lo, (monday-origin).total_seconds()/60)
        b = min(hi, (sunday-origin).total_seconds()/60)
        if a < b:
            allowed.append((a, b))
        monday += timedelta(days=7)
    for resource, windows in data["resource_windows"].items():
        corrected = []
        for w in windows:
            pieces = [(max(float(w["start_min"]), a), min(float(w["end_min"]), b)) for a, b in allowed]
            pieces = [(a, b) for a, b in pieces if a < b]
            for i, (a, b) in enumerate(pieces):
                start, end = origin + timedelta(minutes=a), origin + timedelta(minutes=b)
                corrected.append(dict(w,
                    window_id=w["window_id"] if len(pieces)==1 else f'{w["window_id"]}__WEEK{i+1}',
                    start_min=a, end_min=b,
                    start_datetime=start.isoformat(sep=" "), end_datetime=end.isoformat(sep=" "),
                    calendar_date=start.date().isoformat()))
        data["resource_windows"][resource] = corrected
    meta["working_calendar_policy"] = POLICY
    return data
