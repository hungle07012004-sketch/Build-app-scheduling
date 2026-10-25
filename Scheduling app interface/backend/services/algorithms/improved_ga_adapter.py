from .common import solver_module

def solve(path, settings):
    return solver_module("solve_improved_ga_ig_v3_earlystop_instance").solve_instance(
        path, population=settings["ga_population"], generations=settings["ga_generations"],
        mutation_rate=settings.get("ga_mutation_rate",0.12),crossover_rate=settings.get("ga_crossover_rate",0.90),
        runs=settings["runs"], seed_base=settings["seed_base"], parallel_runs=False,
        target_cmax=settings["target_cmax"],
        target_polish_generations=settings["target_polish_generations"],
        collect_convergence=True, progress_every=100)
