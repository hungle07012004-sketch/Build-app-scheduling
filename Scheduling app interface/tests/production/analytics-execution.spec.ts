import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {algorithmName} from '../../src/algorithms';
import {runLabel} from '../../src/runLabels';

const ids = ['3495dcebb04b4cddb77dd6c8182e6fde', 'b13bc76503bb49f481ee9f2b6a278a8b'];
function fixture() {
  const stations = ['WS_SCALE', 'WS_2B_INSPECTION_WITH_A_LONG_RESOURCE_NAME'].map((id,i) => ({
    id, stage:'Duplicate stage', utilization:i ? 86.2 : 0, status:'Available', upcomingJobs:[], availability:'Planned load', currentJob:null,
  }));
  const history = Array.from({length:7},(_,i) => ({id:ids[i] || `run-${i}-full-identifier`,
    created_at:'2026-10-25T08:30:00+00:00',algo:i ? 'CP-SAT' : 'SS Micro-GA Fast',
    makespan:i===6 ? null : 71042.58399999994+i, runtime:600.1007336, jobs:168, accepted:i===1}));
  return {jobs:[], workstations:stations, history, latest_accepted_schedule:ids[1], solver:'SS Micro-GA Fast',
    makespan_min:71042.58399999994, average_flow_time_min:12345.678, total_tardiness_min:3074911.701,
    tardy_jobs:148, completed_jobs:1234, scheduled_jobs:2000, selection_reason:'Only feasible result.',
    algorithms:[{algorithm:'SS Micro-GA Fast', feasible:true, makespan_min:71042.58399999994},
      {algorithm:'CP-SAT',feasible:false,makespan_min:null}]};
}
async function mockApp(page:Page, summary:any) {
  const calls:{method:string;path:string;body:any}[]=[];
  let execution = [
    {run_id:ids[0],job_id:'J001',status:'Scheduled',updated_at:'2026-10-25T09:00:00Z'},
    {run_id:ids[0],job_id:'J002',status:'Scheduled',updated_at:'2026-10-25T09:00:00Z'},
    {run_id:ids[1],job_id:'J003',status:'Scheduled',updated_at:'2026-10-25T09:00:00Z'},
    {run_id:'orphaned-run-original-id',job_id:'J004',status:'Scheduled',updated_at:'2026-10-25T09:00:00Z'},
  ];
  await page.addInitScript(() => sessionStorage.setItem('al13-token','isolated-ui-test'));
  await page.route('**/api/**', async route => {
    const req=route.request(), path=new URL(req.url()).pathname;
    calls.push({method:req.method(),path,body:req.postDataJSON()});
    if(path==='/api/analytics/overview') return route.fulfill({json:summary});
    if(path==='/api/workstations') return route.fulfill({json:summary.currentWorkstations || summary.workstations});
    if(path==='/api/input/current') return route.fulfill({json:{id:'ui-only',schema_version:'AL13_BENCHMARK_V5',origin_datetime:'2026-10-01 06:00:00',horizon_end_datetime:'2026-11-30 06:00:00'}});
    if(path==='/api/execution') return route.fulfill({json:execution});
    if(path==='/api/execution/jobs/J001' && req.method()==='PATCH') {
      const body=req.postDataJSON();
      execution=execution.map(r=>r.job_id==='J001' && r.run_id===body.run_id ? {...r,status:body.status,updated_at:'2026-10-26T10:00:00Z'} : r).reverse();
      return route.fulfill({json:{}});
    }
    if(path.endsWith('/result')) return route.fulfill({json:{run_id:ids[0],status:'failed',algorithms:[],selected:null,selection_reason:'No feasible result.'}});
    if(path.endsWith('/status')) return route.fulfill({json:{status:'failed'}});
    if(path==='/api/calendar') return route.fulfill({json:[]});
    throw new Error('Unexpected API request: '+req.method()+' '+path);
  });
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  return calls;
}
const section=(page:Page,title:string)=>page.locator('section').filter({has:page.getByRole('heading',{name:title,exact:true})});

test('Analytics plan metrics and algorithm bars match the real persisted API response',async({page})=>{
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  const summary=await page.evaluate(async()=>{
    const response=await fetch('/api/analytics/overview',{headers:{Authorization:'Bearer '+sessionStorage.getItem('al13-token')}});
    if(!response.ok) throw new Error('Overview API failed');
    return response.json();
  });
  expect(summary.latest_accepted_schedule).toBeTruthy();
  const format=(v:number)=>v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Makespan (Plan)'})).toContainText(format(summary.makespan_min)+' min');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Total Tardiness'})).toContainText(format(summary.total_tardiness_min)+' min');
  const performance=section(page,'Algorithm Performance');
  const feasible=summary.algorithms.filter((r:any)=>r.feasible);
  await expect(performance.getByTestId('horizontal-bars').locator(':scope > div')).toHaveCount(feasible.length);
  for(const r of feasible) {
    await expect(performance.getByText(algorithmName(r.algorithm),{exact:true})).toBeVisible();
    await expect(performance.getByText(format(r.makespan_min)+' min',{exact:true})).toBeVisible();
  }
});

test('Analytics preserves values, sources, algorithm identity, export and run navigation',async({page})=>{
  const summary=fixture(), calls=await mockApp(page,summary);
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Makespan (Plan)'})).toContainText('71,042.58 min');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Total Tardiness'})).toContainText('3,074,911.70 min');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Completed Jobs'})).toContainText('1,234 / 2,000');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Avg. Workstation'})).toContainText('43.10%');
  await expect(page.getByText('All applied schedules · reported status')).toBeVisible();
  const utilization=section(page,'Workstation Utilization (%)');
  await expect(utilization.getByText('WS_SCALE',{exact:true})).toBeVisible();
  await expect(utilization.getByText('0.00 %',{exact:true})).toBeVisible();
  await expect(utilization.locator('svg')).toHaveCount(0);
  const performance=section(page,'Algorithm Performance');
  await expect(performance.getByText('GA (SS Micro-GA Fast)',{exact:true})).toBeVisible();
  await expect(performance.getByText('CP-SAT',{exact:true})).toHaveCount(0);
  await expect(performance.getByText('71,042.58 min',{exact:true})).toBeVisible();
  const trend=section(page,'Makespan Trend (min)');
  await expect(trend.locator('svg text')).toHaveText(['R1','R2','R3','R4','R5','R6','R7']);
  await expect(trend.getByRole('button')).toHaveCount(6); // missing data is a gap
  await trend.getByRole('button',{name:new RegExp(ids[0])}).focus();
  await expect(trend.getByRole('tooltip')).toContainText(runLabel(summary.history[0]));
  await expect(trend.getByRole('tooltip')).toContainText('Makespan: 71,042.58 min');
  await expect(trend.getByRole('tooltip')).toHaveAttribute('title',new RegExp(ids[0]));
  const row=page.getByRole('button',{name:'Open schedule '+ids[0],exact:true});
  await expect(row).toHaveAttribute('title',new RegExp(ids[0]));
  await expect(row).toContainText(runLabel(summary.history[0]));
  await expect(row).not.toContainText(ids[0]);
  const downloaded=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export Report',exact:true}).click();
  const file=await downloaded;
  expect(JSON.parse(readFileSync((await file.path())!,'utf8'))).toEqual(summary);
  await row.focus();await page.keyboard.press('Enter');
  await expect(page.getByRole('heading',{name:'Scheduling Result'})).toBeVisible();
  expect(await page.evaluate(()=>sessionStorage.getItem('al13-run'))).toBe(ids[0]);
  expect(calls.filter(r=>r.method!=='GET')).toEqual([]);
});

test('Missing accepted metrics and unknown utilization are never displayed as zero',async({page})=>{
  const summary:any=fixture();
  summary.latest_accepted_schedule=null;summary.completed_jobs=null;summary.scheduled_jobs=null;
  await mockApp(page,summary);
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Makespan (Plan)'})).toContainText('— min');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Completed Jobs'})).toContainText('— / —');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Avg. Workstation'})).toContainText('—%');
  await expect(section(page,'Workstation Utilization (%)').getByText('— %',{exact:true})).toHaveCount(2);
  await expect(section(page,'Algorithm Performance').getByText('No schedule data yet.')).toBeVisible();
  await expect(section(page,'Makespan Trend (min)').getByRole('button')).toHaveCount(6);
});

test('Partial metrics remain unknown while a genuine zero stays zero',async({page})=>{
  const summary:any=fixture();
  summary.makespan_min=null;summary.workstations[1].utilization=null;
  summary.algorithms[0].makespan_min=null;
  await mockApp(page,summary);await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Makespan (Plan)'})).toContainText('— min');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Avg. Workstation'})).toContainText('—%');
  await expect(section(page,'Workstation Utilization (%)').getByText('0.00 %',{exact:true})).toBeVisible();
  await expect(section(page,'Algorithm Performance').getByText('— min',{exact:true})).toBeVisible();
});

test('Execution schedule labels are stable across refresh, reorder, reload and status changes',async({page})=>{
  const summary=fixture(),calls=await mockApp(page,summary);
  await page.getByRole('button',{name:'Production Execution',exact:true}).click();
  const label=(id:string)=>page.getByTestId('execution-schedule-label').and(page.locator(`[title="${id}"]`));
  const first=await label(ids[0]).first().innerText();
  expect(first).toBe('Schedule · 25 Oct 2026, 15:30 · 3495DCEB');
  expect(await label(ids[0]).allTextContents()).toEqual([first,first]);
  expect(await label(ids[1]).innerText()).not.toBe(first);
  await expect(label('orphaned-run-original-id')).toHaveText('Schedule · ORPHANED');
  await page.getByLabel('Status J001',{exact:true}).selectOption('Completed');
  await expect(page.getByLabel('Status J001',{exact:true})).toBeEnabled();
  await expect(page.getByLabel('Status J001',{exact:true})).toHaveValue('Completed');
  expect(await label(ids[0]).allTextContents()).toEqual([first,first]);
  expect(calls.filter(r=>r.method==='PATCH')).toEqual([{method:'PATCH',path:'/api/execution/jobs/J001',body:{run_id:ids[0],status:'Completed'}}]);
  summary.history = [...summary.history.slice(0,6).reverse(),summary.history[6]];
  await page.reload();await page.getByRole('button',{name:'Production Execution',exact:true}).click();
  await expect(label(ids[0]).first()).toHaveText(first);
  await page.screenshot({path:test.info().outputPath('execution-desktop.png'),fullPage:true});
});

test('Analytics and execution stay inside their available width on desktop and narrow layouts',async({page})=>{
  await mockApp(page,fixture());
  for(const width of [1440,768]) {
    await page.setViewportSize({width,height:1000});
    if(width<1024)await page.getByRole('button',{name:'Open navigation',exact:true}).click();
    await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
    expect(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();
    for(const card of await page.locator('section').all()) expect(await card.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();
    await page.screenshot({path:test.info().outputPath(`analytics-${width}.png`),fullPage:true});
    if(width<1024)await page.getByRole('button',{name:'Open navigation',exact:true}).click();
    await page.getByRole('button',{name:'Production Execution',exact:true}).click();
    expect(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();
    await expect(page.getByLabel('Status J001',{exact:true})).toBeEnabled();
  }
});

test('Accepted resources, completion timestamps and incompatible histories remain distinct',async({page})=>{
  const summary:any=fixture();
  summary.current_dataset_id='new-dataset'; summary.accepted_dataset_id='old-dataset';
  summary.currentWorkstations=[{...summary.workstations[0],id:'CURRENT_ONLY',utilization:99}];
  summary.history=summary.history.map((r:any,i:number)=>({...r,status:i===1?'failed':'completed',
    makespan:i===1?null:r.makespan,finished_at:'2026-10-26T10:15:00Z',dataset_id:i?'old':'new',
    comparison_key:i<2?'different-'+i:'same',calendar:{start:'2026-10-01',end:'2026-11-30',signature:'calendar-'+i},
    search_settings:{micro_time_limit:i?600:900},applied:false}));
  await mockApp(page,summary);
  await page.getByText('Upcoming Jobs & Recent Activity',{exact:true}).click();
  const activity=page.getByTestId('recent-activity');
  await expect(activity).toContainText('Finished:');
  await expect(activity).toContainText('failed');
  await expect(activity).not.toContainText('Invalid Date');
  const expected=await page.evaluate(()=>new Date('2026-10-26T10:15:00Z').toLocaleString());
  await expect(activity).toContainText(expected);
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  const util=section(page,'Workstation Utilization (%)');
  await expect(util).toContainText('WS_SCALE');await expect(util).not.toContainText('CURRENT_ONLY');
  await expect(page.getByTestId('analytics-kpi').filter({hasText:'Avg. Workstation'})).toContainText('43.10%');
  const trend=section(page,'Makespan Trend (min)');
  const path=await trend.locator('svg > path').getAttribute('d');
  expect((path!.match(/M/g)||[]).length).toBe(2);
  await expect(page.getByRole('button',{name:'Open schedule '+ids[0],exact:true})).toContainText('900');
  await expect(page.getByRole('button',{name:'Open schedule '+ids[0],exact:true})).toContainText('[calendar]');
  await expect(section(page,'Algorithm Performance')).toContainText('Not a historical algorithm ranking');
});
