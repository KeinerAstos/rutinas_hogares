from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OracleSettings:
    user: str
    password: str
    host: str = "100.72.128.86"
    port: int = 2100
    service: str = "SRV_REPHELIXDBPROD.bogexaprpri1.icesprodvcn1.oraclevcn.com"
    schema: str = "ARADMIN"
    pool_min: int = 1
    pool_max: int = 1
    pool_increment: int = 1
    call_timeout_ms: int = 60000

    @classmethod
    def from_env(cls) -> "OracleSettings":
        return cls(
            user=os.getenv("ORACLE_USER", "").strip(),
            password=os.getenv("ORACLE_PASSWORD", ""),
            host=os.getenv("ORACLE_HOST", "100.72.128.86").strip(),
            port=int(os.getenv("ORACLE_PORT", "2100")),
            service=os.getenv(
                "ORACLE_SERVICE",
                "SRV_REPHELIXDBPROD.bogexaprpri1.icesprodvcn1.oraclevcn.com",
            ).strip(),
            schema=os.getenv("ORACLE_SCHEMA", "ARADMIN").strip() or "ARADMIN",
            pool_min=max(1, int(os.getenv("ORACLE_POOL_MIN", "1"))),
            pool_max=max(1, int(os.getenv("ORACLE_POOL_MAX", "1"))),
            pool_increment=max(1, int(os.getenv("ORACLE_POOL_INCREMENT", "1"))),
            call_timeout_ms=max(1, int(os.getenv("ORACLE_CALL_TIMEOUT_MS", "60000"))),
        )

    @property
    def configured(self) -> bool:
        return bool(self.user and self.password and self.host and self.service)
