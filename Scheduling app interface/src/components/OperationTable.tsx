import { useMemo, useState } from 'react';
import { Download, Search } from 'lucide-react';
import { taskJobIds, type GanttTask } from '../data';
import StatusChip from './StatusChip';
import { metric } from './aps';
export default function OperationTable({operations,job,onJob,onView,validateExport}:{operations:GanttTask[];job:string;onJob:(id:string)=>void;onView:(id:string)=>void;validateExport:()=>Promise<void>}) {
  const [query,setQuery]=useState(''),[event,setEvent]=useState('All'),[sort,setSort]=useState('time'),[page,setPage]=useState(0),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const rows=useMemo(()=>operations.filter(t=>(job==='All'||taskJobIds(t).includes(job))&&(event==='All'||t.eventType===event)&&[t.jobId,t.product,t.operation,t.workstation,t.resourceUnits,t.eventId].some(v=>(v||'').toLowerCase().includes(query.toLowerCase()))).sort((a,b)=>sort==='job'?a.jobId.localeCompare(b.jobId,undefined,{numeric:true})||a.startMin-b.startMin:a.startMin-b.startMin),[operations,job,event,query,sort]);
  const current=Math.min(page,Math.max(0,Math.ceil(rows.length/50)-1));
  const columns=['Job ID','Product','Event Type','Operation','Workstation','Resources','Start Time','Finish Time','Processing Time (min)','Event / Batch ID','Status'];
  const values=(t:GanttTask)=>[t.jobId,t.product,t.eventType||'',t.operation,t.workstation,t.resourceUnits||'',t.startTime,t.finishTime,t.processingMin,t.eventId||'',t.status];
  async function exportCsv() {
    setBusy(true);setError('');
    try {
      await validateExport();
      const quote=(v:unknown)=>{const s=String(v??'');return '"'+(/^[=+@\-]/.test(s)?"'"+s:s).replace(/"/g,'""')+'"';};
      const csv=[columns,...rows.map(values)].map(r=>r.map(quote).join(',')).join('\r\n');
      const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));
      const a=document.createElement('a');a.href=url;a.download='AL13_Detailed_Schedule.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <section data-testid="operation-table" className="p-4 lg:p-5"><div className="flex flex-wrap gap-3 items-center mb-4">
    <h2 className="font-semibold mr-auto">Detailed Schedule</h2><label className="flex gap-2 items-center border border-slate-200 rounded p-2"><Search size={14}/><input aria-label="Search schedule operations" placeholder="Job, resource or event…" className="text-xs outline-none" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/></label>
    <label className="text-xs text-slate-500">Job <select aria-label="Table Job filter" className="border border-slate-200 rounded p-2" value={job} onChange={e=>{onJob(e.target.value);setPage(0);}}><option>All</option>{[...new Set(operations.flatMap(taskJobIds))].sort().map(id=><option key={id}>{id}</option>)}</select></label>
    <select aria-label="Event type filter" className="aps-secondary" value={event} onChange={e=>{setEvent(e.target.value);setPage(0);}}><option value="All">All event types</option>{[...new Set(operations.map(t=>t.eventType).filter(Boolean))].map(t=><option key={t}>{t}</option>)}</select>
    <select aria-label="Sort operations" className="aps-secondary" value={sort} onChange={e=>{setSort(e.target.value);setPage(0);}}><option value="time">Start time</option><option value="job">Job ID</option></select>
    <button className="aps-secondary" disabled={busy||!rows.length} onClick={()=>void exportCsv()}><Download size={14}/>{busy?'Validating…':'Export CSV'}</button>
  </div><p className="text-xs text-slate-500 mb-3">{rows.length} / {operations.length} operations · Export CSV includes all filtered rows. Click a Job to locate it in Gantt.</p>
    {error&&<p role="alert" className="text-red-600 text-sm mb-3">{error}</p>}
    <div className="aps-panel overflow-x-auto"><table className="aps-table"><thead><tr>{columns.map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{rows.slice(current*50,current*50+50).map((t,i)=><tr key={(t.eventId||'event')+i}><td><button className="text-blue-600 font-mono" aria-label={'Locate '+t.jobId+' in Gantt'} onClick={()=>onView(taskJobIds(t)[0])}>{t.jobId}</button></td><td>{t.product}</td><td>{t.eventType||'—'}</td><td>{t.operation}</td><td>{t.workstation}</td><td className="text-[11px]">{t.resourceUnits||'Not recorded'}</td><td className="whitespace-nowrap">{t.startTime}</td><td className="whitespace-nowrap">{t.finishTime}</td><td>{metric(t.processingMin)}</td><td>{t.eventId||'—'}</td><td><StatusChip status={t.status}/></td></tr>)}</tbody></table></div>
    <div className="flex justify-between items-center text-xs text-slate-500 mt-4"><span>Page {current+1} · 50 rows per page</span><div className="flex gap-3"><button disabled={!current} onClick={()=>setPage(current-1)}>Previous</button><button disabled={(current+1)*50>=rows.length} onClick={()=>setPage(current+1)}>Next</button></div></div>
  </section>;
}
