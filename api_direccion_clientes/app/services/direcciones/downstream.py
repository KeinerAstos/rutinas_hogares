from __future__ import annotations

# ATLAS_DIRECCIONES_DOWNSTREAM_V1
#
# Dependencias downstream exclusivas de Direcciones FTTH:
# - ACS batch
# - Diagnosticador por cuenta
# - Diagnosticador por MAC
#
# Código funcional extraído literalmente desde maximo.service.
# No importa MaximoClient.

import json as _dir_json
import os
import re
import subprocess as _dir_subprocess
import sys as _dir_sys
import time
import unicodedata

from pathlib import Path
from typing import Any, Dict


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

def _consultar_direccion_diagnosticador(
    query: str,
    query_type: str = "cuenta",
):
    # FTTH_ACS_MAC_DIAGNOSTICADOR_V1
    mode = str(query_type or "cuenta").strip().lower()

    if mode not in {"cuenta", "mac"}:
        raise ValueError(
            f"Tipo de consulta Diagnosticador no soportado: {mode}"
        )

    script = _script_configurado("DIAGNOSTICADOR_SCRIPT")
    result = _ejecutar_script_json(
        script,
        [
            "--url", str(os.getenv("DIAGNOSTICADOR_URL") or "").strip(),
            "--query-type", mode,
            "--query", query,
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

                    # FTTH_ACS_MAC_DIAGNOSTICADOR_V1
                    "device_found": bool(attempt.get("device_found")),
                    "device_serial": str(attempt.get("device_serial") or ""),
                    "device_mac": str(attempt.get("device_mac") or ""),
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
                else (
                    "DIAGNOSTICADOR_NO_DISPONIBLE"
                    if diagnosticador_estado == "DIAGNOSTICADOR_NO_DISPONIBLE"
                    else "DIRECCIONES_CUENTA_OK_VECINOS_SIN_FILAS"
                )
            ),
            "mensaje": (
                str(
                    diagnosticador_result.get("mensaje")
                    or (
                        "No se puede consultar el cablemodem o la ONT. "
                        "El cablemodem no tiene registros históricos en los últimos "
                        "siete(7) días o la ONT no se encuentra en disponible en el ACS."
                    )
                )
                if diagnosticador_estado == "DIAGNOSTICADOR_NO_DISPONIBLE"
                else None
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

def consultar_vecinos_por_mac(
    mac: str,
) -> Dict[str, Any]:
    started = time.perf_counter()

    compact = re.sub(
        r"[^0-9A-Fa-f]",
        "",
        str(mac or ""),
    ).upper()

    if not re.fullmatch(r"[0-9A-F]{12}", compact):
        return {
            "ok": False,
            "codigo": "MAC_REQUERIDA",
            "mac_consulta": "",
            "cuenta_consulta": "",
            "clientes": [],
            "clientes_encontrados": 0,
            "duracion_seg": 0,
        }

    normalized_mac = ":".join(
        compact[index:index + 2]
        for index in range(0, 12, 2)
    )

    try:
        vecinos, diagnosticador_result = (
            _consultar_direccion_diagnosticador(
                normalized_mac,
                "mac",
            )
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
                "mac_consulta": normalized_mac,
                "cuenta_consulta": "",
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
                "cuenta_consulta": "",
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
                else (
                    "DIAGNOSTICADOR_NO_DISPONIBLE"
                    if diagnosticador_estado == "DIAGNOSTICADOR_NO_DISPONIBLE"
                    else "DIRECCIONES_MAC_OK_VECINOS_SIN_FILAS"
                )
            ),
            "mensaje": (
                str(
                    diagnosticador_result.get("mensaje")
                    or (
                        "No se puede consultar el cablemodem o la ONT. "
                        "El cablemodem no tiene registros históricos en los últimos "
                        "siete(7) días o la ONT no se encuentra en disponible en el ACS."
                    )
                )
                if diagnosticador_estado == "DIAGNOSTICADOR_NO_DISPONIBLE"
                else None
            ),
            "mac_consulta": normalized_mac,
            "cuenta_consulta": "",
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
            "mac_consulta": normalized_mac,
            "cuenta_consulta": "",
            "clientes": [],
            "clientes_encontrados": 0,
            "error": error_text,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

