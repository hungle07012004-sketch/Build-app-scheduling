import {test,expect} from '@playwright/test';

for(const v5 of [true,false]) test('Scheduling Method guidance and availability: '+(v5?'V5':'legacy'),async({page})=>{
  const jobs=Array.from({length:40},(_,i)=>({id:'J'+String(i+1).padStart(3,'0'),product:'Test product',batchQty:10,priority:'Medium',status:'Unscheduled',releaseTime:'2026-10-01 06:00:00',materialReady:'2026-10-01 06:00:00',dueDate:'2026-10-02 06:00:00'}));
  await page.route('**/api/input/current',route=>route.fulfill({json:{id:'method-preview',filename:'UI fixture',jobs:40,workstations:1,schema_version:v5?'AL13_BENCHMARK_V5':'legacy',origin_datetime:'2026-10-01 06:00:00',horizon_end_datetime:'2026-10-02 06:00:00'}}));
  await page.route('**/api/analytics/overview',route=>route.fulfill({json:{jobs,history:[],algorithms:[],workstations:[],total_jobs:40}}));
  await page.route('**/api/workstations',route=>route.fulfill({json:[]}));
  await page.route('**/api/schedules/preview',route=>{
    const body=route.request().postDataJSON();
    const algorithms=body.algorithms.length?body.algorithms:[v5?(body.job_ids.length<=20?'CP-SAT':'SS Micro-GA Fast'):(body.job_ids.length<40?'Improved GA':'MLPSO')];
    return route.fulfill({json:{valid:true,algorithms,routing_reason:'Deterministic preview fixture.',availability:{}}});
  });
  await page.goto('/');
  await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Scheduling Studio',exact:true}).click();
  const section=page.getByRole('region',{name:'Scheduling Method',exact:true});
  await expect(page.getByRole('radio',{name:'Automatic',exact:true})).toBeChecked();
  await expect(section.getByText('Recommended',{exact:true})).toBeVisible();
  for(let i=0;i<4;i++)await page.locator('tbody tr').nth(i).click();
  // Guidance banners were intentionally removed; preview still calls the API.
  await expect(section.getByRole('status')).toHaveCount(0);
  await page.getByRole('radio',{name:v5?'CP-SAT':'GA',exact:true}).check();
  await expect(page.getByRole('radio',{name:v5?'CP-SAT':'GA',exact:true})).toBeChecked();
  await page.screenshot({path:test.info().outputPath('method-small-'+(v5?'v5':'legacy')+'.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('checkbox',{name:'Select all filtered jobs'}).check();
  await expect(page.getByRole('radio',{name:v5?'CP-SAT':'GA',exact:true})).toBeChecked();
  await expect(section.getByRole('status')).toHaveCount(0);
  await expect(section.getByRole('radio')).toHaveCount(4);
  await expect(section.getByText('Parallel Optimization',{exact:true})).toHaveCount(0);
  await expect(section.getByText('Unavailable for V5',{exact:true})).toHaveCount(0);
  await expect(section.getByText('Optimization Time',{exact:true})).toHaveCount(0);
  if(v5){
    await page.getByRole('radio',{name:'GA',exact:true}).check();
    await expect(page.getByRole('radio',{name:'GA',exact:true})).toBeChecked();
  }
  await expect(page.getByRole('radio',{name:v5?'MLPSO':'CP-SAT',exact:true})).toBeDisabled();
  await page.getByRole('radio',{name:'Automatic',exact:true}).check();
  await expect(page.getByRole('radio',{name:'Automatic',exact:true})).toBeChecked();
  await expect(section.getByRole('status')).toHaveCount(0);
  await page.screenshot({path:test.info().outputPath('method-large-'+(v5?'v5':'legacy')+'.png'),fullPage:true,animations:'disabled'});
});
