# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
import unicodedata
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv

from app.services.helix.runtime.helix_runner import (
    DOWNLOAD_DIR,
    RESULT_DIR,
    main_async,
)

from app.services.helix.summary import consultar_resumen_async
from app.services.helix.oracle_summary_service import consultar_resumen_ot_oracle

from app.services.helix.credential_state import get_credentials_alert

PROJECT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_DIR / ".env", override=False)

_WO_RE = re.compile(r"\b(WO\d{13,14})\b", re.IGNORECASE)
_FILE_RE = re.compile(
    r"(?i)([^\\/:*?\"<>|\r\n]{1,180}\."
    r"(?:pdf|docx?|xlsx?|xlsm|csv|txt|zip|rar|7z|png|jpe?g|gif|bmp|tiff?|pptx?|msg|eml))"
)

_QUEUE_TIMEOUT_SECONDS = max(
    30,
    int(os.getenv("HELIX_CHAT_QUEUE_TIMEOUT_SECONDS", "600") or "600"),
)
_HELIX_SEMAPHORE = BoundedSemaphore(
    max(1, int(os.getenv("HELIX_CHAT_WORKERS", "1") or "1"))
)


def _normalizar_texto(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sÃ­", "on"}


def _extraer_archivo(mensaje: str, ot: str) -> str:
    # Primero respetar nombres entre comillas.
    for match in re.finditer(r'["\']([^"\']+)["\']', mensaje):
        value = match.group(1).strip()
        if _FILE_RE.fullmatch(value):
            return value

    tail = mensaje
    position = mensaje.upper().find(ot.upper())
    if position >= 0:
        tail = mensaje[position + len(ot):]

    matches = list(_FILE_RE.finditer(tail))
    if not matches:
        return ""

    value = matches[-1].group(1).strip()
    value = re.sub(
        r"^(?:archivo|documento|adjunto|descargar|bajar)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    return value


def _helix_lookup_candidates(ot: str) -> list[str]:
    value = str(ot or "").strip().upper()
    match = _WO_RE.fullmatch(value)
    if not match:
        return []

    original = match.group(1).upper()
    candidates = [original]

    numeric = original[2:]

    # HELIX_WILDCARD_LENGTH_FIX_V1
    #
    # La entrada Helix acepta WO con 13 o 14 digitos.
    # En ambos formatos se permite construir el criterio wildcard.
    if len(numeric) not in (13, 14):
        return candidates

    significant = numeric.lstrip("0") or "0"

    # HELIX_WILDCARD_LOOKUP_V1
    #
    # El segundo intento no inventa otra WO rellenando ceros.
    # Usa el comodin de SmartIT como criterio de busqueda.
    #
    # Ejemplo:
    #   WO0000005842345
    #       ->
    #   WO%5842345
    #
    # El valor con % NO representa una WO real. Si SmartIT devuelve
    # una única Work Order, helix_runner resuelve posteriormente su
    # identificador real antes de continuar.
    fallback = "WO%" + significant

    if fallback != original:
        candidates.append(fallback)

    return candidates


def detectar_consulta_helix(mensaje: str) -> dict[str, Any] | None:
    text = str(mensaje or "").strip()
    normalized = _normalizar_texto(text)
    match = _WO_RE.search(text)

    if not match:
        return None

    # Se exige mencionar Helix para no cambiar silenciosamente otros flujos.
    if "helix" not in normalized:
        return None

    ot = match.group(1).upper()
    descargar = any(
        word in normalized
        for word in ("descargar", "descarga", "bajar", "abrir archivo")
    )
    archivo = _extraer_archivo(text, ot) if descargar else ""

    return {
        "ot": ot,
        "descargar": descargar,
        "archivo": archivo,
        "todos": any(
            phrase in normalized
            for phrase in ("todos los archivos", "todos los adjuntos", "descargar todos")
        ),
    }


def _latest_result(ot: str, started_at: float) -> Path | None:
    candidates = sorted(
        RESULT_DIR.glob(f"resultado_{ot}_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if candidate.stat().st_mtime >= started_at - 2:
            return candidate
    return None


def _public_file(item: dict[str, Any], ot: str, incident: str) -> dict[str, Any]:
    local_path = Path(str(item.get("ruta_local") or ""))
    filename = local_path.name or str(item.get("nombre") or "")

    source_id = ""
    if local_path.parent and local_path.parent.name:
        source_id = str(local_path.parent.name or "").strip()

    if not source_id:
        source_id = str(item.get("origen_id") or incident or ot).strip()

    source_type = (
        "WO"
        if source_id.upper().startswith("WO")
        else "INC"
        if source_id.upper().startswith("INC")
        else "HELIX"
    )

    return {
        "nombre": filename,
        "filename": filename,
        "download_url": (
            f"/api/deco/helix/files/{quote(ot)}/{quote(source_id)}/{quote(filename)}"
        ),
        "size_bytes": local_path.stat().st_size if local_path.is_file() else 0,
        "origen": "HELIX",
        "origen_tipo": source_type,
        "origen_id": source_id,
    }



def _build_response(payload: dict[str, Any]) -> dict[str, Any]:
    ot = str(payload.get("ot") or "")
    code = str(payload.get("codigo") or "HELIX_ERROR")
    incident = str(payload.get("incidente_abierto") or "")
    attachments = payload.get("adjuntos") or []
    names = [str(item.get("nombre") or "") for item in attachments if item.get("nombre")]
    downloaded = [item for item in attachments if item.get("descargado")]

    if code == "HELIX_OT_NO_ENCONTRADA":
        response_text = f"Helix no encontrÃ³ la orden de trabajo {ot}."
    elif code == "HELIX_SIN_INCIDENTES_RELACIONADOS":
        response_text = (
            f"Helix encontrÃ³ {ot}, pero la orden no tiene incidentes relacionados."
        )
    elif code == "HELIX_SIN_DOCUMENTOS_ADJUNTOS":
        response_text = (
            f"Helix encontrÃ³ {ot}"
            + (f" y el incidente {incident}" if incident else "")
            + ", pero no encontrÃ³ documentos adjuntos."
        )
    elif code == "HELIX_DOCUMENTOS_ADJUNTOS_DETECTADOS":
        listing = "\n".join(f"{index}. {name}" for index, name in enumerate(names, 1))
        response_text = (
            f"Helix encontrÃ³ {len(names)} documento(s) para {ot}"
            + (f" en {incident}" if incident else "")
            + ":\n\n"
            + listing
        )
        if len(names) == 1:
            response_text += (
                f"\n\nPara descargarlo escribe: descargar helix {ot} \"{names[0]}\""
            )
        elif names:
            response_text += (
                f"\n\nIndica cuÃ¡l deseas descargar, por ejemplo: "
                f"descargar helix {ot} \"{names[0]}\""
            )
    elif code == "HELIX_REQUIERE_SELECCION_ADJUNTO":
        listing = "\n".join(f"{index}. {name}" for index, name in enumerate(names, 1))
        response_text = (
            f"Helix encontrÃ³ varios documentos para {ot}. Selecciona uno:\n\n{listing}"
        )
    elif code == "HELIX_ADJUNTOS_DESCARGADOS":
        response_text = (
            f"Documento(s) descargado(s) desde Helix para {ot}: "
            + ", ".join(str(item.get("nombre") or "") for item in downloaded)
        )
    else:
        response_text = (
            str(payload.get("error") or "")
            or f"No fue posible completar la consulta de {ot} en Helix."
        )

    files = [_public_file(item, ot, incident) for item in downloaded]

    return {
        "ok": bool(payload.get("ok")),
        "tipo_respuesta": "helix_redes_neutras",
        "codigo": code,
        "origen": "HELIX",
        "respuesta": response_text,
        "ot": ot,
        "ot_encontrada": bool(payload.get("ot_encontrada")),
        "incidente": incident,
        "incidentes_relacionados": payload.get("incidentes_relacionados") or [],
        "documentos_adjuntos_encontrados": bool(
            payload.get("documentos_adjuntos_encontrados")
        ),
        "cantidad_documentos_adjuntos": int(
            payload.get("cantidad_documentos_adjuntos") or 0
        ),
        "requiere_seleccion": bool(payload.get("requiere_seleccion")),
        "adjuntos": attachments,
        "archivos": files,
        "duracion_seg": payload.get("duracion_seg") or 0,
        "error": payload.get("error") or "",
    }


def consultar_redes_neutras_helix(
    ot: str,
    *,
    descargar: bool = False,
    archivo: str = "",
    todos: bool = False,
) -> dict[str, Any]:
    ot_match = _WO_RE.fullmatch(str(ot or "").strip())
    if not ot_match:
        return {
            "ok": False,
            "tipo_respuesta": "helix_redes_neutras",
            "codigo": "HELIX_OT_INVALIDA",
            "origen": "HELIX",
            "respuesta": "La orden Helix debe tener el formato WO seguido de 13 o 14 dÃ­gitos.",
            "ot": str(ot or ""),
            "adjuntos": [],
            "archivos": [],
        }

    acquired = _HELIX_SEMAPHORE.acquire(timeout=_QUEUE_TIMEOUT_SECONDS)
    if not acquired:
        return {
            "ok": False,
            "tipo_respuesta": "helix_redes_neutras",
            "codigo": "HELIX_QUEUE_TIMEOUT",
            "origen": "HELIX",
            "respuesta": (
                "La consulta no pudo iniciar porque hay otra consulta de Helix en curso. "
                "Intenta nuevamente en unos minutos."
            ),
            "ot": ot_match.group(1).upper(),
            "adjuntos": [],
            "archivos": [],
        }

    started_at = time.time()
    normalized_ot = ot_match.group(1).upper()
    try:
        args = argparse.Namespace(
            ot=normalized_ot,
            incidente="",
            descargar=bool(descargar),
            archivo=str(archivo or "").strip(),
            todos=bool(todos),
            headless=_as_bool("HELIX_CHAT_HEADLESS", False),
            mantener_abierto=0,
        )

        asyncio.run(main_async(args))
        result_file = _latest_result(normalized_ot, started_at)
        if not result_file:
            raise RuntimeError(
                "Helix terminÃ³ sin generar el archivo JSON de resultado."
            )

        payload = json.loads(result_file.read_text(encoding="utf-8"))
        return _build_response(payload)

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        credentials_invalid = "HELIX_CREDENCIALES_INVALIDAS" in str(exc)

        return {
            "ok": False,
            "tipo_respuesta": "helix_redes_neutras",
            "codigo": (
                "HELIX_CREDENCIALES_INVALIDAS"
                if credentials_invalid
                else "HELIX_SERVICE_ERROR"
            ),
            "origen": "HELIX",
            "respuesta": (
                "âš  Helix rechazÃ³ las credenciales configuradas. "
                "Se requiere actualizar el usuario de SmartIT."
                if credentials_invalid
                else "No fue posible completar la consulta en Helix."
            ),
            "requiere_actualizar_credenciales": credentials_invalid,
            "ot": normalized_ot,
            "adjuntos": [],
            "archivos": [],
            "error": error_text,
        }
    finally:
        _HELIX_SEMAPHORE.release()


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

            # HELIX_SESSION_RECOVERY_V1_1
            #
            # Helix puede indicar "Login completado" pero dejar una
            # sesion PWA invalida (ARERR 623, login-light persistente,
            # lupa/input inexistente, PREVIEW roto, etc.).
            #
            # consultar_resumen_async cierra su context al salir.
            # Reinvocarlo crea una sesion de navegador nueva.
            session_retry_used = False

            while True:
                try:
                    payload = asyncio.run(
                        consultar_resumen_async(
                            candidate_ot,
                            headless=_as_bool(
                                "HELIX_CHAT_HEADLESS",
                                True,
                            ),
                        )
                    )

                    last_lookup_exc = None
                    break

                except Exception as lookup_exc:
                    last_lookup_exc = lookup_exc

                    error_text = (
                        f"{type(lookup_exc).__name__}: "
                        f"{lookup_exc}"
                    )

                    upper = error_text.upper()

                    session_transient = bool(
                        # Banner observado visualmente.
                        "HELIX_AUTH_TRANSIENT_623" in upper
                        or "ARERR [623]" in upper
                        or "ARERR[623]" in upper
                        or "46237202" in upper

                        # Sesion PWA que vuelve al login.
                        or "LOGIN-LIGHT.HTML" in upper

                        # Caso real certificado 2026-09-18:
                        # login OK aparente, pero nunca aparece la lupa.
                        or (
                            "NO SE ENCONTR" in upper
                            and "LUPA DE B" in upper
                            and "SQUEDA GLOBAL" in upper
                        )

                        # Mismo problema si la lupa aparece pero no
                        # llega a construirse el input global.
                        or (
                            "NO SE ENCONTR" in upper
                            and "INPUT DE B" in upper
                            and "SQUEDA GLOBAL" in upper
                        )

                        # PWA autenticada a medias.
                        or "QUEDO EN PREVIEW" in upper
                        or "QUEDÓ EN PREVIEW" in upper

                        # Context que Helix mata durante el fallo.
                        or (
                            "TARGET PAGE, CONTEXT OR BROWSER "
                            "HAS BEEN CLOSED"
                            in upper
                        )
                    )

                    if (
                        session_transient
                        and not session_retry_used
                    ):
                        session_retry_used = True

                        print(
                            "HELIX_SESSION_RECOVERY_V1_1 "
                            f"wo={candidate_ot} "
                            "accion=CERRAR_REABRIR "
                            "retry=1/1 "
                            f"causa={error_text[:180]}",
                            flush=True,
                        )

                        # Pequeña separación para que Windows/libere
                        # completamente Chromium/profile handles.
                        time.sleep(2.0)
                        continue

                    if (
                        session_transient
                        and session_retry_used
                    ):
                        raise RuntimeError(
                            "HELIX_SESSION_TRANSIENT_FAIL: "
                            "Helix fallo dos veces consecutivas "
                            "despues de cerrar y reabrir navegador. "
                            f"ultimo_error={error_text}"
                        ) from lookup_exc

                    # HELIX_NO_RESULTS_FAST_FALLBACK_V1
                    #
                    # SmartIT ya confirmo de forma estable que la WO
                    # no existe en Helix. No se recrea una sesion
                    # completa del navegador por este resultado.
                    #
                    # El flujo exterior continua con la variante de
                    # WO siguiente, si existe.
                    #
                    # Los errores tecnicos/transitorios conservan
                    # HELIX_SESSION_RECOVERY_V1_1.
                    work_order_not_found = (
                        "WorkOrderNotFoundError"
                        in error_text
                    )

                    if work_order_not_found:
                        print(
                            "HELIX_NO_RESULTS_FAST_FALLBACK_V1 "
                            f"wo={candidate_ot} "
                            "accion=SIGUIENTE_CANDIDATO",
                            flush=True,
                        )
                        break

                    raise

            if payload is not None:
                break

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
            # HELIX_SERVICE_DESCRIPCION_OT_V1
            "descripcion_ot",
            # HELIX_SERVICE_CGE_CI_FIELDS_V2
            "ci",
            "descripcion_ci",
            # HELIX_SERVICE_IMPACT_TRUNK_V1
            "descripcion_impacto",
            "impacto_gpon",
            "impacto_id_troncal",
            "impacto_descripcion_troncal",
            "impacto_nombre_comercial",
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

            # HELIX_SERVICE_GES_FIELDS_V1
            # Campos producidos por summary.py para gestiones GES.
            # Solo propagacion de contrato: no ejecuta logica adicional.
            "es_ges",
            "ges_direcciones",
            "ges_fuentes_notas",
            "ges_notas_revisadas",
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
                "âš  Helix rechazÃ³ las credenciales configuradas. "
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
    normalized_ot = str(ot or "").strip().upper()
    if not _WO_RE.fullmatch(normalized_ot):
        return _consultar_resumen_ot_helix_base(ot)

    try:
        oracle_result = consultar_resumen_ot_oracle(normalized_ot)
        if oracle_result.get("ok") and oracle_result.get("codigo") != "ORACLE_GES_SMARTIT_REQUIRED":
            return oracle_result
        if oracle_result.get("codigo") == "ORACLE_GES_SMARTIT_REQUIRED":
            print(f"DIRECCIONES_ORACLE wo={normalized_ot} status=GES_SMARTIT_ENRICHMENT", flush=True)
        else:
            print(f"DIRECCIONES_ORACLE wo={normalized_ot} status=FALLBACK_SMARTIT code={oracle_result.get('codigo')}", flush=True)
    except Exception as exc:
        print(
            f"DIRECCIONES_ORACLE wo={normalized_ot} status=FALLBACK_SMARTIT error={type(exc).__name__}",
            flush=True,
        )
    smartit_result = _consultar_resumen_ot_helix_base(normalized_ot)
    smartit_result.setdefault("data", {})["fuente_resumen"] = "SMARTIT_FALLBACK"
    return smartit_result


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
        # DIRECCIONES_HELIX_RELACIONADOS_LAZY_IMPORT_V1
        # Esta capacidad no pertenece al arranque normal de Direcciones.
        # Solo se carga cuando se solicita explícitamente el flujo de
        # relacionados de un incidente.
        from app.services.helix.runtime.incident_related_items import (
            consultar_relacionados_incidente_async,
        )

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
