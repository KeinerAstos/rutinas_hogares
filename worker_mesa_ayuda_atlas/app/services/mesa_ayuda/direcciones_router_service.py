from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.endpoints_settings import (
    get_direcciones_api_timeout,
    get_direcciones_api_url,
)


# ATLAS_DIRECCIONES_EXTERNAL_API_V1
def _error_payload(
    *,
    wo: str,
    codigo: str,
    mensaje: str,
    error: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "codigo": codigo,
        "wo": wo,
        "respuesta": mensaje,
        "error": error,
        "router_direcciones": "API_EXTERNA_8021",
        "_router": {
            "transport": "HTTP",
            "service": "api_direccion_clientes",
            "external": True,
        },
        "direcciones": {
            "ok": False,
            "codigo": codigo,
            "clientes_encontrados": 0,
            "clientes": [],
        },
    }


# ATLAS_DIRECCIONES_EXTERNAL_API_V1
def consultar_direcciones_por_wo(
    wo: str,
) -> dict[str, Any]:

    value = str(
        wo or ""
    ).strip().upper()

    base_url = get_direcciones_api_url()
    timeout = get_direcciones_api_timeout()

    url = (
        base_url
        + "/api/v1/direcciones/consultar"
    )

    body = json.dumps(
        {
            "wo": value,
        }
    ).encode(
        "utf-8"
    )

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
            codigo="DIRECCIONES_API_HTTP_ERROR",
            mensaje=(
                "La API externa de Direcciones "
                "respondio con error HTTP."
            ),
            error=(
                f"HTTP {exc.code}"
                + (
                    f": {detail}"
                    if detail
                    else ""
                )
            ),
        )

    except (
        URLError,
        TimeoutError,
        socket.timeout,
    ) as exc:

        return _error_payload(
            wo=value,
            codigo="DIRECCIONES_API_NO_DISPONIBLE",
            mensaje=(
                "No fue posible comunicarse con "
                "la API externa de Direcciones."
            ),
            error=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    except Exception as exc:
        return _error_payload(
            wo=value,
            codigo="DIRECCIONES_API_ERROR",
            mensaje=(
                "Ocurrio un error comunicandose "
                "con la API externa de Direcciones."
            ),
            error=(
                f"{type(exc).__name__}: {exc}"
            ),
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
            codigo="DIRECCIONES_API_RESPUESTA_INVALIDA",
            mensaje=(
                "La API externa de Direcciones "
                "devolvio JSON invalido."
            ),
            error=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    if not isinstance(payload, dict):
        return _error_payload(
            wo=value,
            codigo="DIRECCIONES_API_RESPUESTA_INVALIDA",
            mensaje=(
                "La API externa de Direcciones "
                "devolvio una respuesta no reconocida."
            ),
        )

    # El endpoint 8021 responde con:
    #
    # {
    #     "ok": ...,
    #     "data": { contrato real de Direcciones }
    # }
    #
    # El chatbot historicamente consume directamente
    # el contrato interno, por eso se extrae "data".
    result = payload.get("data")

    # ATLAS_DIRECCIONES_MULTICANAL_V1
    canales = payload.get("canales")

    if not isinstance(result, dict):
        # Compatibilidad defensiva en caso de que 8021
        # en el futuro entregue el contrato directamente.
        if "codigo" in payload:
            result = payload
        else:
            return _error_payload(
                wo=value,
                codigo="DIRECCIONES_API_RESPUESTA_INVALIDA",
                mensaje=(
                    "La API externa de Direcciones "
                    "no entrego el objeto data esperado."
                ),
            )

    result.setdefault(
        "_router",
        {
            "transport": "HTTP",
            "service": "api_direccion_clientes",
            "external": True,
        },
    )

    result.setdefault(
        "router_direcciones",
        "API_EXTERNA_8021",
    )

    # ATLAS_DIRECCIONES_MULTICANAL_V1
    if isinstance(canales, dict):
        result["canales"] = canales

    return result
