from __future__ import annotations

import json
import socket
from pathlib import PurePath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.endpoints_settings import (
    get_pathtrak_captures_api_timeout,
    get_pathtrak_captures_api_url,
)


def _error_payload(
    *,
    nodo: str,
    tipo_captura: str,
    codigo: str,
    error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "tipo_respuesta": "pathtrak_captura",
        "codigo": codigo,
        "error": error,
        "nodo": nodo,
        "tipo_captura": tipo_captura,
        "_router": {
            "transport": "HTTP",
            "service": "api_captures_pathtrak",
            "external": True,
        },
    }


# ATLAS_PATHTRAK_EXTERNAL_API_V1
def capturar_pathtrak(
    nodo: str,
    tipo_captura: str = "qoe",
) -> dict[str, Any]:

    nodo = str(nodo or "").strip().upper()
    tipo_captura = str(
        tipo_captura or "qoe"
    ).strip().lower()

    base_url = get_pathtrak_captures_api_url()
    timeout = get_pathtrak_captures_api_timeout()

    url = base_url + "/api/pathtrak/capturar"

    body = json.dumps(
        {
            "nodo": nodo,
            "tipo_captura": tipo_captura,
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
            pass

        return _error_payload(
            nodo=nodo,
            tipo_captura=tipo_captura,
            codigo="PATHTRAK_API_HTTP_ERROR",
            error=(
                f"HTTP {exc.code}"
                + (f": {detail}" if detail else "")
            ),
        )

    except (URLError, TimeoutError, socket.timeout) as exc:

        return _error_payload(
            nodo=nodo,
            tipo_captura=tipo_captura,
            codigo="PATHTRAK_API_NO_DISPONIBLE",
            error=f"{type(exc).__name__}: {exc}",
        )

    except Exception as exc:

        return _error_payload(
            nodo=nodo,
            tipo_captura=tipo_captura,
            codigo="PATHTRAK_API_ERROR",
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
            nodo=nodo,
            tipo_captura=tipo_captura,
            codigo="PATHTRAK_API_RESPUESTA_INVALIDA",
            error=f"{type(exc).__name__}: {exc}",
        )

    if not isinstance(payload, dict):

        return _error_payload(
            nodo=nodo,
            tipo_captura=tipo_captura,
            codigo="PATHTRAK_API_RESPUESTA_INVALIDA",
            error="La API PathTrak devolvio una respuesta no reconocida.",
        )

    respuesta = payload.get("respuesta")

    def _proxy_public_url(valor):
        public_url = str(valor or "").strip()

        prefix = "/api/pathtrak/screenshots/"

        if not public_url.startswith(prefix):
            return public_url

        filename = PurePath(
            public_url[len(prefix):]
        ).name

        if not filename:
            return public_url

        return (
            "/api/deco/screenshots/pathtrak/"
            + filename
        )

    if isinstance(respuesta, dict):

        # Captura individual.
        if respuesta.get("public_url"):
            respuesta["public_url"] = _proxy_public_url(
                respuesta.get("public_url")
            )

        # Captura combinada qoe_ruido.
        for campo in ("capturas", "evidencias"):
            items = respuesta.get(campo)

            if isinstance(items, list):
                for item in items:
                    if (
                        isinstance(item, dict)
                        and item.get("public_url")
                    ):
                        item["public_url"] = _proxy_public_url(
                            item.get("public_url")
                        )

        # Lista resumida de URLs.
        public_urls = respuesta.get("public_urls")

        if isinstance(public_urls, list):
            respuesta["public_urls"] = [
                _proxy_public_url(url)
                for url in public_urls
                if url
            ]

    payload["_router"] = {
        "transport": "HTTP",
        "service": "api_captures_pathtrak",
        "external": True,
    }

    return payload