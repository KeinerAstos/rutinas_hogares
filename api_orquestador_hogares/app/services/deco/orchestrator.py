# -*- coding: utf-8 -*-
"""
Orquestador central de Chat DECO.

Centraliza clasificación, ejecución, metadatos y contrato de respuesta,
pero delega el trabajo real a los servicios existentes para evitar
romper FTTH, Máximo, Helix, Inventario o PathTrak.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from app.services.deco.registry import (
    capability_to_dict,
    catalogo_capacidades,
    clasificar_solicitud,
)

from app.services.deco import operation_service


ORCHESTRATOR_VERSION = "DECO_CENTRAL_V1"


def clasificar(
    mensaje: str,
    *,
    recurso_heredado: str = "direct",
) -> dict[str, Any]:
    capacidad = clasificar_solicitud(
        mensaje,
        recurso_heredado=recurso_heredado,
    )

    return capability_to_dict(capacidad)


def obtener_recurso(
    mensaje: str,
    *,
    recurso_heredado: str = "direct",
) -> str:
    capacidad = clasificar_solicitud(
        mensaje,
        recurso_heredado=recurso_heredado,
    )

    # Conservamos la política de colas ya validada.
    return capacidad.recurso


def _normalizar_resultado(
    resultado: Any,
) -> dict[str, Any]:
    if isinstance(resultado, dict):
        return dict(resultado)

    return {
        "ok": True,
        "tipo_respuesta": "resultado",
        "respuesta": str(resultado),
    }


def ejecutar(
    *,
    mensaje: str,
    conversation_id: str | None,
    ejecutor_heredado: Callable[
        [str, str | None],
        Any,
    ],
    recurso_heredado: str = "direct",
) -> dict[str, Any]:
    inicio = time.perf_counter()

    capacidad = clasificar_solicitud(
        mensaje,
        recurso_heredado=recurso_heredado,
    )

    try:
        if capacidad.dominio == "operacion":
            resultado_crudo = operation_service.ejecutar(
                mensaje
            )
        else:
            resultado_crudo = ejecutor_heredado(
                mensaje,
                conversation_id,
            )

        resultado = _normalizar_resultado(
            resultado_crudo,
        )

    except Exception as exc:
        resultado = {
            "ok": False,
            "tipo_respuesta": "deco_orchestrator_error",
            "codigo": "DECO_ORCHESTRATOR_EXECUTION_ERROR",
            "respuesta": (
                "No fue posible completar la operación "
                "solicitada en Chat DECO."
            ),
            "error": f"{type(exc).__name__}: {exc}",
        }

    resultado.setdefault(
        "dominio",
        capacidad.dominio,
    )

    resultado.setdefault(
        "operacion",
        capacidad.operacion,
    )

    resultado.setdefault(
        "descripcion_operacion",
        capacidad.descripcion,
    )

    resultado.setdefault(
        "orquestador",
        ORCHESTRATOR_VERSION,
    )

    resultado.setdefault(
        "duracion_orquestador_seg",
        round(
            time.perf_counter() - inicio,
            2,
        ),
    )

    return resultado


def health() -> dict[str, Any]:
    catalogo = catalogo_capacidades()

    operation_health = operation_service.health()

    return {
        "ok": True,
        "version": ORCHESTRATOR_VERSION,
        "centralizado": True,
        "hfc_modo": "HEREDADO_SIN_CAMBIOS",
        "dominios": catalogo["dominios"],
        "operacion": operation_health,
    }
