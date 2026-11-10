import { algorithmName } from '../algorithms';
import { metric } from './aps';
import {runLabel,runDate} from '../runLabels';
export function openRun(id:string) { sessionStorage.setItem('al13-run',id); window.dispatchEvent(new Event('al13-open-run')); }
export function runContext(r:any) {
  const s=r.search_settings || {};
  const budget=String(r.algo).includes('Micro')?s.micro_time_limit:String(r.algo).includes('CP-SAT')?s.exact_time_limit:s.milp_time_limit;
  return `${algorithmName(r.algo)} · ${r.jobs} jobs · Dataset ${r.dataset_id || 'not recorded'} · Calendar ${r.calendar?.start || 'not recorded'} → ${r.calendar?.end || 'not recorded'} · Budget ${budget == null ? 'not recorded' : budget+' s'} · ${r.status}`;
}
export default function RunHistory({rows}:{rows:any[]}) {
  return <div className="overflow-x-auto"><table className="aps-table"><thead><tr>{['Schedule','Algorithm','Jobs','Makespan (min)','Runtime (s)','Lifecycle',''].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
    {rows.map(r=><tr key={r.id} title={runContext(r)}><td><button className="text-blue-600 font-semibold text-left" title={r.id} onClick={()=>openRun(r.id)}>{runLabel(r)}</button><p className="text-[10px] text-slate-400 mt-1">{r.finished_at?'Finished: '+runDate(r.finished_at):'Created: '+runDate(r.created_at)} (UTC+07)</p></td><td>{algorithmName(r.algo)}</td><td>{metric(r.jobs,0)}</td><td className="tabular-nums">{metric(r.makespan)}</td><td className="tabular-nums">{metric(r.runtime)}</td><td><span className="rounded px-2 py-1 text-[11px] bg-slate-100">{r.status || 'Unknown'}</span><div className="text-[10px] text-slate-500 mt-1">{r.accepted?'Accepted':'Not accepted'} · {r.applied?'Applied':'Not applied'}</div></td><td><button aria-label={'Open schedule '+r.id} onClick={()=>openRun(r.id)} className="text-blue-600 font-medium">View →</button></td></tr>)}
    {!rows.length&&<tr><td colSpan={7} className="text-center text-slate-500 py-10">No scheduling runs to display.</td></tr>}
  </tbody></table></div>;
}
