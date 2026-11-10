import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { STAGES, JOB_COLORS, taskJobIds, type GanttTask, type Job } from '../data';
import { layoutOperations, operationLabel, type VisualOperation } from './ganttLayout';
import { ZoomIn, ZoomOut, X } from 'lucide-react';
import {calendarReason, indexCalendar, operationShifts, shiftNames, shiftHours, calendarTime, weeklyShutdowns, type CalendarWindow, type CalendarCheck} from './ganttCalendar';

export type {CalendarWindow} from './ganttCalendar';
interface Props {
  tasks?: GanttTask[]; jobs?: Job[]; calendar?: CalendarWindow[];
  horizonStart?: string; horizonEnd?: string;
  calendarPolicy?: string;
  initialJob?: string;
  onViewJob?: (id: string) => void;
}
type ColorMode = 'Job' | 'Operation' | 'Status';
const palette = ['#2563EB','#7C3AED','#0891B2','#059669','#B45309','#4F46E5','#DC2626','#BE185D','#0F766E','#C2410C'];
const statusColors: Record<string,string> = {'Scheduled':'#2563EB','In Progress':'#B45309','Completed':'#059669','Delayed':'#DC2626'};
const stamp = (value: string) => new Date(value.replace(' ', 'T')).getTime();
const midnight=(value:number)=>{const d=new Date(value);d.setHours(0,0,0,0);return d.getTime();};
const addDays=(value:number,count:number)=>{const d=new Date(value);d.setDate(d.getDate()+count);return d.getTime();};
const dateInput=(value:number)=>{const d=new Date(value);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
const unique = (values: string[]) => [...new Set(values)].sort();
const number = (value: number) => value.toLocaleString(undefined,{maximumFractionDigits:2});
function color(key: string, mode: ColorMode) {
  if (mode === 'Status') return statusColors[key] || '#475569';
  if (mode === 'Job' && JOB_COLORS[key]) return JOB_COLORS[key];
  const hash = [...key].reduce((n,c) => (n * 31 + c.charCodeAt(0)) >>> 0, 0);
  return palette[hash % palette.length];
}
function textColor(bg: string) {
  const rgb = [1,3,5].map(i => parseInt(bg.slice(i,i+2),16)/255).map(v => v<=0.04045 ? v/12.92 : ((v+0.055)/1.055)**2.4);
  return rgb[0]*0.2126+rgb[1]*0.7152+rgb[2]*0.0722 > 0.179 ? '#0F172A' : '#FFFFFF';
}
const selectClass = 'border border-slate-200 rounded-md bg-white px-2 py-1 text-xs text-slate-700 max-w-36';
export default function GanttChart({tasks=[],jobs=[],calendar=[],horizonStart,horizonEnd,calendarPolicy,onViewJob,initialJob=''}: Props) {
  const [groupBy,setGroupBy]=useState<'Workstation'|'Job'>('Workstation');
  const [resource,setResource]=useState('All');
  const resourceIds=(t:GanttTask)=>[...new Set([t.workstation,...(t.resourceUnits||'').split(/[;,|]/).map(s=>s.trim()).filter(Boolean)])];
  const [zoom,setZoom] = useState(1);
  const [hourView,setHourView] = useState(false);
  const [viewDays,setViewDays] = useState<1|3|7|null>(1);
  const [viewDate,setViewDate] = useState<number|null>(null);
  const [search,setSearch] = useState(initialJob);
  const [mode,setMode] = useState<ColorMode>('Job');
  const [filters,setFilters] = useState({Job:'All',Workstation:'All',Operation:'All',Shift:'All'});
  const [selected,setSelected] = useState<VisualOperation|null>(null);
  const [selectedJob,setSelectedJob] = useState('');
  const [hover,setHover] = useState<{task?:VisualOperation;details?:string[][];rect:DOMRect}|null>(null);
  const [reviewOpen,setReviewOpen] = useState(false);
  const [reviewLimit,setReviewLimit] = useState(50);
  const [expanded,setExpanded] = useState(false);
  const [compact,setCompact] = useState(false);
  const [fitHeight,setFitHeight] = useState(false);
  const [showFilters,setShowFilters] = useState(true);
  const [showLegend,setShowLegend] = useState(true);
  const expandButton=useRef<HTMLButtonElement>(null);
  useEffect(()=>{
    if(!expanded)return;
    expandButton.current?.focus();
    const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){setExpanded(false);setHover(null);expandButton.current?.focus();}};
    window.addEventListener('keydown',escape);
    return()=>window.removeEventListener('keydown',escape);
  },[expanded]);
  const correctedCalendar=calendarPolicy==='MON_SAT_SUN00_MON06_V1';
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [tooltipPosition,setTooltipPosition] = useState({left:8,top:8});
  useLayoutEffect(()=>{
    if(!hover || !tooltipRef.current) return;
    const {width:w,height:h}=tooltipRef.current.getBoundingClientRect();
    const r=hover.rect, gap=10, edge=8;
    const clampX=(v:number)=>Math.max(edge,Math.min(v,window.innerWidth-w-edge));
    const clampY=(v:number)=>Math.max(edge,Math.min(v,window.innerHeight-h-edge));
    // Measure the rendered content; never clamp a guessed height over the bar.
    let left=clampX(r.left), top=r.bottom+gap;
    if(top+h>window.innerHeight-edge) {
      if(r.top-gap-h>=edge) top=r.top-gap-h;
      else if(r.right+gap+w<=window.innerWidth-edge) {left=r.right+gap;top=clampY(r.top);}
      else if(r.left-gap-w>=edge) {left=r.left-gap-w;top=clampY(r.top);}
      else top=clampY(top);
    }
    setTooltipPosition({left,top});
  },[hover,calendar]);
  useEffect(()=>{
    const dismiss=()=>setHover(null);
    window.addEventListener('resize',dismiss);
    window.addEventListener('scroll',dismiss,true);
    return()=>{window.removeEventListener('resize',dismiss);window.removeEventListener('scroll',dismiss,true);};
  },[]);
  const calendarIndex=useMemo(()=>indexCalendar(calendar),[calendar]);
  const checkText=(check:CalendarCheck)=>check.overlaps.length
    ? `${check.overlaps.length} saved-calendar overlap(s)`
    : check.unchecked.length?'Partial check — resource details missing':'Listed resources continuously available';
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewport,setViewport] = useState(900);
  const [viewportHeight,setViewportHeight] = useState(600);
  const [scrollLeft,setScrollLeft] = useState(0);
  const zoomAnchor=useRef<number|null>(null);
  useEffect(() => {
    const el=scrollRef.current;
    if (!el) return;
    const measure=()=>{setViewport(el.clientWidth);setViewportHeight(el.clientHeight);};
    const observer=new ResizeObserver(measure);
    observer.observe(el); measure();
    return () => observer.disconnect();
  },[]);
  const windowFor=useMemo(()=>{
    const windows=new Map(calendar.map(w=>[w.station_id+'|'+w.window_id,w]));
    const byStation=new Map<string,CalendarWindow[]>();
    for(const w of calendar) {const list=byStation.get(w.station_id)||[];list.push(w);byStation.set(w.station_id,list);}
    const cache=new WeakMap<GanttTask,CalendarWindow|undefined>();
    return (t:GanttTask)=>{
      if(!cache.has(t)) cache.set(t,windows.get(t.workstation+'|'+t.windowId) || byStation.get(t.workstation)?.find(w=>
        w.start_datetime && w.end_datetime && stamp(w.start_datetime)<=stamp(t.startTime) && stamp(w.end_datetime)>=stamp(t.finishTime)));
      return cache.get(t);
    };
  },[calendar]);
  const shift=(t:GanttTask) => windowFor(t)?.shift_id || 'Not specified in saved calendar';
  const shiftSequence=(t:GanttTask)=>operationShifts(stamp(t.startTime),stamp(t.finishTime));
  const fields=(t:GanttTask & {members?:GanttTask[]}) => [
    ['Job ID',t.jobId],['Operation',t.operation],['Workstation',t.workstation],
    ['Calendar Date',windowFor(t)?.calendar_date || t.startTime.slice(0,10)],
    ['Shift',shiftSequence(t).slice(0,6).map(i=>shiftNames[i]).join(' → ')+(shiftSequence(t).length>6?' → …':'')],
    ['Saved shift ID',shift(t)],['Start Time',t.startTime],['Finish Time',t.finishTime],
    ['Processing Time',number(t.processingMin)+' min'],['Status',t.status],
    ['Calendar check',checkText(calendarIndex.check(t))],
    ...(calendarIndex.check(t).weekly.length?[['Weekly rule','Overlaps Sunday shutdown / Monday before 06:00']]:[]),
    ...(t.eventType?[['Event Type',t.eventType]]:[]),
    ...(t.members?[['Saved events',String(t.members.length)],['Event / batch IDs',(t.members.slice(0,6).map(m=>m.eventId).filter(Boolean).join(', ')||'Unavailable')+(t.members.length>6?' (more in details)':'')]]:[]),
  ];
  const options=useMemo(()=>({Job:unique(tasks.flatMap(taskJobIds)),Workstation:unique(tasks.map(t=>t.workstation)),Operation:unique(tasks.map(t=>t.operation)),Shift:unique([...shiftNames,...tasks.map(shift)])}),[tasks,windowFor]);
  const visible=useMemo(()=>tasks.filter(t=>(resource==='All'||resourceIds(t).includes(resource))&&(filters.Job==='All'||taskJobIds(t).includes(filters.Job))&&(filters.Workstation==='All'||t.workstation===filters.Workstation)&&(filters.Operation==='All'||t.operation===filters.Operation)&&(filters.Shift==='All'||(shiftNames.includes(filters.Shift)?shiftSequence(t).some(i=>shiftNames[i]===filters.Shift):shift(t)===filters.Shift))),[tasks,filters,calendar,resource]);
  const review=useMemo(()=>{
    const records=visible.map(task=>({task,check:calendarIndex.check(task)}));
    return {records:records.filter(r=>r.check.overlaps.length||r.check.weekly.length||r.check.unchecked.length),
      overlaps:records.filter(r=>r.check.overlaps.length).length,weekly:records.filter(r=>r.check.weekly.length).length,
      partial:records.filter(r=>r.check.unchecked.length).length};
  },[visible,calendarIndex]);
  const weeklyCalendarConflicts=useMemo(()=>[...calendarIndex.byResource].filter(([,windows])=>windows.some(w=>weeklyShutdowns(w.start,w.end).length)).map(([resource])=>resource),[calendarIndex]);
  const layout=useMemo(()=>layoutOperations(visible,groupBy),[visible,groupBy]);
  const stages=useMemo(()=>[...layout.stations.keys()].sort((a,b)=>groupBy==='Job'?a.localeCompare(b,undefined,{numeric:true}):STAGES.indexOf(tasks.find(t=>t.workstation===a)!.operation)-STAGES.indexOf(tasks.find(t=>t.workstation===b)!.operation)||a.localeCompare(b)),[layout,tasks,groupBy]);
  const rowGeometry=useMemo(()=>{
    const dense=compact||fitHeight,barHeight=dense?26:36,lanePitch=dense?32:44;
    const target=fitHeight?Math.max(40,Math.min(48,Math.floor((viewportHeight-72)/Math.max(1,stages.length)))):44;
    const rows=new Map<string,{height:number;top:number}>();let top=0;
    for(const station of stages){
      const lanes=(layout.stations.get(station)||[]).reduce((n,t)=>Math.max(n,t.lane+1),1);
      // Keep overlapping operations on distinct lanes; never squeeze them together.
      const height=dense?Math.max(target,lanes*lanePitch+8):layout.rowHeight;
      rows.set(station,{height,top});top+=height;
    }
    return {rows,barHeight,lanePitch,inset:dense?6:10};
  },[compact,fitHeight,viewportHeight,stages,layout]);
  const [minStart,maxFinish]=useMemo(()=>tasks.reduce(([lo,hi],t)=>[Math.min(lo,stamp(t.startTime)),Math.max(hi,stamp(t.finishTime))],[Infinity,-Infinity]),[tasks]);
  const horizonLo=stamp(horizonStart||'') || (tasks.length?minStart:0);
  const horizonHi=stamp(horizonEnd||'') || (tasks.length?maxFinish:86400000);
  const firstDay=midnight(horizonLo),lastDay=midnight(horizonHi-1);
  const clampDay=(value:number)=>Math.max(firstDay,Math.min(lastDay,midnight(value)));
  const activeDay=clampDay(viewDate??(tasks.length?minStart:horizonLo));
  const padding=Math.min(60*60000,Math.max(15*60000,(maxFinish-minStart)*0.03));
  // Hourly view starts at actual work. Fit includes the saved planning horizon.
  const startMs=viewDays?activeDay:tasks.length?(hourView?minStart-padding:Math.min(minStart-padding,horizonLo)):0;
  const endMs=viewDays?Math.min(addDays(activeDay,viewDays),addDays(lastDay,1)):tasks.length?(hourView?maxFinish+padding:Math.max(maxFinish+padding,horizonHi)):3600000;
  const duration=(endMs-startMs)/60000;
  const width=Math.max(1,viewport-136,hourView?duration*1.5:0)*zoom;
  const scale=width/duration;
  // All timeline layers use this origin and scale. Only 1px guides snap to pixels.
  const timeToPixel=(time:number)=>(time-startMs)/60000*scale;
  const x=(time:string)=>timeToPixel(stamp(time));
  const barTimes=useMemo(()=>{
    const times=new Map<VisualOperation,{start:number;end:number;previousEnd:number;nextStart:number}>();
    for(const station of layout.stations.values()) {
      const previous=new Map<number,VisualOperation>();
      for(const task of station) {
        const prior=previous.get(task.lane),before=prior?times.get(prior):undefined;
        const start=stamp(task.startTime),end=stamp(task.finishTime);
        times.set(task,{start,end,previousEnd:before?.end??-Infinity,nextStart:Infinity});
        if(before) before.nextStart=start;
        previous.set(task.lane,task);
      }
    }
    return times;
  },[layout]);
  const virtualized=tasks.length>1000;
  const inViewport=(start:number,end:number)=>!virtualized || (end>=scrollLeft-viewport && start<=scrollLeft+viewport*2);
  const windowCount=useMemo(()=>visible.filter(t=>stamp(t.finishTime)>startMs && stamp(t.startTime)<endMs).length,[visible,startMs,endMs]);
  // Shade calendar gaps per workstation. Missing calendar data is not a day off.
  const nonWorking=useMemo(()=>new Map(stages.map(station=>[station,groupBy==='Workstation'?calendarIndex.shading(station,startMs,endMs):[]])),[calendarIndex,stages,startMs,endMs,groupBy]);
  function showDay(value:number,count:1|3|7=viewDays||1) {
    setViewDate(clampDay(value));setViewDays(count);setHourView(false);setZoom(1);setHover(null);
    zoomAnchor.current=null;scrollRef.current?.scrollTo({left:0});
  }
  function changeZoom(factor:number) {
    zoomAnchor.current=startMs+((scrollRef.current?.scrollLeft||0)+Math.max(1,viewport-136)/2)/scale*60000;
    setZoom(z=>Math.min(16,Math.max(1,z*factor)));
  }
  function fitSchedule() {
    setViewDays(null);
    setHourView(false);
    if(scrollRef.current) {
      setViewport(scrollRef.current.clientWidth);
      scrollRef.current.scrollTo({left:0});
    }
    setZoom(1);
  }
  useEffect(()=>{setZoom(1);setViewDate(null);},[minStart,maxFinish]);
  // Navigation changes only the viewport, never the standardized event array.
  useLayoutEffect(()=>{
    const el=scrollRef.current;
    if(!el) return;
    const query=search.trim().toLowerCase();
    const matches=visible.filter(t=>query?taskJobIds(t).some(id=>id.toLowerCase().includes(query)):selected?taskJobIds(t).includes(selectedJob):true);
    const first=matches.reduce<GanttTask|null>((a,t)=>!a||stamp(t.startTime)<stamp(a.startTime)?t:a,null);
    const navigating=!!query || filters.Job!=='All' || !!selected;
    const target=selected&&!query&&filters.Job==='All'&&taskJobIds(selected).includes(selectedJob)?selected:first;
    if(target && navigating) {
      if(viewDays && !(stamp(target.finishTime)>startMs && stamp(target.startTime)<endMs)) setViewDate(clampDay(stamp(target.startTime)));
      el.scrollLeft=Math.max(0,x(target.startTime)-24);
      el.scrollTop=rowGeometry.rows.get(groupBy==='Job'?taskJobIds(target)[0]:target.workstation)?.top||0;
    }
    setScrollLeft(el.scrollLeft);
  },[tasks,search,filters,selected,selectedJob,groupBy]);
  useLayoutEffect(()=>{
    const el=scrollRef.current;if(!el) return;
    if(zoomAnchor.current!==null) {
      el.scrollLeft=Math.max(0,(zoomAnchor.current-startMs)/60000*scale-Math.max(1,viewport-136)/2);
      zoomAnchor.current=null;
    } else if(viewDays || !hourView) el.scrollLeft=0;
    else el.scrollLeft=Math.max(0,(minStart-startMs)/60000*scale-24);
    setScrollLeft(el.scrollLeft);setHover(null);
  },[startMs,endMs,hourView,viewDays,zoom,viewport]);
  // Tick density follows the visible duration; positions stay calendar aligned.
  const visibleMinutes=Math.max(1,viewport-136)/scale;
  const candidates=visibleMinutes<=480?[30,60]:visibleMinutes<=2880?[60,120]:visibleMinutes<=10080?[240,360]:[1440,2880,10080];
  const interval=hourView?60:(candidates.find(n=>n*scale>=48)||candidates[candidates.length-1]);
  const anchor=new Date(startMs); anchor.setHours(0,0,0,0);
  const ticks:number[]=[];
  for(let at=anchor.getTime();at<=endMs;at+=interval*60000) if(at>=startMs) ticks.push(at);
  // Avoid rasterizing a repeating gradient across the entire 60-day canvas.
  // Hourly lines remain independent of label density; overview lines thin out.
  const gridInterval=60*scale>=6?60:interval;
  const gridLines:{at:number;left:number;shift:boolean}[]=[];
  const gridStart=Math.max(startMs,startMs+(scrollLeft-viewport)/scale*60000);
  const gridEnd=Math.min(endMs,startMs+(scrollLeft+viewport*2)/scale*60000);
  for(let at=anchor.getTime()+Math.ceil((gridStart-anchor.getTime())/(gridInterval*60000))*gridInterval*60000;at<=gridEnd;at+=gridInterval*60000) {
    gridLines.push({at,left:Math.round(timeToPixel(at)),shift:gridInterval===60&&[6,14,22].includes(new Date(at).getHours())});
  }
  const days:{at:number;end:number;label:string}[]=[];
  const day=new Date(startMs); day.setHours(0,0,0,0);
  while(day.getTime()<endMs) {
    const at=Math.max(startMs,day.getTime());
    const label=day.toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'});
    day.setDate(day.getDate()+1);
    days.push({at,end:Math.min(endMs,day.getTime()),label});
  }
  const highlighted=(t:GanttTask)=>search.trim()?taskJobIds(t).some(id=>id.toLowerCase().includes(search.trim().toLowerCase())):!selected||taskJobIds(t).includes(selectedJob);
  const keyFor=(t:GanttTask)=>mode==='Job'?taskJobIds(t)[0]:mode==='Operation'?t.operation:t.status;
  const legends=useMemo(()=>mode==='Status'?Object.keys(statusColors):unique(mode==='Job'?visible.flatMap(taskJobIds):visible.map(keyFor)),[mode,visible]);
  const route=selected?tasks.filter(t=>taskJobIds(t).includes(selectedJob)).slice().sort((a,b)=>a.startMin-b.startMin):[];
  const job=jobs.find(j=>j.id===selectedJob);
  const completion=route.reduce<GanttTask|null>((last,t)=>!last||t.endMin>last.endMin?t:last,null);
  const completionMs=completion?stamp(completion.finishTime):NaN;
  const releaseMs=job?.releaseTime?stamp(job.releaseTime):NaN;
  const dueMs=job?.dueDate?stamp(job.dueDate):NaN;
  const summary=[
    ['Release Time',job?.releaseTime||'Unavailable'],
    ['Completion Time',completion?.finishTime||'Unavailable'],
    ['Flow Time',Number.isFinite(releaseMs)?number((completionMs-releaseMs)/60000)+' min':'Unavailable'],
    ['Due Date',job?.dueDate||'Unavailable'],
    ['Tardiness',Number.isFinite(dueMs)?number(Math.max(0,(completionMs-dueMs)/60000))+' min':'Unavailable'],
  ];
  function reset() {setSearch('');setSelected(null);setResource('All');setFilters({Job:'All',Workstation:'All',Operation:'All',Shift:'All'});}
  const reviewIdentity=(task:GanttTask)=><div><button className="text-blue-600 underline" onClick={()=>{setSearch(taskJobIds(task)[0]);setSelected(null);showDay(stamp(task.startTime));}}>{task.jobId} · {task.operation} · {task.eventId}</button><span className="ml-2">{task.workstation} · {task.startTime} → {task.finishTime}</span></div>;
  return <div data-testid="gantt-chart" data-expanded={expanded} className={'flex flex-col min-h-0 bg-white '+(expanded?'fixed inset-0 z-40':'h-full')}>
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-slate-200">
      <h2 className="font-semibold text-sm">Gantt Chart — By {groupBy}</h2>
      <div className="flex rounded-md border border-slate-200 p-0.5 text-xs">{(['Workstation','Job'] as const).map(g=><button key={g} aria-pressed={groupBy===g} onClick={()=>{setGroupBy(g);setHover(null);}} className="px-3 py-1 rounded aria-pressed:bg-blue-600 aria-pressed:text-white">By {g}</button>)}</div>
      {groupBy==='Job'&&<p className="text-[10px] text-slate-500 w-full">Shared batches appear on each member Job row. Resource calendar shading is available in Workstation view; calendar checks remain in tooltips.</p>}
    </header>
    <div className="shrink-0 max-h-[50%] overflow-y-auto">
    <div className="flex flex-wrap items-center gap-2 px-4 py-1.5 border-b border-slate-100 text-xs">
      <button disabled={activeDay<=firstDay} onClick={()=>showDay(addDays(activeDay,-1))} className="border border-slate-200 rounded px-2 py-1 disabled:opacity-30">Previous Day</button>
      <input aria-label="Gantt Date" type="date" value={dateInput(activeDay)} min={dateInput(firstDay)} max={dateInput(lastDay)} onChange={e=>{if(e.target.value)showDay(stamp(e.target.value+'T00:00:00'));}} className="border border-slate-200 rounded px-2 py-1"/>
      <button disabled={activeDay>=lastDay} onClick={()=>showDay(addDays(activeDay,1))} className="border border-slate-200 rounded px-2 py-1 disabled:opacity-30">Next Day</button>
      {([1,3,7] as const).map(n=><button key={n} aria-pressed={viewDays===n} onClick={()=>showDay(activeDay,n)} className="border border-slate-200 rounded px-2 py-1 aria-pressed:bg-blue-50 aria-pressed:border-blue-400">{n} {n===1?'Day':'Days'}</button>)}
      <span className="text-slate-500 hidden 2xl:inline">{viewDays===1?'00:00–24:00 · ':''}Local calendar days</span>
      <div className="ml-auto flex flex-wrap gap-1">
        <button aria-expanded={showFilters} onClick={()=>{setShowFilters(v=>!v);setHover(null);}} className="border border-slate-200 rounded px-2 py-1">Filters{Object.values(filters).some(v=>v!=='All')||search?' •':''}</button>
        <button aria-expanded={showLegend} onClick={()=>{setShowLegend(v=>!v);setHover(null);}} className="border border-slate-200 rounded px-2 py-1">Legend</button>
        <button aria-pressed={compact} onClick={()=>{setCompact(v=>!v);setFitHeight(false);setHover(null);}} className="border border-slate-200 rounded px-2 py-1 aria-pressed:bg-blue-50">Compact View</button>
        <button aria-pressed={fitHeight} onClick={()=>{setFitHeight(v=>!v);setHover(null);}} className="border border-slate-200 rounded px-2 py-1 aria-pressed:bg-blue-50">Fit Height</button>
        <button ref={expandButton} aria-label={expanded?'Exit expanded Gantt':'Expand Gantt'} aria-pressed={expanded} onClick={()=>{zoomAnchor.current=startMs+((scrollRef.current?.scrollLeft||0)+Math.max(1,viewport-136)/2)/scale*60000;setExpanded(v=>!v);setHover(null);}} className="border border-blue-200 text-blue-600 rounded px-2 py-1">{expanded?'Exit expanded':'Expand'}</button>
      </div>
    </div>
    <div hidden={!showFilters} className={showFilters?'flex flex-wrap items-center gap-2 px-4 py-1.5 border-b border-slate-100':'hidden'}>
      <label className="text-xs text-slate-500">Resource <select aria-label="Resource filter" className={selectClass} value={resource} onChange={e=>setResource(e.target.value)}><option>All</option>{unique(tasks.flatMap(resourceIds)).map(r=><option key={r}>{r}</option>)}</select></label>
      <label className="text-xs text-slate-500 flex gap-2 items-center">Search Job <input aria-label="Search Job" placeholder="Job ID" value={search} onChange={e=>{setSearch(e.target.value);setSelected(null);}} className="border border-slate-200 rounded-md px-2 py-1 w-28 text-slate-700"/></label>
      <button onClick={reset} className="text-xs text-[#2563EB] px-2 py-1">Clear / Reset</button>
      {(Object.keys(filters) as (keyof typeof filters)[]).map(key=><label key={key} className="text-xs text-slate-500 flex gap-1 items-center">{key}<select aria-label={key+' filter'} value={filters[key]} onChange={e=>setFilters({...filters,[key]:e.target.value})} className={selectClass}><option>All</option>{options[key].map(v=><option key={v}>{v}</option>)}</select></label>)}
    </div>
    <div className="flex flex-wrap items-center gap-2 px-4 py-1.5 border-b border-slate-100">
      <label className="text-xs text-slate-500 flex gap-2 items-center">Color by<select aria-label="Color by" className={selectClass} value={mode} onChange={e=>setMode(e.target.value as ColorMode)}>{['Job','Operation','Status'].map(v=><option key={v}>{v}</option>)}</select></label>
      <span className="text-xs text-slate-400 ml-3">Zoom</span>
      <button aria-label="Zoom out" disabled={zoom<=1} onClick={()=>changeZoom(0.5)} className="p-1.5 border border-slate-200 rounded disabled:opacity-30"><ZoomOut size={14}/></button>
      <span data-testid="zoom-level" className="text-xs text-slate-600 w-10 text-center">{Math.round(zoom*100)}%</span>
      <button aria-label="Zoom in" disabled={zoom>=16} onClick={()=>changeZoom(2)} className="p-1.5 border border-slate-200 rounded disabled:opacity-30"><ZoomIn size={14}/></button>
      <button aria-pressed={hourView} onClick={()=>{setViewDays(null);setHourView(true);setZoom(1);scrollRef.current?.scrollTo({left:0});}} className="text-xs border border-slate-200 rounded px-2 py-1">Hourly view</button>
      <button onClick={fitSchedule} className="text-xs border border-slate-200 rounded px-2 py-1">Fit Schedule</button>
      <span className="text-xs text-slate-400 hidden 2xl:inline">Local hours · scroll horizontally to follow jobs</span>
      <span className="text-xs text-slate-400">{layout.barCount} bars; contiguous cycles grouped</span>
      <span data-testid="operation-count" className="ml-auto text-xs text-slate-500">{visible.length} / {tasks.length} operations</span>
      {viewDays&&<span data-testid="window-operation-count" className="text-xs text-slate-500">{windowCount} in selected days</span>}
    </div>
    <div hidden={!showLegend}>
    <div aria-label={mode+' color legend'} className="flex gap-3 px-4 py-1.5 overflow-x-auto shrink-0 border-b border-slate-100 text-xs text-slate-600">
      {legends.map(key=><span key={key} className="flex items-center gap-1.5 whitespace-nowrap"><span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{backgroundColor:color(key,mode)}}/>{key}</span>)}
    </div>
    <div aria-label="Calendar legend" className="flex flex-wrap gap-x-4 gap-y-1 px-4 py-1.5 shrink-0 border-b border-slate-100 text-[11px] text-slate-600">
      <span className="flex items-center gap-1"><span className="w-3 h-3 bg-slate-200/40 border border-slate-200"/>Unavailable (saved calendar)</span>
      <span className="flex items-center gap-1" title="Saved unavailability during Sunday 00:00–Monday 06:00"><span className="w-3 h-3 bg-violet-100 border border-violet-200"/>Sunday Shutdown</span>
      <span className="flex items-center gap-1"><span className="h-3 border-l border-slate-400"/>Shift Boundary</span>
      <span className="whitespace-nowrap">{shiftNames.map((name,i)=>`${name} (${shiftHours[i]})`).join(' · ')}</span>
    </div>
    </div>
    <details data-testid="calendar-review" className="shrink-0 px-4 py-1.5 border-b border-slate-100 text-xs text-slate-600" onToggle={e=>setReviewOpen(e.currentTarget.open)}>
      <summary className="cursor-pointer">Calendar Review <span data-testid="weekly-conflict-badge" className={'ml-2 rounded-full px-2 py-0.5 '+(review.weekly?'bg-amber-50 text-amber-800':'bg-slate-100 text-slate-600')}>{review.weekly} weekly-rule conflicts</span><span className="ml-2 text-slate-400">Filtered operations</span></summary>
      {reviewOpen&&<div className="max-h-44 overflow-auto py-2 space-y-2">
        <p>{correctedCalendar?'Generated with the corrected calendar. Any overlap or weekly-rule conflict below is an error requiring review.':'Historical / unversioned calendar: conflicts are reported against the weekly rule; saved events and availability remain unchanged.'} Crossing a shift boundary alone is allowed.</p>
        {!!weeklyCalendarConflicts.length&&<p className="text-amber-700">Saved availability conflicts with the weekly rule for: {weeklyCalendarConflicts.join(', ')}. These conflicts are reported separately from saved-calendar overlaps.</p>}
        {(['overlaps','weekly','unchecked'] as const).map((kind,index)=>{
          const records=review.records.filter(r=>r.check[kind].length);
          return <section key={kind} aria-label={['Saved-calendar overlaps','Weekly-rule conflicts','Incomplete checks'][index]} className="border-t border-slate-100 pt-1">
            <strong>{['Saved-calendar overlaps','Weekly-rule conflicts','Incomplete checks'][index]} ({records.length})</strong>
            {!records.length&&<p>None detected.</p>}
            {records.slice(0,reviewLimit).map(({task,check},i)=><div key={task.eventId||i} className="py-1">
              {reviewIdentity(task)}
              {kind==='overlaps'&&check.overlaps.map((g,j)=><p key={j}>Saved-calendar overlap: {g.resource} · {calendarTime(g.start)} → {calendarTime(g.end)} · {g.reason}</p>)}
              {kind==='weekly'&&<><p>Resources listed: {task.resourceUnits||task.workstation}</p>{check.weekly.map((g,j)=><p key={j}>Weekly-rule conflict: {calendarTime(g.start)} → {calendarTime(g.end)}</p>)}</>}
              {kind==='unchecked'&&check.unchecked.map((reason,j)=><p key={j}>Not fully checked: {reason}</p>)}
            </div>)}
            {records.length>reviewLimit&&<button className="text-blue-600 underline" onClick={()=>setReviewLimit(n=>n+50)}>Show more ({records.length-reviewLimit} remaining)</button>}
          </section>;
        })}
      </div>}
    </details>
    </div>
    <div className="flex flex-1 min-h-0 overflow-hidden">
      <div ref={scrollRef} data-testid="gantt-scroll" data-domain-start={startMs} data-domain-end={endMs} className="flex-1 min-w-0 overflow-auto" onScroll={e=>{setHover(null);setScrollLeft(e.currentTarget.scrollLeft);}}>
        <div style={{width:136+width,minWidth:'100%'}}>
          <div data-testid="gantt-header" className="flex sticky top-0 z-20 bg-white border-b border-slate-200 h-14">
            <div className="sticky left-0 z-30 bg-[#F8FAFC] border-r border-slate-200 flex items-center px-3 text-xs font-semibold text-slate-500 shrink-0" style={{width:136}}>{groupBy}</div>
            <div className="relative overflow-hidden" style={{width}}>
              {days.filter(d=>inViewport(timeToPixel(d.at),timeToPixel(d.end))).map(d=><div key={d.at} className="absolute top-0 h-7 border-l border-b border-slate-100 text-xs font-semibold text-slate-600 px-2 py-1 overflow-hidden whitespace-nowrap" style={{left:timeToPixel(d.at),width:timeToPixel(d.end)-timeToPixel(d.at)}}>{(d.end-d.at)/60000*scale>=56?<span className="relative" style={{left:Math.max(0,Math.min((d.end-d.at)/60000*scale-56,scrollLeft-timeToPixel(d.at)))}}>{d.label}</span>:null}</div>)}
              {ticks.filter(at=>inViewport(timeToPixel(at),timeToPixel(at))).map(at=><span data-testid="axis-tick" data-time={at} key={at} className="absolute top-8 text-[11px] text-slate-500 pl-1 whitespace-nowrap" style={{left:Math.round(timeToPixel(at))}}>{interval>=1440?new Date(at).toLocaleDateString(undefined,{month:'short',day:'numeric'}):new Date(at).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',hour12:false})}</span>)}
            </div>
          </div>
          {stages.map((station,i)=><div key={station} className="flex border-b border-slate-200" data-testid="gantt-row" style={{height:rowGeometry.rows.get(station)!.height,background:i%2?'var(--gantt-row-odd, #FFFFFF)':'var(--gantt-row-even, #F8FAFC)'}}>
            <div data-testid="station-label" className="sticky left-0 z-10 shrink-0 flex items-center px-3 border-r border-slate-200 text-xs font-semibold text-slate-600" style={{width:136,background:i%2?'var(--gantt-row-odd, #FFFFFF)':'var(--gantt-row-even, #F8FAFC)'}}>{station}</div>
            <div className="relative overflow-hidden" style={{width}}>
              {(nonWorking.get(station)||[]).filter(g=>inViewport(timeToPixel(g.start),timeToPixel(g.end))).map(g=><div key={g.start} data-testid="non-working-time" data-start={g.start} data-end={g.end} data-resource={station} data-kind={g.kind} aria-label="Non-working calendar time" className={'absolute inset-y-0 '+(g.kind==='sunday'?'bg-violet-100/70':'bg-slate-200/40')} style={{left:timeToPixel(g.start),width:timeToPixel(g.end)-timeToPixel(g.start)}}
                onMouseEnter={e=>setHover({rect:e.currentTarget.getBoundingClientRect(),details:[['Calendar',g.kind==='sunday'?'Sunday shutdown':'Unavailable according to saved calendar'],['Reason',calendarReason(g.reason)],['Resource',station],['Start Time',calendarTime(g.start)],['Finish Time',calendarTime(g.end)]]})} onMouseLeave={()=>setHover(null)}/>)}
              {gridLines.map(line=>{const boundary=line.shift&&(!calendarIndex.byResource.has(station)||calendarIndex.byResource.get(station)!.some(w=>w.start<=line.at&&line.at<=w.end));return <div key={line.at} data-testid="gantt-grid-line" data-time={line.at} data-shift-boundary={boundary} aria-hidden="true" className="absolute inset-y-0 pointer-events-none" style={{left:line.left,width:1,backgroundColor:boundary?'#CBD5E1':'#E2E8F0'}}>{boundary&&<span className="absolute inset-y-0 -left-1 w-2 pointer-events-auto" onMouseEnter={e=>{const next=operationShifts(line.at,line.at+1)[0];setHover({rect:e.currentTarget.getBoundingClientRect(),details:[['Calendar','Shift boundary'],['Resource',station],['Time',calendarTime(line.at)],['Shift',`${shiftNames[next]} (${shiftHours[next]})`],['Availability','A boundary alone does not interrupt work']]});}} onMouseLeave={()=>setHover(null)}/>}</div>;})}
              {(layout.stations.get(station)||[]).map((task,index)=>{
                const times=barTimes.get(task)!;
                if(times.end<=startMs || times.start>=endMs) return null;
                const left=timeToPixel(Math.max(times.start,startMs));
                const barWidth=timeToPixel(Math.min(times.end,endMs))-left;
                if(!inViewport(left,left+barWidth)) return null;
                // Expand into empty space only, never over a neighboring event.
                const extra=Math.max(0,(8-barWidth)/2);
                const hitBefore=Math.max(0,Math.min(extra,left,(left-timeToPixel(times.previousEnd))/2));
                const hitAfter=Math.max(0,Math.min(extra,width-left-barWidth,(timeToPixel(times.nextStart)-left-barWidth)/2));
                const full=task.jobId+' \u00b7 '+operationLabel(task.operation);
                const label=barWidth>=full.length*6+10?full:barWidth>=task.jobId.length*6+8?task.jobId:'';
                const bg=color(keyFor(task),mode);
                return <button key={task.jobId+'|'+task.operation+'|'+index} data-testid="gantt-bar" data-job={task.jobId} data-operation={task.operation} data-start={task.startTime} data-finish={task.finishTime} data-processing={task.processingMin} data-event-count={task.members.length} data-event-ids={JSON.stringify(task.members.map(m=>m.eventId))} data-lane={task.lane} data-highlighted={highlighted(task)} aria-label={fields(task).map(([k,v])=>k+': '+v).join('; ')}
                  data-hover-related={hover?.task?taskJobIds(task).some(id=>taskJobIds(hover.task!).includes(id)):undefined}
                  className="absolute h-9 rounded-md whitespace-nowrap hover:brightness-110 transition-[filter,opacity] text-[11px] font-semibold text-center cursor-pointer focus-visible:outline-2 focus-visible:outline-slate-900"
                  style={{top:rowGeometry.inset+task.lane*rowGeometry.lanePitch,height:rowGeometry.barHeight,borderRadius:6,left,width:barWidth,backgroundColor:bg,color:textColor(bg),opacity:highlighted(task)?1:0.18,boxShadow:selected && taskJobIds(task).includes(selectedJob)?'inset 0 0 0 2px #0F172A':'0 1px 2px #0F172A12'}}
                  onClick={()=>{setSelected(task);setSelectedJob(taskJobIds(task)[0]);setSearch('');setHover(null);}}
                  onMouseEnter={e=>setHover({task,rect:e.currentTarget.getBoundingClientRect()})}
                  onFocus={e=>setHover({task,rect:e.currentTarget.getBoundingClientRect()})}
                  onBlur={()=>setHover(null)}
                  onMouseLeave={()=>setHover(null)}><span aria-hidden="true" className="absolute inset-y-0" style={{left:-hitBefore,width:barWidth+hitBefore+hitAfter}}/><span className="block overflow-hidden">{label}</span>{mode==='Job'&&taskJobIds(task).length>1&&barWidth>=20&&<span aria-hidden="true" className="absolute bottom-0 left-0 right-0 flex h-1 overflow-hidden rounded-b-md">{taskJobIds(task).map(id=><span key={id} className="flex-1" style={{backgroundColor:color(id,'Job')}}/>)}</span>}</button>;
              })}
            </div>
          </div>)}
          {!visible.length&&<p className="p-6 text-sm text-slate-500">No operations match these filters.</p>}
          {!!visible.length&&!windowCount&&<p className="p-4 text-xs text-slate-500">No operations in the selected time window. Choose another day or Fit Schedule.</p>}
        </div>
      </div>
      {selected&&<aside aria-label="Job Details" className="w-80 max-w-[45%] shrink-0 border-l border-slate-200 bg-white overflow-y-auto text-xs">
        <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center"><div><strong className="text-sm text-slate-900">Job Details · {selectedJob}</strong><p className="text-slate-500 mt-1">{selected.product}</p></div><button aria-label="Close job details" onClick={()=>setSelected(null)}><X size={16}/></button></div>
        {taskJobIds(selected).length>1 && <div className="px-4 pt-3 flex gap-2">{taskJobIds(selected).map(id=><button className="text-blue-600 underline" key={id} onClick={()=>setSelectedJob(id)}>{id}</button>)}</div>}
        <dl className="p-4 space-y-2">{summary.map(([k,v])=><div key={k}><dt className="text-slate-500">{k}</dt><dd className="text-slate-800 break-words">{v}</dd></div>)}</dl>
        <p className="px-4 text-[11px] text-slate-400">Flow time and tardiness summarize saved completion, release and due times.</p>
        {onViewJob&&<button className="mx-4 my-3 text-[#2563EB] font-medium" onClick={()=>onViewJob(selectedJob)}>View in Schedule Table</button>}
        <div className="px-4 py-3 border-t border-slate-100"><strong>Selected operation</strong><dl className="mt-2 space-y-2">{fields(selected).map(([k,v])=><div key={k}><dt className="text-slate-500">{k}</dt><dd className="break-words">{v}</dd></div>)}</dl></div>
        {selected.members.length>1&&<div className="px-4 py-3 border-t border-slate-100"><strong>Saved events in this bar ({selected.members.length})</strong><ol className="mt-2 space-y-2">{selected.members.map((member,i)=><li key={member.eventId||i} className="text-slate-600">{member.eventId||member.operation}<br/>{member.startTime} &rarr; {member.finishTime}<br/>{number(member.processingMin)} min</li>)}</ol></div>}
        <div className="px-4 py-3 border-t border-slate-100"><strong>Scheduled route</strong><ol className="mt-3 border-l border-blue-200 pl-3 space-y-3">{route.map((t,i)=><li key={i}><div className="font-medium text-slate-700">{t.operation} · {t.workstation}</div><div className="text-slate-500 mt-1">{t.startTime}<br/>→ {t.finishTime}</div></li>)}</ol></div>
      </aside>}
    </div>
    {hover&&<div ref={tooltipRef} role="tooltip" className="fixed z-50 pointer-events-none w-80 max-w-[calc(100vw-16px)] bg-white border border-slate-200 rounded-lg shadow-xl p-3 text-xs" style={tooltipPosition}><dl className="space-y-1.5">{(hover.task?fields(hover.task).filter(([k])=>!['Event / batch IDs','Event Type'].includes(k)):hover.details||[]).map(([k,v])=><div className="flex gap-3 justify-between" key={k}><dt className="text-slate-500 shrink-0">{k}</dt><dd className="text-slate-900 text-right break-words min-w-0">{v}</dd></div>)}</dl>{hover.task&&<p className="text-slate-500 mt-2">Click for full event details</p>}</div>}
  </div>;
}
