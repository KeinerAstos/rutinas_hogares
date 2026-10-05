from __future__ import annotations

import os
import time
import uuid
import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# La configuración se carga ANTES de importar servicios ATLAS.
load_dotenv(ROOT / ".env", override=True)

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.services.direcciones.router import (
    consultar_direcciones_por_wo,
)
from app.services.direcciones.response_contract import (
    construir_respuesta_multicanal,
)


SERVICE_NAME = "api_direccion_clientes"
SERVICE_VERSION = "1.0.0"


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(SERVICE_NAME)


app = FastAPI(
    title="ATLAS - Dirección de Clientes",
    description="Microservicio independiente para consultas de Dirección de Clientes.",
    version=SERVICE_VERSION,
)


class DireccionRequest(BaseModel):
    wo: str = Field(
        ...,
        min_length=1,
        description="Work Order a consultar",
    )


def _normalizar_wo(value: str) -> str:
    return str(value or "").strip().upper()


@app.get("/health")
def health() -> dict[str, Any]:

    return {
        "ok": True,
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "status": "UP",
        "port": 8021,
    }


@app.get("/ready")
def ready() -> dict[str, Any]:

    callable_ok = callable(consultar_direcciones_por_wo)

    return {
        "ok": callable_ok,
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "router_callable": callable_ok,
    }



# ATLAS_DIRECCIONES_CONCURRENCY_V1
import asyncio as _atlas_direcciones_asyncio
import os as _atlas_direcciones_os

_DIRECCIONES_MAX_CONCURRENT = max(
    1,
    int(
        _atlas_direcciones_os.getenv(
            "DIRECCIONES_MAX_CONCURRENT",
            "3",
        )
        or "3"
    ),
)

_DIRECCIONES_REQUEST_SEMAPHORE = (
    _atlas_direcciones_asyncio.Semaphore(
        _DIRECCIONES_MAX_CONCURRENT
    )
)


@app.post("/api/v1/direcciones/consultar")
async def consultar_direccion(
    request: DireccionRequest,
) -> dict[str, Any]:

    request_id = uuid.uuid4().hex

    wo = _normalizar_wo(request.wo)

    if not wo:
        raise HTTPException(
            status_code=400,
            detail="WO vacía.",
        )

    # Evitar que una entrada absurda llegue a Helix.
    if not wo.startswith("WO"):
        raise HTTPException(
            status_code=400,
            detail="El identificador debe comenzar por WO.",
        )

    inicio = time.perf_counter()

    logger.info(
        "request_id=%s wo=%s inicio_consulta",
        request_id,
        wo,
    )

    try:

        # La implementación heredada es síncrona.
        # run_in_threadpool evita bloquear el event loop de FastAPI.
        # ATLAS_DIRECCIONES_CONCURRENCY_V1_SLOT
        slot_started = time.perf_counter()

        await _DIRECCIONES_REQUEST_SEMAPHORE.acquire()

        slot_wait = round(
            time.perf_counter() - slot_started,
            3,
        )

        logger.info(
            (
                "request_id=%s wo=%s "
                "concurrency_slot_acquired "
                "wait=%s max=%s"
            ),
            request_id,
            wo,
            slot_wait,
            _DIRECCIONES_MAX_CONCURRENT,
        )

        try:
            resultado = await run_in_threadpool(
                consultar_direcciones_por_wo,
                wo,
            )
        finally:
            _DIRECCIONES_REQUEST_SEMAPHORE.release()

        elapsed = round(
            time.perf_counter() - inicio,
            3,
        )

        logger.info(
            "request_id=%s wo=%s completado elapsed=%s",
            request_id,
            wo,
            elapsed,
        )

        return {
            "ok": True,
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "request_id": request_id,
            "wo": wo,
            "elapsed_seconds": elapsed,
            "data": resultado,
            "canales": construir_respuesta_multicanal(
                wo,
                resultado,
            ),
        }

    except Exception as exc:

        elapsed = round(
            time.perf_counter() - inicio,
            3,
        )

        logger.exception(
            "request_id=%s wo=%s error elapsed=%s",
            request_id,
            wo,
            elapsed,
        )

        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "service": SERVICE_NAME,
                "request_id": request_id,
                "wo": wo,
                "elapsed_seconds": elapsed,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        ) from exc
