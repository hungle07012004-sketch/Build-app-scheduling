Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath 'dist/index.html')) { throw 'Run npm run build first.' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Create .venv and install backend/requirements-lock.txt first.' }
$previousFrontendMode = $env:SERVE_FRONTEND
try {
    $env:SERVE_FRONTEND = '1'
    & '.\.venv\Scripts\python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
}
finally { $env:SERVE_FRONTEND = $previousFrontendMode }
