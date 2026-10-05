# API de Direcciones de Clientes

API FastAPI que recibe una orden de trabajo (WO), consulta Helix/SmartIT y
obtiene direcciones según la red detectada: HFC, FTTH, GES o CGE. EYN se
devuelve como categoría bloqueada.

## Inicio

1. Crear `.env` a partir de `.env.example` y configurar credenciales y
   endpoints.
2. Instalar dependencias con `pip install -r requirements.txt`.
3. Instalar Chromium con `playwright install chromium`.
4. Ejecutar `./RUN_API_8021.ps1`.

La aplicación se sirve como `app.main:app` en `127.0.0.1:8021`, con un worker.

## Endpoints

- `POST /api/v1/direcciones/consultar` con cuerpo `{"wo":"WO..."}`.
- `GET /health` comprueba el proceso.
- `GET /ready` comprueba que el router funcional esté disponible.
- `GET /openapi.json` expone el contrato OpenAPI.

## Estructura

- `app/main.py`: API y contrato HTTP.
- `app/services/direcciones_router.py`: clasificación y selección de flujo.
- `app/services/helix/`: consulta y extracción desde Helix/SmartIT.
- `app/services/hfc_direcciones_service.py`: flujo HFC.
- `app/services/ftth_troncal_direcciones_service.py`: flujo FTTH.
- `app/integrations/pathtrak/`: consulta PathTrak y resolución de MAC.
- `app/integrations/acs_tr069/`: resolución FTTH por ACS.
- `app/integrations/diagnosticador/`: clientes vecinos y direcciones.
- `data/ftth_atp/`: inventario ATP usado por FTTH.

## Flujos

HFC: Helix → nodo → caché/CMTS → PathTrak → MAC → Diagnosticador →
direcciones. La búsqueda PathTrak conserva el fallback `End + Space` para
nodos EDE.

FTTH: Helix → topología/ATP/OLT → serial → ACS → cuenta → Diagnosticador →
direcciones, con fallback por MAC cuando corresponde.

La API no genera screenshots ni evidencias HTML. Los logs operativos se
escriben en `logs/` y no deben usarse como respaldo de código.
