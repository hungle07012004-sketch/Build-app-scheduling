# Final verification — 2026-09-25

| Check | Result | Evidence |
|---|---|---|
| Backend tests | PASS — 28/28, 0 failed, 0 skipped | .tmp-verification/backend-results.xml; 51.57 s |
| Python environment | PASS | Python 3.13.12; FastAPI 0.141.1, pandas 2.3.3, SciPy 1.18.1 imported; pip check clean |
| Frontend typecheck | PASS | npm run check |
| Frontend lint | N/A | No lint script/linter configured; strict TypeScript check is enabled |
| Frontend production build | PASS | Vite 8.0.5 installed from frozen pnpm lock |
| API health | PASS | HTTP 200, status=ok on production server |
| Excel upload | PASS | Current backend suite and full browser workflow checkpoint |
| 10-job MILP flow | PASS | Current real-solver API test and full browser workflow checkpoint |
| 50-job Improved GA + MLPSO flow | PASS | Current real-solver API test, two processes, same instance hash; full browser workflow checkpoint |
| Gantt | PASS | Production browser test compares all operation/timing tooltips to saved solver operations |
| Schedule table | PASS | Production browser test compares Job/Operation/Station/Start/Finish exactly to API operations |
| Accept | PASS | Current backend guard tests and full browser workflow checkpoint |
| Apply | PASS | Rejected before acceptance; idempotent afterward |
| Production execution update | PASS | API/browser workflow tests and analytics completion count |
| Persisted schedule reopen | PASS | Fresh backend lifespan; new production server; browser reload with run pointer cleared |
| Frozen snapshot immutability | PASS | Input edit leaves snapshot bytes/hash and saved result unchanged |
| One failed solver | PASS | Both failure directions tested through orchestration; valid sibling result retained |

Full browser workflow checkpoint: 1 passed in 23.1 s.
Final production browser verification: 1 passed in 12.4 s, compiled frontend and locked dependencies, no new optimization.
Reports: .tmp-verification/backend-tests.txt, frontend-typecheck.txt, frontend-build.txt, production-browser-results.json.
Inputs are synthetic AL13 integration fixtures; these checks do not establish research solution quality.
One non-failing Starlette/httpx deprecation warning remains in the test client.

## Files

This directory has no .git. Git status/diff are unavailable.
The inventory is based on the original scaffold and files implemented in this task.

Created:
- backend/: FastAPI app/config/errors, production frontend host, requirements and requirements-lock.
- backend/api/: auth, input_data, scheduling, analytics.
- backend/database/db.py and backend/models/schemas.py.
- backend/services/: input_service, instance_service, validation_service, optimization_service, progress_service, result_standardizer, schedule_service, analytics_service.
- backend/services/algorithms/: common import boundary and MILP/Improved GA V3/MLPSO adapters.
- backend/tests/: fixtures, integration/persistence/frontend-host tests, fixture export and saved-result preparation.
- src/api.ts, src/AppData.tsx.
- src/components/ApiNotice.tsx, src/components/InputDataPanel.tsx.
- src/pages/ExecutionPage.tsx.
- tests/e2e/workflow.spec.ts, tests/production/reopen.spec.ts.
- playwright.config.ts, playwright.production.config.ts.
- run-backend.ps1, run-frontend.ps1, run-production.ps1, run-frontend.cmd, run-production.cmd.
- README.md and VERIFICATION.md.

Modified:
- src/App.tsx and src/data.ts.
- Existing pages: LoginPage, DashboardPage, JobsPage, ScheduleFlow, WorkstationsPage, AnalyticsPage, SettingsPage.
- Existing Sidebar and GanttChart components.
- vite.config.ts, package.json, pnpm-lock.yaml, .gitignore.

The original mathematical/data-generation modules in code/ were not rewritten or moved.
Ignored local artifacts include .venv, .tools, node_modules, dist, backend/storage, .tmp-*, test-results and tests/fixtures.

## Architecture

Existing React/Figma UI → /api → FastAPI → validation/frozen snapshot → adapters → original Python solvers.
SQLite stores input revisions, run parameters/seeds, results, accept/apply and execution events.
A background queue coordinates runs; large cases use two processes sharing the same snapshot file/hash.
Production serves dist and /api on the same FastAPI origin.

## Demo readiness and Windows EXE

Ready for a local thesis workflow demo using the verified inputs.
Before research-data acceptance, test the actual AL13 workbook and representative workloads with thesis parameters.

No EXE was built. Before packaging:
1. Accept real-workbook results and runtime criteria.
2. Bundle Python, native SciPy/pandas dependencies, original solvers and compiled dist.
3. Add a launcher, per-user writable storage/config/log paths, graceful worker shutdown.
4. Verify Windows multiprocessing spawn/freeze_support in the packaged launcher.
5. Test install, upload, solving, persistence and upgrades on clean Windows; verify signing/Application Control compatibility.

Model limits remain: unit-capacity workstations, fixed AL13 operation chain, MILP resource redundancy requirement, conservative FIFO preflight.
Running optimization is not resumed after restart; interrupted runs can be rerun.

Final launcher check: PowerShell execution policy blocks .ps1 on this machine; run-frontend.cmd and run-production.cmd were added as compatible launchers without changing security policy.

## Gantt presentation update - 2026-09-25

Scope: frontend visualization only; no backend, solver or saved result changes.
Existing saved 10-job MILP result: c2fcb2610ee4442586ce6d18eaf0e4d5 (100 operations).

- TypeScript: PASS (run-frontend.cmd check).
- Production build: PASS (run-frontend.cmd build).
- Production browser tests: PASS, 2/2 (run-frontend.cmd test:production).
- Bar/operation identity and Gantt/table count equality: PASS.
- Labels fit within bars; full Job/Operation labels at suitable zoom: PASS.
- Deterministic job colors across stations and page reload: PASS.
- J001 search and whole-job selection highlight all 10 operations: PASS.
- Tooltip start/finish/processing values and original calendar shift: PASS.
- Job, Workstation, Operation and Shift filters: PASS.
- Zoom bounds, Fit Schedule, regular hourly ticks and sticky column: PASS.
- View in Schedule Table selects the same job: PASS.
- Tab switching triggers no optimization requests: PASS.
- Saved schedule reopening, unchanged results and frozen snapshot bytes: PASS.
- Saved 50-job GA/MLPSO comparison and 500-row table reopening: PASS.

Files changed for this update:
src/components/GanttChart.tsx, src/pages/ScheduleFlow.tsx, src/data.ts,
tests/production/reopen.spec.ts, tests/e2e/workflow.spec.ts, README.md, VERIFICATION.md.
Created: tests/production/gantt.spec.ts.
Generated: dist/, .tmp-verification browser report and screenshots.

No dependencies added; existing lockfiles remain applicable.
Backend and long optimization tests were not rerun for this frontend-only change.
Their preceding results remain documented above, not claimed as new executions.

## Actual schedule range correction - 2026-09-25

Supersedes the earlier requested-horizon Fit behavior.
Gantt now fits earliest saved start through latest saved finish, with 3% padding
per side (minimum 15 minutes); initial opening and Fit use this same domain.
Axis density adapts to the visible duration. Narrow date groups omit text instead
of clipping date labels. Bars keep exact timestamp proportions, with a transparent
minimum 6px interaction target. No backend or solver files changed.

Current export 3cf4cba9e5024ff789e8664bfb0db9c5:
100 operations; earliest 2026-07-01 06:43:00;
latest 2026-07-02 11:04:54.600000; actual extent 1701.91 minutes.
The configured July 31 horizon is ignored. Default ticks: 2 hours.
TypeScript PASS; production build PASS; browser suite 3/3 PASS (11.3s).
Verified default and repeated Fit domain, exact bar position/duration proportions,
100/100 count, readable fitting labels, J001 highlighting/color consistency,
exact tooltip times, identical table rows and zero solver requests.
Existing persistence/snapshot and Gantt interaction tests also pass.
Screenshot: .tmp-verification/current-milp-fit.png.
The export is replayed unchanged in the isolated browser test; its original
dataset context is unavailable there and is reported as unavailable by the UI.

Changed: src/components/GanttChart.tsx, src/pages/ScheduleFlow.tsx,
tests/production/gantt.spec.ts, README.md, VERIFICATION.md.
Added: tests/production/scaling.spec.ts and a local ignored copy of the export.

## V5 solver integration - 2026-10-04

Source: parent build app/solve_milp_instance.py (CP-SAT),
solve_ss_micro_ga_fast_instance.py and benchmark_common_fast.py.
Dependencies: correct workstation V5 decoder, Improved GA helpers and generator.
Source/package hashes: backend/solver_v5/SOURCE_MANIFEST.json.
Solver math/search preserved; only package imports changed. Legacy code/ untouched.

PASS: backend 32/32 (44.22s; no skipped tests).
PASS: TypeScript and Vite production build.
PASS: production browser 4/4 (30.0s), including legacy reopening,
actual-time scaling and V5 pooled-batch Gantt/table/accept/apply/execution.
PASS: pip check; OR-Tools 9.15.6755 added and Python lockfile refreshed.
PASS: real V5 workbook: 180 jobs, 6291 OIL cycles, no validation errors.
PASS: real 20-job V5 Micro-GA: mu=4, 2 epochs, 100 per-run decode budget;
1239 validated events, makespan 9327.41499999998 minutes.
This smoke configuration is not a thesis search or optimality claim.
Both new solvers passed real tests on a small physical fixture covering full
and pooled batches, setup and OIL. The initial legacy MILP test skip did not
recur in the final full run. One existing Starlette/httpx warning remains.

New: backend/solver_v5/ (6 modules, init, source manifest),
backend/services/v5_service.py, backend/services/algorithms/v5_adapter.py,
backend/tests/test_v5.py, tests/production/v5.spec.ts.
Modified: backend/models/schemas.py; services input_service, instance_service,
validation_service, result_standardizer, optimization_service, progress_service;
backend/api/input_data.py; backend/tests/export_fixtures.py;
backend/requirements.txt, backend/requirements-lock.txt;
src/data.ts, src/AppData.tsx, src/pages/ScheduleFlow.tsx,
src/pages/SettingsPage.tsx, src/components/GanttChart.tsx; README.md, VERIFICATION.md.
No EXE created. Restart backend and upload V5 input to use the new solvers.

## User-selected algorithms, Excel and hourly Gantt - 2026-10-06

PASS: backend 37/37 (58.25s); frontend TypeScript; production build.
PASS: production browser 4/4 (13.9s).
Explicit CP-SAT and Micro-GA selections are persisted and executed on small V5
input. Wrong-schema and duplicate algorithm selections are rejected.
Auto remains backward compatible; Compare uses the same frozen input and
retains successful results if another solver fails.
Excel download verified in browser. Workbook rows, numeric processing times,
job contributors and datetime values verified against saved operations.
Text cells cannot become Excel formulas. Export never launches a solver.
Hourly Gantt opens with hour ticks / minimum 90px per hour; Fit keeps actual
schedule bounds. Regression covers colors, labels, zoom, filters, table
identity, saved reopening and frozen snapshot immutability.

Created: src/algorithms.ts, backend/services/excel_service.py.
Modified: backend/models/schemas.py, backend/services/optimization_service.py,
backend/api/scheduling.py, backend/tests/test_v5.py, src/api.ts,
src/pages/ScheduleFlow.tsx, src/pages/SettingsPage.tsx,
src/pages/AnalyticsPage.tsx, src/pages/DashboardPage.tsx,
src/components/GanttChart.tsx, tests/production/v5.spec.ts, README.md,
VERIFICATION.md. dist rebuilt. No solver code or dependency changes.
Existing Starlette/httpx test-client deprecation warning remains.

## Scheduling Method UI and requested workbook - 2026-10-06

Approved scope: leave backend scheduling/solver logic unchanged and display
V5 Parallel Optimization availability honestly.
Method cards: Automatic default, Exact Search, Parallel Optimization.
Size guidance uses /api/config; manual selection is retained on size changes.
V5 GA+PSO is unavailable; legacy parallel uses Improved GA + MLPSO.
60/120/300/Custom presets are disabled because no shared time budget exists.
Advanced settings use readable labels. Other pages, Gantt/table, solver math,
request schema, routing and stored schedules were not changed.

Export Results to Excel:
AL13_Schedule_<run_id>_<Asia/Ho_Chi_Minh date>.xlsx.
Exactly three sheets: Schedule Summary, Detailed Schedule, Algorithm Comparison.
Includes requested metrics, method/algorithm, job count, actual time-limit
availability, status/reason, snapshot shifts, event IDs/types/resources,
pooled contributors and selected/not-selected flags for actual participants.
Export does not rerun a solver or create invented PSO results.

PASS: frontend TypeScript; production build.
PASS: targeted backend/export integration tests 10/10 (6.40s).
PASS: browser 6/6 (23.5s), including CP-SAT download, existing legacy parallel
export, method guidance/availability, saved reopening and frozen snapshots.
Visual screenshots inspected: method-small-v5.png and scheduling-method-review.png.
A repeat run initially timed out because accumulated synthetic history hid the
old reference run. Recreated isolated fixtures and reran successfully.

Created: src/components/SchedulingMethod.tsx, tests/production/method.spec.ts.
Modified: src/pages/ScheduleFlow.tsx, src/api.ts,
backend/services/excel_service.py, backend/api/scheduling.py,
backend/tests/test_v5.py, tests/production/v5.spec.ts,
tests/production/reopen.spec.ts, tests/e2e/workflow.spec.ts,
README.md, VERIFICATION.md. dist rebuilt.


## Faster Micro-GA verification (2026-10-12)

Final benchmark: V5 AL13_n020_i01, 20 jobs, shared frozen SHA-256 `62d8d830242ae6948fee677c9706a77feb24a1d00db5bd70c62b6fc572c2b8f8`. Each before/after pair uses the same seed, population 24, 3000-epoch cap and 12000-decode cap. Baseline disables time/stagnation limits; app configuration uses 120 seconds, patience 600, minimum 1300 epochs. Runs were sequential without concurrent build/test commands.

| Seed | Baseline seconds | Early-stop seconds | Reduction | Epochs before / after | Makespan (both) |
|---|---:|---:|---:|---:|---:|
| 42 | 110.75 | 85.48 | 22.8% | 1924 / 1410 | 8340.446 |
| 1051 | 109.39 | 81.73 | 25.3% | 2341 / 1809 | 8340.446 |

All four results passed the existing V5 schedule validator. Snapshot bytes/hash were unchanged. Makespan, tardiness and flow time matched within each pair. Both tuned runs stopped for no improvement. This is one instance with two seeds, not a general guarantee or proof of optimality. An exploratory 600-epoch minimum lost ~5.4% makespan; that setting was rejected as the default.

PASS: TypeScript and frontend production build. PASS: four browser tests covering V5/legacy method selection, real Micro-GA budget submission/result/Excel without rerun, and CP-SAT accept/apply/execution/reopening. Visual inspection: `.tmp-verification/fast-scheduling-review.png`.

The first Fast Scheduling browser run exposed an existing small-population initialization failure (18 repaired chromosomes available versus requested 24). Fixed by allowing the available population and ending futile enumeration after all small-instance raw permutations are seen. Retest passed.

Changed production files: backend/models/schemas.py; backend/services/algorithms/v5_adapter.py; backend/solver_v5/solve_ss_micro_ga_fast_instance.py; backend/solver_v5/solve_improved_ga_instance.py; backend/solver_v5/SOURCE_MANIFEST.json; backend/services/excel_service.py; src/components/SchedulingMethod.tsx; src/pages/ScheduleFlow.tsx; src/pages/SettingsPage.tsx. Added backend/tests/test_micro_stopping.py, backend/tests/benchmark_micro_stopping.py, tests/production/fast.spec.ts; updated test_v5.py and method.spec.ts. README/VERIFICATION updated; dist rebuilt. No dependencies added; decoder, constraints, Gantt and table implementations were not changed.

Final backend suite: PASS, 46/46 in 53.44 seconds (`.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider`). Includes deadline during a partial batch, skipped attempts after timeout, frozen input bytes, small-population initialization, restart-independent patience, and final reconstruction not mislabeling the search stop. One existing Starlette/httpx deprecation warning. A new clock-mock test initially advanced time during seed decoding as well as final reconstruction; scoped the mock to keep_schedule=True, then the full suite passed.


# Scheduling UI refinement (2026-10-13)

See UI_REFINEMENT.md for rules, grouping semantics, file list and verification details.


## Final results - 2026-10-13

- Backend: PASS, 52/52 (59.29s), with the existing Starlette/httpx deprecation warning.
- Frontend TypeScript: PASS (`run-frontend.cmd check`).
- Frontend production build: PASS (`run-frontend.cmd build`, 348ms final build).
- Production browser tests: PASS, 12/12 (42.8s), on a freshly copied isolated DB.
- Visual review: automatic-method-review.png, v5-result.png and grouped-gantt-10.png / grouped-gantt-100.png in .tmp-verification/.
- No lint script is configured in package.json.

Tests initially caught and resolved a missing-preview null dereference. Synthetic
chart tests were corrected to verify proportional width at the actual viewport
scale, and reopening assertions now wait for async rendering. Old repeated-test
records were cleared by creating a new isolated fixture copy, not by changing
production storage. Windows sandbox blocked pytest's old temporary directory and
hung browser teardown; final tests ran with approved escalation and exited cleanly.

Availability limitations remain intentional: no compatible V5 MLPSO is installed,
and the existing CP-SAT model accepts V5, not legacy datasets. Disabled cards
explain this; algorithms are never silently replaced in manual mode. Automatic
rules are heuristics rather than an empirical optimal-engine predictor. Long
schedules still require zoom/filters to label short events; their widths are never
inflated or real gaps concealed.
