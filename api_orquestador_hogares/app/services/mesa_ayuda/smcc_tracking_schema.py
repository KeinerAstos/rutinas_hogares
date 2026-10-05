from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SmccEventName = Literal[
    "SESSION_STARTED",
    "OPTION_SELECTED",
    "WO_RECEIVED",
    "ATLAS_REQUEST_STARTED",
    "ATLAS_REQUEST_FINISHED",
    "MESSAGE_SENT",
    "SESSION_FINISHED",
    "SESSION_ERROR",
    "HEARTBEAT",
    "SMCC_REQUEST",
    "ATLAS_RESPONSE",
    # SMCC_HELIX_AUDIT_V1_1
    "HELIX_NOTE_RESULT",
]


class SmccTrackingEventIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    event: SmccEventName
    wo: str | None = Field(default=None, max_length=32)
    operation: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, max_length=40)
    result: str | None = Field(default=None, max_length=120)
    error_code: str | None = Field(default=None, max_length=120)
    extension_version: str | None = Field(default=None, max_length=40)
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)