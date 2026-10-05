from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent

load_dotenv(ROOT / ".env")

from pathtrak_engine import capturar_spectrum
from pathtrak_response import construir_respuesta_pathtrak


SERVICE = "api_captures_pathtrak"
VERSION = "1.0.0"
PORT = 8024

SCREENSHOT_DIR = Path(r"C:\xampp\htdocs\CentralNOC\modules\Dashboard_Hogar\backend\data\pathtrak")
_CAPTURE_LOCK = threading.Lock()

SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class CaptureRequest(BaseModel):
    nodo: str = Field(min_length=1, max_length=80)
    tipo_captura: str = Field(default="qoe", max_length=20)


app = FastAPI(
    title="ATLAS - API Captures PathTrak",
    version=VERSION,
)


def _credentials_configured() -> bool:
    return bool(
        str(os.getenv("PATHTRAK_USER") or "").strip()
        and str(os.getenv("PATHTRAK_PASSWORD") or "").strip()
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": SERVICE,
        "version": VERSION,
        "port": PORT,
        "credentials_configured": _credentials_configured(),
        "screenshots_dir": str(SCREENSHOT_DIR),
        "engine_loaded": callable(capturar_spectrum),
    }


@app.post("/api/pathtrak/capturar")
def capturar(request: CaptureRequest) -> dict[str, Any]:

    nodo = str(request.nodo or "").strip().upper()
    tipo = str(request.tipo_captura or "qoe").strip().lower()

    if not nodo:
        raise HTTPException(
            status_code=422,
            detail="El nodo es obligatorio.",
        )

    aliases = {
        "ruido": "ondas",
        "ambas": "qoe_ruido",
        "ambos": "qoe_ruido",
        "qoe+ruido": "qoe_ruido",
        "qoe-y-ruido": "qoe_ruido",
        "qoe_ondas": "qoe_ruido",
    }
    tipo = aliases.get(tipo, tipo)

    if tipo not in {"qoe", "ondas", "qoe_ruido"}:
        raise HTTPException(
            status_code=422,
            detail="tipo_captura debe ser qoe, ondas o qoe_ruido.",
        )

    started = time.perf_counter()

    try:
        # Playwright sÃ­ncrono y PathTrak se ejecutan secuencialmente.
        with _CAPTURE_LOCK:
            raw = capturar_spectrum(nodo, tipo_captura=tipo)
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "PATHTRAK_API_ENGINE_EXCEPTION",
            "error": f"{type(exc).__name__}: {exc}",
            "nodo": nodo,
            "tipo_captura": tipo,
            "_api": {
                "service": SERVICE,
                "version": VERSION,
                "port": PORT,
                "duracion_api_seg": round(
                    time.perf_counter() - started,
                    3,
                ),
            },
        }

    result = construir_respuesta_pathtrak(raw)

    if not isinstance(result, dict):
        result = {
            "ok": False,
            "codigo": "PATHTRAK_API_INVALID_RESPONSE",
            "error": "El motor PathTrak no devolvio un diccionario.",
        }

    # PATHTRAK_EXTERNAL_ERROR_CODE_V1
    # Garantiza un codigo operativo incluso cuando el motor heredado
    # devuelve ok=False sin clasificacion explicita.
    if result.get("ok") is False and not str(result.get("codigo") or "").strip():
        error_text = str(result.get("error") or "").strip()

        if "No se pudo capturar en Centro ni Regionales" in error_text:
            result["codigo"] = "PATHTRAK_CAPTURA_NO_DISPONIBLE"
        else:
            result["codigo"] = "PATHTRAK_CAPTURA_ERROR"

    result.setdefault("nodo", nodo)
    result.setdefault("tipo_captura", tipo)

    result["_api"] = {
        "service": SERVICE,
        "version": VERSION,
        "port": PORT,
        "duracion_api_seg": round(
            time.perf_counter() - started,
            3,
        ),
    }

    return result


@app.get("/api/pathtrak/screenshots/{filename}")
def screenshot(filename: str):

    safe_name = Path(filename).name

    if safe_name != filename:
        raise HTTPException(
            status_code=400,
            detail="Nombre de archivo invalido.",
        )

    file_path = SCREENSHOT_DIR / safe_name

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Screenshot no encontrado.",
        )

    return FileResponse(file_path)

