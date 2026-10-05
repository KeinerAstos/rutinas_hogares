from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.clients.dispositivos import (
    DispositivosClientError,
    actualizar,
    estado_actualizacion,
    listar,
    resumen,
)


router = APIRouter()


def _raise_client_error(
    exc: DispositivosClientError,
) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={
            "ok": False,
            "codigo": exc.codigo,
            "respuesta": exc.mensaje,
            "error": exc.error,
            "_router": {
                "transport": "HTTP",
                "service": "api_dispositivos_vips",
                "external": True,
            },
        },
    ) from exc


@router.get("")
@router.get("/")
def dispositivos() -> dict:
    try:
        payload = listar()
    except DispositivosClientError as exc:
        _raise_client_error(exc)

    payload.setdefault(
        "_router",
        {
            "transport": "HTTP",
            "service": "api_dispositivos_vips",
            "external": True,
        },
    )

    return payload


@router.get("/resumen")
def dispositivos_resumen() -> dict:
    try:
        payload = resumen()
    except DispositivosClientError as exc:
        _raise_client_error(exc)

    payload.setdefault(
        "_router",
        {
            "transport": "HTTP",
            "service": "api_dispositivos_vips",
            "external": True,
        },
    )

    return payload


@router.post("/actualizar", status_code=202)
def dispositivos_actualizar() -> dict:
    try:
        payload = actualizar()
    except DispositivosClientError as exc:
        _raise_client_error(exc)

    payload.setdefault(
        "_router",
        {
            "transport": "HTTP",
            "service": "api_dispositivos_vips",
            "external": True,
        },
    )

    return payload


@router.get("/actualizar/{job_id}")
def dispositivos_actualizar_estado(
    job_id: str,
) -> dict:
    try:
        payload = estado_actualizacion(job_id)
    except DispositivosClientError as exc:
        _raise_client_error(exc)

    payload.setdefault(
        "_router",
        {
            "transport": "HTTP",
            "service": "api_dispositivos_vips",
            "external": True,
        },
    )

    return payload
