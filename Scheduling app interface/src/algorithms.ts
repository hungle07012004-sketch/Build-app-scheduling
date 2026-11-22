export const algorithms: Record<string,{name:string;description:string}> = {
  'MIN_TARDY_GROUPED_LNS_V5': {name:'Min Tardy — Due-Date Grouping + LNS',description:'Minimizes late jobs, then total tardiness and makespan under original due dates.'},
  'CP-SAT': {name:'CP-SAT',description:'Exact constraint solver. A feasible schedule is only optimal when proven.'},
  'SS Micro-GA Fast': {name:'GA (SS Micro-GA Fast)',description:'Genetic search for V5 production schedules.'},
  'MILP': {name:'MILP (legacy)',description:'Existing mathematical solver for legacy datasets and saved schedules.'},
  'Improved GA': {name:'GA (Improved GA)',description:'Genetic search with the existing IG implementation for legacy data.'},
  'MLPSO': {name:'MLPSO',description:'Multi-Level Particle Swarm Optimization; V3 with the shared decoder for V5 data.'},
};
export const algorithmName=(id?:string)=>id ? algorithms[id]?.name || id : '\u2014';
