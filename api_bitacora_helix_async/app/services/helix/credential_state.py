from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[3]
STATE_DIR = Path(r"C:\xampp\htdocs\rutinas_hogares\api_direccion_clientes\runtime\helix\data")
STATE_FILE = STATE_DIR / "credential_alert.json"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _mask_user(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 3:
        return "*" * len(text)
    return text[:2] + ("*" * min(8, max(1, len(text) - 3))) + text[-1:]


def _default() -> dict[str, Any]:
    return {
        "active": False,
        "code": "",
        "message": "",
        "detected_at": "",
        "user": "",
    }


def get_credentials_alert() -> dict[str, Any]:
    with _LOCK:
        if not STATE_FILE.exists():
            return _default()

        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return _default()

        result = _default()
        if isinstance(payload, dict):
            result.update({
                "active": bool(payload.get("active")),
                "code": str(payload.get("code") or ""),
                "message": str(payload.get("message") or ""),
                "detected_at": str(payload.get("detected_at") or ""),
                "user": str(payload.get("user") or ""),
            })
        return result


def mark_credentials_invalid(
    username: str | None,
    detail: str = "SmartIT rechazo el usuario o la contraseña.",
) -> dict[str, Any]:
    payload = {
        "active": True,
        "code": "HELIX_CREDENCIALES_INVALIDAS",
        "message": (
            "Helix rechazo las credenciales configuradas. "
            "Se requiere actualizar SMARTIT_USER / SMARTIT_PASSWORD."
        ),
        "detected_at": _now(),
        "user": _mask_user(username),
        "detail": str(detail or ""),
    }

    with _LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(STATE_FILE)

    return get_credentials_alert()


def clear_credentials_alert() -> None:
    with _LOCK:
        try:
            STATE_FILE.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            # Nunca se debe romper una consulta Helix solo por no poder limpiar la alerta.
            pass