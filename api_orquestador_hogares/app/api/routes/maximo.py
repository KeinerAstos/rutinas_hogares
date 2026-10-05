from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.maximo.config import settings
from app.services.maximo.service import (
    consultar_evidencia_maximo,
    detectar_consulta_maximo,
)


router = APIRouter(
    prefix="/api/maximo",
    tags=["Máximo - Redes Neutras"],
)


class MaximoChatRequest(BaseModel):
    mensaje: str = Field(
        min_length=1,
        max_length=500,
    )


class MaximoOtRequest(BaseModel):
    ot: str = Field(
        min_length=6,
        max_length=20,
    )


@router.get("/health")
def maximo_health():
    return {
        "ok": True,
        "service": "atlas_maximo",
        "configured": bool(
            settings.maximo_url
            and settings.maximo_user
            and settings.maximo_password
        ),
        "headless": settings.headless,
        "download_dir": str(
            settings.downloads_dir
        ),
    }


@router.post("/chat")
def maximo_chat(req: MaximoChatRequest):
    ot = detectar_consulta_maximo(req.mensaje)

    if not ot:
        raise HTTPException(
            status_code=422,
            detail=(
                "Escribe una consulta como: "
                "evidencia OT5311599."
            ),
        )

    return consultar_evidencia_maximo(ot)


@router.post("/evidencias")
def maximo_evidencias(req: MaximoOtRequest):
    ot = detectar_consulta_maximo(
        f"evidencia {req.ot}"
    )

    if not ot:
        raise HTTPException(
            status_code=422,
            detail="La OT no tiene un formato válido.",
        )

    return consultar_evidencia_maximo(ot)


@router.get("/files/{filename}")
def maximo_file(filename: str):
    safe_name = Path(filename).name

    if safe_name != filename:
        raise HTTPException(
            status_code=403,
            detail="Nombre de archivo no permitido.",
        )

    base = settings.downloads_dir.resolve()
    path = (base / safe_name).resolve()

    try:
        path.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Ruta no permitida.",
        )

    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Evidencia no encontrada.",
        )

    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/octet-stream",
    )
