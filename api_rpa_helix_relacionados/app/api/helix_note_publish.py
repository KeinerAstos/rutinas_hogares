from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.helix.note_publish_service import (
    precheck_note_publish,
    precommit_note_publish_service,
)


router = APIRouter(
    tags=["HELIX NOTE PUBLISH"]
)


# ATLAS_HELIX_NOTE_PUBLISH_ROUTER_8023_V2
@router.post(
    "/api/deco/helix/notas/precheck"
)
def helix_note_publish_precheck(
    payload: dict[str, Any],
):
    return precheck_note_publish(
        payload
    )


@router.post(
    "/api/deco/helix/notas/precommit"
)
def helix_note_publish_precommit(
    payload: dict[str, Any],
):
    return precommit_note_publish_service(
        payload
    )
