import { useEffect, useRef, useState } from 'react';
import { LayoutDashboard, Database, CalendarClock, Factory, BarChart3, Settings, LogOut, CalendarCheck, ChevronDown, X } from 'lucide-react';
export type Page = 'dashboard' | 'jobs' | 'scheduling' | 'results' | 'workstations' | 'processing-times' | 'resources' | 'calendar' | 'analytics' | 'settings' | 'execution';
export const pageNames: Record<Page, string> = { dashboard:'Dashboard', jobs:'Jobs', scheduling:'Scheduling Studio', results:'Schedule Results', workstations:'Workstations', 'processing-times':'Processing Times', resources:'Resources', calendar:'Working Calendar', analytics:'Analytics & Reports', settings:'Settings', execution:'Production Execution' };
const inputPages: Page[] = ['jobs', 'workstations', 'processing-times', 'resources', 'calendar'];
const nav = [{ id:'dashboard' as Page, icon:LayoutDashboard }, { id:'scheduling' as Page, icon:CalendarClock }, { id:'results' as Page, icon:CalendarCheck }, { id:'execution' as Page, icon:Factory }, { id:'analytics' as Page, icon:BarChart3 }];
export default function Sidebar({ current, onNavigate, onLogout, onClose, open }: { current: Page; onNavigate: (page: Page) => void; onLogout: () => void; onClose:()=>void; open:boolean }) {
  const [inputOpen, setInputOpen] = useState(inputPages.includes(current));
  const ref=useRef<HTMLElement>(null);
  const [small,setSmall]=useState(()=>window.matchMedia('(max-width:1023px)').matches);
  useEffect(()=>{const query=window.matchMedia('(max-width:1023px)');const update=()=>setSmall(query.matches);query.addEventListener('change',update);return()=>query.removeEventListener('change',update);},[]);
  useEffect(()=>{
    if(!open||!small) return;
    const previous=document.activeElement as HTMLElement;
    ref.current?.querySelector<HTMLButtonElement>('[aria-label="Close menu"]')?.focus();
    const trap=(event:KeyboardEvent)=>{
      if(event.key!=='Tab') return;
      const elements=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled)')||[]).filter(el=>el.getClientRects().length);
      const first=elements[0],last=elements[elements.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
      if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    };
    document.addEventListener('keydown',trap);return()=>{document.removeEventListener('keydown',trap);previous?.focus();};
  },[open,small]);
  const item = (id:Page, Icon: typeof Factory) => <button key={id} title={pageNames[id]} aria-current={current === id ? 'page' : undefined} onClick={() => onNavigate(id)} className={'aps-nav-item ' + (current === id ? 'active' : '')}><Icon size={19}/><span>{pageNames[id]}</span></button>;
  return <aside ref={ref} inert={small&&!open} className="aps-sidebar">
    <div className="aps-brand"><span className="aps-brand-icon"><Factory size={30}/></span><div><strong>Smart Production APS</strong><small>Production Scheduling (AL13 V5)</small></div><button className="industrial-menu ml-auto" aria-label="Close menu" onClick={onClose}><X size={20}/></button></div>
    <nav aria-label="Main navigation" className="flex-1 overflow-y-auto px-3 py-5 space-y-1">
      <p className="aps-nav-caption">WORKSPACE</p>{item(nav[0].id, nav[0].icon)}
      <button title="Input Data" className={'aps-nav-item ' + (inputPages.includes(current) ? 'selected-parent' : '')} aria-expanded={inputOpen} onClick={() => setInputOpen(v => !v)}><Database size={19}/><span className="flex-1 text-left">Input Data</span><ChevronDown size={14} className={inputOpen ? 'rotate-180' : ''}/></button>
      {inputOpen && <div className="aps-subnav">{inputPages.map(id => <button key={id} aria-current={current === id ? 'page' : undefined} className={current === id ? 'active' : ''} onClick={() => onNavigate(id)}>{pageNames[id]}</button>)}</div>}
      {nav.slice(1).map(({id,icon}) => item(id,icon))}
      <div className="pt-5 mt-5 border-t border-white/10">{item('settings',Settings)}</div>
    </nav>
    <div className="aps-sidebar-footer"><div className="aps-factory-mark" aria-hidden="true"><Factory size={100} strokeWidth={0.6}/></div><p>PLAN WITH CONFIDENCE</p><small>Real constraints. Traceable schedules.</small><button className="aps-nav-item mt-4" onClick={onLogout}><LogOut size={18}/><span>Sign Out</span></button></div>
  </aside>;
}
