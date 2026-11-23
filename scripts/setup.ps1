$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = Join-Path (Split-Path $projectRoot -Parent) 'contract-review-runtime'
$pythonExe = Join-Path $runtimeRoot 'venv\Scripts\python.exe'
Set-Location $projectRoot
if (-not (Test-Path $pythonExe)) {
    python -m venv (Join-Path $runtimeRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python environment.' }
}
& $pythonExe -m pip install -r backend/requirements.lock.txt --index-url https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed.' }
} finally { Pop-Location }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host 'Setup complete. Start backend and frontend in separate terminals.'
