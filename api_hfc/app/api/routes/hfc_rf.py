from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.hfc_rf_service import service

router = APIRouter(prefix="/api/deco/hfc/rf", tags=["HFC RF / Ruido"])


def fail(exc: Exception) -> None:
    raise HTTPException(
        status_code=502,
        detail={"code": "HFC_RF_UNAVAILABLE", "error": str(exc)},
    )


@router.get("/schema")
def schema():
    try:
        return service.schema()
    except Exception as exc:
        fail(exc)


@router.get("/resumen")
def resumen():
    try:
        return service.resumen()
    except Exception as exc:
        fail(exc)


@router.get("/upstreams")
def upstreams(
    node: str | None = None,
    cmts: str | None = None,
    upstream: str | None = None,
    search: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    try:
        return service.upstreams(
            node=node,
            cmts=cmts,
            upstream=upstream,
            search=search,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        fail(exc)


@router.get("/ranking")
def ranking(
    node: str | None = None,
    cmts: str | None = None,
    search: str | None = None,
    limit: int = Query(20, ge=1, le=200),
):
    try:
        return service.ranking(node=node, cmts=cmts, search=search, limit=limit)
    except Exception as exc:
        fail(exc)


@router.get("/spectrum")
def spectrum(
    node: str | None = None,
    cmts: str | None = None,
    upstream: str | None = None,
    limit: int = Query(5000, ge=1, le=20000),
):
    try:
        return service.spectrum(node=node, cmts=cmts, upstream=upstream, limit=limit)
    except Exception as exc:
        fail(exc)
