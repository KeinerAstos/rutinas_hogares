from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.smcc_tracking_settings import get_smcc_tracking_settings


_lock = threading.RLock()


def _now() -> datetime:
    return datetime.now().astimezone()


class SmccConversationRepository:
    def __init__(self) -> None:
        settings = get_smcc_tracking_settings()
        self.root = Path(settings.data_dir) / "conversations"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, conversation_id: str) -> Path:
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.jsonl"

    def append(self, record: dict[str, Any]) -> None:
        path = self._path(str(record["conversation_id"]))
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with _lock:
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        path = self._path(conversation_id)
        if not path.exists():
            return []

        result: list[dict[str, Any]] = []
        with _lock:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        item = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if item.get("conversation_id") == conversation_id:
                        result.append(item)

        return result
