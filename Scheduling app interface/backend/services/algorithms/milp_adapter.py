from .common import solver_module

def solve(path, settings):
    return solver_module("solve_milp_instance").solve_instance(
        path, time_limit=settings["milp_time_limit"], keep_schedule=True)
