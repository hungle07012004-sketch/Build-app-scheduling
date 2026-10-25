import {defineConfig} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('.tmp-verification/reopen-fixture.json','utf8'));
export default defineConfig({
  testDir:'./tests/production',timeout:45_000,workers:1,
  outputDir:'.tmp-verification/production-browser',
  reporter:[['list'],['json',{outputFile:'.tmp-verification/production-browser-results.json'}]],
  use:{baseURL:'http://127.0.0.1:8001',headless:true,viewport:{width:1440,height:1000},trace:'retain-on-failure'},
  webServer:{
    command:'.venv\\Scripts\\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8001',
    url:'http://127.0.0.1:8001/api/health',reuseExistingServer:false,
    env:{SERVE_FRONTEND:'1',SCHEDULING_DATA_DIR:fixture.backend_dir},timeout:30_000
  }
});
