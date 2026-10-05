"""Deteccion de intencion para capturas PathTrak."""

import re
from typing import Any, Dict, Optional


def detectar_intencion_pathtrak(mensaje: str) -> Optional[Dict[str, Any]]:
    texto = str(mensaje or "").strip()
    low = texto.lower().strip()

    palabras_qoe = [
        "qoe",
        "qo e",
        "estado visual",
        "captura estado",
        "cap estado",
        "pantallazo qoe",
        "captura qoe",
        "cap qoe",
    ]

    palabras_ruido = [
        "ondas",
        "onda",
        "spectrum",
        "spectra",
        "espectro",
        "ruido",
        "grafica de ondas",
        "gráfica de ondas",
        "grafica de ruido",
        "gráfica de ruido",
        "captura ondas",
        "captura ruido",
        "cap ondas",
        "cap ruido",
    ]

    tiene_qoe = any(palabra in low for palabra in palabras_qoe)
    tiene_ruido = any(palabra in low for palabra in palabras_ruido)

    pide_ambas = (
        tiene_qoe
        and tiene_ruido
    ) or any(
        frase in low
        for frase in [
            "ambas capturas",
            "las dos capturas",
            "dos capturas",
            "qoe + ruido",
            "qoe y ruido",
            "qoe con ruido",
            "qoe y ondas",
        ]
    )

    if pide_ambas:
        tipo = "qoe_ruido"
    elif tiene_ruido:
        tipo = "ondas"
    elif tiene_qoe:
        tipo = "qoe"
    else:
        return None

    nodo = None
    match_nodo = re.search(
        r"\bnodo\s+([A-Za-z0-9._-]{2,20})\b",
        texto,
        re.I,
    )
    if match_nodo:
        nodo = match_nodo.group(1).upper()

    if not nodo:
        limpio = low
        for palabra in [
            "captura",
            "capturas",
            "cap",
            "pantallazo",
            "imagen",
            "foto",
            "nodo",
            "del",
            "de",
            "la",
            "el",
            "las",
            "los",
            "por favor",
            "qoe",
            "qo e",
            "estado visual",
            "captura estado",
            "cap estado",
            "ondas",
            "onda",
            "spectrum",
            "spectra",
            "espectro",
            "ruido",
            "grafica",
            "gráfica",
            "ambas",
            "ambos",
            "dos",
            "y",
            "con",
        ]:
            limpio = re.sub(
                rf"\b{re.escape(palabra)}\b",
                " ",
                limpio,
                flags=re.I,
            )

        partes = [
            parte.strip().upper()
            for parte in re.split(r"\s+", limpio)
            if parte.strip()
        ]

        for parte in partes:
            if re.match(r"^[A-Z0-9._-]{2,20}$", parte):
                nodo = parte
                break

    if not nodo:
        return {
            "ok": False,
            "error": (
                "No pude identificar el nodo. Ejemplo: "
                "captura qoe y ruido nodo SRL3B"
            ),
        }

    return {
        "ok": True,
        "tipo": tipo,
        "nodo": nodo,
    }
