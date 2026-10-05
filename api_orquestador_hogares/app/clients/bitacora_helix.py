"""Cliente HTTP de la fachada CentralNOC hacia API Bitacora Helix 8025."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from app.config.endpoints_settings import (
    get_bitacora_helix_api_timeout,
    get_bitacora_helix_api_url,
)


class BitacoraHelixGatewayError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        detail: Any,
    ) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _call(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:

    base = get_bitacora_helix_api_url()
    timeout = get_bitacora_helix_api_timeout()

    url = base + path

    data = None
    headers = {
        "Accept": "application/json",
    }

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(
        url=url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with request.urlopen(
            req,
            timeout=timeout,
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace",
            )

            body = json.loads(raw) if raw else {}

            return int(response.status), body

    except error.HTTPError as exc:

        raw = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        try:
            detail = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            detail = raw or str(exc)

        raise BitacoraHelixGatewayError(
            int(exc.code),
            detail,
        ) from exc

    except error.URLError as exc:
        raise BitacoraHelixGatewayError(
            502,
            {
                "detail": "BITACORA_HELIX_ENGINE_UNAVAILABLE",
                "error": str(exc.reason),
            },
        ) from exc

    except TimeoutError as exc:
        raise BitacoraHelixGatewayError(
            504,
            {
                "detail": "BITACORA_HELIX_ENGINE_TIMEOUT",
            },
        ) from exc


def create_job(
    *,
    incident: str,
    incident_id: int,
) -> tuple[int, Any]:

    return _call(
        "POST",
        "/jobs",
        {
            "incident": incident,
            "incident_id": incident_id,
        },
    )


def get_job(job_id: str) -> tuple[int, Any]:
    return _call(
        "GET",
        f"/jobs/{job_id}",
    )


def get_queue_status() -> tuple[int, Any]:
    return _call(
        "GET",
        "/queue/status",
    )


def get_worker_status() -> tuple[int, Any]:
    return _call(
        "GET",
        "/worker/status",
    )