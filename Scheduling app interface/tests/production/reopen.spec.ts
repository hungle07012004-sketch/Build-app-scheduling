import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));

test('Production build: same-origin API, persisted reopening, immutable snapshots and identical Gantt/table data',async({page,request})=>{
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  const health=await request.get('/api/health');
  expect(health.ok()).toBeTruthy();
  expect((await health.json()).status).toBe('ok');
  await page.goto('/');
  await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  const token=await page.evaluate(()=>sessionStorage.getItem('al13-token'));
  const headers={Authorization:'Bearer '+token};
  const before=await (await request.get('/api/schedules/'+fixture.small_id+'/result',{headers})).json();
  expect(before.accepted_at).toBeTruthy();
  expect(before.applied_at).toBeTruthy();
  const original=readFileSync(fixture.snapshot_path);
  expect(createHash('sha256').update(original).digest('hex')).toBe(fixture.snapshot_sha256);

  await page.getByRole('button',{name:'Analytics',exact:true}).click();
  await page.getByText(fixture.small_id,{exact:true}).click();
  await expect(page.getByRole('heading',{name:'Scheduling Result'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Applied',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Schedule Table',exact:true}).click();
  const table=await page.locator('tbody tr').evaluateAll(rows=>rows.map(row=>Array.from(row.querySelectorAll('td')).slice(0,5).map(cell=>cell.textContent)));
  expect(table).toEqual(before.selected.operations.map((o:any)=>[o.jobId,o.operation,o.workstation,o.startTime,o.finishTime]));
  await page.getByRole('button',{name:'Gantt View',exact:true}).click();
  await page.getByRole('button',{name:'Hourly view',exact:true}).click();
  const bars=await page.getByTestId('gantt-bar').evaluateAll(elements=>elements.map(el=>[el.getAttribute('data-job'),el.getAttribute('data-operation'),el.getAttribute('data-start'),el.getAttribute('data-finish')]).sort());
  expect(bars).toEqual(before.selected.operations.map((o:any)=>[o.jobId,o.operation,o.startTime,o.finishTime]).sort());
  await page.screenshot({path:'.tmp-verification/production-gantt.png',fullPage:true});

  const edited=await request.patch('/api/input/'+fixture.small_dataset_id+'/jobs/J001',{
    headers,data:{priority:3,due_datetime:'2026-09-03T08:00'}});
  expect(edited.ok()).toBeTruthy();
  expect((await edited.json()).id).not.toBe(fixture.small_dataset_id);
  expect(readFileSync(fixture.snapshot_path).equals(original)).toBeTruthy();
  const after=await (await request.get('/api/schedules/'+fixture.small_id+'/result',{headers})).json();
  expect(after).toEqual(before);

  // Clear in-memory page state and run pointer, then reopen solely through persisted history.
  await page.evaluate(()=>sessionStorage.removeItem('al13-run'));
  await page.reload();
  await page.getByRole('button',{name:'Analytics',exact:true}).click();
  await page.getByText(fixture.small_id,{exact:true}).click();
  await expect(page.getByRole('heading',{name:'Scheduling Result'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Applied',exact:true})).toBeDisabled();

  await page.getByRole('button',{name:'Analytics',exact:true}).click();
  await page.getByText(fixture.large_id,{exact:true}).click();
  await page.getByRole('button',{name:'Algorithm Comparison',exact:true}).click();
  await expect(page.locator('tbody tr')).toHaveCount(2);
  const large=await (await request.get('/api/schedules/'+fixture.large_id+'/result',{headers})).json();
  expect(large.algorithms.every((r:any)=>r.feasible)).toBeTruthy();
  expect(new Set(large.algorithms.map((r:any)=>r.instance_sha256)).size).toBe(1);
  await page.getByRole('button',{name:'Schedule Table',exact:true}).click();
  await expect(page.locator('tbody tr')).toHaveCount(500);
  const exportResponse=await request.get('/api/schedules/'+fixture.large_id+'/export.xlsx',{headers});
  expect(exportResponse.ok()).toBeTruthy();
  expect(exportResponse.headers()['content-type']).toContain('spreadsheetml');
  expect(exportResponse.headers()['content-disposition']).toContain('AL13_Schedule_'+fixture.large_id+'_');
  expect((await exportResponse.body()).subarray(0,2).toString()).toBe('PK');
  expect(await (await request.get('/api/schedules/'+fixture.large_id+'/result',{headers})).json()).toEqual(large);

  expect(errors).toEqual([]);
});
