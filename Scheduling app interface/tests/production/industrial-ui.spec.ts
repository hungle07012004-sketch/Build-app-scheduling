import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
const saved=JSON.parse(readFileSync('.tmp-verification/large-gantt-result.json','utf8'));
const calendar=JSON.parse(readFileSync('.tmp-verification/large-gantt-calendar.json','utf8'));
async function login(page:any){await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');await page.getByRole('button',{name:'Sign In',exact:true}).click();await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();}

test('Industrial dashboard/studio: real navigation, photographs, responsive drawer, keyboard and reduced motion',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:1648,height:928});await login(page);
  await expect(page.locator('.aps-metric')).toHaveCount(4);
  await expect(page.locator('.cinematic-hero-photo')).toHaveCSS('background-image',/factory-hero.*webp/);
  await page.screenshot({path:test.info().outputPath('dashboard-1648.png')});
  await page.setViewportSize({width:1440,height:900});
  await page.screenshot({path:test.info().outputPath('dashboard-1440.png')});
  await page.getByRole('button',{name:'View Recent Results',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Schedule Results',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Dashboard',exact:true}).click();
  await page.getByRole('button',{name:'Create Schedule',exact:true}).click();
  await page.setViewportSize({width:1648,height:928});
  await page.getByRole('checkbox',{name:'Select J001',exact:true}).check();
  const start=await page.getByLabel('Scheduling Start',{exact:true}).inputValue();
  const end=await page.getByLabel('Scheduling End',{exact:true}).inputValue();
  await expect(page.getByRole('radio',{name:'Automatic',exact:true})).toBeChecked();
  await page.screenshot({path:test.info().outputPath('studio-1648.png')});
  for(const width of [980,390]){
    await page.setViewportSize({width,height:900});
    expect(await page.locator('main').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();
    await page.getByRole('button',{name:'Open navigation',exact:true}).click();
    await expect(page.getByRole('button',{name:'Close menu',exact:true})).toBeFocused();
    await page.keyboard.press('Shift+Tab');await expect(page.getByRole('button',{name:'Sign Out',exact:true})).toBeFocused();
    await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Open navigation',exact:true})).toBeFocused();
    await expect.poll(async()=>{const box=await page.locator('.aps-sidebar').boundingBox();return Math.ceil(box!.x+box!.width);}).toBeLessThanOrEqual(0);
    await expect(page.getByRole('checkbox',{name:'Select J001',exact:true})).toBeChecked();
    await expect(page.getByLabel('Scheduling Start',{exact:true})).toHaveValue(start);
    await expect(page.getByLabel('Scheduling End',{exact:true})).toHaveValue(end);
    await page.screenshot({path:test.info().outputPath('studio-'+width+'.png')});
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.mouse.move(100,100);
  await expect(page.locator('.industrial-backdrop')).toHaveCSS('transform','none');
  expect(errors).toEqual([]);
});

test('Industrial result: large schedule shading and event coordinates survive themed layout and zoom',async({page})=>{
  const before=JSON.stringify(saved);const writes:string[]=[];
  page.on('request',request=>{if(request.method()==='POST'&&/\/schedules\//.test(request.url()))writes.push(request.url());});
  await page.route('**/api/schedules/'+fixture.small_id+'/result',route=>route.fulfill({json:saved}));
  await page.route('**/api/calendar?run_id=*',route=>route.fulfill({json:calendar}));
  await page.setViewportSize({width:1648,height:928});await login(page);
  await page.getByRole('button',{name:'View Recent Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button',{name:'Open schedule '+fixture.small_id,exact:true}).click();
  await expect(page.getByTestId('operation-count')).toHaveText('11290 / 11290 operations');
  await page.screenshot({path:test.info().outputPath('results-1648.png')});
  const first=page.getByTestId('gantt-bar').first();
  const identity=await first.getAttribute('data-event-ids');
  await first.hover();await expect(page.getByRole('tooltip')).toBeVisible();
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await expect(page.getByTestId('zoom-level')).toHaveText('200%');
  await page.getByRole('button',{name:'Fit Schedule',exact:true}).click();
  const ids=await page.getByTestId('gantt-bar').evaluateAll(nodes=>nodes.flatMap(node=>JSON.parse(node.getAttribute('data-event-ids')||'[]')));
  for(const id of JSON.parse(identity!))expect(ids).toContain(id);
  expect(JSON.stringify(saved)).toBe(before);expect(writes).toEqual([]);
});
