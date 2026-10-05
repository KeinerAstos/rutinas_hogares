# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

from dotenv import load_dotenv

from app.services.helix.runtime.helix_runner import DOWNLOAD_DIR

from app.services.helix.summary import consultar_resumen_async
from app.services.helix.runtime.incident_related_items import consultar_relacionados_incidente_async
from app.services.helix.credential_state import get_credentials_alert

PROJECT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_DIR / ".env", override=False)

_WO_RE = re.compile(r"\b(WO\d{13,14})\b", re.IGNORECASE)

_QUEUE_TIMEOUT_SECONDS = max(
    30,
    int(os.getenv("HELIX_CHAT_QUEUE_TIMEOUT_SECONDS", "600") or "600"),
)
_HELIX_SEMAPHORE = BoundedSemaphore(
    max(1, int(os.getenv("HELIX_CHAT_WORKERS", "1") or "1"))
)




def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}




def _helix_lookup_candidates(ot: str) -> list[str]:
    value = str(ot or "").strip().upper()
    match = _WO_RE.fullmatch(value)
    if not match:
        return []

    original = match.group(1).upper()
    candidates = [original]

    numeric = original[2:]
    if len(numeric) != 14:
        return candidates

    significant = numeric.lstrip("0") or "0"
    if len(significant) > 13:
        return candidates

    fallback = "WO" + significant.zfill(13)
    if fallback != original:
        candidates.append(fallback)

    return candidates













def helix_health() -> dict[str, Any]:
    try:
        import playwright  # noqa: F401
        playwright_ok = True
    except Exception:
        playwright_ok = False

    return {
        "loaded": True,
        "playwright_loaded": playwright_ok,
        "credentials_configured": bool(
            os.getenv("SMARTIT_USER", "").strip()
            and os.getenv("SMARTIT_PASSWORD", "")
        ),
        "headless": _as_bool("HELIX_CHAT_HEADLESS", False),
        "workers": int(os.getenv("HELIX_CHAT_WORKERS", "1") or "1"),
        "downloads_dir": str(DOWNLOAD_DIR),
        "credentials_alert": get_credentials_alert(),
    }


helix_downloads_dir = DOWNLOAD_DIR


# PATCH_HELIX_RESUMEN_OT_ATLAS_V1
def _consultar_resumen_ot_helix_base(ot: str) -> dict[str, Any]:
    ot_match = _WO_RE.fullmatch(str(ot or "").strip())
    if not ot_match:
        return {
            "ok": False,
            "tipo_respuesta": "helix_resumen_ot",
            "codigo": "HELIX_OT_INVALIDA",
            "origen": "HELIX",
            "respuesta": "La orden debe tener formato WO seguido de 13 o 14 digitos.",
            "data": {},
        }

    normalized_ot = ot_match.group(1).upper()
    acquired = _HELIX_SEMAPHORE.acquire(timeout=_QUEUE_TIMEOUT_SECONDS)

    if not acquired:
        return {
            "ok": False,
            "tipo_respuesta": "helix_resumen_ot",
            "codigo": "HELIX_QUEUE_TIMEOUT",
            "origen": "HELIX",
            "respuesta": "Hay otra consulta Helix en curso.",
            "data": {"numero_ot": normalized_ot},
        }

    started_at = time.monotonic()

    try:
        lookup_candidates = _helix_lookup_candidates(normalized_ot)
        if not lookup_candidates:
            lookup_candidates = [normalized_ot]

        payload = None
        lookup_ot = normalized_ot
        last_lookup_exc: Exception | None = None

        for candidate_ot in lookup_candidates:
            lookup_ot = candidate_ot
            try:
                payload = asyncio.run(
                    consultar_resumen_async(
                        candidate_ot,
                        headless=_as_bool("HELIX_CHAT_HEADLESS", True),
                    )
                )
                last_lookup_exc = None
                break
            except Exception as lookup_exc:
                last_lookup_exc = lookup_exc
                error_text = f"{type(lookup_exc).__name__}: {lookup_exc}"
                if "WorkOrderNotFoundError" not in error_text:
                    raise

        if payload is None:
            if last_lookup_exc is not None:
                raise last_lookup_exc
            raise RuntimeError("Helix no devolvio informacion para la orden consultada.")

        data = {
            "numero_ot": normalized_ot,
            "numero_ot_consultada": lookup_ot,
            "uso_fallback_wo": lookup_ot != normalized_ot,
            "estado_ot": str(payload.get("estado_ot") or "").strip(),
            "incidente_relacionado": str(
                payload.get("incidente_relacionado") or ""
            ).strip(),
            "aliado": str(payload.get("aliado") or "").strip(),
            "ciudad": str(payload.get("ciudad") or "").strip(),
            "regional": str(payload.get("regional") or "").strip(),
            "estado_ofsc": str(payload.get("estado_ofsc") or "").strip(),
            "origen_datos": str(payload.get("origen_datos") or "").strip(),
            "uso_fallback": bool(payload.get("uso_fallback")),
            "tiempo_ofsc_seg": payload.get("tiempo_ofsc_seg"),
        }

        # HELIX_TITLE_FIELDS_V3
        for field in (
            # HELIX_SERVICE_CATEGORIA_OPERACIONAL_V1
            "categoria_operacional",
            "titulo_ot",
            "fuente_titulo",
            "tipo_red",
            "tipo_elemento",
            "nodo_detectado",
            "nodos_detectados",
            "estado_nodo",
            "es_hfc",
            "es_pathtrak",
            "es_ftth",
            "es_troncal",
            "es_mw",
            "tecnologia_pon",
            "elemento_red",
            "rack",
            "shelf",
            "slot",
            "port",
            "frame",
            "subslot",
            "port_informado",
            "parser_version",
        ):
            data[field] = payload.get(field)

        if payload.get("error_titulo"):
            data["error_titulo"] = payload["error_titulo"]

        code = str(payload.get("codigo") or "HELIX_OT_RESUMEN_ERROR")
        response_text = (
            f"Resumen de {normalized_ot} consultado correctamente en Helix."
            if code == "HELIX_OT_RESUMEN_OK"
            else str(payload.get("error") or "No fue posible completar la consulta.")
        )

        return {
            "ok": bool(payload.get("ok")) or any(
                data.get(field)
                for field in (
                    "incidente_relacionado",
                    "aliado",
                    "ciudad",
                    "regional",
                )
            ),
            "tipo_respuesta": "helix_resumen_ot",
            "codigo": code,
            "origen": "HELIX",
            "respuesta": response_text,
            "data": data,
            "duracion_seg": round(time.monotonic() - started_at, 2),
            "error": str(payload.get("error") or ""),
        }
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        credentials_invalid = "HELIX_CREDENCIALES_INVALIDAS" in str(exc)

        return {
            "ok": False,
            "tipo_respuesta": "helix_resumen_ot",
            "codigo": (
                "HELIX_CREDENCIALES_INVALIDAS"
                if credentials_invalid
                else "HELIX_OT_RESUMEN_EXCEPTION"
            ),
            "origen": "HELIX",
            "respuesta": (
                "⚠ Helix rechazó las credenciales configuradas. "
                "Se requiere actualizar SMARTIT_USER / SMARTIT_PASSWORD."
                if credentials_invalid
                else "No fue posible completar la consulta."
            ),
            "requiere_actualizar_credenciales": credentials_invalid,
            "data": {"numero_ot": normalized_ot},
            "duracion_seg": round(time.monotonic() - started_at, 2),
            "error": error_text,
        }
    finally:
        _HELIX_SEMAPHORE.release()


def consultar_resumen_ot_helix(ot: str) -> dict[str, Any]:
    # HELIX_SINGLE_SESSION_PUBLIC_V3
    return _consultar_resumen_ot_helix_base(ot)


# HELIX_INC_RELATED_ITEMS_ATLAS_V1
_INC_RE = re.compile(r"^INC\d{12}$", re.IGNORECASE)


def consultar_relacionados_incidente(inc: str) -> dict[str, Any]:
    normalized_inc = str(inc or "").strip().upper()
    if not _INC_RE.fullmatch(normalized_inc):
        return {
            "ok": False,
            "tipo_respuesta": "helix_inc_relacionados",
            "codigo": "HELIX_INC_INVALIDO",
            "origen": "HELIX",
            "inc": normalized_inc,
            "titulo": "",
            "total_wo": 0,
            "wo_relacionadas": [],
            "total_ta": 0,
            "ta_relacionadas": [],
            "estado": "ERROR",
            "error": "El incidente debe tener formato INC seguido de 12 digitos.",
        }

    acquired = _HELIX_SEMAPHORE.acquire(timeout=_QUEUE_TIMEOUT_SECONDS)
    if not acquired:
        return {
            "ok": False,
            "tipo_respuesta": "helix_inc_relacionados",
            "codigo": "HELIX_QUEUE_TIMEOUT",
            "origen": "HELIX",
            "inc": normalized_inc,
            "titulo": "",
            "total_wo": 0,
            "wo_relacionadas": [],
            "total_ta": 0,
            "ta_relacionadas": [],
            "estado": "ERROR",
            "error": "Hay otra consulta Helix en curso.",
        }

    try:
        payload = asyncio.run(
            consultar_relacionados_incidente_async(
                normalized_inc,
                headless=_as_bool("HELIX_CHAT_HEADLESS", True),
            )
        )
        payload["tipo_respuesta"] = "helix_inc_relacionados"
        return payload
    except Exception as exc:
        credentials_invalid = "HELIX_CREDENCIALES_INVALIDAS" in str(exc)
        return {
            "ok": False,
            "tipo_respuesta": "helix_inc_relacionados",
            "codigo": (
                "HELIX_CREDENCIALES_INVALIDAS"
                if credentials_invalid
                else "HELIX_INC_RELACIONADOS_ERROR"
            ),
            "origen": "HELIX",
            "inc": normalized_inc,
            "titulo": "",
            "total_wo": 0,
            "wo_relacionadas": [],
            "total_ta": 0,
            "ta_relacionadas": [],
            "estado": "ERROR",
            "requiere_actualizar_credenciales": credentials_invalid,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        _HELIX_SEMAPHORE.release()
