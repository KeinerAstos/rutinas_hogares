from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config.bitacora_settings import (
    BITACORA_APPLY_ENABLED,
    BITACORA_ENGINE_DATA_DIR,
    BITACORA_HOST,
    BITACORA_LOG_DIR,
    BITACORA_PORT,
    BITACORA_RUNTIME_DIR,
    PROJECT_ROOT,
    ensure_runtime_dirs,
)


ensure_runtime_dirs()

QUEUE = BITACORA_RUNTIME_DIR / "queue"
RESULTS = BITACORA_RUNTIME_DIR / "results"
DONE = BITACORA_RUNTIME_DIR / "done"
FAILED = BITACORA_RUNTIME_DIR / "failed"
PID_FILE = BITACORA_RUNTIME_DIR / "worker.pid"

app = FastAPI(
    title="ATLAS Bitacora Helix Async",
    version="1.0.0",
)


class JobRequest(BaseModel):
    incident: str = Field(min_length=9, max_length=30)
    incident_id: int = Field(default=0, ge=0)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")

    tmp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8-sig",
            )
        )
    except Exception:
        return None


def worker_status() -> dict[str, Any]:
    # ATLAS_WORKER_STATUS_WINDOWS_V1
    info = _worker_process_info()

    return {
        "pid": info.get("pid"),
        "alive": bool(
            info.get("alive")
            and info.get("validated")
        ),
    }


def count_json(directory: Path) -> int:
    return sum(
        1
        for _ in directory.glob("*.json")
    )


def find_job_file(job_id: str) -> tuple[str, Path] | None:
    safe_job = Path(job_id).name

    if safe_job != job_id:
        return None

    for status, directory in (
        ("done", DONE),
        ("failed", FAILED),
        ("result", RESULTS),
        ("pending", QUEUE),
    ):
        path = directory / f"{safe_job}.json"

        if path.exists():
            return status, path

    return None


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "atlas_bitacora_helix_async",
        "port": BITACORA_PORT,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    worker = worker_status()

    return {
        "ok": True,
        "service": "atlas_bitacora_helix_async",
        "version": "1.0.0",
        "host": BITACORA_HOST,
        "port": BITACORA_PORT,
        "project_root": str(PROJECT_ROOT),
        "runtime": str(BITACORA_RUNTIME_DIR),
        "log_dir": str(BITACORA_LOG_DIR),
        "engine_data": str(BITACORA_ENGINE_DATA_DIR),
        "apply_enabled": BITACORA_APPLY_ENABLED,
        "worker": worker,
        "queue": {
            "pending": count_json(QUEUE),
            "results": count_json(RESULTS),
            "done": count_json(DONE),
            "failed": count_json(FAILED),
        },
    }


@app.get("/queue/status")
def queue_status() -> dict[str, Any]:
    return {
        "ok": True,
        "runtime": str(BITACORA_RUNTIME_DIR),
        "worker": worker_status(),
        "pending": count_json(QUEUE),
        "results": count_json(RESULTS),
        "done": count_json(DONE),
        "failed": count_json(FAILED),
    }


@app.post("/jobs", status_code=202)
def create_job(req: JobRequest) -> dict[str, Any]:
    incident = req.incident.strip().upper()

    if (
        not incident.startswith("INC")
        or not incident[3:].isdigit()
    ):
        raise HTTPException(
            status_code=400,
            detail="INC_INVALIDO",
        )

    unique = uuid.uuid4().hex[:16]

    if req.incident_id > 0:
        job_id = (
            f"{req.incident_id}_"
            f"{incident}_"
            f"{unique}"
        )
    else:
        job_id = (
            f"test_"
            f"{incident}_"
            f"{unique}"
        )

    payload = {
        "job_id": job_id,
        "incident_id": req.incident_id,
        "incident": incident,

        # Seguridad intencional.
        # La API de pruebas NO permite solicitar apply=True.
        "apply": False,

        "created_at": datetime.now().astimezone().isoformat(),
        "source": "api_bitacora_helix_async_8025",
    }

    path = QUEUE / f"{job_id}.json"

    write_json_atomic(
        path,
        payload,
    )

    return {
        "ok": True,
        "accepted": True,
        "job_id": job_id,
        "status": "pending",
        "apply": False,
        "worker_alive": worker_status()["alive"],
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    found = find_job_file(job_id)

    if found is None:
        raise HTTPException(
            status_code=404,
            detail="JOB_NOT_FOUND",
        )

    status, path = found
    payload = read_json(path)

    return {
        "ok": True,
        "job_id": job_id,
        "status": status,
        "payload": payload,
    }

# ==============================================================================================
# ATLAS_WORKER_CONTROL_V1
# Control administrado del worker Bitacora Helix Async.
# ==============================================================================================

import subprocess as _worker_subprocess
import sys as _worker_sys
import time as _worker_time


WORKER_SCRIPT = (
    PROJECT_ROOT
    / "app"
    / "services"
    / "helix"
    / "bitacora_queue_worker.py"
)

WORKER_STDOUT = (
    BITACORA_LOG_DIR
    / "managed_worker_stdout.log"
)

WORKER_STDERR = (
    BITACORA_LOG_DIR
    / "managed_worker_stderr.log"
)


def _worker_pid_from_file() -> int | None:
    if not PID_FILE.exists():
        return None

    try:
        value = PID_FILE.read_text(
            encoding="ascii",
        ).strip()

        pid = int(value)

        if pid <= 0:
            return None

        return pid

    except Exception:
        return None


def _get_process_commandline(pid: int) -> str:
    try:
        command = (
            "$p = Get-CimInstance Win32_Process "
            f"-Filter 'ProcessId={pid}' "
            "-ErrorAction SilentlyContinue; "
            "if ($p) { $p.CommandLine }"
        )

        result = _worker_subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(
                _worker_subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        return (result.stdout or "").strip()

    except Exception:
        return ""


def _worker_process_info() -> dict[str, Any]:
    pid = _worker_pid_from_file()

    if pid is None:
        return {
            "pid": None,
            "alive": False,
            "validated": False,
            "command_line": None,
        }

    command_line = _get_process_commandline(pid)

    if not command_line:
        return {
            "pid": pid,
            "alive": False,
            "validated": False,
            "command_line": None,
        }

    normalized = command_line.lower()

    validated = (
        "bitacora_queue_worker.py" in normalized
        and
        str(PROJECT_ROOT).lower() in normalized
    )

    return {
        "pid": pid,
        "alive": True,
        "validated": validated,
        "command_line": command_line,
    }


@app.get("/worker/status")
def managed_worker_status() -> dict[str, Any]:
    info = _worker_process_info()

    return {
        "ok": True,
        "worker": info,
        "pending": count_json(QUEUE),
        "apply_enabled": BITACORA_APPLY_ENABLED,
    }


@app.post("/worker/start")
def managed_worker_start() -> dict[str, Any]:
    current = _worker_process_info()

    if current["alive"]:
        return {
            "ok": True,
            "started": False,
            "already_running": True,
            "worker": current,
        }

    # PID file viejo/stale.
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except Exception:
            pass

    BITACORA_LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    stdout_handle = open(
        WORKER_STDOUT,
        "a",
        encoding="utf-8",
    )

    stderr_handle = open(
        WORKER_STDERR,
        "a",
        encoding="utf-8",
    )

    try:
        process = _worker_subprocess.Popen(
            [
                _worker_sys.executable,
                str(WORKER_SCRIPT),
            ],
            cwd=str(PROJECT_ROOT),
            stdout=stdout_handle,
            stderr=stderr_handle,
            stdin=_worker_subprocess.DEVNULL,
            creationflags=getattr(
                _worker_subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    # El python.exe del venv puede actuar como launcher.
    # La autoridad del PID sera worker.pid creado por el worker real.
    info = None

    for _ in range(30):
        _worker_time.sleep(0.2)

        info = _worker_process_info()

        if info["alive"]:
            break

        if process.poll() is not None:
            break

    if not info or not info["alive"]:
        raise HTTPException(
            status_code=500,
            detail="WORKER_NO_INICIO",
        )

    if not info["validated"]:
        raise HTTPException(
            status_code=500,
            detail="WORKER_PID_NO_VALIDADO",
        )

    return {
        "ok": True,
        "started": True,
        "already_running": False,
        "worker": info,
    }


@app.post("/worker/stop")
def managed_worker_stop() -> dict[str, Any]:
    current = _worker_process_info()

    if not current["alive"]:

        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except Exception:
                pass

        return {
            "ok": True,
            "stopped": False,
            "already_stopped": True,
            "worker": {
                "pid": None,
                "alive": False,
                "validated": False,
                "command_line": None,
            },
        }

    if not current["validated"]:
        raise HTTPException(
            status_code=409,
            detail="PID_NO_PERTENECE_AL_WORKER_ATLAS",
        )

    pid = int(current["pid"])

    result = _worker_subprocess.run(
        [
            "taskkill.exe",
            "/PID",
            str(pid),
            "/T",
            "/F",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(
            _worker_subprocess,
            "CREATE_NO_WINDOW",
            0,
        ),
    )

    for _ in range(30):
        _worker_time.sleep(0.2)

        info = _worker_process_info()

        if not info["alive"]:
            break

    info = _worker_process_info()

    if info["alive"]:
        raise HTTPException(
            status_code=500,
            detail="WORKER_NO_SE_DETUVO",
        )

    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except Exception:
            pass

    return {
        "ok": True,
        "stopped": True,
        "already_stopped": False,
        "pid": pid,
        "taskkill_returncode": result.returncode,
        "worker": {
            "pid": None,
            "alive": False,
            "validated": False,
            "command_line": None,
        },
    }

# FIN ATLAS_WORKER_CONTROL_V1
