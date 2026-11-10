"""Read-only saved-schedule sensitivity. No production imports or solver calls.

Run from repository root with --database and a NEW --output directory.
The source SQLite must be checkpointed (no nonempty WAL); otherwise stop.
All writes, including the byte-copy database snapshot, stay under --output.
"""
from __future__ import annotations
import argparse
import copy
import csv
import hashlib
import json
import math
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCENARIOS = [('Baseline', 0), ('S1', 1), ('S2', 3), ('S3', 5), ('S4', 7)]
TOL = 1e-5  # minutes, for saved KPI/timestamp float comparison only


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda: file.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Missing/invalid number: '+label)
    return float(value)


def local(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None: raise ValueError('Expected factory-local timestamp without timezone')
    return dt


def completions(data, result, request):
    """V5 final contributor completion is the MAX of its exact-10 batches.

    Multiple batches per job are expected, not duplicate jobs. Duplicate events
    or repeated contributors inside one batch are rejected, never de-duplicated.
    """
    jobs = data['jobs']; ids = [j['job_id'] for j in jobs]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate job IDs in frozen input')
    if Counter(ids) != Counter(request['job_ids']): raise ValueError('Request/input job mapping differs')
    origin = local(data['metadata']['origin_datetime'])
    v5 = data['metadata'].get('schema_version') == 'AL13_BENCHMARK_V5'
    completion = {}; seen = set(); event_ids = set(); final_count = Counter()
    for job in jobs:
        finite(job['release_min'], 'release_min'); due = finite(job['due_min'], 'due_min')
        if job.get('due_datetime'):
            if abs((local(job['due_datetime'])-origin).total_seconds()/60-due)>TOL:
                raise ValueError('due_datetime/due_min disagree: '+job['job_id'])
        if job.get('release_datetime'):
            if abs((local(job['release_datetime'])-origin).total_seconds()/60-job['release_min'])>TOL:
                raise ValueError('release datetime/minute disagree: '+job['job_id'])
    operations = result.get('operations')
    if not operations: raise ValueError('Feasible result without operations')
    for op in operations:
        signature = json.dumps(op, sort_keys=True, separators=(',', ':'))
        if signature in seen: raise ValueError('Duplicate saved operation')
        seen.add(signature)
        if op.get('eventId'):
            if op['eventId'] in event_ids: raise ValueError('Duplicate eventId')
            event_ids.add(op['eventId'])
        end = finite(op['endMin'], 'endMin')
        if abs((local(op['finishTime'])-origin).total_seconds()/60-end)>TOL:
            raise ValueError('finishTime/endMin disagree')
        contributors = op.get('jobIds') or [op['jobId']]
        if len(contributors)!=len(set(contributors)) or not set(contributors)<=set(ids):
            raise ValueError('Duplicate/unknown job contributor')
        if ';'.join(contributors) != op['jobId']: raise ValueError('jobIds/jobId disagree')
        final = op.get('eventType')=='2C_BATCH' if v5 else op['operation']=='2C'
        if final:
            for job in contributors:
                completion[job] = max(completion.get(job, -math.inf), end)
                final_count[job] += 1
    if set(completion)!=set(ids): raise ValueError('Missing final completion for selected job')
    if not v5 and any(n!=1 for n in final_count.values()): raise ValueError('Duplicate legacy final operation')
    for job in jobs:
        if completion[job['job_id']]<job['release_min']: raise ValueError('Completion before release')
    return completion


def metrics(jobs, completion, epsilon):
    tardiness = [max(0., completion[j['job_id']]-j['due_min']) for j in jobs]
    count = sum(t>epsilon for t in tardiness); total = sum(tardiness); n = len(jobs)
    # Use exactly the same epsilon classification as the App's standardizer.
    return dict(total_jobs=n, on_time_jobs=n-count, tardy_jobs=count,
        otd_percent=100*(n-count)/n, total_tardiness_min=total,
        average_tardiness_all_jobs_min=total/n,
        average_tardiness_tardy_jobs_min=sum(t for t in tardiness if t>epsilon)/count if count else None,
        maximum_tardiness_min=max(tardiness), cmax_min=max(completion.values()))


def csv_write(path, rows):
    if not rows: return
    with path.open('x', encoding='utf-8-sig', newline='') as file:
        writer=csv.DictWriter(file, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def audit_text():
    return '''## A. Current System Audit

- Workbook upload đang dùng: xem `manifest.json` (dataset, Excel, frozen input và hash từng run). V5 `backend/services/v5_service.py:28` đọc workbook, `backend/solver_v5/generate_benchmark_instances.py:301` đọc `Due_DateTime`, dòng 318 đổi sang phút kể từ `origin_datetime`, dòng 320 giữ timestamp gốc. Không đổi timezone hay chuẩn hóa về đầu/cuối ngày.
- `backend/services/input_service.py:93` dựng timestamp origin + minutes, dòng 97 truyền `dueDate` tới frontend. `src/pages/JobsPage.tsx:148` và `src/pages/ScheduleFlow.tsx:83` hiện chỉ ngày qua `split(' ')[0]`; backend vẫn giữ giờ/phút/giây.
- Frozen instance lưu `due_min` và `due_datetime`; request/run, algorithm results và selected result được lưu bởi `backend/services/optimization_service.py:61`, `:140`, `:146`. Mỗi run dùng đúng frozen input của run đó, không lấy due date hiện tại thay cho lịch sử.
- V5 `backend/services/v5_service.py:186` tính `sum(max(0,Cj-dj))`; dòng 188 đếm `Cj>dj+1e-6`. Legacy `backend/services/result_standardizer.py:38`/`:44` dùng cùng công thức với ngưỡng 1e-9. Completion V5 là lần hoàn tất batch 2C cuối cùng có job đóng góp; không nhầm một job có nhiều batch với nhiều jobs.
- V5 GA: `backend/solver_v5/benchmark_common.py:686` chỉ Cmax; Micro-GA gọi fitness chung (`solve_ss_micro_ga_fast_instance.py:70`, `:839`) và khởi tạo qua base GA (`:269`). Base GA có seed EDD ở `solve_improved_ga_instance.py:201`.
- V5 MLPSO: seed EDD tại `backend/solver_v5/solve_mlpso_v3_instance.py:359`, objective Cmax tại `:1223`. Due date tác động diagnostic và có thể tác động đường khởi tạo nếu thay đổi tương đối giữa jobs; không là hard deadline. Gia hạn đồng đều theo phút giữ nguyên thứ tự EDD, nhưng lượt này không chạy lại để khẳng định kết quả search.
- V5 CP-SAT: `backend/solver_v5/solve_milp_instance.py:580` có `model.Minimize(cmax)`; `:684`/`:685` dùng due cho KPI. Không có hard deadline theo due trong model hiện đọc. Due có thể tác động artifact warm-start từ heuristic bên ngoài, không đổi objective Cmax.
- Legacy Improved GA: `code/solve_improved_ga_ig_v3_earlystop_instance.py:230`, `:1484` objective lexicographic Cmax → tardiness → flow. Due có thể tác động seed EDD trong `code/solve_ga_instance.py:185` và tie-break fitness. Legacy MLPSO chỉ Cmax (`code/solve_mlpso_instance.py:843`); legacy MILP chỉ Cmax (`code/solve_milp_instance.py:298`). Không đồng nhất mọi solver thành cùng objective legacy.
- Calendar dùng đúng `resource_windows`/`station_windows` của frozen run. Không regenerate calendar, không apply lại policy hiện tại. Các lịch lịch sử khác input/calendar không được coi là benchmark so sánh thuật toán công bằng.
- Thời gian sản xuất là factory-local naive theo `backend/services/instance_service.py:9`; diễn giải Asia/Ho_Chi_Minh (UTC+07) theo ngữ cảnh App, không chuyển UTC. `created_at` của DB có thể là UTC nhưng không dùng để tính tardiness. Một ngày gia hạn = 1440 phút = 86400 giây, gồm cả cuối tuần; không phải ngày làm việc. Mọi timestamp xuất giữ chính xác, không làm tròn đầu/cuối ngày.
- Phân tích KPI sau lịch cố định không chứng minh toàn bộ physical feasibility lại. Chỉ nhận các kết quả App đánh dấu feasible và kiểm tra mapping, hash, thời gian và KPI. Workbook provenance hiện ghi nguồn/quy tắc gán due date chưa có bằng chứng đầy đủ; không coi due date là cam kết nhà máy đã xác minh.
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args(); root=Path(__file__).resolve().parents[2]
    base=Path(__file__).resolve().parent; out=args.output.resolve()
    if not out.is_relative_to(base) or out==base: raise ValueError('Output must be a NEW child of the experiment folder')
    if out.exists(): raise FileExistsError('Refusing to overwrite an existing experiment')
    db=args.database.resolve()
    wal=Path(str(db)+'-wal')
    if wal.exists() and wal.stat().st_size: raise RuntimeError('Live SQLite WAL found. Stop: need a consistent approved read-only snapshot; do not checkpoint production.')
    out.mkdir(parents=True)
    protected={str(p.relative_to(root)):sha(p) for folder in ('backend','src','code') for p in (root/folder).rglob('*')
               if p.is_file() and p.suffix in ('.py','.tsx','.ts','.css') and '__pycache__' not in p.parts and 'storage' not in p.parts}
    db_hash=sha(db); snapshot=out/'saved_schedules_snapshot.sqlite3';shutil.copyfile(db,snapshot)
    if sha(db)!=db_hash or sha(snapshot)!=db_hash or (wal.exists() and wal.stat().st_size):
        raise RuntimeError('Source DB changed during copy. No conclusions; rerun into a new folder after activity stops.')
    conn=sqlite3.connect(snapshot.as_uri()+'?mode=ro&immutable=1',uri=True);conn.row_factory=sqlite3.Row
    runs=list(conn.execute('select * from runs order by created_at,id'));datasets={r['id']:dict(r) for r in conn.execute('select * from datasets')}
    manifest=dict(created_utc=datetime.now(timezone.utc).isoformat(),database=str(db),database_sha256=db_hash,
        source_database_read_method='byte copy, stable hashes, no nonempty WAL; queries only on immutable copy',
        schedules=[],skipped=[],baseline_validation=[],errors=[],datasets=[],protected_code_files=len(protected))
    prepared=[]; file_hashes={}; summaries=[]; details=[]; recovered=[]
    for dataset in datasets.values():
        p=Path(dataset['path']);entry={k:dataset[k] for k in ('id','filename','path','sha256','created_at')}
        if p.is_file(): entry['actual_sha256']=sha(p);file_hashes[str(p)]=entry['actual_sha256']
        manifest['datasets'].append(entry)
    for run in runs:
        result=json.loads(run['result']);request=json.loads(run['request']);rid=run['id']
        algorithms=result.get('algorithms') or ([result['selected']] if result.get('selected') else [])
        if not algorithms: manifest['skipped'].append(dict(run_id=rid,reason='No saved algorithm result',status=run['status']));continue
        seen_algorithms=set()
        for algo in algorithms:
            name=algo.get('algorithm');key=dict(run_id=rid,algorithm=name)
            if not algo.get('feasible'):
                manifest['skipped'].append({**key,'reason':'No saved feasible schedule','status':algo.get('status')});continue
            try:
                if name in seen_algorithms: raise ValueError('Duplicate solver result in run')
                seen_algorithms.add(name)
                path=Path(run['instance_path']);digest=sha(path);file_hashes[str(path)]=digest
                if digest!=run['instance_sha256']: raise ValueError('Frozen input SHA-256 differs from saved run')
                data=json.loads(path.read_text(encoding='utf-8'))
                if algo.get('instance_sha256') and algo['instance_sha256']!=digest: raise ValueError('Algorithm input hash differs')
                before=json.dumps(data,sort_keys=True);ops_before=json.dumps(algo['operations'],sort_keys=True)
                completion=completions(data,algo,request)
                v5=data['metadata'].get('schema_version')=='AL13_BENCHMARK_V5';eps=1e-6 if v5 else 1e-9
                baseline=metrics(data['jobs'],completion,eps)
                for field,saved in [('cmax_min','makespan_min'),('tardy_jobs','tardy_jobs'),('total_tardiness_min','total_tardiness_min')]:
                    expected=finite(algo.get(saved),saved)
                    if abs(baseline[field]-expected)>TOL:raise ValueError(f'Baseline mismatch {saved}: calculated={baseline[field]}, saved={expected}')
                flow=sum(completion[j['job_id']]-j['release_min'] for j in data['jobs'])
                if abs(flow-finite(algo.get('total_flow_time_min'),'total_flow_time_min'))>TOL:raise ValueError('Baseline flow mismatch')
                if result.get('selected',{} ) and result['selected'].get('algorithm')==name:
                    if result['selected']!=algo:raise ValueError('Selected/algorithm saved result differ')
                record={**key,'created_at':run['created_at'],'dataset_id':run['dataset_id'],
                    'schema':data['metadata'].get('schema_version','legacy'),'instance_path':str(path),'input_sha256':digest,
                    'origin_datetime':data['metadata']['origin_datetime'],
                    'calendar_sha256':hashlib.sha256(json.dumps({k:v for k,v in data.items() if 'window' in k or 'calendar' in k},sort_keys=True).encode()).hexdigest(),
                    'working_calendar_policy':data['metadata'].get('working_calendar_policy'),
                    'selected':bool(result.get('selected') and result['selected'].get('algorithm')==name),
                    'app_tardy_jobs':algo['tardy_jobs'],'app_total_tardiness_min':algo['total_tardiness_min'],
                    'app_cmax_min':algo['makespan_min'],'baseline_status':'PASS',**baseline}
                manifest['baseline_validation'].append(record)
                manifest['schedules'].append({k:record[k] for k in ('run_id','algorithm','instance_path','input_sha256','calendar_sha256','working_calendar_policy','schema','selected')})
                prepared.append((record,data,algo,completion,eps,before,ops_before))
            except (ValueError,KeyError,TypeError,OSError) as exc:
                manifest['errors'].append({**key,'error':str(exc)})
    conn.close()
    # Global fail-closed: do not publish scenario conclusions if any baseline is invalid.
    if not manifest['errors']:
        for record,data,algo,completion,eps,before,ops_before in prepared:
            origin=local(data['metadata']['origin_datetime']);prev=None;newly_seen=set()
            for scenario,days in SCENARIOS:
                trial=copy.deepcopy(data)
                for job in trial['jobs']:
                    job['due_min']+=days*1440
                    if job.get('due_datetime'): job['due_datetime']=(local(job['due_datetime'])+timedelta(days=days)).isoformat(sep=' ')
                kpi=metrics(trial['jobs'],completion,eps)
                if abs(kpi['cmax_min']-record['cmax_min'])>TOL:raise AssertionError('Cmax changed')
                if prev and (kpi['tardy_jobs']>prev['tardy_jobs'] or kpi['total_tardiness_min']>prev['total_tardiness_min']+TOL):raise AssertionError('Non-monotonic results')
                restored=copy.deepcopy(trial)
                for old,new in zip(data['jobs'],restored['jobs']):
                    new['due_min']=old['due_min']
                    if 'due_datetime' in old:new['due_datetime']=old['due_datetime']
                if json.dumps(restored,sort_keys=True)!=before:raise AssertionError('Non-due input changed')
                summaries.append({k:record[k] for k in ('run_id','algorithm','selected','schema','input_sha256')}|dict(scenario=scenario,extension_days=days,**kpi))
                switched=[]
                for job,updated in zip(data['jobs'],trial['jobs']):
                    jid=job['job_id'];c=completion[jid];original=max(0.,c-job['due_min']);now=max(0.,c-updated['due_min'])
                    recovered_here=original>eps and now<=eps
                    if recovered_here:switched.append(jid)
                    details.append(dict(run_id=record['run_id'],algorithm=record['algorithm'],scenario=scenario,extension_days=days,
                        job_id=jid,release_min=job['release_min'],release_datetime=(origin+timedelta(minutes=job['release_min'])).isoformat(sep=' '),
                        completion_min=c,completion_datetime=(origin+timedelta(minutes=c)).isoformat(sep=' '),
                        original_due_min=job['due_min'],original_due_datetime=(origin+timedelta(minutes=job['due_min'])).isoformat(sep=' '),
                        scenario_due_min=updated['due_min'],scenario_due_datetime=(origin+timedelta(minutes=updated['due_min'])).isoformat(sep=' '),
                        original_tardiness_min=original,scenario_tardiness_min=now,tardy=now>eps,
                        recovered_vs_baseline=recovered_here))
                newly=sorted(set(switched)-newly_seen);newly_seen.update(switched)
                recovered.append((record['run_id'],record['algorithm'],scenario,days,newly,switched))
                prev=kpi
            if json.dumps(data,sort_keys=True)!=before or json.dumps(algo['operations'],sort_keys=True)!=ops_before:raise AssertionError('Original in-memory input/schedule changed')
    manifest['protected_code_changed']=[p for p,h in protected.items() if sha(root/p)!=h]
    manifest['input_files_changed']=[p for p,h in file_hashes.items() if sha(p)!=h]
    manifest['database_unchanged']=sha(db)==db_hash and not (wal.exists() and wal.stat().st_size)
    manifest['checks_passed']=not manifest['errors'] and not manifest['protected_code_changed'] and not manifest['input_files_changed'] and manifest['database_unchanged']
    manifest['scenario_rows']=len(summaries);manifest['job_rows']=len(details)
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    csv_write(out/'baseline_validation.csv',manifest['baseline_validation'])
    csv_write(out/'due_date_sensitivity_results.csv',summaries)
    csv_write(out/'job_level_comparison.csv',details)
    report=['# Due Date Extension – Simulation & Impact Analysis\n\nTEST ONLY. Không chạy solver. Không thay đổi production.\n',audit_text(),
        '## B. Baseline Validation\n',
        f"Snapshot: `{snapshot.name}`; SHA-256 `{db_hash}`. {len(runs)} runs; {len(prepared)} feasible solver schedules kiểm tra; {len(manifest['skipped'])} entries không có lịch khả thi được loại, không gán KPI 0.\n",
        'Đối chiếu từng run/solver trong `baseline_validation.csv`: Cmax, tardy jobs, total tardiness và flow; mapping request/frozen jobs, completion, timestamp-minute consistency, input hash, duplicate events/jobs/contributors. Không tự điền dữ liệu thiếu.\n']
    if manifest['errors']:
        report+=['**STOP: baseline không hợp lệ; không xuất kết luận scenario.**\n',json.dumps(manifest['errors'],ensure_ascii=False,indent=2)]
    else:
        report+=['**PASS:** tất cả baseline khớp App trong tolerance 1e-5 phút; count khớp chính xác.\n',
            '## C. Scenario Comparison\n',
            'KPI đầy đủ ở `due_date_sensitivity_results.csv`: tổng jobs, on-time, tardy, OTD, tổng/trung bình/max tardiness và Cmax. Average-all chia tất cả jobs; average-tardy chỉ chia jobs trễ theo epsilon của App, để trống khi không có job trễ. Không làm tròn trong CSV; bảng dưới chỉ định dạng để đọc.\n',
            '| Run ID | Solver | Jobs | Baseline | +1d | +3d | +5d | +7d | OTD baseline → +7d | Cmax phút |\n|---|---|---:|---:|---:|---:|---:|---:|---|---:|\n']
        for record,*_ in prepared:
            rows=[r for r in summaries if r['run_id']==record['run_id'] and r['algorithm']==record['algorithm']]
            report.append(f"| {record['run_id']} | {record['algorithm']} | {record['total_jobs']} | "+' | '.join(str(r['tardy_jobs']) for r in rows)+f" | {rows[0]['otd_percent']:.2f}% → {rows[-1]['otd_percent']:.2f}% | {record['cmax_min']:.6f} |\n")
        report+=['\n## D. Job-Level Analysis\nCác ID dưới đây là **mới chuyển sang đúng hạn so với scenario ngắn hơn trước đó**. CSV đánh dấu cả recovered_vs_baseline; không nhầm hai cách đếm.\n']
        for rid,name,scenario,days,newly,switched in recovered:
            if days: report.append(f"- `{rid}` / {name} / +{days}d: mới {len(newly)} [{', '.join(newly) or '—'}]; tổng hồi phục so baseline {len(switched)}.\n")
        report+=['\n## E. Recommendation\n',
            'Chọn mức gia hạn theo từng lịch và cam kết khách hàng; không có một mức chung bảo đảm OTD cho mọi lịch. Đây là cải thiện KPI do thay deadline, **không phải thuật toán chạy tốt hơn, tăng công suất hay hoàn thành sớm hơn**. Cmax/release/processing/calendar/machine capacity/schedule không đổi trong mọi scenario. Các run lịch sử có thể trùng input hoặc chạy nhiều lần: không coi tổng jobs qua các run là số đơn hàng độc lập; không so chất lượng solver khi input khác nhau.\n',
            'Với mỗi lịch: mức ngắn nhất đạt 100% OTD trong các mốc thử (nếu có) được liệt kê dưới; đây là lựa chọn sensitivity, không phải đề nghị tự đổi hợp đồng. Với lịch đã đúng hạn toàn bộ, không cần gia hạn.\n']
        for record,*_ in prepared:
            rows=[r for r in summaries if r['run_id']==record['run_id'] and r['algorithm']==record['algorithm']]
            zero=next((r for r in rows if r['tardy_jobs']==0),None)
            drops=[(rows[i-1]['tardy_jobs']-rows[i]['tardy_jobs'],rows[i]['extension_days']) for i in range(1,5)]
            maximum=max(x[0] for x in drops)
            report.append(f"- `{record['run_id']}` / {record['algorithm']}: "+(f"mốc ngắn nhất hết trễ: +{zero['extension_days']}d" if zero else 'chưa hết trễ dù +7d')+f"; giảm count mạnh nhất giữa hai mốc: {maximum} jobs, tại mốc "+', '.join(f'+{d}d' for n,d in drops if n==maximum)+'.\n')
        report+=['\n### Nếu được phê duyệt triển khai sau này\n',
            'Không cần sửa solver để làm chế độ sensitivity trên lịch cố định. Đề xuất thêm endpoint phân tích riêng và tham số extension_days, không mutate frozen run: `backend/models/schemas.py` (request riêng), `backend/api/scheduling.py` (endpoint), một service sensitivity mới dùng completion/KPI hiện có, `src/api.ts` và `src/pages/ScheduleFlow.tsx` hoặc trang kết quả riêng (điều khiển và bảng); `backend/services/excel_service.py` nếu cần xuất báo cáo riêng. Không sửa workbook importer/calendar/decoder/objective. Nếu lưu scenario vào DB thì cần thiết kế/migration riêng được duyệt; thử nghiệm này không cần DB migration.\n',
            'Nếu mục tiêu là giảm trễ bằng lịch sản xuất tốt hơn, Due Date Extension không thay thế tối ưu tardiness, kiểm tra công suất hay xác minh due date thực tế. Đổi objective/seed khi re-optimize là phạm vi khác, cần phê duyệt riêng. Không triển khai các thay đổi này trong lượt TEST ONLY.\n']
    report+=['\n## Bảo toàn và giới hạn\n',f"Code đổi hash: {manifest['protected_code_changed']}; nguồn input/Excel đổi hash: {manifest['input_files_changed']}; DB giữ nguyên hash/no-WAL: {manifest['database_unchanged']}.\n",
        'Không claim lịch historical tuân thủ weekly shutdown ngoài calendar đã lưu. Báo cáo chỉ bao phủ snapshot đã ghi và có thể không gồm run phát sinh sau snapshot. Nếu DB thay đổi đồng thời, không chứng nhận bảo toàn byte identity; manifest sẽ ghi FAIL. Không có kết luận cải thiện chất lượng solver từ thử nghiệm này.\n']
    (out/'due_date_sensitivity_report.md').write_text(''.join(report),encoding='utf-8')
    print(json.dumps(dict(output=str(out),schedules=len(prepared),errors=manifest['errors'],checks_passed=manifest['checks_passed'],scenario_rows=len(summaries),job_rows=len(details)),ensure_ascii=False))
    return 0 if manifest['checks_passed'] else 2


if __name__=='__main__':raise SystemExit(main())
