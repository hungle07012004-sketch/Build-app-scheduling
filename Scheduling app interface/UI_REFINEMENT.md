# Scheduling method and Gantt refinement - 2026-10-13

## Automatic rules

The server calculates a decision from the frozen-in-memory selection in
`/api/schedules/preview`. Validation and execution call the same selection
function. Preview creates no run and executes no solver. Manual requests bypass
the automatic choice. Results persist the rule inputs, thresholds, mode and reason.

V5 CP-SAT is chosen when available and all configured limits pass:

| Environment variable | Default |
|---|---:|
| AUTO_CP_MAX_JOBS | SMALL_SCALE_MAX_JOBS (20) |
| AUTO_CP_MAX_EVENTS | 250 |
| AUTO_CP_MAX_RESOURCES | 30 |
| AUTO_CP_MAX_WINDOWS | 600 |
| AUTO_CP_MAX_SCORE | 450 |
| AUTO_CP_MIN_SECONDS | 60 |

Estimated events = job operations + OIL cycles + floor(total quantity / 10)
+ job count times setup-resource count. The latter two terms estimate batching
and setup model size; they do not replace manufacturing constraints or validate
batch feasibility. Difficulty score = events + 0.2 * precedence edges
+ 3 * resource count + 0.05 * calendar-window count. Otherwise V5 chooses GA.

Legacy uses GA unless operations >= AUTO_MLPSO_MIN_OPERATIONS (400), resources >=
AUTO_MLPSO_MIN_RESOURCES (30), or calendar windows >= AUTO_MLPSO_MIN_WINDOWS (1500),
in which case it prefers MLPSO. Unavailable engines are excluded with an explicit
fallback reason; no available engine blocks execution. These are deterministic,
configurable heuristics without a claim that the chosen engine is objectively best.

## Available engines and preserved behavior

- V5: CP-SAT / GA (SS Micro-GA Fast). MLPSO is unavailable for this schema.
- Legacy: GA (Improved GA + IG) / the existing MLPSO. CP-SAT is unavailable.
- Four cards remain visible in both schemas; incompatible choices are disabled.
- No replacement PSO, new decoder or mathematical solver was introduced.
- Historical MILP and multi-algorithm requests/results remain supported.
- Manual single choices execute only that engine; Automatic now executes one.
- GA mutation/crossover and MLPSO mr/pcr/gcr settings are forwarded to existing
  supported solver parameters. Defaults retain existing values.
- CP-SAT native status is shown separately. Feasible results are not labeled
  globally optimal without `proven_optimal`.
- Gantt, table, comparison and Excel all consume persisted standardized results.

## Gantt representation

OIL rows contain actual OIL-cycle occupancy intervals, not duplicate renderings.
2C rows contain real exact-10 batch events, including batches shared by jobs.
The visualization groups only exactly adjacent events with the same operation,
contributors, workstation, window, resource assignment and execution status.
Gaps, overlaps and contributor changes always start separate bars. Nothing is
merged in the saved schedule. Details expose original IDs and times per event.

All bars have height 36px and radius 6px, actual time-proportional widths, common
label sizing rules and hover/selection styling. Overlaps use separate lanes;
rows share a height sufficient for the maximum lane count. Stable job colors
are kept, including contributor markers on pooled batches. The hourly grid uses
a CSS background instead of one DOM node per tick per row. Grouping/lane layout
is memoized. Counts report raw operations separately from visual bar count.
The existing Fit Schedule, hourly view, zoom, filters, sticky headers/labels,
details and navigation to the raw Schedule Table remain available.

## Files created

- backend/services/algorithm_selection.py
- src/algorithmSettings.ts
- src/components/ganttLayout.ts
- backend/tests/test_algorithm_selection.py
- tests/production/automatic.spec.ts
- tests/production/grouped-gantt.spec.ts
- UI_REFINEMENT.md

## Files modified

- backend/services/instance_service.py
- backend/services/optimization_service.py
- backend/api/scheduling.py
- backend/models/schemas.py
- backend/services/algorithms/v5_adapter.py
- backend/services/algorithms/improved_ga_adapter.py
- backend/services/algorithms/mlpso_adapter.py
- backend/services/excel_service.py
- src/components/SchedulingMethod.tsx
- src/components/GanttChart.tsx
- src/pages/ScheduleFlow.tsx
- src/pages/SettingsPage.tsx
- src/algorithms.ts
- src/data.ts
- backend/tests/test_integration.py
- tests/production/method.spec.ts
- tests/production/fast.spec.ts
- tests/production/v5.spec.ts
- README.md and VERIFICATION.md
- dist/ (generated production build)

No dependency or lockfile changes were needed. Solver implementation files,
constraints, objectives and decoders were not edited for this refinement.

## Verification scope

Backend regression tests include deterministic multi-factor selection,
availability fallback, budget changes, preview without DB writes, manual override,
preview/execution consistency, real solver adapters and persisted snapshot checks.
Browser tests exercise real V5 Automatic budget switching and GA execution,
manual CP-SAT and GA flows, Excel export, acceptance, application and reopening.
Synthetic 10/30/50/100-job chart fixtures test grouping, original event coverage,
exact intervals, overlaps/lanes, proportional widths, filter/tooltips and raw-table
counts without solver calls. The 100-job fixture reduces 800 events to 500 bars.
These synthetic visualization checks are not optimization benchmarks or claims of
production feasibility. Existing 100-operation MILP results are replayed unchanged.


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

## Gantt tooltip and saved shift correction

- Measure the tooltip before positioning it above/below/beside its operation. Remove the duplicate browser-native title tooltip; retain accessible operation labels and exact timestamps.
- Keep event IDs and event types in the click-to-open details panel; hover shows the essential schedule fields.
- Load shift windows from `/api/calendar?run_id=...`, reading the existing frozen instance without modifying the schedule. Calendar loading is independent of job loading. Missing window IDs can be resolved using a containing window for the same workstation; absent calendar metadata is explicitly labeled, never guessed.
- Verification: frontend typecheck and production build passed; frozen-calendar read/immutability regression passed; saved MILP and grouped Gantt browser checks passed for 10/30/50/100 jobs. No solver was executed for these checks.

## Solver result persistence repair (2026-10-15)

A feasible 180-job GA run could leave the UI running forever: convergence statistics contained Infinity for infeasible population members, which strict JSON persistence rejected. The error handler then attempted to serialize the same invalid result again.

Search metadata now converts non-finite diagnostic values to null without changing operation data, objective values or constraints. Each worker verifies JSON serialization and saves an atomic `.result.json` checkpoint before returning. The coordinator handles non-serializable results as failures and no longer reuses potentially invalid payloads in its final error handler.

Verification: 11 targeted backend tests passed, including a regression for stuck run state; a short 180-job diagnostic produced 11,301 validated events and strict JSON output. The original run's best permutation was not saved, so its exact 64,041.692-minute incumbent cannot be recovered from logs alone. A new 300-second run uses the original immutable 60-day snapshot inputs.

Recovery verification completed: replacement run `93e3d5cd15514a379fe252b93debac72` finished through the live API with 180 jobs, 11,290 validated events, makespan 64,020.396 minutes and solver runtime 300.099 seconds. Status is completed, validation issues are empty, checkpoint matches persisted result, and snapshot hash is unchanged. API reopening and Excel export passed (three sheets; Detailed Schedule has 11,290 data rows). The interrupted original remains in history with a reference to the replacement. No schedule was accepted or applied automatically.

## Large Gantt visibility and computation presets (2026-10-17)

Targeted runtime changes are limited to `src/components/GanttChart.tsx` and `src/pages/ScheduleFlow.tsx`.

- Hourly padding is bounded to 15-60 minutes. Opening and job navigation scroll to the earliest matching saved event; search/selection highlights remain intact. Zoom retains the visible time center. Workstation labels remain sticky.
- Fit Schedule covers the saved request's planning horizon (60 days for the current V5 result) and all operation timestamps, with adaptive date ticks. Hourly view continues to use actual operation bounds.
- For large results, offscreen bars/ticks are not mounted until scrolling brings them near the viewport. Calendar lookups, operation bounds, layout and filter choices are cached. The complete standardized operation array remains unchanged; Fit renders the complete grouped schedule.
- Added 600 sec (10 min) and 900 sec (15 min) presets alongside 60/120/300 and Custom. Default remains 120 sec. Existing CP-SAT and Micro-GA settings, APIs and search-budget behavior are unchanged. Computation time remains separate from the production calendar.

Validation: TypeScript PASS; production build PASS; 14 targeted backend tests PASS (budget forwarding plus existing stopping regressions); 8 browser tests PASS (legacy and grouped Gantt, the saved 180-job/11,290-event schedule, synthetic 22,580-event stress case, and all five presets with Automatic/CP-SAT/GA). Browser tests send real preview requests and intercept run requests to avoid optimization; adapter tests verify the selected budget reaches the existing solver entrypoints. Saved selected result was compared before/after and is identical. No production solver rerun, acceptance or application was performed.

## Daily Gantt navigation

Runtime change is confined to `src/components/GanttChart.tsx`. Default view is one local calendar day (00:00 through the following midnight), starting on the first operation's date. Previous/Next Day and the date input are bounded by the saved request's calendar dates; 3/7-day windows are truncated at its final calendar day. Fit Schedule and the existing Hourly view remain available.

Only bars overlapping the window are mounted. Their display geometry is clipped at the window boundaries while event identity, original start/finish, duration, grouping, tooltip and job details remain unchanged. Search/job selection navigates to the matching day; manual day navigation does not reset the search or filters. The operation total remains the filtered full-result count, with a separate selected-days count.

Calendar gaps are shaded per workstation using saved working-window timestamps. Missing calendar information is not assumed to mean downtime. The current production calendar explicitly includes Sunday shifts, so these remain working time; a Sunday without working windows is fully shaded. No Sunday closure was invented and no production calendar or backend was changed.

Validation: TypeScript PASS; production build PASS; 11 browser tests PASS, covering first/last day, 1/3/7-day ranges, operations crossing both boundaries, full-day and partial Sunday downtime, filters, search, zoom, hourly/full overview, persisted reopening and immutable snapshots. Replayed the existing 180-job/11,290-event result and a 22,580-event synthetic stress case without invoking a solver. Saved production result comparison remained identical. Visual review captured daily, cross-midnight and Sunday views in `.tmp-verification/`.
