from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.smcc_tracking_settings import get_smcc_tracking_settings


_LOCK = threading.RLock()


def _utcnow_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SmccTrackingRepository:
    def __init__(self) -> None:
        settings = get_smcc_tracking_settings()
        self.root = settings.data_dir
        self.sessions_dir = self.root / "sessions"
        self.events_dir = self.root / "events"
        self.active_file = self.sessions_dir / "active.json"
        self.history_dir = self.sessions_dir / "history"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        if not self.active_file.exists():
            self._write_json_atomic(self.active_file, {})

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default

    @staticmethod
    def _write_json_atomic(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp_name, path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass

    @staticmethod
    def _history_file(root: Path, iso_ts: str) -> Path:
        day = iso_ts[:10]
        return root / f"{day}.json"

    def list_active(self) -> list[dict[str, Any]]:
        with _LOCK:
            raw = self._read_json(self.active_file, {})
            return list(raw.values()) if isinstance(raw, dict) else []

    def get_active(self, conversation_id: str) -> dict[str, Any] | None:
        with _LOCK:
            raw = self._read_json(self.active_file, {})
            if not isinstance(raw, dict):
                return None
            item = raw.get(conversation_id)
            return item if isinstance(item, dict) else None

    def upsert_active(self, session: dict[str, Any]) -> None:
        cid = str(session["conversation_id"])
        with _LOCK:
            raw = self._read_json(self.active_file, {})
            if not isinstance(raw, dict):
                raw = {}
            raw[cid] = session
            self._write_json_atomic(self.active_file, raw)

    def close_session(self, session: dict[str, Any]) -> None:
        cid = str(session["conversation_id"])
        ended_at = str(session.get("ended_at") or _utcnow_iso())

        with _LOCK:
            raw = self._read_json(self.active_file, {})
            if not isinstance(raw, dict):
                raw = {}
            raw.pop(cid, None)
            self._write_json_atomic(self.active_file, raw)

            hist = self._history_file(self.history_dir, ended_at)
            items = self._read_json(hist, [])
            if not isinstance(items, list):
                items = []
            items.append(session)
            self._write_json_atomic(hist, items)

    def append_event(self, event: dict[str, Any]) -> None:
        ts = str(event.get("timestamp") or _utcnow_iso())
        path = self.events_dir / f"{ts[:10]}.jsonl"

        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(event, ensure_ascii=False))
                fh.write("\n")

    def list_history(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        files = sorted(self.history_dir.glob("*.json"), reverse=True)
        out: list[dict[str, Any]] = []

        for path in files:
            day = path.stem
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue

            items = self._read_json(path, [])
            if isinstance(items, list):
                out.extend(reversed(items))

            if len(out) >= limit:
                break

        return out[:limit]

    def find_session(self, conversation_id: str) -> dict[str, Any] | None:
        active = self.get_active(conversation_id)
        if active:
            return active

        for path in sorted(self.history_dir.glob("*.json"), reverse=True):
            items = self._read_json(path, [])
            if not isinstance(items, list):
                continue
            for item in reversed(items):
                if str(item.get("conversation_id")) == conversation_id:
                    return item
        return None