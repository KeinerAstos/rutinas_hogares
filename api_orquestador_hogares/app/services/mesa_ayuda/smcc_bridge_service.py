# -*- coding: utf-8 -*-
"""Puente SMCC -> Mesa de Ayuda.

FASE 1:
- recibe mensajes normalizados desde la extension local SMCC;
- solo procesa origen CLIENTE;
- deduplica por caso + message_id;
- usa el Caso SMCC como conversation_id estable;
- no escribe nada en SMCC.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Any

from app.services.mesa_ayuda.conversation_service import conversar
from app.queue.manager import (
    QueueJobTimeout,
    smcc_queue_manager,
)
from app.services.mesa_ayuda.smcc_tracking_schema import SmccTrackingEventIn
from app.services.mesa_ayuda.smcc_tracking_service import SmccTrackingService
from app.services.mesa_ayuda.smcc_conversation_log_service import SmccConversationLogService


# SMCC_ATLAS_BRIDGE_SERVICE_FASE1_V1
_CASE_RE = re.compile(r"^\d{6,30}$")

_registry_lock = threading.RLock()
_case_locks: dict[str, threading.RLock] = {}
_initialized_cases: set[str] = set()
_seen_messages: OrderedDict[str, None] = OrderedDict()
_MAX_SEEN = 10000

# ATLAS_SMCC_CONCURRENCY_CONTROL_V1
# Maximo operativo: 3 conversaciones SMCC procesandose simultaneamente.
_SMCC_MAX_CONCURRENT = 3
_smcc_slots = threading.BoundedSemaphore(_SMCC_MAX_CONCURRENT)
_smcc_active = 0
_smcc_waiting = 0


def _acquire_smcc_slot() -> None:
    global _smcc_active, _smcc_waiting

    with _registry_lock:
        _smcc_waiting += 1

    try:
        _smcc_slots.acquire()
    finally:
        with _registry_lock:
            _smcc_waiting = max(0, _smcc_waiting - 1)

    with _registry_lock:
        _smcc_active += 1


def _release_smcc_slot() -> None:
    global _smcc_active

    with _registry_lock:
        _smcc_active = max(0, _smcc_active - 1)

    _smcc_slots.release()
def _case_lock(case_id: str) -> threading.RLock:
    with _registry_lock:
        lock = _case_locks.get(case_id)
        if lock is None:
            lock = threading.RLock()
            _case_locks[case_id] = lock
        return lock


def _seen(key: str) -> bool:
    with _registry_lock:
        if key in _seen_messages:
            _seen_messages.move_to_end(key)
            return True
        return False


def _remember(key: str) -> None:
    with _registry_lock:
        _seen_messages[key] = None
        _seen_messages.move_to_end(key)

        while len(_seen_messages) > _MAX_SEEN:
            _seen_messages.popitem(last=False)


# SMCC_CONVERSATION_LOG_FAIL_OPEN_V1
def _log_smcc_message(
    *,
    conversation_id: str,
    role: str,
    text: str,
    message_id: str = "",
    timestamp: str = "",
) -> bool:
    try:
        SmccConversationLogService().append_message(
            conversation_id=conversation_id,
            role=role,
            text=text,
            message_id=message_id,
            timestamp=timestamp,
        )
        return True
    except Exception:
        return False

# SMCC_TRACKING_FAIL_OPEN_V1
_WO_RE = re.compile(r"\bWO\d{7,20}\b", re.IGNORECASE)


def _extract_wo_for_tracking(message: str) -> str | None:
    match = _WO_RE.search(str(message or ""))
    if not match:
        return None
    return match.group(0).upper()


def _track_smcc_event(
    *,
    conversation_id: str,
    event: str,
    wo: str | None = None,
    operation: str | None = None,
    status: str | None = None,
    result: str | None = None,
    error_code: str | None = None,
    channel: str = "",
) -> bool:
    try:
        safe_metadata: dict[str, Any] = {
            "source": "SMCC_BRIDGE_BACKEND",
        }
        if channel:
            safe_metadata["channel"] = str(channel).strip().upper()[:40]

        payload = SmccTrackingEventIn(
            conversation_id=str(conversation_id).strip(),
            event=event,
            wo=wo,
            operation=operation,
            status=status,
            result=result,
            error_code=error_code,
            metadata=safe_metadata,
        )

        SmccTrackingService().register_event(payload)
        return True
    except Exception:
        return False

def _resultado_control_smcc(result: dict) -> str:
    """Resultado de calidad consumido por la extension SMCC."""
    if not isinstance(result, dict):
        return "Revisar"

    codigo = str(result.get("codigo") or "").strip().upper()
    estado = str(result.get("estado") or "").strip().upper()

    operativo = result.get("resultado_operativo")
    op_codigo = ""
    if isinstance(operativo, dict):
        op_codigo = str(operativo.get("codigo") or "").strip().upper()

    direcciones = result.get("resultado_direcciones")
    dir_codigo = ""
    if isinstance(direcciones, dict):
        dir_codigo = str(direcciones.get("codigo") or "").strip().upper()

    ok_codes = {
        "MESA_DIRECCIONES_CLIENTES_OK",
        "MESA_REDES_NEUTRAS_OK",
        "MESA_DIRECCIONES_RESULTADO_LISTO",
        "MESA_REDES_NEUTRAS_RESULTADO_LISTO",
        "HFC_DIRECCIONES_OK",
        "HELIX_ADJUNTOS_DESCARGADOS",
        "RN_SMCC_DOCUMENT_READY",
    }

    bad_codes = {
        "MESA_DIRECCIONES_CLIENTES_PARCIAL",
        "MESA_REDES_NEUTRAS_PARCIAL",
        "MESA_DIRECCIONES_EMPRESAS_NEGOCIOS",
        "MESA_DIRECCIONES_NO_SERVICIOS_FIJOS",
        "MESA_DIRECCIONES_OLT_NO_DISPONIBLE",
        "MESA_DIRECCIONES_ACS_TIMEOUT",
        "MESA_DIRECCIONES_DIAGNOSTICADOR_TIMEOUT",
        "MESA_DIRECCIONES_ACS_LOGIN_ERROR",
        "MESA_DIRECCIONES_DIAGNOSTICADOR_LOGIN_ERROR",
        "MESA_DIRECCIONES_ACS_SIN_CUENTA",
        "MESA_DIRECCIONES_EMPRESAS_NEGOCIOS_SIN_INFO",
        "MESA_REDES_NEUTRAS_CREDENCIALES_INVALIDAS",
        "HELIX_SERVICE_ERROR",
        "HELIX_RESPUESTA_INVALIDA",
    }

    if codigo in bad_codes or op_codigo in bad_codes or dir_codigo in bad_codes:
        return "Revisar"

    if codigo in ok_codes or op_codigo in ok_codes or dir_codigo in ok_codes:
        return "Resultado correcto"

    if codigo.endswith("_OK"):
        return "Resultado correcto"

    if estado.startswith("RESULTADO_") and codigo.endswith("_RESULTADO_LISTO"):
        return "Resultado correcto"

    return "Revisar"


def health_smcc_bridge() -> dict[str, Any]:
    queue_status = smcc_queue_manager.status()

    with _registry_lock:
        return {
            "ok": True,
            "servicio": "smcc_atlas_bridge",
            "version": "FASE1_V1",
            "modo": "READ_SMCC_PROCESS_ATLAS",
            "escritura_smcc": False,
            "envio_smcc": False,
            "casos_inicializados": len(_initialized_cases),
            "mensajes_deduplicados_memoria": len(_seen_messages),
            # ATLAS_SMCC_PRIORITY_QUEUE_V1
            "queue_manager": "ATLAS_PRIORITY_QUEUE_V1_1",
            "max_concurrent_smcc": queue_status["workers"],
            "active_smcc": queue_status["active"],
            "waiting_smcc": queue_status["queued"],
            "available_smcc": queue_status["available"],
            "oldest_wait_seconds": queue_status["oldest_wait_seconds"],
            "queue_completed": queue_status["completed"],
            "queue_errors": queue_status["errors"],
            "queue_timeouts": queue_status["timeouts"],
        }


# ATLAS_SMCC_AGENT_SESSION_AUTH_EXTRACT_V1
def _extract_smcc_agent_helix_auth(
    helix_auth: dict[str, Any] | None,
    helix_auth_mode: str = "",
) -> tuple[str, str]:
    """
    Extrae credenciales efímeras del agente SMCC.

    No persiste, no registra y no devuelve password
    fuera del proceso actual.
    """
    mode = str(
        helix_auth_mode or ""
    ).strip().upper()

    if mode != "SESSION_CREDENTIALS":
        return "", ""

    auth = (
        helix_auth
        if isinstance(helix_auth, dict)
        else {}
    )

    scope = str(
        auth.get("scope") or ""
    ).strip().upper()

    persistence = str(
        auth.get("persistence") or ""
    ).strip().upper()

    if scope != "SMCC_AGENT_SESSION":
        return "", ""

    if persistence not in {"", "NONE"}:
        return "", ""

    username = str(
        auth.get("username") or ""
    ).strip()

    password = str(
        auth.get("password") or ""
    )

    if not username or not password:
        return "", ""

    return username, password


def procesar_mensaje_smcc(
    *,
    case_id: str,
    message_id: str,
    origin: str,
    text: str,
    timestamp: str = "",
    channel: str = "WHATSAPP",
    agent_id: str = "",
    source_url: str = "",
    dom_id: str = "",
    # ATLAS_SMCC_PROCESS_CLIENT_AUTH_ARGS_V1
    helix_auth: dict[str, Any] | None = None,
    helix_auth_mode: str = "",
) -> dict[str, Any]:
    case = str(case_id or "").strip()
    msg_id = str(message_id or "").strip()
    origin_norm = str(origin or "").strip().upper()
    message = str(text or "").strip()
    channel_norm = str(channel or "WHATSAPP").strip().upper()
    agent = str(agent_id or "").strip() or "SMCC_AGENT"

    # ATLAS_SMCC_AGENT_SESSION_AUTH_LOCAL_V1
    helix_username, helix_password = (
        _extract_smcc_agent_helix_auth(
            helix_auth,
            helix_auth_mode,
        )
    )

    if not _CASE_RE.fullmatch(case):
        return {
            "ok": False,
            "processed": False,
            "codigo": "SMCC_CASE_INVALIDO",
            "case_id": case,
            "error": "case_id invalido.",
        }

    if not msg_id:
        return {
            "ok": False,
            "processed": False,
            "codigo": "SMCC_MESSAGE_ID_VACIO",
            "case_id": case,
            "error": "message_id es obligatorio.",
        }

    if origin_norm != "CLIENTE":
        return {
            "ok": True,
            "processed": False,
            "ignored": True,
            "duplicate": False,
            "codigo": "SMCC_ORIGEN_IGNORADO",
            "case_id": case,
            "message_id": msg_id,
            "origin": origin_norm,
            "motivo": "FASE1 solo procesa mensajes CLIENTE.",
        }

    if not message:
        return {
            "ok": True,
            "processed": False,
            "ignored": True,
            "duplicate": False,
            "codigo": "SMCC_TEXTO_VACIO",
            "case_id": case,
            "message_id": msg_id,
        }

    dedup_key = f"{case}:{msg_id}"

    if _seen(dedup_key):
        return {
            "ok": True,
            "processed": False,
            "ignored": False,
            "duplicate": True,
            "codigo": "SMCC_DUPLICADO",
            "case_id": case,
            "message_id": msg_id,
        }

    conversation_id = f"smcc:{case}"
    case_lock = _case_lock(case)

    with case_lock:
        # Recheck después de adquirir lock de caso.
        if _seen(dedup_key):
            return {
                "ok": True,
                "processed": False,
                "ignored": False,
                "duplicate": True,
                "codigo": "SMCC_DUPLICADO",
                "case_id": case,
                "message_id": msg_id,
            }

        with _registry_lock:
            initialized = case in _initialized_cases

        # SMCC_TRACKING_FLOW_V1
        wo_for_tracking = _extract_wo_for_tracking(message)

        try:
            if not initialized:
                _track_smcc_event(
                    conversation_id=conversation_id,
                    event="SESSION_STARTED",
                    status="ACTIVE",
                    channel=channel_norm,
                )

                conversar(
                    mensaje="reiniciar",
                    conversation_id=conversation_id,
                    user_id=agent,
                    canal="SMCC_WHATSAPP",
                )

                with _registry_lock:
                    _initialized_cases.add(case)

            _track_smcc_event(
                conversation_id=conversation_id,
                event="SMCC_REQUEST",
                wo=wo_for_tracking,
                status="ACTIVE",
                channel=channel_norm,
            )

            if wo_for_tracking:
                _track_smcc_event(
                    conversation_id=conversation_id,
                    event="WO_RECEIVED",
                    wo=wo_for_tracking,
                    status="ACTIVE",
                    channel=channel_norm,
                )

            _track_smcc_event(
                conversation_id=conversation_id,
                event="ATLAS_REQUEST_STARTED",
                wo=wo_for_tracking,
                status="ACTIVE",
                channel=channel_norm,
            )

            _log_smcc_message(
                conversation_id=conversation_id,
                role="CLIENTE",
                text=message,
                message_id=msg_id,
                timestamp=str(timestamp or "").strip(),
            )

            # ATLAS_SMCC_PRIORITY_QUEUE_V1
            # Cola priorizada SMCC. El lock por caso sigue serializando cada conversacion.
            try:
                result = smcc_queue_manager.submit_and_wait(
                    job_id=dedup_key,
                    case_id=case,
                    source="SMCC",
                    priority=10,
                    queue_timeout_seconds=60.0,
                    fn=lambda: conversar(
                        mensaje=message,
                        conversation_id=conversation_id,
                        user_id=agent,
                        canal="SMCC_WHATSAPP",
                        # ATLAS_SMCC_AGENT_SESSION_AUTH_TO_CONVERSAR_V1
                        helix_username=helix_username,
                        helix_password=helix_password,
                    ),
                )
            except QueueJobTimeout:
                _track_smcc_event(
                    conversation_id=conversation_id,
                    event="SESSION_ERROR",
                    wo=wo_for_tracking,
                    status="ERROR",
                    result="QUEUE_TIMEOUT",
                    error_code="SMCC_QUEUE_TIMEOUT",
                    channel=channel_norm,
                )
                return {
                    "ok": False,
                    "processed": False,
                    "ignored": False,
                    "duplicate": False,
                    "codigo": "SMCC_QUEUE_TIMEOUT",
                    "resultado": "Error tÃ©cnico",
                    "resultado_control": "Error tÃ©cnico",
                    "case_id": case,
                    "message_id": msg_id,
                    "origin": origin_norm,
                    "conversation_id": conversation_id,
                    "atlas_reply": "La solicitud superÃ³ el tiempo mÃ¡ximo de espera antes de iniciar su procesamiento.",
                    "escritura_smcc": False,
                    "envio_smcc": False,
                }

            result_code = str(
                result.get("codigo")
                or result.get("tipo_respuesta")
                or result.get("estado")
                or "OK"
            ).strip()[:120]

            operation_for_tracking = str(
                result.get("tipo_nombre")
                or result.get("operation")
                or result.get("gestion")
                or ""
            ).strip()[:160]

            if (
                not operation_for_tracking
                and result_code == "MESA_AYUDA_TIPO_SELECCIONADO"
            ):
                atlas_text_for_operation = str(
                    result.get("respuesta") or ""
                ).strip()

                prefix = "Perfecto. Seleccionaste:"

                if prefix in atlas_text_for_operation:
                    operation_for_tracking = (
                        atlas_text_for_operation
                        .split(prefix, 1)[1]
                        .split(".", 1)[0]
                        .strip()
                    )[:160]

            _track_smcc_event(
                conversation_id=conversation_id,
                event="ATLAS_REQUEST_FINISHED",
                wo=wo_for_tracking,
                operation=operation_for_tracking or None,
                status="ACTIVE",
                result=result_code,
                channel=channel_norm,
            )

            _track_smcc_event(
                conversation_id=conversation_id,
                event="ATLAS_RESPONSE",
                wo=wo_for_tracking,
                operation=operation_for_tracking or None,
                status="ACTIVE",
                result=result_code,
                channel=channel_norm,
            )

            atlas_reply_for_log = str(result.get("respuesta") or "").strip()
            _log_smcc_message(
                conversation_id=conversation_id,
                role="ATLAS",
                text=atlas_reply_for_log,
            )

            _remember(dedup_key)

        except Exception as exc:
            _track_smcc_event(
                conversation_id=conversation_id,
                event="SESSION_ERROR",
                wo=wo_for_tracking,
                status="ERROR",
                result="ERROR",
                error_code=type(exc).__name__[:120],
                channel=channel_norm,
            )
            raise

    atlas_reply = str(result.get("respuesta") or "").strip()
    resultado_control = _resultado_control_smcc(result)

    if isinstance(result, dict):
        result = dict(result)
        result["resultado"] = resultado_control
        result["resultado_control"] = resultado_control

    return {
        "ok": True,
        "processed": True,
        "ignored": False,
        "duplicate": False,
        "codigo": "SMCC_ATLAS_PROCESADO",
        "resultado": resultado_control,
        "resultado_control": resultado_control,
        "case_id": case,
        "message_id": msg_id,
        "origin": origin_norm,
        "timestamp": str(timestamp or "").strip(),
        "channel": channel_norm,
        "source_url": str(source_url or "").strip(),
        "dom_id": str(dom_id or "").strip(),
        "conversation_id": conversation_id,
        "atlas_reply": atlas_reply,
        "atlas": result,
        "escritura_smcc": False,
        "envio_smcc": False,
    }
