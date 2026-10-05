from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

BASE_DIR = Path(__file__).resolve().parent
IMAGE_PATH = BASE_DIR / "assets" / "ot_fibra_coaxial_app_conecta.png"
IMAGE_URL = "/api/ot/informacion/imagen"
VERSION = "3.0.0"

app = FastAPI(title="ATLAS - Informacion OT Fibra / Coaxial", version=VERSION)


def _information(tipo: str) -> dict[str, Any]:
    return {
        "ok": True,
        "codigo": f"MESA_GENERACION_OT_{tipo}_INFO",
        "tipo_respuesta": "imagen_informativa",
        "tipo": tipo,
        "estado": "ESPERANDO_TIPO",
        "respuesta": (
            "Para generar órdenes de Trabajo de Fibra y Coaxial, "
            "realice la gestión en la APP CONECTA."
        ),
        "imagen_url": IMAGE_URL,
        "pide_wo": False,
        "pide_credenciales": False,
        "volver_menu": True,
    }


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "api_ot_fibra_coaxial",
        "version": VERSION,
        "port": 8027,
        "modo": "INFORMATIVO",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": IMAGE_PATH.is_file(),
        "service": "api_ot_fibra_coaxial",
        "version": VERSION,
        "port": 8027,
        "modo": "INFORMATIVO",
        "image_exists": IMAGE_PATH.is_file(),
    }


@app.get("/api/ot/informacion/fibra")
def informacion_fibra() -> dict[str, Any]:
    return _information("FIBRA")


@app.get("/api/ot/informacion/coaxial")
def informacion_coaxial() -> dict[str, Any]:
    return _information("COAXIAL")


@app.get("/api/ot/informacion/imagen")
def imagen() -> FileResponse:
    if not IMAGE_PATH.is_file():
        raise HTTPException(status_code=404, detail="IMAGEN_NO_DISPONIBLE")
    return FileResponse(
        IMAGE_PATH,
        media_type="image/png",
        filename=IMAGE_PATH.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# Estos contratos siguen respondiendo a consumidores antiguos, pero ya no
# ejecutan Helix ni pueden crear una OT. Se retiran cuando se migren todos.
def _retired() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={
            "ok": False,
            "codigo": "OT_AUTOMATIZACION_RETIRADA",
            "respuesta": "La generación de órdenes se realiza en APP CONECTA.",
            "imagen_url": IMAGE_URL,
            "pide_wo": False,
        },
    )


@app.post("/dry-run")
async def dry_run() -> JSONResponse:
    return _retired()


@app.post("/commit")
async def commit() -> JSONResponse:
    return _retired()


@app.post("/cancel")
async def cancel() -> JSONResponse:
    return _retired()
