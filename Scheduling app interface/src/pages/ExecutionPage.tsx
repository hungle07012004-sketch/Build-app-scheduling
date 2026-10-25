import { useState } from 'react';
import { api, useApi } from '../api';
import { useAppData } from '../AppData';
import ApiNotice from '../components/ApiNotice';
import StatusChip from '../components/StatusChip';

export default function ExecutionPage() {
  const {data,refresh,error} = useApi<any[]>('/execution',[]);
  const {refresh:refreshApp} = useAppData();
  const [mutationError,setError] = useState('');
  const [saving,setSaving] = useState(false);
  async function update(row:any,status:string) {
    setSaving(true); setError('');
    try { await api('/execution/jobs/'+encodeURIComponent(row.job_id),{method:'PATCH',body:JSON.stringify({run_id:row.run_id,status})}); await Promise.all([refresh(),refreshApp()]); }
    catch(e) {setError((e as Error).message);} finally {setSaving(false);}
  }
  return <div className="flex-1 overflow-y-auto bg-[#F8FAFC]"><div className="max-w-7xl mx-auto px-6 py-6">
    <h1 className="text-xl font-bold text-[#0F172A]">Production Execution</h1><p className="text-sm text-[#64748B] mt-1 mb-6">Update jobs from accepted and applied schedules.</p>
    <ApiNotice error={error || mutationError}/>
    <div className="bg-white rounded-xl border border-slate-200 overflow-auto"><table className="w-full text-sm">
      <thead className="bg-[#F8FAFC] text-[#64748B]"><tr>{['Schedule','Job','Status','Updated','Update Status'].map(h=><th className="p-3 text-left" key={h}>{h}</th>)}</tr></thead>
      <tbody>{data.map(r=><tr className="border-t border-slate-100" key={r.run_id+r.job_id}><td className="p-3 font-mono text-xs">{r.run_id.slice(0,12)}</td><td className="p-3 text-[#2563EB]">{r.job_id}</td><td className="p-3"><StatusChip status={r.status}/></td><td className="p-3 text-xs">{new Date(r.updated_at).toLocaleString()}</td><td className="p-3"><select aria-label={'Status '+r.job_id} disabled={saving} value={r.status} onChange={e=>void update(r,e.target.value)} className="border border-slate-200 rounded-lg p-2">{['Scheduled','In Progress','Completed','Delayed'].map(s=><option key={s}>{s}</option>)}</select></td></tr>)}</tbody>
    </table>{!data.length && <p className="p-10 text-center text-slate-400">Apply an accepted schedule to begin production execution.</p>}</div>
  </div></div>;
}
