from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query, Response

from app.services.mesa_ayuda.smcc_tracking_schema import SmccTrackingEventIn
from app.services.mesa_ayuda.smcc_tracking_service import SmccTrackingService
from app.services.mesa_ayuda.smcc_conversation_log_service import SmccConversationLogService
from app.config.smcc_analytics_settings import get_smcc_analytics_settings
from app.clients.smcc_analytics import obtener_dashboard


router = APIRouter(
    prefix="/api/deco/mesa-ayuda/smcc/tracking",
    tags=["SMCC Tracking"],
)


def _service() -> SmccTrackingService:
    return SmccTrackingService()


@router.get("/health")
def tracking_health():
    return _service().health()


@router.post("/events")
def tracking_event(req: SmccTrackingEventIn):
    return _service().register_event(req)


@router.get("/active")
def tracking_active():
    return {
        "ok": True,
        "items": _service().active(),
    }


@router.get("/sessions")
def tracking_sessions(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
):
    return {
        "ok": True,
        "items": _service().history(date_from, date_to, limit),
    }


@router.get("/messages")
def tracking_messages(conversation_id: str = Query(min_length=1, max_length=200)):
    return {
        "ok": True,
        "items": SmccConversationLogService().messages(conversation_id),
    }

@router.get("/sessions/{conversation_id:path}")
def tracking_session_detail(conversation_id: str):
    item = _service().detail(conversation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Sesion SMCC no encontrada.")
    return {"ok": True, "session": item}


@router.get("/summary")
def tracking_summary():
    return {
        "ok": True,
        "summary": _service().summary(),
    }
# SMCC_ANALYTICS_THIN_PROXY_V1
@router.get("/analytics/dashboard")
def tracking_analytics_dashboard(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
):
    return obtener_dashboard(
        date_from=date_from,
        date_to=date_to,
    )
