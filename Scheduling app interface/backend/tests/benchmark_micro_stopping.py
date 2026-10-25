"""Small reproducible before/after benchmark; writes artifacts, never the app DB."""
import sys
import contextlib
import hashlib
import json
from pathlib import Path
from backend.solver_v5.solve_ss_micro_ga_fast_instance import solve_instance
from backend.services.result_standardizer import standardize

if __name__ == "__main__":
    source = Path("../../../correct workstation/benchmark_instances_v5_h60/AL13_n020_i01.json")
    folder = Path(".tmp-verification/micro-stopping")
    folder.mkdir(parents=True,exist_ok=True)
    path = folder / "frozen.json"
    path.write_bytes(source.read_bytes())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    data = json.loads(path.read_text())
    output = folder / ("results-final.json" if "--final" in sys.argv else "results.json")
    tuned_only = "--tuned-only" in sys.argv
    rows = json.loads((folder/"results.json").read_text()) if tuned_only else []
    for seed in (42,1051):
        for mode in (("early_stop_tuned",) if tuned_only else ("baseline", "early_stop_tuned")):
            with (folder/f"{output.stem}-{mode}-{seed}.log").open("w") as log, contextlib.redirect_stdout(log):
                raw = solve_instance(path,population=24,generations=3000,max_decodes=12000,
                    runs=1,seed_base=seed,parallel_runs=False,progress_every=100,
                    time_limit=120 if mode!="baseline" else 0,
                    no_improve=600 if mode!="baseline" else 0,
                    min_epochs=1300 if mode=="early_stop_tuned" else 600 if mode!="baseline" else 0)
            result = standardize("SS Micro-GA Fast",raw,data)
            assert result["feasible"],result["validation_issues"]
            assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
            row = dict(mode=mode,seed=seed,instance_sha256=digest,
                **{k:result[k] for k in ("feasible","runtime_sec","makespan_min","total_tardiness_min","total_flow_time_min")},
                stop_reason=raw["search_stop_reason"],epochs=raw["run_summaries"][0]["epochs_completed"])
            rows.append(row)
            output.write_text(json.dumps(rows,indent=2))
            print(json.dumps(row),flush=True)
