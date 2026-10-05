# -*- coding: utf-8 -*-
"""
Servicio de consulta del inventario de red ATLAS.

Responsabilidad:
- Detectar comandos "ip equipo <NOMBRE>".
- Consultar el endpoint local de inventario.
- No abrir SSH ni conectarse directamente a la OLT.
- Devolver el contrato esperado por Chat DECO.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.paths import INVENTORY_TOKEN_FILE


DEFAULT_INVENTORY_URL = ""

DEFAULT_TOKEN_FILE = INVENTORY_TOKEN_FILE


def detectar_consulta_equipo(
    mensaje: str,
) -> Optional[str]:
    texto = str(mensaje or "").strip()

    patrones = [
        r"^\s*ip\s+equipo\s+(.+?)\s*$",
        r"^\s*consultar\s+olt\s+(.+?)\s*$",
        r"^\s*buscar\s+olt\s+(.+?)\s*$",
        r"^\s*ip\s+olt\s+(.+?)\s*$",
    ]

    for patron in patrones:
        coincidencia = re.match(
            patron,
            texto,
            flags=re.IGNORECASE,
        )

        if not coincidencia:
            continue

        equipo = coincidencia.group(1).strip().upper()

        if re.fullmatch(
            r"[A-Z0-9._-]{3,191}",
            equipo,
        ):
            return equipo

    return None


def _inventory_url() -> str:
    value = str(
        os.getenv(
            "ATLAS_INVENTORY_API_URL",
            DEFAULT_INVENTORY_URL,
        )
    ).strip()

    if not value:
        raise RuntimeError(
            "Falta configurar ATLAS_INVENTORY_API_URL en .env."
        )

    return value


def _token_file() -> Path:
    configurado = str(
        os.getenv(
            "ATLAS_INVENTORY_TOKEN_FILE",
            str(DEFAULT_TOKEN_FILE),
        )
    ).strip()

    return Path(configurado)


def _read_token() -> str:
    ruta = _token_file()

    if not ruta.exists() or not ruta.is_file():
        raise RuntimeError(
            "No existe el archivo de token del inventario ATLAS: "
            f"{ruta}"
        )

    token = ruta.read_text(
        encoding="utf-8",
        errors="replace",
    ).strip()

    if not token:
        raise RuntimeError(
            "El archivo de token del inventario ATLAS está vacío."
        )

    return token


def _request_inventory(
    accion: str,
    **parametros: Any,
) -> Dict[str, Any]:
    query = {
        "accion": accion,
        **{
            clave: valor
            for clave, valor in parametros.items()
            if valor is not None and str(valor).strip() != ""
        },
    }

    url = (
        _inventory_url()
        + "?"
        + urlencode(query)
    )

    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "X-Inventory-Token": _read_token(),
            "User-Agent": "ATLAS-DECO-Inventory/1.0",
        },
    )

    try:
        with urlopen(
            request,
            timeout=20,
        ) as response:
            raw = response.read().decode(
                "utf-8",
                errors="replace",
            )

    except HTTPError as error:
        raw = error.read().decode(
            "utf-8",
            errors="replace",
        )

        try:
            detalle = json.loads(raw)
        except Exception:
            detalle = {
                "mensaje": raw or str(error),
            }

        return {
            "ok": False,
            "http_status": error.code,
            "codigo": detalle.get(
                "codigo",
                "INVENTARIO_HTTP_ERROR",
            ),
            "mensaje": (
                detalle.get("mensaje")
                or detalle.get("error")
                or str(error)
            ),
            "detalle": detalle,
        }

    except URLError as error:
        return {
            "ok": False,
            "codigo": "INVENTARIO_NO_DISPONIBLE",
            "mensaje": (
                "No fue posible comunicar con la API local "
                "de inventario ATLAS."
            ),
            "detalle": str(error),
        }

    except Exception as error:
        return {
            "ok": False,
            "codigo": "INVENTARIO_REQUEST_ERROR",
            "mensaje": str(error),
        }

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "codigo": "INVENTARIO_JSON_INVALIDO",
            "mensaje": (
                "La API de inventario no devolvió JSON válido."
            ),
            "detalle": raw[:1000],
        }

    return data


def consultar_equipo(
    nombre_equipo: str,
) -> Dict[str, Any]:
    consulta = str(
        nombre_equipo or ""
    ).strip().upper()

    if not consulta:
        return {
            "ok": False,
            "tipo_respuesta": "inventario_atlas_equipo",
            "codigo": "EQUIPO_REQUERIDO",
            "respuesta": "Debe indicar el nombre del equipo.",
            "consulta": consulta,
        }

    resultado = _request_inventory(
        "equipo",
        valor=consulta,
    )

    if not resultado.get("ok"):
        codigo = str(
            resultado.get("codigo")
            or "INVENTARIO_ERROR"
        )

        if codigo == "EQUIPO_NO_ENCONTRADO":
            mensaje = (
                f"El equipo {consulta} no aparece en el "
                "inventario activo de ATLAS."
            )
        else:
            mensaje = str(
                resultado.get("mensaje")
                or "No fue posible consultar el inventario ATLAS."
            )

        return {
            "ok": False,
            "tipo_respuesta": "inventario_atlas_equipo",
            "codigo": codigo,
            "consulta": consulta,
            "fuente": "atlas",
            "respuesta": mensaje,
            "detalle": resultado.get("detalle"),
        }

    contenido = resultado.get("data")
    contenido = (
        contenido
        if isinstance(contenido, dict)
        else {}
    )

    filas = contenido.get("data")
    filas = filas if isinstance(filas, list) else []

    equipos = []

    for fila in filas:
        if not isinstance(fila, dict):
            continue

        equipos.append(
            {
                "id": fila.get("id"),
                "nombre_equipo": (
                    fila.get("nombre_equipo")
                    or consulta
                ),
                "tipo_equipo": (
                    fila.get("tipo_equipo")
                    or "EQUIPO"
                ),
                "ip_equipo": fila.get("ip_equipo"),
                "ip_valida": bool(
                    int(fila.get("ip_valida") or 0)
                ),
                "vendor": fila.get("vendor"),
                "instancia": fila.get("instancia"),
                "sds": fila.get("sds"),
                "ciudad": fila.get("ciudad"),
                "divisional": fila.get("divisional"),
                "regional": fila.get("regional"),
                "fecha_exportacion": (
                    fila.get("fecha_exportacion")
                ),
                "actualizado_en": fila.get("actualizado_en"),
            }
        )

    if not equipos:
        return {
            "ok": False,
            "tipo_respuesta": "inventario_atlas_equipo",
            "codigo": "EQUIPO_SIN_DATOS",
            "consulta": consulta,
            "fuente": "atlas",
            "respuesta": (
                f"ATLAS respondió, pero no entregó datos "
                f"para {consulta}."
            ),
        }

    return {
        "ok": True,
        "tipo_respuesta": "inventario_atlas_equipo",
        "codigo": "INVENTARIO_ATLAS_EQUIPO_OK",
        "consulta": consulta,
        "fuente": "atlas",
        "respuesta": {
            "total": len(equipos),
            "equipos": equipos,
        },
        "metodo": "consultar_equipo_inventario_atlas",
    }


def health_inventory() -> Dict[str, Any]:
    return _request_inventory("health")

