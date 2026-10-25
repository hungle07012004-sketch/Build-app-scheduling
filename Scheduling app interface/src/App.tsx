import { AppDataProvider, useAppData } from './AppData';
import { post } from './api';
import ApiNotice from './components/ApiNotice';
import ExecutionPage from './pages/ExecutionPage';
import { useState, useEffect } from 'react';
import Sidebar, { type Page } from './components/Sidebar';
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
  const [inScheduleFlow, setInScheduleFlow] = useState(false);

  useEffect(() => { const expire=()=>{sessionStorage.removeItem('al13-token');setLoggedIn(false);}; window.addEventListener('al13-unauthorized',expire); return ()=>window.removeEventListener('al13-unauthorized',expire); }, []);

  useEffect(()=>{const open=()=>setPage('scheduling');window.addEventListener('al13-open-run',open);return()=>window.removeEventListener('al13-open-run',open);},[]);
  if (!loggedIn) return <LoginPage onLogin={() => setLoggedIn(true)} />;

  function goToSchedule() { sessionStorage.removeItem('al13-run'); setInScheduleFlow(true); setPage('scheduling'); }
  function handleScheduleDone() { setInScheduleFlow(false); setPage('execution'); }

  return (
    <AppDataProvider><div className="flex h-screen overflow-hidden bg-[#F8FAFC]">
      <Sidebar
        current={page}
        onNavigate={p => { setPage(p); if (p !== 'scheduling') setInScheduleFlow(false); }}
        onLogout={() => { void post('/auth/logout').catch(()=>{}); sessionStorage.removeItem('al13-token'); setLoggedIn(false); }}
      />
      <main className="flex-1 flex flex-col overflow-hidden"><DataNotice/>
        {page === 'dashboard' && (
          <DashboardPage onCreateSchedule={goToSchedule} />
        )}
        {page === 'jobs' && <JobsPage />}
        {page === 'execution' && <ExecutionPage />}
        {page === 'scheduling' && (
          <ScheduleFlow onDone={handleScheduleDone} />
        )}
        {page === 'workstations' && <WorkstationsPage />}
        {page === 'analytics' && <AnalyticsPage />}
        {page === 'settings' && <SettingsPage />}
      </main>
    </div></AppDataProvider>
  );
}

function DataNotice() { const {error,loading,dataset,refresh}=useAppData(); return <>{loading && <p className="px-6 py-2 text-xs text-slate-500">Loading production data...</p>}<ApiNotice error={error}><button className="ml-3 underline" onClick={()=>void refresh()}>Retry</button></ApiNotice>{!loading && !error && !dataset && <p className="px-6 py-3 bg-blue-50 text-sm text-blue-700">Open Jobs to upload your AL13 input data.</p>}</>; }
