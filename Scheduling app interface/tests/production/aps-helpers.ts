import {expect,type Page} from '@playwright/test';
// Read every page, not just the first 50 visible records.
export async function scheduleRows(page:Page) {
  const section=page.getByTestId('operation-table');
  const result:(string|null)[][]=[];
  while(true) {
    result.push(...await section.locator('tbody tr').evaluateAll(rows=>rows.map(row=>{
      const cells=Array.from(row.querySelectorAll('td'));
      return [0,3,4,6,7].map(i=>cells[i]?.textContent??null);
    })));
    const next=section.getByRole('button',{name:'Next',exact:true});
    if(await next.isDisabled())break;
    const before=await section.locator('tbody tr').first().innerText();
    await next.click();
    await expect(section.locator('tbody tr').first()).not.toHaveText(before);
  }
  return result;
}
