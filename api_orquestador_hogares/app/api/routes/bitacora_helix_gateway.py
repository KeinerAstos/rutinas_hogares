"""Fachada HTTP CentralNOC para Bitacora Helix externa."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.clients.bitacora_helix import (
    BitacoraHelixGatewayError,
    create_job,
    get_job,
    get_queue_status,
    get_worker_status,
)


router = APIRouter(
    prefix="/api/deco/bitacora/helix",
    tags=["Bitacora Helix Gateway"],
)


_INC_RE = re.compile(r"^INC\d{6,12}$")


class JobRequest(BaseModel):
    incident: str
    incident_id: int = Field(default=0, ge=0)

    @field_validator("incident")
    @classmethod
    def validate_incident(cls, value: str) -> str:
        value = value.strip().upper()

        if not _INC_RE.fullmatch(value):
            raise ValueError(
                "incident debe tener formato INC seguido de 6 a 12 digitos"
            )

        return value


def _raise_gateway(
    exc: BitacoraHelixGatewayError,
) -> None:

    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.detail,
    )


@router.post("/jobs", status_code=202)
def bitacora_helix_create_job(
    body: JobRequest,
):
    try:
        status, data = create_job(
            incident=body.incident,
            incident_id=body.incident_id,
        )
    except BitacoraHelixGatewayError as exc:
        _raise_gateway(exc)

    return JSONResponse(
        status_code=status,
        content=data,
    )


@router.get("/jobs/{job_id}")
def bitacora_helix_get_job(
    job_id: str,
):
    try:
        status, data = get_job(job_id)
    except BitacoraHelixGatewayError as exc:
        _raise_gateway(exc)

    return JSONResponse(
        status_code=status,
        content=data,
    )


@router.get("/queue/status")
def bitacora_helix_queue_status():
    try:
        status, data = get_queue_status()
    except BitacoraHelixGatewayError as exc:
        _raise_gateway(exc)

    return JSONResponse(
        status_code=status,
        content=data,
    )


@router.get("/worker/status")
def bitacora_helix_worker_status():
    try:
        status, data = get_worker_status()
    except BitacoraHelixGatewayError as exc:
        _raise_gateway(exc)

    return JSONResponse(
        status_code=status,
        content=data,
    )