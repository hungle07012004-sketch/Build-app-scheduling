@echo off
setlocal
cd /d "%~dp0"
if not exist "dist\index.html" (
  echo Run run-frontend.cmd build first.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Create .venv and install backend/requirements-lock.txt first.
  exit /b 1
)
set "SERVE_FRONTEND=1"
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%
