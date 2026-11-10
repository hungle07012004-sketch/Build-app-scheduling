import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));

for(const action of ['Use latest calendar','Schedule with latest calendar','Adjust']) test('Saved run switches to current calendar via '+action,async({page})=>{
  const solverCalls:string[]=[];
  page.on('request',r=>{if(r.method()==='POST' && /schedules.*(run|rerun)/.test(r.url()))solverCalls.push(r.url());});
  await page.route('**/api/input/current',async route=>{
    const response=await route.fetch();const data=await response.json();
    await route.fulfill({json:{...data,id:'new-calendar',origin_datetime:'2026-10-01 06:00:00',horizon_end_datetime:'2026-11-30 06:00:00'}});
  });
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await page.getByRole('button',{name:'Schedule Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button', {name: 'Open schedule '+fixture.small_id, exact: true}).click();
  await page.getByTestId('result-details').locator('summary').click();
  await expect(page.getByText(/Calendar used:/)).toBeVisible();
  await page.getByRole('button',{name:action,exact:true}).click();
  await expect(page.getByLabel('Scheduling Start')).toHaveValue('2026-10-01T06:00');
  await expect(page.getByLabel('Scheduling End')).toHaveValue('2026-11-30T06:00');
  expect(solverCalls).toEqual([]);
});
