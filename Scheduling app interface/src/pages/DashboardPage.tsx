import { algorithmName } from '../algorithms';
import { useAppData } from '../AppData';
import StatusChip from '../components/StatusChip';
import { TrendingUp, AlertCircle, CheckCircle2, Clock, Play, CalendarPlus } from 'lucide-react';

interface DashboardPageProps {
  onCreateSchedule: () => void;
}

// Simple SVG bar chart
function UtilizationBar({ value, label }: { value: number; label: string }) {
  const color = value > 85 ? '#EF4444' : value > 65 ? '#F59E0B' : '#10B981';
  return (
    <div className="flex items-center gap-3">
      <div className="w-12 text-xs text-[#64748B] text-right flex-shrink-0">{label}</div>
      <div className="flex-1 h-5 bg-slate-100 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${value}%`, backgroundColor: color }}
        />
      </div>
      <div className="w-10 text-xs font-semibold text-[#0F172A] text-right">{value}%</div>
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

function KPICard({ label, value, sub, icon: Icon, color }: {
  label: string; value: string; sub?: string; icon: any; color: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5">
      <div className="flex items-start justify-between mb-3">
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center`} style={{ backgroundColor: color + '18' }}>
          <Icon size={18} style={{ color }} />
        </div>
      </div>
      <div className="text-2xl font-bold text-[#0F172A]">{value}</div>
      <div className="text-sm text-[#64748B] mt-0.5">{label}</div>
      {sub && <div className="text-xs text-[#94A3B8] mt-1">{sub}</div>}
    </div>
  );
}

export default function DashboardPage({ onCreateSchedule }: DashboardPageProps) {
  const {jobs:JOBS,workstations:WORKSTATIONS_DATA,overview} = useAppData();
  const byStatus = (s: string) => JOBS.filter(j => j.status === s).length;
  const total = JOBS.length;
  const avgUtil = Math.round(WORKSTATIONS_DATA.reduce((s, w) => s + w.utilization, 0) / (WORKSTATIONS_DATA.length || 1));

  const recentActivity = (overview.history || []).slice(0,5).map((r:any)=>({time:new Date(r.created_at).toLocaleString(),msg:algorithmName(r.algo)+' completed · Makespan '+r.makespan.toFixed(2)+' min',type:'success'}));

  const donutData = [
    { label: 'Unscheduled', value: byStatus('Unscheduled'), color: '#94A3B8' },
    { label: 'Scheduled', value: byStatus('Scheduled'), color: '#3B82F6' },
    { label: 'In Progress', value: byStatus('In Progress'), color: '#F59E0B' },
    { label: 'Completed', value: byStatus('Completed'), color: '#10B981' },
    { label: 'Delayed', value: byStatus('Delayed'), color: '#EF4444' },
  ];

  const upcomingJobs = JOBS.filter(j => j.status === 'Unscheduled' || j.status === 'Scheduled').slice(0, 5);

  return (
    <div className="flex-1 overflow-y-auto bg-[#F8FAFC]">
      <div className="max-w-7xl mx-auto px-6 py-6">
        {/* Header */}
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-xl font-bold text-[#0F172A]">Production Dashboard</h1>
            <p className="text-sm text-[#64748B] mt-0.5">{new Date().toLocaleDateString()} · Latest accepted schedule</p>
          </div>
          <button
            onClick={onCreateSchedule}
            className="flex items-center gap-2 px-4 py-2.5 bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-sm font-semibold rounded-lg transition-all shadow-sm"
          >
            <CalendarPlus size={16} />
            Create Schedule
          </button>
        </div>

        {/* KPI Row */}
        <div className="grid grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          <KPICard label="Total Jobs" value={String(total)} icon={TrendingUp} color="#2563EB" />
          <KPICard label="Unscheduled" value={String(byStatus('Unscheduled'))} icon={Clock} color="#64748B" />
          <KPICard label="Scheduled" value={String(byStatus('Scheduled'))} icon={CalendarPlus} color="#3B82F6" />
          <KPICard label="In Progress" value={String(byStatus('In Progress'))} icon={Play} color="#F59E0B" />
          <KPICard label="Completed" value={String(byStatus('Completed'))} icon={CheckCircle2} color="#10B981" />
          <KPICard label="Delayed" value={String(byStatus('Delayed'))} icon={AlertCircle} color="#EF4444" />
        </div>

        {/* Secondary KPIs */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="bg-white rounded-xl border border-slate-200 p-5 flex items-center gap-4">
            <div className="w-10 h-10 rounded-lg bg-[#EFF6FF] flex items-center justify-center">
              <TrendingUp size={20} className="text-[#2563EB]" />
            </div>
            <div>
              <div className="text-2xl font-bold text-[#0F172A]">{overview.makespan_min?.toFixed(2) ?? '\u2014'} <span className="text-sm font-normal text-[#64748B]">min</span></div>
              <div className="text-sm text-[#64748B]">Current Makespan</div>
            </div>
          </div>
          <div className="bg-white rounded-xl border border-slate-200 p-5 flex items-center gap-4">
            <div className="w-10 h-10 rounded-lg bg-[#ECFDF5] flex items-center justify-center">
              <CheckCircle2 size={20} className="text-[#10B981]" />
            </div>
            <div>
              <div className="text-2xl font-bold text-[#0F172A]">{avgUtil}%</div>
              <div className="text-sm text-[#64748B]">Avg. Workstation Util.</div>
            </div>
          </div>
          <div className="bg-white rounded-xl border border-slate-200 p-5 flex items-center gap-4">
            <div className="w-10 h-10 rounded-lg bg-[#FEF3C7] flex items-center justify-center">
              <Clock size={20} className="text-[#F59E0B]" />
            </div>
            <div>
              <div className="text-2xl font-bold text-[#0F172A]">{overview.tardy_jobs ?? '\u2014'}</div>
              <div className="text-sm text-[#64748B]">Tardy Jobs (Plan)</div>
            </div>
          </div>
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-2 gap-6 mb-6">
          {/* Job Status Donut */}
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-[#0F172A] text-sm mb-4">Job Status Distribution</h3>
            <DonutChart data={donutData} />
          </div>

          {/* Workstation Utilization */}
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-[#0F172A] text-sm mb-4">Workstation Utilization</h3>
            <div className="space-y-2.5">
              {WORKSTATIONS_DATA.slice(0, 6).map(ws => (
                <UtilizationBar key={ws.id} label={ws.id} value={ws.utilization} />
              ))}
            </div>
          </div>
        </div>

        {/* Bottom Row */}
        <div className="grid grid-cols-2 gap-6">
          {/* Upcoming Jobs */}
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-[#0F172A] text-sm">Upcoming Jobs</h3>
              <span className="text-xs text-[#64748B]">Next 5</span>
            </div>
            <div className="space-y-2">
              {upcomingJobs.map(job => (
                <div key={job.id} className="flex items-center gap-3 py-1.5">
                  <span className="font-mono text-xs font-semibold text-[#2563EB] w-12">{job.id}</span>
                  <span className="flex-1 text-sm text-[#0F172A] truncate">{job.product}</span>
                  <StatusChip status={job.priority} dot={false} />
                  <StatusChip status={job.status} />
                </div>
              ))}
            </div>
          </div>

          {/* Recent Activity */}
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="font-semibold text-[#0F172A] text-sm mb-4">Recent Activity</h3>
            <div className="space-y-3">
              {recentActivity.map((a: {time:string;msg:string;type:string}, i:number) => (
                <div key={i} className="flex gap-3">
                  <div className={`w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 ${
                    a.type === 'success' ? 'bg-emerald-500' : a.type === 'warning' ? 'bg-amber-500' : 'bg-[#2563EB]'
                  }`} />
                  <div>
                    <p className="text-sm text-[#0F172A] leading-snug">{a.msg}</p>
                    <p className="text-xs text-[#94A3B8] mt-0.5">{a.time}</p>
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
