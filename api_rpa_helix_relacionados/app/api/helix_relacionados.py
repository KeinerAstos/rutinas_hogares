from __future__ import annotations

import time

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.helix.incident_related_batch_service import (
    detener_job,
    iniciar_job,
    obtener_job,
    pausar_job,
    reanudar_job,
)


router = APIRouter(
    prefix="/api/deco/helix/incidentes/relacionados",
    tags=["Helix Relacionados"],
)


class HelixRelacionadosJobRequest(BaseModel):
    incidentes: list[str] = Field(default_factory=list)
    workers: int = Field(default=7, ge=1, le=7)
    area: str = Field(default="front")


@router.post("/jobs")
def iniciar_job_api(
    request: HelixRelacionadosJobRequest,
) -> dict[str, Any]:
    return iniciar_job(
        incidentes=request.incidentes,
        workers=request.workers,
        area=request.area,
    )


@router.get("/jobs/{job_id}")
def obtener_job_api(
    job_id: str,
) -> dict[str, Any]:
    return obtener_job(job_id)



# ATLAS_HELIX_RELACIONADOS_INDIVIDUAL_8023_V1
@router.get("/individual/{inc}")
def consultar_incidente_individual_api(
    inc: str,
) -> dict[str, Any]:

    ticket = str(inc or "").strip().upper()

    start = iniciar_job(
        incidentes=[ticket],
        workers=1,
        area="front",
    )

    if not isinstance(start, dict) or not start.get("ok"):
        result = (
            dict(start)
            if isinstance(start, dict)
            else {}
        )

        result.setdefault(
            "ok",
            False,
        )
        result.setdefault(
            "tipo_respuesta",
            "helix_inc_relacionados",
        )
        result.setdefault(
            "origen",
            "HELIX_RELACIONADOS_API_8023",
        )
        result.setdefault(
            "incidente",
            ticket,
        )

        return result

    job_id = str(
        start.get("job_id") or ""
    ).strip()

    if not job_id:
        return {
            "ok": False,
            "tipo_respuesta": "helix_inc_relacionados",
            "codigo": "HELIX_BATCH_JOB_ID_VACIO",
            "origen": "HELIX_RELACIONADOS_API_8023",
            "incidente": ticket,
            "respuesta": (
                "8023 inicio la consulta pero no devolvio job_id."
            ),
            "error": "",
        }

    deadline = time.monotonic() + 600.0

    while time.monotonic() < deadline:

        current = obtener_job(job_id)

        if not isinstance(current, dict):
            return {
                "ok": False,
                "tipo_respuesta": "helix_inc_relacionados",
                "codigo": "HELIX_BATCH_RESPUESTA_INVALIDA",
                "origen": "HELIX_RELACIONADOS_API_8023",
                "incidente": ticket,
                "job_id": job_id,
                "respuesta": (
                    "8023 devolvio una respuesta de job no valida."
                ),
                "error": "",
            }

        estado_job = str(
            current.get("estado") or ""
        ).strip().upper()

        if estado_job == "COMPLETADO":

            resultados = current.get("resultados")

            if not isinstance(resultados, list):
                resultados = []

            if not resultados:
                return {
                    "ok": False,
                    "tipo_respuesta": "helix_inc_relacionados",
                    "codigo": "HELIX_BATCH_SIN_RESULTADO",
                    "origen": "HELIX_RELACIONADOS_API_8023",
                    "incidente": ticket,
                    "job_id": job_id,
                    "respuesta": (
                        "La consulta finalizo sin resultado individual."
                    ),
                    "error": "",
                }

            first = resultados[0]

            if not isinstance(first, dict):
                return {
                    "ok": False,
                    "tipo_respuesta": "helix_inc_relacionados",
                    "codigo": "HELIX_BATCH_RESULTADO_INVALIDO",
                    "origen": "HELIX_RELACIONADOS_API_8023",
                    "incidente": ticket,
                    "job_id": job_id,
                    "respuesta": (
                        "El resultado individual no tiene formato valido."
                    ),
                    "error": "",
                }

            result = dict(first)

            estado_resultado = str(
                result.get("estado") or ""
            ).strip().upper()

            result.setdefault(
                "ok",
                estado_resultado
                in {
                    "OK",
                    "SIN_RELACIONADOS",
                },
            )

            result.setdefault(
                "tipo_respuesta",
                "helix_inc_relacionados",
            )

            result.setdefault(
                "origen",
                "HELIX_RELACIONADOS_API_8023",
            )

            result.setdefault(
                "incidente",
                ticket,
            )

            result["job_id"] = job_id

            return result

        if estado_job == "ERROR":
            return {
                "ok": False,
                "tipo_respuesta": "helix_inc_relacionados",
                "codigo": "HELIX_BATCH_ERROR",
                "origen": "HELIX_RELACIONADOS_API_8023",
                "incidente": ticket,
                "job_id": job_id,
                "respuesta": (
                    "8023 no pudo completar la consulta individual."
                ),
                "error": str(
                    current.get("error") or ""
                ),
            }

        time.sleep(0.5)

    return {
        "ok": False,
        "tipo_respuesta": "helix_inc_relacionados",
        "codigo": "HELIX_BATCH_TIMEOUT",
        "origen": "HELIX_RELACIONADOS_API_8023",
        "incidente": ticket,
        "job_id": job_id,
        "respuesta": (
            "La consulta individual excedio 600 segundos."
        ),
        "error": "",
    }


def _normalizar_control_resultado(
    result: dict[str, Any],
) -> dict[str, Any]:
    result.setdefault("tipo_respuesta", "helix_inc_relacionados")
    result.setdefault("origen", "HELIX_RELACIONADOS_API_8023")
    return result


@router.post("/jobs/{job_id}/pausar")
def pausar_job_api(
    job_id: str,
) -> dict[str, Any]:
    return _normalizar_control_resultado(pausar_job(job_id))


@router.post("/jobs/{job_id}/reanudar")
def reanudar_job_api(
    job_id: str,
) -> dict[str, Any]:
    return _normalizar_control_resultado(reanudar_job(job_id))


@router.post("/jobs/{job_id}/detener")
def detener_job_api(
    job_id: str,
) -> dict[str, Any]:
    return _normalizar_control_resultado(detener_job(job_id))
