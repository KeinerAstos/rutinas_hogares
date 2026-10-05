from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_DIR / ".env", override=False)


def _required(name: str) -> str:
    value = str(os.getenv(name) or '').strip()
    if not value:
        raise RuntimeError(f'Falta configurar {name} en .env.')
    return value


def _required_int(name: str, minimum: int = 1, maximum: int = 65535) -> int:
    raw = _required(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f'{name} debe ser entero.') from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f'{name} fuera de rango.')
    return value


@dataclass(frozen=True)
class HfcSnapshotSettings:
    host: str
    port: int
    user: str
    query: str
    timeout: int


@dataclass(frozen=True)
class HfcModemsQuerySettings:
    host: str
    port: int
    user: str
    remote_root: str


@dataclass(frozen=True)
class HfcMacsCacheSettings:
    host: str
    port: int
    user: str
    remote_root: str


def get_hfc_snapshot_settings() -> HfcSnapshotSettings:
    return HfcSnapshotSettings(
        host=_required('HFC_SNAPSHOT_HOST'),
        port=_required_int('HFC_SNAPSHOT_PORT'),
        user=_required('HFC_SNAPSHOT_USER'),
        query=_required('HFC_SNAPSHOT_QUERY'),
        timeout=_required_int('HFC_SNAPSHOT_TIMEOUT', 1, 3600),
    )


def get_hfc_modems_query_settings() -> HfcModemsQuerySettings:
    return HfcModemsQuerySettings(
        host=_required('HFC_MODEMS_QUERY_HOST'),
        port=_required_int('HFC_MODEMS_QUERY_PORT'),
        user=_required('HFC_MODEMS_QUERY_USER'),
        remote_root=_required('HFC_MODEMS_QUERY_REMOTE_ROOT').rstrip('/'),
    )


def get_hfc_macs_cache_settings() -> HfcMacsCacheSettings:
    return HfcMacsCacheSettings(
        host=_required('HFC_MACS_CACHE_HOST'),
        port=_required_int('HFC_MACS_CACHE_PORT'),
        user=_required('HFC_MACS_CACHE_USER'),
        remote_root=_required('HFC_MACS_CACHE_REMOTE_ROOT').rstrip('/'),
    )