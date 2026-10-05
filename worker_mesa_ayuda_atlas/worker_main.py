from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

config = json.loads(
    (ROOT / "worker_config.json").read_text(
        encoding="utf-8-sig"
    )
)

IPC_ROOT = Path(config["ipc_root"]).resolve()
PENDING = IPC_ROOT / "pending"
PROCESSING = IPC_ROOT / "processing"
RESULTS = IPC_ROOT / "results"
FAILED = IPC_ROOT / "failed"
HEARTBEAT = IPC_ROOT / "heartbeat.json"
PID_FILE = IPC_ROOT / "worker.pid"
LOG_FILE = IPC_ROOT / "worker.log"

for directory in (
    PENDING,
    PROCESSING,
    RESULTS,
    FAILED,
):
    directory.mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

from app.services.mesa_ayuda.conversation_service import (
    conversar,
    health as conversation_health,
)
from app.services.mesa_ayuda.smcc_bridge_service import (
    procesar_mensaje_smcc,
    health_smcc_bridge,
)
from app.services.smcc_tracking_service import (
    SmccTrackingService,
)
from app.services.smcc_conversation_log_service import (
    SmccConversationLogService,
)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def atomic_write_json(path: Path, payload: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")

    temp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    os.replace(temp, path)


def log(message: str) -> None:
    line = f"[{utc_now()}] {message}\n"

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as fh:
        fh.write(line)


def write_heartbeat() -> None:
    atomic_write_json(
        HEARTBEAT,
        {
            "ok": True,
            "service": "atlas_mesa_worker_local",
            "pid": os.getpid(),
            "timestamp": utc_now(),
            "transport": "filesystem_ipc",
            "port": None,
        },
    )


def handle(req_type: str, payload: dict[str, Any]) -> Any:
    tracking = SmccTrackingService()

    if req_type == "health":
        return {
            "ok": True,
            "worker": {
                "service": "atlas_mesa_worker_local",
                "pid": os.getpid(),
                "transport": "filesystem_ipc",
                "port": None,
            },
            "mesa": conversation_health(),
            "smcc_bridge": health_smcc_bridge(),
            "tracking": tracking.health(),
        }

    if req_type == "mesa_chat":
        return conversar(**payload)

    if req_type == "smcc_bridge":
        return procesar_mensaje_smcc(**payload)

    if req_type == "tracking_health":
        return tracking.health()

    if req_type == "tracking_active":
        return {
            "ok": True,
            "items": tracking.active(),
        }

    if req_type == "tracking_sessions":
        return {
            "ok": True,
            "items": tracking.history(
                payload.get("date_from"),
                payload.get("date_to"),
                int(payload.get("limit") or 200),
            ),
        }

    if req_type == "tracking_detail":
        cid = str(
            payload.get("conversation_id") or ""
        ).strip()

        return {
            "ok": True,
            "session": tracking.detail(cid),
        }

    if req_type == "tracking_summary":
        return {
            "ok": True,
            **tracking.summary(),
        }

    if req_type == "tracking_messages":
        cid = str(
            payload.get("conversation_id") or ""
        ).strip()

        return {
            "ok": True,
            "messages": (
                SmccConversationLogService()
                .messages(cid)
            ),
        }

    raise ValueError(
        f"Tipo IPC no soportado: {req_type}"
    )


def process_file(path: Path) -> None:
    request_id = path.stem
    processing_path = PROCESSING / path.name

    try:
        os.replace(path, processing_path)

        request = json.loads(
            processing_path.read_text(
                encoding="utf-8-sig"
            )
        )

        req_type = str(
            request.get("type") or ""
        ).strip()

        payload = request.get("payload")

        if not isinstance(payload, dict):
            payload = {}

        result = handle(req_type, payload)

        envelope = {
            "ok": True,
            "id": request_id,
            "type": req_type,
            "completed_at": utc_now(),
            "result": result,
        }

        atomic_write_json(
            RESULTS / f"{request_id}.json",
            envelope,
        )

        processing_path.unlink(missing_ok=True)

    except Exception as exc:
        failure = {
            "ok": False,
            "id": request_id,
            "completed_at": utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }

        atomic_write_json(
            FAILED / f"{request_id}.json",
            failure,
        )

        try:
            processing_path.unlink(missing_ok=True)
        except Exception:
            pass

        log(
            f"ERROR request={request_id} "
            f"{type(exc).__name__}: {exc}"
        )


def main() -> None:
    PID_FILE.write_text(
        str(os.getpid()),
        encoding="ascii",
    )

    log(
        f"Worker iniciado pid={os.getpid()}"
    )

    last_heartbeat = 0.0

    try:
        while True:
            now = time.time()

            if now - last_heartbeat >= 2:
                write_heartbeat()
                last_heartbeat = now

            files = sorted(
                PENDING.glob("*.json"),
                key=lambda p: (
                    p.stat().st_mtime,
                    p.name,
                ),
            )

            if not files:
                time.sleep(0.20)
                continue

            process_file(files[0])

    finally:
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()