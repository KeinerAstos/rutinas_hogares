$ErrorActionPreference = "Stop"

$ApiDir = "C:\xampp\htdocs\rutinas_hogares\api_ot_fibra_coaxial_v2"
$Port = 8027

Write-Host ""
Write-Host "==============================================="
Write-Host " API OT FIBRA / COAXIAL"
Write-Host " Puerto: $Port"
Write-Host "==============================================="
Write-Host ""

if (-not (Test-Path $ApiDir)) {
    Write-Host "ERROR: No existe la carpeta:"
    Write-Host $ApiDir
    Read-Host "ENTER para salir"
    exit 1
}

$Listener = Get-NetTCPConnection `
    -LocalPort $Port `
    -State Listen `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($Listener) {
    Write-Host "El puerto $Port ya esta ocupado."
    Write-Host "PID: $($Listener.OwningProcess)"
    Write-Host ""
    Write-Host "No se iniciara otra instancia."
    Read-Host "ENTER para salir"
    exit 0
}

$Python = (Get-Command python -ErrorAction Stop).Source

Write-Host "Python:"
Write-Host $Python
Write-Host ""
Write-Host "Carpeta API:"
Write-Host $ApiDir
Write-Host ""

Set-Location $ApiDir

Write-Host "Iniciando API..."
Write-Host "Health: http://127.0.0.1:$Port/health"
Write-Host ""
Write-Host "NO CIERRES ESTA VENTANA."
Write-Host "==============================================="
Write-Host ""

& $Python -m uvicorn main:app --host 0.0.0.0 --port $Port