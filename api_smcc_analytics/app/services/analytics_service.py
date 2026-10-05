from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from math import ceil
from typing import Any

from app.config.settings import get_settings
from app.repositories.smcc_repository import SmccAnalyticsRepository


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Fecha invalida: {value}. Use YYYY-MM-DD."
        ) from exc


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0

    ordered = sorted(values)

    index = max(
        0,
        min(
            len(ordered) - 1,
            ceil(percentile * len(ordered)) - 1,
        ),
    )

    return int(ordered[index])


def _event_timestamp(item: dict[str, Any]) -> datetime | None:
    return _parse_datetime(item.get("timestamp"))


def _derive_sessions(
    events: list[dict[str, Any]],
    timeout_minutes: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for event in events:
        conversation_id = str(
            event.get("conversation_id") or ""
        ).strip()

        timestamp = _event_timestamp(event)

        if not conversation_id or timestamp is None:
            continue

        grouped[conversation_id].append(event)

    sessions: list[dict[str, Any]] = []

    timeout_seconds = timeout_minutes * 60

    for conversation_id, rows in grouped.items():
        rows.sort(
            key=lambda row: _event_timestamp(row)
            or datetime.min.astimezone()
        )

        current: list[dict[str, Any]] = []

        for row in rows:
            ts = _event_timestamp(row)

            if ts is None:
                continue

            if current:
                previous_ts = _event_timestamp(current[-1])

                if (
                    previous_ts is not None
                    and (ts - previous_ts).total_seconds()
                    > timeout_seconds
                ):
                    sessions.append(
                        _build_session(
                            conversation_id,
                            current,
                            timeout_minutes,
                        )
                    )
                    current = []

            current.append(row)

        if current:
            sessions.append(
                _build_session(
                    conversation_id,
                    current,
                    timeout_minutes,
                )
            )

    sessions.sort(
        key=lambda row: row["started_at"]
    )

    return sessions


def _build_session(
    conversation_id: str,
    rows: list[dict[str, Any]],
    timeout_minutes: int,
) -> dict[str, Any]:
    first_ts = _event_timestamp(rows[0])
    last_ts = _event_timestamp(rows[-1])

    if first_ts is None or last_ts is None:
        raise ValueError("Sesion derivada sin timestamps validos.")

    terminal_error = any(
        str(row.get("event") or "").upper()
        == "SESSION_ERROR"
        for row in rows
    )

    now = datetime.now().astimezone()

    age_seconds = max(
        0,
        int((now - last_ts).total_seconds()),
    )

    if terminal_error:
        status = "ERROR"
    elif age_seconds <= timeout_minutes * 60:
        status = "IN_PROGRESS"
    else:
        status = "COMPLETED_BY_INACTIVITY"

    wo = ""
    result = ""
    error_code = ""

    for row in rows:
        value = str(row.get("wo") or "").strip()

        if value:
            wo = value

        value = str(row.get("result") or "").strip()

        if value:
            result = value

        value = str(row.get("error_code") or "").strip()

        if value:
            error_code = value

    duration_seconds = max(
        0,
        int((last_ts - first_ts).total_seconds()),
    )

    return {
        "conversation_id": conversation_id,
        "status": status,
        "started_at": first_ts,
        "ended_at": last_ts,
        "duration_seconds": duration_seconds,
        "event_count": len(rows),
        "wo": wo,
        "result": result,
        "error_code": error_code,
    }


class SmccAnalyticsService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.repo = SmccAnalyticsRepository()

    def health(self) -> dict[str, Any]:
        repo = self.repo.health()

        return {
            "ok": bool(
                repo["data_dir_exists"]
                and repo["events_dir_exists"]
            ),
            "service": "api_smcc_analytics",
            "version": "V1_2",
            "host": self.settings.host,
            "port": self.settings.port,
            "session_timeout_minutes": (
                self.settings.session_timeout_minutes
            ),
            **repo,
        }

    def _resolve_period(
        self,
        date_from: str | None,
        date_to: str | None,
    ) -> tuple[date, date]:
        today = datetime.now().astimezone().date()

        end = _parse_date(date_to) or today
        start = _parse_date(date_from)

        if start is None:
            start = end - timedelta(
                days=self.settings.default_days - 1
            )

        if start > end:
            raise ValueError(
                "date_from no puede ser posterior a date_to."
            )

        days = (end - start).days + 1

        if days > self.settings.max_days:
            raise ValueError(
                f"El rango maximo permitido es "
                f"{self.settings.max_days} dias."
            )

        return start, end

    def dashboard(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        start, end = self._resolve_period(
            date_from,
            date_to,
        )

        events = self.repo.list_events_range(
            start,
            end,
        )

        sessions = _derive_sessions(
            events,
            self.settings.session_timeout_minutes,
        )

        completed = [
            row
            for row in sessions
            if row["status"]
            == "COMPLETED_BY_INACTIVITY"
        ]

        errors = [
            row
            for row in sessions
            if row["status"] == "ERROR"
        ]

        in_progress = [
            row
            for row in sessions
            if row["status"] == "IN_PROGRESS"
        ]

        terminal = completed + errors

        effectiveness = (
            round(
                len(completed)
                / len(terminal)
                * 100,
                2,
            )
            if terminal
            else 0.0
        )

        durations = [
            int(row["duration_seconds"])
            for row in terminal
        ]

        avg_duration = (
            round(
                sum(durations)
                / len(durations),
                2,
            )
            if durations
            else 0.0
        )

        by_day: dict[str, dict[str, Any]] = {}

        current = start

        while current <= end:
            key = current.isoformat()

            by_day[key] = {
                "date": key,
                "sessions": 0,
                "completed": 0,
                "errors": 0,
                "in_progress": 0,
            }

            current += timedelta(days=1)

        by_hour = {
            hour: {
                "hour": hour,
                "sessions": 0,
                "completed": 0,
                "errors": 0,
                "in_progress": 0,
            }
            for hour in range(24)
        }

        result_counter: Counter[str] = Counter()
        error_counter: Counter[str] = Counter()

        for row in sessions:
            started_at = row["started_at"]

            day_key = started_at.date().isoformat()

            if day_key in by_day:
                by_day[day_key]["sessions"] += 1

                if row["status"] == "ERROR":
                    by_day[day_key]["errors"] += 1
                elif row["status"] == "IN_PROGRESS":
                    by_day[day_key]["in_progress"] += 1
                else:
                    by_day[day_key]["completed"] += 1

            hour = started_at.hour
            by_hour[hour]["sessions"] += 1

            if row["status"] == "ERROR":
                by_hour[hour]["errors"] += 1
            elif row["status"] == "IN_PROGRESS":
                by_hour[hour]["in_progress"] += 1
            else:
                by_hour[hour]["completed"] += 1

            result = str(
                row.get("result") or ""
            ).strip()

            if result:
                result_counter[result] += 1

            error_code = str(
                row.get("error_code") or ""
            ).strip()

            if error_code:
                error_counter[error_code] += 1

        unique_conversations = len(
            {
                str(row["conversation_id"])
                for row in sessions
            }
        )

        return {
            "ok": True,
            "service": "api_smcc_analytics",
            "version": "V1_2",
            "segmentation": {
                "mode": "INACTIVITY",
                "timeout_minutes": (
                    self.settings.session_timeout_minutes
                ),
            },
            "period": {
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "days": (end - start).days + 1,
            },
            "source": {
                "events": len(events),
                "unique_conversations": unique_conversations,
                "derived_sessions": len(sessions),
            },
            "totals": {
                "sessions": len(sessions),
                "completed": len(completed),
                "errors": len(errors),
                "in_progress": len(in_progress),
                "success_rate": effectiveness,
            },
            "duration": {
                "avg_seconds": avg_duration,
                "median_seconds": (
                    _percentile(durations, 0.50)
                    if durations
                    else 0
                ),
                "p90_seconds": (
                    _percentile(durations, 0.90)
                    if durations
                    else 0
                ),
                "p95_seconds": (
                    _percentile(durations, 0.95)
                    if durations
                    else 0
                ),
                "max_seconds": (
                    max(durations)
                    if durations
                    else 0
                ),
            },
            "by_day": list(by_day.values()),
            "by_hour": [
                by_hour[hour]
                for hour in range(24)
            ],
            "results": [
                {
                    "result": key,
                    "sessions": value,
                }
                for key, value
                in result_counter.most_common(20)
            ],
            "error_codes": [
                {
                    "error_code": key,
                    "sessions": value,
                }
                for key, value
                in error_counter.most_common(20)
            ],
        }