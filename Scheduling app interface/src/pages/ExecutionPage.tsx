import { useState } from 'react';
import { api, useApi } from '../api';
import { useAppData } from '../AppData';
import ApiNotice from '../components/ApiNotice';
import StatusChip from '../components/StatusChip';

function scheduleLabel(id:string, created?:string) {
  const date = created ? new Date(created) : null;
  const stamp = date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('en-GB', {
    day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:'Asia/Ho_Chi_Minh',
  }).format(date) : null;
  // Stable creation date + ID, never row order or mutable execution.updated_at.
  return `Schedule${stamp ? ' · '+stamp : ''} · ${id.slice(0,8).toUpperCase()}`;
}

export default function ExecutionPage() {
  const {data,refresh,error} = useApi<any[]>('/execution',[]);
  const {refresh:refreshApp,overview} = useAppData();
  const created = new Map<string,string>((overview.history || []).map((r:any) => [r.id,r.created_at]));
  const [mutationError,setError] = useState('');
  const [saving,setSaving] = useState(false);
  async function update(row:any,status:string) {
    setSaving(true); setError('');
    try { await api('/execution/jobs/'+encodeURIComponent(row.job_id),{method:'PATCH',body:JSON.stringify({run_id:row.run_id,status})}); await Promise.all([refresh(),refreshApp()]); }
    catch(e) {setError((e as Error).message);} finally {setSaving(false);}
  }
  return <div className="flex-1 min-w-0 overflow-y-auto bg-[#F8FAFC]"><div className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
    <h1 className="text-xl font-bold text-[#0F172A]">Production Execution</h1><p className="text-sm text-[#64748B] mt-1 mb-6">Update jobs from accepted and applied schedules.</p>
    <ApiNotice error={error || mutationError}/>
    <p className="text-xs text-slate-500 mb-3">Schedule labels use creation time (factory time, UTC+07) and a stable short ID. Hover for the original ID.</p>
    <div className="bg-white rounded-xl border border-slate-200 overflow-auto"><table className="w-full min-w-[680px] text-sm">
      <thead className="bg-[#F8FAFC] text-[#64748B]"><tr>{['Schedule','Job','Status','Updated','Update Status'].map(h=><th className="p-3 text-left" key={h}>{h}</th>)}</tr></thead>
      <tbody>{data.map(r=><tr className="border-t border-slate-100" key={r.run_id+r.job_id}><td className="p-3 text-xs font-medium min-w-52 max-w-80 break-words" title={r.run_id} data-testid="execution-schedule-label">{scheduleLabel(r.run_id,created.get(r.run_id))}</td><td className="p-3 text-[#2563EB]">{r.job_id}</td><td className="p-3"><StatusChip status={r.status}/></td><td className="p-3 text-xs">{new Date(r.updated_at).toLocaleString()}</td><td className="p-3"><select aria-label={'Status '+r.job_id} disabled={saving} value={r.status} onChange={e=>void update(r,e.target.value)} className="border border-slate-200 rounded-lg p-2">{['Scheduled','In Progress','Completed','Delayed'].map(s=><option key={s}>{s}</option>)}</select></td></tr>)}</tbody>
    </table>{!data.length && <p className="p-10 text-center text-slate-400">Apply an accepted schedule to begin production execution.</p>}</div>
  </div></div>;
}
