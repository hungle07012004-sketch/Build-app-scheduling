import {test,expect,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
const saved=JSON.parse(readFileSync('.tmp-verification/large-gantt-result.json','utf8'));
const calendar=JSON.parse(readFileSync('.tmp-verification/large-gantt-calendar.json','utf8'));

async function openResult(page:Page,result=saved) {
  await page.route('**/api/schedules/'+fixture.small_id+'/result',r=>r.fulfill({json:result}));
  // The replay run is not in the isolated database; keep its read-only export
  // validation response consistent with the mocked result. Real validation is
  // covered by validation-export.spec.ts and backend V5 regression tests.
  await page.route('**/api/schedules/'+result.run_id+'/result?validate_export=true',r=>r.fulfill({json:result}));
  await page.route('**/api/calendar?run_id=*',r=>r.fulfill({json:calendar}));
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await page.getByRole('button',{name:'Schedule Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button', {name: 'Open schedule '+fixture.small_id, exact: true}).click();
  await expect(page.getByTestId('operation-count')).toHaveText(`${result.selected.operations.length} / ${result.selected.operations.length} operations`);
}
const geometry=(page:Page)=>page.getByTestId('gantt-row').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().height));

test('Compact summary and large Gantt layout modes preserve data, actions and navigation',async({page})=>{
  await page.setViewportSize({width:1920,height:1080});
  const mutations:string[]=[];
  page.on('request',r=>{if(r.method()==='POST'&&!r.url().endsWith('/auth/login'))mutations.push(r.url());});
  await openResult(page);
  const summary=page.getByTestId('result-summary'), details=page.getByTestId('result-details');
  expect((await summary.boundingBox())!.height).toBeLessThanOrEqual(200);
  await expect(details).not.toHaveAttribute('open','');
  for(const label of ['Makespan','Tardy Jobs','Total Flow Time','Computation Time'])await expect(summary.getByText(label,{exact:true}).first()).toBeVisible();
  await details.locator('summary').click();
  for(const label of ['Selected Method','Total Tardiness','Scheduled Jobs','Status'])await expect(details.getByText(label,{exact:true})).toBeVisible();
  await expect(details).toContainText(saved.run_id);
  await expect(details.getByText(/Calendar used:/)).toBeVisible();
  await details.locator('summary').click();
  for(const label of ['Adjust','JSON','Export Results to Excel','Accept & Save'])await expect(summary.getByRole('button',{name:label,exact:true})).toBeEnabled();
  const download=page.waitForEvent('download');
  await summary.getByRole('button',{name:'JSON',exact:true}).click();
  expect((await download).suggestedFilename()).toBe('schedule-'+saved.run_id+'.json');
  await summary.getByRole('button',{name:'Accept & Save',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Accept this production schedule?'})).toBeVisible();
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  const normal=await geometry(page);
  await page.screenshot({path:test.info().outputPath('layout-normal.png')});
  await page.getByRole('button',{name:'Compact View',exact:true}).click();
  expect((await geometry(page)).every((h,i)=>h<=normal[i])).toBe(true);
  const scroll=page.getByTestId('gantt-scroll');
  await page.getByRole('button',{name:'Fit Schedule',exact:true}).click();
  const identity=()=>page.getByTestId('gantt-bar').evaluateAll(nodes=>nodes.map(n=>['data-event-ids','data-start','data-finish','data-processing'].map(k=>n.getAttribute(k))).sort());
  const compactData=await identity();
  expect(await page.getByTestId('gantt-bar').evaluateAll(ns=>ns.reduce((n,e)=>n+Number(e.getAttribute('data-event-count')),0))).toBe(11290);
  await page.getByRole('button',{name:'Fit Height',exact:true}).click();
  await expect(page.getByRole('button',{name:'Fit Height',exact:true})).toHaveAttribute('aria-pressed','true');
  expect(await identity()).toEqual(compactData);
  await page.getByRole('button',{name:'Expand Gantt',exact:true}).click();
  await expect.poll(async()=>Math.round((await page.getByTestId('gantt-chart').boundingBox())!.height)).toBe(1080);
  expect(await identity()).toEqual(compactData);
  await page.getByRole('button',{name:'Filters',exact:true}).click();
  await expect(page.getByLabel('Search Job',{exact:true})).toBeHidden();
  await page.getByRole('button',{name:'Legend',exact:true}).click();
  await expect(page.getByLabel('Job color legend',{exact:true})).toBeHidden();
  await expect(page.getByTestId('calendar-review')).not.toHaveAttribute('open','');
  await page.screenshot({path:test.info().outputPath('layout-expanded-fit.png')});
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button',{name:'Expand Gantt',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Filters',exact:true}).click();
  await page.getByLabel('Search Job',{exact:true}).fill('J004');
  await expect(page.locator('[data-testid="gantt-bar"][data-job="J004"]').first()).toHaveAttribute('data-highlighted','true');
  await page.getByRole('button',{name:/^Filters/}).click();
  await page.getByRole('button',{name:/^Filters/}).click();
  await expect(page.getByLabel('Search Job',{exact:true})).toHaveValue('J004');
  await page.getByRole('button',{name:'1 Day',exact:true}).click();
  await page.getByRole('button',{name:'Clear / Reset',exact:true}).click();
  const bar=page.getByTestId('gantt-bar').first();await bar.hover();
  await expect(page.getByRole('tooltip')).toContainText((await bar.getAttribute('data-start'))!);
  const labelLeft=(await page.getByTestId('station-label').first().boundingBox())!.x;
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await scroll.evaluate(el=>{el.scrollLeft=300;el.scrollTop=200;});
  await expect.poll(async()=>Math.round((await page.getByTestId('gantt-header').boundingBox())!.y-(await scroll.boundingBox())!.y)).toBe(0);
  expect((await page.getByTestId('station-label').first().boundingBox())!.x).toBe(labelLeft);
  await page.setViewportSize({width:768,height:900});
  await page.getByRole('button',{name:'Expand Gantt',exact:true}).click();
  await expect.poll(()=>scroll.evaluate(el=>el.clientHeight)).toBeGreaterThan(300);
  await page.getByRole('button',{name:'Exit expanded Gantt',exact:true}).click();
  await expect(page.getByTestId('operation-count')).toHaveText('11290 / 11290 operations');
  expect(mutations).toEqual([]);
});

test('Fit Height shows 15 workstation rows on desktop with 20000 unchanged events',async({page})=>{
  await page.setViewportSize({width:1920,height:1080});
  // Synthetic display-only stress fixture: sequential events on 15 workstations.
  const ops=Array.from({length:20000},(_,i)=>{
    const start=new Date(2026,9,1,6,Math.floor(i/15)*2);
    const stamp=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:00`;
    return {...saved.selected.operations[0],eventId:`stress-${i}`,jobId:`J${i%180}`,jobIds:[`J${i%180}`],workstation:`WS_${String(i%15).padStart(2,'0')}`,operation:'PRE',startTime:stamp(start),finishTime:stamp(new Date(+start+60000)),processingMin:1};
  });
  await openResult(page,{...saved,selected:{...saved.selected,operations:ops}});
  await page.getByRole('button',{name:'Fit Height',exact:true}).click();
  await page.getByRole('button',{name:'Expand Gantt',exact:true}).click();
  await page.getByRole('button',{name:'Filters',exact:true}).click();
  await page.getByRole('button',{name:'Legend',exact:true}).click();
  await expect.poll(async()=>Math.max(...await geometry(page))).toBeLessThanOrEqual(48);
  expect(Math.min(...await geometry(page))).toBeGreaterThanOrEqual(40);
  for(const row of await page.getByTestId('gantt-row').all())await expect(row).toBeInViewport({ratio:1});
  await page.getByRole('button',{name:'Fit Schedule',exact:true}).click();
  const displayed=await page.getByTestId('gantt-bar').evaluateAll(ns=>ns.map(n=>[JSON.parse(n.getAttribute('data-event-ids')!)[0],n.getAttribute('data-start'),n.getAttribute('data-finish'),Number(n.getAttribute('data-processing'))]).sort());
  expect(displayed).toEqual(ops.map(o=>[o.eventId,o.startTime,o.finishTime,o.processingMin]).sort());
  await page.screenshot({path:test.info().outputPath('layout-15-stations.png')});
});

test('Compact result keeps Excel download and Run Again / Accept / Apply callbacks',async({page,request})=>{
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  const headers={Authorization:'Bearer '+await page.evaluate(()=>sessionStorage.getItem('al13-token'))};
  const original=await (await request.get('/api/schedules/'+fixture.small_id+'/result',{headers})).json();
  let replay={...original,accepted_at:null,applied_at:null};
  // Keep all writes intercepted: this checks button wiring without rerunning or changing a schedule.
  await page.route('**/api/schedules/'+fixture.small_id+'/result',r=>r.fulfill({json:replay}));
  await page.route('**/api/input/current',async r=>{const response=await r.fetch();await r.fulfill({json:{...await response.json(),id:original.request.dataset_id}});});
  await page.reload();
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await page.getByRole('button',{name:'Schedule Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button', {name: 'Open schedule '+fixture.small_id, exact: true}).click();
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export Results to Excel',exact:true}).click();
  expect((await download).suggestedFilename()).toContain('AL13_Schedule_'+fixture.small_id);
  let reruns=0,accepts=0,applies=0;
  await page.route('**/api/schedules/'+fixture.small_id+'/rerun',r=>{reruns++;return r.fulfill({status:400,json:{message:'Test: optimization deliberately not started'}});});
  await page.getByRole('button',{name:'Run Again',exact:true}).click();
  await expect(page.getByText('Test: optimization deliberately not started',{exact:true})).toBeVisible();
  expect(reruns).toBe(1);
  await page.route('**/api/schedules/'+fixture.small_id+'/accept',r=>{accepts++;replay={...replay,accepted_at:'2026-10-18T10:00:00'};return r.fulfill({json:{status:'accepted'}});});
  await page.route('**/api/schedules/'+fixture.small_id+'/apply',r=>{applies++;replay={...replay,applied_at:'2026-10-18T10:00:00'};return r.fulfill({json:{status:'applied'}});});
  await page.getByRole('button',{name:'Accept & Save',exact:true}).click();
  await page.getByRole('button',{name:'Accept & Save',exact:true}).last().click();
  await expect(page.getByRole('button',{name:'Apply to Production',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Apply to Production',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Execution'})).toBeVisible();
  expect(accepts).toBe(1);expect(applies).toBe(1);
  expect(await (await request.get('/api/schedules/'+fixture.small_id+'/result',{headers})).json()).toEqual(original);
});
