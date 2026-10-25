import { useAppData } from '../AppData';
import { useState } from 'react';
import { type WorkstationData } from '../data';
import StatusChip from '../components/StatusChip';
import { X } from 'lucide-react';

function DrawerPanel({ ws, onClose }: { ws: WorkstationData; onClose: () => void }) {
  return (
    <div className="w-80 flex-shrink-0 bg-white border-l border-slate-200 flex flex-col h-full">
      <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
        <div>
          <div className="font-semibold text-[#0F172A]">{ws.id}</div>
          <div className="text-xs text-[#64748B]">Stage: {ws.stage}</div>
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-slate-100 text-slate-500"><X size={16} /></button>
      </div>
      <div className="p-5 space-y-4 overflow-y-auto">
        <div>
          <div className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-2">Status</div>
          <StatusChip status={ws.status} size="md" />
        </div>
        <div>
          <div className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-2">Utilization</div>
          <div className="flex items-center gap-3">
            <div className="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{ width: `${ws.utilization}%`, backgroundColor: ws.utilization > 85 ? '#EF4444' : ws.utilization > 65 ? '#F59E0B' : '#10B981' }}
              />
            </div>
            <span className="text-sm font-bold text-[#0F172A] w-10 text-right">{ws.utilization}%</span>
          </div>
        </div>
        <div>
          <div className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-2">Current Job</div>
          {ws.currentJob
            ? <span className="font-mono text-sm font-semibold text-[#2563EB]">{ws.currentJob}</span>
            : <span className="text-sm text-[#94A3B8]">—</span>}
        </div>
        <div>
          <div className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-2">Upcoming Jobs</div>
          {ws.upcomingJobs.length > 0
            ? <div className="flex flex-wrap gap-1.5">{ws.upcomingJobs.map(j => (
                <span key={j} className="font-mono text-xs bg-[#EFF6FF] text-[#2563EB] px-2 py-0.5 rounded font-semibold">{j}</span>
              ))}</div>
            : <span className="text-sm text-[#94A3B8]">No upcoming jobs</span>}
        </div>
        <div>
          <div className="text-xs font-semibold text-[#64748B] uppercase tracking-wide mb-2">Availability</div>
          <div className="text-sm text-[#0F172A]">{ws.availability}</div>
        </div>
      </div>
    </div>
  );
}

export default function WorkstationsPage() {
  const {workstations:WORKSTATIONS_DATA} = useAppData();
  const [selected, setSelected] = useState<WorkstationData | null>(null);

  return (
    <div className="flex-1 overflow-hidden flex">
      <div className="flex-1 overflow-y-auto bg-[#F8FAFC]">
        <div className="max-w-6xl mx-auto px-6 py-6">
          <div className="mb-6">
            <h1 className="text-xl font-bold text-[#0F172A]">Workstations</h1>
            <p className="text-sm text-[#64748B] mt-0.5">
              {WORKSTATIONS_DATA.filter(w => w.status === 'Available').length} available ·{' '}
              {WORKSTATIONS_DATA.filter(w => w.status === 'Busy').length} busy ·{' '}
              {WORKSTATIONS_DATA.filter(w => w.status === 'Downtime').length} downtime
            </p>
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
            {WORKSTATIONS_DATA.map(ws => (
              <div
                key={ws.id}
                onClick={() => setSelected(ws === selected ? null : ws)}
                className={`bg-white rounded-xl border cursor-pointer transition-all p-4 ${
                  selected?.id === ws.id ? 'border-[#2563EB] ring-2 ring-[#2563EB]/20' : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <div className="font-semibold text-[#0F172A] text-sm">{ws.id}</div>
                    <div className="text-xs text-[#64748B]">Stage: {ws.stage}</div>
                  </div>
                  <StatusChip status={ws.status} size="sm" />
                </div>

                <div className="mb-2">
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-[#64748B]">Utilization</span>
                    <span className="font-semibold text-[#0F172A]">{ws.utilization}%</span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${ws.utilization}%`,
                        backgroundColor: ws.utilization > 85 ? '#EF4444' : ws.utilization > 65 ? '#F59E0B' : '#10B981'
                      }}
                    />
                  </div>
                </div>

                <div className="text-xs text-[#64748B] flex items-center gap-1.5">
                  {ws.currentJob
                    ? <>Current: <span className="font-mono font-semibold text-[#2563EB]">{ws.currentJob}</span></>
                    : ws.status === 'Downtime' ? 'Under maintenance' : 'No active job'}
                </div>
              </div>
            ))}
          </div>

          {/* Table view */}
          <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
            <div className="px-5 py-3.5 border-b border-slate-100">
              <h3 className="font-semibold text-[#0F172A] text-sm">All Workstations</h3>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-[#F8FAFC]">
                  {['Workstation', 'Stage', 'Current Job', 'Availability', 'Utilization', 'Status'].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-[#64748B] uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {WORKSTATIONS_DATA.map(ws => (
                  <tr
                    key={ws.id}
                    onClick={() => setSelected(ws === selected ? null : ws)}
                    className={`cursor-pointer transition-colors ${selected?.id === ws.id ? 'bg-[#EFF6FF]' : 'hover:bg-slate-50'}`}
                  >
                    <td className="px-4 py-3 font-mono text-xs font-semibold text-[#0F172A]">{ws.id}</td>
                    <td className="px-4 py-3">
                      <span className="bg-slate-100 text-slate-700 text-xs font-semibold px-2 py-0.5 rounded">{ws.stage}</span>
                    </td>
                    <td className="px-4 py-3">
                      {ws.currentJob
                        ? <span className="font-mono text-xs font-semibold text-[#2563EB]">{ws.currentJob}</span>
                        : <span className="text-[#94A3B8]">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs text-[#64748B]">{ws.availability}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{ width: `${ws.utilization}%`, backgroundColor: ws.utilization > 85 ? '#EF4444' : ws.utilization > 65 ? '#F59E0B' : '#10B981' }}
                          />
                        </div>
                        <span className="text-xs font-medium text-[#0F172A]">{ws.utilization}%</span>
                      </div>
                    </td>
                    <td className="px-4 py-3"><StatusChip status={ws.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {selected && <DrawerPanel ws={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
