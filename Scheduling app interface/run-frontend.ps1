param(
    [ValidateSet('dev','build','preview','check','test:e2e','test:production')]
    [string]$Task = 'dev'
)
Set-Location $PSScriptRoot
$frontendNode = Get-ChildItem -LiteralPath '.tools' -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'node-*-win-x64' } | Select-Object -First 1
if ($frontendNode) { $env:PATH = $frontendNode.FullName + ';' + $env:PATH }
npm.cmd run $Task
exit $LASTEXITCODE
