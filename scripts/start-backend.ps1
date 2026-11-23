param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = Join-Path (Split-Path $projectRoot -Parent) 'contract-review-runtime'
$pythonExe = Join-Path $runtimeRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) { throw 'Run scripts/setup.ps1 first.' }
Set-Location $projectRoot
$env:PYTHONUTF8 = '1'
& $pythonExe -m uvicorn backend.app.main:app --host 127.0.0.1 --port $Port
