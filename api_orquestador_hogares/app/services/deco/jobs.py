# -*- coding: utf-8 -*-
from collections.abc import Callable
from typing import Any

from app.services.deco.job_manager import deco_job_manager
from app.services.deco.orchestrator import (
    ejecutar as orchestrator_execute,
    health as orchestrator_health,
    obtener_recurso as orchestrator_resource,
)
from app.services.deco.registry import clasificar_recurso_cola


def obtener_recurso(message: str) -> str:
    recurso_heredado = clasificar_recurso_cola(message)

    return orchestrator_resource(
        message,
        recurso_heredado=recurso_heredado,
    )


def ejecutar(
    message: str,
    conversation_id: str | None,
    ejecutor_heredado: Callable[[str, str | None], Any],
):
    recurso_heredado = clasificar_recurso_cola(message)

    return orchestrator_execute(
        mensaje=message,
        conversation_id=conversation_id,
        ejecutor_heredado=ejecutor_heredado,
        recurso_heredado=recurso_heredado,
    )


def crear_job(
    *,
    message: str,
    conversation_id: str | None,
    user_id: str | None,
    ejecutor_heredado: Callable[[str, str | None], Any],
):
    resource = obtener_recurso(message)

    return deco_job_manager.submit(
        resource=resource,
        message=message,
        conversation_id=conversation_id,
        user_id=user_id,
        operation=lambda: ejecutar(
            message,
            conversation_id,
            ejecutor_heredado,
        ),
    )


def listar_jobs(
    *,
    limit: int,
    conversation_id: str | None,
):
    return {
        "ok": True,
        "jobs": deco_job_manager.list_recent(
            limit=limit,
            conversation_id=conversation_id,
        ),
        "stats": deco_job_manager.stats(),
    }


def obtener_job(job_id: str):
    return deco_job_manager.get(job_id)


def health():
    return orchestrator_health()