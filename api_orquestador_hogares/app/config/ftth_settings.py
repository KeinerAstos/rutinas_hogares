from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(
    BACKEND_ROOT / ".env",
    override=False,
)


class FtthConfigurationError(RuntimeError):
    """Error de configuración operativa FTTH."""


def _required_env(name: str) -> str:
    value = str(
        os.getenv(name)
        or ""
    ).strip()

    if not value:
        raise FtthConfigurationError(
            f"{name}_NO_CONFIGURADO"
        )

    return value


def _required_port(name: str) -> int:
    value = _required_env(name)

    try:
        port = int(value)
    except ValueError as exc:
        raise FtthConfigurationError(
            f"{name}_INVALIDO"
        ) from exc

    if not 1 <= port <= 65535:
        raise FtthConfigurationError(
            f"{name}_FUERA_DE_RANGO"
        )

    return port


def _positive_int(
    name: str,
) -> int:
    value = _required_env(name)

    try:
        number = int(value)
    except ValueError as exc:
        raise FtthConfigurationError(
            f"{name}_INVALIDO"
        ) from exc

    if number <= 0:
        raise FtthConfigurationError(
            f"{name}_INVALIDO"
        )

    return number


@dataclass(frozen=True)
class FtthJumpSettings:
    host: str
    port: int
    connect_timeout: int


def get_ftth_jump_settings() -> FtthJumpSettings:
    """
    Configuración central del jump server utilizado por FTTH.

    No depende de HFC_REMOTE_HOST.
    No contiene IPs ni puertos quemados en código.
    """

    return FtthJumpSettings(
        host=_required_env(
            "FTTH_JUMP_HOST"
        ),
        port=_required_port(
            "FTTH_JUMP_PORT"
        ),
        connect_timeout=_positive_int(
            "FTTH_JUMP_CONNECT_TIMEOUT"
        ),
    )

# FTTH_DIRECCIONES_SETTINGS_V2_START
@dataclass(frozen=True)
class FtthDireccionesSettings:
    acs_max_candidates: int
    acs_batch_timeout_sec: int


def get_ftth_direcciones_settings() -> FtthDireccionesSettings:
    return FtthDireccionesSettings(
        acs_max_candidates=_positive_int("FTTH_ACS_MAX_CANDIDATES"),
        acs_batch_timeout_sec=_positive_int("FTTH_ACS_BATCH_TIMEOUT_SEC"),
    )
# FTTH_DIRECCIONES_SETTINGS_V2_END

