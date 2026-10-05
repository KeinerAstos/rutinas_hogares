from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.bitacora_settings import (
    BITACORA_APPLY_ENABLED,
    BITACORA_APPLY_SCRIPT,
    BITACORA_LOG_DIR,
    BITACORA_PHP_EXE,
    BITACORA_RUNTIME_DIR,
    ensure_runtime_dirs,
)

CLI = PROJECT_ROOT / "app" / "services" / "helix" / "incident_config_items_cli.py"
PYTHON = Path(sys.executable)
PHP = BITACORA_PHP_EXE
APPLY = BITACORA_APPLY_SCRIPT

ROOT = BITACORA_RUNTIME_DIR
QUEUE = ROOT / "queue"
DONE = ROOT / "done"
FAILED = ROOT / "failed"
RESULTS = ROOT / "results"
PID_FILE = ROOT / "worker.pid"

LOG_DIR = BITACORA_LOG_DIR
LOG_FILE = LOG_DIR / "helix_bitacora_queue_worker.log"

ensure_runtime_dirs()


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_HIDE = 0

def log(message: str) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | PID={os.getpid()} | {message}\n")

def process_image(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(handle)

def hide_playwright_windows(stop_event: threading.Event) -> None:
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def enum_proc(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            image = process_image(int(pid.value)).lower()
            if (
                "ms-playwright" in image
                and ("chromium-" in image or "chrome-win64" in image)
                and "headless_shell" not in image
            ):
                user32.ShowWindow(hwnd, SW_HIDE)
        except Exception:
            pass
        return True

    while not stop_event.is_set():
        try:
            user32.EnumWindows(enum_proc, 0)
        except Exception:
            pass
        stop_event.wait(0.15)

def write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def process_job(job_path: Path) -> None:
    started = time.time()
    job = json.loads(job_path.read_text(encoding="utf-8-sig"))
    incident = str(job.get("incident", "")).strip().upper()
    incident_id = int(job.get("incident_id") or 0)
    apply_result = bool(job.get("apply", True)) and BITACORA_APPLY_ENABLED
    job_id = str(job.get("job_id") or job_path.stem)

    if not incident.startswith("INC"):
        raise RuntimeError("INC_INVALIDO")

    log(f"START job={job_id} inc={incident} apply={apply_result}")

    # ATLAS_HELIX_RETRY_EXIT2_V1
    # SmartIT puede cargar ocasionalmente el INC sin CI/elementos y el CLI
    # finaliza con código 2. Se permite UN único reintento después de 3 s.
    # Cualquier otro código de salida falla inmediatamente.
    proc = None
    stdout = ""
    stderr = ""

    for helix_attempt in (1, 2):
        log(
            f"HELIX_ATTEMPT job={job_id} "
            f"inc={incident} attempt={helix_attempt}/2"
        )

        stop_hider = threading.Event()
        hider = threading.Thread(
            target=hide_playwright_windows,
            args=(stop_hider,),
            daemon=True,
        )
        hider.start()

        try:
            proc = subprocess.run(
                [str(PYTHON), str(CLI), incident],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                creationflags=getattr(
                    subprocess,
                    "CREATE_NO_WINDOW",
                    0,
                ),
            )
        finally:
            stop_hider.set()
            hider.join(timeout=2)

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode == 0:
            if helix_attempt > 1:
                log(
                    f"HELIX_RECOVERED job={job_id} "
                    f"inc={incident} attempt={helix_attempt}/2"
                )
            break

        if proc.returncode == 2 and helix_attempt == 1:
            log(
                f"HELIX_RETRY job={job_id} "
                f"inc={incident} exit=2 wait=3s"
            )
            time.sleep(3)
            continue

        raise RuntimeError(
            f"HELIX_EXIT={proc.returncode} "
            f"ATTEMPT={helix_attempt}/2 "
            f"STDERR={stderr[-1500:]} "
            f"STDOUT={stdout[-1500:]}"
        )

    if proc is None:
        raise RuntimeError("HELIX_NO_PROCESS_RESULT")

    data = json.loads(stdout)
    if not data.get("ok"):
        raise RuntimeError(f"HELIX_NOT_OK: {data}")

    elements = data.get("elementos") or []
    if not elements:
        raise RuntimeError("HELIX_SIN_ELEMENTOS")

    payload = {
        "job_id": job_id,
        "incident_id": incident_id,
        "incident": incident,
        "apply": apply_result,
        "helix": data,
        "elapsed_seconds": round(time.time() - started, 2),
    }

    result_file = RESULTS / f"{job_id}.json"
    write_json_atomic(result_file, payload)

    if apply_result:
        if APPLY is None:
            raise RuntimeError("BITACORA_APPLY_SCRIPT_NO_CONFIGURADO")

        if incident_id <= 0:
            raise RuntimeError("INCIDENT_ID_INVALIDO_PARA_APPLY")

        apply_proc = subprocess.run(
            [str(PHP), str(APPLY), str(incident_id), incident, str(result_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if apply_proc.returncode != 0:
            raise RuntimeError(
                f"APPLY_FAIL EXIT={apply_proc.returncode} "
                f"STDOUT={(apply_proc.stdout or '')[-1200:]} "
                f"STDERR={(apply_proc.stderr or '')[-1200:]}"
            )

    done = dict(payload)
    done["status"] = "ok"
    write_json_atomic(DONE / f"{job_id}.json", done)

    try:
        job_path.unlink()
    except FileNotFoundError:
        pass

    log(f"OK job={job_id} inc={incident} elements={len(elements)} elapsed={done['elapsed_seconds']}")

def fail_job(job_path: Path, error: Exception) -> None:
    try:
        job = json.loads(job_path.read_text(encoding="utf-8-sig"))
    except Exception:
        job = {"job_id": job_path.stem}

    job_id = str(job.get("job_id") or job_path.stem)
    payload = {
        "status": "error",
        "job": job,
        "error": f"{type(error).__name__}: {error}",
        "traceback": traceback.format_exc(),
    }
    write_json_atomic(FAILED / f"{job_id}.json", payload)

    try:
        job_path.unlink()
    except FileNotFoundError:
        pass

    log(f"ERROR job={job_id} {type(error).__name__}: {error}")

def main() -> int:
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")
    log("WORKER_START")
    try:
        while True:
            jobs = sorted(QUEUE.glob("*.json"), key=lambda p: p.stat().st_mtime)
            if not jobs:
                time.sleep(0.6)
                continue
            for job_path in jobs:
                try:
                    process_job(job_path)
                except subprocess.TimeoutExpired as exc:
                    fail_job(job_path, RuntimeError(f"HELIX_TIMEOUT_60S: {exc}"))
                except Exception as exc:
                    fail_job(job_path, exc)
    finally:
        try:
            if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except Exception:
            pass
        log("WORKER_STOP")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
