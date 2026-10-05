from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse


SERVICE_NAME = "api_confirmaciones"
SERVICE_VERSION = "1.0.0"
SERVICE_PORT = 8026

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

IMAGE_NAME = "confirmacion_hfc_ftth_claro_te_ayuda.jpg"
IMAGE_PATH = ASSETS_DIR / IMAGE_NAME
IMAGE_URL = "/api/confirmaciones/imagen"

# Las respuestas de FTTH/HFC son completamente estaticas. Se construyen una sola
# vez al iniciar el proceso en lugar de recrear el mismo diccionario en cada request.
CONFIRMATIONS = {
    "FTTH": {
        "ok": True,
        "codigo": "MESA_CONFIRMACION_FTTH_INFO",
        "estado": "ESPERANDO_TIPO",
        "resultado_estado": "RESULTADO_CONFIRMACION_FTTH",
        "respuesta": (
            "Para realizar la Confirmación FTTH, "
            "utilice la opción 1-1-4 en Claro Te Ayuda."
        ),
        "imagen_url": IMAGE_URL,
        "tipo_respuesta": "imagen_informativa",
        "volver_menu": True,
        "pide_wo": False,
        "_api": {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "port": SERVICE_PORT,
        },
    },
    "HFC": {
        "ok": True,
        "codigo": "MESA_CONFIRMACION_HFC_INFO",
        "estado": "ESPERANDO_TIPO",
        "resultado_estado": "RESULTADO_CONFIRMACION_HFC",
        "respuesta": (
            "Para realizar la Confirmación HFC, "
            "utilice la opción 1-1-4 en Claro Te Ayuda."
        ),
        "imagen_url": IMAGE_URL,
        "tipo_respuesta": "imagen_informativa",
        "volver_menu": True,
        "pide_wo": False,
        "_api": {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "port": SERVICE_PORT,
        },
    },
}

# El JSON casi nunca cambia y la imagen es el recurso mas pesado. Un cache corto
# para JSON y uno mas largo para la imagen evita transferencias repetidas.
JSON_CACHE_HEADERS = {
    "Cache-Control": "public, max-age=300",
}
IMAGE_CACHE_HEADERS = {
    "Cache-Control": "public, max-age=86400, stale-while-revalidate=3600",
}
HEALTH_HEADERS = {
    "Cache-Control": "no-store",
}

app = FastAPI(
    title="ATLAS - API Confirmaciones FTTH/HFC",
    version=SERVICE_VERSION,
)


@app.get("/health", response_class=JSONResponse)
async def health() -> JSONResponse:
    return JSONResponse(
        content={
            "ok": True,
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "port": SERVICE_PORT,
            "image_exists": IMAGE_PATH.is_file(),
            "image_name": IMAGE_NAME,
        },
        headers=HEALTH_HEADERS,
    )


@app.get("/api/confirmaciones/ftth", response_class=JSONResponse)
async def confirmacion_ftth() -> JSONResponse:
    return JSONResponse(
        content=CONFIRMATIONS["FTTH"],
        headers=JSON_CACHE_HEADERS,
    )


@app.get("/api/confirmaciones/hfc", response_class=JSONResponse)
async def confirmacion_hfc() -> JSONResponse:
    return JSONResponse(
        content=CONFIRMATIONS["HFC"],
        headers=JSON_CACHE_HEADERS,
    )


@app.get("/api/confirmaciones/imagen", response_class=FileResponse)
async def confirmacion_imagen() -> FileResponse:
    if not IMAGE_PATH.is_file():
        raise HTTPException(
            status_code=404,
            detail="Imagen de confirmacion no encontrada.",
        )

    return FileResponse(
        path=IMAGE_PATH,
        media_type="image/jpeg",
        filename=IMAGE_NAME,
        content_disposition_type="inline",
        headers=IMAGE_CACHE_HEADERS,
    )
