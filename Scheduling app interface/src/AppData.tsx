import { createContext, useContext, useCallback, useEffect, useState, type ReactNode } from 'react';
import { api } from './api';
import type { Job, WorkstationData } from './data';

export interface Dataset { schema_version?:string; id:string; filename:string; uploaded_at:string; jobs:number; workstations:number; origin_datetime:string; horizon_end_datetime:string; validation_status:string; }
interface DataState {
  jobs: Job[]; workstations: WorkstationData[]; overview: any; dataset: Dataset | null;
  error: string; loading: boolean; refresh: () => Promise<void>;
}
const Context = createContext<DataState | null>(null);
export const useAppData = () => { const value = useContext(Context); if (!value) throw new Error('Missing AppDataProvider'); return value; };
export function AppDataProvider({children}: {children:ReactNode}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [workstations, setWorkstations] = useState<WorkstationData[]>([]);
  const [overview, setOverview] = useState<any>({history:[],algorithms:[]});
  const [dataset, setDataset] = useState<Dataset|null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [summary, current, stations] = await Promise.all([api('/analytics/overview'), api('/input/current'),api('/workstations')]);
      setJobs(summary.jobs); setWorkstations(stations); setOverview(summary); setDataset(current); setError('');
    } catch(e) { setError((e as Error).message); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return <Context.Provider value={{jobs,workstations,overview,dataset,error,loading,refresh}}>{children}</Context.Provider>;
}
