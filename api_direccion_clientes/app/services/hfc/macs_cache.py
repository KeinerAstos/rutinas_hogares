from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.config.hfc_endpoints_settings import get_hfc_macs_cache_settings

_HFC_MACS_CACHE = get_hfc_macs_cache_settings()

REMOTE_HOST = _HFC_MACS_CACHE.host
REMOTE_PORT = _HFC_MACS_CACHE.port
REMOTE_USER = _HFC_MACS_CACHE.user
REMOTE_ROOT = _HFC_MACS_CACHE.remote_root
REMOTE_HELPER = f'{REMOTE_ROOT}/app/api/hfc_macs_cache_query.py'
SSH_EXE = os.getenv('HFC_SSH_EXE', 'ssh.exe').strip() or 'ssh.exe'
DEFAULT_KEY = Path.home() / '.ssh' / 'noc_cable_atlas_ed25519'
SSH_KEY = Path(os.getenv('HFC_SSH_KEY', str(DEFAULT_KEY)).strip() or str(DEFAULT_KEY))


def _clean(value: Any) -> str:
    return str(value or '').strip()


def _safe_json(text: str) -> dict[str, Any]:
    value = _clean(text)
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    for line in reversed(value.splitlines()):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def query_hfc_node_macs_cache(node: str, limit: int = 20, timeout_sec: int = 8) -> dict[str, Any]:
    node = _clean(node).upper()
    limit = max(1, min(int(limit or 20), 100))
    if not node:
        return {'ok': False, 'codigo': 'HFC_CACHE_NODO_VACIO', 'nodo': '', 'macs': []}
    if not SSH_KEY.is_file():
        return {'ok': False, 'codigo': 'HFC_CACHE_SSH_KEY_NO_EXISTE', 'nodo': node, 'macs': []}

    command = [
        SSH_EXE,
        '-p', str(REMOTE_PORT),
        '-i', str(SSH_KEY),
        '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=5',
        f'{REMOTE_USER}@{REMOTE_HOST}',
        'python3', REMOTE_HELPER,
        '--node', node,
        '--limit', str(limit),
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=max(4, int(timeout_sec)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'codigo': 'HFC_CACHE_TIMEOUT', 'nodo': node, 'macs': []}
    except Exception as exc:
        return {'ok': False, 'codigo': 'HFC_CACHE_ERROR', 'nodo': node, 'macs': [], 'error': f'{type(exc).__name__}: {exc}'}

    data = _safe_json(proc.stdout)
    if not data:
        return {
            'ok': False,
            'codigo': 'HFC_CACHE_RESPUESTA_INVALIDA',
            'nodo': node,
            'macs': [],
            'exit_code': proc.returncode,
            'stderr': _clean(proc.stderr)[-500:],
        }
    data['exit_code'] = proc.returncode
    return data
