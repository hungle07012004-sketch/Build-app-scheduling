import {test,expect} from '@playwright/test';
import {runLabel,runDate,shortRunContext} from '../../src/runLabels';
test('Schedule label uses stable creation time in factory timezone, not UUID or completion time',()=>{
  const run={id:'eca16733abcdef',created_at:'2026-11-01T12:55:05.346477+00:00',finished_at:'2026-11-02T13:00:00Z'};
  expect(runLabel(run)).toBe('Schedule · 01 Nov 2026, 19:55:05');
  expect(runLabel({...run,finished_at:'2026-12-01T00:00:00Z'})).toBe(runLabel(run));
  expect(runLabel(run)).not.toContain(run.id);
  expect(runDate(run.finished_at)).toBe('02 Nov 2026, 20:00');
});
test('Missing or invalid dates are honest and do not crash',()=>{
  expect(runLabel({})).toBe('Schedule · Date not recorded');
  expect(runDate('bad-date')).toBe('Time not recorded');
  expect(runDate()).toBe('Time not recorded');
});
test('Compact makespan context omits technical IDs without fabricating budgets',()=>{
  expect(shortRunContext({jobs:20,algo:'SS Micro-GA Fast',search_settings:{micro_time_limit:120},status:'completed',dataset_id:'opaque-id'})).toBe('20 jobs · 120 s budget · completed');
  expect(shortRunContext({jobs:0,status:'failed'})).toBe('0 jobs · failed');
});
