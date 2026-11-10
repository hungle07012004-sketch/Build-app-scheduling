# AL13 Production Scheduling

The existing Figma React UI is connected to FastAPI and SQLite. Legacy solvers remain in code/.
Updated AL13 V5 solvers are packaged in backend/solver_v5/. The app adds cooperative search limits and small-population initialization handling; the decoder, objective and physical constraints are unchanged.

## Start on this machine

Run the following in two PowerShell terminals from the project directory.

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
.\run-frontend.cmd
```

Open http://localhost:8443. API documentation: http://127.0.0.1:8000/docs.
Demo login: **planner@factory.com / planner-demo**.

run-frontend.cmd uses the project-local portable Node in .tools/ when present.
If Node is already on PATH, npm run dev is equivalent.
Use one backend worker and avoid --reload while optimization is running.

## Reproducible installation

Verified on Windows with Python 3.13.12, Node 22.23.3 and pnpm 10.34.5.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-lock.txt
npx --yes pnpm@10.34.5 install --frozen-lockfile
```

pnpm-lock.yaml is the authoritative frontend lockfile.
backend/requirements-lock.txt records the tested Python environment.
backend/requirements.txt declares compatible package ranges.
npm run commands work after installing dependencies with pnpm.

SciPy and pandas load successfully in the project .venv.
If Application Control blocks native DLLs on another machine, use an approved environment;
do not disable Windows security controls. Heavy solver/parser imports are lazy so API startup remains available.

## Development and production API configuration

The frontend calls relative /api URLs.
Vite development and preview proxy /api to http://127.0.0.1:8000.

For production, FastAPI serves dist/ and /api on the same origin:

```powershell
.\run-frontend.cmd build
.\run-production.cmd
```

Open http://127.0.0.1:8000. Vite is not needed in production mode.
Equivalent commands when Node is on PATH:

```powershell
npm run build
$env:SERVE_FRONTEND = '1'
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

When switching back to development in that terminal, remove the variable:
Remove-Item Env:SERVE_FRONTEND.
A separate static hosting service must reverse-proxy /api to FastAPI; copying dist alone does not provide an API.

## Data and workflow

1. Log in, open Jobs, and click Upload Input File.
2. Upload an AL13 .xlsx workbook or a frozen .json instance using AL13_BENCHMARK_V1.
3. Browse Jobs, Workstations, Processing Times, Working Calendar and Resources.
4. Create Schedule, select jobs and a usable horizon, then Validate Data.
5. Up to SMALL_SCALE_MAX_JOBS (default 20): MILP. Larger selections: Improved GA V3 and MLPSO concurrently.
6. Review Algorithm Comparison, Gantt View and Schedule Table.
7. Accept & Save, then Apply to Production.
8. Update jobs in Production Execution; Dashboard and Analytics read stored data.
9. Click a run in Analytics / Algorithm Runtime History to reopen it.
10. Run Again creates a new run; Adjust returns to input selection.

Editing a job's priority, product or dates creates a dataset revision.
Add Job copies an existing processing recipe, precedence, quantity and dates.
Change quantities or recipes in the workbook and upload again.
Old run snapshots and accepted schedules are preserved.

The comparator uses makespan, then total tardiness, then total flow time.
Runtime is display information only.
Only MILP with proven_optimal=true is labelled Optimal.
One failed solver does not discard its sibling's validated feasible result.
Gantt and table consume the same standardized operations.

SQLite, original uploads, immutable snapshots, results and solver logs are stored under backend/storage/.
Input/output validation rejects inconsistent assignments, timing, precedence, resources, overlaps and makespan.
Live metaheuristic progress is parsed from solver logs; no progress or optimization values are fabricated.
A restart marks unfinished runs interrupted; completed and accepted runs remain available.

## Configuration

- SMALL_SCALE_MAX_JOBS: defined in backend/config.py, default 20.
- SCHEDULING_DATA_DIR: default backend/storage/.
- SCHEDULING_EMAIL and SCHEDULING_PASSWORD: initial local account, set before its first creation.
- SERVE_FRONTEND=1: serve compiled dist from FastAPI.

Settings and Advanced settings control solver budgets and tracked seeds.
All selected IDs, parameters, frozen input and algorithm outputs are persisted.

## Tests

Backend:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q -ra
```

Frontend, including the local Node fallback:

```powershell
.\run-frontend.cmd check
.\run-frontend.cmd build
```

Full browser workflow:

```powershell
.\.venv\Scripts\python.exe -m backend.tests.export_fixtures
npx playwright install chromium
.\run-frontend.cmd test:e2e
```

The test starts local servers when absent. Stop the demo backend first so browser tests use isolated test storage.
If Node is not on PATH, invoke npx.cmd from the portable Node directory in .tools/ for browser installation.

Small case: upload tests/fixtures/TEST_AL13_10.xlsx, select all 10 jobs, validate and run MILP.
Large case: upload tests/fixtures/TEST_AL13_50.json, select all 50 jobs, validate and run the two metaheuristics.
These are synthetic test inputs; their displayed schedules come from actual solvers.
Smoke tests use reduced budgets (population 6, two generations/iterations, one run).
Use research parameters and the real workbook for thesis performance evaluation.

After a successful full browser test, verify production reopening without rerunning optimization:

```powershell
.\.venv\Scripts\python.exe -m backend.tests.prepare_reopen_fixture
.\run-frontend.cmd test:production
```

This copies existing test results into isolated storage, serves the production build on port 8001,
reopens saved runs, compares Gantt/table operations against the API, and checks snapshot bytes and hash after input edits.

Latest verification (2026-10-06): backend 37/37 PASS; TypeScript PASS; production build PASS; production browser 4/4 PASS; pip check PASS.
No lint script is configured. See [VERIFICATION.md](VERIFICATION.md) for all results and file inventory.

## Architecture

Existing Figma React UI -> FastAPI -> validation/frozen instance -> thin adapters -> original Python solvers.
SQLite persists dataset revisions, run requests/results, acceptance/application and execution events.
A background queue coordinates runs; large runs launch two processes against the same JSON file and SHA-256.
Adapters preserve the solver model and delegate to code/ without copying algorithm logic.

## Limitations and demo readiness

The application is ready for a local workflow demo using verified inputs.
The user's research AL13 workbook still needs acceptance testing with representative solver budgets.
MILP rejects cases where resource redundancy is not proven.
The legacy model requires unit-capacity workstations and its fixed AL13 operation chain. V5 uses the supplied physical resource, OIL-cycle, setup and exact-10 batch model.
FIFO preflight is conservative: failure is not a mathematical proof that every possible schedule is infeasible.
This is a single-planner local prototype; it has no multi-organization authorization or process resumption after restart.

No EXE has been built. Before packaging, validate the real workbook, bundle Python/native DLLs/solver sources/dist,
add a launcher and per-user storage/log paths, verify Windows multiprocessing freeze_support,
and test installation and persistence on a clean Windows machine.

The .cmd launchers work when PowerShell execution policy blocks .ps1 scripts. No security policy changes are required.

## Scheduling Result Gantt

Successful saved results open in Gantt View. Color by Job is the default;
Operation and Status modes have their own matching legends. Search Job highlights
matching operations without removing other jobs. Job, Workstation, Operation and
Shift filters explicitly restrict visible operations; Clear / Reset clears both
filters and highlighting.

Fit Schedule shows the actual earliest operation start to latest finish, with 3% padding on each side (minimum 15 minutes). Input horizons and due dates do not affect the Gantt domain. Zoom increases from 100% to
1600%; narrow bars intentionally hide labels until there is enough space. The
workstation column stays visible while scrolling. Hover shows exact saved operation
times and processing duration. Clicking a bar highlights its entire job, shows
the scheduled route, and offers View in Schedule Table with that job selected.

Calendar/shift and release/due details come from the run's original dataset
revision, never the latest edited input. Missing context is shown as unavailable.
Completion uses the latest saved job operation; flow time and tardiness are display
summaries of saved completion and original release/due times. No operations are
rescheduled or changed. Gantt and Schedule Table consume the same result array.

The production browser suite also checks Gantt search, colors across reloads,
filters, tooltips, labels, zoom/Fit, sticky columns and tab switching without any
solver requests, using an existing saved 10-job MILP result.

The scaling regression can replay an existing exported MILP result without running
a solver. Set GANTT_RESULT_JSON to the export's absolute path before running
run-frontend.cmd test:production, or place it at
tests/fixtures/current-milp-result.json. This optional regression is skipped if
the export is absent. Original dataset/calendar details may be unavailable in
the isolated replay; operation timestamps are taken unchanged from the export.

## Updated solvers: AL13 V5 (2026-10-04)

Restart the backend after updating this version. Upload a V5 workbook such as
dataset_AL13.xlsx from the correct workstation folder, or a frozen JSON with
metadata.schema_version = AL13_BENCHMARK_V5. Formats are detected automatically.
Do not relabel a legacy dataset as V5: it lacks the cycle/batch/resource data.

- V5 selections up to SMALL_SCALE_MAX_JOBS (default 20): CP-SAT.
- Larger V5 selections: SS Micro-GA Fast.
- Legacy datasets retain MILP / Improved GA + MLPSO routing and saved histories.
- V5 minimizes final 2C makespan only; tardiness and flow time are diagnostics.
- The new solve_milp_instance.py is an OR-Tools CP-SAT solver, not a MILP backend.
- No V5 MLPSO was supplied in build app; legacy MLPSO is not run on V5 inputs.

Source provenance and SHA-256 values: backend/solver_v5/SOURCE_MANIFEST.json.
Exact, Micro-GA and fast decoder sources come from the parent build app folder.
Required reference decoder, base Improved GA helpers and workbook generator
come from correct workstation. Only imports were qualified to isolate these
modules from the legacy names.

Settings and the validation screen show CP-SAT time/workers and Micro-GA
population/epochs/decode budget for V5. Independent runs and seed apply to
Micro-GA as before. The decode budget is per run; the supplied solver separately
builds a refined seed (see seed diagnostics). Existing results never change.

Upload preserves the actual workbook calendar. Supply a generated V5 JSON
with a longer calendar when needed; the app does not extend calendars.
Selected Batch_Key totals must be divisible by 10 for exact 2C batches.
V5 job cloning/product reassignment require source workbook updates so cycle
quantities, processing and batch compatibility remain consistent.

Each JOB_OP, OIL_CYCLE, SETUP and 2C_BATCH event is preserved once.
Pooled 2C events include both job IDs; Gantt uses both colors and either job
filter includes the shared event. Table and Gantt share the event list.
V5 is not restricted to 10 operations per job. Physical calendars, resources,
OIL phases, streaming, batch composition and clean-pipeline setup are validated.

Verification:

    .\.venv\Scripts\python.exe -m pytest backend/tests -q
    .\.venv\Scripts\python.exe -m backend.tests.export_fixtures
    .\run-frontend.cmd check
    .\run-frontend.cmd build
    .\run-frontend.cmd test:production

Browser tests require the persisted fixture described above.
The V5 browser test uses isolated synthetic input and a 5-second CP-SAT budget.
The real 180-job workbook and a 20-job Micro-GA smoke run also passed.
Smoke budgets validate integration, not thesis optimization quality.

## Manual algorithm selection and Excel export (2026-10-06)

Select Jobs now offers Algorithm: Auto, each compatible solver, or Compare.
V5 supports Micro-GA (fast heuristic scheduling) and CP-SAT (exact optimization).
Legacy inputs support MILP, Improved GA and MLPSO. Explicit choices override
job-count routing and persist with the request, including reruns.
Compare runs selected solvers on the same frozen instance and waits for all;
exact solvers may consume their full configured time limit.
A heuristic result is not labeled proven optimal.

Export Excel downloads a real .xlsx workbook for the saved selected result:
Schedule Summary, Detailed Schedule and Algorithm Comparison. Schedule rows match the Gantt/table
operations exactly, including shared 2C batches once. Start/finish are Excel
datetime cells in factory local time; processing values stay numeric minutes.
Export never calls a solver. JSON export remains available.

Gantt opens in Hourly view: regular hourly ticks and at least 90 pixels per hour,
with date groups, horizontal scrolling and sticky workstation labels.
Fit Schedule still fits the actual operation range, never the full input horizon.
Zoom changes visualization only; saved timestamps and durations stay unchanged.

## Scheduling Method cards and revised Excel export (2026-10-06)

Scheduling uses Automatic (default) and Exact Search, plus Fast Scheduling for V5.
Parallel Optimization has been removed from the selection UI. Existing saved
comparison results remain readable. Automatic retains its original routing;
choose Fast Scheduling explicitly to use Micro-GA even with a small selection.

Export Results to Excel produces AL13_Schedule_<run_id>_<date>.xlsx with:
Schedule Summary, Detailed Schedule, Algorithm Comparison.
It includes method, actual algorithm, job count, real time-limit information,
metrics, run status and selection reason; one row per saved standardized event;
shift from the immutable snapshot; event identifiers/types, pooled contributors
and resources; all actual comparison participants with Selected / Not Selected.
Unavailable shift/time-limit data is explicitly identified. No solver is rerun.
Gantt, Schedule Table, existing saved results and mathematical code are unchanged.

Before repeated production-browser runs, recreate isolated fixtures with
.venv\Scripts\python.exe -m backend.tests.prepare_reopen_fixture.
This avoids accumulated synthetic schedules pushing older reference runs
out of the Analytics history list. It does not change production storage.


## Faster V5 scheduling (2026-10-12)

1. Select jobs, then choose **GA (SS Micro-GA Fast)**.
2. Validate the input. Choose **60 / 120 / 300 seconds** or enter custom seconds.
3. Run Optimization. Review the validated schedule, stop reason and attempts
   completed; export to Excel or accept/apply as before.

New CP-SAT runs default to 120 seconds rather than 600. Existing explicitly saved
settings are preserved, so select a preset to change them. Automatic now uses the configurable multi-factor rules documented in UI_REFINEMENT.md. A CP-SAT timeout without an incumbent cannot produce
a valid schedule; increase its budget or choose Fast Scheduling.

Micro-GA defaults: total search budget 120 seconds, 600 epochs without objective
improvement, minimum 1300 epochs before stagnation stopping. The patience counter
is independent of adaptive mutation/restarts. With restarts enabled, at least one
restart and 100 subsequent epochs are allowed before stagnation stopping. The
hard time/decode/generation budgets can stop search earlier. Set
`micro_no_improve=0` to disable stagnation stopping; the time budget still applies.
The existing population (24), generation cap (3000) and decode cap (12000) remain.
The 1300-epoch floor was selected after a 600-epoch floor lost about 5.4% makespan
on the checked 20-job instance; it is not a universal quality guarantee.

The Micro-GA time budget includes seed refinement and is shared across attempts.
It is cooperative: the current decode, initial incumbent, setup and final schedule
reconstruction/validation may add time. No new attempt starts after the budget.
Budgeted attempts run sequentially. A partially evaluated batch retains its best
completed candidate. Small selections can use the available distinct repaired
chromosomes instead of failing to fill a population of 24. No batch constraint is
relaxed. The final schedule must still pass the existing validator before use.

Direct solver Python calls keep limits disabled by default for reproducible
research: `time_limit=0`, `no_improve=0`. The app adapter explicitly supplies its
limits. Original import hashes remain in SOURCE_MANIFEST.json alongside hashes
and notes for these app changes. No dependency changes are required.

Reproduce the bounded comparison (20 jobs, seeds 42/1051, unchanged frozen JSON):
`.venv\Scripts\python.exe -B -m backend.tests.benchmark_micro_stopping --final`
This takes several minutes. Run it without concurrent tests/builds. Results are
written to `.tmp-verification/micro-stopping/results-final.json`; no production
records are written. The benchmark requires the original local dataset at
`../../../correct workstation/benchmark_instances_v5_h60/AL13_n020_i01.json`.

Checks: `.venv\Scripts\python.exe -m pytest backend/tests -q`,
`run-frontend.cmd check`, `run-frontend.cmd build`.


## Current scheduling selection and Gantt UI (2026-10-13)

The four cards are **Automatic / CP-SAT / GA / MLPSO**. V5 supports CP-SAT and
SS Micro-GA Fast; legacy supports Improved GA and the actual MLPSO implementation.
An incompatible card is disabled with its reason. Historical MILP and comparison
results remain available; MILP is not relabeled as CP-SAT.

Automatic uses the server-side `/api/schedules/preview` decision also used by
validation and execution. It chooses one engine based on operation/event counts,
OIL cycles, estimated exact-10 batches/setups, resources, calendar windows,
precedence, job count, CP-SAT budget and engine availability. Manual selection
overrides it. Relevant settings are shown after validation; Run stays disabled
until the preview matches the current settings. The decision is recorded with
results and Excel exports. The rules are configurable heuristics, not benchmark
claims about the best algorithm. See [UI_REFINEMENT.md](UI_REFINEMENT.md) for rules,
availability limitations, changed files and verification.

Gantt uses 36px bars, 6px corners and non-overlapping visual lanes. Exactly
adjacent OIL/cycle or 2C batch events with the same contributors, operation,
workstation, resource assignment, status and window are grouped for display.
Real gaps stay empty. Counts, details, table and exports retain original events.
Shared batches use the first contributor's solid color and colored contributor
markers; details list all jobs. Zoom/fit keep true time proportions.

### V5 planning calendar: 60 days (2026-10-15)

V5 Excel uploads now use 60 days from the first staffed shift, matching the benchmark generator's `--horizon-days 60` default. The generator repeats the source staffed shift pattern and breaks; downtime is applied only at explicitly supplied timestamps. Job release/due dates, processing times and solver constraints are unchanged. Imported frozen JSON retains its supplied horizon.

The existing 180-job Excel dataset was revised to end at 2026-11-30 06:00, retaining the original dataset and saved scheduling snapshots. Reload the app and start a new Scheduling selection to use the new default; Run Again on an old result intentionally retains the old request. A longer calendar does not guarantee that a heuristic finds a feasible schedule within its search budget.

### V5 paired benchmark runner (2026-11-08)

Use `backend.benchmark_v5` for SS Micro-GA Fast versus CP-SAT from **this App**.
It calls the current `v5_adapter` functions directly. The older
`backend.tests.benchmark_micro_stopping` helper only compares Micro-GA stopping
configurations; external `run_experiments.py` copies are not this runner.

Run from this project directory, supplying an existing **frozen V5 JSON** (not a
schedule export or Excel workbook). Example command for a future experiment:

```powershell
.venv\Scripts\python.exe -B -m backend.benchmark_v5 --instance "path\to\frozen.json" --output-dir "benchmark-results\experiment-001" --seconds 300 --seeds 42 1051 2060 --repeats 1
```

The output directory must not exist. The runner never resumes, reuses cached
results or overwrites existing experiments, even for identical configurations.
It does not connect to the application database or regenerate the calendar.

Protocol `paired-single-worker-search-cap-v1`:

- Same exact frozen bytes/calendar/objective `MIN_CMAX_FINAL_2C` for both solvers.
- One attempt per algorithm/seed, one CP-SAT worker, sequential execution;
  algorithm order alternates between pairs. Same nominal search-time cap.
- Other settings keep App defaults. Optional `--settings settings.json` accepts
  a `SolverSettings` object, including Micro-GA epoch/decode/patience limits.
  Seed, attempts, workers and both time limits are explicitly overridden by the
  paired protocol; requested and effective settings are both recorded. App
  defaults and persisted configuration are never changed.
- This is **not equal end-to-end runtime or equal evaluation count**. Micro-GA
  includes seed refinement in its cooperative search budget and may stop early
  on another limit. CP-SAT applies the cap inside `Solve()`, excluding model
  construction. Final reconstruction/validation adds elapsed time.
- `--repeats 2` repeats each seed for reproducibility checks. Wall-clock-limited
  searches can differ between repetitions even with a fixed seed.

Artifacts: exact input copy `frozen.json`; `manifest.json` with input/calendar
hashes, full effective settings, solver signature defaults, backend source
hashes, Git revision/status, package/Python/platform versions and protocol;
per-attempt logs and JSON with raw plus validated results; `results.json` with
paired comparisons; `results.csv` with per-attempt metrics. GA evaluations and
seed-refinement decodes are separate. CP-SAT evaluations are null (not comparable
to GA decoder calls); branches/conflicts are recorded instead. CP-SAT's exact
non-optimal stopping cause is not exposed by the adapter, so the runner records
solver status and marks the cause unknown rather than assuming timeout.

Gap to the CP optimum is reported only when both results pass validation and
the CP incumbent equals its lower bound. Otherwise the runner reports only a
difference to the CP feasible incumbent, or null when comparison is unavailable.
Changing frozen input or backend source during an experiment aborts comparison.
Missing metrics remain null. Use a new output directory and a fresh Python
process after any code/configuration changes.

Small smoke tests have been run; no official long benchmark was executed.
See [runner verification report](SCHEDULING_BENCHMARK_RUNNER_REPORT.md) for evidence
and limitations before planning thesis experiments.
