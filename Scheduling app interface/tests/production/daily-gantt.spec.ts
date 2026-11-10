import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
const saved=JSON.parse(readFileSync('tests/fixtures/current-milp-result.json','utf8'));
const stamp=(s:string)=>new Date(s.replace(' ','T')).getTime();
const ops=[
  ['before','J001','2026-10-02 23:00:00','2026-10-03 01:00:00'],
  ['after','J002','2026-10-03 23:00:00','2026-10-04 01:00:00'],
  ['spanning','J003','2026-10-02 22:00:00','2026-10-04 02:00:00'],
  ['last','J004','2026-11-29 22:00:00','2026-11-29 23:00:00'],
].map(([eventId,jobId,startTime,finishTime])=>({eventId,jobId,jobIds:[jobId],product:'Test',operation:'FINAL',workstation:'WS_FINAL',startTime,finishTime,
  startMin:(stamp(startTime)-stamp('2026-10-01 00:00:00'))/60000,endMin:(stamp(finishTime)-stamp('2026-10-01 00:00:00'))/60000,
  processingMin:(stamp(finishTime)-stamp(startTime))/60000,status:'Scheduled'}));

for(const sundayWorks of [false,true]) test(`Daily boundaries, overlap and Sunday calendar (working=${sundayWorks})`,async({page})=>{
  const replay={...saved,request:{...saved.request,horizon_start:'2026-10-01T00:00',horizon_end:'2026-11-30T00:00'},selected:{...saved.selected,operations:ops}};
  const calendar=[{station_id:'WS_FINAL',window_id:'Fri',shift_id:'Shift1',calendar_date:'2026-10-02',start_datetime:'2026-10-02 00:00:00',end_datetime:'2026-10-04 00:00:00'},
    ...(sundayWorks?[{station_id:'WS_FINAL',window_id:'Sun',shift_id:'Sunday shift',calendar_date:'2026-10-04',start_datetime:'2026-10-04 06:00:00',end_datetime:'2026-10-04 14:00:00'}]:[])];
  const calls:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&/schedules.*(run|rerun)/.test(r.url()))calls.push(r.url());});
  await page.route('**/api/schedules/'+fixture.small_id+'/result',r=>r.fulfill({json:replay}));
  await page.route('**/api/calendar?run_id=*',r=>r.fulfill({json:calendar}));
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();await page.getByRole('button',{name:'Schedule Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button', {name: 'Open schedule '+fixture.small_id, exact: true}).click();
  const picker=page.getByLabel('Gantt Date'),scroll=page.getByTestId('gantt-scroll'),bars=page.getByTestId('gantt-bar');
  await picker.fill('2026-10-03');
  await expect(bars).toHaveCount(3);
  await expect(page.getByTestId('window-operation-count')).toHaveText('3 in selected days');
  expect(Number(await scroll.getAttribute('data-domain-start'))).toBe(stamp('2026-10-03 00:00:00'));
  expect(Number(await scroll.getAttribute('data-domain-end'))).toBe(stamp('2026-10-04 00:00:00'));
  const geometry=await bars.evaluateAll(ns=>ns.map(n=>({id:n.getAttribute('data-job'),start:n.getAttribute('data-start'),finish:n.getAttribute('data-finish'),left:parseFloat((n as HTMLElement).style.left),width:parseFloat((n as HTMLElement).style.width),parent:n.parentElement!.clientWidth})));
  for(const g of geometry){const o=ops.find(o=>o.jobId===g.id)!;expect(g.start).toBe(o.startTime);expect(g.finish).toBe(o.finishTime);expect(g.left).toBeGreaterThanOrEqual(0);expect(g.left+g.width).toBeLessThanOrEqual(g.parent+1);}
  expect(geometry.find(g=>g.id==='J001')!.left).toBe(0);
  expect(geometry.find(g=>g.id==='J003')!.width).toBeCloseTo(geometry[0].parent,0);
  const crossing=page.locator('[data-testid="gantt-bar"][data-job="J001"]');
  await crossing.hover();await expect(page.getByRole('tooltip')).toContainText(ops[0].startTime);
  await crossing.click();await expect(page.getByLabel('Job Details',{exact:true})).toContainText(ops[0].finishTime);
  await expect(picker).toHaveValue('2026-10-03');
  await page.getByRole('button',{name:'Close job details',exact:true}).click();
  await page.getByLabel('Shift filter',{exact:true}).selectOption('Shift1');
  await expect(bars).toHaveCount(1);
  await page.getByLabel('Shift filter',{exact:true}).selectOption('All');
  await page.screenshot({path:test.info().outputPath(`daily-cross-midnight-${sundayWorks}.png`)});
  await page.getByRole('button',{name:'Next Day',exact:true}).click();
  await expect(picker).toHaveValue('2026-10-04');
  const gaps=page.getByTestId('non-working-time');await expect(gaps).toHaveCount(sundayWorks?2:1);
  const gapDuration=await gaps.evaluateAll(ns=>ns.reduce((a,n)=>a+Number(n.getAttribute('data-end'))-Number(n.getAttribute('data-start')),0));
  expect(gapDuration/3600000).toBe(sundayWorks?16:24);
  await page.screenshot({path:test.info().outputPath(`daily-sunday-${sundayWorks}.png`)});
  // Navigation remains on the requested day even while filtering.
  await page.getByLabel('Operation filter',{exact:true}).selectOption('FINAL');await expect(picker).toHaveValue('2026-10-04');
  await page.getByLabel('Workstation filter',{exact:true}).selectOption('WS_FINAL');await expect(picker).toHaveValue('2026-10-04');
  await page.getByRole('button',{name:'3 Days',exact:true}).click();
  expect(Number(await scroll.getAttribute('data-domain-end'))-Number(await scroll.getAttribute('data-domain-start'))).toBe(3*86400000);
  await page.getByRole('button',{name:'7 Days',exact:true}).click();
  expect(Number(await scroll.getAttribute('data-domain-end'))-Number(await scroll.getAttribute('data-domain-start'))).toBe(7*86400000);
  await picker.fill('2026-10-01');await expect(page.getByRole('button',{name:'Previous Day',exact:true})).toBeDisabled();
  await picker.fill('2026-11-29');await expect(page.getByRole('button',{name:'Next Day',exact:true})).toBeDisabled();
  expect(Number(await scroll.getAttribute('data-domain-end'))).toBe(stamp('2026-11-30 00:00:00'));
  await page.getByRole('button',{name:'1 Day',exact:true}).click();
  await page.getByLabel('Search Job',{exact:true}).fill('J001');await expect(picker).toHaveValue('2026-10-02');
  await page.getByRole('button',{name:'Next Day',exact:true}).click();await expect(picker).toHaveValue('2026-10-03');
  await expect(crossing).toHaveAttribute('data-highlighted','true');
  await page.getByRole('button',{name:'Clear / Reset',exact:true}).click();
  await page.getByRole('button',{name:'Fit Schedule',exact:true}).click();await expect(bars).toHaveCount(4);
  expect(Number(await scroll.getAttribute('data-domain-end'))).toBe(stamp('2026-11-30 00:00:00'));
  await page.screenshot({path:test.info().outputPath(`daily-calendar-${sundayWorks}.png`)});
  expect(calls).toEqual([]);
});
