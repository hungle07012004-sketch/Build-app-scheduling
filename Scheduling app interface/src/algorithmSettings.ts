export const algorithmSettings:Record<string,[string,number][]>={
  'CP-SAT':[['exact_time_limit',120],['exact_workers',0],['seed_base',42]],
  'SS Micro-GA Fast':[['runs',1],['seed_base',42],['micro_population',24],['micro_generations',3000],['micro_max_decodes',12000],['micro_mutation_rate',0.15],['micro_crossover_rate',0.90],['micro_no_improve',600],['micro_min_epochs',1300]],
  'Improved GA':[['runs',1],['seed_base',42],['ga_population',80],['ga_generations',500],['ga_mutation_rate',0.12],['ga_crossover_rate',0.90],['target_polish_generations',500]],
  'MLPSO':[['runs',1],['seed_base',42],['mlpso_population',60],['mlpso_iterations',500],['mlpso_max_decodes',12000],['mlpso_mr',0.80],['mlpso_pcr',0.80],['mlpso_gcr',0.40]],
  'MILP':[['milp_time_limit',600]],
};
