# -*- coding: utf-8 -*-
"""Gateway interno Mesa de Ayuda -> API OT relacionada 8027."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.endpoints_settings import (
    get_ot_relacionada_api_timeout,
    get_ot_relacionada_api_url,
)


# ATLAS_OT_DYNAMIC_CREDENTIALS_GATEWAY_V2
def consultar_ot_relacionada_dryrun(
    wo: str,
    tipo: str,
    *,
    helix_username: str = "",
    helix_password: str = "",
) -> dict[str, Any]:
    base_url = get_ot_relacionada_api_url()
    timeout = get_ot_relacionada_api_timeout()

    payload = {
        "wo": str(wo or "").strip().upper(),
        "tipo": str(tipo or "").strip().upper(),
        "helix_username": str(helix_username or "").strip(),
        "helix_password": str(helix_password or ""),
    }

    request = Request(
        f"{base_url}/dry-run",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")

        result = json.loads(raw)

        if not isinstance(result, dict):
            raise RuntimeError("OT_8027_RESPUESTA_INVALIDA")

        return result

    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass

        return {
            "ok": False,
            "codigo": f"OT_8027_HTTP_{exc.code}",
            "error": body or str(exc),
            "allow_save_click": False,
            "reportar_como_creada": False,
        }

    except URLError as exc:
        return {
            "ok": False,
            "codigo": "OT_8027_NO_DISPONIBLE",
            "error": str(exc.reason),
            "allow_save_click": False,
            "reportar_como_creada": False,
        }

    except Exception as exc:
        return {
            "ok": False,
            "codigo": "OT_8027_GATEWAY_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "allow_save_click": False,
            "reportar_como_creada": False,
        }


def confirmar_ot_relacionada(
    wo: str,
    tipo: str,
    *,
    expected_incident: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    base_url = get_ot_relacionada_api_url()
    timeout = get_ot_relacionada_api_timeout()

    payload = {
        "wo": str(wo or "").strip().upper(),
        "tipo": str(tipo or "").strip().upper(),
        "expected_incident": str(expected_incident or "").strip().upper(),
        "session_id": str(session_id or "").strip(),
    }

    request = Request(
        f"{base_url}/commit",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("OT_8027_RESPUESTA_INVALIDA")
        return result
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return {
            "ok": False,
            "codigo": f"OT_8027_HTTP_{exc.code}",
            "error": body or str(exc),
            "save_clicked": False,
            "save_confirmed": False,
        }
    except URLError as exc:
        return {
            "ok": False,
            "codigo": "OT_8027_NO_DISPONIBLE",
            "error": str(exc.reason),
            "save_clicked": False,
            "save_confirmed": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "OT_8027_GATEWAY_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "save_clicked": False,
            "save_confirmed": False,
        }


def cancelar_ot_relacionada(
    *,
    wo: str = "",
    tipo: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    base_url = get_ot_relacionada_api_url()
    timeout = get_ot_relacionada_api_timeout()

    payload = {
        "wo": str(wo or "").strip().upper(),
        "tipo": str(tipo or "").strip().upper(),
        "session_id": str(session_id or "").strip(),
    }

    request = Request(
        f"{base_url}/cancel",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("OT_8027_RESPUESTA_INVALIDA")
        return result
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "OT_8027_CANCEL_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }


def estado_ot_relacionada_api() -> dict[str, Any]:
    base_url = get_ot_relacionada_api_url()
    timeout = get_ot_relacionada_api_timeout()

    request = Request(
        f"{base_url}/health",
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("OT_8027_RESPUESTA_INVALIDA")
        return result
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "OT_8027_HEALTH_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }


def creacion_ot_habilitada() -> bool:
    health = estado_ot_relacionada_api()
    return bool(
        health.get("ok") is True
        and health.get("commit_enabled") is True
        and health.get("live_session_available") is True
    )
