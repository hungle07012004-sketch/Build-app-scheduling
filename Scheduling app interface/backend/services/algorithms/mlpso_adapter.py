from .common import solver_module

def solve(path, settings):
    import json
    from pathlib import Path
    from backend.services.v5_service import is_v5
    if is_v5(json.loads(Path(path).read_text(encoding="utf-8"))):
        from backend.solver_v5.solve_mlpso_v3_instance import solve_instance
        return solve_instance(
            path, population=settings["mlpso_population"], iterations=settings["mlpso_iterations"],
            mr=settings.get("mlpso_mr",0.80),pcr=settings.get("mlpso_pcr",0.80),gcr=settings.get("mlpso_gcr",0.40),
            max_decodes=settings.get("mlpso_max_decodes",12000),
            runs=settings["runs"],seed_base=settings["seed_base"],parallel_runs=False,
            collect_convergence=True,progress_every=100)
    return solver_module("solve_mlpso_instance").solve_instance(
        path, population=settings["mlpso_population"], iterations=settings["mlpso_iterations"],
        mr=settings.get("mlpso_mr",0.80),pcr=settings.get("mlpso_pcr",0.80),gcr=settings.get("mlpso_gcr",0.40),
        runs=settings["runs"], seed_base=settings["seed_base"], parallel_runs=False,
        collect_convergence=True, progress_every=100)
