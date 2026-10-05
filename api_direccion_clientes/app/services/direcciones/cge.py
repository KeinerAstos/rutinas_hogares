from __future__ import annotations

import re
import unicodedata
from typing import Any

SERVICE_VERSION = "CGE_DIRECCIONES_DESCRIPCION_V1"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.upper().split())


def es_categoria_reclamacion_usuario(value: str) -> bool:
    categoria = _normalize(value)
    return categoria.startswith("SERVICIOS FIJOS > RECLAMACION > USUARIO")


def _extract(pattern: str, texto: str) -> str:
    match = re.search(pattern, texto or "", flags=re.I | re.S)
    if not match:
        return ""
    return _clean(match.group(1))


def extraer_direccion_cge(descripcion: str) -> dict[str, Any]:
    texto = _clean(descripcion)

    municipio = _extract(
        r"\bMunicipio\s+(.+?)(?=\s+Direcci[oó]n\s+Cliente\b|$)",
        texto,
    )

    direccion = _extract(
        r"\bDirecci[oó]n\s+Cliente\s*(?:Calle\s*:\s*)?(.+?)"
        r"(?=\s+Com\s*:|\s+Nombre\s+Cliente\b|$)",
        texto,
    )

    nombre = _extract(
        r"\bNombre\s+Cliente\s+(.+?)"
        r"(?=\s+Contacto\s+telef[oó]nico\b|\s+Cuenta\b|$)",
        texto,
    )

    contacto = _extract(
        r"\bContacto\s+telef[oó]nico\s+Cliente\s+(.+?)"
        r"(?=\s+Cuenta\b|$)",
        texto,
    )

    cuenta = _extract(
        r"\bCuenta\s+([0-9]{6,12})\b",
        texto,
    )

    serial_ont = _extract(
        r"\bSerial\s+Ont\s+Afectada\s+([A-Za-z0-9:._-]+)",
        texto,
    )

    id_troncal = _extract(
        r"\bID\s+TRONCAL\s+([A-Za-z0-9._-]+)",
        texto,
    )

    subtipo_trabajo = _extract(
        r"\bSubtipo\s+de\s+trabajo\s+(.+?)"
        r"(?=\s+Mostrar\b|\s+Categor[ií]a\b|$)",
        texto,
    )

    # CGE_RECLAMACION_FORMATO_INCIDENTE_V2
    #
    # Los patrones historicos anteriores siguen teniendo prioridad.
    # Estos extractores solo rellenan campos que continuen vacios.

    if not direccion:
        # CGE_DIRECCION_DELIMITADOR_SUPERVISOR_V3_1
        direccion = _extract(
            r"\bDirecci[oó]n\s*:\s*"
            r"(?:Calle\s*:\s*)?"
            r"(.+?)"
            r"(?="
            r"\s+Supervisor\s+que\s+valida\s*:"
            r"|\s+Nombre\s+del\s+supervisor\s+que\s+valida\s*:"
            r"|\s+Numero\s+de\s+celular\s+Supervisor\s*:"
            r"|\s+Nombre\s+del\s+cliente\s*:"
            r"|\s+Nombre\s*:"
            r"|\s+Tel[eé]fono\s+de\s+contacto\s*:"
            r"|\s+\(Se\s+anexa\s+cuenta\b"
            r"|\s+Se\s+anexa\s+cuenta\b"
            r"|$"
            r")",
            texto,
        )

    if not cuenta:
        cuenta = _extract(
            r"\bCuenta\s+de\s+Usuario\s*:\s*"
            r"("
            r"[0-9]{6,12}"
            r"|"
            r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}"
            r")\b",
            texto,
        )

    if not cuenta:
        cuenta = _extract(
            r"\bCuenta\s+MAC\s*:\s*"
            r"("
            r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}"
            r")\b",
            texto,
        )

    if not nombre:
        nombre = _extract(
            r"\bNombre\s*:\s*(.+?)"
            r"(?="
            r"\s+Tel[eé]fono\s+de\s+contacto\s*:"
            r"|\s+Se\s+anexa\s+cuenta\b"
            r"|$"
            r")",
            texto,
        )

    if not contacto:
        contacto = _extract(
            r"\bTel[eé]fono\s+de\s+contacto\s*:\s*"
            r"([+0-9 ()_-]{7,25})",
            texto,
        )

    # CGE_CAMPOS_DIRECTOS_V3_2
    #
    # Formato CGE adicional:
    #   Cuenta: 77191308
    #   Nombre del cliente: ...
    #   TRONCAL: XS172H
    #
    # Solo completa campos que los formatos historicos no resolvieron.

    if not cuenta:
        cuenta = _extract(
            r"\bCuenta\s*:\s*([0-9]{6,12})\b",
            texto,
        )

    if not nombre:
        nombre = _extract(
            r"\bNombre\s+del\s+cliente\s*:\s*(.+?)"
            r"(?="
            r"\s+Tel[eé]fono\s+de\s+contacto\s*:"
            r"|\s+\(Se\s+anexa\s+cuenta\b"
            r"|\s+Se\s+anexa\s+cuenta\b"
            r"|$"
            r")",
            texto,
        )

    if not id_troncal:
        id_troncal = _extract(
            r"\bTRONCAL\s*:\s*([A-Za-z0-9._-]+)",
            texto,
        )

    cliente = {
        "cuenta": cuenta,
        "direccion": direccion,
        "municipio": municipio,
        "nombre_cliente": nombre,
        "contacto": contacto,
        "serial_ont": serial_ont,
        "id_troncal": id_troncal,
        "subtipo_trabajo": subtipo_trabajo,
        "fuente": "DESCRIPCION_HELIX",
    }

    ok = bool(direccion)

    return {
        "ok": ok,
        "codigo": (
            "DIRECCIONES_CGE_RECLAMACION_USUARIO_OK"
            if ok
            else "DIRECCIONES_CGE_DESCRIPCION_SIN_DIRECCION"
        ),
        "service_version": SERVICE_VERSION,
        "tipo_respuesta": "cge_reclamacion_usuario",
        "fuente": "DESCRIPCION_HELIX",
        "descripcion_ot": texto,
        "clientes_encontrados": 1 if ok else 0,
        "clientes": [cliente] if ok else [],
        "consulta_ejecutada": True,
    }
