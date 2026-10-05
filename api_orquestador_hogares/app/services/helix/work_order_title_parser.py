from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _upper_ascii(text: str | None) -> str:
    clean = _clean(text)
    normalized = unicodedata.normalize("NFKD", clean)
    return "".join(
        ch for ch in normalized
        if not unicodedata.combining(ch)
    ).upper()


_RESERVED_NODE_WORDS = {
    "CON",
    "PATHTRAK",
    "CLUSTER",
    "VARIOS",
    "VARIAS",
    "AFECTACION",
    "DE",
    "DEL",
    "LOS",
    "LAS",
    "NODO",
    "NODE",
}

_NODE_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"

_NODE_DOUBLE_RE = re.compile(
    rf"\b(?:NODE|NODO)\s*:?\s*(?:NODE|NODO)\s*:?\s*({_NODE_TOKEN})\b",
    re.IGNORECASE,
)

_NODE_GENERIC_RE = re.compile(
    rf"\b(?:NODE|NODO)\s*:?\s*({_NODE_TOKEN})\b",
    re.IGNORECASE,
)

_TECH_PON_RE = re.compile(
    r"\b(XGS[- ]?PON|XGSPON|GPON|EPON|PON)\b",
    re.IGNORECASE,
)

_ELEMENT_RE = re.compile(
    r"(?P<element>[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:C600|C650|CP\d*|MA5800))"
    r"\s*,\s*(?:RACK|FRAME)\s*=",
    re.IGNORECASE,
)

# HELIX_TITLE_PARSER_GES_STA_TRONCAL_ELEMENT_V1
# Regla deliberadamente acotada a la familia observada en SmartIT:
#     GES STA TRONCAL <ELEMENTO> ...
# No se aplica a cualquier ocurrencia generica de TRONCAL.
_GES_STA_TRONCAL_ELEMENT_RE = re.compile(
    rf"\bGES\s+STA\s+TRONCAL(?:ES)?\s*:?\s*({_NODE_TOKEN})\b",
    re.IGNORECASE,
)

_GES_STA_TRONCAL_RESERVED = {
    "AFECTADA",
    "AFECTADO",
    "AFECTACION",
    "ARCIALMEMTE",
    "CAIDA",
    "CAIDO",
    "CAIDOS",
    "CAIDAS",
    "CON",
    "DE",
    "DEL",
    "EN",
    "FUERA",
    "INTERMITENTE",
    "PARCIAL",
    "PARCIALMENTE",
    "PRINCIPAL",
    "SECUNDARIA",
    "SECUNDARIO",
    "TOTAL",
    "TOTALMENTE",
    "VARIA",
    "VARIAS",
    "VARIOS",

    # HELIX_TITLE_PARSER_TRONCAL_TECH_GUARD_V3
    "EPON",
    "FOHFC",
    "FTTH",
    "GPON",
    "HFC",
    "MW",
    "PON",
    "XGS",
    "XGSPON",
    "XGS-PON",
}

_FIELD_PATTERNS = {
    "rack": re.compile(r"\bRACK\s*=\s*([^,\s]*)", re.IGNORECASE),
    "shelf": re.compile(r"\bSHELF\s*=\s*([^,\s]*)", re.IGNORECASE),
    "slot": re.compile(r"\bSLOT\s*=\s*([^,\s]*)", re.IGNORECASE),
    "port": re.compile(r"\bPORT\s*=\s*([^,\s]*)", re.IGNORECASE),
    "frame": re.compile(r"\bFRAME\s*=\s*([^,\s]*)", re.IGNORECASE),
    "subslot": re.compile(r"\bSUBSLOT\s*=\s*([^,\s]*)", re.IGNORECASE),
}


@dataclass
class WorkOrderTitleInfo:
    titulo_ot: str = ""
    fuente_titulo: str = ""

    tipo_red: str = ""
    tipo_elemento: str = ""

    nodo_detectado: str = ""
    nodos_detectados: list[str] = field(default_factory=list)
    estado_nodo: str = "SIN_NODO"

    es_hfc: bool = False
    es_pathtrak: bool = False
    es_ftth: bool = False
    es_troncal: bool = False
    es_mw: bool = False

    tecnologia_pon: str = ""
    elemento_red: str = ""

    rack: str = ""
    shelf: str = ""
    slot: str = ""
    port: str = ""
    frame: str = ""
    subslot: str = ""

    port_informado: bool = False
    parser_version: str = "HELIX_TITLE_PARSER_V2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_nodes(title: str) -> tuple[list[str], str]:
    upper = _upper_ascii(title)

    if re.search(r"\b(?:NODO|NODE)\s*:?\s*VARIOS\b", upper):
        return [], "NODOS_VARIOS"

    if re.search(r"\bVARIOS\s+NODOS\b|\bVARIAS\s+NODOS\b", upper):
        return [], "NODOS_VARIOS"

    if re.search(r"\bNODO\s+CLUSTER\b", upper):
        return [], "MULTIPLES_NODOS"

    nodes: list[str] = []

    for match in _NODE_DOUBLE_RE.finditer(title):
        candidate = match.group(1).upper()

        if (
            candidate not in _RESERVED_NODE_WORDS
            and candidate not in nodes
        ):
            nodes.append(candidate)

    for match in _NODE_GENERIC_RE.finditer(title):
        candidate = match.group(1).upper()

        if candidate in _RESERVED_NODE_WORDS:
            continue

        if candidate not in nodes:
            nodes.append(candidate)

    if len(nodes) == 1:
        return nodes, "UN_NODO"

    if len(nodes) > 1:
        return nodes, "MULTIPLES_NODOS"

    return [], "SIN_NODO"


def parse_work_order_title(
    title: str | None,
    *,
    source: str = "",
) -> dict[str, Any]:
    cleaned = _clean(title)
    upper = _upper_ascii(cleaned)

    nodes, node_state = _extract_nodes(cleaned)

    es_pathtrak = bool(re.search(r"\bPATHTRAK\b", upper))
    es_ftth = bool(re.search(r"\bFTTH\b", upper))
    es_troncal = bool(re.search(r"\bTRONCAL(?:ES)?\b", upper))
    es_mw = bool(
        re.search(r"\bENLACE\s+MW\b|\bMW\s*:", upper)
    )
    es_hfc = "HFC" in upper or "FOHFC" in upper

    tech = ""
    tech_match = _TECH_PON_RE.search(cleaned)

    if tech_match:
        tech = tech_match.group(1).upper().replace(" ", "")
        if tech == "XGS-PON":
            tech = "XGSPON"

    element = ""
    element_match = _ELEMENT_RE.search(cleaned)

    if element_match:
        element = element_match.group("element").upper()

    # HELIX_TITLE_PARSER_GES_STA_TRONCAL_ELEMENT_V1
    if es_troncal and not element:
        troncal_match = _GES_STA_TRONCAL_ELEMENT_RE.search(cleaned)

        if troncal_match:
            troncal_candidate = troncal_match.group(1).upper().strip()

            if (
                troncal_candidate
                and troncal_candidate not in _GES_STA_TRONCAL_RESERVED
            ):
                element = troncal_candidate

    fields: dict[str, str] = {}

    for name, regex in _FIELD_PATTERNS.items():
        match = regex.search(cleaned)
        fields[name] = (match.group(1) if match else "").strip()

    if es_ftth:
        tipo_red = "FTTH"
    elif es_hfc:
        tipo_red = "HFC"
    elif es_mw:
        tipo_red = "MW"
    else:
        tipo_red = ""

    if es_troncal:
        tipo_elemento = "TRONCAL"
    elif node_state in {
        "UN_NODO",
        "NODOS_VARIOS",
        "MULTIPLES_NODOS",
    }:
        tipo_elemento = "NODO"
    elif element:
        tipo_elemento = "OLT_PON"
    elif es_mw:
        tipo_elemento = "ENLACE_MW"
    else:
        tipo_elemento = ""

    info = WorkOrderTitleInfo(
        titulo_ot=cleaned,
        fuente_titulo=_clean(source),
        tipo_red=tipo_red,
        tipo_elemento=tipo_elemento,
        nodo_detectado=(
            nodes[0]
            if node_state == "UN_NODO" and nodes
            else ""
        ),
        nodos_detectados=nodes,
        estado_nodo=node_state,
        es_hfc=es_hfc,
        es_pathtrak=es_pathtrak,
        es_ftth=es_ftth,
        es_troncal=es_troncal,
        es_mw=es_mw,
        tecnologia_pon=tech,
        elemento_red=element,
        rack=fields["rack"],
        shelf=fields["shelf"],
        slot=fields["slot"],
        port=fields["port"],
        frame=fields["frame"],
        subslot=fields["subslot"],
        port_informado=bool(fields["port"]),
    )

    return info.to_dict()