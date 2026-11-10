// Display labels only. Database IDs and navigation keys remain unchanged.
export function runDate(value?:string, seconds=false):string {
  if(!value)return 'Time not recorded';
  const date=new Date(value);
  if(!Number.isFinite(date.getTime()))return 'Time not recorded';
  return new Intl.DateTimeFormat('en-GB',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',...(seconds?{second:'2-digit' as const}:{}),hourCycle:'h23',timeZone:'Asia/Ho_Chi_Minh'}).format(date);
}
export function runLabel(run:{created_at?:string}) {
  const date=runDate(run.created_at,true);
  return date==='Time not recorded'?'Schedule · Date not recorded':'Schedule · '+date;
}
export function shortRunContext(run:any) {
  const budget=String(run.algo).includes('Micro')?run.search_settings?.micro_time_limit:String(run.algo).includes('CP-SAT')?run.search_settings?.exact_time_limit:run.search_settings?.milp_time_limit;
  return [run.jobs==null?'Job count unavailable':run.jobs+' jobs',budget==null?null:budget+' s budget',run.status].filter(Boolean).join(' · ');
}
