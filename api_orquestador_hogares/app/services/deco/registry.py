# -*- coding: utf-8 -*-
"""
Registro central de capacidades de Chat DECO.

No ejecuta automatizaciones. Su responsabilidad es clasificar cada
solicitud en un dominio y una operación sin alterar los servicios
existentes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DecoCapability:
    dominio: str
    operacion: str
    recurso: str
    descripcion: str


def _normalizar(texto: str) -> str:
    valor = str(texto or "").strip().lower()

    valor = "".join(
        caracter
        for caracter in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caracter)
    )

    return re.sub(r"\s+", " ", valor)


def clasificar_recurso_cola(
    mensaje: str,
    *,
    requiere_maximo: bool = False,
) -> str:
    """
    Politica central para seleccionar la cola del Job Manager.

    No ejecuta servicios. Solo devuelve maximo, pathtrak o direct.
    """
    if requiere_maximo:
        return "maximo"

    texto = _normalizar(mensaje)

    if (
        "helix" in texto
        or re.search(r"\bwo\d{3,20}\b", texto)
    ):
        return "maximo"

    pathtrak_terms = (
        "pathtrak",
        "captura qoe",
        "captura de qoe",
        "captura ondas",
        "captura de ondas",
        "ondas nodo",
        "espectro nodo",
        "analizador de espectro",
        "ruido nodo",
        "qoe nodo",
    )

    if any(term in texto for term in pathtrak_terms):
        return "pathtrak"

    maximo_terms = (
        "maximo",
        "evidencia ot",
        "redes neutras",
        "direccion",
        "cuentas afectadas",
        "cuenta rr",
        "serial",
        "mac ",
    )

    if (
        any(term in texto for term in maximo_terms)
        or re.search(r"\bot\d{5,12}\b", texto)
    ):
        return "maximo"

    return "direct"

def clasificar_solicitud(
    mensaje: str,
    *,
    recurso_heredado: str = "direct",
) -> DecoCapability:
    texto = _normalizar(mensaje)

    # --------------------------------------------------------
    # FTTH
    # --------------------------------------------------------

    if re.search(
        r"\bestado\s+troncal\b",
        texto,
    ):
        return DecoCapability(
            dominio="ftth",
            operacion="consultar_troncal",
            recurso=recurso_heredado,
            descripcion="Consulta operativa de troncal FTTH",
        )

    if re.search(
        r"\b(ip\s+equipo|ip\s+olt|consultar\s+olt|buscar\s+olt)\b",
        texto,
    ):
        return DecoCapability(
            dominio="ftth",
            operacion="consultar_olt",
            recurso=recurso_heredado,
            descripcion="Consulta de OLT en inventario ATLAS",
        )

    if re.search(
        r"\b(acs|diagnosticador|direccion(?:es)?\s+de\s+clientes)\b",
        texto,
    ) or re.search(
        r"^\s*(cuenta|serial|mac)\s+",
        texto,
    ):
        return DecoCapability(
            dominio="ftth",
            operacion="diagnostico_cliente",
            recurso=recurso_heredado,
            descripcion="Consulta ACS y Diagnosticador",
        )

    # --------------------------------------------------------
    # OPERACIÓN EXPLÍCITA
    # Debe evaluarse antes de Helix para distinguir la Bitácora.
    # --------------------------------------------------------

    if (
        re.search(r"\bincidentes?\s+activos?\b", texto)
        or re.search(r"\bbuscar\s+incidente\b", texto)
        or re.search(r"\binc\d{5,20}\b", texto)
    ):
        return DecoCapability(
            dominio="operacion",
            operacion="consultar_incidentes",
            recurso=recurso_heredado,
            descripcion="Consulta de incidentes operativos",
        )

    if (
        "bitacora" in texto
        or "mesa de ayuda" in texto
        or re.search(r"\bseguimientos?\s+(?:bitacora\s+)?ot\b", texto)
    ):
        return DecoCapability(
            dominio="operacion",
            operacion="consultar_bitacora",
            recurso=recurso_heredado,
            descripcion="Consulta de bitácora operativa",
        )

    # --------------------------------------------------------
    # REDES NEUTRAS
    # --------------------------------------------------------



    # --------------------------------------------------------
    # INVENTARIO
    # --------------------------------------------------------

    if re.search(
        r"\b(inventario|atlas)\b",
        texto,
    ):
        operacion = (
            "consultar_nodo"
            if "nodo" in texto
            else "consultar_equipo"
        )

        return DecoCapability(
            dominio="inventario",
            operacion=operacion,
            recurso=recurso_heredado,
            descripcion="Consulta del inventario operativo ATLAS",
        )

    # --------------------------------------------------------
    # OPERACIÓN
    # --------------------------------------------------------

    if re.search(
        r"\b(incidente|incidentes\s+activos)\b",
        texto,
    ):
        return DecoCapability(
            dominio="operacion",
            operacion="consultar_incidentes",
            recurso=recurso_heredado,
            descripcion="Consulta de incidentes operativos",
        )

    if re.search(
        r"\b(bitacora|mesa\s+de\s+ayuda)\b",
        texto,
    ):
        return DecoCapability(
            dominio="operacion",
            operacion="consultar_bitacora",
            recurso=recurso_heredado,
            descripcion="Consulta de bitácora operativa",
        )

    # --------------------------------------------------------
    # HFC Y OTROS FLUJOS HEREDADOS
    # --------------------------------------------------------

    if re.search(
        r"\b(pathtrak|qoe|ruido|spectrum|estado\s+nodo|diagnostico\s+real)\b",
        texto,
    ):
        return DecoCapability(
            dominio="hfc_heredado",
            operacion="flujo_existente",
            recurso=recurso_heredado,
            descripcion="Flujo HFC existente sin modificación",
        )

    return DecoCapability(
        dominio="deco",
        operacion="consulta_general",
        recurso=recurso_heredado,
        descripcion="Consulta general de Chat DECO",
    )


def capability_to_dict(
    capability: DecoCapability,
) -> dict[str, Any]:
    return asdict(capability)


def catalogo_capacidades() -> dict[str, Any]:
    return {
        "ok": True,
        "orquestador": "DECO_CENTRAL_V1",
        "dominios": {
            "ftth": [
                "consultar_olt",
                "consultar_troncal",
                "diagnostico_cliente",
            ],
            "inventario": [
                "consultar_nodo",
                "consultar_equipo",
            ],
            "operacion": [
                "consultar_incidentes",
                "consultar_bitacora",
            ],
            "hfc_heredado": [
                "flujo_existente",
            ],
        },
    }

# CATALOGO_HFC_RAPIDO_V1
CAPACIDADES_HFC_RAPIDAS = [
    {
        "intencion": "HFC_ESTADO_NODO",
        "dominio": "hfc",
        "nombre": "Estado de nodo",
        "frase": "estado nodo <NODO>",
        "ejemplo": "estado nodo TCG",
        "metodo": "GET",
        "endpoint": "/api/deco/hfc/estado/{nodo}",
        "rapida": True,
        "pathtrak": False,
    },
    {
        "intencion": "HFC_DIAGNOSTICO_PROFUNDO",
        "dominio": "hfc",
        "nombre": "Diagnostico profundo",
        "frase": "diagnostico profundo nodo <NODO>",
        "ejemplo": "diagnostico profundo nodo TCG",
        "metodo": "CHAT_JOB",
        "endpoint": "/api/deco/jobs",
        "rapida": False,
        "pathtrak": True,
    },
]


def catalogo_hfc_rapido():
    return {"ok": True, "dominio": "hfc", "capacidades": CAPACIDADES_HFC_RAPIDAS}

# CATALOGO_HFC_MODEMS_NODO_V1
HFC_CAPABILITY_MODEMS_NODO = {
    "id": "HFC_MODEMS_NODO",
    "titulo": "Módems de nodo",
    "descripcion": "Resumen rápido de cable módems por nodo.",
    "metodo": "GET",
    "endpoint": "/api/deco/hfc/modems/{nodo}",
    "rapido": True,
    "pathtrak": False,
    "ejemplos": [
        "modems nodo TCG",
        "modems nodo 3GA",
        "modems nodo PMA",
    ],
}


# CATALOGO_HFC_ESTADO_CMTS_V1
HFC_CAPABILITY_ESTADO_CMTS = {
    "id": "HFC_ESTADO_CMTS",
    "titulo": "Estado CMTS",
    "descripcion": "Resumen rápido de afectación y cable módems de un CMTS.",
    "metodo": "GET",
    "endpoint": "/api/deco/hfc/cmts/estado/{cmts}",
    "rapido": True,
    "pathtrak": False,
    "ejemplos": [
        "estado cmts GIRA-GIRA-H-03-CS100G",
        "estado cmts ARME-GALA-H-02-CS100G",
    ],
}


# CATALOGO_HFC_NODOS_CMTS_V1
HFC_CAPABILITY_NODOS_CMTS = {
    "id": "HFC_NODOS_CMTS",
    "titulo": "Nodos por CMTS",
    "descripcion": "Lista completa de nodos asociados a un CMTS.",
    "metodo": "GET",
    "endpoint": "/api/deco/hfc/cmts/nodos/{cmts}",
    "rapido": True,
    "pathtrak": False,
    "ejemplos": [
        "nodos cmts GIRA-GIRA-H-03-CS100G",
        "nodos cmts ARME-GALA-H-02-CS100G",
    ],
}


# CATALOGO_HFC_EXPANDIDO_V1
HFC_CAPABILITY_BUSCAR_NODO = {
    "id": "HFC_BUSCAR_NODO",
    "endpoint": "/api/deco/hfc/buscar/nodo/{nodo}",
    "rapido": True,
    "pathtrak": False,
}

HFC_CAPABILITY_AFECTADOS_CMTS = {
    "id": "HFC_AFECTADOS_CMTS",
    "endpoint": "/api/deco/hfc/cmts/afectados/{cmts}",
    "rapido": True,
    "pathtrak": False,
}

HFC_CAPABILITY_NODOS_CRITICOS_ZONA = {
    "id": "HFC_NODOS_CRITICOS_ZONA",
    "endpoint": "/api/deco/hfc/zona/criticos/{zona}",
    "rapido": True,
    "pathtrak": False,
}

HFC_CAPABILITY_BUSCAR_CMTS = {
    "id": "HFC_BUSCAR_CMTS",
    "endpoint": "/api/deco/hfc/buscar/cmts/{texto}",
    "rapido": True,
    "pathtrak": False,
}


# CATALOGO_HFC_HISTORIAL_V1
HFC_CAPABILITY_HISTORIAL_NODO = {
    "id": "HFC_HISTORIAL_NODO",
    "endpoint": "/api/deco/hfc/nodo/historial/{nodo}",
    "rapido": True,
    "pathtrak": False,
}
