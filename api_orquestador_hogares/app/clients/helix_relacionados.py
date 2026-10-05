from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.endpoints_settings import (
    get_helix_relacionados_api_timeout,
    get_helix_relacionados_api_url,
)


# ATLAS_HELIX_RELACIONADOS_EXTERNAL_API_V2
def _request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_override: float | None = None,
) -> dict[str, Any]:
    base = get_helix_relacionados_api_url()

    # ATLAS_HELIX_INDIVIDUAL_TIMEOUT_V1
    timeout = (
        float(timeout_override)
        if timeout_override is not None
        else get_helix_relacionados_api_timeout()
    )

    body = None

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    request = Request(
        base + path,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
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
            pass

        return {
            "ok": False,
            "codigo": "HELIX_RELACIONADOS_API_HTTP_ERROR",
            "respuesta": (
                "La API externa de Helix Relacionados "
                "respondio con error HTTP."
            ),
            "error": (
                f"HTTP {exc.code}"
                + (f": {detail}" if detail else "")
            ),
        }

    except (URLError, TimeoutError, socket.timeout) as exc:
        return {
            "ok": False,
            "codigo": "HELIX_RELACIONADOS_API_NO_DISPONIBLE",
            "respuesta": (
                "No fue posible comunicarse con "
                "la API externa de Helix Relacionados."
            ),
            "error": f"{type(exc).__name__}: {exc}",
        }

    except Exception as exc:
        return {
            "ok": False,
            "codigo": "HELIX_RELACIONADOS_API_ERROR",
            "respuesta": (
                "Ocurrio un error comunicandose con "
                "la API externa de Helix Relacionados."
            ),
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        result = json.loads(
            raw.decode(
                "utf-8",
                errors="strict",
            )
        )
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "HELIX_RELACIONADOS_API_RESPUESTA_INVALIDA",
            "respuesta": (
                "La API externa devolvio JSON invalido."
            ),
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not isinstance(result, dict):
        return {
            "ok": False,
            "codigo": "HELIX_RELACIONADOS_API_RESPUESTA_INVALIDA",
            "respuesta": (
                "La API externa devolvio una respuesta "
                "no reconocida."
            ),
            "error": "",
        }

    return result


def iniciar_job(
    incidentes: list[str],
    workers: int = 7,
    area: str = "front",
) -> dict[str, Any]:
    return _request_json(
        "POST",
        "/api/deco/helix/incidentes/relacionados/jobs",
        {
            "incidentes": incidentes,
            "workers": workers,
            "area": area,
        },
    )


def obtener_job(
    job_id: str,
) -> dict[str, Any]:
    key = str(job_id or "").strip()

    return _request_json(
        "GET",
        (
            "/api/deco/helix/incidentes/relacionados/jobs/"
            + key
        ),
    )


# ATLAS_HELIX_RELACIONADOS_INDIVIDUAL_CLIENT_V1
def consultar_incidente(
    inc: str,
) -> dict[str, Any]:

    ticket = str(
        inc or ""
    ).strip().upper()

    if not ticket:
        return {
            "ok": False,
            "tipo_respuesta": "helix_inc_relacionados",
            "codigo": "HELIX_INC_INVALIDO",
            "origen": "HELIX_RELACIONADOS_API",
            "incidente": "",
            "respuesta": "INC invalido.",
            "error": "",
        }

    return _request_json(
        "GET",
        (
            "/api/deco/helix/incidentes/"
            "relacionados/individual/"
            + ticket
        ),
        timeout_override=620.0,
    )

# ATLAS_HELIX_WO_SUMMARY_EXTERNAL_CLIENT_V1
def consultar_resumen_ot(
    ot: str,
) -> dict[str, Any]:
    key = str(
        ot or ""
    ).strip().upper()

    return _request_json(
        "GET",
        (
            "/api/deco/helix/ot/"
            + key
            + "/resumen"
        ),
        timeout_override=620.0,
    )

