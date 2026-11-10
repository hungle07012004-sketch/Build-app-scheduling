export type JobStatus = 'Unscheduled' | 'Scheduled' | 'In Progress' | 'Completed' | 'Delayed';
export type Priority = 'High' | 'Medium' | 'Low';

export interface Job {
  id: string;
  product: string;
  batchQty: number;
  priority: Priority;
  releaseTime: string;
  materialReady: string;
  loopsetRelease: string;
  priorityValue: number;
  dueDate: string;
  status: JobStatus;
}

export interface GanttTask {
  eventId?: string;
  resourceUnits?: string;
  eventType?: string;
  jobIds?: string[];
  windowId?: string;
  jobId: string;
  product: string;
  operation: string;
  workstation: string;
  startTime: string;
  finishTime: string;
  processingMin: number;
  startMin: number;
  endMin: number;
  status: 'Scheduled' | 'In Progress' | 'Completed' | 'Delayed';
}

export interface WorkstationData {
  id: string;
  stage: string;
  currentJob: string | null;
  availability: string;
  utilization: number | null;
  status: 'Available' | 'Busy' | 'Downtime';
  upcomingJobs: string[];
}

export const STAGES = ['2B', 'PRE', 'FINAL', '2A', 'LASER', 'SCALE', 'OIL', 'SEAL', 'PACK', 'VISUAL', '2C', '2C_BATCH', 'SETUP'];

export const JOB_COLORS: Record<string, string> = {
  J001: '#3B82F6', J002: '#8B5CF6', J003: '#06B6D4', J004: '#10B981',
  J005: '#F59E0B', J006: '#6366F1', J007: '#EF4444', J008: '#EC4899',
  J009: '#14B8A6', J010: '#F97316', J011: '#3B82F6', J012: '#8B5CF6',
  J013: '#06B6D4', J014: '#10B981', J015: '#F59E0B', J016: '#6366F1',
  J017: '#EF4444', J018: '#EC4899', J019: '#14B8A6', J020: '#F97316',
};


export const taskJobIds = (task: GanttTask): string[] => task.jobIds || [task.jobId];
