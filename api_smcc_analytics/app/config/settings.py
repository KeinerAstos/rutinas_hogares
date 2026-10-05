from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT_DIR / ".env"


def _load_env_file() -> None:
    if not ENV_FILE.is_file():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw.strip())
    except ValueError:
        return default

    return max(minimum, value)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    tracking_data_dir: Path
    active_timeout_seconds: int
    default_days: int
    max_days: int
    session_timeout_minutes: int


def get_settings() -> Settings:
    _load_env_file()

    return Settings(
        host=os.getenv("SMCC_ANALYTICS_HOST", "127.0.0.1").strip(),
        port=_env_int("SMCC_ANALYTICS_PORT", 8028),
        tracking_data_dir=Path(
            os.getenv(
                "SMCC_TRACKING_DATA_DIR",
                r"C:\xampp\htdocs\rutinas_hogares\api_smcc_analytics\data",
            )
        ),
        active_timeout_seconds=_env_int(
            "SMCC_TRACKING_ACTIVE_TIMEOUT_SECONDS",
            300,
        ),
        default_days=_env_int(
            "SMCC_ANALYTICS_DEFAULT_DAYS",
            1,
        ),
        max_days=_env_int(
            "SMCC_ANALYTICS_MAX_DAYS",
            90,
        ),
        session_timeout_minutes=_env_int(
            "SMCC_ANALYTICS_SESSION_TIMEOUT_MINUTES",
            15,
        ),
    )