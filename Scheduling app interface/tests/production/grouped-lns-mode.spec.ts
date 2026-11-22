import {test,expect} from '@playwright/test';
import {createServer,type Server} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';

// Serve the built UI inside the test worker: no live backend/database or Vite.
let server:Server;
test.beforeAll(async()=>{
  const root=resolve('dist');
  server=createServer(async(req,res)=>{
    const path=resolve(root,'.'+(req.url==='/'?'/index.html':req.url!.split('?')[0]));
    if(!path.startsWith(root+sep)){res.writeHead(403);res.end();return;}
    try{
      const body=await readFile(path);
      res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.webp':'image/webp'} as Record<string,string>)[extname(path)]||'application/octet-stream');
      res.end(body);
    }catch{res.writeHead(404);res.end();}
  });
  await new Promise<void>((ok,fail)=>server.once('error',fail).listen(8765,'127.0.0.1',ok));
});
test.afterAll(async()=>{server?.closeAllConnections();await new Promise<void>(ok=>server?.close(()=>ok()));});

const mode='MIN_TARDY_GROUPED_LNS_V5';
test('Opt-in grouped LNS configuration, request and original-due metrics',async({page})=>{
  const jobs=Array.from({length:8},(_,i)=>({id:'J'+String(i+1).padStart(3,'0'),product:'Test',batchQty:10,priority:'Medium',status:'Unscheduled',releaseTime:'2026-10-01 06:00:00',materialReady:'2026-10-01 06:00:00',dueDate:'2026-10-02 06:00:00'}));
  let submitted:any;
  const selected={algorithm:mode,status:'FEASIBLE',feasible:true,proven_optimal:false,makespan_min:2000,total_tardiness_min:100,tardy_jobs:2,total_flow_time_min:5000,runtime_sec:2,operations:[],metadata:{total_jobs:8,tardy_percent:25,otd_percent:75,N_pre_overdue:1,due_group_days:7,unique_full_decoder_calls:128}};
  await page.addInitScript(()=>{sessionStorage.setItem('al13-token','fixture');localStorage.removeItem('al13-settings');sessionStorage.removeItem('al13-run');});
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let json:any=[];
    if(path==='/api/input/current')json={id:'fixture',filename:'V5 UI fixture',jobs:8,workstations:1,schema_version:'AL13_BENCHMARK_V5',origin_datetime:'2026-10-01 06:00:00',horizon_end_datetime:'2026-11-01 06:00:00'};
    else if(path==='/api/analytics/overview')json={jobs,history:[],algorithms:[],workstations:[],total_jobs:8};
    else if(path.endsWith('/preview')||path.endsWith('/validate')){
      const body=route.request().postDataJSON();json={valid:true,size:8,classification:'small',schema_version:'AL13_BENCHMARK_V5',issues:[],algorithms:body.algorithms.length?body.algorithms:['CP-SAT'],availability:{},routing_reason:'UI fixture'};
    }else if(path==='/api/schedules/run'){submitted=route.request().postDataJSON();json={run_id:'grouped-fixture',status:'queued'};}
    else if(path.endsWith('/status'))json={status:'completed',algorithms:[]};
    else if(path.endsWith('/result'))json={id:'grouped-fixture',run_id:'grouped-fixture',status:'completed',request:submitted,selected,algorithms:[selected],selection_reason:'Lexicographic N,T,C',routing_reason:'Manual opt-in'};
    else if(path.includes('/jobs'))json=jobs;
    await route.fulfill({json});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Scheduling Studio',exact:true}).click();
  await expect(page.getByRole('radio',{name:'Automatic',exact:true})).toBeChecked();
  await expect(page.getByRole('radio',{name:'GA',exact:true})).toBeEnabled();
  await page.getByRole('checkbox',{name:'Select all filtered jobs'}).check();
  await page.getByRole('radio',{name:'Min Tardy — Due-Date Grouping + LNS',exact:true}).check();
  await expect(page.getByText('Minimize Tardy Jobs → Total Tardiness → Cmax',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Validate Data',exact:true}).click();
  await expect(page.getByText('Data Validation Passed',{exact:true})).toBeVisible();
  await page.getByText('Advanced settings',{exact:true}).click();
  await expect(page.getByLabel('due_group_days',{exact:true})).toHaveValue('3');
  await page.getByLabel('due_group_days',{exact:true}).selectOption('7');
  await page.getByLabel('seed_base',{exact:true}).fill('1051');
  await page.getByLabel('grouped_max_decodes',{exact:true}).fill('128');
  await expect(page.getByRole('checkbox',{name:/Reuse the best independently/})).not.toBeChecked();
  await page.getByRole('button',{name:'Run Optimization',exact:true}).click();
  const metrics=page.getByTestId('grouped-lns-metrics');
  await expect(metrics).toBeVisible();
  await expect(metrics).toContainText('Tardy jobs: 2 (25');
  await expect(metrics).toContainText('OTD: 75');
  await expect(metrics).toContainText('Overdue before PRE: 1');
  await expect(metrics).toContainText('Grouping: 7 calendar days');
  await expect(metrics).toContainText('Unique decodes: 128');
  expect(submitted.algorithms).toEqual([mode]);
  expect(submitted.settings).toMatchObject({due_group_days:7,seed_base:1051,grouped_max_decodes:128});
  expect(submitted.settings.due_date_extension_days).toBeUndefined();
  await page.screenshot({path:test.info().outputPath('grouped-result.png'),fullPage:true});
});
