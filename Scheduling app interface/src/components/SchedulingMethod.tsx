import { useEffect, useState } from 'react';
import { post } from '../api';

export interface MethodPreview {
  algorithms:string[]; routing_reason:string; valid:boolean;
  availability:Record<string,{available:boolean;reason:string}>;
}
interface Props {
  jobCount:number; v5:boolean; value:string[];
  onChange:(algorithms:string[])=>void; disabled?:boolean;
  previewRequest?:Record<string,unknown>;
  onPreview?:(preview:MethodPreview,key:string)=>void;
}
export const schedulingMethodName=(values:string[]=[])=>!values.length?'Automatic':values.length>1?'Saved comparison':values[0]==='CP-SAT'?'CP-SAT':values[0]==='MLPSO'?'MLPSO':values[0]==='MILP'?'MILP (legacy)':'GA';

export default function SchedulingMethod({jobCount,v5,value,onChange,disabled=false,previewRequest,onPreview}:Props) {
  const requestKey=JSON.stringify(previewRequest);
  const [state,setState]=useState<{key:string;data?:MethodPreview;error?:string}>({key:''});
  useEffect(()=>{
    let cancelled=false;
    if(!jobCount||!previewRequest) return;
    const timer=setTimeout(()=>{
      post<MethodPreview>('/schedules/preview',previewRequest).then(data=>{
        if(!cancelled){setState({key:requestKey,data});onPreview?.(data,requestKey);}
      }).catch(e=>{if(!cancelled)setState({key:requestKey,error:(e as Error).message});});
    },200);
    return()=>{cancelled=true;clearTimeout(timer);};
  },[requestKey,jobCount]);
  const preview=state.key===requestKey?state.data:undefined;
  const error=state.key===requestKey?state.error:undefined;
  const active=schedulingMethodName(value);
  const choices=[
    {name:'Automatic',subtitle:'Recommended',description:'Uses the selected jobs, operations, resources and search budget to choose an available method.',technical:'CP-SAT, GA or MLPSO, according to dataset compatibility',algorithms:[],compatible:true},
    {name:'CP-SAT',subtitle:'Exact Constraint Solver',description:'Searches systematically for a feasible schedule and, when possible, proves optimality. Complex instances may need more time.',technical:'CP-SAT (OR-Tools)',algorithms:['CP-SAT'],compatible:v5},
    {name:'GA',subtitle:'Genetic Algorithm',description:'Improves candidate schedules through genetic search. Suitable when you want a good schedule within the available search budget.',technical:v5?'SS Micro-GA Fast':'Improved GA + IG',algorithms:[v5?'SS Micro-GA Fast':'Improved GA'],compatible:true},
    {name:'MLPSO',subtitle:'Multi-Level Particle Swarm Optimization',description:'Searches using cooperating particle groups to improve production schedules.',technical:v5?'MLPSO V3 · shared V5 decoder · iteration/evaluation limits':'Existing MLPSO implementation (legacy data)',algorithms:['MLPSO'],compatible:true},
  ];
  return <section aria-label="Scheduling Method" className="bg-white rounded-xl border border-slate-200 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2 mb-1"><h2 className="text-sm font-semibold text-[#0F172A]">Scheduling Method</h2><span className="text-xs text-slate-500">{jobCount} selected jobs</span></div>
    <p className="text-xs text-slate-500 mb-4">Choose how to build your production schedule. You can override Automatic at any time.</p>
    <div role="radiogroup" aria-label="Scheduling Method" className="space-y-3">
      {choices.map(choice=>{
        const availability=preview?.availability[choice.algorithms[0]];
        const unavailable=!choice.compatible||availability?.available===false;
        const reason=availability?.reason||(choice.name==='MLPSO'?'No V5-compatible MLPSO implementation is connected.':'CP-SAT requires compatible V5 data.');
        return <label key={choice.name} className={'flex items-start gap-3 rounded-lg border p-3 transition-colors '+(active===choice.name?'border-blue-400 bg-[#EFF6FF]':'border-slate-200 bg-white')+(disabled||unavailable?' cursor-not-allowed':' cursor-pointer hover:border-blue-300')}>
          <input type="radio" name="scheduling-method" aria-label={choice.name} checked={active===choice.name} disabled={disabled||unavailable} onChange={()=>onChange(choice.algorithms)} className="mt-1 accent-[#2563EB]"/>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold text-slate-800">{choice.name}</span>{choice.name==='Automatic'?<span className="text-[11px] font-medium text-blue-700 bg-blue-100 px-2 py-0.5 rounded-full">Recommended</span>:<span className="text-xs text-slate-500">{choice.subtitle}</span>}</div>
            <p className="text-sm text-slate-600 mt-1 leading-relaxed">{choice.description}</p>
            <p className="text-xs text-slate-500 mt-1">Technical method: {choice.technical}</p>
            {unavailable&&<p className="text-xs text-slate-500 mt-2">Unavailable: {reason}</p>}
          </div>
        </label>;
      })}
    </div>
    {error&&<p role="alert" className="text-xs text-red-600 mt-3">{error}</p>}
  </section>;
}
