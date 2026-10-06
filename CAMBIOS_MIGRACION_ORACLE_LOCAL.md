# Cambios específicos: migración local de lecturas Oracle

Fecha: 2026-10-06

Alcance: `api_direccion_clientes` en la copia local del repositorio.

## Archivos agregados

### `api_direccion_clientes/app/config/oracle_settings.py`

- Carga la configuración Oracle desde el `.env` local sin imprimir credenciales.
- Valida las variables necesarias y define valores predeterminados para esquema y pool.
- Restringe el nombre del esquema a caracteres alfanuméricos y guion bajo antes de incorporarlo a las consultas.

### `api_direccion_clientes/app/services/helix/oracle_repository.py`

- Crea de forma diferida un pool de conexiones con `oracledb`.
- Implementa consultas parametrizadas y de solo lectura para:
  - `ARADMIN.WOI_WORKORDER`, por `WORK_ORDER_ID`.
  - `ARADMIN.HPD_HELP_DESK`, por `INCIDENT_NUMBER`.
  - `ARADMIN.AST_BASEELEMENT`, por `RECONCILIATION_IDENTITY`, limitada a una fila.
- Cada consulta selecciona únicamente las columnas indicadas por la migración; no ejecuta escrituras ni `SELECT *`.

### `api_direccion_clientes/app/services/helix/oracle_summary_service.py`

- Implementa la adaptación del resumen Oracle al contrato que consume Direcciones.
- Reutiliza `parse_work_order_title()` y respeta el orden de prioridad: resumen WO, descripción WO, CI de la WO, descripción del incidente, CI del incidente y AST como fallback.
- Mantiene estado, incidente relacionado desde `ROOT_INCIDENT`, categoría operacional, datos de aliado y ubicación, descripciones, CI y topología.
- La descripción completa puede completar o corregir rack, shelf, slot y port del resumen.
- Usa `NODE` de Oracle para corregir la falsa detección de “AFECTADOS” como nodo en FTTH.
- Evita consultar AST cuando la topología HFC ya está resuelta por nodo.
- Conserva `port` y agrega `puertos_afectados`.
- Reutiliza `parse_descripcion_impacto_troncal()` para derivar los campos de impacto que pueda reconocer la descripción del incidente.
- Identifica WO GES y las devuelve para que la función pública conserve el enriquecimiento temporal de SmartIT.

## Archivo modificado

### `api_direccion_clientes/app/services/helix/service.py`

- `consultar_resumen_ot_helix(ot)` conserva su nombre y firma.
- Intenta Oracle primero para identificadores WO válidos.
- Devuelve directamente el resultado Oracle si la consulta funciona.
- En errores Oracle o para GES, continúa por el flujo SmartIT existente y registra que se usó fallback.
- No modifica la publicación de notas Helix.

## Dependencia

`api_direccion_clientes/requirements.txt` contiene `oracledb`, pero ese cambio ya estaba presente en el árbol de trabajo antes de esta tarea; se conservó sin editarlo.

## Validación realizada

- `py_compile` para los tres módulos Oracle y `service.py`.
- Pruebas directas contra Oracle de estas WO:

| WO | Resultado observado |
| --- | --- |
| `WO0000005893979` | FTTH, `ZAC-CTG.EL_BOSQUE-CP1`, rack 1, shelf 1, slot 4, port 10; INC `INC000006966546`. |
| `WO0000005892881` | FTTH troncal, `ZAC-ANT.APART-B2-C600`, rack 1, shelf 1, slot 17, port 13; INC `INC000006963419`. |
| `WO0000005876643` | HFC, nodo `O1E`; INC `INC000006924426`. |
| `WO0000005847725` | HFC, nodo `RRO`; INC `INC000006844967`. |
| `WO0000005814186` | FTTH troncal, `ZAC-BOG.ATP_ASTURIAS-N2-C600`, rack 1, shelf 1, slot 12, port 3; INC `INC000006775256`. |
| `WO0000005847731` | FTTH, `ZAC-BOG.ATP_ASTURIAS-N10-C600`, rack 1, shelf 1, slot 4, port 15; INC `INC000006845214`. |

- La función pública devolvió resultado Oracle para una WO FTTH y una HFC.
- `/health` y `/ready` respondieron correctamente en `127.0.0.1:8021`. El puerto ya estaba ocupado por un proceso Python preexistente y no se detuvo; por eso no se verificó por HTTP la versión recién editada ni se ejecutó una prueba de extremo a extremo del endpoint de direcciones.

## Archivos fuera de alcance

No se modificaron router, downstream FTTH/HFC, Diagnosticador, PathTrak, el mecanismo de notas Helix ni otros APIs.
