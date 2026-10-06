from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_DIR / ".env", override=False)


@dataclass(frozen=True)
class OracleSettings:
    user: str
    password: str
    host: str
    port: int
    service: str
    schema: str = "ARADMIN"
    pool_min: int = 1
    pool_max: int = 4
    pool_increment: int = 1
    call_timeout_ms: int = 30000

    @classmethod
    def from_env(cls) -> "OracleSettings":
        required = {
            "ORACLE_USER": os.getenv("ORACLE_USER", "").strip(),
            "ORACLE_PASSWORD": os.getenv("ORACLE_PASSWORD", ""),
            "ORACLE_HOST": os.getenv("ORACLE_HOST", "").strip(),
            "ORACLE_SERVICE": os.getenv("ORACLE_SERVICE", "").strip(),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise RuntimeError("Configuración Oracle incompleta: " + ", ".join(missing))
        schema = os.getenv("ORACLE_SCHEMA", "ARADMIN").strip().upper() or "ARADMIN"
        if not schema.replace("_", "").isalnum():
            raise RuntimeError("ORACLE_SCHEMA inválido")
        return cls(
            user=required["ORACLE_USER"],
            password=required["ORACLE_PASSWORD"],
            host=required["ORACLE_HOST"],
            port=int(os.getenv("ORACLE_PORT", "1521")),
            service=required["ORACLE_SERVICE"],
            schema=schema,
            pool_min=max(1, int(os.getenv("ORACLE_POOL_MIN", "1"))),
            pool_max=max(1, int(os.getenv("ORACLE_POOL_MAX", "4"))),
            pool_increment=max(1, int(os.getenv("ORACLE_POOL_INCREMENT", "1"))),
            call_timeout_ms=max(1000, int(os.getenv("ORACLE_CALL_TIMEOUT_MS", "30000"))),
        )
