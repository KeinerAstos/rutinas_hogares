$ErrorActionPreference = "Stop"

$Root = "C:\xampp\htdocs\rutinas_hogares\api_rpa_helix_relacionados"

Set-Location $Root

$Python = Join-Path $Root ".venv_prod\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "No existe $Python"
}

& $Python `
    -m uvicorn `
    app.main:app `
    --host 127.0.0.1 `
    --port 8023 `
    --workers 1
