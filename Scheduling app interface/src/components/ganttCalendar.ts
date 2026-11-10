import type {GanttTask} from '../data';

export interface CalendarWindow {
  station_id:string; window_id:string; calendar_date:string; shift_id:string;
  start_datetime?:string; end_datetime?:string;
}
export interface TimeRange {start:number;end:number}
export interface CalendarGap extends TimeRange {resource:string;kind:'unknown'|'sunday';reason:string}
export const calendarReason = (reason?: string) => reason?.trim() ? reason : 'Reason not recorded in saved calendar';
export interface CalendarCheck {
  overlaps:CalendarGap[];
  weekly:TimeRange[];
  unchecked:string[];
}
export const calendarStamp=(s:string)=>new Date(s.replace(' ','T')).getTime();
const dayStart=(t:number)=>{const d=new Date(t);d.setHours(0,0,0,0);return d.getTime();};
const nextDay=(t:number)=>{const d=new Date(t);d.setDate(d.getDate()+1);return d.getTime();};
export const calendarTime=(t:number)=>{
  const d=new Date(t),pad=(n:number)=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
export const shiftNames=['Shift 1','Shift 2','Shift 3'];
export const shiftHours=['06:00–14:00','14:00–22:00','22:00–06:00'];
export function operationShifts(start:number,end:number) {
  const result:number[]=[];
  for(let at=start;at<end;){
    const d=new Date(at),h=d.getHours(),shift=h>=6&&h<14?0:h>=14&&h<22?1:2;
    result.push(shift);
    if(h<6)d.setHours(6,0,0,0);
    else if(h<14)d.setHours(14,0,0,0);
    else if(h<22)d.setHours(22,0,0,0);
    else {d.setDate(d.getDate()+1);d.setHours(6,0,0,0);}
    at=d.getTime();
  }
  return result;
}

// A separate check against the requested weekly rule, never a replacement calendar.
export function weeklyShutdowns(start:number,end:number) {
  const ranges:TimeRange[]=[];
  for(let day=dayStart(start);day<end;day=nextDay(day)) {
    const weekday=new Date(day).getDay(),finish=new Date(day);
    if(weekday===0)finish.setDate(finish.getDate()+1);
    else if(weekday===1)finish.setHours(6,0,0,0);
    else continue;
    const a=Math.max(start,day),b=Math.min(end,finish.getTime());
    if(a<b)ranges.push({start:a,end:b});
  }
  return ranges;
}

export function indexCalendar(calendar:CalendarWindow[]) {
  const byResource=new Map<string,TimeRange[]>();
  const knownResources=new Set(calendar.map(w=>w.station_id));
  for(const w of calendar) {
    const start=calendarStamp(w.start_datetime||''),end=calendarStamp(w.end_datetime||'');
    if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)continue;
    const list=byResource.get(w.station_id)||[];list.push({start,end});byResource.set(w.station_id,list);
  }
  for(const [resource,windows] of byResource) {
    const merged:TimeRange[]=[];
    for(const w of windows.sort((a,b)=>a.start-b.start)) {
      const last=merged.at(-1);
      // Adjacent shifts are continuous availability, not a production break.
      if(last&&w.start<=last.end)last.end=Math.max(last.end,w.end);
      else merged.push({...w});
    }
    byResource.set(resource,merged);
  }
  function gaps(resource:string,start:number,end:number):CalendarGap[]|null {
    const windows=byResource.get(resource);
    if(!windows)return null;
    // Locate the first potentially overlapping window without scanning 60 days.
    let lo=0,hi=windows.length;
    while(lo<hi){const mid=(lo+hi)>>>1;if(windows[mid].end<=start)lo=mid+1;else hi=mid;}
    const result:CalendarGap[]=[];
    let cursor=start;
    for(let i=lo;i<windows.length&&windows[i].start<end;i++) {
      const w=windows[i];
      if(w.start>cursor)result.push({resource,start:cursor,end:Math.min(w.start,end),kind:'unknown',reason:calendarReason()});
      cursor=Math.max(cursor,w.end);
    }
    if(cursor<end)result.push({resource,start:cursor,end,kind:'unknown',reason:calendarReason()});
    return result;
  }
  function shading(resource:string,start:number,end:number):CalendarGap[] {
    // Purple is the intersection of a saved gap and Sunday 00:00–Monday 06:00.
    // Historical working windows are never shaded or overwritten by the rule.
    return (gaps(resource,start,end)||[]).flatMap(gap=>{
      const sunday:TimeRange[]=[];
      for(const range of weeklyShutdowns(gap.start,gap.end)) {
        const last=sunday.at(-1);
        if(last&&last.end===range.start)last.end=range.end;
        else sunday.push({...range});
      }
      const pieces:CalendarGap[]=[];let cursor=gap.start;
      for(const s of sunday){
        if(cursor<s.start)pieces.push({...gap,start:cursor,end:s.start});
        pieces.push({...gap,...s,kind:'sunday'});cursor=s.end;
      }
      if(cursor<gap.end)pieces.push({...gap,start:cursor});
      return pieces;
    });
  }
  const cache=new WeakMap<GanttTask,CalendarCheck>();
  function check(task:GanttTask):CalendarCheck {
    const cached=cache.get(task);if(cached)return cached;
    const start=calendarStamp(task.startTime),end=calendarStamp(task.finishTime);
    const overlaps:CalendarGap[]=[],unchecked:string[]=[];
    const resources=new Set([task.workstation,...(task.resourceUnits||'').split(';').map(s=>s.trim()).filter(Boolean)]);
    for(const entry of resources){
      const phase=entry.match(/^(.*?)\(([^)]+)\)$/),resource=phase?phase[1]:entry;
      const missing=gaps(resource,start,end);
      if(missing===null){unchecked.push(`${resource}: saved availability unavailable`);continue;}
      // V5 OIL operators occupy only load/unload phases; their timestamps are not
      // in the standardized result. Whole-cycle gaps cannot prove a phase clash.
      if(phase&&missing.length){unchecked.push(`${entry}: phase timestamps not saved`);continue;}
      overlaps.push(...missing);
    }
    if(!task.resourceUnits)unchecked.push('Additional resource assignments not saved');
    const result={overlaps,unchecked,weekly:weeklyShutdowns(start,end)};
    cache.set(task,result);return result;
  }
  return {gaps,shading,check,knownResources,byResource};
}
