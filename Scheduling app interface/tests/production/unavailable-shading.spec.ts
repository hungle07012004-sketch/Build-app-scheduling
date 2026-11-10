import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {indexCalendar,calendarStamp} from '../../src/components/ganttCalendar';
const saved=JSON.parse(readFileSync('.tmp-qa/2026-10-29-shading/saved.json','utf8'));
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
const stamp=(time:string)=>calendarStamp('2026-10-05 '+time);

test('Saved Oct 5 availability is resource-specific and leaves all original events untouched',()=>{
  const before=JSON.stringify(saved),c=indexCalendar(saved.calendar);
  const stations=[...new Set<string>(saved.calendar.map((w:any)=>w.station_id))].filter(s=>s.startsWith('WS_'));
  expect(stations).toHaveLength(9);
  for(const resource of stations){
    const gaps=c.shading(resource,stamp('06:00'),stamp('22:00'));
    expect(gaps.map(g=>[g.start,g.end])).toEqual([[stamp(resource==='WS_LASER'?'09:30':'10:00'),stamp(resource==='WS_LASER'?'10:45':'10:30')],[stamp('18:00'),stamp('18:30')]]);
    expect(gaps.every(g=>g.resource===resource&&g.kind==='unknown')).toBe(true);
    expect(gaps.some(g=>g.start<=stamp('14:00')&&g.end>stamp('14:00'))).toBe(false);
  }
  expect(JSON.stringify(saved)).toBe(before);
});

test('Saved run shading labels, lighter fill, row ownership and navigation',async({page})=>{
  const writes:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&!r.url().endsWith('/auth/login'))writes.push(r.url());});
  await page.route('**/api/schedules/'+fixture.small_id+'/result',r=>r.fulfill({json:saved.result}));
  await page.route('**/api/calendar?run_id=*',r=>r.fulfill({json:saved.calendar}));
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await page.getByRole('button',{name:'Schedule Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button',{name:'Open schedule '+fixture.small_id,exact:true}).click();
  await page.getByLabel('Gantt Date',{exact:true}).fill('2026-10-05');
  await expect(page.getByLabel('Calendar legend')).toContainText('Unavailable (saved calendar)');
  const gap=page.locator(`[data-testid="non-working-time"][data-resource="WS_LASER"][data-start="${stamp('09:30')}"]`);
  await expect(gap).toHaveAttribute('data-end',String(stamp('10:45')));
  await expect(gap).toHaveClass(/bg-slate-200\/40/);
  await expect(gap.locator('xpath=../..')).toContainText('WS_LASER');
  await gap.hover();
  await expect(page.getByRole('tooltip')).toContainText('Unavailable according to saved calendar');
  await expect(page.getByRole('tooltip')).toContainText('Reason not recorded in saved calendar');
  await expect(page.getByRole('tooltip')).toContainText('2026-10-05 09:30:00');
  await expect(page.getByRole('tooltip')).toContainText('2026-10-05 10:45:00');
  await page.getByRole('button',{name:'Next Day',exact:true}).click();
  await expect(page.getByLabel('Gantt Date',{exact:true})).toHaveValue('2026-10-06');
  await page.getByRole('button',{name:'Previous Day',exact:true}).click();
  await expect(gap).toBeAttached();
  for(const bar of await page.getByTestId('gantt-bar').all()){
    const ids=JSON.parse((await bar.getAttribute('data-event-ids'))!);
    const ops=saved.result.selected.operations.filter((o:any)=>ids.includes(o.eventId));
    expect(ops.length).toBe(ids.length);
    expect(await bar.getAttribute('data-start')).toBe(ops.reduce((a:any,b:any)=>calendarStamp(a.startTime)<calendarStamp(b.startTime)?a:b).startTime);
    expect(await bar.getAttribute('data-finish')).toBe(ops.reduce((a:any,b:any)=>calendarStamp(a.finishTime)>calendarStamp(b.finishTime)?a:b).finishTime);
  }
  await page.screenshot({path:test.info().outputPath('shading.png')});
  expect(writes).toEqual([]);
});
