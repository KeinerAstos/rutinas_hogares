from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(
    name: str,
    default: int,
    minimum: int = 1,
    maximum: int = 32,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default

    return max(minimum, min(value, maximum))


class DecoJobManager:
    """
    Colas independientes para proteger los recursos pesados.

    - PathTrak utiliza su propio conjunto limitado de workers.
    - Máximo utiliza una cola independiente.
    - Las consultas directas tienen su propio límite.
    """

    FINAL_STATUSES = {
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()

        self._jobs: dict[str, dict[str, Any]] = {}
        self._pending_by_resource: dict[str, list[str]] = {
            "pathtrak": [],
            "maximo": [],
            "direct": [],
        }

        self._executors = {
            "pathtrak": ThreadPoolExecutor(
                max_workers=_env_int(
                    "DECO_PATHTRAK_WORKERS",
                    2,
                    maximum=8,
                ),
                thread_name_prefix="deco-pathtrak",
            ),
            "maximo": ThreadPoolExecutor(
                max_workers=_env_int(
                    "DECO_MAXIMO_WORKERS",
                    1,
                    maximum=4,
                ),
                thread_name_prefix="deco-maximo",
            ),
            "direct": ThreadPoolExecutor(
                max_workers=_env_int(
                    "DECO_DIRECT_WORKERS",
                    4,
                    maximum=16,
                ),
                thread_name_prefix="deco-direct",
            ),
        }

        self._max_jobs = _env_int(
            "DECO_MAX_STORED_JOBS",
            500,
            maximum=5000,
        )

    def submit(
        self,
        *,
        resource: str,
        operation: Callable[[], dict[str, Any]],
        message: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        resource = str(resource or "direct").lower().strip()

        if resource not in self._executors:
            resource = "direct"

        job_id = f"deco_{uuid4().hex}"

        job = {
            "id": job_id,
            "resource": resource,
            "status": "QUEUED",
            "message": str(message or ""),
            "conversation_id": conversation_id,
            "user_id": user_id,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "queue_position": None,
            "result": None,
            "error": None,
        }

        with self._lock:
            self._cleanup_locked()

            self._jobs[job_id] = job
            self._pending_by_resource[resource].append(job_id)
            self._refresh_positions_locked(resource)

        self._executors[resource].submit(
            self._run,
            job_id,
            operation,
        )

        return self.get(job_id) or deepcopy(job)

    def get(
        self,
        job_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def list_recent(
        self,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))

        with self._lock:
            jobs = list(self._jobs.values())

            if conversation_id:
                jobs = [
                    job
                    for job in jobs
                    if job.get("conversation_id") == conversation_id
                ]

            jobs.sort(
                key=lambda item: item.get("created_at") or "",
                reverse=True,
            )

            return deepcopy(jobs[:safe_limit])

    def stats(self) -> dict[str, Any]:
        with self._lock:
            statuses: dict[str, int] = {}
            resources: dict[str, dict[str, int]] = {}

            for job in self._jobs.values():
                status = str(job.get("status") or "UNKNOWN")
                resource = str(job.get("resource") or "unknown")

                statuses[status] = statuses.get(status, 0) + 1

                resources.setdefault(resource, {})
                resources[resource][status] = (
                    resources[resource].get(status, 0) + 1
                )

            return {
                "ok": True,
                "total_jobs": len(self._jobs),
                "statuses": statuses,
                "resources": resources,
                "workers": {
                    "pathtrak": _env_int(
                        "DECO_PATHTRAK_WORKERS",
                        2,
                        maximum=8,
                    ),
                    "maximo": _env_int(
                        "DECO_MAXIMO_WORKERS",
                        1,
                        maximum=4,
                    ),
                    "direct": _env_int(
                        "DECO_DIRECT_WORKERS",
                        4,
                        maximum=16,
                    ),
                },
            }

    def _run(
        self,
        job_id: str,
        operation: Callable[[], dict[str, Any]],
    ) -> None:
        started_perf = time.perf_counter()

        with self._lock:
            job = self._jobs.get(job_id)

            if not job:
                return

            resource = job["resource"]

            self._remove_pending_locked(
                resource,
                job_id,
            )

            job["status"] = "RUNNING"
            job["started_at"] = _now()
            job["queue_position"] = None

            self._refresh_positions_locked(resource)

        try:
            result = operation()

            if not isinstance(result, dict):
                result = {
                    "ok": True,
                    "tipo_respuesta": "resultado",
                    "respuesta": str(result),
                }

            with self._lock:
                job = self._jobs.get(job_id)

                if not job:
                    return

                resultado_ok = bool(
                    result.get("ok", True)
                )

                if resultado_ok:
                    job["status"] = "COMPLETED"
                    job["error"] = None
                else:
                    job["status"] = "FAILED"
                    job["error"] = str(
                        result.get("error")
                        or result.get("respuesta")
                        or "La operación terminó con error."
                    )

                job["result"] = result
                job["finished_at"] = _now()
                job["duration_seconds"] = round(
                    time.perf_counter() - started_perf,
                    2,
                )

        except Exception as exc:
            with self._lock:
                job = self._jobs.get(job_id)

                if not job:
                    return

                job["status"] = "FAILED"
                job["result"] = None
                job["error"] = str(exc)
                job["finished_at"] = _now()
                job["duration_seconds"] = round(
                    time.perf_counter() - started_perf,
                    2,
                )

    def _remove_pending_locked(
        self,
        resource: str,
        job_id: str,
    ) -> None:
        pending = self._pending_by_resource.get(resource)

        if pending is None:
            return

        try:
            pending.remove(job_id)
        except ValueError:
            pass

    def _refresh_positions_locked(
        self,
        resource: str,
    ) -> None:
        pending = self._pending_by_resource.get(resource, [])

        valid_pending: list[str] = []

        for job_id in pending:
            job = self._jobs.get(job_id)

            if not job:
                continue

            if job.get("status") != "QUEUED":
                continue

            valid_pending.append(job_id)

        self._pending_by_resource[resource] = valid_pending

        for position, job_id in enumerate(
            valid_pending,
            start=1,
        ):
            job = self._jobs.get(job_id)

            if job:
                job["queue_position"] = position

    def _cleanup_locked(self) -> None:
        if len(self._jobs) < self._max_jobs:
            return

        completed = [
            job
            for job in self._jobs.values()
            if job.get("status") in self.FINAL_STATUSES
        ]

        completed.sort(
            key=lambda item: item.get("finished_at") or "",
        )

        remove_count = max(
            1,
            len(self._jobs) - self._max_jobs + 1,
        )

        for job in completed[:remove_count]:
            self._jobs.pop(job["id"], None)


deco_job_manager = DecoJobManager()
