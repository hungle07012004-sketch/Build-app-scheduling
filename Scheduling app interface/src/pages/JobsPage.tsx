import { useAppData } from '../AppData';
import { api, post } from '../api';
import ApiNotice from '../components/ApiNotice';
import InputDataPanel from '../components/InputDataPanel';
import { useState } from 'react';
import { type Job, type JobStatus, type Priority } from '../data';
import StatusChip from '../components/StatusChip';
import { Search, Plus, Filter, Edit2, Trash2, Eye, ChevronDown } from 'lucide-react';

const STATUS_OPTIONS: (JobStatus | 'All')[] = ['All', 'Unscheduled', 'Scheduled', 'In Progress', 'Completed', 'Delayed'];
const PRIORITY_OPTIONS: (Priority | 'All')[] = ['All', 'High', 'Medium', 'Low'];


function JobFormModal({ job, onClose }: { job?: Job; onClose: () => void }) {
  const {dataset,jobs,refresh}=useAppData();
  const [error,setError]=useState('');
  const [saving,setSaving]=useState(false);
  const [values,setValues]=useState<Record<string,string>>({
    job_id:'',template_job_id:jobs[0]?.id || '',product_code:job?.product || '',
    priority:String(job?.priorityValue || 1),release_datetime:job?.releaseTime?.replace(' ','T') || '',
    element_ready_datetime:job?.materialReady?.replace(' ','T') || '',loopset_release_datetime:job?.loopsetRelease?.replace(' ','T') || '',
    due_datetime:job?.dueDate?.replace(' ','T') || '',
  });
  async function save(schedule=false) {
    if(!dataset) return;
    setSaving(true);setError('');
    try {
      if(job) {
        const {job_id,template_job_id,...fields}=values;
        await api('/input/'+dataset.id+'/jobs/'+encodeURIComponent(job.id),{method:'PATCH',body:JSON.stringify({...fields,priority:Number(fields.priority)})});
      } else {
        await post('/input/'+dataset.id+'/jobs',{job_id:values.job_id,template_job_id:values.template_job_id});
      }
      await refresh();onClose();
      if(schedule) {sessionStorage.removeItem('al13-run');window.dispatchEvent(new Event('al13-open-run'));}
    } catch(e){setError((e as Error).message);} finally {setSaving(false);}
  }
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"><div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-6">
    <h3 className="text-lg font-semibold text-[#0F172A] mb-5">{job?'Edit Job '+job.id:'Add Job from Existing Recipe'}</h3><ApiNotice error={error}/>
    <div className="grid grid-cols-2 gap-4">
      {!job ? <><label className="text-xs text-[#64748B]">Job ID<input required value={values.job_id} onChange={e=>setValues({...values,job_id:e.target.value})} className="w-full border border-slate-200 rounded-lg p-2 text-sm"/></label>
        <label className="text-xs text-[#64748B]">Copy recipe from<select value={values.template_job_id} onChange={e=>setValues({...values,template_job_id:e.target.value})} className="w-full border border-slate-200 rounded-lg p-2 text-sm">{jobs.map(j=><option value={j.id} key={j.id}>{j.id}</option>)}</select></label>
        <p className="col-span-2 text-xs text-[#64748B]">Copies quantity, processing options, precedence and dates. Edit dates after adding; use Excel upload for a new processing recipe or quantity.</p></> :
        Object.entries(values).filter(([key])=>!['job_id','template_job_id'].includes(key)).map(([key,value])=><label key={key} className="text-xs text-[#64748B]">{key.replace(/_/g,' ')}<input value={value} type={key==='priority'?'number':key.endsWith('datetime')?'datetime-local':'text'} onChange={e=>setValues({...values,[key]:e.target.value})} className="w-full border border-slate-200 rounded-lg p-2 text-sm"/></label>)}
    </div>
    <p className="text-xs text-slate-400 mt-4">Saving creates an input revision; existing schedules retain their frozen data.</p>
    <div className="flex justify-end gap-2 mt-5"><button disabled={saving} onClick={onClose} className="px-4 py-2 text-sm text-[#64748B] rounded-lg">Cancel</button><button disabled={saving || !dataset || (!job && !values.job_id)} onClick={()=>void save()} className="px-4 py-2 text-sm bg-slate-100 rounded-lg">Save Job</button><button disabled={saving || !dataset || (!job && !values.job_id)} onClick={()=>void save(true)} className="px-4 py-2 text-sm bg-[#2563EB] text-white rounded-lg">Save & Schedule</button></div>
  </div></div>;
}
export default function JobsPage() {
  const {jobs:JOBS,dataset,refresh}=useAppData();
  const [page,setPage]=useState(0);
  const [error,setError]=useState('');
  async function remove(job:Job) {
    if(!dataset || !window.confirm('Remove '+job.id+' from a new input revision?')) return;
    try {await api('/input/'+dataset.id+'/jobs/'+encodeURIComponent(job.id),{method:'DELETE'});await refresh();}
    catch(e){setError((e as Error).message);}
  }
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<JobStatus | 'All'>('All');
  const [priorityFilter, setPriorityFilter] = useState<Priority | 'All'>('All');
  const [showModal, setShowModal] = useState(false);
  const [editJob, setEditJob] = useState<Job | undefined>();

  const filtered = JOBS.filter(j => {
    if (statusFilter !== 'All' && j.status !== statusFilter) return false;
    if (priorityFilter !== 'All' && j.priority !== priorityFilter) return false;
    if (search && !j.id.toLowerCase().includes(search.toLowerCase()) && !j.product.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="flex-1 overflow-y-auto bg-[#F8FAFC]">
      <div className="max-w-7xl mx-auto px-6 py-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-bold text-[#0F172A]">Manage Input Data</h1>
            <p className="text-sm text-[#64748B] mt-0.5">{JOBS.length} total jobs · {filtered.length} shown</p>
          </div>
          <button
            onClick={() => { setEditJob(undefined); setShowModal(true); }}
            className="flex items-center gap-2 px-4 py-2.5 bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-sm font-semibold rounded-lg transition-all"
          >
            <Plus size={16} />
            Add Job
          </button>
        </div>

        <InputDataPanel/><ApiNotice error={error}/>
        {/* Filters */}
        <div className="flex items-center gap-3 mb-4">
          <div className="relative flex-1 max-w-sm">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#94A3B8]" />
            <input
              value={search}
              onChange={e => (setSearch(e.target.value),setPage(0))}
              placeholder="Search by Job ID or Product…"
              className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB] text-[#0F172A]"
            />
          </div>
          <div className="relative">
            <select
              value={statusFilter}
              onChange={e => (setStatusFilter(e.target.value as any),setPage(0))}
              className="appearance-none pl-3 pr-8 py-2 text-sm rounded-lg border border-slate-200 bg-white text-[#0F172A] focus:outline-none focus:ring-2 focus:ring-[#2563EB] cursor-pointer"
            >
              {STATUS_OPTIONS.map(s => <option key={s}>{s}</option>)}
            </select>
            <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
          </div>
          <div className="relative">
            <select
              value={priorityFilter}
              onChange={e => (setPriorityFilter(e.target.value as any),setPage(0))}
              className="appearance-none pl-3 pr-8 py-2 text-sm rounded-lg border border-slate-200 bg-white text-[#0F172A] focus:outline-none focus:ring-2 focus:ring-[#2563EB] cursor-pointer"
            >
              {PRIORITY_OPTIONS.map(p => <option key={p}>{p}</option>)}
            </select>
            <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
          </div>
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-[#F8FAFC]">
                  {['Job ID', 'Product', 'Qty', 'Priority', 'Release Time', 'Material Ready', 'Due Date', 'Status', 'Actions'].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {filtered.slice(page*50,(page+1)*50).map(job => (
                  <tr key={job.id} className="hover:bg-[#F8FAFC] transition-colors">
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs font-semibold text-[#2563EB]">{job.id}</span>
                    </td>
                    <td className="px-4 py-3 text-[#0F172A] font-medium whitespace-nowrap">{job.product}</td>
                    <td className="px-4 py-3 text-[#64748B] text-center">{job.batchQty}</td>
                    <td className="px-4 py-3"><StatusChip status={job.priority} dot={false} /></td>
                    <td className="px-4 py-3 text-[#64748B] text-xs whitespace-nowrap">{job.releaseTime}</td>
                    <td className="px-4 py-3 text-[#64748B] text-xs whitespace-nowrap">{job.materialReady}</td>
                    <td className="px-4 py-3 text-[#64748B] text-xs whitespace-nowrap">{job.dueDate.split(' ')[0]}</td>
                    <td className="px-4 py-3"><StatusChip status={job.status} /></td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <button className="p-1.5 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600" title="View" onClick={() => { setEditJob(job); setShowModal(true); }}>
                          <Eye size={14} />
                        </button>
                        <button
                          className="p-1.5 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600"
                          onClick={() => { setEditJob(job); setShowModal(true); }}
                          title="Edit"
                        >
                          <Edit2 size={14} />
                        </button>
                        <button className="p-1.5 rounded hover:bg-red-50 text-slate-400 hover:text-red-500" title="Delete" onClick={()=>void remove(job)}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filtered.length === 0 && (
            <div className="py-16 text-center text-[#94A3B8]">
              <p className="font-medium">No jobs match your filters</p>
              <p className="text-sm mt-1">Try adjusting your search or filter criteria</p>
            </div>
          )}
        </div>
      </div>

      <div className="flex gap-4 px-6 pb-4 text-sm text-[#64748B]"><button disabled={page===0} onClick={()=>setPage(p=>p-1)}>Previous</button><span>Page {page+1}</span><button disabled={(page+1)*50>=filtered.length} onClick={()=>setPage(p=>p+1)}>Next</button></div>
      {showModal && (
        <JobFormModal
          job={editJob}
          onClose={() => { setShowModal(false); setEditJob(undefined); }}
        />
      )}
    </div>
  );
}
