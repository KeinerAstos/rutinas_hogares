"""Deteccion de intenciones HFC y diagnostico profundo."""

import re
from typing import Any, Dict, List, Optional


def detectar_diagnostico_real_hfc(mensaje: str) -> Optional[Dict[str, Any]]:
    texto = str(mensaje or "").strip()
    low = texto.lower()

    disparadores = [
        "diagnostico real",
        "diagnóstico real",
        "diagnostico hfc",
        "diagnóstico hfc",
        "diagnostico real hfc",
        "diagnóstico real hfc",
        "estado real",
        "estado hfc",
        "revisar de pies a cabeza",
        "mirar de pies a cabeza",
        "de pies a cabeza",
        "hfc profundo",
        "profundo hfc",
        "diagnostico de verdad",
        "diagnóstico de verdad",
    ]

    if not any(x in low for x in disparadores):
        return None

    limpio = texto

    palabras_basura = [
        "diagnostico", "diagnóstico", "real", "hfc", "estado",
        "revisar", "mirar", "pies", "cabeza", "profundo", "profunda",
        "de", "del", "la", "el", "nodo", "nodos", "por", "favor",
        "verdad", "completo", "completa", "validar", "valida",
        "con", "pathtrak", "qoe", "ondas", "spectrum", "espectro",
    ]

    for p in palabras_basura:
        limpio = re.sub(rf"\b{re.escape(p)}\b", " ", limpio, flags=re.I)

    candidatos = [
        x.strip().upper()
        for x in re.split(r"[\s,;|]+", limpio)
        if x.strip()
    ]

    nodos = []

    for c in candidatos:
        if re.match(r"^INC\d+$", c):
            continue

        if re.match(r"^[A-Z0-9_-]{2,20}$", c):
            if c not in nodos:
                nodos.append(c)

    if not nodos:
        m = re.search(r"\bnodo\s+([A-Za-z0-9_-]{2,20})", texto, re.I)
        if m:
            nodos.append(m.group(1).upper())

    if not nodos:
        return {
            "ok": False,
            "error": "No pude identificar el nodo. Ejemplo: diagnóstico real nodo 6601",
        }

    return {
        "ok": True,
        "nodos": nodos[:3],
        "pathtrak": any(x in low for x in ["pathtrak", "qoe", "ondas", "spectrum", "espectro"]),
    }


def detectar_diagnostico_profundo(mensaje: str) -> Optional[Dict[str, Any]]:
    """
    Detecta diagnóstico profundo clásico:
    - diagnóstico profundo nodo TCG
    - diagnóstico profundo nodos TCG 6601
    - revisar a fondo TCG 6601
    - analizar profundo nodo 6601
    """

    texto = str(mensaje or "").strip()
    low = texto.lower()

    disparadores = [
        "diagnostico profundo",
        "diagnóstico profundo",
        "revisar a fondo",
        "analizar a fondo",
        "analisis profundo",
        "análisis profundo",
        "revision profunda",
        "revisión profunda",
        "mirar a fondo",
    ]

    if not any(x in low for x in disparadores):
        return None

    limpio = texto

    palabras_basura = [
        "diagnostico", "diagnóstico", "profundo", "profunda",
        "revisar", "revision", "revisión", "analizar",
        "analisis", "análisis", "mirar", "fondo",
        "nodo", "nodos", "incidente", "incidentes",
        "inc", "los", "las", "el", "la", "de", "del",
        "por", "favor", "a",
    ]

    for p in palabras_basura:
        limpio = re.sub(rf"\b{re.escape(p)}\b", " ", limpio, flags=re.I)

    candidatos = [x.strip().upper() for x in re.split(r"[\s,;|]+", limpio) if x.strip()]
    nodos: List[str] = []

    for c in candidatos:
        if re.match(r"^INC\d+$", c):
            continue
        if re.match(r"^[A-Z0-9_-]{2,20}$", c) and c not in nodos:
            nodos.append(c)

    if not nodos:
        return {
            "ok": False,
            "error": "No pude identificar nodos. Ejemplo: diagnóstico profundo nodos TCG 6601",
        }

    return {"ok": True, "nodos": nodos[:5]}
