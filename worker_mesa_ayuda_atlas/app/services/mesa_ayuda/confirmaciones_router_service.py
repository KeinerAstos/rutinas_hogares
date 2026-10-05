from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.config.endpoints_settings import (
    get_confirmaciones_api_timeout,
    get_confirmaciones_api_url,
)


def _error_payload(
    *,
    tipo: str,
    codigo: str,
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "codigo": codigo,
        "respuesta": error,
        "tipo": tipo,
        "_router": {
            "transport": "HTTP",
            "service": "api_confirmaciones",
            "external": True,
        },
    }


# ATLAS_CONFIRMACIONES_EXTERNAL_API_V1
def obtener_confirmacion(tipo: str) -> dict[str, Any]:

    tipo = str(tipo or "").strip().lower()

    if tipo not in {"ftth", "hfc"}:
        return _error_payload(
            tipo=tipo,
            codigo="CONFIRMACIONES_TIPO_INVALIDO",
            error="Tipo de confirmacion no soportado.",
        )

    url = (
        get_confirmaciones_api_url()
        + "/api/confirmaciones/"
        + tipo
    )

    try:
        with urlopen(
            url,
            timeout=get_confirmaciones_api_timeout(),
        ) as response:
            raw = response.read()

    except HTTPError as exc:
        return _error_payload(
            tipo=tipo,
            codigo="CONFIRMACIONES_API_HTTP_ERROR",
            error=f"HTTP {exc.code}",
        )

    except (URLError, TimeoutError, socket.timeout) as exc:
        return _error_payload(
            tipo=tipo,
            codigo="CONFIRMACIONES_API_NO_DISPONIBLE",
            error=f"{type(exc).__name__}: {exc}",
        )

    except Exception as exc:
        return _error_payload(
            tipo=tipo,
            codigo="CONFIRMACIONES_API_ERROR",
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        payload = json.loads(
            raw.decode(
                "utf-8",
                errors="strict",
            )
        )
    except Exception as exc:
        return _error_payload(
            tipo=tipo,
            codigo="CONFIRMACIONES_API_RESPUESTA_INVALIDA",
            error=f"{type(exc).__name__}: {exc}",
        )

    if not isinstance(payload, dict):
        return _error_payload(
            tipo=tipo,
            codigo="CONFIRMACIONES_API_RESPUESTA_INVALIDA",
            error="Respuesta no reconocida.",
        )

    if payload.get("imagen_url") == "/api/confirmaciones/imagen":
        payload["imagen_url"] = (
            "/api/deco/confirmaciones/imagen"
        )

    payload["_router"] = {
        "transport": "HTTP",
        "service": "api_confirmaciones",
        "external": True,
    }

    return payload