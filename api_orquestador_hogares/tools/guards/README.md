# ATLAS - Guard de endpoints centralizados

Este directorio contiene el guard permanente que protege la politica de centralizacion de endpoints de ATLAS.

## Politica

Los URLs, hosts, IPs y puertos de infraestructura deben administrarse desde `.env` y ser consumidos mediante loaders de `app/config/*`.

El inventario dinamico de red (OLT, CMTS y capacidades por equipo) pertenece al inventario correspondiente y no debe hardcodearse en la logica de servicios.

## Ejecutar

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

& "C:\xampp\htdocs\CentralNOC\modules\Dashboard_Hogar\backend\tools\guards\RUN_GUARD_ENDPOINTS_CENTRALIZADOS.ps1"
```

Resultado esperado:

```text
VIOLATIONS=0
GUARD_STATUS=PASS
CENTRALIZATION_POLICY=COMPLIANT
ENDPOINTS_HARDCODEADOS_REALES=0
GUARD_PERMANENTE=PASS
```

Si aparece `GUARD_STATUS=FAIL`, no debe desplegarse el cambio hasta revisar los endpoints reportados.

El guard es READ ONLY y no realiza conexiones externas.