param(
    [int]$RefreshSeconds = 5
)

$ErrorActionPreference = "SilentlyContinue"

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

$Services = @(
    @{ Port = [int]$PortMap.legacy_8011; Name = "Backend CentralNOC";     Health = "http://127.0.0.1:$($PortMap.legacy_8011)/api/deco/mesa-ayuda/health" },
    @{ Port = [int]$PortMap.direcciones; Name = "Direcciones Clientes";  Health = "http://127.0.0.1:$($PortMap.direcciones)/health" },
    @{ Port = [int]$PortMap.redes_neutras; Name = "Redes Neutras";         Health = "http://127.0.0.1:$($PortMap.redes_neutras)/health" },
    @{ Port = [int]$PortMap.helix_relacionados; Name = "Helix Relacionados";    Health = "http://127.0.0.1:$($PortMap.helix_relacionados)/health" },
    @{ Port = [int]$PortMap.pathtrak; Name = "Capturas PathTrak";     Health = "http://127.0.0.1:$($PortMap.pathtrak)/health" },
    @{ Port = [int]$PortMap.bitacora_helix; Name = "Bitacora Helix Async";  Health = "http://127.0.0.1:$($PortMap.bitacora_helix)/health" },
    @{ Port = [int]$PortMap.confirmaciones; Name = "Confirmaciones";        Health = "http://127.0.0.1:$($PortMap.confirmaciones)/health" },
    @{ Port = [int]$PortMap.ot_fibra_coaxial; Name = "OT Fibra / Coaxial";    Health = "http://127.0.0.1:$($PortMap.ot_fibra_coaxial)/health" },
    @{ Port = [int]$PortMap.smcc_analytics; Name = "SMCC Analytics";        Health = "http://127.0.0.1:$($PortMap.smcc_analytics)/health" },
    @{ Port = [int]$PortMap.dispositivos_vips; Name = "Dispositivos VIP"; Health = "http://127.0.0.1:$($PortMap.dispositivos_vips)/health" },
    @{ Port = [int]$PortMap.hfc; Name = "HFC"; Health = "http://127.0.0.1:$($PortMap.hfc)/health" }
)

function Get-ServiceRow {
    param($Service)

    $Listener = Get-NetTCPConnection `
        -LocalPort $Service.Port `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1

    if (-not $Listener) {
        return [PSCustomObject]@{
            Puerto   = $Service.Port
            Servicio = $Service.Name
            Estado   = "DOWN"
            Health   = "-"
            PID      = ""
        }
    }

    $PidValue = [int]$Listener.OwningProcess

    try {
        $R = Invoke-WebRequest `
            -Uri $Service.Health `
            -UseBasicParsing `
            -TimeoutSec 3

        $Health = "HTTP_$($R.StatusCode)"
    }
    catch {
        $Code = $null

        try {
            $Code = [int]$_.Exception.Response.StatusCode
        }
        catch {}

        if ($Code) {
            $Health = "HTTP_$Code"
        }
        else {
            $Health = "FAIL"
        }
    }

    return [PSCustomObject]@{
        Puerto   = $Service.Port
        Servicio = $Service.Name
        Estado   = "UP"
        Health   = $Health
        PID      = $PidValue
    }
}

while ($true) {

    Clear-Host

    $Now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    Write-Host "====================================================================================================" -ForegroundColor Cyan
    Write-Host " ATLAS - MONITOR DE SERVICIOS EN VIVO" -ForegroundColor Cyan
    Write-Host " ACTUALIZADO: $Now" -ForegroundColor DarkGray
    Write-Host " REFRESCO: $RefreshSeconds segundos   |   Ctrl+C para salir" -ForegroundColor DarkGray
    Write-Host "====================================================================================================" -ForegroundColor Cyan
    Write-Host ""

    $Rows = foreach ($Service in $Services) {
        Get-ServiceRow -Service $Service
    }

    foreach ($Row in $Rows) {

        $Color = "Green"

        if ($Row.Estado -eq "DOWN") {
            $Color = "Red"
        }
        elseif ($Row.Health -eq "FAIL") {
            $Color = "Yellow"
        }
        elseif ($Row.Health -notmatch "HTTP_200") {
            $Color = "Yellow"
        }

        $Line = "{0,-6} {1,-24} {2,-7} {3,-10} PID={4}" -f `
            $Row.Puerto,
            $Row.Servicio,
            $Row.Estado,
            $Row.Health,
            $Row.PID

        Write-Host $Line -ForegroundColor $Color
    }

    $Up = @($Rows | Where-Object { $_.Estado -eq "UP" }).Count
    $Down = @($Rows | Where-Object { $_.Estado -eq "DOWN" }).Count
    $BadHealth = @(
        $Rows |
        Where-Object {
            $_.Estado -eq "UP" -and
            $_.Health -ne "HTTP_200"
        }
    ).Count

    Write-Host ""
    Write-Host "----------------------------------------------------------------------------------------------------"

    if ($Down -eq 0 -and $BadHealth -eq 0) {
        Write-Host " ESTADO GENERAL: OK   |   UP=$Up   DOWN=$Down   HEALTH_WARN=$BadHealth" -ForegroundColor Green
    }
    else {
        Write-Host " ESTADO GENERAL: ATENCION   |   UP=$Up   DOWN=$Down   HEALTH_WARN=$BadHealth" -ForegroundColor Yellow
    }

    Write-Host "----------------------------------------------------------------------------------------------------"
    Write-Host ""
    Write-Host "Comandos de administracion:" -ForegroundColor Cyan
    Write-Host "  .\ATLAS_SERVICES.ps1 status"
    Write-Host "  .\ATLAS_SERVICES.ps1 start <puerto>"
    Write-Host "  .\ATLAS_SERVICES.ps1 restart <puerto>"
    Write-Host "  .\ATLAS_SERVICES.ps1 stop <puerto>"

    Start-Sleep -Seconds $RefreshSeconds
}