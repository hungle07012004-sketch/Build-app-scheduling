import {runLabel,shortRunContext} from '../runLabels';
import { algorithmName } from '../algorithms';
import { useState } from 'react';
import { Plus, Layers, Cpu, ArrowUpRight } from 'lucide-react';
import { Panel, MetricCard, metric } from '../components/aps';
import RunHistory, {runContext} from '../components/RunHistory';
import { MakespanTrend } from './AnalyticsPage';
import { useAppData } from '../AppData';
import { WorkstationData } from '../data';
import StatusChip from '../components/StatusChip';
import { AlertCircle, Clock } from 'lucide-react';

interface DashboardPageProps {
  onCreateSchedule: () => void;
  onViewResults: () => void;
}

// Simple SVG bar chart
function UtilizationBar({ value, label }: { value: number | null; label: string }) {
  const color = 'linear-gradient(90deg, #FF7900, #F5C542)';
  return (
    <div className="min-w-0">
      <div className="flex items-start justify-between gap-3 mb-1">
        <div className="min-w-0 break-words text-xs text-[#64748B]" title={label}>{label}</div>
        <div className="shrink-0 text-xs font-semibold text-[#0F172A] text-right tabular-nums">{value === null ? '\u2014' : `${value.toFixed(2)}%`}</div>
      </div>
      <div className="h-5 bg-slate-100 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${value ?? 0}%`, background: color }}
        />
      </div>
    </div>
  );
}

// Simple donut
function DonutChart({ data }: { data: { label: string; value: number; color: string }[] }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  let cumAngle = -90;
  const cx = 60, cy = 60, r = 45, iR = 30;
  const paths = data.map(d => {
    const angle = (d.value / (total || 1)) * 359.999;
    const start = cumAngle;
    cumAngle += angle;
    const toRad = (deg: number) => (deg * Math.PI) / 180;
    const x1 = cx + r * Math.cos(toRad(start));
    const y1 = cy + r * Math.sin(toRad(start));
    const x2 = cx + r * Math.cos(toRad(start + angle));
    const y2 = cy + r * Math.sin(toRad(start + angle));
    const x3 = cx + iR * Math.cos(toRad(start + angle));
    const y3 = cy + iR * Math.sin(toRad(start + angle));
    const x4 = cx + iR * Math.cos(toRad(start));
    const y4 = cy + iR * Math.sin(toRad(start));
    const large = angle > 180 ? 1 : 0;
    return {
      d: `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${iR} ${iR} 0 ${large} 0 ${x4} ${y4} Z`,
      color: d.color,
      label: d.label,
      value: d.value,
    };
  });

  return (
    <div className="flex items-center gap-6">
      <svg width={120} height={120} viewBox="0 0 120 120">
        {paths.map((p, i) => (
          <path key={i} d={p.d} fill={p.color} />
        ))}
        <text x={60} y={56} textAnchor="middle" fontSize={18} fontWeight={700} fill="#0F172A">{total}</text>
        <text x={60} y={70} textAnchor="middle" fontSize={9} fill="#64748B">Total Jobs</text>
      </svg>
      <div className="space-y-1.5">
        {data.map(d => (
          <div key={d.label} className="flex items-center gap-2 text-xs">
            <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: d.color }} />
            <span className="text-[#64748B] w-20">{d.label}</span>
            <span className="font-semibold text-[#0F172A]">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function DashboardPage({ onCreateSchedule, onViewResults }: DashboardPageProps) {
  const {jobs:JOBS,overview,dataset}=useAppData();
  const [range,setRange]=useState('All');
  const accepted=!!overview.latest_accepted_schedule;
  const chosen=(overview.algorithms||[]).find((r:any)=>r.algorithm===overview.solver);
  const history:any[]=overview.history||[];
  const now=Date.now();
  const recent=history.filter(r=>range==='All'||now-new Date(r.created_at).getTime()<=Number(range)*86400000);
  const trend=recent.slice(0,12).reverse().map(r=>({id:r.id,value:r.makespan,key:r.comparison_key,context:runContext(r),label:runLabel(r),summary:algorithmName(r.algo)+' | '+shortRunContext(r)}));
  const statuses=[['Completed','#10b981'],['In Progress','#2563eb'],['Scheduled','#8b5cf6'],['Unscheduled','#f59e0b'],['Delayed','#ef4444']].map(([label,color])=>({label,color,value:JOBS.filter(j=>j.status===label).length}));
  const upcoming=JOBS.filter(j=>['Unscheduled','Scheduled'].includes(j.status)).slice(0,5);
  const executionProgress=typeof overview.completed_jobs==='number'&&Number.isFinite(overview.completed_jobs)&&typeof overview.scheduled_jobs==='number'&&Number.isFinite(overview.scheduled_jobs)&&overview.scheduled_jobs>0&&overview.completed_jobs>=0&&overview.completed_jobs<=overview.scheduled_jobs
    ? 100*overview.completed_jobs/overview.scheduled_jobs : null;
  return <div className="flex-1 min-w-0 overflow-y-auto"><div className="aps-page industrial-dashboard">
    <header className="cinematic-hero">
      <div className="cinematic-hero-photo" aria-hidden="true"/>
      <div className="cinematic-hero-content"><p className="industrial-eyebrow">SMART PRODUCTION APS / AL13 V5</p><h1 aria-label="Production Dashboard"><span>Production</span><br/>Scheduling Platform</h1><p className="cinematic-hero-description">Optimize production with exact and metaheuristic algorithms for complex manufacturing environments.</p><div className="flex flex-wrap gap-3 mt-5"><button className="aps-primary" aria-label="Create Schedule" onClick={onCreateSchedule}><Plus size={17}/>New Scheduling Run</button><button className="aps-secondary" onClick={onViewResults}>View Recent Results<ArrowUpRight size={16}/></button></div></div>
    </header>
    <div className="industrial-dashboard-kpis grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-5">
      <MetricCard label="Total Jobs" value={metric(dataset?JOBS.length:null,0)} source="Current dataset" icon={Layers} tone="orange"/>
      <MetricCard label="Makespan (Cmax)" value={<>{metric(accepted?overview.makespan_min:null)} <small className="text-sm font-medium">min</small></>} source="Latest accepted plan" icon={Clock} tone="green"/>
      <MetricCard label="Tardy Jobs" value={metric(accepted?overview.tardy_jobs:null,0)} source="Latest accepted plan · diagnostic" icon={AlertCircle} tone="amber"/>
      <MetricCard label="Solver" value={<span className="text-lg">{accepted?algorithmName(overview.solver):'—'}</span>} source={accepted?(chosen?.proven_optimal?'Optimality proven':chosen?.feasible?'Feasible · optimality not proven':'Status not recorded'):'No accepted schedule'} icon={Cpu} tone="purple"/>
    </div>
    <p className="text-[11px] text-slate-500 mb-5 break-words">{accepted?<span title={overview.latest_accepted_schedule}>Accepted plan {overview.latest_accepted_schedule.slice(0,8)}… · {overview.accepted_applied?'Applied':'Not applied'} · Dataset {overview.accepted_dataset_id}</span>:'Accept a schedule to populate plan metrics.'} {dataset&&<span className="ml-2">Current data: {dataset.filename}</span>}</p>
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-5 mb-5">
      <Panel title="Workstation Utilization (Accepted plan)" subtitle="Planned load / saved available time. Not measured utilization.">
        <div className="space-y-3 max-h-72 overflow-y-auto pr-2">{(overview.workstations||[]).map((ws:WorkstationData)=><UtilizationBar key={ws.id} label={ws.id} value={accepted&&typeof ws.utilization==='number'&&Number.isFinite(ws.utilization)?ws.utilization:null}/>)}</div>
        {!overview.workstations?.length&&<p className="text-xs text-slate-500 py-10">No resource data available.</p>}
      </Panel>
      <Panel title="Makespan History" subtitle="Historical tracking, not a benchmark. Lines only connect matching input and settings." action={<select aria-label="History period" className="border border-slate-200 rounded text-xs p-1" value={range} onChange={e=>setRange(e.target.value)}><option value="All">All dates</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option></select>}>
        <MakespanTrend data={trend}/>
      </Panel>
      <Panel title="Job Status" subtitle="Current dataset · reported job status">
        <DonutChart data={statuses}/>
        <div className="mt-5 pt-4 border-t border-slate-100 text-xs text-slate-500">Execution across applied schedules: <strong className="text-slate-700">{metric(overview.completed_jobs,0)} / {metric(overview.scheduled_jobs,0)}</strong> completed.
          {executionProgress!==null&&<div className="mt-3"><div className="flex justify-between gap-2 mb-2"><span>Reported production progress</span><strong>{metric(executionProgress)}%</strong></div><progress aria-label="Reported completion across applied schedules" className="industrial-progress" max={overview.scheduled_jobs} value={overview.completed_jobs}/></div>}
        </div>
      </Panel>
    </div>
    <Panel title="Recent Scheduling Runs" subtitle="Completed does not mean accepted or applied. Run times use finished_at when recorded.">
      <RunHistory rows={history.slice(0,5)}/>
    </Panel>
    <details className="aps-panel mt-5"><summary className="p-4 text-sm font-semibold">Upcoming Jobs & Recent Activity</summary><div className="grid md:grid-cols-2 gap-6 p-5 pt-0">
      <div><h3 className="text-xs font-semibold mb-3">Upcoming jobs · current dataset</h3>{upcoming.map(j=><div key={j.id} className="flex justify-between border-t border-slate-100 py-2 text-xs"><span>{j.id} · {j.product}</span><span>{j.dueDate}</span><StatusChip status={j.status}/></div>)}</div>
      <div data-testid="recent-activity"><h3 className="text-xs font-semibold mb-3">Recent Activity</h3>{history.slice(0,5).map(r=><p key={r.id} className="text-xs py-2 border-t border-slate-100">{algorithmName(r.algo)} · {r.status} · {r.finished_at?'Finished: '+new Date(r.finished_at).toLocaleString():'Created: '+(r.created_at?new Date(r.created_at).toLocaleString():'Not recorded')} · Makespan {metric(r.makespan)} min</p>)}</div>
    </div></details>
  </div></div>;
}
