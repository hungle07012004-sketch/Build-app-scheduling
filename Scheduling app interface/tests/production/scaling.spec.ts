import {test,expect} from '@playwright/test';
import {existsSync,readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
const exportPath=process.env.GANTT_RESULT_JSON || 'tests/fixtures/current-milp-result.json';
const saved=existsSync(exportPath)?JSON.parse(readFileSync(exportPath,'utf8')):null;
test('Current exported 100-operation MILP fits actual timestamps instead of July input horizon',async({page})=>{
  test.skip(!saved,'Provide the existing MILP export via GANTT_RESULT_JSON to verify its actual time range.');
  const solverRequests:string[]=[];
  page.on('request',r=>{if(r.method()==='POST'&&/\/schedules\/.*(run|rerun)/.test(r.url()))solverRequests.push(r.url());});
  // Replay the existing exported response unchanged; no optimization or DB writes.
  await page.route('**/api/schedules/'+fixture.small_id+'/result',route=>route.fulfill({json:saved}));
  await page.goto('/');
  await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Analytics',exact:true}).click();
  await page.getByText(fixture.small_id,{exact:true}).click();
  await page.getByRole('button',{name:'Hourly view',exact:true}).click();
  const bars=page.getByTestId('gantt-bar');
  await expect(bars).toHaveCount(100);
  await expect(page.getByTestId('operation-count')).toHaveText('100 / 100 operations');
  const ops=saved.selected.operations;
  const time=(s:string)=>new Date(s.replace(' ','T')).getTime();
  const min=Math.min(...ops.map((o:any)=>time(o.startTime)));
  const max=Math.max(...ops.map((o:any)=>time(o.finishTime)));
  const pad=Math.max(900000,(max-min)*0.03);
  const scroll=page.getByTestId('gantt-scroll');
  await expect(scroll).toHaveAttribute('data-domain-start',String(min-pad));
  await expect(scroll).toHaveAttribute('data-domain-end',String(max+pad));
  await expect(page.getByTestId('zoom-level')).toHaveText('100%');
  const geometry=await bars.evaluateAll(nodes=>nodes.map(n=>({
    job:n.getAttribute('data-job'),op:n.getAttribute('data-operation'),
    start:n.getAttribute('data-start'),finish:n.getAttribute('data-finish'),
    left:parseFloat((n as HTMLElement).style.left),width:parseFloat((n as HTMLElement).style.width),
    timeline:n.parentElement!.clientWidth,label:n.textContent,
    clipped:n.querySelector('span:last-child')!.scrollWidth>n.clientWidth,
  })));
  for(const bar of geometry){
    const op=ops.find((o:any)=>o.jobId===bar.job&&o.operation===bar.op);
    expect(bar.start).toBe(op.startTime);expect(bar.finish).toBe(op.finishTime);
    expect(bar.left/bar.timeline).toBeCloseTo((time(op.startTime)-(min-pad))/(max-min+pad*2),4);
    expect(bar.width/bar.timeline).toBeCloseTo((time(op.finishTime)-time(op.startTime))/(max-min+pad*2),4);
    expect(bar.width).toBeGreaterThan(1);
    expect(bar.left+bar.width).toBeLessThanOrEqual(bar.timeline+1);
    if(bar.label)expect(bar.clipped).toBeFalsy();
  }
  expect(geometry.some(b=>!!b.label)).toBeTruthy();
  const ticks=await page.getByTestId('axis-tick').allTextContents();
  expect(ticks.length).toBeGreaterThan(10);
  expect(ticks.every(t=>/^\d{2}:00$/.test(t))).toBeTruthy();
  await page.getByLabel('Search Job',{exact:true}).fill('J001');
  await expect(page.locator('[data-testid="gantt-bar"][data-highlighted="true"]')).toHaveCount(10);
  const jobBars=page.locator('[data-testid="gantt-bar"][data-job="J001"]');
  const colors=await jobBars.evaluateAll(nodes=>nodes.map(n=>(n as HTMLElement).style.backgroundColor));
  expect(new Set(colors).size).toBe(1);
  const op=ops.find((o:any)=>o.jobId==='J001');
  await expect(jobBars.first()).toHaveAttribute('aria-label',new RegExp(op.startTime));
  await expect(jobBars.first()).toHaveAttribute('aria-label',new RegExp(op.finishTime));
  await page.getByRole('button',{name:'Clear / Reset'}).click();
  await page.screenshot({path:'.tmp-verification/current-milp-fit.png',fullPage:true});
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await scroll.evaluate(el=>{el.scrollLeft=250;});
  await page.getByRole('button',{name:'Fit Schedule',exact:true}).click();
  await expect(page.getByTestId('zoom-level')).toHaveText('100%');
  expect(await scroll.evaluate(el=>el.scrollLeft)).toBe(0);
  await expect(scroll).toHaveAttribute('data-domain-end',String(Math.max(max+pad,time(saved.request?.horizon_end||'')||max)));
  await page.getByRole('button',{name:'Schedule Table',exact:true}).click();
  const rows=await page.locator('tbody tr').evaluateAll(nodes=>nodes.map(n=>Array.from(n.querySelectorAll('td')).slice(0,5).map(c=>c.textContent)));
  expect(rows).toEqual(ops.map((o:any)=>[o.jobId,o.operation,o.workstation,o.startTime,o.finishTime]));
  expect(solverRequests).toEqual([]);
});
