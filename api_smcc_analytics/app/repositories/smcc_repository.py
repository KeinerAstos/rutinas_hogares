from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import get_settings


class SmccAnalyticsRepository:
    def __init__(self) -> None:
        settings = get_settings()

        self.root = settings.tracking_data_dir
        self.sessions_dir = self.root / "sessions"
        self.active_file = self.sessions_dir / "active.json"
        self.history_dir = self.sessions_dir / "history"
        self.events_dir = self.root / "events"

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            OSError,
        ):
            return default

    def health(self) -> dict[str, Any]:
        return {
            "data_dir": str(self.root),
            "data_dir_exists": self.root.is_dir(),
            "sessions_dir_exists": self.sessions_dir.is_dir(),
            "active_file_exists": self.active_file.is_file(),
            "history_dir_exists": self.history_dir.is_dir(),
            "events_dir_exists": self.events_dir.is_dir(),
        }

    def list_active(self) -> list[dict[str, Any]]:
        raw = self._read_json(self.active_file, {})

        if not isinstance(raw, dict):
            return []

        return [
            item
            for item in raw.values()
            if isinstance(item, dict)
        ]

    def list_history_range(
        self,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        current = date_from

        while current <= date_to:
            path = self.history_dir / f"{current.isoformat()}.json"
            raw = self._read_json(path, [])

            if isinstance(raw, list):
                items.extend(
                    item
                    for item in raw
                    if isinstance(item, dict)
                )

            current += timedelta(days=1)

        return items

    def list_events_range(
        self,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        current = date_from

        while current <= date_to:
            path = self.events_dir / f"{current.isoformat()}.jsonl"

            if path.is_file():
                try:
                    with path.open("r", encoding="utf-8") as fh:
                        for raw_line in fh:
                            line = raw_line.strip()

                            if not line:
                                continue

                            try:
                                item = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            if isinstance(item, dict):
                                items.append(item)
                except OSError:
                    pass

            current += timedelta(days=1)

        return items