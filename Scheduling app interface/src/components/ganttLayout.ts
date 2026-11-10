import { taskJobIds, type GanttTask } from '../data';
export type VisualOperation = GanttTask & { members:GanttTask[]; lane:number };
export const timestamp=(value:string)=>new Date(value.replace(' ','T')).getTime();
export const operationLabel=(op:string)=>op==='OIL_CYCLE'?'OIL':op==='2C_BATCH'?'2C BATCH':op;

// Presentation only. Source events remain the schedule/table/export authority.
export function layoutOperations(tasks:GanttTask[], groupBy:'Workstation'|'Job'='Workstation') {
  const buckets=new Map<string,GanttTask[]>();
  tasks.forEach((task,index)=>{
    const groupable=['OIL','OIL_CYCLE','2C_BATCH'].includes(task.operation);
    const key=groupable?JSON.stringify([task.workstation,task.operation,[...taskJobIds(task)].sort(),task.status,task.windowId,task.resourceUnits]):'single:'+index;
    const bucket=buckets.get(key)||[];bucket.push(task);buckets.set(key,bucket);
  });
  const groups:VisualOperation[]=[];
  for(const bucket of buckets.values()){
    let current:VisualOperation|undefined;
    for(const task of [...bucket].sort((a,b)=>timestamp(a.startTime)-timestamp(b.startTime)||timestamp(a.finishTime)-timestamp(b.finishTime))){
      // Exact adjacency only: even a small real gap or overlap starts a new bar.
      if(current&&timestamp(current.finishTime)===timestamp(task.startTime)){
        current={...current,finishTime:task.finishTime,endMin:task.endMin,
          processingMin:current.processingMin+task.processingMin,members:[...current.members,task]};
        groups[groups.length-1]=current;
      }else{
        current={...task,members:[task],lane:0};groups.push(current);
      }
    }
  }
  const stations=new Map<string,VisualOperation[]>();
  for(const task of groups) {
    for(const key of groupBy==='Job'?taskJobIds(task):[task.workstation]) {
      const list=stations.get(key)||[];list.push({...task});stations.set(key,list);
    }
  }
  let maxLanes=1;
  for(const list of stations.values()){
    list.sort((a,b)=>timestamp(a.startTime)-timestamp(b.startTime)||timestamp(a.finishTime)-timestamp(b.finishTime));
    const ends:number[]=[];
    for(const task of list){
      let lane=ends.findIndex(end=>end<=timestamp(task.startTime));
      if(lane<0)lane=ends.length;
      ends[lane]=timestamp(task.finishTime);task.lane=lane;
    }
    maxLanes=Math.max(maxLanes,ends.length);
  }
  return {stations,barCount:groups.length,rowHeight:Math.max(56,maxLanes*44+12)};
}
