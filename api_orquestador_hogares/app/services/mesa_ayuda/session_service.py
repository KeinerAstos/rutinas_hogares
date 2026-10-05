# -*- coding: utf-8 -*-
"""Estado conversacional temporal del simulador Mesa de Ayuda.

La interfaz de almacenamiento está aislada para poder sustituirla por
persistencia MySQL/Redis sin cambiar el motor de conversación.
"""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any

_SESSION_TTL_SECONDS = 30 * 60
_MAX_SESSIONS = 1000

_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}


def _key(canal: str, conversation_id: str) -> str:
    safe_channel = str(canal or "ATLAS").strip().upper() or "ATLAS"
    safe_conversation = str(conversation_id or "").strip()

    if not safe_conversation:
        raise ValueError("conversation_id es obligatorio.")

    return f"{safe_channel}:{safe_conversation}"


def _cleanup_locked() -> None:
    now = time.time()

    expired = [
        key
        for key, value in _sessions.items()
        if now - float(value.get("_updated_ts") or 0) > _SESSION_TTL_SECONDS
    ]

    for key in expired:
        _sessions.pop(key, None)

    if len(_sessions) <= _MAX_SESSIONS:
        return

    ordered = sorted(
        _sessions.items(),
        key=lambda item: float(item[1].get("_updated_ts") or 0),
    )

    for key, _value in ordered[: len(_sessions) - _MAX_SESSIONS]:
        _sessions.pop(key, None)


def nueva_sesion(
    *,
    canal: str,
    conversation_id: str,
    user_id: str | None,
) -> dict[str, Any]:
    key = _key(canal, conversation_id)
    now = time.time()

    session = {
        "canal": str(canal or "ATLAS").strip().upper() or "ATLAS",
        "conversation_id": str(conversation_id).strip(),
        "user_id": str(user_id or "").strip() or None,
        "estado": "ESPERANDO_TIPO",
        "tipo_numero": None,
        "tipo_codigo": None,
        "tipo_nombre": None,
        "wo": None,
        "solicitud_id": None,
        "_created_ts": now,
        "_updated_ts": now,
    }

    with _lock:
        _cleanup_locked()
        _sessions[key] = session
        return deepcopy(session)


def obtener_sesion(
    *,
    canal: str,
    conversation_id: str,
) -> dict[str, Any] | None:
    key = _key(canal, conversation_id)

    with _lock:
        _cleanup_locked()
        session = _sessions.get(key)

        if not session:
            return None

        session["_updated_ts"] = time.time()
        return deepcopy(session)


def guardar_sesion(session: dict[str, Any]) -> dict[str, Any]:
    key = _key(
        str(session.get("canal") or "ATLAS"),
        str(session.get("conversation_id") or ""),
    )

    copy = deepcopy(session)
    copy["_updated_ts"] = time.time()

    with _lock:
        _cleanup_locked()
        _sessions[key] = copy
        return deepcopy(copy)


def eliminar_sesion(
    *,
    canal: str,
    conversation_id: str,
) -> None:
    key = _key(canal, conversation_id)

    with _lock:
        _sessions.pop(key, None)


def stats() -> dict[str, Any]:
    with _lock:
        _cleanup_locked()

        by_state: dict[str, int] = {}
        by_channel: dict[str, int] = {}

        for session in _sessions.values():
            state = str(session.get("estado") or "DESCONOCIDO")
            channel = str(session.get("canal") or "DESCONOCIDO")

            by_state[state] = by_state.get(state, 0) + 1
            by_channel[channel] = by_channel.get(channel, 0) + 1

        return {
            "sesiones_activas": len(_sessions),
            "por_estado": by_state,
            "por_canal": by_channel,
            "ttl_segundos": _SESSION_TTL_SECONDS,
        }