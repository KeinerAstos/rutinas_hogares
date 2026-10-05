"""Subsistema de captura PathTrak extraido de deco_service.py."""

import re as _deco_pathtrak_cap_re
from pathlib import Path as _deco_pathtrak_Path
from urllib.parse import quote as _deco_pathtrak_quote

from app.services.deco import pathtrak_runtime as _pathtrak_runtime


def get_pathtrak_capture_func():
    return _pathtrak_runtime.get_capture_func()


def _deco_pathtrak_cap_norm(txt: str) -> str:
    return (
        str(txt or "")
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

def _deco_pathtrak_cap_extraer_nodo(mensaje: str):
    txt = _deco_pathtrak_cap_norm(mensaje)

    if "pathtrak" not in txt:
        return None

    # No tocar capturas directas que ya funcionan por su propio flujo.
    if "qoe" in txt or "ondas" in txt or "spectrum" in txt:
        return None

    patrones = [
        r"\bnodo\s+([a-z0-9_-]+)\s+.*\bpathtrak\b",
        r"\bpathtrak\s+(?:del\s+|de\s+|para\s+)?(?:el\s+)?nodo\s+([a-z0-9_-]+)\b",
        r"\bpathtrak\s+([a-z0-9_-]+)\b",
    ]

    for patron in patrones:
        m = _deco_pathtrak_cap_re.search(patron, txt, _deco_pathtrak_cap_re.I)
        if m:
            return m.group(1).strip().upper()

    return None

def _deco_pathtrak_buscar_imagen(obj):
    """
    Busca en cualquier respuesta del módulo PathTrak un archivo o URL de imagen.
    """
    if obj is None:
        return None

    if isinstance(obj, str):
        v = obj.strip()
        vl = v.lower()

        if vl.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return v

        if "/api/deco/screenshots/pathtrak/" in vl:
            return v

        return None

    if isinstance(obj, dict):
        # Priorizar llaves típicas
        for k in [
            "url",
            "image_url",
            "screenshot_url",
            "captura_url",
            "pathtrak_url",
            "public_url",
            "display_url",
            "filename",
            "file",
            "path",
            "screenshot",
            "captura",
            "imagen",
        ]:
            if k in obj:
                found = _deco_pathtrak_buscar_imagen(obj.get(k))
                if found:
                    return found

        for v in obj.values():
            found = _deco_pathtrak_buscar_imagen(v)
            if found:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = _deco_pathtrak_buscar_imagen(item)
            if found:
                return found

    return None

def _deco_pathtrak_url_from_file(imagen):
    if not imagen:
        return None, None

    imagen_str = str(imagen).strip()
    filename = _deco_pathtrak_Path(imagen_str).name

    if imagen_str.startswith("http://") or imagen_str.startswith("https://"):
        return imagen_str, filename

    if imagen_str.startswith("/toba-api/"):
        return imagen_str, filename

    if imagen_str.startswith("/api/"):
        return "/toba-api" + imagen_str, filename

    return (
        "/api/deco/screenshots/pathtrak/" + _deco_pathtrak_quote(filename),
        filename,
    )

def _deco_pathtrak_buscar_debug(obj):
    if obj is None:
        return None

    if isinstance(obj, str):
        v = obj.strip()
        if v.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and "debug_" in v.lower():
            return v
        return None

    if isinstance(obj, dict):
        for k in ["debug", "debug_url", "debug_path", "debug_screenshot", "last_debug"]:
            if k in obj:
                found = _deco_pathtrak_buscar_debug(obj.get(k))
                if found:
                    return found

        for k in ["errores", "errors"]:
            if k in obj:
                found = _deco_pathtrak_buscar_debug(obj.get(k))
                if found:
                    return found

        for v in obj.values():
            found = _deco_pathtrak_buscar_debug(v)
            if found:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = _deco_pathtrak_buscar_debug(item)
            if found:
                return found

    return None

def _deco_pathtrak_normalizar_captura(resultado):
    if resultado is None:
        return {
            "ok": False,
            "tipo": "qoe",
            "titulo": "QoE / Estado del nodo",
            "filename": None,
            "url": None,
            "debug_url": None,
            "error": "PathTrak no retornó resultado.",
            "raw": None,
        }

    ok = False
    error = None

    if isinstance(resultado, dict):
        ok = bool(resultado.get("ok", False))
        error = resultado.get("error") or resultado.get("detalle_error")
    else:
        error = "Respuesta PathTrak no esperada."

    # Si PathTrak fue exitoso, buscar SOLO captura válida.
    if ok:
        candidato = {}

        if isinstance(resultado, dict):
            for k in [
                "screenshot",
                "public_url",
                "image_url",
                "screenshot_url",
                "captura_url",
                "pathtrak_url",
                "display_url",
                "filename",
                "file",
                "path",
                "captura",
                "imagen",
            ]:
                if k in resultado:
                    candidato[k] = resultado.get(k)

        imagen = _deco_pathtrak_buscar_imagen(candidato)
        url, filename = _deco_pathtrak_url_from_file(imagen)

        return {
            "ok": True,
            "tipo": "qoe",
            "titulo": "QoE / Estado del nodo",
            "filename": filename,
            "url": url,
            "debug_url": None,
            "error": None,
            "raw": resultado,
        }

    # Si falló, NO poner url principal. Solo debug_url.
    debug_img = _deco_pathtrak_buscar_debug(resultado)
    debug_url, debug_filename = _deco_pathtrak_url_from_file(debug_img)

    return {
        "ok": False,
        "tipo": "qoe",
        "titulo": "QoE / Estado del nodo",
        "filename": None,
        "url": None,
        "debug_url": debug_url,
        "debug_filename": debug_filename,
        "error": error or "No se pudo generar captura QoE.",
        "raw": resultado,
    }

def _deco_pathtrak_capturar_qoe(nodo: str):
    cap_func = get_pathtrak_capture_func()

    if cap_func is None:
        return {
            "ok": False,
            "error": "El módulo PathTrak no está disponible.",
            "detalle": _pathtrak_runtime.get_capture_error(),
        }

    try:
        return cap_func(nodo, tipo_captura="qoe")
    except TypeError:
        # Respaldo por si la función no acepta keyword
        return cap_func(nodo, "qoe")
