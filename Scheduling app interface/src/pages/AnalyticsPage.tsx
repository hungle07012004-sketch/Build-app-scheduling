import { algorithmName } from '../algorithms';
import { useAppData } from '../AppData';
import { downloadJson } from '../api';

function SvgBarChart({ data }: { data: { label: string; value: number; color: string }[] }) {
  if (!data.length) return <p className="text-sm text-slate-400 py-10 text-center">No schedule data yet.</p>;
  const max = Math.max(1,...data.map(d => d.value));
  const W = 480, H = 160, PAD = 40, BAR_GAP = 8;
  const barW = (W - PAD * 2 - BAR_GAP * Math.max(1,data.length - 1)) / data.length;

  return (
    <svg viewBox={`0 0 ${W} ${H + 30}`} width="100%" preserveAspectRatio="none">
      {data.map((d, i) => {
        const x = PAD + i * (barW + BAR_GAP);
        const h = (d.value / max) * H;
        const y = H - h;
        return (
          <g key={d.label}>
            <rect x={x} y={y} width={barW} height={h} rx={4} fill={d.color} opacity={0.85} />
            <text x={x + barW / 2} y={H + 16} textAnchor="middle" fontSize={9} fill="#64748B">{d.label}</text>
            <text x={x + barW / 2} y={y - 4} textAnchor="middle" fontSize={9} fill="#0F172A" fontWeight={600}>{d.value}</text>
          </g>
        );
      })}
    </svg>
  );
}

function SvgLineChart({ data }: { data: { label: string; value: number }[] }) {
  if (!data.length) return <p className="text-sm text-slate-400 py-10 text-center">No schedule data yet.</p>;
  const max = Math.max(1,...data.map(d => d.value));
  const min = Math.min(...data.map(d => d.value));
  const W = 480, H = 140, PAD_X = 40, PAD_Y = 20;
  const pts = data.map((d, i) => ({
    x: PAD_X + (i / Math.max(1,data.length - 1)) * (W - PAD_X * 2),
    y: PAD_Y + (1 - (d.value - min) / (max - min || 1)) * (H - PAD_Y * 2),
  }));
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
  const fill = `${path} L ${pts[pts.length - 1].x} ${H} L ${pts[0].x} ${H} Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H + 30}`} width="100%">
      <defs>
        <linearGradient id="lineGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2563EB" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#2563EB" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={fill} fill="url(#lineGrad)" />
      <path d={path} fill="none" stroke="#2563EB" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      {pts.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r={4} fill="#2563EB" />
          <text x={p.x} y={H + 16} textAnchor="middle" fontSize={9} fill="#64748B">{data[i].label}</text>
          <text x={p.x} y={p.y - 8} textAnchor="middle" fontSize={9} fill="#2563EB" fontWeight={600}>{data[i].value}</text>
        </g>
      ))}
    </svg>
  );
}

export default function AnalyticsPage() {
  const {workstations:WORKSTATIONS_DATA,overview}=useAppData();
  const history = overview.history || [];
  const makespanTrend = history.slice(0,7).reverse().map((r:any)=>({label:r.id.slice(0,6),value:r.makespan}));

  const utilizationBars = WORKSTATIONS_DATA.map(ws => ({
    label: ws.stage,
    value: ws.utilization,
    color: ws.utilization > 85 ? '#EF4444' : ws.utilization > 65 ? '#F59E0B' : '#10B981',
  }));

  const algoPerf = (overview.algorithms || []).filter((r:any)=>r.feasible).map((r:any)=>({label:r.algorithm,value:r.makespan_min,color:r.algorithm===overview.solver?'#2563EB':'#6366F1'}));
  const fmt=(v:any)=>v==null?'--':Number(v).toFixed(2);
  const metrics = [
    {label:'Makespan',value:fmt(overview.makespan_min)+' min'},
    {label:'Avg. Flow Time',value:fmt(overview.average_flow_time_min)+' min'},
    {label:'Total Tardiness',value:fmt(overview.total_tardiness_min)+' min'},
    {label:'Tardy Jobs (Plan)',value:overview.tardy_jobs ?? '--'},
    {label:'Avg. Workstation Util.',value:fmt(WORKSTATIONS_DATA.reduce((s,w)=>s+w.utilization,0)/(WORKSTATIONS_DATA.length||1))+'%'},
    {label:'Completed Jobs',value:String(overview.completed_jobs || 0)+' / '+String(overview.scheduled_jobs || 0)},
  ];

  return (
    <div className="flex-1 overflow-y-auto bg-[#F8FAFC]">
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-bold text-[#0F172A]">Analytics</h1>
            <p className="text-sm text-[#64748B] mt-0.5">Scheduling performance and production metrics</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">Latest accepted schedule</span>
            <button onClick={()=>downloadJson(overview,'production-report.json')} className="px-4 py-2 text-sm bg-white border border-slate-200 text-[#64748B] hover:bg-slate-50 rounded-lg font-medium">
              Export Report
            </button>
          </div>
        </div>

        {/* KPI metrics */}
        <div className="grid grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
          {metrics.map(m => (
            <div key={m.label} className="bg-white rounded-xl border border-slate-200 p-4">
              <div className="text-xl font-bold text-[#0F172A]">{m.value}</div>
              <div className="text-xs text-[#64748B] mt-0.5 leading-snug">{m.label}</div>

            </div>
          ))}
        </div>

        {/* Charts */}
        <div className="grid grid-cols-2 gap-6 mb-6">
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-[#0F172A] text-sm mb-4">Makespan Trend (min)</h3>
            <SvgLineChart data={makespanTrend} />
          </div>
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-[#0F172A] text-sm mb-4">Workstation Utilization (%)</h3>
            <SvgBarChart data={utilizationBars} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-[#0F172A] text-sm mb-4">Algorithm Performance</h3>
            <SvgBarChart data={algoPerf} />
            <div className="mt-3 p-3 bg-[#EFF6FF] rounded-lg text-xs text-[#1E3A8A]">
              {overview.selection_reason}
            </div>
          </div>
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-[#0F172A] text-sm mb-1">Algorithm Runtime History</h3>
            <p className="text-xs text-[#94A3B8] mb-4">Last 5 scheduling runs</p>
            <div className="space-y-3">
              {history.slice(0,5).map((r:any) => (
                <div role="button" tabIndex={0} onClick={()=>{sessionStorage.setItem('al13-run',r.id);window.dispatchEvent(new Event('al13-open-run'));}} onKeyDown={e=>{if(e.key==='Enter'){sessionStorage.setItem('al13-run',r.id);window.dispatchEvent(new Event('al13-open-run'));}}} key={r.id} className="flex items-center justify-between text-sm py-2 border-b border-slate-50 last:border-0">
                  <div>
                    <span className="font-mono text-xs font-semibold text-[#2563EB]">{r.id}</span>
                    <span className="ml-2 text-xs text-[#64748B]">{r.jobs} jobs · {algorithmName(r.algo)}</span>
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-medium text-[#0F172A]">{fmt(r.makespan)} min</div>
                    <div className="text-xs text-[#94A3B8]">{fmt(r.runtime)} s</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
