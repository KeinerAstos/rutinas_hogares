from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.endpoints_settings import (
    get_dispositivos_api_timeout,
    get_dispositivos_api_url,
)


SERVICE_NAME = "api_dispositivos_vips"


class DispositivosClientError(RuntimeError):
    def __init__(
        self,
        codigo: str,
        mensaje: str,
        *,
        status_code: int = 502,
        error: str = "",
    ) -> None:
        super().__init__(mensaje)

        self.codigo = codigo
        self.mensaje = mensaje
        self.status_code = int(status_code)
        self.error = error


def _request_json(
    method: str,
    path: str,
) -> dict[str, Any]:

    url = get_dispositivos_api_url() + path
    timeout = get_dispositivos_api_timeout()

    request = Request(
        url=url,
        headers={
            "Accept": "application/json",
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

        raise DispositivosClientError(
            "DISPOSITIVOS_API_HTTP_ERROR",
            "La API externa de Dispositivos respondio con error HTTP.",
            status_code=int(exc.code),
            error=detail or str(exc),
        ) from exc

    except (URLError, TimeoutError, socket.timeout) as exc:

        raise DispositivosClientError(
            "DISPOSITIVOS_API_NO_DISPONIBLE",
            "No fue posible comunicarse con la API externa de Dispositivos.",
            status_code=502,
            error=f"{type(exc).__name__}: {exc}",
        ) from exc

    except Exception as exc:

        raise DispositivosClientError(
            "DISPOSITIVOS_API_ERROR",
            "Error comunicandose con la API externa de Dispositivos.",
            status_code=502,
            error=f"{type(exc).__name__}: {exc}",
        ) from exc

    try:
        payload = json.loads(
            raw.decode(
                "utf-8",
                errors="strict",
            )
        )

    except Exception as exc:

        raise DispositivosClientError(
            "DISPOSITIVOS_API_RESPUESTA_INVALIDA",
            "La API externa de Dispositivos devolvio JSON invalido.",
            status_code=502,
            error=f"{type(exc).__name__}: {exc}",
        ) from exc

    if not isinstance(payload, dict):

        raise DispositivosClientError(
            "DISPOSITIVOS_API_RESPUESTA_INVALIDA",
            "La API externa de Dispositivos devolvio una respuesta no reconocida.",
            status_code=502,
        )

    return payload


def health() -> dict[str, Any]:
    return _request_json(
        "GET",
        "/health",
    )


def listar() -> dict[str, Any]:
    return _request_json(
        "GET",
        "/api/vips",
    )


def resumen() -> dict[str, Any]:
    return _request_json(
        "GET",
        "/api/vips/resumen",
    )


def actualizar() -> dict[str, Any]:
    return _request_json(
        "POST",
        "/api/vips/actualizar",
    )


def estado_actualizacion(
    job_id: str,
) -> dict[str, Any]:

    key = str(job_id or "").strip()

    if not key:
        raise DispositivosClientError(
            "DISPOSITIVOS_JOB_INVALIDO",
            "job_id es obligatorio.",
            status_code=400,
        )

    return _request_json(
        "GET",
        "/api/vips/actualizar/" + key,
    )