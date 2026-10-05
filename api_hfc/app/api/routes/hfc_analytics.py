from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services import hfc_analytics_service as service


router = APIRouter(
    prefix="/api/deco/hfc/analytics",
    tags=["HFC Analytics"],
)


@router.get("/resumen")
def resumen():
    try:
        return service.resumen()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "codigo": "HFC_ANALYTICS_UNAVAILABLE",
                "error": str(exc),
            },
        )


@router.get("/cmts")
def cmts(
    technology: Optional[str] = Query(
        default=None
    ),
):
    try:
        return service.cmts(
            technology=technology,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "codigo": "HFC_ANALYTICS_UNAVAILABLE",
                "error": str(exc),
            },
        )


@router.get("/nodos")
def nodos(
    technology: Optional[str] = Query(
        default=None
    ),
    cmts: Optional[str] = Query(
        default=None
    ),
    search: Optional[str] = Query(
        default=None
    ),
    limit: int = Query(
        default=500,
        ge=1,
        le=5000,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
):
    try:
        return service.nodos(
            technology=technology,
            cmts_name=cmts,
            search=search,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "codigo": "HFC_ANALYTICS_UNAVAILABLE",
                "error": str(exc),
            },
        )


@router.get("/nodo/{nombre}")
def nodo(
    nombre: str,
    cmts: Optional[str] = Query(
        default=None
    ),
):
    try:
        return service.nodo(
            nombre,
            cmts_name=cmts,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "codigo": "HFC_ANALYTICS_UNAVAILABLE",
                "error": str(exc),
            },
        )