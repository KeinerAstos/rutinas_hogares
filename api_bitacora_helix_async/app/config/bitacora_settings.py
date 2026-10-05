from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def _load_env(path: Path) -> None:
    if not path.exists():
        return

    for raw in path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    ).splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split("=", 1)

        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


def _path_env(name: str, default: Path) -> Path:
    value = str(os.getenv(name) or "").strip()

    if not value:
        return default

    path = Path(value)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def _bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()

    if not raw:
        return default

    return raw in {
        "1",
        "true",
        "yes",
        "si",
        "sí",
        "on",
    }


_load_env(ENV_FILE)


BITACORA_RUNTIME_DIR = _path_env(
    "BITACORA_HELIX_RUNTIME_DIR",
    PROJECT_ROOT / "runtime",
)

BITACORA_LOG_DIR = _path_env(
    "BITACORA_HELIX_LOG_DIR",
    PROJECT_ROOT / "logs",
)

BITACORA_ENGINE_DATA_DIR = _path_env(
    "BITACORA_HELIX_ENGINE_DATA_DIR",
    BITACORA_RUNTIME_DIR / "engine_data",
)

SMARTIT_LOG_DIR = BITACORA_LOG_DIR / "smartit"

BITACORA_APPLY_ENABLED = _bool_env(
    "BITACORA_HELIX_APPLY_ENABLED",
    False,
)

_raw_apply_script = str(
    os.getenv("BITACORA_HELIX_APPLY_SCRIPT") or ""
).strip()

BITACORA_APPLY_SCRIPT = (
    Path(_raw_apply_script)
    if _raw_apply_script
    else None
)

BITACORA_PHP_EXE = Path(
    str(
        os.getenv("BITACORA_HELIX_PHP_EXE")
        or r"C:\xampp\php\php.exe"
    ).strip()
)

BITACORA_HOST = str(
    os.getenv("BITACORA_HELIX_HOST")
    or "127.0.0.1"
).strip()

BITACORA_PORT = int(
    str(
        os.getenv("BITACORA_HELIX_PORT")
        or "8025"
    ).strip()
)


def ensure_runtime_dirs() -> None:
    for directory in (
        BITACORA_RUNTIME_DIR,
        BITACORA_RUNTIME_DIR / "queue",
        BITACORA_RUNTIME_DIR / "results",
        BITACORA_RUNTIME_DIR / "done",
        BITACORA_RUNTIME_DIR / "failed",
        BITACORA_ENGINE_DATA_DIR,
        BITACORA_LOG_DIR,
        SMARTIT_LOG_DIR,
    ):
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )