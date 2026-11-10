import { AppDataProvider, useAppData } from './AppData';
import { post } from './api';
import ApiNotice from './components/ApiNotice';
import ExecutionPage from './pages/ExecutionPage';
import { useState, useEffect } from 'react';
import Sidebar, { type Page, pageNames } from './components/Sidebar';
import './aps.css';
import './industrial.css';
import IndustrialBackdrop from './components/IndustrialBackdrop';
import { Home, ChevronRight, Factory, Menu } from 'lucide-react';
import ResultsPage from './pages/ResultsPage';
import InputDataPanel from './components/InputDataPanel';
import { PageHeading } from './components/aps';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import JobsPage from './pages/JobsPage';
import ScheduleFlow from './pages/ScheduleFlow';
import WorkstationsPage from './pages/WorkstationsPage';
import AnalyticsPage from './pages/AnalyticsPage';
import SettingsPage from './pages/SettingsPage';

export default function App() {
  const [loggedIn, setLoggedIn] = useState(!!sessionStorage.getItem('al13-token'));
  const [page, setPage] = useState<Page>('dashboard');
  const [flowKey, setFlowKey] = useState(0);
  const [navigationOpen, setNavigationOpen] = useState(false);

  useEffect(()=>{const close=(event:KeyboardEvent)=>{if(event.key==='Escape')setNavigationOpen(false);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[]);

  useEffect(() => { const expire=()=>{sessionStorage.removeItem('al13-token');setLoggedIn(false);}; window.addEventListener('al13-unauthorized',expire); return ()=>window.removeEventListener('al13-unauthorized',expire); }, []);

  useEffect(()=>{const open=()=>{setFlowKey(k=>k+1);setPage('scheduling');};window.addEventListener('al13-open-run',open);return()=>window.removeEventListener('al13-open-run',open);},[]);
  if (!loggedIn) return <LoginPage onLogin={() => setLoggedIn(true)} />;

  function goToSchedule() { sessionStorage.removeItem('al13-run'); setFlowKey(k=>k+1); setPage('scheduling'); }
  function handleScheduleDone() { setPage('execution'); }

  return (
    <AppDataProvider><div className={'aps-shell flex h-dvh overflow-hidden '+(navigationOpen?'navigation-open':'')}>
      <IndustrialBackdrop/>
      {navigationOpen&&<button className="industrial-nav-scrim" aria-label="Close navigation" onClick={()=>setNavigationOpen(false)}/>}
      <Sidebar
        current={page}
        open={navigationOpen}
        onNavigate={p => { setPage(p); setNavigationOpen(false); }}
        onClose={()=>setNavigationOpen(false)}
        onLogout={() => { void post('/auth/logout').catch(()=>{}); sessionStorage.removeItem('al13-token'); setLoggedIn(false); }}
      />
      <main className="flex-1 min-w-0 flex flex-col overflow-hidden"><header className="aps-topbar"><div className="flex items-center gap-2"><button className="industrial-menu" aria-label="Open navigation" aria-expanded={navigationOpen} onClick={()=>setNavigationOpen(v=>!v)}><Menu size={20}/></button><Home size={15}/><span>Workspace</span><ChevronRight size={13}/><strong className="text-slate-800">{pageNames[page]}</strong></div><span className="flex items-center gap-2"><Factory size={15}/><span className="hidden sm:inline">AL13 V5 · Production Planning</span></span></header><DataNotice/>
        {page === 'dashboard' && (
          <DashboardPage onCreateSchedule={goToSchedule} onViewResults={()=>setPage('results')} />
        )}
        {page === 'jobs' && <JobsPage />}
        {page === 'results' && <ResultsPage onCreateSchedule={goToSchedule}/>}
        {(['processing-times','resources','calendar'] as Page[]).includes(page) && <div className="flex-1 overflow-auto"><div className="aps-page"><PageHeading title={pageNames[page]} description="Review the current dataset. Saved runs retain their original frozen input."/><InputDataPanel key={page} initialTab={page}/></div></div>}
        {page === 'execution' && <ExecutionPage />}
        {page === 'scheduling' && (
          <ScheduleFlow key={flowKey} onDone={handleScheduleDone} />
        )}
        {page === 'workstations' && <WorkstationsPage />}
        {page === 'analytics' && <AnalyticsPage />}
        {page === 'settings' && <SettingsPage />}
      </main>
    </div></AppDataProvider>
  );
}

function DataNotice() { const {error,loading,dataset,refresh}=useAppData(); return <>{loading && <p className="px-6 py-2 text-xs text-slate-500">Loading production data...</p>}<ApiNotice error={error}><button className="ml-3 underline" onClick={()=>void refresh()}>Retry</button></ApiNotice>{!loading && !error && !dataset && <p className="px-6 py-3 bg-blue-50 text-sm text-blue-700">Open Jobs to upload your AL13 input data.</p>}</>; }
