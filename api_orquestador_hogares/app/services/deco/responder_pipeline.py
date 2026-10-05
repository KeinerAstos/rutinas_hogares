"""Pipeline final DECO: fallback, HFC, PathTrak, AUTH y Maximo."""

from __future__ import annotations

_EXTRACTED_NAMES = {
    "_responder_chat_base",
    "_estado_nodo_fallback_norm",
    "_estado_nodo_fallback_extraer_nodo",
    "_estado_nodo_fallback_es_respuesta_fallida",
    "_responder_chat_estado_fallback",
    "_deco_compacto_get",
    "_deco_compacto_counts",
    "_deco_formatear_hfc_real_compacto",
    "_responder_chat_hfc_compacto",
    "_deco_alias_pathtrak_norm",
    "_deco_alias_pathtrak_extraer_nodo",
    "_responder_chat_pathtrak_presentacion",
    "_sync_pathtrak_parallel",
    "_responder_chat_pathtrak_paralelo",
    "_deco_es_error_auth_cmts",
    "_deco_extraer_nodo_desde_mensaje",
    "_deco_normalizar_error_auth_cmts",
    "responder_chat",
}

def configure(namespace):
    for key, value in namespace.items():
        if key.startswith("__"):
            continue
        if key in _EXTRACTED_NAMES:
            continue
        if key == "configure":
            continue
        globals()[key] = value

def _responder_chat_base(mensaje: str) -> Dict[str, Any]:
    mensaje = str(mensaje or "").strip()

    if not mensaje:
        return {"ok": False, "respuesta": "Mensaje vacío."}

    # 1. Diagnóstico HFC real V1.2
    intencion_hfc = detectar_diagnostico_real_hfc(mensaje)
    if intencion_hfc:
        if not intencion_hfc.get("ok"):
            return {"ok": False, "respuesta": intencion_hfc.get("error")}

        return hacer_diagnostico_real_hfc(
            intencion_hfc.get("nodos", []),
            incluir_pathtrak=bool(intencion_hfc.get("pathtrak")),
        )

    # 2. Diagnóstico profundo clásico del DECO local
    intencion_profunda = detectar_diagnostico_profundo(mensaje)
    if intencion_profunda:
        if not intencion_profunda.get("ok"):
            return {"ok": False, "respuesta": intencion_profunda.get("error")}

        return hacer_diagnostico_profundo_nodos(intencion_profunda.get("nodos", []))

    # 3. Capturas PathTrak individuales
    intencion_pathtrak = detectar_intencion_pathtrak(mensaje)
    if intencion_pathtrak:
        if not intencion_pathtrak.get("ok"):
            return {"ok": False, "respuesta": intencion_pathtrak.get("error")}

        cap_func = get_pathtrak_capture_func()
        if cap_func is None:
            return {
                "ok": False,
                "respuesta": "El módulo de PathTrak no está disponible.",
                "error": _pathtrak_runtime.get_capture_error(),
            }

        resultado = cap_func(
            intencion_pathtrak["nodo"],
            tipo_captura=intencion_pathtrak["tipo"],
        )
        return construir_respuesta_pathtrak(resultado)

    # 4. Listado de nodos por CMTS con contexto
    if es_solicitud_todos_nodos(mensaje):
        cmts_directo = extraer_cmts_de_mensaje(mensaje)
        cmts_consulta = cmts_directo or _SESSION.get("ultimo_cmts")

        if not cmts_consulta:
            return {
                "ok": True,
                "respuesta": (
                    "No tengo un CMTS en contexto todavía.\n\n"
                    "Primero consulta un CMTS, por ejemplo:\n\n"
                    "estado cmts JAMU-JAMU-H-01-CS100G\n\n"
                    "o escribe directamente:\n\n"
                    "ver todos los nodos del cmts JAMU-JAMU-H-01-CS100G"
                ),
                "metodo": "listar_todos_nodos_cmts",
            }

        return listar_todos_nodos_cmts(cmts_consulta)

    # 5. Motor normal NOC
    normal = ejecutar_motor_noc_directo(mensaje)
    if normal.get("ok"):
        recordar_cmts_desde_respuesta(normal.get("respuesta"))

    return normal

def _estado_nodo_fallback_norm(txt: str) -> str:
    return (
        str(txt or "")
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

def _estado_nodo_fallback_extraer_nodo(mensaje: str):
    txt = _estado_nodo_fallback_norm(mensaje)

    # No tocar comandos especiales que ya tienen su propio flujo.
    bloqueados = [
        "diagnostico real",
        "diagnostico profundo",
        "diagnóstico real",
        "diagnóstico profundo",
        "pathtrak",
        "captura",
        "qoe",
        "ondas",
        "incidente",
        " inc ",
        "nodos del cmts",
        "todos los nodos",
        "cmts ",
    ]

    if any(x in txt for x in bloqueados):
        return None

    m = _estado_nodo_fallback_re.search(
        r"\bestado\s+nodo\s+([a-z0-9_-]+)\b",
        txt,
        _estado_nodo_fallback_re.I,
    )

    if not m:
        return None

    return m.group(1).strip().upper()

def _estado_nodo_fallback_es_respuesta_fallida(resp: dict) -> bool:
    if not isinstance(resp, dict):
        return False

    texto = _estado_nodo_fallback_norm(
        resp.get("respuesta")
        or resp.get("message")
        or resp.get("texto")
        or ""
    )

    if not texto:
        return False

    es_casa = (
        "vendor      : casa" in texto
        or "driver      : casa" in texto
        or "vendor: casa" in texto
        or "driver: casa" in texto
    )

    tiene_ceros = (
        "total       : 0" in texto
        and "online      : 0" in texto
        and "init        : 0" in texto
    )

    diagnostico_invalido = any(x in texto for x in [
        "no se pudo validar correctamente",
        "no se pudo validar",
        "service group",
        "revisar / posible escalamiento",
    ])

    return bool(es_casa and tiene_ceros and diagnostico_invalido)

def _responder_chat_estado_fallback(mensaje: str) -> Dict[str, Any]:
    """
    Wrapper final:
    - Ejecuta el responder_chat original.
    - Si el comando fue "estado nodo X" y el motor base falla con ceros en CASA,
      ejecuta automáticamente "diagnóstico real nodo X".
    """
    mensaje_limpio = str(mensaje or "").strip()
    nodo = _estado_nodo_fallback_extraer_nodo(mensaje_limpio)

    respuesta_base = _responder_chat_base(mensaje_limpio)

    if not nodo:
        return respuesta_base

    if not _estado_nodo_fallback_es_respuesta_fallida(respuesta_base):
        return respuesta_base

    try:
        respuesta_real = _responder_chat_base(
            f"diagnóstico real nodo {nodo}"
        )

        if isinstance(respuesta_real, dict):
            respuesta_real["fallback_deco"] = True
            respuesta_real["metodo"] = "estado_nodo_auto_diagnostico_real"
            respuesta_real["fallback_motivo"] = (
                f"El motor base de 'estado nodo {nodo}' devolvió 0 módems "
                "para un CMTS CASA. DECO ejecutó automáticamente diagnóstico real HFC."
            )

        return respuesta_real

    except Exception as exc:
        if isinstance(respuesta_base, dict):
            respuesta_base["fallback_deco_error"] = f"{type(exc).__name__}: {exc}"
        return respuesta_base

def _deco_compacto_get(d, *keys, default=None):
    actual = d
    for k in keys:
        if not isinstance(actual, dict):
            return default
        actual = actual.get(k)
        if actual is None:
            return default
    return actual

def _deco_compacto_counts(data: dict) -> dict:
    return _hfc_analysis._deco_compacto_counts(data)

def _deco_formatear_hfc_real_compacto(resp: dict) -> dict:
    return _hfc_analysis._deco_formatear_hfc_real_compacto(resp)

def _responder_chat_hfc_compacto(mensaje: str) -> Dict[str, Any]:
    resp = _responder_chat_estado_fallback(mensaje)

    if isinstance(resp, dict) and resp.get("tipo_respuesta") == "hfc_real_diagnostic":
        return _deco_formatear_hfc_real_compacto(resp)

    return resp

def _deco_alias_pathtrak_norm(txt: str) -> str:
    return (
        str(txt or "")
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

def _deco_alias_pathtrak_extraer_nodo(mensaje: str):
    txt = _deco_alias_pathtrak_norm(mensaje)

    if "pathtrak" not in txt:
        return None

    # No tocar capturas QoE/Ondas, esas ya funcionan por otro flujo.
    if "qoe" in txt or "ondas" in txt or "spectrum" in txt:
        return None

    patrones = [
        r"\bpathtrak\s+(?:del\s+|de\s+|para\s+)?(?:el\s+)?nodo\s+([a-z0-9_-]+)\b",
        r"\bnodo\s+([a-z0-9_-]+)\s+.*\bpathtrak\b",
        r"\bpathtrak\s+([a-z0-9_-]+)\b",
    ]

    for patron in patrones:
        m = _deco_alias_pathtrak_re.search(patron, txt, _deco_alias_pathtrak_re.I)
        if m:
            return m.group(1).strip().upper()

    return None

def _responder_chat_pathtrak_presentacion(mensaje: str) -> Dict[str, Any]:
    mensaje_limpio = str(mensaje or "").strip()
    txt = mensaje_limpio.lower()

    nodo_pathtrak = _deco_alias_pathtrak_extraer_nodo(mensaje_limpio)

    if nodo_pathtrak:
        resp = _responder_chat_hfc_compacto(
            f"diagnóstico real nodo {nodo_pathtrak} con pathtrak"
        )

        if isinstance(resp, dict):
            resp["alias_deco"] = True
            resp["alias_original"] = mensaje_limpio
            resp["alias_convertido"] = (
                f"diagnóstico real nodo {nodo_pathtrak} con pathtrak"
            )
    else:
        resp = _responder_chat_hfc_compacto(mensaje_limpio)

    if (
        isinstance(resp, dict)
        and resp.get("alias_deco") is True
        and "pathtrak" in txt
    ):
        resp["metodo"] = "diagnostico_real_con_pathtrak"
        resp["tipo_operativo"] = "Diagnóstico HFC real + PathTrak"
        resp["fallback_deco"] = False

        resumen = resp.get("resumen")
        if isinstance(resumen, dict):
            resumen["tipo_operativo"] = "Diagnóstico HFC real + PathTrak"
            resumen["pathtrak_solicitado"] = True

    if (
        isinstance(resp, dict)
        and resp.get("alias_deco") is True
        and resp.get("tipo_operativo") == "Diagnóstico HFC real + PathTrak"
        and isinstance(resp.get("respuesta"), str)
    ):
        texto = resp["respuesta"]

        if "Tipo        : Diagnóstico HFC real + PathTrak" not in texto:
            texto = texto.replace(
                "Driver      : HFC_REAL_CASA\n",
                "Driver      : HFC_REAL_CASA\n"
                "Tipo        : Diagnóstico HFC real + PathTrak\n",
                1,
            )

        resp["respuesta"] = texto

    return resp

def _sync_pathtrak_parallel() -> None:
    _pathtrak_parallel.configure(
        base_responder=_responder_chat_pathtrak_presentacion,
        extract_node=_deco_pathtrak_cap_extraer_nodo,
        capture_qoe=_deco_pathtrak_capturar_qoe,
        normalize_capture=_deco_pathtrak_normalizar_captura,
    )

def _responder_chat_pathtrak_paralelo(mensaje: str) -> Dict[str, Any]:
    _sync_pathtrak_parallel()
    return _pathtrak_parallel.responder_chat_paralelo(mensaje)

def _deco_es_error_auth_cmts(resp):
    import json as _json

    try:
        texto = _json.dumps(resp, ensure_ascii=False, default=str)
    except Exception:
        texto = str(resp)

    texto_l = texto.lower()

    patrones = [
        "authenticationexception",
        "authentication failed",
        "paramiko.ssh_exception.authenticationexception",
        "auth_password",
        "cmts_user",
        "cmts_password",
    ]

    return any(p in texto_l for p in patrones)

def _deco_extraer_nodo_desde_mensaje(mensaje):
    import re as _re

    texto = str(mensaje or "").strip()

    m = _re.search(r"\bnodo\s+([A-Za-z0-9_-]+)\b", texto, flags=_re.I)
    if m:
        return m.group(1).upper()

    m = _re.search(r"\b([A-Za-z]{2,}\d+[A-Za-z0-9_-]*|\d{3,}[A-Za-z0-9_-]*)\b", texto, flags=_re.I)
    if m:
        return m.group(1).upper()

    return ""

def _deco_normalizar_error_auth_cmts(resp, mensaje_original=""):
    if not isinstance(resp, dict):
        return resp

    if resp.get("ok") is not False:
        return resp

    if not _deco_es_error_auth_cmts(resp):
        return resp

    nodo = _deco_extraer_nodo_desde_mensaje(mensaje_original)
    nodo_txt = f" del nodo {nodo}" if nodo else ""

    traceback_original = resp.get("error") or resp.get("traceback") or resp.get("debug_traceback") or ""

    return {
        "ok": False,
        "tipo_respuesta": "cmts_auth_error",
        "metodo": resp.get("metodo") or "responder",
        "codigo": "AUTH_CMTS_FAILED",
        "respuesta": (
            f"No pude realizar la validación{nodo_txt} porque el CMTS rechazó "
            "el usuario o la contraseña SSH configurados.\n\n"
            "Validación ejecutada: conexión hacia CMTS por jump server.\n"
            "Resultado: autenticación fallida.\n\n"
            "Acción requerida:\n"
            "1. Verificar que CMTS_USER y CMTS_PASSWORD en /data/toba/.env estén correctos.\n"
            "2. Probar acceso manual al CMTS con esas credenciales.\n"
            "3. Reiniciar el backend después de actualizar el .env."
        ),
        "detalle_operativo": "El motor llegó hasta el CMTS, pero Paramiko recibió Authentication failed.",
        "error": "AUTH_CMTS_FAILED",
        "debug_traceback": traceback_original,
    }

def responder_chat(mensaje):
    try:
        from app.services.maximo.service import (
            consultar_evidencia_maximo,
            detectar_consulta_maximo,
        )

        ot = detectar_consulta_maximo(mensaje)
        if ot:
            return consultar_evidencia_maximo(ot)
    except Exception as exc:
        return {
            "ok": False,
            "tipo_respuesta": "maximo_evidence",
            "codigo": "MAXIMO_INTEGRATION_ERROR",
            "respuesta": "No fue posible iniciar la consulta de evidencia en Maximo.",
            "error": str(exc),
        }

    resp = _responder_chat_pathtrak_paralelo(mensaje)
    return _deco_normalizar_error_auth_cmts(resp, mensaje)
