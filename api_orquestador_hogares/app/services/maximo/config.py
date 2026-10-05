from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_DIR / ".env", override=True)


@dataclass(frozen=True)
class Settings:
    maximo_url: str = os.getenv(
        "MAXIMO_URL",
        "",
    ).strip()
    maximo_user: str = os.getenv("MAXIMO_USER", "").strip()
    maximo_password: str = os.getenv("MAXIMO_PASSWORD", "").strip()
    headless: bool = os.getenv("MAXIMO_HEADLESS", "true").lower() in {
        "1", "true", "si", "yes"
    }
    slow_mo: int = int(os.getenv("MAXIMO_SLOWMO", "0") or "0")
    timeout_ms: int = int(os.getenv("MAXIMO_TIMEOUT_MS", "60000") or "60000")
    downloads_dir: Path = Path(
        os.getenv(
            "MAXIMO_DOWNLOAD_DIR",
            str(PROJECT_DIR / "data" / "maximo_evidencias"),
        )
    ).expanduser().resolve()
    debug_dir: Path = Path(
        os.getenv(
            "MAXIMO_DEBUG_DIR",
            str(PROJECT_DIR / "logs" / "maximo_debug"),
        )
    ).expanduser().resolve()


settings = Settings()
settings.downloads_dir.mkdir(parents=True, exist_ok=True)
settings.debug_dir.mkdir(parents=True, exist_ok=True)
