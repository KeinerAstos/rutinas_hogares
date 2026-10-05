from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SmccAnalyticsSettings:
    base_url: str
    timeout_seconds: int


def _env_int(
    name: str,
    default: int,
    minimum: int = 1,
) -> int:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw.strip())
    except ValueError:
        return default

    return max(minimum, value)


def get_smcc_analytics_settings() -> SmccAnalyticsSettings:
    base_url = str(
        os.getenv(
            "SMCC_ANALYTICS_BASE_URL",
            "",
        )
    ).strip().rstrip("/")

    return SmccAnalyticsSettings(
        base_url=base_url,
        timeout_seconds=_env_int(
            "SMCC_ANALYTICS_HTTP_TIMEOUT_SECONDS",
            15,
        ),
    )