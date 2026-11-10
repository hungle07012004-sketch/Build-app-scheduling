import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

const fixture = JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json', 'utf8'));

for (const rejected of [false, true]) test(`JSON export validates saved result (rejected=${rejected})`, async ({page, request}) => {
  await page.goto('/');
  await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button', {name: 'Sign In', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Production Dashboard'})).toBeVisible();
  await page.getByRole('button', {name: 'Analytics & Reports', exact: true}).click();
  await page.getByRole('button',{name:'Schedule Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button', {name: 'Open schedule '+fixture.small_id, exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Scheduling Result'})).toBeVisible();
  const token = await page.evaluate(() => sessionStorage.getItem('al13-token'));
  const url = '/api/schedules/' + fixture.small_id + '/result';
  const before = await (await request.get(url, {headers: {Authorization: 'Bearer ' + token}})).json();
  let checked = false;
  await page.route('**' + url + '?validate_export=true', async route => {
    checked = true;
    if (rejected) await route.fulfill({status: 409, json: {message: 'Saved schedule failed validation; it has not been changed.', error_code: 'INVALID_SCHEDULE'}});
    else await route.continue();
  });
  const downloads: string[] = [];
  page.on('download', d => downloads.push(d.suggestedFilename()));
  const download = rejected ? null : page.waitForEvent('download');
  await page.getByRole('button', {name: 'JSON', exact: true}).click();
  if (rejected) {
    await expect(page.getByText('Saved schedule failed validation; it has not been changed.', {exact: true})).toBeVisible();
    expect(downloads).toEqual([]);
  } else {
    const file = await download!;
    expect(file.suggestedFilename()).toBe('schedule-' + fixture.small_id + '.json');
    expect(JSON.parse(readFileSync((await file.path())!, 'utf8'))).toEqual(before);
  }
  expect(checked).toBeTruthy();
  await expect(page.getByTestId('operation-count')).toHaveText('100 / 100 operations');
  expect(await (await request.get(url, {headers: {Authorization: 'Bearer ' + token}})).json()).toEqual(before);
});
