import type { JobStatus } from '../data';

const STATUS_STYLES: Record<string, string> = {
  'Unscheduled': 'bg-slate-100 text-slate-600',
  'Scheduled':   'bg-blue-50 text-blue-700',
  'In Progress': 'bg-amber-50 text-amber-700',
  'Completed':   'bg-emerald-50 text-emerald-700',
  'Delayed':     'bg-red-50 text-red-600',
  'Available':   'bg-emerald-50 text-emerald-700',
  'Busy':        'bg-amber-50 text-amber-700',
  'Downtime':    'bg-red-50 text-red-600',
  'High':        'bg-red-50 text-red-600',
  'Medium':      'bg-amber-50 text-amber-700',
  'Low':         'bg-slate-100 text-slate-600',
};

const STATUS_DOTS: Record<string, string> = {
  'Unscheduled': 'bg-slate-400',
  'Scheduled':   'bg-blue-500',
  'In Progress': 'bg-amber-500',
  'Completed':   'bg-emerald-500',
  'Delayed':     'bg-red-500',
  'Available':   'bg-emerald-500',
  'Busy':        'bg-amber-500',
  'Downtime':    'bg-red-500',
};

interface StatusChipProps {
  status: string;
  dot?: boolean;
  size?: 'sm' | 'md';
}

export default function StatusChip({ status, dot = true, size = 'sm' }: StatusChipProps) {
  const base = STATUS_STYLES[status] || 'bg-slate-100 text-slate-600';
  const dotColor = STATUS_DOTS[status] || 'bg-slate-400';
  const padding = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm';
  return (
    <span className={`inline-flex items-center gap-1.5 ${padding} rounded-full font-medium ${base}`}>
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />}
      {status}
    </span>
  );
}
