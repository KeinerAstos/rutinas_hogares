$ErrorActionPreference = "Stop"

$Backend = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python  = Join-Path $Backend ".venv\Scripts\python.exe"
$Guard   = Join-Path $PSScriptRoot "guard_endpoints_centralizados.py"

Write-Host ""
Write-Host "========================================================================================================================" -ForegroundColor Cyan
Write-Host " ATLAS - RUN GUARD ENDPOINTS CENTRALIZADOS" -ForegroundColor Cyan
Write-Host "========================================================================================================================" -ForegroundColor Cyan
Write-Host " MODE=READ_ONLY" -ForegroundColor Cyan
Write-Host " BACKEND=$Backend" -ForegroundColor Cyan
Write-Host "========================================================================================================================" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $Python)) {
    throw "PYTHON_NO_EXISTE=$Python"
}

if (-not (Test-Path -LiteralPath $Guard)) {
    throw "GUARD_NO_EXISTE=$Guard"
}

Push-Location $Backend

try {
    & $Python $Guard
    $Rc = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($Rc -ne 0) {
    throw "GUARD_FAIL_RC=$Rc"
}

Write-Host ""
Write-Host "GUARD_PERMANENTE=PASS" -ForegroundColor Green
Write-Host "MODIFICACIONES=0" -ForegroundColor Green