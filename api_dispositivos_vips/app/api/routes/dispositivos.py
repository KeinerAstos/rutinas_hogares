from fastapi import APIRouter, HTTPException

from app.jobs.dispositivos import run_dispositivos_update
from app.services.dispositivos_service import list_devices, summarize_devices
from app.services.job_manager import job_manager


router = APIRouter()


@router.get("")
@router.get("/")
def dispositivos() -> dict:
    return list_devices()


@router.get("/resumen")
def dispositivos_resumen() -> dict:
    return summarize_devices()


@router.post("/actualizar", status_code=202)
def dispositivos_actualizar() -> dict:
    return job_manager.submit_unique("dispositivos_actualizar", run_dispositivos_update)


@router.get("/actualizar/{job_id}")
def dispositivos_actualizar_estado(job_id: str) -> dict:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Trabajo de actualización no encontrado.")
    return job
