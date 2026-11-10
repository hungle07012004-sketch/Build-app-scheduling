import { useState } from 'react';
import { api, ApiError, useApi } from '../api';
import { useAppData } from '../AppData';
import ApiNotice from './ApiNotice';

export default function InputDataPanel({initialTab='jobs'}:{initialTab?:string}) {
  const {dataset,refresh} = useAppData();
  const [tab,setTab] = useState(initialTab);
  const [uploading,setUploading] = useState(false);
  const [error,setError] = useState('');
  const [search,setSearch] = useState('');
  const [page,setPage] = useState(0);
  const {data,error:loadError} = useApi<any>(dataset && tab!=='jobs' ? '/' + tab + '?dataset_id=' + dataset.id : null, []);
  let rows: Record<string,unknown>[] = Array.isArray(data) ? data : tab==='resources' ? [
    ...Object.entries(data.requirements || {}).flatMap(([op,items]) => (items as any[]).map(r=>({operation_code:op,...r}))),
    ...Object.entries(data.units || {}).map(([rt,units])=>({resource_type:rt,units:(units as string[]).join(', ')})),
    ...(data.capacity_by_shift || [])] : [];
  rows = rows.filter(r=>JSON.stringify(r).toLowerCase().includes(search.toLowerCase()));
  const columns = [...new Set(rows.flatMap(r=>Object.keys(r)))];
  async function upload(file?:File) {
    if (!file) return;
    setUploading(true); setError('');
    try {
      const form = new FormData(); form.append('file',file);
      await api('/input/upload',{method:'POST',body:form}); await refresh();
    } catch(e) { const err=e as ApiError; setError(err.message + (err.details ? ' ' + JSON.stringify(err.details) : '')); }
    finally {setUploading(false);}
  }
  return <div className="mb-5">
    <div className="flex items-center gap-4 mb-3">
      <label className="cursor-pointer px-4 py-2.5 rounded-lg bg-[#2563EB] text-sm font-semibold text-white">
        {uploading ? 'Validating upload…' : 'Upload Input File'}
        <input aria-label="Upload Input File" disabled={uploading} className="hidden" type="file" accept=".xlsx,.json" onChange={e=>{void upload(e.target.files?.[0]); e.target.value='';}} />
      </label>
      <span className="text-xs text-[#64748B]">{dataset ? dataset.filename + ' · ' + dataset.jobs + ' jobs · ' + dataset.workstations + ' workstations · ' + dataset.validation_status : 'Upload an AL13 workbook or frozen JSON instance to begin.'}</span>
    </div>
    {dataset && <p className="text-xs text-[#64748B] mb-3">Uploaded {new Date(dataset.uploaded_at).toLocaleString()} · Calendar: {dataset.origin_datetime} → {dataset.horizon_end_datetime}</p>}
    <ApiNotice error={error || loadError}/>
    <div className="flex gap-1 border-b border-slate-200">
      {[['jobs','Jobs'],['workstations','Workstations'],['processing-times','Processing Times'],['calendar','Working Calendar'],['resources','Resources']].map(([key,label])=>
        <button key={key} onClick={()=>{setTab(key);setPage(0);setSearch('');}} className={'px-4 py-2.5 text-sm border-b-2 ' + (tab===key?'border-[#2563EB] text-[#2563EB]':'border-transparent text-[#64748B]')}>{label}</button>)}
    </div>
    {tab!=='jobs' && <div className="mt-3 rounded-xl bg-white border border-slate-200 p-3">
      <input aria-label="Search input data" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}} placeholder="Search input data…" className="px-3 py-2 border border-slate-200 rounded-lg text-sm mb-3"/>
      <div className="overflow-auto max-h-80"><table className="w-full text-xs"><thead><tr>{columns.map(c=><th className="text-left p-2 text-[#64748B]" key={c}>{c}</th>)}</tr></thead>
      <tbody>{rows.slice(page*50,(page+1)*50).map((r,i)=><tr className="border-t border-slate-100" key={i}>{columns.map(c=><td className="p-2 whitespace-nowrap" key={c}>{String(r[c] ?? '')}</td>)}</tr>)}</tbody></table></div>
      <div className="flex gap-3 text-xs mt-2"><button disabled={page===0} onClick={()=>setPage(p=>p-1)}>Previous</button><span>{rows.length} records · Page {page+1}</span><button disabled={(page+1)*50>=rows.length} onClick={()=>setPage(p=>p+1)}>Next</button></div>
    </div>}
  </div>;
}
