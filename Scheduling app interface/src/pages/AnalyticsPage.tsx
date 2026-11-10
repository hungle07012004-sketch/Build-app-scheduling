import {runLabel,shortRunContext} from '../runLabels';
﻿import { useState } from 'react';
import { algorithmName } from '../algorithms';
import { useAppData } from '../AppData';
import { downloadJson } from '../api';

const numeric = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const fmt = (v: unknown, digits = 2) => numeric(v) ? v.toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
type Bar = {label: string; value: number | null; color: string; detail?: string};

function HorizontalBars({data, unit}: {data: Bar[]; unit: string}) {
  if (!data.length) return <p className="text-sm text-slate-400 py-10 text-center">No schedule data yet.</p>;
  const max = Math.max(unit === '%' ? 100 : 1, ...data.flatMap(d => numeric(d.value) ? [d.value] : []));
  return <div className="space-y-4" data-testid="horizontal-bars">
    {data.map((d, i) => <div key={i} title={`${d.detail || d.label}: ${fmt(d.value)} ${unit}`}>
      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs mb-1.5">
        <span className="min-w-0 break-words text-slate-600">{d.label}</span>
        <span className="shrink-0 tabular-nums font-medium">{fmt(d.value)} {unit}</span>
      </div>
      <div className="h-2 rounded bg-slate-100 overflow-hidden" aria-hidden="true">
        {numeric(d.value) && <div className="h-full rounded" style={{width: `${Math.max(0, d.value) / max * 100}%`, backgroundColor: d.color}} />}
      </div>
    </div>)}
  </div>;
}

export function MakespanTrend({data}: {data: {id: string; value: unknown; key?: string; context: string; label?:string; summary?:string}[]}) {
  const [active, setActive] = useState<number | null>(null);
  if (!data.length) return <p className="text-sm text-slate-400 py-10 text-center">No schedule data yet.</p>;
  const values = data.flatMap(d => numeric(d.value) ? [d.value] : []);
  const max = Math.max(1, ...values), min = Math.min(...values, max);
  const points = data.map((d, i) => ({x: 32 + i / Math.max(1, data.length - 1) * 416,
    y: numeric(d.value) ? 24 + (1 - (d.value - min) / (max - min || 1)) * 112 : null}));
  // Missing metrics create gaps; they are never drawn as zero.
  const path = points.map((p, i) => p.y === null ? '' : `${i === 0 || points[i-1].y === null || !data[i].key || data[i].key !== data[i-1].key ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
  const selected = active === null ? null : data[active];
  return <div>
    <svg viewBox="0 0 480 176" className="w-full industrial-trend" aria-label="Makespan of recent runs, oldest to newest">
      <path d={path} fill="none" stroke="#2563EB" strokeWidth={2} />
      {points.map((p, i) => <g key={data[i].id}>
        <text x={p.x} y={166} textAnchor="middle" fontSize={12} fill="#64748B">R{i+1}</text>
        {p.y !== null && <g role="button" tabIndex={0} aria-label={`Run R${i+1}: ${fmt(data[i].value)} min, ${data[i].id}`}
          onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}>
          <title>{data[i].id}: {fmt(data[i].value)} min | {data[i].context}</title>
          <circle cx={p.x} cy={p.y} r={12} fill="transparent" />
          <circle cx={p.x} cy={p.y} r={4} fill="#2563EB" pointerEvents="none" />
        </g>}
      </g>)}
    </svg>
    <div className="min-h-16 text-xs text-slate-500 break-words" aria-live="polite">
      {selected ? <span role="tooltip" title={selected.id+' | '+selected.context}><strong className="block text-slate-700">{selected.label || 'Schedule'}</strong><span className="block mt-1">Makespan: <strong>{fmt(selected.value)} min</strong></span><span className="block mt-1">{selected.summary || selected.context}</span><span className="block text-[10px] text-slate-400 mt-1">Factory time (UTC+07) | Hover for technical details</span></span> : 'Hover or focus a point for the schedule date and makespan. Missing values are not plotted.'}
    </div>
  </div>;
}

export default function AnalyticsPage() {
  const {overview} = useAppData();
  const history = overview.history || [];
  const accepted = overview.latest_accepted_schedule;
  const context = (r:any) => {
    const settings = r.search_settings || {};
    const name = String(r.algo || '').toLowerCase();
    const budget = name.includes('micro') ? settings.micro_time_limit : name.includes('cp') ? settings.exact_time_limit : name.includes('milp') ? settings.milp_time_limit : null;
    return `${algorithmName(r.algo)} · ${r.jobs} jobs · ${r.status || 'Unknown status'} · Dataset ${r.dataset_id || 'unknown'} · Calendar ${r.calendar?.start || 'unknown'} → ${r.calendar?.end || 'unknown'} [${r.calendar?.signature?.slice(0,8) || 'unknown'}] · Time budget: ${numeric(budget) ? `${budget} s` : 'not recorded / other stopping limits'}`;
  };
  const trend = history.slice(0,7).reverse().map((r:any) => ({id:r.id, value:r.makespan, key:r.comparison_key, context:context(r),label:runLabel(r),summary:algorithmName(r.algo)+' | '+shortRunContext(r)}));
  const utilization = (overview.workstations || []).map((ws:any) => ({label:ws.id, detail:`${ws.id} | ${ws.stage}`,
    value:accepted && numeric(ws.utilization) ? ws.utilization : null,
    color:ws.utilization > 85 ? '#EF4444' : ws.utilization > 65 ? '#F59E0B' : '#10B981'}));
  // Keep the existing arithmetic mean; do not average a partial/missing dataset.
  const average = utilization.length > 0 && utilization.every((w:Bar) => numeric(w.value))
    ? utilization.reduce((s:number,w:Bar) => s + w.value!, 0) / utilization.length : null;
  const performance = accepted ? (overview.algorithms || []).filter((r:any) => r.feasible).map((r:any) => ({
    label:algorithmName(r.algorithm), value:numeric(r.makespan_min) ? r.makespan_min : null,
    color:r.algorithm === overview.solver ? '#2563EB' : '#6366F1', detail:algorithmName(r.algorithm),
  })) : [];
  const metrics = [
    {label:'Makespan (Plan)', value:fmt(accepted ? overview.makespan_min : null)+' min', source:'Latest accepted schedule'},
    {label:'Avg. Flow Time (Plan)', value:fmt(accepted ? overview.average_flow_time_min : null)+' min', source:'Latest accepted schedule'},
    {label:'Total Tardiness (Plan)', value:fmt(accepted ? overview.total_tardiness_min : null)+' min', source:'Latest accepted schedule'},
    {label:'Tardy Jobs (Plan)', value:fmt(accepted ? overview.tardy_jobs : null,0), source:'Latest accepted schedule'},
    {label:'Avg. Workstation Util. (Plan)', value:fmt(average)+'%', source:'Mean across listed workstations'},
    {label:'Completed Jobs (Execution)', value:fmt(overview.completed_jobs,0)+' / '+fmt(overview.scheduled_jobs,0), source:'All applied schedules · reported status'},
  ];
  function openRun(id:string) { sessionStorage.setItem('al13-run',id); window.dispatchEvent(new Event('al13-open-run')); }
  return <div className="flex-1 min-w-0 overflow-y-auto bg-[#F8FAFC]">
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div><h1 className="text-xl font-bold text-[#0F172A]">Analytics</h1><p className="text-sm text-slate-500 mt-0.5">Scheduling performance and production metrics</p></div>
        <button onClick={() => downloadJson(overview,'production-report.json')} className="px-4 py-2 text-sm bg-white border border-slate-200 text-slate-500 hover:bg-slate-50 rounded-lg font-medium">Export Report</button>
      </div>
      <p className="text-xs text-slate-500 mb-4" title={accepted || undefined}>
        {accepted ? `Plan metrics: latest accepted schedule ${accepted.slice(0,8)}… · ${overview.accepted_applied ? 'Applied' : 'Not applied'} · dataset ${overview.accepted_dataset_id || 'unknown'}` : 'No accepted schedule. Plan metrics are unavailable.'}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6 gap-3 mb-6">
        {metrics.map(m => <div key={m.label} className="min-w-0 bg-white rounded-xl border border-slate-200 p-4" data-testid="analytics-kpi">
          <div className="text-lg font-bold text-slate-900 tabular-nums break-words">{m.value}</div>
          <div className="text-xs text-slate-600 mt-1">{m.label}</div><div className="text-xs text-slate-400 mt-1">{m.source}</div>
        </div>)}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="min-w-0 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-sm">Makespan Trend (min)</h3><p className="text-xs text-slate-500 mt-1 mb-4">Last 7 runs · oldest to newest. Historical tracking, not a benchmark. Lines connect only identical frozen inputs and search settings; other points are not comparable.</p>
          <MakespanTrend data={trend}/>
        </section>
        <section className="min-w-0 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-sm">Workstation Utilization (%)</h3>
          <p className="text-xs text-slate-500 mt-1 mb-4">Planned load / saved available time across the accepted horizon; not measured machine utilization. Current workstation list; missing accepted data is shown as —.</p>
          <HorizontalBars data={utilization} unit="%"/>
        </section>
        <section className="min-w-0 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-sm">Algorithm Performance</h3><p className="text-xs text-slate-500 mt-1 mb-4">Latest accepted run only · feasible participants · makespan (min). Not a historical algorithm ranking or a controlled benchmark.</p>
          <HorizontalBars data={performance} unit="min"/>
          {overview.selection_reason && <p className="mt-3 p-3 bg-blue-50 rounded-lg text-xs text-blue-900 break-words">{overview.selection_reason}</p>}
        </section>
        <section className="min-w-0 bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="font-semibold text-sm">Algorithm Runtime History</h3><p className="text-xs text-slate-500 mt-1 mb-4">Last 5 runs · all statuses · newest first</p>
          {!history.length && <p className="text-sm text-slate-400">No schedule data yet.</p>}
          <div className="space-y-2">{history.slice(0,5).map((r:any) => <button type="button" aria-label={`Open schedule ${r.id}`} title={`${r.id} | ${context(r)}`} key={r.id} onClick={() => openRun(r.id)} className="w-full flex flex-wrap items-center justify-between gap-2 text-left py-3 border-b border-slate-100 last:border-0 hover:bg-slate-50 rounded">
            <div className="min-w-0"><span className="text-xs font-semibold text-blue-600">{runLabel(r)}</span>
              <p className="text-xs text-slate-500 break-words">{fmt(r.jobs,0)} jobs · {algorithmName(r.algo)}</p>
              <span className="text-xs text-slate-400">{r.status || 'Unknown status'} | {r.accepted ? 'Accepted' : 'Not accepted'} | {r.applied ? 'Applied' : 'Not applied'}</span><p className="text-xs text-slate-400 break-all">{context(r)}</p></div>
            <div className="shrink-0 text-right text-xs tabular-nums"><div>{fmt(r.makespan)} min</div><div className="text-slate-500">{fmt(r.runtime)} s</div></div>
          </button>)}</div>
        </section>
      </div>
    </div>
  </div>;
}
