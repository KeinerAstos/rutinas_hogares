from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.services.mesa_ayuda.smcc_conversation_repository import SmccConversationRepository


Role = Literal["CLIENTE", "ATLAS"]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SmccConversationLogService:
    def __init__(self) -> None:
        self.repo = SmccConversationRepository()

    def append_message(
        self,
        *,
        conversation_id: str,
        role: Role,
        text: str,
        message_id: str = "",
        timestamp: str = "",
    ) -> bool:
        cid = str(conversation_id or "").strip()
        body = str(text or "").strip()

        if not cid or not body:
            return False

        self.repo.append(
            {
                "conversation_id": cid,
                "role": role,
                "text": body,
                "message_id": str(message_id or "").strip() if role == "CLIENTE" else "",
                "timestamp": str(timestamp or "").strip() or _now_iso(),
            }
        )
        return True

    def messages(self, conversation_id: str) -> list[dict]:
        return self.repo.list_messages(str(conversation_id or "").strip())