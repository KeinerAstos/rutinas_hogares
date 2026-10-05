"""Helpers de presentacion PathTrak extraidos del motor DECO heredado."""

from pathlib import Path
from typing import Any, Dict, Optional


def _pathtrak_public_url(screenshot: Optional[str]) -> str:
    if not screenshot:
        return ""

    filename = Path(screenshot).name
    return f"/api/deco/screenshots/pathtrak/{filename}"


def construir_respuesta_pathtrak(resultado: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte la respuesta de PathTrak en una estructura segura para JSON.

    También soporta una captura combinada QoE + Ruido generada dentro de la
    misma sesión del navegador.
    """
    if not isinstance(resultado, dict):
        return {
            "ok": False,
            "tipo_respuesta": "pathtrak_captura",
            "respuesta": "PathTrak devolvió una respuesta no válida.",
            "error": "RESPUESTA_PATHTRAK_INVALIDA",
        }

    nodo = str(resultado.get("nodo") or "")
    region = str(resultado.get("region") or "")
    tipo = str(resultado.get("tipo") or "")
    error = str(resultado.get("error") or "Error desconocido")
    debug = resultado.get("debug")

    def normalizar_captura(
        captura: Any,
        tipo_interno: str,
        tipo_label: str,
    ) -> Dict[str, Any]:
        captura = captura if isinstance(captura, dict) else {}
        screenshot = captura.get("screenshot")
        return {
            "ok": bool(captura.get("ok")),
            "tipo": tipo_interno,
            "tipo_label": tipo_label,
            "nodo": str(captura.get("nodo") or nodo),
            "region": str(captura.get("region") or region),
            "url": captura.get("url"),
            "screenshot": screenshot,
            "public_url": _pathtrak_public_url(screenshot),
            "error": captura.get("error"),
            "debug": captura.get("debug"),
        }

    if tipo == "qoe_ruido":
        capturas = resultado.get("capturas")
        capturas = capturas if isinstance(capturas, dict) else {}

        qoe = normalizar_captura(
            capturas.get("qoe"),
            "qoe",
            "QoE / Estado del nodo",
        )
        ruido = normalizar_captura(
            capturas.get("ruido") or capturas.get("ondas"),
            "ondas",
            "Ruido / Spectrum",
        )

        cantidad_ok = sum([bool(qoe.get("ok")), bool(ruido.get("ok"))])

        if cantidad_ok == 2:
            codigo = "PATHTRAK_QOE_RUIDO_OK"
            estado = "OK"
            mensaje = (
                "Se generaron las capturas QoE y Ruido utilizando una "
                "sola sesión de PathTrak."
            )
        elif cantidad_ok == 1:
            codigo = "PATHTRAK_QOE_RUIDO_PARCIAL"
            estado = "PARCIAL"
            mensaje = (
                "PathTrak generó una de las dos capturas. Revisa el "
                "detalle de la captura que no pudo completarse."
            )
        else:
            return {
                "ok": False,
                "tipo_respuesta": "pathtrak_captura",
                "codigo": "PATHTRAK_QOE_RUIDO_ERROR",
                "respuesta": {
                    "tipo": "qoe_ruido",
                    "tipo_label": "QoE + Ruido",
                    "nodo": nodo,
                    "region": region,
                    "estado": "ERROR",
                    "mensaje": "No pude generar las capturas QoE y Ruido.",
                    "capturas": [qoe, ruido],
                    "debug": debug,
                },
                "error": error,
            }

        return {
            "ok": True,
            "tipo_respuesta": "pathtrak_captura",
            "codigo": codigo,
            "respuesta": {
                "tipo": "qoe_ruido",
                "tipo_label": "QoE + Ruido",
                "nodo": nodo,
                "region": region,
                "estado": estado,
                "mensaje": mensaje,
                "capturas": [qoe, ruido],
                "public_urls": [
                    item.get("public_url")
                    for item in [qoe, ruido]
                    if item.get("public_url")
                ],
            },
        }

    if not resultado.get("ok"):
        tipo_label = (
            "Ruido / Spectrum"
            if tipo == "ondas"
            else "QoE / Estado del nodo"
        )

        if "PATHTRAK_PNM_SIN_LICENCIA" in error:
            return {
                "ok": False,
                "tipo_respuesta": "pathtrak_captura",
                "codigo": "PATHTRAK_PNM_SIN_LICENCIA",
                "respuesta": {
                    "tipo": tipo,
                    "tipo_label": tipo_label,
                    "nodo": nodo,
                    "region": region,
                    "estado": "NO_DISPONIBLE",
                    "mensaje": (
                        "PathTrak informa que el nodo PNM seleccionado "
                        "no tiene licencia."
                    ),
                    "aclaracion": (
                        "Esto impide generar la gráfica de ruido, "
                        "pero no confirma que el nodo esté caído."
                    ),
                    "debug": debug,
                },
                "error": "El nodo PNM seleccionado no tiene licencia.",
            }

        return {
            "ok": False,
            "tipo_respuesta": "pathtrak_captura",
            "respuesta": {
                "tipo": tipo,
                "tipo_label": tipo_label,
                "nodo": nodo,
                "region": region,
                "estado": "ERROR",
                "mensaje": "No pude generar la captura de PathTrak.",
                "debug": debug,
            },
            "error": error,
        }

    screenshot = resultado.get("screenshot")
    tipo_label = (
        "Ruido / Spectrum"
        if tipo == "ondas"
        else "QoE / Estado del nodo"
    )

    respuesta_pathtrak = {
        "tipo": tipo,
        "tipo_label": tipo_label,
        "nodo": nodo,
        "region": region,
        "url": resultado.get("url"),
        "screenshot": screenshot,
        "public_url": _pathtrak_public_url(screenshot),
    }

    for campo in (
        "estado",
        "estado_label",
        "mensaje",
        "detalle",
        "codigo",
        "aclaracion",
    ):
        valor = resultado.get(campo)

        if valor not in (None, ""):
            respuesta_pathtrak[campo] = valor

    return {
        "ok": True,
        "tipo_respuesta": "pathtrak_captura",
        "codigo": (
            resultado.get("codigo")
            or (
                "PATHTRAK_NODO_CAIDO"
                if resultado.get("estado")
                == "NODO_CAIDO"
                else "PATHTRAK_CAPTURA_OK"
            )
        ),
        "respuesta": respuesta_pathtrak,
    }


def _convertir_captura_pathtrak(captura: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not captura or not captura.get("ok"):
        return {
            "ok": False,
            "error": captura.get("error") if isinstance(captura, dict) else "No generada",
        }

    screenshot = captura.get("screenshot")
    return {
        "ok": True,
        "tipo": captura.get("tipo"),
        "region": captura.get("region"),
        "url": captura.get("url"),
        "screenshot": screenshot,
        "public_url": _pathtrak_public_url(screenshot),
    }

