# -*- coding: utf-8 -*-
"""Catalogo unico del simulador Mesa de Ayuda."""

from __future__ import annotations

from typing import Final

SOLICITUDES: Final[dict[str, dict[str, str]]] = {
    "1": {
        "codigo": "DIRECCIONES_CLIENTES",
        "nombre": "Direcciones de clientes",
    },
    "2": {
        "codigo": "REDES_NEUTRAS",
        "nombre": "Solicitudes Redes Neutras",
    },
    "3": {
        "codigo": "CANCELACION_OT",
        "nombre": "Cancelaci\u00f3n de OT",
    },
    "4": {
        "codigo": "GENERACION_OT_FIBRA",
        "nombre": "Generaci\u00f3n de OT Fibra",
    },
    "5": {
        "codigo": "CONFIRMACION_FTTH",
        "nombre": "Confirmaci\u00f3n FTTH",
    },
    "6": {
        "codigo": "GRAFICA_PATHTRAK",
        "nombre": "Gr\u00e1fica PathTrak",
    },
    "7": {
        "codigo": "CONFIRMACION_HFC",
        "nombre": "Confirmaci\u00f3n HFC",
    },
    "8": {
        "codigo": "FILTRACION_RUIDO",
        "nombre": "Recepci\u00f3n de informaci\u00f3n filtraci\u00f3n de ruido",
    },
    "9": {
        "codigo": "GENERACION_OT_COAXIAL",
        "nombre": "Generaci\u00f3n de OT Coaxial",
    },
    "0": {
        "codigo": "OTRO_TIPO_SOLICITUD",
        "nombre": "Otro tipo de solicitud",
    },
}

CLASIFICACIONES_INTERNAS: Final[tuple[str, ...]] = (
    "ERROR_ESCALAMIENTO",
    "MESA_INCORRECTA",
    "SOLICITUD_NO_SOPORTADA",
)


def opciones_publicas() -> list[dict[str, str]]:
    order = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]

    return [
        {
            "numero": numero,
            **SOLICITUDES[numero],
        }
        for numero in order
    ]


def obtener_opcion(numero: str) -> dict[str, str] | None:
    item = SOLICITUDES.get(str(numero or "").strip())
    return dict(item) if item else None


def texto_menu() -> str:
    lines = [
        "Hola 👋 Te damos la bienvenida a nuestro canal de atención.",
        "",
        "Soy ATLAS y estaré apoyándote con tu solicitud.\n\nPara continuar, por favor selecciona una de las siguientes opciones:",
        "",
    ]

    for item in opciones_publicas():
        lines.append(f"{item['numero']}. {item['nombre']}")

    lines.extend(
        [
            "",
            "Ingresa únicamente el número de la opción que deseas gestionar.",
        ]
    )

    return "\n".join(lines)