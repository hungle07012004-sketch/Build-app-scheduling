import { useState, useEffect } from 'react';
import { taskJobIds, type GanttTask, type Job } from '../data';
import { useAppData } from '../AppData';
import { api, post, ApiError, downloadJson, downloadExcel } from '../api';
import { algorithmSettings } from '../algorithmSettings';
import { algorithmName } from '../algorithms';
import ApiNotice from '../components/ApiNotice';
import SchedulingMethod, { schedulingMethodName, type MethodPreview } from '../components/SchedulingMethod';
import StatusChip from '../components/StatusChip';
import GanttChart, { type CalendarWindow } from '../components/GanttChart';
import { Search, CheckSquare, Square, ChevronRight, ArrowLeft, CheckCircle2, Loader2, Download, RotateCcw, Save } from 'lucide-react';
const parameterLabel=(key:string)=>({
  ga_mutation_rate:'Mutation rate (base)',ga_crossover_rate:'Crossover rate',micro_mutation_rate:'Mutation rate (base)',micro_crossover_rate:'Crossover rate',mlpso_mr:'Mutation probability (mr)',mlpso_pcr:'Personal-best crossover (pcr)',mlpso_gcr:'Group-best crossover (gcr)',
  micro_no_improve:'Stop after epochs without improvement (0 = off)',micro_min_epochs:'Minimum epochs before early stop',micro_time_limit:'GA search budget (seconds)',
  runs:'Independent attempts',seed_base:'Random seed (repeatability)',exact_time_limit:'CP-SAT time limit (seconds)',
  exact_workers:'CPU workers (0 = automatic)',micro_population:'Candidate schedules per generation',
  micro_generations:'Maximum search epochs',micro_max_decodes:'Maximum schedule evaluations per run',
  ga_population:'GA candidate schedules',ga_generations:'GA search generations',
  mlpso_population:'MLPSO swarm size',mlpso_iterations:'MLPSO iterations',
  milp_time_limit:'Exact Search time limit (seconds)',target_polish_generations:'Refinement generations after reaching target'
} as Record<string,string>)[key] || key;
function SelectJobsStep({ onNext, initial, busy, settings }: { settings:Record<string,number>; onNext: (ids: Set<string>, start:string, end:string, algorithms:string[]) => void; initial:any; busy:boolean }) {
  const {jobs:JOBS,dataset} = useAppData();
  const matchingInitial=initial?.dataset_id===dataset?.id ? initial : null;
  const [start,setStart] = useState(matchingInitial?.horizon_start || dataset?.origin_datetime?.replace(' ','T').slice(0,16) || '');
  const [end,setEnd] = useState(matchingInitial?.horizon_end || dataset?.horizon_end_datetime?.replace(' ','T').slice(0,16) || '');
  useEffect(()=>{
    if(dataset && initial?.dataset_id!==dataset.id) {
      setStart(dataset.origin_datetime.replace(' ','T').slice(0,16));
      setEnd(dataset.horizon_end_datetime.replace(' ','T').slice(0,16));
    }
  },[dataset?.id]);
  const [priority,setPriority] = useState('All');
  const [product,setProduct] = useState('All');
  const [jobStatus,setJobStatus] = useState('All');
  const [releaseDate,setReleaseDate] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set(initial?.job_ids || []));

  const [algorithmChoice,setAlgorithmChoice] = useState<string>((initial?.algorithms || []).join('|'));
  const available = JOBS;
  const filtered = available.filter(j =>
    (priority==='All'||j.priority===priority) && (product==='All'||j.product===product) && (jobStatus==='All'||j.status===jobStatus) && (!releaseDate||j.releaseTime.startsWith(releaseDate)) && (!search || j.id.toLowerCase().includes(search.toLowerCase()) || j.product.toLowerCase().includes(search.toLowerCase()))
  );
  const allSelected = filtered.length > 0 && filtered.every(j => selected.has(j.id));

  function toggle(id: string) {
    setSelected(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  function toggleAll() {
    setSelected(s => allSelected ? new Set([...s].filter(id => !filtered.find(j => j.id === id))) : new Set([...s, ...filtered.map(j => j.id)]));
  }

  return (
    <div className="flex-1 overflow-y-auto bg-[#F8FAFC]">
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="mb-6">
          <div className="flex items-center gap-2 text-sm text-[#64748B] mb-1">
            <span>Scheduling</span><ChevronRight size={14} /><span className="text-[#2563EB] font-medium">Select Jobs</span>
          </div>
          <h1 className="text-xl font-bold text-[#0F172A]">Select Jobs to Schedule</h1>
          <p className="text-sm text-[#64748B] mt-0.5">Choose unscheduled jobs to include in this scheduling run.</p>
        </div>

        {/* Info bar */}
        <div className="flex items-center gap-4 mb-4 p-3 bg-[#EFF6FF] rounded-lg border border-blue-100">
          <CheckCircle2 size={16} className="text-[#2563EB]" />
          <span className="text-sm text-[#1E3A8A] font-medium">Selected Jobs: <strong>{selected.size}</strong></span>
          <span className="text-sm text-[#64748B]">·</span>
          <span className="text-sm text-[#64748B]">Available: {available.length} jobs</span>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-sm">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#94A3B8]" />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search jobs…"
              className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB]"
            />
          </div>
          <div>
            <label className="block text-xs text-[#64748B] mb-1">Scheduling Start</label>
            <input aria-label="Scheduling Start" type="datetime-local" value={start} onChange={e=>setStart(e.target.value)} className="px-3 py-2 text-sm rounded-lg border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB]" />
          </div>
          <div>
            <label className="block text-xs text-[#64748B] mb-1">Scheduling End</label>
            <input aria-label="Scheduling End" type="datetime-local" value={end} onChange={e=>setEnd(e.target.value)} className="px-3 py-2 text-sm rounded-lg border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB]" />
          </div>
        </div>

        <div className="flex flex-wrap gap-3 mb-4">
          <select aria-label="Priority filter" value={priority} onChange={e=>setPriority(e.target.value)} className="border border-slate-200 rounded-lg p-2 text-sm">{['All','High','Medium','Low'].map(v=><option key={v}>{v}</option>)}</select>
          <select aria-label="Product filter" value={product} onChange={e=>setProduct(e.target.value)} className="border border-slate-200 rounded-lg p-2 text-sm">{['All',...new Set(JOBS.map(j=>j.product))].map(v=><option key={v}>{v}</option>)}</select>
          <select aria-label="Status filter" value={jobStatus} onChange={e=>setJobStatus(e.target.value)} className="border border-slate-200 rounded-lg p-2 text-sm">{['All','Unscheduled','Scheduled','In Progress','Completed','Delayed'].map(v=><option key={v}>{v}</option>)}</select>
          <label className="text-xs text-slate-500">Release date <input type="date" value={releaseDate} onChange={e=>setReleaseDate(e.target.value)} className="border border-slate-200 rounded-lg p-2"/></label>
        </div>
        <div className="mb-4"><SchedulingMethod jobCount={selected.size} v5={dataset?.schema_version==='AL13_BENCHMARK_V5'} value={algorithmChoice?algorithmChoice.split('|'):[]} onChange={values=>setAlgorithmChoice(values.join('|'))} disabled={busy} previewRequest={{dataset_id:dataset?.id,job_ids:[...selected],horizon_start:start,horizon_end:end,algorithms:algorithmChoice?algorithmChoice.split('|'):[],settings}}/></div>
        {/* Table */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden mb-6">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 bg-[#F8FAFC]">
                <th className="px-4 py-3 w-10">
                  <button onClick={toggleAll}>
                    {allSelected ? <CheckSquare size={16} className="text-[#2563EB]" /> : <Square size={16} className="text-slate-400" />}
                  </button>
                </th>
                {['Job ID', 'Product', 'Qty', 'Priority', 'Material Ready', 'Due Date', 'Status'].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.map(job => (
                <tr
                  key={job.id}
                  className={`cursor-pointer transition-colors ${selected.has(job.id) ? 'bg-[#EFF6FF]' : 'hover:bg-slate-50'}`}
                  onClick={() => toggle(job.id)}
                >
                  <td className="px-4 py-3">
                    {selected.has(job.id)
                      ? <CheckSquare size={16} className="text-[#2563EB]" />
                      : <Square size={16} className="text-slate-300" />}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs font-semibold text-[#2563EB]">{job.id}</td>
                  <td className="px-4 py-3 text-[#0F172A] font-medium">{job.product}</td>
                  <td className="px-4 py-3 text-[#64748B]">{job.batchQty}</td>
                  <td className="px-4 py-3"><StatusChip status={job.priority} dot={false} /></td>
                  <td className="px-4 py-3 text-xs text-[#64748B]">{job.materialReady}</td>
                  <td className="px-4 py-3 text-xs text-[#64748B]">{job.dueDate.split(' ')[0]}</td>
                  <td className="px-4 py-3"><StatusChip status={job.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="flex justify-end">
          <button
            disabled={selected.size === 0 || !start || !end || busy}
            onClick={() => onNext(selected,start,end,algorithmChoice?algorithmChoice.split('|'):[])}
            className="flex items-center gap-2 px-6 py-2.5 bg-[#2563EB] hover:bg-[#1D4ED8] disabled:bg-slate-200 disabled:text-slate-400 text-white text-sm font-semibold rounded-lg transition-all"
          >
            Validate Data
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Step 1: Configuration ────────────────────────────────────────────────

interface Issue { entity:string; field:string; message:string; }
interface Validation { valid:boolean; issues:Issue[]; size:number; classification:string; algorithms:string[]; schema_version?:string; }
interface AlgorithmResult { algorithm:string; feasible:boolean; proven_optimal:boolean; status:string; makespan_min:number|null; total_tardiness_min:number|null; total_flow_time_min:number|null; runtime_sec:number|null; operations:GanttTask[]; message?:string; validation_issues?:Issue[]; metadata?: {status_name?:string; search_stop_reason?:string; completed_runs?:number; runs?:number; working_calendar_policy?:string}; }
interface RunResult { selection_mode?:string; routing_reason?:string; request?: {dataset_id:string; horizon_start:string; horizon_end:string}; run_id:string; status:string; algorithms:AlgorithmResult[]; selected:AlgorithmResult|null; selection_reason:string; accepted_at?:string; applied_at?:string; }
const number = (value: number|null|undefined) => value == null ? '\u2014' : value.toLocaleString(undefined,{maximumFractionDigits:2});

function Comparison({result}: {result:RunResult}) {
  return <div className="p-6">
    <div className="bg-white rounded-xl border border-slate-200 overflow-auto mb-4"><table className="w-full text-sm">
      <thead className="bg-[#F8FAFC]"><tr>{['Algorithm','Makespan (min)','Tardiness (min)','Flow time (min)','Runtime (s)','Status'].map(h=><th className="text-left px-4 py-3 text-xs font-semibold text-[#64748B]" key={h}>{h}</th>)}</tr></thead>
      <tbody>{result.algorithms?.map(r=><tr className={result.selected?.algorithm===r.algorithm?'bg-[#EFF6FF]':'border-t border-slate-100'} key={r.algorithm}>
        <td className="p-4 font-semibold">{algorithmName(r.algorithm)}</td><td className="p-4">{number(r.makespan_min)}</td><td className="p-4">{number(r.total_tardiness_min)}</td><td className="p-4">{number(r.total_flow_time_min)}</td><td className="p-4">{number(r.runtime_sec)}</td>
        <td className="p-4"><div>{r.status}{result.selected?.algorithm===r.algorithm?' · Selected':''}</div>{r.message && <p className="text-xs text-red-600 mt-1">{r.message}</p>}{r.validation_issues?.slice(0,5).map((e,i)=><p key={i} className="text-xs text-red-600">{e.entity}: {e.message}</p>)}</td>
      </tr>)}</tbody></table></div>
    <div className="bg-[#EFF6FF] border border-blue-200 rounded-xl p-4 text-sm text-[#1E3A8A]">{result.selected && <strong>{algorithmName(result.selected.algorithm)} selected. </strong>}{result.selection_reason}</div>
  </div>;
}

function ResultStep({result,onRunAgain,onAdjust,onDone,onUpdated,onUseLatest}: {result:RunResult;onRunAgain:()=>void;onAdjust:()=>void;onDone:()=>void;onUpdated:()=>Promise<void>;onUseLatest:()=>void}) {
  const {dataset}=useAppData();
  const [tab,setTab] = useState<'gantt'|'details'|'comparison'>(result.selected?'gantt':'comparison');
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [showSaveModal,setShowSaveModal] = useState(false);
  const [tableJob,setTableJob] = useState('All');
  const [context,setContext] = useState<{jobs:Job[];calendar:CalendarWindow[]}>({jobs:[],calendar:[]});
  const [contextError,setContextError] = useState('');
  useEffect(()=>{
    let active=true;
    setContext({jobs:[],calendar:[]});setContextError('');
    const id=result.request?.dataset_id;
    if(id) {
      const query='?dataset_id='+encodeURIComponent(id);
      api<Job[]>('/jobs'+query)
        .then(jobs=>{if(active)setContext(old=>({...old,jobs}));})
        .catch(()=>{if(active)setContextError('Original job details are unavailable. Saved operation times remain available.');});
    }
    api<CalendarWindow[]>('/calendar?run_id='+encodeURIComponent(result.run_id))
      .then(calendar=>{if(active)setContext(old=>({...old,calendar}));})
      .catch(()=>{if(active)setContextError('Saved calendar details could not be loaded. Saved operation times remain available.');});
    return()=>{active=false;};
  },[result.request?.dataset_id,result.run_id]);
  const selected = result.selected;
  const operations = selected?.operations || [];
  async function action(kind:'accept'|'apply') {
    setBusy(true);setError('');
    try { await post('/schedules/'+result.run_id+'/'+kind); await onUpdated();setShowSaveModal(false); if(kind==='apply') onDone(); }
    catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  const summary = [
    {label:'Selected Method',value:algorithmName(selected?.algorithm),color:'#2563EB'},
    {label:'Makespan',value:number(selected?.makespan_min)+' min',color:'#10B981'},
    {label:'Total Tardiness',value:number(selected?.total_tardiness_min)+' min',color:'#0F172A'},
    {label:'Computation Time',value:number(selected?.runtime_sec)+' s',color:'#0F172A'},
    {label:'Scheduled Jobs',value:String(new Set(operations.flatMap(taskJobIds)).size),color:'#0F172A'},
    {label:'Status',value:result.applied_at?'Applied':result.accepted_at?'Accepted':selected?.status || result.status,color:'#0F172A'},
  ];
  return <div className="flex-1 min-h-0 overflow-hidden flex flex-col bg-[#F8FAFC]">
    <div data-testid="result-summary" className="shrink-0 px-4 py-3 bg-white border-b border-slate-200 max-h-[45vh] overflow-y-auto">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div><h1 className="text-lg font-bold text-[#0F172A]">Scheduling Result</h1><p className="text-xs text-[#64748B] mt-0.5">Run {result.run_id.slice(0,12)}</p></div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button onClick={onAdjust} className="px-2 py-1.5 text-xs border border-slate-200 rounded-lg">Adjust</button>
          <button title={dataset && result.request && dataset.id!==result.request.dataset_id?'Review a new schedule using the current calendar':'Repeat the saved job selection and date range using current calendar rules'} disabled={busy} onClick={dataset && result.request && dataset.id!==result.request.dataset_id?onUseLatest:onRunAgain} className="flex items-center gap-1.5 px-2 py-1.5 text-xs border border-slate-200 rounded-lg"><RotateCcw size={14}/>{dataset && result.request && dataset.id!==result.request.dataset_id?'Schedule with latest calendar':'Run Again'}</button>
          <button onClick={()=>downloadJson(result,'schedule-'+result.run_id+'.json')} className="flex items-center gap-1.5 px-2 py-1.5 text-xs border border-slate-200 rounded-lg"><Download size={14}/>JSON</button>
          <button disabled={!selected || busy} onClick={async()=>{setBusy(true);setError('');try{await downloadExcel(result.run_id);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}} className="flex items-center gap-1.5 px-2 py-1.5 text-xs border border-slate-200 rounded-lg disabled:opacity-50"><Download size={14}/>Export Results to Excel</button>
          {selected && !result.accepted_at && <button disabled={busy} onClick={()=>setShowSaveModal(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-[#2563EB] text-white rounded-lg font-medium"><Save size={14}/>Accept & Save</button>}
          {result.accepted_at && <button disabled={busy || !!result.applied_at} onClick={()=>void action('apply')} className="px-3 py-1.5 text-xs bg-[#2563EB] text-white rounded-lg disabled:opacity-50">{result.applied_at?'Applied':'Apply to Production'}</button>}
        </div>
      </div>
      <div className="grid grid-cols-3 lg:grid-cols-6 gap-2">{summary.map(s=><div key={s.label} className="bg-[#F8FAFC] rounded-lg px-2 py-1.5 border border-slate-100"><div className="text-sm font-bold leading-5 break-words" style={{color:s.color}}>{s.value}</div><div className="text-xs text-[#64748B] mt-0.5">{s.label}</div></div>)}</div>
      <details data-testid="result-details" className="mt-2 text-xs text-slate-600">
        <summary className="cursor-pointer w-fit">Show Details{dataset && result.request && dataset.id!==result.request.dataset_id && <span className="ml-2 text-amber-700">Calendar notice</span>}</summary>
        <div className="mt-2">
          <p className="mb-2">Run {result.run_id} &middot; {result.selection_reason}</p>
          {selected && <p className="mb-2">Schedule status: {selected.status} &middot; Run status: {result.status}</p>}
      {result.routing_reason&&<p className="text-xs text-slate-500 mb-2">{result.selection_mode==='Automatic'?'Automatic selection':'Manual selection'}: {selected?algorithmName(selected.algorithm):'No feasible schedule'}. {result.routing_reason}</p>}
      {result.request&&<p className="text-xs text-slate-600 mb-2">Calendar used: {result.request.horizon_start.replace('T',' ')} → {result.request.horizon_end.replace('T',' ')}. These are the dates of the saved run.</p>}
      {dataset && result.request && dataset.id!==result.request.dataset_id && <div className="mb-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
        The current dataset has a calendar from {dataset.origin_datetime} to {dataset.horizon_end_datetime}. This saved run uses an older dataset.
        <button disabled={busy} onClick={onUseLatest} className="ml-3 font-semibold underline">Use latest calendar</button>
      </div>}
      {selected?.metadata?.status_name&&<p className="text-xs text-slate-500 mb-2">Solver status: {selected.metadata.status_name}. {selected.proven_optimal?'Optimality proven.':'Optimality not proven.'}</p>}
      {selected?.metadata?.search_stop_reason && <p className="text-xs text-slate-500 mb-3">{selected.metadata.search_stop_reason==='time_limit'?'Search budget reached. Showing the best validated schedule found.':selected.metadata.search_stop_reason==='no_improvement'?'Stopped early after no further improvement. Showing the best validated schedule found.':'Search completed within the configured iteration/evaluation limits.'} Attempts completed: {selected.metadata.completed_runs} / {selected.metadata.runs}.</p>}

        </div>
      </details>
    </div>
    <ApiNotice error={error}/>
    <div className="flex shrink-0 flex-wrap items-center gap-1 px-4 pt-1 bg-white border-b border-slate-200">
      {(['gantt','details','comparison'] as const).map(t=><button key={t} onClick={()=>setTab(t)} className={'px-4 py-2.5 text-sm font-medium border-b-2 -mb-px '+(tab===t?'border-[#2563EB] text-[#2563EB]':'border-transparent text-[#64748B]')}>{t==='gantt'?'Gantt View':t==='details'?'Schedule Table':'Algorithm Comparison'}</button>)}
    </div>
    {contextError && <p className="px-6 py-2 text-xs text-amber-700">{contextError}</p>}
    <div className={'flex-1 min-h-0 '+(tab==='gantt'?'overflow-hidden':'overflow-auto')}>
      {tab==='comparison' && <Comparison result={result}/>}
      {tab==='gantt' && (selected ? <GanttChart horizonStart={result.request?.horizon_start} horizonEnd={result.request?.horizon_end} calendarPolicy={selected.metadata?.working_calendar_policy} tasks={operations} jobs={context.jobs} calendar={context.calendar} onViewJob={id=>{setTableJob(id);setTab('details');}}/> : <p className="p-6 text-slate-500">No valid feasible schedule to display.</p>)}
      {tab==='details' && <div className="p-6 overflow-auto"><div className="flex items-center gap-3 mb-3 text-xs text-slate-500"><label>Job <select aria-label="Table Job filter" className="border border-slate-200 rounded px-2 py-1 bg-white" value={tableJob} onChange={e=>setTableJob(e.target.value)}><option>All</option>{[...new Set(operations.flatMap(taskJobIds))].sort().map(id=><option key={id}>{id}</option>)}</select></label><span>{operations.filter(t=>tableJob==='All'||taskJobIds(t).includes(tableJob)).length} / {operations.length} operations</span></div><div className="bg-white rounded-xl border border-slate-200 overflow-auto"><table className="w-full text-sm">
        <thead className="bg-[#F8FAFC]"><tr>{['Job ID','Operation','Workstation','Start Time','Finish Time','Processing Time','Status'].map(h=><th className="text-left px-4 py-3 text-xs font-semibold text-[#64748B]" key={h}>{h}</th>)}</tr></thead>
        <tbody>{operations.filter(t=>tableJob==='All'||taskJobIds(t).includes(tableJob)).map(t=><tr className="border-t border-slate-100" key={t.eventId || t.jobId+t.operation}>
          <td className="p-3 font-mono text-xs text-[#2563EB]">{t.jobId}</td><td className="p-3">{t.operation}</td><td className="p-3">{t.workstation}</td><td className="p-3 text-xs whitespace-nowrap">{t.startTime}</td><td className="p-3 text-xs whitespace-nowrap">{t.finishTime}</td><td className="p-3">{number(t.processingMin)} min</td><td className="p-3"><StatusChip status={t.status}/></td>
        </tr>)}</tbody></table></div></div>}
    </div>
    {showSaveModal && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"><div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 p-6">
      <h3 className="text-lg font-semibold mb-4">Accept this production schedule?</h3>
      <p className="text-sm text-[#64748B] mb-5">{selected?.algorithm} · Makespan {number(selected?.makespan_min)} min · {new Set(operations.flatMap(taskJobIds)).size} jobs</p>
      <p className="text-xs text-slate-500 mb-5">Acceptance saves this schedule for review and production application.</p>
      <div className="flex gap-2 justify-end"><button disabled={busy} onClick={()=>setShowSaveModal(false)} className="px-4 py-2 text-sm">Cancel</button><button disabled={busy} onClick={()=>void action('accept')} className="px-5 py-2 text-sm bg-[#2563EB] text-white rounded-lg">{busy?'Saving...':'Accept & Save'}</button></div>
    </div></div>}
  </div>;
}

export default function ScheduleFlow({onDone}: {onDone:()=>void}) {
  const {dataset,refresh} = useAppData();
  const [request,setRequest] = useState<any>(null);
  const [validation,setValidation] = useState<Validation|null>(null);
  const [runId,setRunId] = useState<string|null>(()=>sessionStorage.getItem('al13-run'));
  const [status,setStatus] = useState<any>(null);
  const [result,setResult] = useState<RunResult|null>(null);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [tick,setTick] = useState(Date.now());
  const [preview,setPreview] = useState<{data:MethodPreview;key:string}|null>(null);
  const [settings,setSettings] = useState<any>(()=>JSON.parse(localStorage.getItem('al13-settings') || '{}'));
  const previewRequest=request?{...request,settings}:undefined;
  const previewCurrent=preview && preview.key===JSON.stringify(previewRequest)?preview.data:null;
  const activeAlgorithms=previewCurrent?.algorithms||preview?.data.algorithms||validation?.algorithms||[];
  const activeFields=[...new Map(activeAlgorithms.flatMap(a=>algorithmSettings[a]||[]))];
  async function validate(ids:Set<string>,start:string,end:string, algorithms:string[]) {
    if (!dataset) return;
    const next = {dataset_id:dataset.id,job_ids:[...ids],horizon_start:start,horizon_end:end,algorithms,settings};
    setBusy(true);setError('');
    try {setValidation(await post('/schedules/validate',next));setRequest(next);}
    catch(e) {const err=e as ApiError; setError(err.message+(err.details?' '+JSON.stringify(err.details):''));} finally {setBusy(false);}
  }
  async function run(rerun=false) {
    setBusy(true);setError('');
    try {
      const response = await post(rerun && runId ? '/schedules/'+runId+'/rerun':'/schedules/run',rerun?undefined:{...request,settings});
      setRunId(response.run_id);sessionStorage.setItem('al13-run',response.run_id);setResult(null);setStatus(null);
    } catch(e) {const err=e as ApiError;setError(err.message+(err.details?' '+JSON.stringify(err.details):''));} finally {setBusy(false);}
  }
  async function loadResult() {
    if (runId) {setResult(await api('/schedules/'+runId+'/result')); await refresh();}
  }
  useEffect(()=>{
    if(!runId) return;
    let cancelled=false;
    let timer:ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const current=await api('/schedules/'+runId+'/status');
        if(cancelled) return;
        setStatus(current);setError('');
        if(['completed','failed','interrupted'].includes(current.status)) {
          const final=await api('/schedules/'+runId+'/result');
          if(!cancelled) {setResult(final);setRequest(final.request);}
        } else timer=setTimeout(poll,1500);
      } catch(e) {if(!cancelled){setError((e as Error).message);timer=setTimeout(poll,5000);}}
    }
    void poll();return()=>{cancelled=true;clearTimeout(timer);};
  },[runId]);
  useEffect(()=>{const timer=setInterval(()=>setTick(Date.now()),1000);return()=>clearInterval(timer);},[]);
  function adjust() {setRunId(null);setResult(null);setValidation(null);sessionStorage.removeItem('al13-run');}
  function useLatestCalendar() {
    if(!dataset) return;
    setRequest({...request,dataset_id:dataset.id,horizon_start:dataset.origin_datetime.replace(' ','T').slice(0,16),horizon_end:dataset.horizon_end_datetime.replace(' ','T').slice(0,16)});
    adjust();
  }
  if(result) return <><ApiNotice error={error}/><ResultStep result={result} onRunAgain={()=>void run(true)} onAdjust={adjust} onUseLatest={useLatestCalendar} onDone={onDone} onUpdated={loadResult}/></>;
  if(runId) return <div className="flex-1 flex flex-col items-center justify-center bg-[#F8FAFC]"><ApiNotice error={error}/>
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm w-full max-w-lg p-8">
      <Loader2 size={28} className="text-[#2563EB] animate-spin mx-auto mb-4"/><h2 className="text-lg font-bold text-center">Optimizing Production Schedule</h2>
      <p className="text-sm text-center text-[#64748B] my-3">{status?.status || 'Loading run...'} · Elapsed {status?.created_at?Math.max(0,Math.floor((tick-new Date(status.created_at).getTime())/1000)):0}s</p>
      <div className="grid gap-4">{status?.algorithms?.map((a:any)=><div key={a.algorithm} className="bg-slate-50 rounded-xl p-4 border border-slate-100"><strong>{algorithmName(a.algorithm)}</strong><span className="float-right text-sm text-[#64748B]">{a.status}</span>{a.progress && <p className="text-sm mt-2">{a.progress.scope}: {a.progress.iteration}/{a.progress.limit} · Best makespan {number(a.progress.best_makespan_min)} min</p>}{a.result?.makespan_min != null && <p className="text-sm mt-2">Makespan: {number(a.result.makespan_min)} min</p>}</div>)}</div>
      <p className="text-xs text-slate-400 mt-4">Results appear when each solver finishes. You can leave this page and return to the run.</p>
    </div></div>;
  if(validation) return <div className="flex-1 overflow-auto bg-[#F8FAFC]"><div className="max-w-2xl mx-auto px-6 py-6">
    <button onClick={()=>setValidation(null)} className="flex gap-2 text-sm text-[#64748B] mb-4"><ArrowLeft size={15}/>Back to selected jobs</button>
    <h1 className="text-xl font-bold mb-4">{validation.valid?'Data Validation Passed':'Data Validation Failed'}</h1>
    <ApiNotice error={error}/>
    <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
      <p>{validation.size} jobs · {validation.classification}</p><p className="font-semibold text-[#2563EB]">{schedulingMethodName(request?.algorithms)}</p>
      <p className="text-xs text-slate-500">Runs: {previewCurrent?activeAlgorithms.map(algorithmName).join(' + '):'Checking selection...'}</p>
      <SchedulingMethod jobCount={validation.size} v5={validation.schema_version==='AL13_BENCHMARK_V5'} value={request?.algorithms || []} onChange={algorithms=>void validate(new Set(request.job_ids),request.horizon_start,request.horizon_end,algorithms)} disabled={busy} previewRequest={previewRequest} onPreview={(data,key)=>setPreview({data,key})}/>
      {validation.issues.map((e,i)=><p key={i} className="text-sm text-red-600">{e.entity} · {e.field}: {e.message}</p>)}
      {validation.schema_version==='AL13_BENCHMARK_V5' && <section aria-label="Optimization Time" className="rounded-lg border border-slate-200 p-3">
        <h3 className="text-sm font-semibold mb-2">Optimization Time</h3>
        <div className="flex flex-wrap gap-2">{[60,120,300,600,900].map(seconds=><button key={seconds} type="button" aria-pressed={activeAlgorithms.every(a=>Number(settings[a==='CP-SAT'?'exact_time_limit':'micro_time_limit']??120)===seconds)} onClick={()=>setSettings({...settings,exact_time_limit:seconds,micro_time_limit:seconds})} className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm aria-pressed:bg-blue-50 aria-pressed:border-blue-400">{seconds} sec{seconds>=600?` (${seconds/60} min)`:""}</button>)}</div>
        <div className="flex flex-wrap gap-3 mt-3">{activeAlgorithms.map(a=>{const key=a==='CP-SAT'?'exact_time_limit':'micro_time_limit';return <label key={a} className="text-xs text-slate-500">{a==='CP-SAT'?'CP-SAT':'GA'} custom seconds<input aria-label={a+' time budget'} type="number" min={1} max={7200} className="block border border-slate-200 rounded p-2 mt-1" value={settings[key]??120} onChange={e=>setSettings({...settings,[key]:Number(e.target.value)})}/></label>;})}</div>
        <p className="text-xs text-slate-500 mt-2">Computation time is separate from the production calendar. Search may finish early. Setup, the current evaluation and final schedule validation can add time. GA shares this budget across all attempts.</p>
      </section>}
      <details><summary className="text-sm text-[#64748B] cursor-pointer">Advanced settings</summary><div className="grid grid-cols-2 gap-3 mt-3">
        {activeFields.map(([key,value])=><label className="text-xs text-[#64748B]" key={key}>{parameterLabel(String(key))}<input aria-label={String(key)} type="number" step="any" className="w-full border border-slate-200 rounded-lg p-2" value={settings[String(key)]??value} onChange={e=>setSettings({...settings,[String(key)]:Number(e.target.value)})}/></label>)}
      </div></details>
      <button aria-label="Run Optimization" disabled={!validation.valid || busy || !previewCurrent?.valid || !activeAlgorithms.length} onClick={()=>void run()} className="px-5 py-2.5 bg-[#2563EB] text-white rounded-lg text-sm font-semibold disabled:opacity-50">{busy?'Starting...':activeAlgorithms.length?'Run '+activeAlgorithms.map(algorithmName).join(' + '):'Checking selection...'}</button>
    </div></div></div>;
  return <div className="flex-1 flex flex-col overflow-hidden"><ApiNotice error={error}/>{busy && <p className="px-6 py-2 text-sm text-[#2563EB]">Validating selected jobs and horizon...</p>}<SelectJobsStep onNext={(ids,start,end,algorithms)=>void validate(ids,start,end,algorithms)} initial={request} busy={busy} settings={settings}/></div>;
}
