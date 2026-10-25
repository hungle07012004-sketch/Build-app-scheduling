from .common import solver_module

def solve(path, settings):
    return solver_module("solve_mlpso_instance").solve_instance(
        path, population=settings["mlpso_population"], iterations=settings["mlpso_iterations"],
        mr=settings.get("mlpso_mr",0.80),pcr=settings.get("mlpso_pcr",0.80),gcr=settings.get("mlpso_gcr",0.40),
        runs=settings["runs"], seed_base=settings["seed_base"], parallel_runs=False,
        collect_convergence=True, progress_every=100)
