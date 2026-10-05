from __future__ import annotations

import asyncio
import re
import threading
from typing import Any

from app.services.helix.note_publish_policy import (
    STATE_FILE,
    _idem_key,
    _load_state,
    _validate_request,
)

from app.services.helix.runtime.note_publish_runtime import (
    precommit_note_publish,
    runtime_capabilities,
)


# ATLAS_HELIX_NOTE_PUBLISH_SERVICE_8023_V3

PRECOMMIT_LOCK = threading.RLock()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        _clean(value),
    ).upper()


def _session_auth_context(
    helix_auth: dict[str, Any] | None,
    helix_auth_mode: str,
) -> dict[str, Any]:

    mode = _norm(
        helix_auth_mode
    )

    if mode not in {
        "",
        "TEST_FALLBACK",
        "SESSION_CREDENTIALS",
    }:
        raise ValueError(
            "HELIX_AUTH_MODE_INVALIDO"
        )

    if mode != "SESSION_CREDENTIALS":
        return {
            "mode": (
                mode
                or "TEST_FALLBACK"
            ),
            "username": "",
            "password": "",
        }

    auth = (
        helix_auth
        if isinstance(
            helix_auth,
            dict,
        )
        else {}
    )

    username = _clean(
        auth.get("username")
    )

    password = str(
        auth.get("password")
        or ""
    )

    scope = _norm(
        auth.get("scope")
    )

    persistence = _norm(
        auth.get("persistence")
    )

    if not username or not password:
        raise ValueError(
            "HELIX_AUTH_CREDENCIALES_VACIAS"
        )

    if (
        len(username) > 200
        or len(password) > 500
    ):
        raise ValueError(
            "HELIX_AUTH_LONGITUD_INVALIDA"
        )

    if any(
        ch in username
        for ch in (
            "\r",
            "\n",
            "\t",
            "\x00",
        )
    ):
        raise ValueError(
            "HELIX_AUTH_USUARIO_INVALIDO"
        )

    if "\x00" in password:
        raise ValueError(
            "HELIX_AUTH_PASSWORD_INVALIDO"
        )

    if scope != "NOTE_PUBLISH_ONLY":
        raise ValueError(
            "HELIX_AUTH_SCOPE_INVALIDO"
        )

    if persistence != "NONE":
        raise ValueError(
            "HELIX_AUTH_PERSISTENCE_INVALIDA"
        )

    return {
        "mode": "SESSION_CREDENTIALS",
        "username": username,
        "password": password,
    }


def precommit_note_publish_service(
    payload: dict[str, Any],
) -> dict[str, Any]:

    case_id = _clean(
        payload.get("case_id")
    )

    note_text = str(
        payload.get("note_text")
        or ""
    )

    note_hash = _clean(
        payload.get("note_hash")
    )

    note_context = (
        payload.get("note_context")
        or {}
    )

    base = {
        "ok": False,
        "codigo": "HELIX_NOTE_PRECOMMIT_ERROR",
        "origen": "HELIX_RELACIONADOS_API_8023",
        "case_id": case_id,
        "wo": _clean(
            note_context.get("wo")
        ).upper(),
        "incidente": "",
        "solicitud_id": _clean(
            note_context.get(
                "solicitud_id"
            )
        ),
        "production_write": False,
        "publication_clicked": False,
        "safe_to_retry": True,
        "duplicate": False,
        "error": "",
    }

    try:
        auth_context = _session_auth_context(
            payload.get("helix_auth"),
            _clean(
                payload.get(
                    "helix_auth_mode"
                )
            ),
        )

    except ValueError as exc:
        base["codigo"] = (
            "HELIX_NOTE_AUTH_INVALID"
        )
        base["error"] = str(exc)
        return base

    base["helix_auth_mode"] = (
        auth_context["mode"]
    )

    base["helix_user"] = (
        auth_context["username"]
    )

    valid_ok, validated = (
        _validate_request(
            case_id=case_id,
            note_text=note_text,
            note_hash=note_hash,
            note_context=note_context,
        )
    )

    if not valid_ok:
        base.update(validated)
        return base

    base["wo"] = validated["wo"]

    base["incidente"] = validated.get(
        "explicit_inc",
        "",
    )

    base["solicitud_id"] = validated[
        "solicitud_id"
    ]

    base["note_hash"] = validated[
        "note_hash"
    ]

    base["note_length"] = len(
        validated["note_text"]
    )

    caps = runtime_capabilities()

    required = (
        "profile_exists",
        "settings_available",
        "login_available",
        "global_search_available",
        "open_work_order_available",
        "related_reader_available",
        "incident_click_available",
        "normalize_available",
    )

    missing = [
        key
        for key in required
        if not bool(
            caps.get(key)
        )
    ]

    if missing:
        base["codigo"] = (
            "HELIX_NOTE_RUNTIME_INCOMPLETO"
        )

        base["error"] = (
            "Capacidades faltantes: "
            + ",".join(missing)
        )

        return base

    key = _idem_key(
        validated["case_id"],
        validated["solicitud_id"],
        validated["wo"],
        validated["tipo_codigo"],
    )

    state = _load_state()

    previous = (
        state.get(
            "records",
            {},
        ).get(key)
        if isinstance(state, dict)
        else None
    )

    if isinstance(previous, dict):

        status = _clean(
            previous.get("status")
        ).upper()

        if status == "VERIFIED":
            base.update({
                "ok": True,
                "codigo": (
                    "HELIX_NOTE_DUPLICADA_YA_VERIFICADA"
                ),
                "duplicate": True,
                "incidente": _clean(
                    previous.get("incidente")
                ),
                "production_write": False,
                "publication_clicked": False,
                "safe_to_retry": False,
            })
            return base

        if status == "COMMIT_UNVERIFIED":
            base.update({
                "codigo": (
                    "HELIX_NOTE_DUPLICATE_BLOCK_COMMIT_INCIERTO"
                ),
                "duplicate": True,
                "incidente": _clean(
                    previous.get("incidente")
                ),
                "production_write": True,
                "publication_clicked": False,
                "safe_to_retry": False,
                "error": (
                    "Existe un commit previo no verificado; "
                    "se bloquea un segundo intento."
                ),
            })
            return base

    headless = bool(
        payload.get(
            "headless",
            True,
        )
    )

    with PRECOMMIT_LOCK:
        try:
            # ATLAS_HELIX_NOTE_PRECOMMIT_WATCHDOG_120_V1
            async def _run_precommit_with_timeout():
                return await asyncio.wait_for(
                    precommit_note_publish(
                        validated,
                        auth_context,
                        headless=headless,
                    ),
                    timeout=120,
                )

            result = asyncio.run(
                _run_precommit_with_timeout()
            )

        except Exception as exc:
            base["codigo"] = (
                "HELIX_NOTE_PRECOMMIT_EXCEPTION"
            )
            base["error"] = (
                f"{type(exc).__name__}: {exc}"
            )
            base["safe_to_retry"] = True
            return base

    base.update({
        "ok": bool(
            result.get("ok")
        ),
        "codigo": _clean(
            result.get("codigo")
        )
        or "HELIX_NOTE_PRECOMMIT_ERROR",
        "incidente": _clean(
            result.get("incident")
        ),
        "status_value": _clean(
            result.get("status_value")
        ),
        "note_type": _clean(
            result.get("note_type")
        ),
        "public_checked": result.get(
            "public_checked"
        ),
        "editor_empty": result.get(
            "editor_empty"
        ),
        "publication_visible": bool(
            result.get(
                "publication_visible"
            )
        ),
        "publication_disabled": result.get(
            "publication_disabled"
        ),
        "production_write": False,
        "publication_clicked": False,
        "safe_to_retry": bool(
            result.get(
                "safe_to_retry",
                True,
            )
        ),
        "error": _clean(
            result.get("error")
        ),
    })

    return base


def precheck_note_publish(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Conserva endpoint de Fase 1.
    Solo valida; no abre Helix.
    """

    case_id = _clean(
        payload.get("case_id")
    )

    note_text = str(
        payload.get("note_text")
        or ""
    )

    note_hash = _clean(
        payload.get("note_hash")
    )

    note_context = (
        payload.get("note_context")
        or {}
    )

    base = {
        "ok": False,
        "codigo": "HELIX_NOTE_PRECHECK_ERROR",
        "origen": "HELIX_RELACIONADOS_API_8023",
        "case_id": case_id,
        "wo": _clean(
            note_context.get("wo")
        ).upper(),
        "incidente": _clean(
            note_context.get(
                "incidente_relacionado"
            )
        ).upper(),
        "solicitud_id": _clean(
            note_context.get(
                "solicitud_id"
            )
        ),
        "production_write": False,
        "safe_to_retry": True,
        "duplicate": False,
        "error": "",
    }

    valid_ok, validated = _validate_request(
        case_id=case_id,
        note_text=note_text,
        note_hash=note_hash,
        note_context=note_context,
    )

    if not valid_ok:
        base.update(validated)
        return base

    key = _idem_key(
        validated["case_id"],
        validated["solicitud_id"],
        validated["wo"],
        validated["tipo_codigo"],
    )

    state = _load_state()

    records = (
        state.get("records")
        if isinstance(
            state,
            dict,
        )
        else {}
    )

    if not isinstance(records, dict):
        records = {}

    previous = records.get(key)

    idempotency_status = "NEW"

    if isinstance(previous, dict):
        idempotency_status = (
            _clean(
                previous.get("status")
            ).upper()
            or "EXISTING"
        )

    base.update({
        "ok": True,
        "codigo": "HELIX_NOTE_PRECHECK_OK",
        "note_hash": validated[
            "note_hash"
        ],
        "note_length": len(
            validated["note_text"]
        ),
        "idempotency_key": key,
        "idempotency_status": (
            idempotency_status
        ),
        "idempotency_state_file": str(
            STATE_FILE
        ),
        "runtime": runtime_capabilities(),
        "error": "",
    })

    return base
