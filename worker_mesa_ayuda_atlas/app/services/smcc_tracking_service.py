from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config.smcc_tracking_settings import get_smcc_tracking_settings
from app.repositories.smcc_tracking_repository import SmccTrackingRepository
from app.schemas.smcc_tracking import SmccTrackingEventIn


_TERMINAL_EVENTS = {"SESSION_FINISHED", "SESSION_ERROR"}


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class SmccTrackingService:
    def __init__(self) -> None:
        self.settings = get_smcc_tracking_settings()
        self.repo = SmccTrackingRepository()

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "smcc_tracking",
            "enabled": self.settings.enabled,
            "data_dir": str(self.settings.data_dir),
            "active_timeout_seconds": self.settings.active_timeout_seconds,
            "retention_days": self.settings.retention_days,
        }

    def register_event(self, incoming: SmccTrackingEventIn) -> dict[str, Any]:
        if not self.settings.enabled:
            return {"ok": False, "code": "SMCC_TRACKING_DISABLED"}

        now = incoming.timestamp or _now()
        ts = _iso(now)
        cid = incoming.conversation_id.strip()

        current = self.repo.get_active(cid) or {
            "conversation_id": cid,
            "source": "SMCC",
            "status": "ACTIVE",
            "started_at": ts,
            "last_activity_at": ts,
            "ended_at": None,
            "duration_seconds": None,
            "wo": None,
            "operation": None,
            "result": None,
            "error_code": None,
            "extension_version": None,
            "last_event": None,

            # SMCC_HELIX_AUDIT_FIELDS_V1_1
            "helix_auth_mode": None,
            "helix_user": None,
            "note_status": None,
            "note_incidente": None,
            "note_production_write": None,
            "note_codigo": None,
            "note_updated_at": None,
        }

        current["last_activity_at"] = ts
        current["last_event"] = incoming.event

        if incoming.wo:
            current["wo"] = incoming.wo.strip()
        if incoming.operation:
            current["operation"] = incoming.operation.strip()
        if incoming.result:
            current["result"] = incoming.result.strip()
        if incoming.error_code:
            current["error_code"] = incoming.error_code.strip()
        if incoming.extension_version:
            current["extension_version"] = incoming.extension_version.strip()

        if incoming.status:
            current["status"] = incoming.status.strip().upper()

        # SMCC_HELIX_AUDIT_METADATA_V1_1

        if incoming.event == "HELIX_NOTE_RESULT":

            metadata = (

                incoming.metadata

                if isinstance(incoming.metadata, dict)

                else {}

            )


            helix_auth_mode = str(

                metadata.get("helix_auth_mode") or ""

            ).strip().upper()


            helix_user = str(

                metadata.get("helix_user") or ""

            ).strip()


            note_status = str(

                metadata.get("note_status") or ""

            ).strip().upper()


            note_incidente = str(

                metadata.get("incidente") or ""

            ).strip().upper()


            note_codigo = str(

                metadata.get("codigo") or ""

            ).strip()


            current["helix_auth_mode"] = (

                helix_auth_mode or None

            )


            current["helix_user"] = (

                helix_user[:200]

                if helix_user

                else None

            )


            current["note_status"] = (

                note_status or None

            )


            current["note_incidente"] = (

                note_incidente[:40]

                if note_incidente

                else None

            )


            current["note_production_write"] = (

                metadata.get("production_write") is True

            )


            current["note_codigo"] = (

                note_codigo[:160]

                if note_codigo

                else None

            )


            current["note_updated_at"] = ts


        if incoming.event == "SESSION_ERROR":
            current["status"] = "ERROR"
        elif incoming.event == "SESSION_FINISHED":
            current["status"] = "FINISHED"
        elif incoming.event == "SESSION_STARTED":
            current["status"] = "ACTIVE"

        event_record = incoming.model_dump(mode="json")
        event_record["timestamp"] = ts
        event_record["source"] = "SMCC"
        self.repo.append_event(event_record)

        if incoming.event in _TERMINAL_EVENTS:
            current["ended_at"] = ts
            start = _parse_iso(current.get("started_at"))
            if start is not None:
                current["duration_seconds"] = max(
                    0,
                    int((now - start).total_seconds()),
                )
            self.repo.close_session(current)
        else:
            self.repo.upsert_active(current)

        return {"ok": True, "session": current}

    def active(self) -> list[dict[str, Any]]:
        now = _now()
        timeout = self.settings.active_timeout_seconds
        result: list[dict[str, Any]] = []

        for item in self.repo.list_active():
            last = _parse_iso(item.get("last_activity_at"))
            stale = False
            if last is not None:
                stale = (now - last).total_seconds() > timeout

            copy = dict(item)
            copy["stale"] = stale
            if stale and copy.get("status") == "ACTIVE":
                copy["display_status"] = "STALE"
            else:
                copy["display_status"] = copy.get("status")
            result.append(copy)

        result.sort(
            key=lambda x: str(x.get("last_activity_at") or ""),
            reverse=True,
        )
        return result

    def history(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self.repo.list_history(date_from, date_to, limit)

    def detail(self, conversation_id: str) -> dict[str, Any] | None:
        return self.repo.find_session(conversation_id)

    def summary(self) -> dict[str, Any]:
        active = self.active()
        today = _now().date().isoformat()
        history = self.repo.list_history(today, today, 5000)

        finished = sum(1 for x in history if x.get("status") == "FINISHED")
        errors = sum(1 for x in history if x.get("status") == "ERROR")
        total_closed = finished + errors
        success_rate = (
            round((finished / total_closed) * 100, 2)
            if total_closed
            else 0.0
        )

        durations = [
            int(x["duration_seconds"])
            for x in history
            if isinstance(x.get("duration_seconds"), int)
        ]
        avg_duration = (
            round(sum(durations) / len(durations), 2)
            if durations
            else 0.0
        )

        return {
            "date": today,
            "active": sum(1 for x in active if not x.get("stale")),
            "stale": sum(1 for x in active if x.get("stale")),
            "today_total_closed": total_closed,
            "finished": finished,
            "errors": errors,
            "success_rate": success_rate,
            "avg_duration_seconds": avg_duration,
        }