from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SmccTrackingSettings:
    enabled: bool
    data_dir: Path
    active_timeout_seconds: int
    retention_days: int


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(minimum, value)


def get_smcc_tracking_settings() -> SmccTrackingSettings:
    data_dir = Path(
        os.getenv(
            "SMCC_TRACKING_DATA_DIR",
            r"C:\xampp\htdocs\rutinas_hogares\api_smcc_analytics\data",
        )
    )
    return SmccTrackingSettings(
        enabled=_env_bool("SMCC_TRACKING_ENABLED", True),
        data_dir=data_dir,
        active_timeout_seconds=_env_int(
            "SMCC_TRACKING_ACTIVE_TIMEOUT_SECONDS", 300
        ),
        retention_days=_env_int("SMCC_TRACKING_RETENTION_DAYS", 90),
    )