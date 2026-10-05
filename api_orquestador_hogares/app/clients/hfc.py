"""Cliente HFC 8030 para el orquestador 8011."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from app.config.endpoints_settings import (
    get_hfc_api_base_url,
    get_hfc_api_timeout,
)


def _request(path: str) -> Dict[str, Any]:
    url = get_hfc_api_base_url() + path

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "atlas-orquestador-hfc/1.0",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=get_hfc_api_timeout(),
        ) as response:

            data = json.loads(
                response.read().decode(
                    "utf-8",
                    errors="replace",
                )
            )

            if not isinstance(data, dict):
                return {
                    "ok": False,
                    "codigo": "HFC_8030_BAD_RESPONSE",
                    "error": "Respuesta 8030 inválida.",
                }

            return data

    except urllib.error.HTTPError as exc:

        try:
            data = json.loads(
                exc.read().decode(
                    "utf-8",
                    errors="replace",
                )
            )

            if isinstance(data, dict):
                return data

        except Exception:
            pass

        return {
            "ok": False,
            "codigo": "HFC_8030_HTTP_ERROR",
            "error": f"HTTP {exc.code}",
        }

    except Exception as exc:
        return {
            "ok": False,
            "codigo": "HFC_8030_UNAVAILABLE",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _q(value: str) -> str:
    return urllib.parse.quote(
        str(value or "").strip(),
        safe="",
    )


def estado_nodo(nodo: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/estado/{_q(nodo)}")


def modems_nodo(nodo: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/modems/{_q(nodo)}")


def estado_cmts(cmts: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/cmts/estado/{_q(cmts)}")


def nodos_cmts(cmts: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/cmts/nodos/{_q(cmts)}")


def buscar_nodo(nodo: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/buscar/nodo/{_q(nodo)}")


def afectados_cmts(cmts: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/cmts/afectados/{_q(cmts)}")


def nodos_criticos_zona(zona: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/zona/criticos/{_q(zona)}")


def buscar_cmts(texto: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/buscar/cmts/{_q(texto)}")


def historial_nodo(nodo: str) -> Dict[str, Any]:
    return _request(f"/api/deco/hfc/nodo/historial/{_q(nodo)}")
