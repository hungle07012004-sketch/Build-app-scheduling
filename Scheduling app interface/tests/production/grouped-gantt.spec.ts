import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
const saved=JSON.parse(readFileSync('tests/fixtures/current-milp-result.json','utf8'));

for(const size of [10,30,50,100])test(`Grouped Gantt preserves ${size} jobs, gaps, lanes, timestamps and raw table`,async({page})=>{
  const origin=new Date(2026,9,1,6).getTime();
  const date=(minute:number)=>new Date(origin+minute*60000).toLocaleString('sv-SE').replace('T',' ');
  const ops:any[]=[];
  for(let i=0;i<size;i++){
    const job='J'+String(i+1).padStart(3,'0');const start=i*12;
    for(const [idx,[op,station,offset,duration]] of ([
      ['OIL_CYCLE','WS_OIL',0,1],['OIL_CYCLE','WS_OIL',1,1],['OIL_CYCLE','WS_OIL',2,1],
      ['OIL_CYCLE','WS_OIL',4,1],['2C_BATCH','WS_2C_BATCH',7,1],['2C_BATCH','WS_2C_BATCH',8,1],
      ['LASER','WS_LASER',5,1],['LASER','WS_LASER',5.5,1],
    ] as [string,string,number,number][]).entries()){
      ops.push({eventId:job+'-'+idx,eventType:op,jobId:job,jobIds:[job],product:'Fixture',operation:op,workstation:station,
        startMin:start+offset,endMin:start+offset+duration,startTime:date(start+offset),finishTime:date(start+offset+duration),processingMin:duration,status:'Scheduled'});
    }
  }
  const replay={...saved,selected:{...saved.selected,operations:ops}};
  const solverCalls:string[]=[];
  page.on('request',r=>{if(r.method()==='POST'&&/schedules.*(run|rerun)/.test(r.url()))solverCalls.push(r.url());});
  await page.route('**/api/schedules/'+fixture.small_id+'/result',route=>route.fulfill({json:replay}));
  await page.route('**/api/calendar?run_id=*',route=>route.fulfill({json:['WS_OIL','WS_2C_BATCH','WS_LASER'].map(station=>({
    station_id:station,window_id:station+'_saved',calendar_date:'2026-10-01',shift_id:'Saved shift S1',
    start_datetime:date(0),end_datetime:date(2000),
  }))}));
  await page.goto('/');await page.locator('input[type=password]').fill('planner-demo');
  await page.getByRole('button',{name:'Sign In',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Production Dashboard'})).toBeVisible();
  await page.getByRole('button',{name:'Analytics & Reports',exact:true}).click();
  await page.getByRole('button',{name:'Schedule Results',exact:true}).click();
  await page.getByLabel('Search runs').fill(fixture.small_id);
  await page.getByRole('button', {name: 'Open schedule '+fixture.small_id, exact: true}).click();
  await page.getByRole('button',{name:'Hourly view',exact:true}).click();
  const bars=page.getByTestId('gantt-bar');
  await expect(bars).toHaveCount(size*5);
  await expect(page.getByTestId('operation-count')).toHaveText(`${size*8} / ${size*8} operations`);
  const geometry=await bars.evaluateAll(nodes=>nodes.map(n=>({height:n.getBoundingClientRect().height,radius:getComputedStyle(n).borderRadius,
    ids:JSON.parse(n.getAttribute('data-event-ids')!),start:n.getAttribute('data-start'),finish:n.getAttribute('data-finish'),
    width:parseFloat((n as HTMLElement).style.width),lane:n.getAttribute('data-lane'),op:n.getAttribute('data-operation')})));
  expect(new Set(geometry.map(g=>g.height))).toEqual(new Set([36]));
  expect(new Set(geometry.map(g=>g.radius))).toEqual(new Set(['6px']));
  expect(geometry.flatMap(g=>g.ids).sort()).toEqual(ops.map(o=>o.eventId).sort());
  const reference=geometry[0];
  const referenceMembers=ops.filter(o=>reference.ids.includes(o.eventId));
  const pixelsPerMinute=reference.width/referenceMembers.reduce((sum,o)=>sum+o.processingMin,0);
  for(const g of geometry){
    const members=ops.filter(o=>g.ids.includes(o.eventId)).sort((a,b)=>a.startMin-b.startMin);
    expect(g.start).toBe(members[0].startTime);expect(g.finish).toBe(members.at(-1).finishTime);
    for(let i=1;i<members.length;i++)expect(members[i].startMin).toBe(members[i-1].endMin);
    expect(g.width/(members.at(-1).endMin-members[0].startMin)).toBeCloseTo(pixelsPerMinute,3);
  }
  expect(new Set(geometry.filter(g=>g.op==='LASER').map(g=>g.lane))).toEqual(new Set(['0','1']));
  await page.getByLabel('Job filter',{exact:true}).selectOption('J001');
  await expect(bars).toHaveCount(5);
  const oil=page.locator('[data-testid="gantt-bar"][data-operation="OIL_CYCLE"]').first();
  await oil.hover();await expect(page.getByRole('tooltip')).toContainText('Saved events');
  await expect(page.getByRole('tooltip')).toContainText('Saved shift S1');
  await expect(oil).not.toHaveAttribute('title');
  const box=await oil.boundingBox(), tip=await page.getByRole('tooltip').boundingBox();
  expect(box && tip).toBeTruthy();
  expect(tip!.x>=0 && tip!.y>=0 && tip!.x+tip!.width<=1440 && tip!.y+tip!.height<=1000).toBeTruthy();
  expect(tip!.x>=box!.x+box!.width || tip!.x+tip!.width<=box!.x || tip!.y>=box!.y+box!.height || tip!.y+tip!.height<=box!.y).toBeTruthy();
  await page.screenshot({path:test.info().outputPath(`tooltip-fixed-${size}.png`),fullPage:true});
  await oil.click();await expect(page.getByText('Saved events in this bar (3)',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Close job details',exact:true}).click();
  await page.getByRole('button',{name:'Clear / Reset',exact:true}).click();
  await page.getByRole('button',{name:'Fit Schedule',exact:true}).click();
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  await page.getByRole('button',{name:'Fit Schedule',exact:true}).click();
  await expect(page.getByTestId('zoom-level')).toHaveText('100%');
  await page.screenshot({path:test.info().outputPath(`grouped-gantt-${size}.png`),fullPage:true});
  await page.getByRole('button',{name:'Schedule Table',exact:true}).click();
  await expect(page.locator('tbody tr')).toHaveCount(Math.min(50,ops.length));
  expect(solverCalls).toEqual([]);
});
