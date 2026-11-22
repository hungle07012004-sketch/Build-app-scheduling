"""Readable Excel export of persisted standardized results, without solver calls."""
from io import BytesIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from backend.services.schedule_service import get_run, validate_saved_selection
from backend.errors import AppError


def filename(run_id):
    date=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")
    return f"AL13_Schedule_{run_id}_{date}.xlsx"


def method_name(request):
    chosen=request.get("algorithms",[])
    if not chosen: return "Automatic"
    if len(chosen)>1: return "Parallel Optimization"
    if chosen[0]=='MIN_TARDY_GROUPED_LNS_V5': return 'Min Tardy - Due-Date Grouping + LNS'
    return "CP-SAT" if chosen[0]=="CP-SAT" else "MLPSO" if chosen[0]=="MLPSO" else "MILP (legacy)" if chosen[0]=="MILP" else "GA"


def shift_label(start, finish):
    """Label factory shifts intersecting [start, finish); availability is validated separately."""
    labels = []
    cursor = start
    while True:
        hour = cursor.hour
        shift, boundary_hour = (1, 14) if 6 <= hour < 14 else (2, 22) if 14 <= hour < 22 else (3, 6)
        labels.append(f"Shift {shift}")
        boundary = cursor.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
        if boundary <= cursor:
            boundary += timedelta(days=1)
        if boundary >= finish:
            return " → ".join(labels)
        cursor = boundary


def export(run_id):
    run=get_run(run_id); result=run["result"]; selected=result.get("selected")
    if not selected or not selected.get("feasible"):
        raise AppError("NO_FEASIBLE_SCHEDULE","No validated schedule is available to export.",status=409)
    validate_saved_selection(run)
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
    if selected['algorithm']=='MIN_TARDY_GROUPED_LNS_V5':
        meta=selected.get('metadata',{})
        for row in [
            ['Objective','Lexicographic (N_tardy, Total Tardiness, Cmax); current dataset deadlines'],
            ['Original due grouping (calendar days)',meta.get('due_group_days')],
            ['Tardy (%)',meta.get('tardy_percent')],['OTD (%)',meta.get('otd_percent')],
            ['Overdue before PRE',meta.get('N_pre_overdue')],
            ['Unique full decoder calls',meta.get('unique_full_decoder_calls')],
            ['Incumbent fallback',bool(meta.get('incumbent_fallback'))],
        ]: summary.append(row)
    sheet=wb.create_sheet("Detailed Schedule")
    if selected.get('metadata',{}).get('deadline_comparison'):
        guard=selected['metadata']
        if guard.get('on_time_reference_policy'):
            summary.append(['On-time policy','Keep validated zero-tardy reference unless search produces a no-worse on-time schedule'])
            summary.append(['Returned schedule origin',guard.get('returned_origin')])
            summary.append(['Search-produced tardy jobs',guard.get('search_produced_metrics',{}).get('tardy_jobs')])
        from pathlib import Path
        import json
        from backend.services.deadline_service import comparison
        data=json.loads(Path(run['instance_path']).read_text(encoding='utf-8'))
        due=comparison(data,selected['operations'])
        summary.append(['Deadline basis','Relaxed to reference final 2C completion; fixed before this run'])
        summary.append(['Reference run',due['source_run_id']])
        summary.append(['Tardy jobs under original deadlines',due['original_tardy_jobs']])
        summary.append(['Tardy jobs under relaxed deadlines',due['adjusted_tardy_jobs']])
        deadlines=wb.create_sheet('Deadline Comparison')
        deadlines.append(['Job ID','Original due (min)','Relaxed due (min)','Final 2C completion (min)','Original tardy','Relaxed tardy'])
        for r in due['jobs']:
            deadlines.append([r['job_id'],r['original_due_min'],r['due_min'],r['completion_min'],r['original_tardy'],r['adjusted_tardy']])
    sheet.append(["Job ID","Product","Operation","Workstation","Start Time","Finish Time","Processing Time (min)","Shift","Status",
                  "Event ID","Event Type","Contributing Job IDs","Resource Units","Window ID"])
    for row_index,o in enumerate(selected["operations"],start=2):
        start, finish = datetime.fromisoformat(o["startTime"]), datetime.fromisoformat(o["finishTime"])
        sheet.append([o["jobId"],o.get("product"),o["operation"],o["workstation"],
            start,finish,o["processingMin"],
            shift_label(start,finish),o["status"],
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
