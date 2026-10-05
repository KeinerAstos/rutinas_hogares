from __future__ import annotations

import os
import re
import threading
import time
import unicodedata

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from .client import MaximoClient
from .config import settings


_MAXIMO_CONCURRENCY = max(
    1,
    int(os.getenv("MAXIMO_MAX_CONCURRENCY", "2")),
)

_MAXIMO_QUEUE_TIMEOUT = max(
    10,
    int(os.getenv("MAXIMO_QUEUE_TIMEOUT", "180")),
)

_MAXIMO_SEMAPHORE = threading.BoundedSemaphore(
    _MAXIMO_CONCURRENCY
)

_OT_RE = re.compile(
    r"\bOT\s*[-:]?\s*(\d{6,10})\b",
    re.IGNORECASE,
)

_TRIGGER_RE = re.compile(
    r"\b("
    r"evidencia|evidencias|"
    r"adjunto|adjuntos|"
    r"archivo|archivos|"
    r"documento|documentos|"
    r"maximo|máximo|"
    r"redes\s+neutras?"
    r")\b",
    re.IGNORECASE,
)


def detectar_consulta_maximo(
    mensaje: str,
) -> Optional[str]:
    texto = str(mensaje or "").strip()

    ot_match = _OT_RE.search(texto)

    if not ot_match:
        return None

    if not _TRIGGER_RE.search(texto):
        return None

    return f"OT{ot_match.group(1)}".upper()


def _archivo_publico(
    attachment: Dict[str, Any],
) -> Dict[str, Any]:
    saved_path_text = str(
        attachment.get("saved_path") or ""
    ).strip()

    if not saved_path_text:
        return {
            **attachment,
            "filename": None,
            "download_url": None,
        }

    saved_path = Path(saved_path_text).resolve()
    base = settings.downloads_dir.resolve()

    try:
        saved_path.relative_to(base)
    except ValueError:
        return {
            **attachment,
            "filename": saved_path.name,
            "download_url": None,
        }

    filename = saved_path.name

    return {
        **attachment,
        "filename": filename,
        "download_url": (
            "/CentralNOC/modules/Dashboard_Hogar/"
            "api/maximo_file.php?filename="
            + quote(filename)
        ),
    }


def consultar_evidencia_maximo(
    ot: str,
) -> Dict[str, Any]:
    inicio = time.perf_counter()

    acquired = _MAXIMO_SEMAPHORE.acquire(
        timeout=_MAXIMO_QUEUE_TIMEOUT
    )

    if not acquired:
        return {
            "ok": False,
            "tipo_respuesta": "maximo_evidence",
            "codigo": "MAXIMO_QUEUE_TIMEOUT",
            "ot": ot,
            "respuesta": (
                "La consulta no pudo iniciar porque hay otras "
                "consultas de Máximo en curso. Intenta nuevamente."
            ),
            "archivos": [],
        }

    try:
        with MaximoClient() as client:
            result = client.validate(
                ot,
                login=True,
            )

        data = asdict(result)

        incidents = data.get("incidents") or []
        incident_key = (
            incidents[0].get("key")
            if incidents
            else None
        )

        attachments = [
            _archivo_publico(item)
            for item in (
                data.get("attachments") or []
            )
        ]

        source = (
            attachments[0].get("note_source")
            if attachments
            else None
        )

        duration = round(
            time.perf_counter() - inicio,
            2,
        )

        code = str(data.get("code") or "")
        ok = (
            bool(data.get("ok"))
            and code != "MAXIMO_AUTOMATION_ERROR"
        )

        if code == "EVIDENCE_DOWNLOADED":
            respuesta = (
                f"Evidencia encontrada para {ot}. "
                f"Se descargaron "
                f"{len(attachments)} archivo(s)."
            )
        elif code == "NO_EVIDENCE_FOUND":
            respuesta = (
                f"Se revisaron "
                f"{data.get('notes_checked', 0)} "
                f"nota(s) de {ot}, pero no se "
                f"encontraron documentos adjuntos."
            )
        else:
            respuesta = (
                data.get("response_text")
                or data.get("error")
                or "No fue posible completar la consulta."
            )

        return {
            "ok": ok,
            "tipo_respuesta": "maximo_evidence",
            "codigo": code,
            "respuesta": respuesta,
            "ot": ot,
            "incidente": incident_key,
            "clasificacion": (
                data.get("classification") or ""
            ),
            "notas_revisadas": (
                data.get("notes_checked") or 0
            ),
            "notas_con_adjuntos": (
                data.get("notes_with_attachments") or 0
            ),
            "fuente": source,
            "archivos": attachments,
            "duracion_seg": duration,
            "error": data.get("error") or "",
        }

    except Exception as exc:
        return {
            "ok": False,
            "tipo_respuesta": "maximo_evidence",
            "codigo": "MAXIMO_SERVICE_ERROR",
            "ot": ot,
            "respuesta": (
                "No fue posible completar la consulta "
                "en Máximo."
            ),
            "archivos": [],
            "error": str(exc),
            "duracion_seg": round(
                time.perf_counter() - inicio,
                2,
            ),
        }

    finally:
        _MAXIMO_SEMAPHORE.release()

# =============================================================================
# PATCH_DIRECCIONES_CLIENTES_V1
# OT -> Maximo Impacto -> serial -> ACS/mycust04 -> Diagnosticador -> direccion
# =============================================================================

import json as _dir_json
import subprocess as _dir_subprocess
import sys as _dir_sys


_DIRECCIONES_TRIGGER_RE = re.compile(
    r"\b(?:"
    r"cuentas?\s+afectadas?|"
    r"clientes?\s+afectados?|"
    r"direcci(?:o|ó)n(?:es)?\s+afectadas?|"
    r"direcci(?:o|ó)n(?:es)?\s+de\s+clientes?|"
    r"cuentas?\s+y\s+direcci(?:o|ó)n(?:es)?|"
    r"vecinos?|"
    r"diagnosticar(?:\s+cliente)?|"
    r"buscar\s+cliente"
    r")\b",
    re.IGNORECASE,
)

_CUENTA_DIRECTA_RE = re.compile(
    r"\b(?:cuenta(?:\s+rr)?|c\.?\s*rr|cliente)"
    r"\s*[:#-]?\s*(\d{5,15})\b",
    re.IGNORECASE,
)

_SERIAL_DIRECTO_RE = re.compile(
    r"\b(?:serial(?:\s+ont)?|s\/n|sn)"
    r"\s*[:#-]?\s*([A-Za-z0-9]{6,32})\b",
    re.IGNORECASE,
)

_MAC_DIRECTA_RE = re.compile(
    r"\bmac(?:\s+address|\s+ont)?"
    r"\s*[:#-]?\s*"
    r"([0-9A-Fa-f]{2}(?:(?::|-)[0-9A-Fa-f]{2}){5}|"
    r"[0-9A-Fa-f]{12})\b",
    re.IGNORECASE,
)

_NUMERO_CUENTA_RE = re.compile(
    r"\b(\d{5,15})\b",
    re.IGNORECASE,
)

_NUMERO_CUENTA_SOLO_RE = re.compile(
    r"^\s*(\d{5,15})\s*$",
    re.IGNORECASE,
)

_DIRECCIONES_CONTEXTOS = {}
_DIRECCIONES_CONTEXTOS_LOCK = threading.RLock()
_DIRECCIONES_CONTEXTOS_TTL = 600


def _limpiar_contextos_direcciones():
    now = time.monotonic()
    expired = [
        key
        for key, value in _DIRECCIONES_CONTEXTOS.items()
        if now - value > _DIRECCIONES_CONTEXTOS_TTL
    ]
    for key in expired:
        _DIRECCIONES_CONTEXTOS.pop(key, None)


def _normalizar_cuenta_diagnosticador(value: str) -> str:
    cuenta = re.sub(
        r"\D+",
        "",
        str(value or ""),
    )

    if not re.fullmatch(r"\d{5,15}", cuenta):
        raise ValueError(
            "La cuenta RR debe contener entre 5 y 15 dígitos."
        )

    return cuenta


def _normalizar_identificador_acs(
    value: str,
    search_by: str,
) -> str:
    tipo = str(search_by or "").strip().lower()

    if tipo == "mac":
        identificador = re.sub(
            r"[^0-9A-Fa-f]+",
            "",
            str(value or ""),
        ).upper()

        if not re.fullmatch(r"[0-9A-F]{12}", identificador):
            raise ValueError(
                "La MAC debe contener exactamente 12 caracteres hexadecimales."
            )

        return identificador

    if tipo == "serial":
        identificador = re.sub(
            r"[^A-Za-z0-9]+",
            "",
            str(value or ""),
        ).upper()

        if not re.fullmatch(r"[A-Z0-9]{6,32}", identificador):
            raise ValueError(
                "El serial debe contener entre 6 y 32 caracteres alfanuméricos."
            )

        return identificador

    raise ValueError(
        f"Tipo de búsqueda ACS no soportado: {search_by}"
    )


def detectar_consulta_direcciones(
    mensaje: str,
    conversation_id: Optional[str] = None,
):
    """
    Detecta cuatro flujos:

    1. OT -> Máximo -> ACS -> Diagnosticador.
    2. Cuenta RR -> Diagnosticador.
    3. Serial -> ACS -> Diagnosticador.
    4. MAC -> ACS -> Diagnosticador.

    El contexto se conserva por 10 minutos para que el usuario pueda
    abrir Dirección de clientes y responder después con el identificador.
    """
    text = str(mensaje or "").strip()
    context_id = str(conversation_id or "default").strip() or "default"

    ot_match = _OT_RE.search(text)
    account_match = _CUENTA_DIRECTA_RE.search(text)
    serial_match = _SERIAL_DIRECTO_RE.search(text)
    mac_match = _MAC_DIRECTA_RE.search(text)
    has_trigger = bool(_DIRECCIONES_TRIGGER_RE.search(text))

    with _DIRECCIONES_CONTEXTOS_LOCK:
        _limpiar_contextos_direcciones()
        pending = context_id in _DIRECCIONES_CONTEXTOS

        # Una OT siempre tiene prioridad sobre números contenidos en el texto.
        if ot_match and (has_trigger or pending):
            _DIRECCIONES_CONTEXTOS.pop(context_id, None)
            return {
                "accion": "ejecutar_ot",
                "ot": f"OT{ot_match.group(1)}".upper(),
                "origen": "OT",
            }

        if account_match:
            _DIRECCIONES_CONTEXTOS.pop(context_id, None)
            return {
                "accion": "ejecutar_cuenta",
                "cuenta": _normalizar_cuenta_diagnosticador(
                    account_match.group(1)
                ),
                "origen": "CUENTA_DIRECTA",
            }

        # Evaluar MAC antes que serial para evitar tratar una MAC compacta
        # explícitamente marcada como serial.
        if mac_match:
            _DIRECCIONES_CONTEXTOS.pop(context_id, None)
            return {
                "accion": "ejecutar_acs",
                "identificador": _normalizar_identificador_acs(
                    mac_match.group(1),
                    "mac",
                ),
                "search_by": "mac",
                "origen": "ACS_MAC",
            }

        if serial_match:
            _DIRECCIONES_CONTEXTOS.pop(context_id, None)
            return {
                "accion": "ejecutar_acs",
                "identificador": _normalizar_identificador_acs(
                    serial_match.group(1),
                    "serial",
                ),
                "search_by": "serial",
                "origen": "ACS_SERIAL",
            }

        # En contexto, un número desnudo conserva el comportamiento previo:
        # se interpreta como cuenta RR. Serial y MAC deben llevar su prefijo
        # para evitar ambigüedad.
        if pending:
            bare_account = _NUMERO_CUENTA_SOLO_RE.fullmatch(text)
            if bare_account:
                _DIRECCIONES_CONTEXTOS.pop(context_id, None)
                return {
                    "accion": "ejecutar_cuenta",
                    "cuenta": _normalizar_cuenta_diagnosticador(
                        bare_account.group(1)
                    ),
                    "origen": "CUENTA_DIRECTA",
                }

        if has_trigger and not ot_match:
            generic_account = _NUMERO_CUENTA_RE.search(text)
            if generic_account:
                _DIRECCIONES_CONTEXTOS.pop(context_id, None)
                return {
                    "accion": "ejecutar_cuenta",
                    "cuenta": _normalizar_cuenta_diagnosticador(
                        generic_account.group(1)
                    ),
                    "origen": "CUENTA_DIRECTA",
                }

        if has_trigger:
            _DIRECCIONES_CONTEXTOS[context_id] = time.monotonic()
            return {
                "accion": "esperar_identificador",
                "ot": None,
                "cuenta": None,
                "identificador": None,
                "search_by": None,
            }

    return None


def _script_configurado(name: str) -> Path:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta configurar {name} en .env.")

    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"No existe el script configurado en {name}: {path}")
    return path


def _extraer_ultimo_json(stdout: str):
    text = str(stdout or "").strip().lstrip("\ufeff")
    decoder = _dir_json.JSONDecoder()
    candidates = []

    for index, char in enumerate(text):
        if char != "{":
            continue

        try:
            value, _ = decoder.raw_decode(text[index:])
        except Exception:
            continue

        if isinstance(value, dict):
            candidates.append(value)

    if not candidates:
        return None

    # Elegir el JSON principal, no un diccionario interno como
    # selectores.search o subscriber_info.
    for value in candidates:
        if (
            "ok" in value
            and "estado" in value
            and (
                "datos" in value
                or "mensaje" in value
                or "serial" in value
            )
        ):
            return value

    return max(candidates, key=lambda value: len(value))

def _ejecutar_script_json(script: Path, args):
    timeout = max(
        30,
        min(180, int(os.getenv("DECO_DIRECCIONES_TIMEOUT", "180"))),
    )

    try:
        process = _dir_subprocess.run(
            [_dir_sys.executable, str(script), *args],
            # Se conserva el directorio del backend para cargar su .env.
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except _dir_subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{script.name} supero el limite de {timeout} segundos."
        ) from exc

    stdout = (process.stdout or "").strip().lstrip("\ufeff")
    stderr = (process.stderr or "").strip()
    result = _extraer_ultimo_json(stdout)

    if result is None:
        detail = stderr[-500:] or stdout[-500:] or f"codigo {process.returncode}"
        raise RuntimeError(f"{script.name} no devolvio JSON valido: {detail}")

    return result


def _buscar_valor(obj, keys):
    normalized = {str(key).lower() for key in keys}

    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in normalized and str(value or "").strip():
                return str(value).strip()
        for value in obj.values():
            found = _buscar_valor(value, normalized)
            if found:
                return found

    if isinstance(obj, list):
        for value in obj:
            found = _buscar_valor(value, normalized)
            if found:
                return found

    return ""


def _consultar_cuenta_acs(value: str, search_by: str = "serial"):
    script = _script_configurado("ACS_TR069_SCRIPT")
    result = _ejecutar_script_json(
        script,
        [
            "--serial", value,
            "--search-by", search_by,
            "--headless",
            "--no-ipping",
            "--hold-seconds", "0",
            "--json-output",
        ],
    )

    account = _buscar_valor(
        result,
        {"mycust04", "cuenta", "cuenta_rr", "cuenta matriz"},
    )
    return account, result


def _consultar_direccion_diagnosticador(account: str):
    script = _script_configurado("DIAGNOSTICADOR_SCRIPT")
    result = _ejecutar_script_json(
        script,
        [
            "--url", str(os.getenv("DIAGNOSTICADOR_URL") or "").strip(),
            "--query-type", "cuenta",
            "--query", account,
            "--hold-seconds", "0",
            # ATLAS_VECINOS_PRIORITY_V1
            "--vecinos-priority",
            "--json-output",
        ],
    )

    if not isinstance(result, dict):
        raise RuntimeError("Diagnosticador devolvio una respuesta JSON inesperada.")

    datos = result.get("datos") or {}
    if not isinstance(datos, dict):
        datos = {}

    vecinos_raw = datos.get("vecinos") or []
    if not isinstance(vecinos_raw, list):
        vecinos_raw = []

    def normalizar_clave(value: Any) -> str:
        value = unicodedata.normalize("NFKD", str(value or ""))
        value = "".join(char for char in value if not unicodedata.combining(char))
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")

    def valor_fila(fila: Dict[str, Any], *aliases: str) -> str:
        normalizada = {
            normalizar_clave(key): value
            for key, value in fila.items()
        }
        for alias in aliases:
            value = normalizada.get(normalizar_clave(alias))
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    vecinos = []
    vistos = set()

    for fila in vecinos_raw:
        if not isinstance(fila, dict):
            continue

        mac = valor_fila(fila, "mac", "mac address", "direccion mac")
        cuenta_rr = valor_fila(
            fila,
            "cuenta_rr",
            "cuenta",
            "c_rr",
            "c. rr",
            "c.rr",
            "cuenta rr",
        )
        direccion = valor_fila(
            fila,
            "direccion",
            "dirección",
            "direccion cliente",
            "direccion instalacion",
        )

        if not mac and not cuenta_rr and not direccion:
            continue

        clave = (mac.upper(), cuenta_rr, direccion.upper())
        if clave in vistos:
            continue
        vistos.add(clave)

        vecinos.append(
            {
                "mac": mac,
                "cuenta_rr": cuenta_rr,
                "direccion": direccion,
            }
        )

    return vecinos, result




def consultar_direcciones_por_acs(
    identificador: str,
    search_by: str,
) -> Dict[str, Any]:
    """
    Consulta:

        Serial/MAC -> ACS -> cuenta RR -> Diagnosticador -> Vecinos.
    """
    started = time.perf_counter()
    tipo = str(search_by or "").strip().lower()
    value = _normalizar_identificador_acs(
        identificador,
        tipo,
    )
    tipo_label = "MAC" if tipo == "mac" else "SERIAL"
    origen = "ACS_MAC" if tipo == "mac" else "ACS_SERIAL"

    acquired = _MAXIMO_SEMAPHORE.acquire(
        timeout=_MAXIMO_QUEUE_TIMEOUT
    )

    if not acquired:
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes",
            "codigo": "ACS_QUEUE_TIMEOUT",
            "origen_consulta": origen,
            "tipo_identificador": tipo_label,
            "identificador_consultado": value,
            "respuesta": (
                "La consulta no pudo iniciar porque hay otras "
                "consultas de ACS o Diagnosticador en curso."
            ),
            "clientes": [],
        }

    try:
        print(
            f"[DIRECCIONES][ACS] Consultando {tipo}={value} "
            "para obtener cuenta RR.",
            flush=True,
        )

        account, acs_result = _consultar_cuenta_acs(
            value,
            search_by=tipo,
        )

        estado_acs = ""
        if isinstance(acs_result, dict):
            estado_acs = str(
                acs_result.get("estado") or ""
            )

        if not account:
            return {
                "ok": False,
                "tipo_respuesta": "direccion_clientes",
                "codigo": "ACS_SIN_CUENTA",
                "origen_consulta": origen,
                "tipo_identificador": tipo_label,
                "identificador_consultado": value,
                "cuenta_consulta": "",
                "flujo_ejecutado": ["ACS"],
                "estado_acs": estado_acs,
                "respuesta": (
                    f"ACS recibió el {tipo_label.lower()} {value}, "
                    "pero no devolvió una cuenta RR."
                ),
                "clientes": [],
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }

        print(
            f"[DIRECCIONES][ACS] Cuenta {account} encontrada. "
            "Consultando Diagnosticador y Vecinos.",
            flush=True,
        )

        vecinos, diagnosticador_result = (
            _consultar_direccion_diagnosticador(account)
        )

        clients = []

        for vecino in vecinos:
            cuenta_rr = str(
                vecino.get("cuenta_rr") or ""
            ).strip()

            clients.append(
                {
                    "serial": value if tipo == "serial" else "",
                    "identificador_consultado": value,
                    "tipo_identificador": tipo_label,
                    "cuenta_consulta": account,
                    "cuenta_rr": cuenta_rr,
                    "mac": str(
                        vecino.get("mac") or ""
                    ).strip(),
                    "direccion": str(
                        vecino.get("direccion") or ""
                    ).strip(),
                    "estado": "OK",
                    "error": "",
                    "es_equipo_consultado": (
                        cuenta_rr == account
                    ),
                }
            )

        valid = [
            item
            for item in clients
            if (
                item.get("cuenta_rr")
                or item.get("mac")
                or item.get("direccion")
            )
        ]

        estado_diagnosticador = ""
        if isinstance(diagnosticador_result, dict):
            estado_diagnosticador = str(
                diagnosticador_result.get("estado") or ""
            )

        lines = [
            f"Dirección obtenida por {tipo_label}: {value}",
            f"Cuenta RR encontrada: {account}",
            "",
        ]

        for item in valid:
            marker = (
                " [CUENTA CONSULTADA]"
                if item.get("es_equipo_consultado")
                else ""
            )

            lines.append(
                f"{item.get('mac') or 'SIN MAC'} // "
                f"{item.get('cuenta_rr') or 'SIN CUENTA'} // "
                f"{item.get('direccion') or 'SIN DIRECCION'}"
                f"{marker}"
            )

        if not valid:
            lines.append(
                "Diagnosticador no devolvió filas en la tabla Vecinos."
            )

        return {
            "ok": bool(valid),
            "tipo_respuesta": "direccion_clientes",
            "codigo": (
                "DIRECCIONES_ACS_OK"
                if valid
                else "DIRECCIONES_ACS_SIN_DATOS"
            ),
            "ot": "",
            "cuenta_consulta": account,
            "origen_consulta": origen,
            "tipo_identificador": tipo_label,
            "identificador_consultado": value,
            "flujo_ejecutado": [
                "ACS",
                "DIAGNOSTICADOR",
            ],
            "estado_acs": estado_acs,
            "estado_diagnosticador": estado_diagnosticador,
            "respuesta": "\n".join(lines),
            "clientes_encontrados": len(valid),
            "clientes": valid,
            "estado_consulta": (
                "OK"
                if valid
                else "DIAGNOSTICADOR_SIN_VECINOS"
            ),
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    except Exception as exc:
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes",
            "codigo": "DIRECCIONES_ACS_ERROR",
            "ot": "",
            "cuenta_consulta": "",
            "origen_consulta": origen,
            "tipo_identificador": tipo_label,
            "identificador_consultado": value,
            "flujo_ejecutado": ["ACS"],
            "respuesta": (
                f"No fue posible consultar el {tipo_label.lower()} "
                f"{value} mediante ACS."
            ),
            "clientes": [],
            "error": f"{type(exc).__name__}: {exc}",
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    finally:
        _MAXIMO_SEMAPHORE.release()

def consultar_direcciones_por_cuenta(
    cuenta: str,
) -> Dict[str, Any]:
    """
    Consulta directa:

        Cuenta RR -> Diagnosticador -> tabla Vecinos.

    Este flujo no abre Máximo y no consulta ACS.
    """
    started = time.perf_counter()
    account = _normalizar_cuenta_diagnosticador(cuenta)

    acquired = _MAXIMO_SEMAPHORE.acquire(
        timeout=_MAXIMO_QUEUE_TIMEOUT
    )

    if not acquired:
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes",
            "codigo": "DIAGNOSTICADOR_QUEUE_TIMEOUT",
            "cuenta_consulta": account,
            "origen_consulta": "CUENTA_DIRECTA",
            "respuesta": (
                "La consulta directa no pudo iniciar porque hay "
                "otras consultas de Diagnosticador en curso."
            ),
            "clientes": [],
        }

    try:
        print(
            f"[DIRECCIONES][CUENTA] Consultando Diagnosticador "
            f"directamente con cuenta {account}.",
            flush=True,
        )

        vecinos, diagnosticador_result = (
            _consultar_direccion_diagnosticador(account)
        )

        clients = []

        for vecino in vecinos:
            cuenta_rr = str(
                vecino.get("cuenta_rr") or ""
            ).strip()

            clients.append(
                {
                    "serial": "",
                    "cuenta_consulta": account,
                    "cuenta_rr": cuenta_rr,
                    "mac": str(
                        vecino.get("mac") or ""
                    ).strip(),
                    "direccion": str(
                        vecino.get("direccion") or ""
                    ).strip(),
                    "estado": "OK",
                    "error": "",
                    "es_equipo_consultado": (
                        cuenta_rr == account
                    ),
                }
            )

        valid = [
            item
            for item in clients
            if (
                item.get("cuenta_rr")
                or item.get("mac")
                or item.get("direccion")
            )
        ]

        lines = [
            f"Dirección y vecinos de la cuenta {account}",
            "",
        ]

        for item in valid:
            marker = (
                " [CUENTA CONSULTADA]"
                if item.get("es_equipo_consultado")
                else ""
            )

            lines.append(
                f"{item.get('mac') or 'SIN MAC'} // "
                f"{item.get('cuenta_rr') or 'SIN CUENTA'} // "
                f"{item.get('direccion') or 'SIN DIRECCION'}"
                f"{marker}"
            )

        if not valid:
            lines.append(
                "Diagnosticador no devolvió filas en la tabla Vecinos."
            )

        diagnosticador_estado = ""
        if isinstance(diagnosticador_result, dict):
            diagnosticador_estado = str(
                diagnosticador_result.get("estado") or ""
            )

        return {
            "ok": bool(valid),
            "tipo_respuesta": "direccion_clientes",
            "codigo": (
                "DIRECCIONES_CUENTA_OK"
                if valid
                else "DIRECCIONES_CUENTA_SIN_DATOS"
            ),
            "ot": "",
            "cuenta_consulta": account,
            "origen_consulta": "CUENTA_DIRECTA",
            "flujo_ejecutado": ["DIAGNOSTICADOR"],
            "respuesta": "\n".join(lines),
            "clientes_encontrados": len(valid),
            "clientes": valid,
            "estado_consulta": (
                "OK"
                if valid
                else "DIAGNOSTICADOR_SIN_VECINOS"
            ),
            "estado_diagnosticador": diagnosticador_estado,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    except Exception as exc:
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes",
            "codigo": "DIRECCIONES_CUENTA_ERROR",
            "ot": "",
            "cuenta_consulta": account,
            "origen_consulta": "CUENTA_DIRECTA",
            "flujo_ejecutado": ["DIAGNOSTICADOR"],
            "respuesta": (
                f"No fue posible consultar la cuenta {account} "
                "directamente en Diagnosticador."
            ),
            "clientes": [],
            "error": f"{type(exc).__name__}: {exc}",
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    finally:
        _MAXIMO_SEMAPHORE.release()

def consultar_direcciones_clientes(ot: str) -> Dict[str, Any]:
    started = time.perf_counter()
    acquired = _MAXIMO_SEMAPHORE.acquire(
        timeout=_MAXIMO_QUEUE_TIMEOUT
    )

    if not acquired:
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes",
            "codigo": "DIRECCIONES_QUEUE_TIMEOUT",
            "ot": ot,
            "respuesta": "Hay otras consultas de Máximo en curso. Intenta nuevamente.",
            "clientes": [],
        }

    try:
        with MaximoClient() as client:
            client.login_maximo()
            client.go_to_work_orders()
            client.search_ot(ot)
            serials = client.extract_impact_serials()

        if not serials:
            return {
                "ok": False,
                "tipo_respuesta": "direccion_clientes",
                "codigo": "IMPACTO_SIN_SERIALES",
                "ot": ot,
                "respuesta": f"Máximo abrió {ot}, pero Impacto no mostró seriales.",
                "clientes": [],
            }

        # Probar seriales en orden hasta que ACS entregue una cuenta RR.
        # En cuanto se obtiene una cuenta, detener el recorrido y consultar
        # Diagnosticador una sola vez.
        print(
            f"[DIRECCIONES] Maximo encontro {len(serials)} seriales; "
            "se probaran en orden hasta obtener una cuenta en ACS.",
            flush=True,
        )

        clients = []
        attempts = []
        selected = None

        for position, candidate in enumerate(serials, start=1):
            if isinstance(candidate, dict):
                serial = str(candidate.get("value") or "").strip()
                search_by = str(candidate.get("search_by") or "serial").strip().lower()
                source = str(candidate.get("source") or "").strip()
                cuenta_maximo = str(candidate.get("cuenta_maximo") or "").strip()
            else:
                serial = str(candidate or "").strip()
                search_by = "serial"
                source = "LEGACY"
                cuenta_maximo = ""

            item = {
                "serial": serial,
                "search_by": search_by,
                "fuente": source,
                "cuenta_maximo": cuenta_maximo,
                "cuenta_rr": "",
                "mac": "",
                "direccion": "",
                "estado": "PENDIENTE",
                "error": "",
            }

            print(
                f"[DIRECCIONES] Intento ACS {position}/{len(serials)}: "
                f"{search_by}={serial}...",
                flush=True,
            )

            try:
                account, acs_result = _consultar_cuenta_acs(serial, search_by)
                item["cuenta_rr"] = account
                item["estado_acs"] = str(acs_result.get("estado") or "")

                if not account:
                    item["estado"] = (
                        "ACS_SIN_RESULTADOS"
                        if item["estado_acs"] == "ACS_SIN_RESULTADOS"
                        else "ACS_SIN_CUENTA"
                    )
                    item["error"] = (
                        "ACS indico No data found."
                        if item["estado"] == "ACS_SIN_RESULTADOS"
                        else "ACS no devolvio mycust04."
                    )
                    attempts.append(item)

                    # Si falla como serial, usar exactamente el mismo valor,
                    # pero seleccionar MAC como tipo de busqueda en ACS.
                    compact_candidate = re.sub(
                        r"[^0-9A-F]",
                        "",
                        serial.upper(),
                    )

                    if (
                        search_by != "mac"
                        and re.fullmatch(
                            r"[0-9A-F]{12}",
                            compact_candidate,
                        )
                    ):
                        print(
                            f"[DIRECCIONES] serial={serial}: sin cuenta; "
                            f"reintentando el mismo valor como mac={serial}.",
                            flush=True,
                        )

                        mac_item = dict(item)
                        mac_item["search_by"] = "mac"
                        mac_item["cuenta_rr"] = ""
                        mac_item["estado"] = "PENDIENTE"
                        mac_item["estado_acs"] = ""
                        mac_item["error"] = ""

                        account, acs_result = _consultar_cuenta_acs(
                            serial,
                            "mac",
                        )

                        mac_item["cuenta_rr"] = account
                        mac_item["estado_acs"] = str(
                            acs_result.get("estado") or ""
                        )

                        if not account:
                            mac_item["estado"] = (
                                "ACS_SIN_RESULTADOS"
                                if mac_item["estado_acs"] == "ACS_SIN_RESULTADOS"
                                else "ACS_SIN_CUENTA"
                            )
                            mac_item["error"] = (
                                "ACS indico No data found."
                                if mac_item["estado"] == "ACS_SIN_RESULTADOS"
                                else "ACS no devolvio mycust04."
                            )
                            attempts.append(mac_item)

                            print(
                                f"[DIRECCIONES] mac={serial}: sin cuenta; "
                                "probando siguiente identificador.",
                                flush=True,
                            )
                            continue

                        item = mac_item
                        search_by = "mac"
                    else:
                        print(
                            f"[DIRECCIONES] mac={serial}: sin cuenta; "
                            "probando siguiente identificador.",
                            flush=True,
                        )
                        continue

                item["estado"] = "ACS_OK"
                selected = item
                print(
                    f"[DIRECCIONES] {search_by}={serial}: cuenta {account} encontrada. "
                    "Se detiene la busqueda en ACS.",
                    flush=True,
                )
                break
            except Exception as exc:
                item["estado"] = "ERROR_ACS"
                item["error"] = f"{type(exc).__name__}: {exc}"
                attempts.append(item)

                print(
                    f"[DIRECCIONES] {search_by}={serial}: fallo ACS: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

        if selected is not None:
            serial = selected["serial"]
            account = selected["cuenta_rr"]
            print(
                f"[DIRECCIONES] {serial}: consultando Diagnosticador "
                f"con cuenta {account} y abriendo Vecinos...",
                flush=True,
            )
            try:
                vecinos, _ = _consultar_direccion_diagnosticador(account)

                clients = [
                    {
                        "serial": serial,
                        "cuenta_consulta": account,
                        "cuenta_rr": vecino.get("cuenta_rr", ""),
                        "mac": vecino.get("mac", ""),
                        "direccion": vecino.get("direccion", ""),
                        "estado": "OK",
                        "error": "",
                    }
                    for vecino in vecinos
                ]

                selected["estado"] = (
                    "OK" if clients else "DIAGNOSTICADOR_SIN_VECINOS"
                )
                if not clients:
                    selected["error"] = (
                        "Diagnosticador no devolvio filas de la tabla Vecinos."
                    )
            except Exception as exc:
                clients = []
                selected["estado"] = "ERROR_DIAGNOSTICADOR"
                selected["error"] = f"{type(exc).__name__}: {exc}"

            print(
                f"[DIRECCIONES] {serial}: {selected['estado']} | "
                f"vecinos encontrados={len(clients)}",
                flush=True,
            )
        else:
            print(
                "[DIRECCIONES] Ningun serial permitio obtener una cuenta RR en ACS.",
                flush=True,
            )

        valid = [
            item for item in clients
            if item.get("cuenta_rr") or item.get("mac") or item.get("direccion")
        ]

        lines = [f"Cuentas y direcciones afectadas - {ot}", ""]
        for item in clients:
            lines.append(
                f"{item.get('mac') or 'SIN MAC'} // "
                f"{item.get('cuenta_rr') or 'SIN CUENTA'} // "
                f"{item.get('direccion') or 'SIN DIRECCION'}"
            )

        resultado_parcial = bool(
            selected
            and selected.get("cuenta_rr")
        )

        if not clients:
            if resultado_parcial:
                lines.append(
                    "ACS encontr? la cuenta "
                    f"{selected.get('cuenta_rr')}, "
                    "pero el m?dulo Vecinos no entreg? filas."
                )
                lines.append("")
                lines.append(
                    "Resultado parcial: cuenta identificada; "
                    "direcciones pendientes de lectura en Vecinos."
                )
            else:
                lines.append(
                    "No fue posible obtener una cuenta v?lida "
                    "ni filas de la tabla Vecinos."
                )

        return {
            "ok": bool(valid or resultado_parcial),
            "tipo_respuesta": "direccion_clientes",
            "codigo": (
                "DIRECCIONES_OK"
                if valid
                else "DIRECCIONES_CUENTA_OK_VECINOS_SIN_FILAS"
                if resultado_parcial
                else "DIRECCIONES_SIN_DATOS"
            ),
            "ot": ot,
            "respuesta": "\n".join(lines),
            "seriales_encontrados": len(serials),
            "clientes_encontrados": len(valid),
            "clientes": clients,
            "serial_consultado": selected.get("serial") if selected else "",
            "cuenta_consulta": selected.get("cuenta_rr") if selected else "",
            "estado_consulta": selected.get("estado") if selected else "ACS_SIN_CUENTA",
            "intentos_acs": attempts,
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    except Exception as exc:
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes",
            "codigo": "DIRECCIONES_ERROR",
            "ot": ot,
            "respuesta": f"No fue posible completar la consulta para {ot}.",
            "clientes": [],
            "error": f"{type(exc).__name__}: {exc}",
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    finally:
        _MAXIMO_SEMAPHORE.release()




# FTTH_ACS_ACCOUNT_ONLY_V1

# ACS_BATCH_RUNNER_V2_START
def resolver_cuentas_acs_batch(
    identificadores: list[str],
    timeout_sec: int,
) -> Dict[str, Any]:
    started = time.perf_counter()
    values: list[str] = []
    seen: set[str] = set()

    for raw in identificadores or []:
        value = str(raw or "").strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)

    if not values:
        return {
            "ok": False,
            "codigo": "IDENTIFICADOR_REQUERIDO",
            "cuenta_consulta": "",
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "duracion_seg": 0,
        }

    timeout = int(timeout_sec)
    if timeout <= 0:
        return {
            "ok": False,
            "codigo": "DIRECCIONES_IDENTIFICADOR_ERROR",
            "cuenta_consulta": "",
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "error": "FTTH_ACS_BATCH_TIMEOUT_INVALIDO",
            "duracion_seg": 0,
        }

    acs_script = _script_configurado("ACS_TR069_SCRIPT")
    runner = acs_script.parent / "acs_account_batch_runner.py"

    if not runner.is_file():
        return {
            "ok": False,
            "codigo": "DIRECCIONES_IDENTIFICADOR_ERROR",
            "cuenta_consulta": "",
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "error": "ACS_BATCH_RUNNER_NO_EXISTE",
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    request_json = _dir_json.dumps({"serials": values}, ensure_ascii=False)

    try:
        process = _dir_subprocess.run(
            [_dir_sys.executable, str(runner)],
            input=request_json,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except _dir_subprocess.TimeoutExpired:
        return {
            "ok": False,
            "codigo": "ACS_TIMEOUT",
            "cuenta_consulta": "",
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    stdout = (process.stdout or "").strip().lstrip("\ufeff")
    stderr = (process.stderr or "").strip()
    result = _extraer_ultimo_json(stdout)

    if not isinstance(result, dict):
        return {
            "ok": False,
            "codigo": "DIRECCIONES_RESPUESTA_INVALIDA",
            "cuenta_consulta": "",
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "error": stderr[-500:] or stdout[-500:] or f"codigo {process.returncode}",
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    state = str(result.get("estado") or "").strip().upper()
    account = str(result.get("cuenta") or "").strip()
    raw_items = result.get("resultados") or []
    if not isinstance(raw_items, list):
        raw_items = []

    mapped: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        item_account = str(item.get("cuenta") or "").strip()
        item_ok = bool(item.get("ok") and item_account)
        attempts = item.get("search_attempts") or []
        safe_attempts = []

        if isinstance(attempts, list):
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                safe_attempts.append({
                    "search_by": str(attempt.get("search_by") or ""),
                    "no_data_source": str(attempt.get("no_data_source") or ""),
                    "wait_elapsed_sec": attempt.get("wait_elapsed_sec"),
                    "fresh_ajax_response": bool(attempt.get("fresh_ajax_response")),
                    "hfDeviceWasFound": str(attempt.get("hfDeviceWasFound") or ""),
                })

        # FTTH_ACS_BATCH_CANDIDATE_MAP_V1
        item_state = str(
            item.get("estado") or ""
        ).strip().upper()

        if item_ok:
            item_code = "ACS_CUENTA_OK"
        elif item_state == "ACS_CANDIDATE_ERROR":
            item_code = "ACS_CANDIDATE_ERROR"
        else:
            item_code = "ACS_SIN_CUENTA"

        mapped.append({
            "ok": item_ok,
            "codigo": item_code,
            "cuenta_consulta": item_account,
            "intentos_acs": safe_attempts,
            "estado_acs": item_state,
            "error_tipo": str(item.get("error_tipo") or ""),
            "error": str(item.get("error") or ""),
            "duracion_seg": item.get("duracion_seg"),
        })

    if state == "ACS_LOGIN_ERROR":
        code = "ACS_LOGIN_ERROR"
    elif state == "ACS_CREDENCIALES_INVALIDAS":
        code = "ACS_CREDENCIALES_INVALIDAS"
    elif state in {"ACS_BATCH_ERROR", "ACS_BATCH_INPUT_ERROR"}:
        code = "DIRECCIONES_IDENTIFICADOR_ERROR"
    elif account:
        code = "ACS_CUENTA_OK"
    elif state == "ACS_BATCH_SIN_RESULTADOS":
        code = "ACS_SIN_CUENTA"
    else:
        code = "DIRECCIONES_RESPUESTA_INVALIDA"

    return {
        "ok": bool(account),
        "codigo": code,
        "cuenta_consulta": account,
        "resultados": mapped,
        "batch_estado": state,
        "browser_launches": int(result.get("browser_launches") or 0),
        "login_count": int(result.get("login_count") or 0),
        "session_reused": bool(result.get("session_reused")),
        "duracion_seg": round(time.perf_counter() - started, 2),
    }
# ACS_BATCH_RUNNER_V2_END

def resolver_cuenta_acs_desde_identificador(
    identificador: str,
    search_by: str = "serial",
) -> Dict[str, Any]:
    started = time.perf_counter()
    value = str(identificador or "").strip()
    mode = str(search_by or "serial").strip().lower()

    if mode not in {"serial", "mac"}:
        mode = "serial"

    if not value:
        return {
            "ok": False,
            "codigo": "IDENTIFICADOR_REQUERIDO",
            "identificador": value,
            "cuenta_consulta": "",
            "intentos_acs": [],
            "duracion_seg": 0,
        }

    attempts: list[dict[str, Any]] = []

    def resolve(candidate_mode: str):
        account, acs_result = _consultar_cuenta_acs(
            value,
            candidate_mode,
        )

        if not isinstance(acs_result, dict):
            acs_result = {}

        estado_acs = str(
            acs_result.get("estado")
            or ""
        ).strip()

        attempts.append(
            {
                "search_by": candidate_mode,
                "cuenta_rr": account or "",
                "estado_acs": estado_acs,
            }
        )

        return str(account or "").strip(), estado_acs

    try:
        account, estado_acs = resolve(mode)

        if not account and estado_acs.upper() == "ACS_ERROR":
            return {
                "ok": False,
                "codigo": "ACS_LOGIN_ERROR",
                "identificador": value,
                "cuenta_consulta": "",
                "intentos_acs": attempts,
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }

        compact = re.sub(
            r"[^0-9A-F]",
            "",
            value.upper(),
        )

        if (
            not account
            and mode != "mac"
            and re.fullmatch(r"[0-9A-F]{12}", compact)
        ):
            account, estado_acs = resolve("mac")

        if not account:
            return {
                "ok": False,
                "codigo": "ACS_SIN_CUENTA",
                "identificador": value,
                "cuenta_consulta": "",
                "intentos_acs": attempts,
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }

        return {
            "ok": True,
            "codigo": "ACS_CUENTA_OK",
            "identificador": value,
            "cuenta_consulta": account,
            "intentos_acs": attempts,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        error_lower = error_text.lower()

        error_code = (
            "ACS_TIMEOUT"
            if "acs_tr069_client.py supero el limite" in error_lower
            else "DIRECCIONES_IDENTIFICADOR_ERROR"
        )

        return {
            "ok": False,
            "codigo": error_code,
            "identificador": value,
            "cuenta_consulta": "",
            "intentos_acs": attempts,
            "error": error_text,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }


# FTTH_DIAGNOSTICADOR_BY_ACCOUNT_ONLY_V1
def consultar_vecinos_por_cuenta(
    cuenta: str,
) -> Dict[str, Any]:
    started = time.perf_counter()
    account = str(cuenta or "").strip()

    if not account:
        return {
            "ok": False,
            "codigo": "CUENTA_REQUERIDA",
            "cuenta_consulta": "",
            "clientes": [],
            "clientes_encontrados": 0,
            "duracion_seg": 0,
        }

    try:
        vecinos, diagnosticador_result = (
            _consultar_direccion_diagnosticador(account)
        )

        if not isinstance(diagnosticador_result, dict):
            diagnosticador_result = {}

        diagnosticador_estado = str(
            diagnosticador_result.get("estado")
            or ""
        ).strip().upper()

        if diagnosticador_estado in {
            "LOGIN_RECHAZADO",
            "LOGIN_ERROR",
            "LOGIN_NO_CONFIRMADO",
        }:
            return {
                "ok": False,
                "codigo": "DIAGNOSTICADOR_LOGIN_ERROR",
                "cuenta_consulta": account,
                "clientes": [],
                "clientes_encontrados": 0,
                "diagnosticador_estado": diagnosticador_estado,
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }

        if not isinstance(vecinos, list):
            vecinos = []

        clientes = []

        for vecino in vecinos:
            if not isinstance(vecino, dict):
                continue

            item = {
                "serial": "",
                "cuenta_consulta": account,
                "cuenta_rr": str(
                    vecino.get("cuenta_rr")
                    or ""
                ).strip(),
                "mac": str(
                    vecino.get("mac")
                    or ""
                ).strip(),
                "direccion": str(
                    vecino.get("direccion")
                    or ""
                ).strip(),
                "estado": "OK",
                "error": "",
            }

            if (
                item["cuenta_rr"]
                or item["mac"]
                or item["direccion"]
            ):
                clientes.append(item)

        nodo = str(
            _buscar_valor(
                diagnosticador_result,
                {"nodo", "node"},
            )
            or ""
        ).strip()

        return {
            "ok": bool(clientes),
            "codigo": (
                "DIRECCIONES_OK"
                if clientes
                else "DIRECCIONES_CUENTA_OK_VECINOS_SIN_FILAS"
            ),
            "cuenta_consulta": account,
            "nodo": nodo,
            "clientes_encontrados": len(clientes),
            "clientes": clientes,
            "diagnosticador_estado": diagnosticador_estado,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        error_lower = error_text.lower()

        error_code = (
            "DIAGNOSTICADOR_TIMEOUT"
            if "script_diagnosticador_actual.py supero el limite" in error_lower
            else "DIRECCIONES_IDENTIFICADOR_ERROR"
        )

        return {
            "ok": False,
            "codigo": error_code,
            "cuenta_consulta": account,
            "clientes": [],
            "clientes_encontrados": 0,
            "error": error_text,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }


# FTTH_TRONCAL_DIRECCIONES_PUBLIC_V1
def consultar_direcciones_desde_identificador(
    identificador: str,
    search_by: str = "serial",
) -> Dict[str, Any]:
    started = time.perf_counter()
    value = str(identificador or "").strip()
    mode = str(search_by or "serial").strip().lower()

    if mode not in {"serial", "mac"}:
        mode = "serial"

    if not value:
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes_identificador",
            "codigo": "IDENTIFICADOR_REQUERIDO",
            "identificador": value,
            "clientes": [],
        }

    attempts = []

    def resolve_account(candidate_mode: str):
        account, acs_result = _consultar_cuenta_acs(
            value,
            candidate_mode,
        )
        attempts.append(
            {
                "search_by": candidate_mode,
                "cuenta_rr": account or "",
                "estado_acs": str(
                    (acs_result or {}).get("estado") or ""
                ),
            }
        )
        return account

    try:
        account = resolve_account(mode)

        # ACS_LOGIN_ERROR_PROPAGATION_V1
        if (
            not account
            and attempts
            and str(attempts[-1].get("estado_acs") or "").upper()
            == "ACS_ERROR"
        ):
            return {
                "ok": False,
                "tipo_respuesta": "direccion_clientes_identificador",
                "codigo": "ACS_LOGIN_ERROR",
                "identificador": value,
                "clientes": [],
                "intentos_acs": attempts,
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }

        compact = re.sub(r"[^0-9A-F]", "", value.upper())

        if (
            not account
            and mode != "mac"
            and re.fullmatch(r"[0-9A-F]{12}", compact)
        ):
            account = resolve_account("mac")

        if not account:
            return {
                "ok": False,
                "tipo_respuesta": "direccion_clientes_identificador",
                "codigo": "ACS_SIN_CUENTA",
                "identificador": value,
                "clientes": [],
                "intentos_acs": attempts,
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }

        vecinos, diagnosticador_result = _consultar_direccion_diagnosticador(account)

        # DIAGNOSTICADOR_LOGIN_ERROR_PROPAGATION_V1
        diagnosticador_estado = str(
            (diagnosticador_result or {}).get("estado") or ""
        ).strip().upper()

        if diagnosticador_estado in {
            "LOGIN_RECHAZADO",
            "LOGIN_ERROR",
            "LOGIN_NO_CONFIRMADO",
        }:
            return {
                "ok": False,
                "tipo_respuesta": "direccion_clientes_identificador",
                "codigo": "DIAGNOSTICADOR_LOGIN_ERROR",
                "identificador": value,
                "cuenta_consulta": account,
                "clientes": [],
                "clientes_encontrados": 0,
                "intentos_acs": attempts,
                "diagnosticador_estado": diagnosticador_estado,
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }

        clientes = [
            {
                "serial": value if mode == "serial" else "",
                "cuenta_consulta": account,
                "cuenta_rr": vecino.get("cuenta_rr", ""),
                "mac": vecino.get("mac", ""),
                "direccion": vecino.get("direccion", ""),
                "estado": "OK",
                "error": "",
            }
            for vecino in vecinos
        ]

        return {
            "ok": bool(clientes),
            "tipo_respuesta": "direccion_clientes_identificador",
            "codigo": (
                "DIRECCIONES_OK"
                if clientes
                else "DIRECCIONES_CUENTA_OK_VECINOS_SIN_FILAS"
            ),
            "identificador": value,
            "cuenta_consulta": account,
            "clientes_encontrados": len(clientes),
            "clientes": clientes,
            "intentos_acs": attempts,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    except Exception as exc:
        # FTTH_DOWNSTREAM_TIMEOUT_CLASSIFICATION_V1
        error_text = f"{type(exc).__name__}: {exc}"
        error_lower = error_text.lower()

        if "acs_tr069_client.py supero el limite" in error_lower:
            error_code = "ACS_TIMEOUT"
        elif "script_diagnosticador_actual.py supero el limite" in error_lower:
            error_code = "DIAGNOSTICADOR_TIMEOUT"
        else:
            error_code = "DIRECCIONES_IDENTIFICADOR_ERROR"

        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes_identificador",
            "codigo": error_code,
            "identificador": value,
            "clientes": [],
            "error": error_text,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }