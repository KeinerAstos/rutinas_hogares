param(
    [Parameter(Position=0)]
    [ValidateSet("status","start","stop","restart")]
    [string]$Action = "status",

    [Parameter(Position=1)]
    [string]$Port = '0' 
)

$ErrorActionPreference = "Stop"
$Port = if ($Port -eq 'all') {
    0
} elseif ($Port -match '^[0-9]{1,5}$') {
    [int]$Port
} else {
    throw "PUERTO_INVALIDO: usa un numero o 'all'"
}


# ==============================================================================================
# ATLAS SERVICES
# Administrador central de servicios
#
# 8011  Backend CentralNOC
# 8021  Direcciones Clientes
# 8022  Redes Neutras V2 (reemplaza legacy)
# 8023  Helix Relacionados
# 8024  Capturas PathTrak V2 (reemplaza legacy)
# 8025  Bitacora Helix Async
# 8026  Confirmaciones V2 (reemplaza legacy)
# 8027  OT Fibra / Coaxial
# 8028  SMCC Analytics
#
# 8013 NO SE ADMINISTRA: instancia duplicada retirada.
# ==============================================================================================

# Mapa compartido de puertos ATLAS. Se carga al iniciar el script.
# Dashboard_Hogar y las APIs estan en carpetas distintas.
# ATLAS_PORTS_FILE permite cambiar la ubicacion del mapa si se mueve el Dashboard.
$PortsFile = if ($env:ATLAS_PORTS_FILE) {
    $env:ATLAS_PORTS_FILE
} else {
    @(
        'C:\xampp\htdocs\atlas\modules\Dashboard_Hogar\backend\config\atlas_ports.json',
        'C:\xampp\htdocs\CentralNOC\modules\Dashboard_Hogar\backend\config\atlas_ports.json'
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not (Test-Path -LiteralPath $PortsFile -PathType Leaf)) { throw "ATLAS_PORTS_NO_EXISTE: $PortsFile" }
try { $PortMap = Get-Content -LiteralPath $PortsFile -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop }
catch { throw "ATLAS_PORTS_JSON_INVALIDO: $($_.Exception.Message)" }
$RequiredPorts = @('legacy_8011','direcciones','redes_neutras','helix_relacionados','pathtrak','bitacora_helix','confirmaciones','ot_fibra_coaxial','smcc_analytics','dispositivos_vips','hfc')
$SeenPorts = @{}
foreach ($Key in $RequiredPorts) {
    $Property = $PortMap.PSObject.Properties[$Key]
    if ($null -eq $Property -or [string]$Property.Value -cnotmatch '^[0-9]{1,5}$') { throw "ATLAS_PORT_INVALIDO: $Key" }
    $Value = [int]$Property.Value
    if ($Value -lt 1024 -or $Value -gt 65535 -or $SeenPorts.ContainsKey($Value)) { throw "ATLAS_PORT_DUPLICADO_O_FUERA_DE_RANGO: $Key=$Value" }
    $SeenPorts[$Value] = $Key
}

$Services = @{

    ([int]$PortMap.legacy_8011) = @{
        Name       = "Backend CentralNOC"
        Root       = (Join-Path $PSScriptRoot 'api_orquestador_hogares')
        Python     = (Join-Path $PSScriptRoot 'api_orquestador_hogares\.venv\Scripts\python.exe')
        App        = "app.main:app"
        Workers    = 1
        Health     = "http://127.0.0.1:$($PortMap.legacy_8011)/api/deco/mesa-ayuda/health"
    }

    ([int]$PortMap.direcciones) = @{
        Name       = "Direcciones Clientes"
        Root       = (Join-Path $PSScriptRoot 'api_direccion_clientes')
        Python     = (Join-Path $PSScriptRoot 'api_direccion_clientes\.venv\Scripts\python.exe')
        App        = "app.main:app"
        Workers    = 1
        Health     = "http://127.0.0.1:$($PortMap.direcciones)/health"
    }

    ([int]$PortMap.redes_neutras) = @{
        Name        = "Redes Neutras V2"
        Root        = (Join-Path $PSScriptRoot 'api_redes_neutras_v2')
        Python      = ""
        App         = "main:app"
        Workers     = 1
        Health      = "http://127.0.0.1:$($PortMap.redes_neutras)/health"
        StartScript = (Join-Path $PSScriptRoot 'api_redes_neutras_v2\START_API_REDES_NEUTRAS_V2.ps1')
    }

    ([int]$PortMap.helix_relacionados) = @{
        Name       = "Helix Relacionados"
        Root       = (Join-Path $PSScriptRoot 'api_rpa_helix_relacionados')
        Python     = (Join-Path $PSScriptRoot 'api_rpa_helix_relacionados\.venv_prod\Scripts\python.exe')
        App        = "app.main:app"
        Workers    = 1
        Health     = "http://127.0.0.1:$($PortMap.helix_relacionados)/health"
    }

    ([int]$PortMap.pathtrak) = @{
        Name        = "Capturas PathTrak V2"
        Root        = (Join-Path $PSScriptRoot 'api_captures_pathtrak_v2')
        Python      = ""
        App         = "auto"
        Workers     = 1
        Health      = "http://127.0.0.1:$($PortMap.pathtrak)/health"
        StartScript = (Join-Path $PSScriptRoot 'api_captures_pathtrak_v2\START_API_CAPTURAS_PATHTRAK_V2.ps1')
    }

    ([int]$PortMap.bitacora_helix) = @{
        Name       = "Bitacora Helix Async"
        Root       = (Join-Path $PSScriptRoot 'api_bitacora_helix_async')
        Python     = (Join-Path $PSScriptRoot 'api_bitacora_helix_async\.venv\Scripts\python.exe')
        App        = "app.main:app"
        Workers    = 0
        Health     = "http://127.0.0.1:$($PortMap.bitacora_helix)/health"
    }

    ([int]$PortMap.confirmaciones) = @{
        Name        = "Confirmaciones V2"
        Root        = (Join-Path $PSScriptRoot 'api_confirmaciones_v2')
        Python      = ""
        App         = "auto"
        Workers     = 1
        Health      = "http://127.0.0.1:$($PortMap.confirmaciones)/health"
        StartScript = (Join-Path $PSScriptRoot 'api_confirmaciones_v2\START_API_CONFIRMACIONES_V2.ps1')
    }

    ([int]$PortMap.ot_fibra_coaxial) = @{
        Name       = "OT Fibra / Coaxial"
        Root       = (Join-Path $PSScriptRoot 'api_ot_fibra_coaxial_v2')
        Python     = ""
        App        = "main:app"
        AppDir     = (Join-Path $PSScriptRoot 'api_ot_fibra_coaxial_v2')
        Workers    = 1
        Health     = "http://127.0.0.1:$($PortMap.ot_fibra_coaxial)/health"
    }

    ([int]$PortMap.dispositivos_vips) = @{
        Name       = "Dispositivos VIP"
        Root       = (Join-Path $PSScriptRoot 'api_dispositivos_vips')
        Python     = ""
        App        = "auto"
        AppDir     = (Join-Path $PSScriptRoot 'api_dispositivos_vips')
        Workers    = 1
        Health     = "http://127.0.0.1:$($PortMap.dispositivos_vips)/health"
    }

    ([int]$PortMap.hfc) = @{
        Name       = "HFC"
        Root       = (Join-Path $PSScriptRoot 'api_hfc')
        Python     = ""
        App        = "auto"
        AppDir     = (Join-Path $PSScriptRoot 'api_hfc')
        Workers    = 1
        Health     = "http://127.0.0.1:$($PortMap.hfc)/health"
    }

    ([int]$PortMap.smcc_analytics) = @{
        Name       = "SMCC Analytics"
        Root       = (Join-Path $PSScriptRoot 'api_smcc_analytics')
        Python     = (Join-Path $PSScriptRoot 'api_smcc_analytics\.venv\Scripts\python.exe')
        App        = "app.main:app"
        Workers    = 0
        Health     = "http://127.0.0.1:$($PortMap.smcc_analytics)/health"
    }
}


function Get-AtlasListener {
    param([int]$Port)

    return Get-NetTCPConnection `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
}


function Get-AtlasHealth {
    param(
        [int]$Port,
        [string]$Url
    )

    try {
        $Response = Invoke-WebRequest `
            -Uri $Url `
            -UseBasicParsing `
            -TimeoutSec 5

        return "HTTP_$($Response.StatusCode)"
    }
    catch {
        $Code = $null

        try {
            $Code = [int]$_.Exception.Response.StatusCode
        }
        catch {}

        if ($Code) {
            return "HTTP_$Code"
        }

        return "FAIL"
    }
}


function Show-AtlasStatus {

    Write-Host ""
    Write-Host "====================================================================================================" -ForegroundColor Cyan
    Write-Host " ATLAS - ESTADO DE SERVICIOS" -ForegroundColor Cyan
    Write-Host "====================================================================================================" -ForegroundColor Cyan

    $Rows = foreach ($ServicePort in ($Services.Keys | Sort-Object)) {

        $S = $Services[$ServicePort]
        $L = Get-AtlasListener -Port $ServicePort

        if ($L) {

            $PidValue = [int]$L.OwningProcess

            $Proc = Get-CimInstance Win32_Process `
                -Filter "ProcessId=$PidValue" `
                -ErrorAction SilentlyContinue

            $Health = Get-AtlasHealth `
                -Port $ServicePort `
                -Url $S.Health

            [PSCustomObject]@{
                Puerto   = $ServicePort
                Servicio = $S.Name
                Estado   = "UP"
                Health   = $Health
                PID      = $PidValue
                Proceso  = $Proc.Name
            }
        }
        else {

            [PSCustomObject]@{
                Puerto   = $ServicePort
                Servicio = $S.Name
                Estado   = "DOWN"
                Health   = "-"
                PID      = ""
                Proceso  = ""
            }
        }
    }

    $Rows |
        Format-Table `
            Puerto,
            Servicio,
            Estado,
            Health,
            PID,
            Proceso `
            -AutoSize

    $Up   = @($Rows | Where-Object Estado -eq "UP").Count
    $Down = @($Rows | Where-Object Estado -eq "DOWN").Count

    Write-Host ""
    Write-Host "UP=$Up  DOWN=$Down  TOTAL=$($Rows.Count)" -ForegroundColor Cyan
}


function Start-AtlasService {
    param([int]$ServicePort)

    if (-not $Services.ContainsKey($ServicePort)) {
        throw "PUERTO_NO_ADMINISTRADO=$ServicePort"
    }

    $S = $Services[$ServicePort]

    $Existing = Get-AtlasListener -Port $ServicePort

    if ($Existing) {
        Write-Host "$ServicePort $($S.Name) -> YA ESTA UP" -ForegroundColor Yellow
        return
    }

    if (-not (Test-Path $S.Root)) {
        throw "ROOT_NO_EXISTE: $($S.Root)"
    }

    # Los servicios V2 pueden tener un START_*.ps1 propio.
    # En ese caso ATLAS delega el arranque al script del microservicio.
    $HasStartScript = $S.ContainsKey("StartScript") -and -not [string]::IsNullOrWhiteSpace([string]$S.StartScript)

    if ($HasStartScript) {

        if (-not (Test-Path $S.StartScript)) {
            throw "START_SCRIPT_NO_EXISTE: $($S.StartScript)"
        }

        # START_*.ps1 V2 tienen puerto propio. Impedir un arranque inconsistente.
        $DefaultPorts = @{ 'Redes Neutras V2' = 8022; 'Capturas PathTrak V2' = 8024; 'Confirmaciones V2' = 8026 }
        if ($DefaultPorts.ContainsKey($S.Name) -and $ServicePort -ne $DefaultPorts[$S.Name]) {
            throw "START_SCRIPT_PUERTO_NO_CENTRALIZADO: $($S.Name). Adaptar $($S.StartScript) antes de usar el puerto $ServicePort"
        }

        Write-Host "$ServicePort $($S.Name) -> INICIANDO CON SCRIPT V2..." -ForegroundColor Yellow

        $PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source

        & $PowerShellExe `
            -NoProfile `
            -ExecutionPolicy Bypass `
            -File $S.StartScript

        if ($LASTEXITCODE -ne 0) {
            throw "START_SCRIPT_FAIL_$ServicePort EXIT_CODE=$LASTEXITCODE"
        }
    }
    else {

        # OT V2 usa el Python del sistema, igual que INICIAR_API_OT_8027.ps1.
        if ([string]::IsNullOrWhiteSpace([string]$S.Python) -and $S.Name -eq "OT Fibra / Coaxial") {
            $S.Python = (Get-Command python -ErrorAction Stop).Source
        }
        # Descubrir el runtime de 8029/8030 sin asumir la estructura interna.
        if ($S.App -eq "auto") {
            if (Test-Path (Join-Path $S.Root 'app\main.py')) {
                $S.App = 'app.main:app'
            }
            elseif (Test-Path (Join-Path $S.Root 'main.py')) {
                $S.App = 'main:app'
            }
            else {
                throw "APP_NO_DETECTADA: $($S.Root). Revisar modulo ASGI real"
            }
            $PythonOptions = @(
                (Join-Path $S.Root '.venv\Scripts\python.exe'),
                (Join-Path $S.Root '.venv_prod\Scripts\python.exe')
            )
            $S.Python = $PythonOptions | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1
            if (-not $S.Python) { throw "PYTHON_NO_EXISTE: crear .venv en $($S.Root)" }
        }
        if (-not (Test-Path $S.Python)) {
            throw "PYTHON_NO_EXISTE: $($S.Python)"
        }

        $Logs = Join-Path $S.Root "logs"

        New-Item `
            -ItemType Directory `
            -Path $Logs `
            -Force |
            Out-Null

        $OutLog = Join-Path $Logs "atlas_$ServicePort.out.log"
        $ErrLog = Join-Path $Logs "atlas_$ServicePort.err.log"

        $Args = @(
            "-m",
            "uvicorn",
            $S.App,
            "--host",
            "127.0.0.1",
            "--port",
            "$ServicePort"
        )

        if ($S.ContainsKey("AppDir")) {
            $Args += @("--app-dir", $S.AppDir)
        }

        if ([int]$S.Workers -gt 0) {
            $Args += @(
                "--workers",
                "$($S.Workers)"
            )
        }

        Write-Host "$ServicePort $($S.Name) -> INICIANDO..." -ForegroundColor Yellow

        Start-Process `
            -FilePath $S.Python `
            -ArgumentList $Args `
            -WorkingDirectory $S.Root `
            -RedirectStandardOutput $OutLog `
            -RedirectStandardError $ErrLog `
            -WindowStyle Hidden
    }

    $Ok = $false

    for ($i = 1; $i -le 15; $i++) {

        Start-Sleep -Seconds 1

        if (Get-AtlasListener -Port $ServicePort) {
            $Ok = $true
            break
        }
    }

    if (-not $Ok) {

        Write-Host "$ServicePort -> NO LEVANTO" -ForegroundColor Red

        if (-not $HasStartScript) {
            Write-Host "ERROR_LOG=$ErrLog" -ForegroundColor Red

            if (Test-Path $ErrLog) {
                Get-Content $ErrLog -Tail 25
            }
        }
        else {
            Write-Host "Revisa la carpeta logs del microservicio: $($S.Root)\logs" -ForegroundColor Red
        }

        throw "START_FAIL_$ServicePort"
    }

    $Health = Get-AtlasHealth `
        -Port $ServicePort `
        -Url $S.Health

    if ($Health -eq "FAIL") {
        Write-Host "$ServicePort -> LISTEN PERO HEALTH FAIL" -ForegroundColor Yellow
    }
    else {
        Write-Host "$ServicePort $($S.Name) -> UP / $Health" -ForegroundColor Green
    }
}


function Stop-AtlasService {
    param([int]$ServicePort)

    if (-not $Services.ContainsKey($ServicePort)) {
        throw "PUERTO_NO_ADMINISTRADO=$ServicePort"
    }

    $S = $Services[$ServicePort]

    $L = Get-AtlasListener -Port $ServicePort

    if (-not $L) {
        Write-Host "$ServicePort $($S.Name) -> YA ESTA DOWN" -ForegroundColor Yellow
        return
    }

    $PidValue = [int]$L.OwningProcess

    $Proc = Get-CimInstance Win32_Process `
        -Filter "ProcessId=$PidValue" `
        -ErrorAction Stop

    Write-Host ""
    Write-Host "PORT=$ServicePort"
    Write-Host "PID=$PidValue"
    Write-Host "CMD=$($Proc.CommandLine)"

    # Protección contra matar un proceso incorrecto
    if ($Proc.CommandLine -notmatch [regex]::Escape("--port $ServicePort")) {
        throw "SEGURIDAD: PID $PidValue no contiene --port $ServicePort"
    }

    if ($Proc.CommandLine -notmatch [regex]::Escape($S.Root)) {
        # En algunos Python launcher el root no aparece siempre.
        # Comprobamos al menos que sea uvicorn.
        if ($Proc.CommandLine -notmatch "uvicorn") {
            throw "SEGURIDAD: proceso no identificado como uvicorn"
        }
    }

    Write-Host "$ServicePort $($S.Name) -> DETENIENDO..." -ForegroundColor Yellow

    Stop-Process `
        -Id $PidValue `
        -Force `
        -ErrorAction Stop

    for ($i = 1; $i -le 10; $i++) {

        Start-Sleep -Milliseconds 500

        if (-not (Get-AtlasListener -Port $ServicePort)) {
            Write-Host "$ServicePort $($S.Name) -> DOWN" -ForegroundColor Green
            return
        }
    }

    throw "STOP_FAIL_$ServicePort"
}


function Start-AllAtlasServices {

    Write-Host ""
    Write-Host "====================================================================================================" -ForegroundColor Cyan
    Write-Host " ATLAS - START ALL" -ForegroundColor Cyan
    Write-Host "====================================================================================================" -ForegroundColor Cyan

    foreach ($ServicePort in ($Services.Keys | Sort-Object)) {

        try {
            Start-AtlasService -ServicePort $ServicePort
        }
        catch {
            Write-Host "$ServicePort -> ERROR: $($_.Exception.Message)" -ForegroundColor Red
        }
    }

    Show-AtlasStatus
}


function Stop-AllAtlasServices {

    Write-Host ""
    Write-Host "====================================================================================================" -ForegroundColor Red
    Write-Host " ATLAS - STOP ALL" -ForegroundColor Red
    Write-Host "====================================================================================================" -ForegroundColor Red

    foreach ($ServicePort in ($Services.Keys | Sort-Object -Descending)) {

        try {
            Stop-AtlasService -ServicePort $ServicePort
        }
        catch {
            Write-Host "$ServicePort -> ERROR: $($_.Exception.Message)" -ForegroundColor Red
        }
    }

    Show-AtlasStatus
}


function Restart-AtlasService {
    param([int]$ServicePort)

    if (-not $Services.ContainsKey($ServicePort)) {
        throw "PUERTO_NO_ADMINISTRADO=$ServicePort"
    }

    Write-Host ""
    Write-Host "====================================================================================================" -ForegroundColor Cyan
    Write-Host " ATLAS - RESTART $ServicePort" -ForegroundColor Cyan
    Write-Host "====================================================================================================" -ForegroundColor Cyan

    Stop-AtlasService -ServicePort $ServicePort

    Start-Sleep -Seconds 1

    Start-AtlasService -ServicePort $ServicePort
}


# ==============================================================================================
# EJECUCION
# ==============================================================================================

switch ($Action) {

    "status" {
        Show-AtlasStatus
    }

    "start" {

        if ($Port -gt 0) {
            Start-AtlasService -ServicePort $Port
            Show-AtlasStatus
        }
        else {
            Start-AllAtlasServices
        }
    }

    "stop" {

        if ($Port -gt 0) {
            Stop-AtlasService -ServicePort $Port
            Show-AtlasStatus
        }
        else {
            Stop-AllAtlasServices
        }
    }

    "restart" {

        if ($Port -le 0) {
            throw "Para restart debes indicar un puerto. Ejemplo: .\ATLAS_SERVICES.ps1 restart 8021"
        }

        Restart-AtlasService -ServicePort $Port
        Show-AtlasStatus
    }
}