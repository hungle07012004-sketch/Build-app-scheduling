import type { ReactNode, ComponentType } from 'react';
export const metric = (value: unknown, digits = 2) => typeof value === 'number' && Number.isFinite(value)
  ? value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
export function PageHeading({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return <header className="aps-heading"><div><h1>{title}</h1><p>{description}</p></div>{actions && <div className="flex flex-wrap gap-2 items-center">{actions}</div>}</header>;
}
export function MetricCard({ label, value, source, icon: Icon, tone = 'blue' }: {
  label: string; value: ReactNode; source: ReactNode; icon: ComponentType<{ size?: number }>; tone?: string;
}) {
  return <article className={'aps-metric aps-tone-' + tone}><span className="aps-metric-icon"><Icon size={22}/></span><div className="min-w-0"><h2>{label}</h2><div className="aps-metric-value">{value}</div><p>{source}</p></div></article>;
}
export function Panel({ title, subtitle, action, children, className = '' }: { title: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={'aps-panel ' + className}><header className="aps-panel-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</header><div className="p-4 lg:p-5">{children}</div></section>;
}
export function WorkflowSteps({ current }: { current: number }) {
  return <ol aria-label="Scheduling workflow" className="aps-steps">{['Configure Input', 'Validate Data', 'Run Optimization', 'Review Schedule'].map((label, i) => <li key={label} aria-current={i === current ? 'step' : undefined} className={i === current ? 'active' : i < current ? 'complete' : ''}><span>{i + 1}</span><div><strong>{label}</strong><small>{['Select jobs and planning dates', 'Check inputs and solver settings', 'Search for a feasible schedule', 'Inspect, export and accept'][i]}</small></div></li>)}</ol>;
}
