$ErrorActionPreference = "Stop"

$Root = "C:\xampp\htdocs\rutinas_hogares\api_bitacora_helix_async"
$Python = "$Root\.venv\Scripts\python.exe"

Set-Location $Root

& $Python -m uvicorn app.main:app `
    --host 127.0.0.1 `
    --port 8025