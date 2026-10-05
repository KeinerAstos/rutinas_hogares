$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "No existe .venv. Prepare primero el entorno virtual."
}

Set-Location $Root

& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8028