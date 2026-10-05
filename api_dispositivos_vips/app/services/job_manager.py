from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from app.core.settings import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_parallel_jobs,
            thread_name_prefix="atlas-job",
        )
        self._lock = Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_by_name: dict[str, str] = {}

    def submit_unique(self, name: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            active_id = self._active_by_name.get(name)
            if active_id and self._jobs[active_id]["status"] in {"QUEUED", "RUNNING"}:
                return deepcopy(self._jobs[active_id])

            job_id = f"job_{uuid4().hex}"
            job = {
                "id": job_id,
                "name": name,
                "status": "QUEUED",
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._active_by_name[name] = job_id
            self._executor.submit(self._run, job_id, operation)
            return deepcopy(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def _run(self, job_id: str, operation: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            self._jobs[job_id]["status"] = "RUNNING"
            self._jobs[job_id]["started_at"] = _now()

        try:
            result = operation()
        except Exception as exc:
            with self._lock:
                self._jobs[job_id]["status"] = "FAILED"
                self._jobs[job_id]["error"] = str(exc)
                self._jobs[job_id]["finished_at"] = _now()
                self._active_by_name.pop(self._jobs[job_id]["name"], None)
            return

        with self._lock:
            self._jobs[job_id]["status"] = "COMPLETED"
            self._jobs[job_id]["result"] = result
            self._jobs[job_id]["finished_at"] = _now()
            self._active_by_name.pop(self._jobs[job_id]["name"], None)


job_manager = JobManager()
