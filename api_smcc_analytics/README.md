# api_smcc_analytics

Microservicio analítico para conversaciones SMCC procesadas por ATLAS.

## Responsabilidad

- Leer datos de tracking SMCC.
- Calcular estadísticas.
- No modificar sesiones.
- No modificar eventos.
- No escribir en SMCC.
- No contener lógica conversacional.

## Puerto

8028

## Endpoints

GET /health

GET /api/v1/smcc/analytics/dashboard

Parámetros:

- date_from=YYYY-MM-DD
- date_to=YYYY-MM-DD

## Fuente

SMCC_TRACKING_DATA_DIR