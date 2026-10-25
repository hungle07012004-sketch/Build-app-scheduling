"""Thin calls into the supplied V5 code; decoder and mathematical objective remain unchanged."""
def solve_exact(path, settings):
    from backend.solver_v5.solve_milp_instance import solve_instance
    return solve_instance(path,time_limit=settings["exact_time_limit"],keep_schedule=True,
                          random_seed=settings["seed_base"],workers=settings["exact_workers"])

def solve_micro(path, settings):
    from backend.solver_v5.solve_ss_micro_ga_fast_instance import solve_instance
    return solve_instance(path,population=settings["micro_population"],
        generations=settings["micro_generations"],max_decodes=settings["micro_max_decodes"],
        mutation_rate=settings.get("micro_mutation_rate",0.15),crossover_rate=settings.get("micro_crossover_rate",0.90),
        runs=settings["runs"],seed_base=settings["seed_base"],parallel_runs=False,
        time_limit=settings.get("micro_time_limit",120),
        no_improve=settings.get("micro_no_improve",600),
        min_epochs=settings.get("micro_min_epochs",1300),
        collect_convergence=True,progress_every=100)
