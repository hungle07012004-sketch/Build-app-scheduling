import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 180_000,
  workers: 1,
  use: {baseURL:'http://127.0.0.1:8443',headless:true,viewport:{width:1440,height:1000},trace:'retain-on-failure'},
  webServer: [
    {command:'.venv\\Scripts\\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000',
     url:'http://127.0.0.1:8000/api/health',reuseExistingServer:!process.env.CI,
     env:{SCHEDULING_DATA_DIR:'.tmp-e2e/backend-'+Date.now()},timeout:30_000},
    {command:'npm run dev -- --host 127.0.0.1',url:'http://127.0.0.1:8443',reuseExistingServer:!process.env.CI,timeout:30_000}
  ]
});
