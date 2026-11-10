# Analytics & Production Execution — UI verification

Date: 25 October 2026 (Asia/Saigon).

## Scope and changes

Production changes are limited to `src/pages/AnalyticsPage.tsx` and `src/pages/ExecutionPage.tsx`.

- Analytics uses thousands separators and two decimal places for measurements. Job counts remain whole numbers. Missing/non-finite metrics display an em dash; legitimate zero values remain zero.
- Makespan Trend uses compact R1–R7 labels, chronological order, and hover/keyboard-focus details containing full run IDs and formatted values. Missing samples create gaps instead of zero-valued points.
- Workstation Utilization uses horizontal bars labeled by unique workstation IDs; full station/group details remain in tooltips. Percentage bars use a scale of at least 100%.
- Algorithm Performance uses the actual feasible participants returned for the latest accepted run and the existing algorithm display-name mapping. No algorithm or result is fabricated.
- Runtime History displays abbreviated IDs with full IDs in tooltips and accessible button names. Clicking or pressing Enter still opens the original saved run. JSON report export preserves the original API object.
- Execution labels use the immutable run creation timestamp in factory time (UTC+07), plus a short run ID. Labels do not depend on row order, updated status, or execution `updated_at`. Missing creation dates fall back to the ID-based label without inventing a date. Full original IDs remain in tooltips, and status updates send the unchanged run ID.
- Cards wrap responsively; execution tables scroll horizontally within their container on narrow screens. No global styling or other page layout was changed.

## Verified KPI sources (calculations unchanged)

| Display | Existing source and interpretation |
|---|---|
| Makespan, average flow time, tardiness, tardy jobs | Selected result of the most recently accepted schedule, ordered by acceptance time |
| Algorithm Performance | Feasible algorithm results belonging to that same accepted run; makespan, not an aggregate benchmark |
| Makespan Trend | Last seven history entries with a selected result, displayed oldest to newest; acceptance is not required |
| Runtime History | Last five history entries with a selected result, newest first |
| Workstation utilization | API planned processing load divided by saved available minutes over the accepted horizon; not measured machine activity |
| Mean workstation utilization | Existing arithmetic mean over the current workstation list; missing accepted coverage or missing values produces an unknown display rather than averaging a partial list |
| Completed / scheduled jobs | Counts of execution records across all applied schedules; reported production status, not the latest accepted schedule alone |

The workstation endpoint lists the current dataset's workstations and matches accepted-plan utilization by ID. This distinction is now stated in the interface. Backend-produced numeric values are preserved; the UI does not reconstruct raw calendar denominators or change the utilization formula.

## Tests and evidence

| Check | Result |
|---|---|
| TypeScript (`tsc --noEmit`) | PASS |
| Vite production build | PASS; separate QA output directory |
| Six new Analytics/Execution browser cases | PASS, including one against actual persisted API data in isolated QA storage |
| Existing production suite plus first five new cases | 37/39 PASS in the full run; two failures investigated below |
| Targeted recheck | 3/3 PASS: two failed cases plus the added real-API Analytics case |
| Unique tests with final passing evidence | 40: 33 browser tests and 7 existing calendar helper tests; no skipped tests in the full run/recheck |
| Visual inspection | Desktop Analytics/Execution and narrow Analytics screenshots inspected; 1440px and 768px layout assertions PASS |
| Production backend/solver/calendar/data integrity | Hash comparison against task-start baseline: no changes |

The full-run failures were:

1. The existing 20,000-event Gantt test exceeded its five-second initial display assertion. It passed when run separately, without changing Gantt code or weakening its assertions. This remains evidence of test timing sensitivity, not a clean first-run suite pass.
2. The V5 workflow's final reopen action still searched for the visible full run ID. Its locator was updated to the accessible `Open schedule <full ID>` button, then the complete V5 test passed, including Excel, accept/apply, execution update and reopen.

Earlier setup attempts exposed accumulated runs in the reused QA database, pushing reference runs outside the intentionally limited five-row history. Extra test runs were removed only from the disposable QA copy. No production records were touched.

The injected missing-history-value fixture also exposed an existing Dashboard assumption: its first five history rows call `makespan.toFixed()` without a null guard. Dashboard is outside this task and was not changed. The Analytics missing-value test retains a null sample outside Dashboard's five-row slice so Analytics itself is exercised. This separate defensive-handling issue remains if such an incomplete history payload reaches Dashboard.

Evidence:

- [Full browser run](.tmp-qa/2026-10-25-analytics-ui/results.json)
- [Targeted recheck](.tmp-qa/2026-10-25-analytics-ui/recheck-results.json)
- [TypeScript log](.tmp-qa/2026-10-25-analytics-ui/typecheck.log)
- [Build log](.tmp-qa/2026-10-25-analytics-ui/build.log)
- [Integrity check](.tmp-qa/2026-10-25-analytics-ui/integrity.json)
- [Analytics desktop](.tmp-qa/2026-10-25-analytics-ui/analytics-1440.png)
- [Analytics narrow](.tmp-qa/2026-10-25-analytics-ui/analytics-768.png)
- [Execution desktop](.tmp-qa/2026-10-25-analytics-ui/execution-desktop.png)

## Modified files

Application:

- `src/pages/AnalyticsPage.tsx`
- `src/pages/ExecutionPage.tsx`

New targeted test: `tests/production/analytics-execution.spec.ts`.

Existing tests: only saved-run navigation locators were updated in `calendar-revision.spec.ts`, `compact-layout.spec.ts`, `daily-gantt.spec.ts`, `gantt-calendar.spec.ts`, `gantt.spec.ts`, `grouped-gantt.spec.ts`, `large-gantt.spec.ts`, `reopen.spec.ts`, `scaling.spec.ts`, `v5.spec.ts`, and `validation-export.spec.ts` under `tests/production/`.

This report is new. Temporary build, copied QA database, test results and screenshots are under `.tmp-qa/2026-10-25-analytics-ui/`.

No solver, decoder, backend API, production database, Working Calendar, horizon, saved schedule or scheduling logic was modified. Small solver runs performed by the existing integration suite used isolated QA storage. No production optimization was rerun. Backend tests were not rerun because no backend code changed. No lint script is configured.
