import { useCallback, useEffect, useState } from 'react';
export class ApiError extends Error {
  constructor(message: string, public details: unknown, public code: string) { super(message); }
}
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const token = sessionStorage.getItem('al13-token');
  const headers = new Headers(options.headers);
  if (token) headers.set('Authorization', 'Bearer ' + token);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch('/api' + path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('al13-unauthorized'));
    throw new ApiError(body.message || 'Request failed (' + response.status + ')', body.details, body.error_code);
  }
  return body;
}
export const post = <T = any>(path: string, body?: unknown) => api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
export function useApi<T>(path: string | null, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const refresh = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try { setData(await api<T>(path)); setError(''); } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, [path]);
  useEffect(() => { void refresh(); }, [refresh]);
  return { data, error, loading, refresh };
}
export function downloadJson(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type:'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadExcel(runId:string) {
  const response=await fetch('/api/schedules/'+encodeURIComponent(runId)+'/export.xlsx',{
    headers:{Authorization:'Bearer '+sessionStorage.getItem('al13-token')}
  });
  if(!response.ok) {
    if(response.status===401) window.dispatchEvent(new Event('al13-unauthorized'));
    const body=await response.json().catch(()=>({}));
    throw new Error(body.message || 'Excel export failed');
  }
  const url=URL.createObjectURL(await response.blob());
  const a=document.createElement('a');a.href=url;a.download=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'AL13_Schedule_'+runId+'.xlsx';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
