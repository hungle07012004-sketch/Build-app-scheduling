import {test,expect} from '@playwright/test';

test('All computation presets reach real preview API and outgoing run payload',async({page})=>{
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Jobs',exact:true}).click();
  await page.getByLabel('Upload Input File').setInputFiles('tests/fixtures/v5-integration.json');
  await expect(page.locator('tbody tr')).toHaveCount(4);
  await page.getByRole('button',{name:'Scheduling',exact:true}).click();
  await page.locator('thead button').click();
  await page.getByRole('button',{name:'Validate Data',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Data Validation Passed'})).toBeVisible();
  await expect(page.getByRole('button',{name:'120 sec',exact:true})).toHaveAttribute('aria-pressed','true');
  const payloads:any[]=[];
  // Capture execution request without starting a long solver on the fixture.
  await page.route('**/api/schedules/run',r=>{payloads.push(r.request().postDataJSON());return r.fulfill({status:409,json:{message:'Execution intercepted by verification'}});});
  for(const method of ['Automatic','CP-SAT','GA']) {
    await page.getByRole('radio',{name:method,exact:true}).click();
    await expect(page.getByRole('radio',{name:method,exact:true})).toBeChecked();
    for(const seconds of [60,120,300,600,900]) {
      const response=page.waitForResponse(r=>r.url().endsWith('/api/schedules/preview') && r.request().postDataJSON().settings.micro_time_limit===seconds);
      await page.getByRole('button',{name:seconds<600?`${seconds} sec`:`${seconds} sec (${seconds/60} min)`,exact:true}).click();
      const preview=await response;expect(preview.status()).toBe(200);
      const sent=preview.request().postDataJSON();
      expect(sent.settings.exact_time_limit).toBe(seconds);
      await expect(page.getByRole('button',{name:'Run Optimization',exact:true})).toBeEnabled();
      await page.getByRole('button',{name:'Run Optimization',exact:true}).click();
      await expect(page.getByText('Execution intercepted by verification',{exact:true})).toBeVisible();
      const payload=payloads.at(-1);
      expect(payload.settings.micro_time_limit).toBe(seconds);
      expect(payload.settings.exact_time_limit).toBe(seconds);
      expect(payload.algorithms).toEqual(method==='Automatic'?[]:[method==='GA'?'SS Micro-GA Fast':'CP-SAT']);
      expect(payload.horizon_end).toBe(sent.horizon_end);
    }
  }
});
