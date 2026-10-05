$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "PYTHON_NO_EXISTE=$Python"
}

$Existing = Get-NetTCPConnection `
    -LocalPort 8021 `
    -State Listen `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($Existing) {
    Write-Host "8021_ALREADY_LISTENING=YES"
    Write-Host "PID=$([int]$Existing.OwningProcess)"
    exit 0
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Set-Location $Root

$LogOut = Join-Path $LogDir "uvicorn_8021_$Stamp.out.log"
$LogErr = Join-Path $LogDir "uvicorn_8021_$Stamp.err.log"

& $Python `
    -m uvicorn `
    app.main:app `
    --host 127.0.0.1 `
    --port 8021 `
    --workers 1 `
    1>> $LogOut `
    2>> $LogErr
