from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.helix.note_policy import SOP_POR_TIPO


# ATLAS_HELIX_NOTE_PUBLISH_POLICY_8023_V1

SERVICE_ROOT = Path(__file__).resolve().parents[3]

STATE_DIR = (
    SERVICE_ROOT
    / "runtime"
    / "data"
    / "helix_note_publish"
)

STATE_FILE = (
    STATE_DIR
    / "idempotency.json"
)

RUN_ROOT = (
    SERVICE_ROOT
    / "runtime"
    / "diagnostics"
    / "helix_note_publish"
)

WRITE_LOCK = threading.RLock()

WO_RE = re.compile(r"^WO\d{13,14}$", re.I)

INC_RE = re.compile(r"^INC\d+$", re.I)

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def _clean(value: Any) -> str:
    return str(value or "").strip()

def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value)).upper()

def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _state_default() -> dict[str, Any]:
    return {"version": 1, "records": {}}

def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _state_default()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _state_default()
    if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
        return _state_default()
    return data

def _save_state(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(STATE_FILE)

def _idem_key(case_id: str, solicitud_id: str, wo: str, tipo_codigo: str) -> str:
    raw = f"{case_id}|{solicitud_id}|{wo}|{tipo_codigo}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _validate_request(
    *,
    case_id: str,
    note_text: str,
    note_hash: str,
    note_context: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    case = _clean(case_id)
    text = str(note_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    supplied_hash = _clean(note_hash).lower()
    context = note_context if isinstance(note_context, dict) else {}

    tipo = _norm(context.get("tipo_codigo"))
    wo = _clean(context.get("wo")).upper()
    explicit_inc = _clean(context.get("incidente_relacionado")).upper()
    solicitud_id = _clean(context.get("solicitud_id"))
    expected_sop = _clean(SOP_POR_TIPO.get(tipo, ""))

    if not case:
        return False, {"codigo": "HELIX_NOTE_CASE_VACIO", "error": "case_id vacío."}
    if not solicitud_id:
        return False, {"codigo": "HELIX_NOTE_SOLICITUD_ID_VACIA", "error": "solicitud_id vacía."}
    if not expected_sop:
        return False, {"codigo": "HELIX_NOTE_TIPO_SIN_SOP", "error": "El tipo no tiene SOP habilitado."}
    if context.get("habilitado") is not True:
        return False, {"codigo": "HELIX_NOTE_DESHABILITADA", "error": "La nota está deshabilitada por backend."}
    if context.get("terminal_nota") is not True:
        return False, {"codigo": "HELIX_NOTE_NO_TERMINAL", "error": "La gestión todavía no está en estado terminal."}
    if context.get("publicar_automaticamente") is not True:
        return False, {"codigo": "HELIX_NOTE_AUTO_OFF", "error": "Publicación automática deshabilitada."}
    if context.get("escritura_helix") is not True:
        return False, {"codigo": "HELIX_NOTE_WRITE_OFF", "error": "Escritura Helix deshabilitada."}
    if _norm(context.get("destino")) != "INC_RELACIONADO":
        return False, {"codigo": "HELIX_NOTE_DESTINO_INVALIDO", "error": "El destino no es INC_RELACIONADO."}
    if not WO_RE.fullmatch(wo):
        return False, {"codigo": "HELIX_NOTE_WO_INVALIDA", "error": "WO inválida."}
    if explicit_inc and not INC_RE.fullmatch(explicit_inc):
        return False, {"codigo": "HELIX_NOTE_INC_INVALIDO", "error": "INC explícito inválido."}
    if not text or len(text) > 120000:
        return False, {"codigo": "HELIX_NOTE_TEXTO_INVALIDO", "error": "Nota vacía o demasiado grande."}
    if text == expected_sop:
        return False, {"codigo": "HELIX_NOTE_SIN_CONVERSACION", "error": "La nota no contiene conversación."}
    if not text.startswith(expected_sop + "\n\n"):
        return False, {"codigo": "HELIX_NOTE_SOP_MISMATCH", "error": "El encabezado SOP no coincide con el backend."}

    calculated = _sha256_text(text)
    if supplied_hash and supplied_hash != calculated:
        return False, {"codigo": "HELIX_NOTE_HASH_MISMATCH", "error": "SHA-256 de la nota no coincide."}

    return True, {
        "case_id": case,
        "note_text": text,
        "note_hash": calculated,
        "tipo_codigo": tipo,
        "sop_titulo": expected_sop,
        "wo": wo,
        "explicit_inc": explicit_inc,
        "solicitud_id": solicitud_id,
    }
