"""Readable Excel export of persisted standardized results, without solver calls."""
from io import BytesIO
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from backend.services.schedule_service import get_run
from backend.errors import AppError


def filename(run_id):
    date=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")
    return f"AL13_Schedule_{run_id}_{date}.xlsx"


def method_name(request):
    chosen=request.get("algorithms",[])
    if not chosen: return "Automatic"
    if len(chosen)>1: return "Parallel Optimization"
    return "CP-SAT" if chosen[0]=="CP-SAT" else "MLPSO" if chosen[0]=="MLPSO" else "MILP (legacy)" if chosen[0]=="MILP" else "GA"


def export(run_id):
    run=get_run(run_id); result=run["result"]; selected=result.get("selected")
    if not selected or not selected.get("feasible"):
        raise AppError("NO_FEASIBLE_SCHEDULE","No validated schedule is available to export.",status=409)
    request=run.get("request",{}); settings=request.get("settings",{})
    algorithms=result.get("algorithms",[]) or [selected]
    limits=[]
    for a in algorithms:
        key={"CP-SAT":"exact_time_limit","MILP":"milp_time_limit"}.get(a["algorithm"])
        if key: limits.append(f'{a["algorithm"]}: {settings.get(key,600)} seconds')
        elif a["algorithm"]=="SS Micro-GA Fast" and settings.get("micro_time_limit"):
            limits.append(f'SS Micro-GA Fast: {settings["micro_time_limit"]} seconds (shared search budget)')
        else: limits.append(f'{a["algorithm"]}: no wall-clock limit (iteration/decode budget)')
    jobs=set(request.get("job_ids",[])) or {j for o in selected["operations"] for j in o.get("jobIds",[o["jobId"]])}
    # Shift lookup uses the immutable run snapshot, never the current edited input.
    windows={}
    try:
        snapshot=json.loads(Path(run["instance_path"]).read_text(encoding="utf-8"))
        for station,rows in snapshot.get("station_windows",snapshot.get("resource_windows",{})).items():
            for w in rows: windows[station,w["window_id"]]=w.get("shift_id")
    except (KeyError,OSError,ValueError):
        pass
    wb=Workbook();summary=wb.active;summary.title="Schedule Summary"
    for row in [
        ["Field","Value"],["Run ID",run_id],["Selected scheduling method",method_name(request)],
        ["Selected algorithm",selected["algorithm"]],["Number of jobs",len(jobs)],
        ["Optimization time limit","; ".join(limits)],
        ["Makespan (min)",selected.get("makespan_min")],["Total tardiness (min)",selected.get("total_tardiness_min")],
        ["Total flow time (min)",selected.get("total_flow_time_min")],["Tardy jobs",selected.get("tardy_jobs")],
        ["Search stop reason",selected.get("metadata",{}).get("search_stop_reason","Not recorded")],
        ["Runtime (s)",selected.get("runtime_sec")],["Run status",run.get("status",selected.get("status"))],
        ["Algorithm routing reason",result.get("routing_reason","Not recorded")],["Selection reason",result.get("selection_reason")],["Solution status",selected.get("status")],
        ["Proven optimal",bool(selected.get("proven_optimal"))],["Accepted at",run.get("accepted_at")],
        ["Applied at",run.get("applied_at")],["Operation count",len(selected["operations"])],
        ["Time convention","Factory local time; durations in minutes"]
    ]: summary.append(row)
    sheet=wb.create_sheet("Detailed Schedule")
    sheet.append(["Job ID","Product","Operation","Workstation","Start Time","Finish Time","Processing Time (min)","Shift","Status",
                  "Event ID","Event Type","Contributing Job IDs","Resource Units","Window ID"])
    for row_index,o in enumerate(selected["operations"],start=2):
        sheet.append([o["jobId"],o.get("product"),o["operation"],o["workstation"],
            datetime.fromisoformat(o["startTime"]),datetime.fromisoformat(o["finishTime"]),o["processingMin"],
            o.get("shift") or windows.get((o["workstation"],o.get("windowId"))) or "Unavailable",o["status"],
            o.get("eventId"),o.get("eventType"),";".join(o.get("jobIds",[o["jobId"]])),o.get("resourceUnits"),o.get("windowId")])
        for col in (5,6): sheet.cell(row_index,col).number_format="yyyy-mm-dd hh:mm:ss.000"
    comparison=wb.create_sheet("Algorithm Comparison")
    comparison.append(["Algorithm","Feasible","Makespan (min)","Total Tardiness (min)","Total Flow Time (min)","Runtime (s)","Status","Selection","Proven optimal"])
    for a in algorithms:
        comparison.append([a["algorithm"],a.get("feasible",False),a.get("makespan_min"),a.get("total_tardiness_min"),
            a.get("total_flow_time_min"),a.get("runtime_sec"),a.get("status"),
            "Selected" if a["algorithm"]==selected["algorithm"] else "Not Selected",a.get("proven_optimal",False)])
    for ws in wb:
        ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
        for cell in ws[1]:
            cell.font=Font(bold=True,color="FFFFFF");cell.fill=PatternFill("solid",fgColor="2563EB")
        for row in ws:
            for cell in row:
                if isinstance(cell.value,str): cell.data_type="s"
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width=min(65,max(16,max(len(str(c.value or "")) for c in column)+2))
    output=BytesIO();wb.save(output)
    return output.getvalue()
