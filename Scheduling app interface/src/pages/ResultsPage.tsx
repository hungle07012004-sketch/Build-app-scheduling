import { useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { useAppData } from '../AppData';
import { PageHeading, Panel } from '../components/aps';
import RunHistory from '../components/RunHistory';
export default function ResultsPage({onCreateSchedule}:{onCreateSchedule:()=>void}) {
  const {overview}=useAppData();
  const [search,setSearch]=useState(''),[status,setStatus]=useState('All'),[page,setPage]=useState(0);
  const history:any[]=overview.history||[];
  const rows=history.filter(r=>(status==='All'||r.status===status)&&[r.id,r.algo,r.dataset_id].some(v=>String(v||'').toLowerCase().includes(search.toLowerCase())));
  const current=Math.min(page,Math.max(0,Math.ceil(rows.length/20)-1));
  return <div className="flex-1 overflow-auto"><div className="aps-page"><PageHeading title="Schedule Results" description="Review saved runs, inspect their Gantt charts and export validated schedules." actions={<button className="aps-primary" onClick={onCreateSchedule}><Plus size={16}/>New Scheduling Run</button>}/>
    <Panel title="Scheduling runs" subtitle="Historical tracking. Different inputs, calendars and search settings are not a fair benchmark. Hover a row for its context.">
      <div className="flex flex-wrap gap-3 mb-4"><label className="flex items-center gap-2 border border-slate-200 rounded-lg px-3 py-2"><Search size={15}/><input aria-label="Search runs" placeholder="Run ID, algorithm or dataset" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}} className="text-xs outline-none w-56"/></label><select aria-label="Run status" className="aps-secondary" value={status} onChange={e=>{setStatus(e.target.value);setPage(0);}}>{['All',...new Set(history.map(r=>r.status))].map(s=><option key={s}>{s}</option>)}</select></div>
      <RunHistory rows={rows.slice(current*20,current*20+20)}/><div className="flex justify-between items-center mt-4 text-xs text-slate-500"><span>{rows.length} runs · Page {current+1}</span><div className="flex gap-3"><button disabled={!current} onClick={()=>setPage(current-1)}>Previous</button><button disabled={(current+1)*20>=rows.length} onClick={()=>setPage(current+1)}>Next</button></div></div>
    </Panel></div></div>;
}
