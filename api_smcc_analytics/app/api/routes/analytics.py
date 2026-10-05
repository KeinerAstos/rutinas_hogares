from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.analytics_service import SmccAnalyticsService


router = APIRouter(
    prefix="/api/v1/smcc/analytics",
    tags=["SMCC Analytics"],
)


def _service() -> SmccAnalyticsService:
    return SmccAnalyticsService()


@router.get("/dashboard")
def dashboard(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
):
    try:
        return _service().dashboard(
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )