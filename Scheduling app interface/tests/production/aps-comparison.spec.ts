import {test,expect} from '@playwright/test';
test('Comparison displays saved bounds/gap and never infers optimality from status text',async({page})=>{
  await page.addInitScript(()=>{sessionStorage.setItem('al13-token','ui-test');sessionStorage.setItem('al13-run','comparison-ui');});
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/analytics/overview')return route.fulfill({json:{jobs:[],workstations:[],history:[],algorithms:[]}});
    if(path==='/api/input/current')return route.fulfill({json:null});
    if(path==='/api/workstations'||path==='/api/calendar')return route.fulfill({json:[]});
    if(path.endsWith('/status'))return route.fulfill({json:{status:'completed'}});
    if(path.endsWith('/result'))return route.fulfill({json:{run_id:'comparison-ui',status:'completed',selected:null,selection_reason:'UI metadata fixture',algorithms:[
      {algorithm:'CP-SAT',status:'OPTIMAL',proven_optimal:false,feasible:true,makespan_min:100,runtime_sec:5,operations:[],metadata:{best_bound_min:80,relative_gap:0.2}},
      {algorithm:'SS Micro-GA Fast',status:'Feasible',proven_optimal:false,feasible:true,makespan_min:120,runtime_sec:5,operations:[]},
    ]}});
    return route.fulfill({status:404,json:{message:'Unexpected fixture request'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Scheduling Studio',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Scheduling Result'})).toBeVisible();
  const exact=page.locator('tbody tr').filter({hasText:'CP-SAT'});
  await expect(exact).toContainText('Feasible');await expect(exact).not.toContainText('OPTIMAL');
  await expect(exact.locator('td').nth(5)).toHaveText('80');
  await expect(exact.locator('td').nth(6)).toHaveText('20');
  await expect(page.locator('tbody tr').filter({hasText:'Micro-GA'}).locator('td').nth(6)).toHaveText('—');
});
