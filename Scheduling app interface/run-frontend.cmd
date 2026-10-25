@echo off
setlocal
cd /d "%~dp0"
set "frontend_task=%~1"
if not defined frontend_task set "frontend_task=dev"
for /d %%N in ("%~dp0.tools\node-*-win-x64") do set "PATH=%%~fN;%PATH%"
call npm.cmd run "%frontend_task%"
exit /b %errorlevel%
