import { LayoutDashboard, Briefcase, CalendarClock, Factory, BarChart3, Settings, LogOut, Cpu } from 'lucide-react';

export type Page = 'dashboard' | 'jobs' | 'scheduling' | 'workstations' | 'analytics' | 'settings' | 'execution';

const NAV = [
  { id: 'dashboard' as Page, label: 'Dashboard', icon: LayoutDashboard },
  { id: 'jobs' as Page, label: 'Jobs', icon: Briefcase },
  { id: 'scheduling' as Page, label: 'Scheduling', icon: CalendarClock },
  { id: 'workstations' as Page, label: 'Workstations', icon: Factory },
  { id: 'execution' as Page, label: 'Production Execution', icon: Factory },
  { id: 'analytics' as Page, label: 'Analytics', icon: BarChart3 },
  { id: 'settings' as Page, label: 'Settings', icon: Settings },
];

interface SidebarProps {
  current: Page;
  onNavigate: (p: Page) => void;
  onLogout: () => void;
}

export default function Sidebar({ current, onNavigate, onLogout }: SidebarProps) {
  return (
    <aside className="w-60 flex-shrink-0 bg-[#0F172A] flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-white/10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#2563EB] flex items-center justify-center">
            <Cpu size={16} className="text-white" />
          </div>
          <div>
            <div className="text-white font-semibold text-sm leading-tight">Smart Production</div>
            <div className="text-[#64748B] text-xs">Scheduling System</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = current === id;
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                active
                  ? 'bg-[#2563EB] text-white'
                  : 'text-[#94A3B8] hover:bg-white/5 hover:text-white'
              }`}
            >
              <Icon size={17} />
              {label}
            </button>
          );
        })}
      </nav>

      {/* User */}
      <div className="px-3 py-4 border-t border-white/10">
        <div className="flex items-center gap-3 px-3 py-2 mb-1">
          <div className="w-8 h-8 rounded-full bg-[#2563EB] flex items-center justify-center text-white text-xs font-semibold">PL</div>
          <div className="flex-1 min-w-0">
            <div className="text-white text-sm font-medium truncate">Planner</div>
            <div className="text-[#64748B] text-xs truncate">Production Planner</div>
          </div>
        </div>
        <button
          onClick={onLogout}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[#64748B] hover:bg-white/5 hover:text-white transition-all"
        >
          <LogOut size={16} />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
