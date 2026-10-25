import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {indexCalendar,operationShifts,weeklyShutdowns,calendarStamp,type CalendarWindow} from '../../src/components/ganttCalendar';
import type {GanttTask} from '../../src/data';

const time=(s:string)=>calendarStamp(s);
const window=(resource:string,start:string,end:string,id='S1'):CalendarWindow=>({station_id:resource,window_id:resource+start,calendar_date:start.slice(0,10),shift_id:id,start_datetime:start,end_datetime:end});
const operation=(startTime:string,finishTime:string,resourceUnits='WS_A;OP_A'):GanttTask=>({eventId:'test',jobId:'J001',jobIds:['J001'],product:'Test',operation:'PRE',workstation:'WS_A',startTime,finishTime,startMin:0,endMin:(time(finishTime)-time(startTime))/60000,processingMin:(time(finishTime)-time(startTime))/60000,status:'Scheduled',resourceUnits});

test('Shift labels use half-open ranges and keep midnight within Shift 3',()=>{
  expect(operationShifts(time('2026-10-02 13:00'),time('2026-10-02 15:00'))).toEqual([0,1]);
  expect(operationShifts(time('2026-10-02 21:00'),time('2026-10-02 23:00'))).toEqual([1,2]);
  expect(operationShifts(time('2026-10-02 23:00'),time('2026-10-03 01:00'))).toEqual([2]);
  expect(operationShifts(time('2026-10-02 05:00'),time('2026-10-02 07:00'))).toEqual([2,0]);
  expect(operationShifts(time('2026-10-02 13:00'),time('2026-10-02 14:00'))).toEqual([0]);
});

test('Adjacent saved shifts allow continuous cross-shift work for every listed resource',()=>{
  const rows=['WS_A','OP_A'].flatMap(r=>[window(r,'2026-10-02 06:00','2026-10-02 14:00'),window(r,'2026-10-02 14:00','2026-10-02 22:00','S2')]);
  const original=JSON.stringify(rows),op=operation('2026-10-02 13:00','2026-10-02 15:00'),before=JSON.stringify(op);
  rows.forEach(Object.freeze);Object.freeze(rows);Object.freeze(op);
  const calendar=indexCalendar(rows);
  expect(calendar.check(op)).toEqual({overlaps:[],weekly:[],unchecked:[]});
  expect(JSON.stringify(rows)).toBe(original);expect(JSON.stringify(op)).toBe(before);
});

test('Operator gap is detected without shading another resource or inventing a reason',()=>{
  const c=indexCalendar([window('WS_A','2026-10-02 06:00','2026-10-02 22:00'),window('OP_A','2026-10-02 06:00','2026-10-02 14:00'),window('OP_A','2026-10-02 14:30','2026-10-02 22:00')]);
  const check=c.check(operation('2026-10-02 13:00','2026-10-02 15:00'));
  expect(check.overlaps).toEqual([{resource:'OP_A',start:time('2026-10-02 14:00'),end:time('2026-10-02 14:30'),kind:'unknown',reason:'Reason not recorded'}]);
  expect(c.shading('WS_A',time('2026-10-02 06:00'),time('2026-10-02 22:00'))).toEqual([]);
  expect(c.check(operation('2026-10-02 13:00','2026-10-02 14:00')).overlaps).toEqual([]);
});

test('Saturday shift stops at Sunday midnight and Monday restarts at 06:00',()=>{
  const c=indexCalendar(['WS_A','OP_A'].flatMap(r=>[window(r,'2026-10-03 22:00','2026-10-04 00:00'),window(r,'2026-10-05 06:00','2026-10-05 14:00')]));
  const a=time('2026-10-04 00:00'),b=time('2026-10-05 00:00');
  expect(c.shading('WS_A',a,b)).toEqual([{resource:'WS_A',start:a,end:b,kind:'sunday',reason:'Reason not recorded'}]);
  expect(c.shading('WS_A',b,time('2026-10-05 07:00'))).toEqual([{resource:'WS_A',start:b,end:time('2026-10-05 06:00'),kind:'sunday',reason:'Reason not recorded'}]);
  const check=c.check(operation('2026-10-03 23:00','2026-10-04 01:00'));
  expect(check.overlaps.map(g=>g.resource)).toEqual(['WS_A','OP_A']);
  expect(check.weekly).toEqual([{start:a,end:time('2026-10-04 01:00')}]);
  expect(c.check(operation('2026-10-05 05:00','2026-10-05 07:00')).overlaps.every(g=>g.end===time('2026-10-05 06:00'))).toBe(true);
  expect(c.check(operation('2026-10-05 06:00','2026-10-05 07:00')).overlaps).toEqual([]);
});

test('Saved Sunday availability is preserved and reported as a separate weekly-rule conflict',()=>{
  const c=indexCalendar(['WS_A','OP_A'].map(r=>window(r,'2026-10-04 00:00','2026-10-05 06:00')));
  const check=c.check(operation('2026-10-04 10:00','2026-10-04 11:00'));
  expect(check.overlaps).toEqual([]);expect(check.weekly).toHaveLength(1);
  expect(c.shading('WS_A',time('2026-10-04 00:00'),time('2026-10-05 00:00'))).toEqual([]);
  expect(weeklyShutdowns(time('2026-10-03 22:00'),time('2026-10-05 07:00'))).toHaveLength(2);
});

test('Missing calendars and missing OIL phase timestamps remain incomplete checks, not false violations',()=>{
  const c=indexCalendar([window('WS_A','2026-10-02 06:00','2026-10-02 22:00'),window('OP_A','2026-10-02 06:00','2026-10-02 14:00')]);
  const check=c.check(operation('2026-10-02 13:00','2026-10-02 15:00','WS_A;OP_A(load);OP_MISSING'));
  expect(check.overlaps).toEqual([]);expect(check.unchecked).toHaveLength(2);
  expect(check.unchecked.join(' ')).toContain('phase timestamps not saved');
  expect(c.gaps('OP_MISSING',time('2026-10-02 13:00'),time('2026-10-02 15:00'))).toBeNull();
});

test('Large saved result audit preserves all event data',()=>{
  const saved=JSON.parse(readFileSync('.tmp-verification/large-gantt-result.json','utf8'));
  const windows=JSON.parse(readFileSync('.tmp-verification/large-gantt-calendar.json','utf8'));
  const ops:GanttTask[]=saved.selected.operations,original=JSON.stringify(ops),before=JSON.stringify(windows);
  const c=indexCalendar(windows),checks=ops.map(o=>c.check(o));
  console.log('Saved calendar audit:',JSON.stringify({events:ops.length,overlaps:checks.filter(c=>c.overlaps.length).length,weeklyConflicts:checks.filter(c=>c.weekly.length).length,partial:checks.filter(c=>c.unchecked.length).length}));
  expect(checks).toHaveLength(11290);expect(JSON.stringify(ops)).toBe(original);expect(JSON.stringify(windows)).toBe(before);
  expect(checks.filter(c=>c.weekly.length)).toHaveLength(1909);
  expect(checks.filter(c=>c.overlaps.length)).toHaveLength(0);
});

for(const corrected of [false,true])test(`Calendar legend, shift tooltip and review (corrected=${corrected})`,async({page})=>{
  const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
  const saved=JSON.parse(readFileSync('.tmp-verification/large-gantt-result.json','utf8'));
  const ops=[operation('2026-10-02 13:00','2026-10-02 15:00'),{...operation('2026-10-02 09:00','2026-10-02 10:00','WS_B'),workstation:'WS_B',jobId:'J002',jobIds:['J002'],eventId:'other'}];
  ops.push({...operation('2026-10-04 10:00','2026-10-04 11:00'),jobId:'J003',jobIds:['J003'],eventId:'weekly'});
  const calendar=[window('WS_A','2026-10-02 06:00','2026-10-02 14:00'),window('WS_A','2026-10-02 14:00','2026-10-02 22:00','S2'),window('OP_A','2026-10-02 06:00','2026-10-02 14:00'),window('OP_A','2026-10-02 14:30','2026-10-02 22:00'),window('WS_B','2026-10-02 06:00','2026-10-02 10:00'),window('WS_B','2026-10-02 10:30','2026-10-02 22:00')];
  const calls:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&/schedules.*(run|rerun)/.test(r.url()))calls.push(r.url());});
  await page.route('**/api/schedules/'+fixture.small_id+'/result',r=>r.fulfill({json:{...saved,selected:{...saved.selected,operations:ops,metadata:{...saved.selected.metadata,...(corrected?{working_calendar_policy:'MON_SAT_SUN00_MON06_V1'}:{})}}}}));
  await page.route('**/api/calendar?run_id=*',r=>r.fulfill({json:calendar}));
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();await page.getByRole('button',{name:'Analytics',exact:true}).click();await page.getByText(fixture.small_id,{exact:true}).click();
  await expect(page.getByLabel('Calendar legend')).not.toContainText('Break:');
  await expect(page.getByLabel('Calendar legend')).toContainText('Unavailable');
  await expect(page.getByLabel('Calendar legend')).toContainText('Sunday Shutdown');
  await expect(page.getByLabel('Calendar legend')).toContainText('Shift Boundary');
  await expect(page.getByLabel('Calendar legend')).toContainText('Shift 3 (22:00–06:00)');
  const bar=page.locator('[data-testid=gantt-bar][data-job=J001]');await bar.hover();
  await expect(page.getByRole('tooltip')).toContainText('Shift 1 → Shift 2');
  await expect(page.getByRole('tooltip')).toContainText('1 saved-calendar overlap');
  await page.getByLabel('Shift filter',{exact:true}).selectOption('Shift 2');await expect(bar).toBeVisible();
  await page.getByLabel('Shift filter',{exact:true}).selectOption('All');
  const gap=page.locator('[data-testid=non-working-time][data-resource=WS_B][data-start="'+time('2026-10-02 10:00')+'"]');
  await gap.hover();await expect(page.getByRole('tooltip')).toContainText('WS_B');await expect(page.getByRole('tooltip')).toContainText('Reason not recorded');
  await expect(page.getByRole('tooltip')).toContainText('2026-10-02 10:30:00');
  const review=page.getByTestId('calendar-review');
  await expect(review).not.toHaveAttribute('open');
  await expect(page.getByTestId('weekly-conflict-badge')).toHaveText('1 weekly-rule conflicts');
  await review.locator('summary').click();
  await expect(review).toContainText(corrected?'Generated with the corrected calendar.':'Historical / unversioned calendar:');
  await expect(review.getByRole('region',{name:'Saved-calendar overlaps',exact:true})).toBeAttached();
  await expect(review.getByRole('region',{name:'Weekly-rule conflicts',exact:true})).toContainText('J003');
  await expect(review.getByRole('region',{name:'Incomplete checks',exact:true})).toContainText('None detected');
  await expect(review).toContainText('Saved-calendar overlap: OP_A');
  await expect(review.getByRole('region',{name:'Weekly-rule conflicts',exact:true})).toContainText('WS_A;OP_A');
  await expect(review).toContainText('2026-10-02 14:30:00');
  await page.screenshot({path:'.tmp-verification/calendar-annotations.png'});
  expect(await bar.getAttribute('data-start')).toBe(ops[0].startTime);expect(await bar.getAttribute('data-finish')).toBe(ops[0].finishTime);expect(calls).toEqual([]);
});
