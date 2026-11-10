import { useState, useEffect } from 'react';
import { taskJobIds, type GanttTask, type Job } from '../data';
import { useAppData } from '../AppData';
import { api, post, ApiError, downloadJson, downloadExcel } from '../api';
import { algorithmSettings } from '../algorithmSettings';
import { algorithmName } from '../algorithms';
import { PageHeading, WorkflowSteps, Panel, MetricCard, metric } from '../components/aps';
import { Clock, Layers, Timer, AlertCircle } from 'lucide-react';
import ApiNotice from '../components/ApiNotice';
import SchedulingMethod, { schedulingMethodName, type MethodPreview } from '../components/SchedulingMethod';
import StatusChip from '../components/StatusChip';
import OperationTable from '../components/OperationTable';
import GanttChart, { type CalendarWindow } from '../components/GanttChart';
import { Search, ChevronRight, ArrowLeft, Loader2, Download, RotateCcw, Save } from 'lucide-react';
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

  const [jobPage,setJobPage]=useState(0);
  const [sort,setSort]=useState('id');
  const sorted=[...filtered].sort((a,b)=>sort==='dueDate'?a.dueDate.localeCompare(b.dueDate):sort==='product'?a.product.localeCompare(b.product)||a.id.localeCompare(b.id):a.id.localeCompare(b.id,undefined,{numeric:true}));
  const currentPage=Math.min(jobPage,Math.max(0,Math.ceil(sorted.length/20)-1));
  useEffect(()=>setJobPage(0),[search,priority,product,jobStatus,releaseDate,sort]);
  const duration=(new Date(end).getTime()-new Date(start).getTime())/86400000;
  return <div className="flex-1 min-w-0 overflow-y-auto"><div className="aps-page">
    <PageHeading title="Scheduling Studio" description="Configure jobs, planning dates and optimization settings to build a production schedule."/>
    <WorkflowSteps current={0}/>
    <div className="aps-studio-grid">
      <section className="aps-panel">
        <header className="aps-panel-heading"><div><h2>Job Selection</h2><p>Choose jobs from the current dataset. Existing saved runs remain unchanged.</p></div><span className="bg-blue-50 text-blue-700 px-2 py-1 rounded text-xs">Total: {available.length} jobs</span></header>
        <div className="p-4 space-y-3">
          <div className="flex flex-wrap gap-2"><label className="relative flex-1 min-w-40"><Search size={15} className="absolute left-3 top-3 text-slate-400"/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search jobs…" className="w-full pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-lg"/></label><select aria-label="Sort jobs" className="border border-slate-200 rounded-lg text-xs px-2" value={sort} onChange={e=>setSort(e.target.value)}><option value="id">Job ID</option><option value="product">Product</option><option value="dueDate">Due date</option></select></div>
          <div className="flex flex-wrap gap-2">
            <select aria-label="Priority filter" value={priority} onChange={e=>setPriority(e.target.value)} className="border border-slate-200 rounded p-1.5 text-xs">{['All','High','Medium','Low'].map(v=><option key={v} value={v}>{v==='All'?'All priorities':v}</option>)}</select>
            <select aria-label="Product filter" value={product} onChange={e=>setProduct(e.target.value)} className="border border-slate-200 rounded p-1.5 text-xs">{['All',...new Set(JOBS.map(j=>j.product))].map(v=><option key={v} value={v}>{v==='All'?'All products':v}</option>)}</select>
            <select aria-label="Status filter" value={jobStatus} onChange={e=>setJobStatus(e.target.value)} className="border border-slate-200 rounded p-1.5 text-xs">{['All','Unscheduled','Scheduled','In Progress','Completed','Delayed'].map(v=><option key={v} value={v}>{v==='All'?'All statuses':v}</option>)}</select>
            <label className="text-xs text-slate-500 flex items-center gap-1">Release <input aria-label="Release date" type="date" value={releaseDate} onChange={e=>setReleaseDate(e.target.value)} className="border border-slate-200 rounded p-1"/></label>
          </div>
          <div className="flex items-center justify-between gap-2 text-xs"><span className="text-blue-700 font-medium">Selected Jobs: <strong>{selected.size}</strong> / {available.length}</span><div className="flex gap-3"><button onClick={toggleAll} className="text-blue-600">{allSelected?'Deselect filtered':'Select all filtered'}</button><button onClick={()=>setSelected(new Set())} className="text-slate-500">Clear selection</button></div></div>
        </div>
        <div className="aps-studio-table"><table className="aps-table">
          <thead><tr><th><input type="checkbox" aria-label="Select all filtered jobs" checked={allSelected} onChange={toggleAll} className="accent-blue-600"/></th>{['Job ID','Product','Qty','Priority','Material Ready','Due Date','Status'].map(h=><th key={h}>{h}</th>)}</tr></thead>
          <tbody>{sorted.slice(currentPage*20,currentPage*20+20).map(job=><tr key={job.id} className={selected.has(job.id)?'bg-blue-50/70 cursor-pointer':'cursor-pointer'} onClick={()=>toggle(job.id)}>
            <td><input type="checkbox" aria-label={'Select '+job.id} checked={selected.has(job.id)} onChange={()=>toggle(job.id)} onClick={e=>e.stopPropagation()} className="accent-blue-600"/></td>
            <td className="font-mono font-semibold text-blue-600">{job.id}</td><td>{job.product}</td><td>{job.batchQty}</td><td><StatusChip status={job.priority} dot={false}/></td><td className="text-[11px] whitespace-nowrap text-slate-500">{job.materialReady}</td><td className="whitespace-nowrap">{job.dueDate.split(' ')[0]}</td><td><StatusChip status={job.status}/></td>
          </tr>)}{!sorted.length&&<tr><td colSpan={8} className="text-center text-slate-500">No jobs match these filters.</td></tr>}</tbody>
        </table></div>
        <footer className="px-4 py-3 flex justify-between text-xs text-slate-500 border-t border-slate-200"><span>{sorted.length} matching jobs · Page {currentPage+1}</span><div className="flex gap-3"><button disabled={!currentPage} onClick={()=>setJobPage(currentPage-1)}>Previous</button><button disabled={(currentPage+1)*20>=sorted.length} onClick={()=>setJobPage(currentPage+1)}>Next</button></div></footer>
      </section>
      <div className="space-y-4">
        <Panel title="Planning Horizon" subtitle="Scheduling dates are independent of solver computation time.">
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-1 gap-3">{[['Scheduling Start',start,setStart],['Scheduling End',end,setEnd]].map(([label,value,setter])=><label key={label as string} className="text-xs text-slate-600">{label as string}<input aria-label={label as string} type="datetime-local" value={value as string} onChange={e=>(setter as (v:string)=>void)(e.target.value)} className="block mt-1.5 w-full p-2 border border-slate-200 rounded-md bg-white"/></label>)}</div>
          <p className="mt-3 bg-blue-50 rounded-md p-3 text-xs text-blue-800">Horizon duration: <strong>{Number.isFinite(duration)&&duration>0?metric(duration)+' days':'Select a valid date range'}</strong></p>
        </Panel>
        <Panel title="Scheduling Objective"><p className="text-sm font-semibold text-slate-800">Minimize Makespan (Cmax)</p><p className="text-[11px] text-slate-500 mt-2">MIN_CMAX_FINAL_2C · Fixed AL13 V5 objective. Tardiness and flow time are diagnostic metrics, not selectable objectives.</p></Panel>
      </div>
      <div><SchedulingMethod jobCount={selected.size} v5={dataset?.schema_version==='AL13_BENCHMARK_V5'} value={algorithmChoice?algorithmChoice.split('|'):[]} onChange={values=>setAlgorithmChoice(values.join('|'))} disabled={busy} previewRequest={{dataset_id:dataset?.id,job_ids:[...selected],horizon_start:start,horizon_end:end,algorithms:algorithmChoice?algorithmChoice.split('|'):[],settings}}/></div>
      <Panel title="Data Validation" subtitle="Validated by the scheduling API after you continue.">
        <ul className="space-y-3 text-xs text-slate-600">{['Jobs, processing times & precedence','Workstation & operator mapping','Saved calendars, breaks & downtime','Setup, OIL phases & 2C batching'].map(label=><li key={label} className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-slate-300"/>{label}</li>)}</ul>
        <p className="text-[11px] text-slate-400 mt-4">Not checked for this selection yet. Detailed issues and search settings appear in the next step.</p>
      </Panel>
    </div>
    <div className="sticky bottom-0 mt-5 flex flex-wrap justify-between items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-3 shadow-sm">
      <span className="text-xs text-slate-500">Selected: <strong className="text-slate-800">{selected.size} jobs</strong> · {schedulingMethodName(algorithmChoice?algorithmChoice.split('|'):[])}</span>
      <button disabled={selected.size===0||!start||!end||busy} onClick={()=>onNext(selected,start,end,algorithmChoice?algorithmChoice.split('|'):[])} className="aps-primary">{busy?'Validating…':'Validate Data'}<ChevronRight size={16}/></button>
    </div>
  </div></div>;
}

// ─── Step 1: Configuration ────────────────────────────────────────────────

interface Issue { entity:string; field:string; message:string; }
interface Validation { valid:boolean; issues:Issue[]; size:number; classification:string; algorithms:string[]; schema_version?:string; }
interface AlgorithmResult { algorithm:string; feasible:boolean; proven_optimal:boolean; status:string; makespan_min:number|null; total_tardiness_min:number|null; total_flow_time_min:number|null; tardy_jobs?:number; runtime_sec:number|null; operations:GanttTask[]; message?:string; validation_issues?:Issue[]; metadata?: {status_name?:string; search_stop_reason?:string; completed_runs?:number; runs?:number; working_calendar_policy?:string; best_bound_min?:number|null; relative_gap?:number|null}; }
interface RunResult { selection_mode?:string; routing_reason?:string; request?: {dataset_id:string; horizon_start:string; horizon_end:string}; run_id:string; status:string; algorithms:AlgorithmResult[]; selected:AlgorithmResult|null; selection_reason:string; accepted_at?:string; applied_at?:string; }
const number = (value: number|null|undefined) => value == null ? '\u2014' : value.toLocaleString(undefined,{maximumFractionDigits:2});

function Comparison({result}: {result:RunResult}) {
  return <div className="p-6">
    <h2 className="font-semibold mb-2">Algorithm Comparison</h2><p className="text-xs text-slate-500 mb-4">Participants in this saved run only. Sharing an input does not imply equal computational budgets. Optimality requires a solver certificate; heuristic results are best found schedules.</p>
    <div className="bg-white rounded-xl border border-slate-200 overflow-auto mb-4"><table className="w-full text-sm">
      <thead className="bg-[#F8FAFC]"><tr>{['Algorithm','Makespan (min)','Tardiness (min)','Flow time (min)','Runtime (s)','Lower bound (min)','Solver gap (%)','Status'].map(h=><th className="text-left px-4 py-3 text-xs font-semibold text-[#64748B]" key={h}>{h}</th>)}</tr></thead>
      <tbody>{result.algorithms?.map(r=><tr className={result.selected?.algorithm===r.algorithm?'bg-[#EFF6FF]':'border-t border-slate-100'} key={r.algorithm}>
        <td className="p-4 font-semibold">{algorithmName(r.algorithm)}</td><td className="p-4">{number(r.makespan_min)}</td><td className="p-4">{number(r.total_tardiness_min)}</td><td className="p-4">{number(r.total_flow_time_min)}</td><td className="p-4">{number(r.runtime_sec)}</td><td className="p-4">{number(r.metadata?.best_bound_min)}</td><td className="p-4">{r.metadata?.relative_gap==null?'\u2014':number(r.metadata.relative_gap*100)}</td>
        <td className="p-4"><div>{r.proven_optimal?'Optimal':r.feasible?'Feasible':r.status}{result.selected?.algorithm===r.algorithm?' · Selected':''}</div>{r.message && <p className="text-xs text-red-600 mt-1">{r.message}</p>}{r.validation_issues?.slice(0,5).map((e,i)=><p key={i} className="text-xs text-red-600">{e.entity}: {e.message}</p>)}</td>
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
  const [ganttJob,setGanttJob] = useState('');
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
    <div data-testid="result-summary" className="aps-result-summary shrink-0 max-h-[45vh] overflow-y-auto">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div><h1 className="text-lg font-bold text-[#0F172A]">Scheduling Result</h1><p className="text-xs text-[#64748B] mt-0.5" title={result.run_id}>Run {result.run_id.slice(0,12)} · {algorithmName(selected?.algorithm)} · {selected?.proven_optimal?'Optimal':selected?.feasible?'Feasible · optimality not proven':result.status}</p></div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button onClick={onAdjust} className="px-2 py-1.5 text-xs border border-slate-200 rounded-lg">Adjust</button>
          <button title={dataset && result.request && dataset.id!==result.request.dataset_id?'Review a new schedule using the current calendar':'Repeat the saved job selection and date range using current calendar rules'} disabled={busy} onClick={dataset && result.request && dataset.id!==result.request.dataset_id?onUseLatest:onRunAgain} className="flex items-center gap-1.5 px-2 py-1.5 text-xs border border-slate-200 rounded-lg"><RotateCcw size={14}/>{dataset && result.request && dataset.id!==result.request.dataset_id?'Schedule with latest calendar':'Run Again'}</button>
          <button disabled={busy} onClick={async()=>{setBusy(true);setError('');try{const verified=await api('/schedules/'+result.run_id+'/result?validate_export=true');downloadJson(verified,'schedule-'+result.run_id+'.json');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}} className="flex items-center gap-1.5 px-2 py-1.5 text-xs border border-slate-200 rounded-lg"><Download size={14}/>JSON</button>
          <button disabled={!selected || busy} onClick={async()=>{setBusy(true);setError('');try{await downloadExcel(result.run_id);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}} className="flex items-center gap-1.5 px-2 py-1.5 text-xs border border-slate-200 rounded-lg disabled:opacity-50"><Download size={14}/>Export Results to Excel</button>
          {selected && !result.accepted_at && <button disabled={busy} onClick={()=>setShowSaveModal(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-[#2563EB] text-white rounded-lg font-medium"><Save size={14}/>Accept & Save</button>}
          {result.accepted_at && <button disabled={busy || !!result.applied_at} onClick={()=>void action('apply')} className="px-3 py-1.5 text-xs bg-[#2563EB] text-white rounded-lg disabled:opacity-50">{result.applied_at?'Applied':'Apply to Production'}</button>}
        </div>
      </div>
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        <MetricCard label="Makespan" value={metric(selected?.makespan_min)+' min'} source="Cmax · scheduling objective" icon={Clock}/>
        <MetricCard label="Tardy Jobs" value={metric(selected?.tardy_jobs,0)} source="Diagnostic · planned completion" icon={AlertCircle} tone="green"/>
        <MetricCard label="Total Flow Time" value={metric(selected?.total_flow_time_min)+' min'} source="Diagnostic · saved result" icon={Layers} tone="purple"/>
        <MetricCard label="Computation Time" value={metric(selected?.runtime_sec)+' s'} source={new Set(operations.flatMap(taskJobIds)).size+' scheduled jobs'} icon={Timer} tone="amber"/>
      </div>
      <details data-testid="result-details" className="mt-2 text-xs text-slate-600">
        <summary className="cursor-pointer w-fit">Show Details{dataset && result.request && dataset.id!==result.request.dataset_id && <span className="ml-2 text-amber-700">Calendar notice</span>}</summary>
        <div className="mt-2">
          <dl className="grid sm:grid-cols-3 gap-3 mb-3">{summary.map(s=><div key={s.label}><dt className="text-slate-500">{s.label}</dt><dd className="font-semibold">{s.value}</dd></div>)}</dl>
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
      {tab==='gantt' && (selected ? <GanttChart initialJob={ganttJob} horizonStart={result.request?.horizon_start} horizonEnd={result.request?.horizon_end} calendarPolicy={selected.metadata?.working_calendar_policy} tasks={operations} jobs={context.jobs} calendar={context.calendar} onViewJob={id=>{setTableJob(id);setTab('details');}}/> : <p className="p-6 text-slate-500">No valid feasible schedule to display.</p>)}
      {tab==='details' && <OperationTable operations={operations} job={tableJob} onJob={setTableJob} onView={id=>{setGanttJob(id);setTab('gantt');}} validateExport={async()=>{await api('/schedules/'+result.run_id+'/result?validate_export=true');}}/>}
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
  if(validation) return <div className="flex-1 overflow-auto"><div className="aps-page">
    <PageHeading title="Scheduling Studio" description="Review input validation and configure the search budget before starting."/><WorkflowSteps current={1}/>
    <button onClick={()=>setValidation(null)} className="flex gap-2 text-sm text-[#64748B] mb-4"><ArrowLeft size={15}/>Back to selected jobs</button>
    <h1 className="text-xl font-bold mb-4">{validation.valid?'Data Validation Passed':'Data Validation Failed'}</h1>
    <ApiNotice error={error}/>
    <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-3 max-w-4xl">
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
