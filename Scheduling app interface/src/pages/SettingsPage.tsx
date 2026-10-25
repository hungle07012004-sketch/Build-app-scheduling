import { useState } from 'react';
import { useApi } from '../api';
import { useAppData } from '../AppData';
import ApiNotice from '../components/ApiNotice';

export default function SettingsPage() {
  const {dataset}=useAppData();
  const v5=dataset?.schema_version==='AL13_BENCHMARK_V5';
  const [settings,setSettings] = useState<Record<string,number>>(()=>JSON.parse(localStorage.getItem('al13-settings') || '{}'));
  const [saved,setSaved] = useState(false);
  const {data,error}=useApi<{small_scale_max_jobs?:number}>('/config',{});
  return <div className="flex-1 overflow-y-auto bg-[#F8FAFC]"><div className="max-w-2xl mx-auto px-6 py-6">
    <div className="flex items-center justify-between mb-6"><div><h1 className="text-xl font-bold text-[#0F172A]">Settings</h1><p className="text-sm text-[#64748B] mt-0.5">Default parameters for new scheduling runs</p></div>
    <button onClick={()=>{localStorage.setItem('al13-settings',JSON.stringify(settings));setSaved(true);}} className="px-4 py-2.5 bg-[#2563EB] text-white text-sm font-semibold rounded-lg">{saved?'Saved':'Save Changes'}</button></div>
    <ApiNotice error={error}/>
    <div className="bg-white rounded-xl border border-slate-200 mb-4"><div className="px-5 py-4 border-b border-slate-100 font-semibold text-sm">Scheduling Settings</div><div className="p-5 text-sm space-y-3 text-[#64748B]">
      <p>{v5?'V5 objective: final 2C makespan only; tardiness and flow time are diagnostics.':'Legacy objective: makespan, then total tardiness, then total flow time.'}</p>
      <p>Automatic chooses one available engine using job count, estimated operations, resource/calendar complexity and the search budget. See the decision and reason on Scheduling before running. Manual selection runs the chosen engine.</p>
      <p>You can choose a solver or Compare when selecting jobs. Auto uses the size rules above.</p>
      <p>Working calendar and factory local dates come from the uploaded AL13 dataset.</p><p>Every schedule requires acceptance before application to production.</p>
    </div></div>
    <div className="bg-white rounded-xl border border-slate-200"><div className="px-5 py-4 border-b border-slate-100 font-semibold text-sm">Optimization Settings</div><div className="p-5 space-y-4">
      {((v5 ? [['runs','Independent runs',1],['seed_base','Random seed',42],['exact_time_limit','CP-SAT time limit (seconds)',120],['exact_workers','CP-SAT workers (0 = auto)',0],['micro_population','Micro-GA population',24],['micro_generations','Micro-GA epochs',3000],['micro_max_decodes','Micro-GA decode budget',12000],['micro_time_limit','Micro-GA total search budget (seconds)',120],['micro_no_improve','Micro-GA epochs without improvement (0 = off)',600],['micro_min_epochs','Micro-GA minimum epochs before stopping',1300]] : [['runs','Independent runs',1],['seed_base','Random seed',42],['ga_population','GA population',80],['ga_generations','GA generations',500],['mlpso_population','MLPSO population',60],['mlpso_iterations','MLPSO iterations',500],['milp_time_limit','MILP time limit (seconds)',600],['target_polish_generations','Target polishing generations',500]]) as [string,string,number][]).map(([key,label,value])=><div key={key} className="flex items-center justify-between"><label htmlFor={key} className="text-sm text-[#0F172A]">{label}</label><input id={key} type="number" min={['seed_base','exact_workers','micro_no_improve','micro_min_epochs'].includes(key)?0:1} value={settings[key]??value} onChange={e=>{setSettings({...settings,[key]:Number(e.target.value)});setSaved(false);}} className="w-28 px-3 py-1.5 text-sm rounded-lg border border-slate-200 text-right"/></div>)}
    </div></div>
  </div></div>;
}
