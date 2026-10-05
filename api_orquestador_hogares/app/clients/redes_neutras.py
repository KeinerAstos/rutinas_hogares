from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.endpoints_settings import (
    get_redes_neutras_api_timeout,
    get_redes_neutras_api_url,
)


# ATLAS_REDES_NEUTRAS_EXTERNAL_API_V1
def _error_payload(
    *,
    wo: str,
    codigo: str,
    mensaje: str,
    error: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "tipo_respuesta": "helix_redes_neutras",
        "codigo": codigo,
        "origen": "REDES_NEUTRAS_API",
        "respuesta": mensaje,
        "ot": wo,
        "ot_encontrada": False,
        "incidente": "",
        "incidentes_relacionados": [],
        "documentos_adjuntos_encontrados": False,
        "cantidad_documentos_adjuntos": 0,
        "requiere_seleccion": False,
        "adjuntos": [],
        "archivos": [],
        "duracion_seg": 0,
        "error": error,
    }


# ATLAS_REDES_NEUTRAS_EXTERNAL_API_V1
def consultar_redes_neutras_por_wo(
    wo: str,
) -> dict[str, Any]:
    value = str(wo or "").strip().upper()

    base_url = get_redes_neutras_api_url()
    timeout = get_redes_neutras_api_timeout()

    url = (
        base_url
        + "/api/redes-neutras/consultar"
    )

    body = json.dumps(
        {
            "wo": value,
        }
    ).encode("utf-8")

    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read()

    except HTTPError as exc:
        detail = ""

        try:
            detail = exc.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            detail = ""

        return _error_payload(
            wo=value,
            codigo="REDES_NEUTRAS_API_HTTP_ERROR",
            mensaje=(
                "La API externa de Redes Neutras "
                "respondio con error HTTP."
            ),
            error=(
                f"HTTP {exc.code}"
                + (f": {detail}" if detail else "")
            ),
        )

    except (URLError, TimeoutError, socket.timeout) as exc:
        return _error_payload(
            wo=value,
            codigo="REDES_NEUTRAS_API_NO_DISPONIBLE",
            mensaje=(
                "No fue posible comunicarse con "
                "la API externa de Redes Neutras."
            ),
            error=f"{type(exc).__name__}: {exc}",
        )

    except Exception as exc:
        return _error_payload(
            wo=value,
            codigo="REDES_NEUTRAS_API_ERROR",
            mensaje=(
                "Ocurrio un error consultando "
                "la API externa de Redes Neutras."
            ),
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
            wo=value,
            codigo="REDES_NEUTRAS_API_RESPUESTA_INVALIDA",
            mensaje=(
                "La API externa de Redes Neutras "
                "devolvio una respuesta JSON invalida."
            ),
            error=f"{type(exc).__name__}: {exc}",
        )

    if not isinstance(payload, dict):
        return _error_payload(
            wo=value,
            codigo="REDES_NEUTRAS_API_RESPUESTA_INVALIDA",
            mensaje=(
                "La API externa de Redes Neutras "
                "devolvio una respuesta no reconocida."
            ),
        )

    # El contrato de 8022 se propaga sin reinterpretarlo.
    # Esto mantiene compatibilidad con _procesar_redes_neutras().
    payload.setdefault(
        "_router",
        {
            "transport": "HTTP",
            "service": "api_redes_neutras",
            "external": True,
        },
    )

    return payload
