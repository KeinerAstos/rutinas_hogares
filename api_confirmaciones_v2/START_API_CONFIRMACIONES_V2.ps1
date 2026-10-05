$ErrorActionPreference = "Stop"

$Root        = "C:\xampp\htdocs\rutinas_hogares\api_confirmaciones_v2"
$Port        = 8026
$ServiceName = "API CONFIRMACIONES V2"
$HealthUrl   = "http://127.0.0.1:8026/health"
$LogDir      = Join-Path $Root "logs"

Write-Host ""
Write-Host "====================================================================================================" -ForegroundColor Cyan
Write-Host " ATLAS - $ServiceName" -ForegroundColor Cyan
Write-Host " PUERTO $Port" -ForegroundColor Cyan
Write-Host "====================================================================================================" -ForegroundColor Cyan

if (-not (Test-Path $Root)) {
    throw "ROOT_NO_EXISTE: $Root"
}

# ==============================================================================================
# Detectar automaticamente el modulo ASGI.
# Soporta las tres estructuras usadas por los microservicios ATLAS:
#   main.py       -> main:app
#   api\main.py   -> api.main:app
#   app\main.py   -> app.main:app
# ==============================================================================================

if (Test-Path (Join-Path $Root "main.py")) {
    $App = "main:app"
}
elseif (Test-Path (Join-Path $Root "api\main.py")) {
    $App = "api.main:app"
}
elseif (Test-Path (Join-Path $Root "app\main.py")) {
    $App = "app.main:app"
}
else {
    throw "NO_SE_ENCONTRO_MAIN: se esperaba main.py, api\main.py o app\main.py dentro de $Root"
}

# ==============================================================================================
# Resolver Python propio del microservicio.
# ==============================================================================================

$Python = $null
$PythonCandidates = @(
    (Join-Path $Root ".venv\Scripts\python.exe"),
    (Join-Path $Root ".venv_prod\Scripts\python.exe"),
    (Join-Path $Root "venv\Scripts\python.exe")
)

foreach ($Candidate in $PythonCandidates) {
    if (Test-Path $Candidate) {
        $Python = $Candidate
        break
    }
}

if (-not $Python) {
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($PythonCommand) {
        $Python = $PythonCommand.Source
    }
}

if (-not $Python) {
    throw "PYTHON_NO_ENCONTRADO: crea .venv en $Root o instala Python en PATH"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# ==============================================================================================
# Evitar doble instancia.
# ==============================================================================================

$Existing = Get-NetTCPConnection `
    -LocalPort $Port `
    -State Listen `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($Existing) {
    $ExistingProc = Get-CimInstance Win32_Process `
        -Filter "ProcessId=$($Existing.OwningProcess)" `
        -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "PORT_${Port}_ALREADY_LISTENING" -ForegroundColor Yellow
    Write-Host "PID=$($Existing.OwningProcess)"
    Write-Host "CMD=$($ExistingProc.CommandLine)"
    exit 0
}

$Stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$OutLog = Join-Path $LogDir "uvicorn_${Port}_${Stamp}.out.log"
$ErrLog = Join-Path $LogDir "uvicorn_${Port}_${Stamp}.err.log"

# ==============================================================================================
# No heredar PYTHONPATH de CentralNOC ni de otro microservicio.
# ==============================================================================================

$OriginalPythonPath = $env:PYTHONPATH

try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue

    $Process = Start-Process `
        -FilePath $Python `
        -ArgumentList @(
            "-m",
            "uvicorn",
            $App,
            "--host",
            "127.0.0.1",
            "--port",
            "$Port",
            "--workers",
            "1"
        ) `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -PassThru `
        -WindowStyle Hidden

    Write-Host ""
    Write-Host "WRAPPER_PID=$($Process.Id)"
    Write-Host "ROOT=$Root"
    Write-Host "PYTHON=$Python"
    Write-Host "APP=$App"
    Write-Host "PORT=$Port"
    Write-Host "OUT_LOG=$OutLog"
    Write-Host "ERR_LOG=$ErrLog"
}
finally {
    if ([string]::IsNullOrEmpty($OriginalPythonPath)) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $OriginalPythonPath
    }
}

# ==============================================================================================
# Esperar listener.
# ==============================================================================================

$Listening = $null

for ($i = 1; $i -le 45; $i++) {
    $Listening = Get-NetTCPConnection `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1

    if ($Listening) { break }
    Start-Sleep -Seconds 1
}

if (-not $Listening) {
    Write-Host "LISTENER=FAIL" -ForegroundColor Red

    if (Test-Path $ErrLog) {
        Write-Host ""
        Write-Host "ULTIMAS LINEAS DEL ERROR LOG:" -ForegroundColor Red
        Get-Content $ErrLog -Tail 120
    }

    exit 1
}

# ==============================================================================================
# Health check. Un /health inexistente no tumba un servicio cuyo listener si esta UP.
# ==============================================================================================

$HealthState = "FAIL"

try {
    $HealthResponse = Invoke-WebRequest `
        -Uri $HealthUrl `
        -UseBasicParsing `
        -TimeoutSec 5

    $HealthState = "HTTP_$($HealthResponse.StatusCode)"
}
catch {
    $HealthCode = $null
    try { $HealthCode = [int]$_.Exception.Response.StatusCode } catch {}
    if ($HealthCode) {
        $HealthState = "HTTP_$HealthCode"
    }
}

# ==============================================================================================
# Validar proceso real.
# ==============================================================================================

$Listening = Get-NetTCPConnection `
    -LocalPort $Port `
    -State Listen `
    -ErrorAction Stop |
    Select-Object -First 1

$RealProc = Get-CimInstance Win32_Process `
    -Filter "ProcessId=$($Listening.OwningProcess)" `
    -ErrorAction Stop

$CmdLine = [string]$RealProc.CommandLine

Write-Host ""
Write-Host "REAL_PID=$($Listening.OwningProcess)"
Write-Host "REAL_CMD=$CmdLine"
Write-Host "HEALTH=$HealthState"

if ($CmdLine -notmatch [regex]::Escape("--port $Port")) {
    Write-Host "RUNTIME_VALIDATION=FAIL_PORT" -ForegroundColor Red
    exit 1
}

if ($CmdLine -notmatch [regex]::Escape($App)) {
    Write-Host "RUNTIME_VALIDATION=FAIL_APP" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "LISTENER=OK" -ForegroundColor Green
Write-Host "RUNTIME_VALIDATION=OK" -ForegroundColor Green
Write-Host "$ServiceName ESCUCHANDO EN $Port" -ForegroundColor Green

if ($HealthState -eq "FAIL") {
    Write-Host "ADVERTENCIA: el puerto esta UP pero $HealthUrl no respondio." -ForegroundColor Yellow
}

exit 0
