from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.services.helix.service import consultar_resumen_ot_helix
from app.services.hfc.remote import query_hfc_snapshot
from app.services.hfc.macs_cache import query_hfc_node_macs_cache
from app.config.hfc_endpoints_settings import get_hfc_modems_query_settings


_HFC_MODEMS_QUERY = get_hfc_modems_query_settings()

REMOTE_HOST = _HFC_MODEMS_QUERY.host
REMOTE_PORT = _HFC_MODEMS_QUERY.port
REMOTE_USER = _HFC_MODEMS_QUERY.user
REMOTE_ROOT = _HFC_MODEMS_QUERY.remote_root

REMOTE_HELPER = (
    f"{REMOTE_ROOT}/app/api/hfc_modems_query.py"
)

SSH_EXE = os.getenv("HFC_SSH_EXE", "ssh.exe").strip() or "ssh.exe"

DEFAULT_KEY = (
    Path.home()
    / ".ssh"
    / "noc_cable_atlas_ed25519"
)

SSH_KEY = Path(
    os.getenv("HFC_SSH_KEY", str(DEFAULT_KEY)).strip()
)

DIAGNOSTICADOR_SCRIPT = Path(
    os.getenv("DIAGNOSTICADOR_SCRIPT", "").strip()
)

CHROME_EXE = Path(
    os.getenv(
        "HFC_CHROME_EXE",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
)

MAX_CMTS_MAC_ATTEMPTS = max(
    1,
    min(
        int(os.getenv("HFC_DIRECCIONES_MAX_CMTS_MACS", "3") or "3"),
        5,
    ),
)

MAX_PATHTRAK_MAC_ATTEMPTS = max(
    1,
    min(
        int(os.getenv("HFC_DIRECCIONES_MAX_PATHTRAK_MACS", "3") or "3"),
        5,
    ),
)

HFC_CMTS_TIMEOUT_SEC = max(
    15,
    min(int(os.getenv("HFC_DIRECCIONES_CMTS_TIMEOUT_SEC", "35") or "35"), 95),
)

HFC_DIAGNOSTICADOR_TIMEOUT_SEC = max(
    20,
    min(int(os.getenv("HFC_DIRECCIONES_DIAG_TIMEOUT_SEC", "45") or "45"), 100),
)

HFC_TOTAL_BUDGET_SEC = max(
    60,
    min(int(os.getenv("HFC_DIRECCIONES_TOTAL_BUDGET_SEC", "120") or "120"), 300),
)

HFC_PATHTRAK_PAGE_TIMEOUT_MS = max(
    15000,
    min(int(os.getenv("HFC_PATHTRAK_PAGE_TIMEOUT_MS", "30000") or "30000"), 90000),
)

HFC_PATHTRAK_EXPORT_WAIT_SEC = max(
    10,
    min(int(os.getenv("HFC_PATHTRAK_EXPORT_WAIT_SEC", "25") or "25"), 55),
)

HFC_PATHTRAK_DOWNLOAD_TIMEOUT_MS = max(
    10000,
    min(int(os.getenv("HFC_PATHTRAK_DOWNLOAD_TIMEOUT_MS", "25000") or "25000"), 45000),
)



def _clean(value: Any) -> str:
    return str(value or "").strip()


def _payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    keys = {
        "tipo_red",
        "tipo_elemento",
        "nodo_detectado",
        "es_hfc",
        "es_ftth",
    }

    if keys.intersection(value.keys()):
        return value

    for key in ("data", "resultado", "respuesta", "detalle"):
        child = value.get(key)
        found = _payload(child)
        if found:
            return found

    return {}


def _wo_candidates(wo: str) -> list[str]:
    value = _clean(wo).upper()

    if not re.fullmatch(r"WO\d{13,14}", value):
        return []

    digits = value[2:]

    if len(digits) == 14:
        canonical = "WO" + str(int(digits)).zfill(13)
        result = [canonical]
        if value != canonical:
            result.append(value)
        return result

    return [value]


def _helix_hfc(wo: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    last: dict[str, Any] = {}

    for candidate in _wo_candidates(wo):
        try:
            result = consultar_resumen_ot_helix(candidate)
        except Exception:
            continue

        if isinstance(result, dict):
            last = result

        data = _payload(result)

        if data:
            tipo_red = _clean(
                data.get("tipo_red")
                or result.get("tipo_red")
            ).upper()

            nodo = _clean(
                data.get("nodo_detectado")
                or result.get("nodo_detectado")
            ).upper()

            if tipo_red:
                return result, data, candidate

            if nodo:
                return result, data, candidate

    return last, _payload(last), ""


def _safe_json(stdout: str) -> dict[str, Any]:
    text = _clean(stdout)
    if not text:
        return {}

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    lines = text.splitlines()

    for index in range(len(lines) - 1, -1, -1):
        candidate = "\n".join(lines[index:]).strip()

        if not candidate.startswith("{"):
            continue

        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            continue

    return {}


def _snapshot_node(node: str) -> dict[str, Any]:
    try:
        result = query_hfc_snapshot(node)
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "HFC_SNAPSHOT_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _cmts_macs(
    node: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    cmts = _clean(snapshot.get("cmts")).upper()
    ip = _clean(snapshot.get("ip"))
    vendor = _clean(snapshot.get("vendor")).upper()

    if not cmts or not ip:
        return {
            "ok": False,
            "codigo": "HFC_CMTS_RESOLUCION_INCOMPLETA",
            "nodo": node,
            "cmts": cmts,
            "ip_cmts": ip,
            "vendor": vendor,
            "macs": [],
        }

    if not SSH_KEY.exists():
        return {
            "ok": False,
            "codigo": "HFC_SSH_KEY_NO_EXISTE",
            "nodo": node,
            "macs": [],
        }

    remote_command = (
        f"cd {shlex.quote(REMOTE_ROOT)}"
        f" && set -a"
        f" && source {shlex.quote(REMOTE_ROOT + '/config/hfc.env')}"
        f" && set +a"
        f" && python3 {shlex.quote(REMOTE_HELPER)}"
        f" --node {shlex.quote(node)}"
        f" --cmts {shlex.quote(cmts)}"
        f" --ip {shlex.quote(ip)}"
        f" --vendor {shlex.quote(vendor)}"
        f" --limit 3"
    )

    command = [
        SSH_EXE,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-i",
        str(SSH_KEY),
        "-p",
        str(REMOTE_PORT),
        f"{REMOTE_USER}@{REMOTE_HOST}",
        remote_command,
    ]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=HFC_CMTS_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "codigo": "HFC_CMTS_TIMEOUT",
            "nodo": node,
            "cmts": cmts,
            "ip_cmts": ip,
            "vendor": vendor,
            "macs": [],
        }

    data = _safe_json(proc.stdout)

    if not data:
        return {
            "ok": False,
            "codigo": "HFC_CMTS_RESPUESTA_INVALIDA",
            "nodo": node,
            "cmts": cmts,
            "ip_cmts": ip,
            "vendor": vendor,
            "macs": [],
            "stderr": _clean(proc.stderr)[-500:],
        }

    data.setdefault("nodo", node)
    data.setdefault("cmts", cmts)
    data.setdefault("ip_cmts", ip)
    data.setdefault("vendor", vendor)

    return data


def _valid_macs(result: dict[str, Any]) -> list[dict[str, str]]:
    raw = result.get("macs")

    if not isinstance(raw, list):
        return []

    valid: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        mac = _clean(item.get("mac")).upper()
        ip = _clean(item.get("ip"))
        upstream = _clean(item.get("upstream"))

        if not re.fullmatch(
            r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}",
            mac,
        ):
            continue

        if mac in seen:
            continue

        # Preferir modems operativos. Si no existe IP, puede quedar
        # para PathTrak; para CMTS evitamos 0.0.0.0.
        if ip == "0.0.0.0":
            continue

        seen.add(mac)

        valid.append(
            {
                "mac": mac,
                "ip": ip,
                "upstream": upstream,
            }
        )

    return valid


def _patch_diagnosticador(source: str) -> str:
    text = source

    text = text.replace(
        "wait_until_template_ready(page, timeout_ms=180000)",
        "wait_until_template_ready(page, timeout_ms=8000)",
    )

    text = text.replace(
        "consultar_vecinos(page, timeout_ms=120000)",
        "consultar_vecinos(page, timeout_ms=40000)",
    )

    if CHROME_EXE.exists():
        chrome = str(CHROME_EXE).replace("\\", "\\\\")

        text = text.replace(
            "p.chromium.launch(headless=headless)",
            (
                "p.chromium.launch("
                "headless=headless, "
                f'executable_path=r"{chrome}"'
                ")"
            ),
        )

        text = text.replace(
            "p.chromium.launch(headless=True)",
            (
                "p.chromium.launch("
                "headless=True, "
                f'executable_path=r"{chrome}"'
                ")"
            ),
        )

    return text



# HFC_CONCURRENCY_GUARDS_V1
import threading as _hfc_concurrency_threading

_HFC_DIAGNOSTICADOR_MAX_CONCURRENT = max(
    1,
    int(
        os.getenv(
            "HFC_DIAGNOSTICADOR_MAX_CONCURRENT",
            "2",
        )
        or "2"
    ),
)

_HFC_PATHTRAK_MAX_CONCURRENT = max(
    1,
    int(
        os.getenv(
            "HFC_PATHTRAK_MAX_CONCURRENT",
            "1",
        )
        or "1"
    ),
)

_HFC_RESOURCE_QUEUE_TIMEOUT_SECONDS = max(
    30,
    int(
        os.getenv(
            "HFC_RESOURCE_QUEUE_TIMEOUT_SECONDS",
            "180",
        )
        or "180"
    ),
)

_HFC_DIAGNOSTICADOR_SEMAPHORE = (
    _hfc_concurrency_threading.BoundedSemaphore(
        _HFC_DIAGNOSTICADOR_MAX_CONCURRENT
    )
)

_HFC_PATHTRAK_SEMAPHORE = (
    _hfc_concurrency_threading.BoundedSemaphore(
        _HFC_PATHTRAK_MAX_CONCURRENT
    )
)


def _diagnosticador_mac_unlocked(
    mac: str,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    if not DIAGNOSTICADOR_SCRIPT.exists():
        return {
            "ok": False,
            "codigo": "DIAGNOSTICADOR_SCRIPT_NO_EXISTE",
            "mac": mac,
            "vecinos": [],
        }

    source = DIAGNOSTICADOR_SCRIPT.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )
    patched = _patch_diagnosticador(source)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix="atlas_hfc_diag_",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(patched)
            temp_path = Path(handle.name)

        command = [
            sys.executable,
            str(temp_path),
            "--query-type",
            "mac",
            "--query",
            mac,
            "--hold-seconds",
            "0",
            # HFC_DIAGNOSTICADOR_VECINOS_PRIORITY_V1_1
            "--vecinos-priority",
            "--json-output",
        ]

        if os.getenv(
            "DIAGNOSTICADOR_HEADLESS",
            "true",
        ).strip().lower() in {"1", "true", "yes", "on"}:
            command.append("--headless")

        effective_timeout = (
            float(timeout_sec)
            if timeout_sec is not None
            else float(HFC_DIAGNOSTICADOR_TIMEOUT_SEC)
        )
        effective_timeout = max(
            5.0,
            min(
                effective_timeout,
                float(HFC_DIAGNOSTICADOR_TIMEOUT_SEC),
            ),
        )

        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=effective_timeout,
            check=False,
        )

        result = _safe_json(proc.stdout)
        if not result:
            return {
                "ok": False,
                "codigo": "DIAGNOSTICADOR_RESPUESTA_INVALIDA",
                "mac": mac,
                "vecinos": [],
                "stderr": _clean(proc.stderr)[-500:],
            }

        datos = result.get("datos")
        if not isinstance(datos, dict):
            datos = {}

        vecinos_raw = datos.get("vecinos")
        if not isinstance(vecinos_raw, list):
            vecinos_raw = []

        vecinos = []
        for item in vecinos_raw:
            if not isinstance(item, dict):
                continue

            cuenta = _clean(item.get("cuenta_rr") or item.get("cuenta"))
            direccion = _clean(item.get("direccion"))
            vecino_mac = _clean(item.get("mac")).upper()

            if not cuenta and not direccion:
                continue

            vecinos.append(
                {
                    "cuenta_rr": cuenta,
                    "direccion": direccion,
                    "mac": vecino_mac,
                    "serial": "",
                    "estado": "",
                }
            )

        estado_vecinos = _clean(datos.get("estado_vecinos")).upper()
        usable = estado_vecinos == "VECINOS_OK" and bool(vecinos)

        return {
            "ok": usable,
            "codigo": (
                "DIAGNOSTICADOR_VECINOS_OK"
                if usable
                else "DIAGNOSTICADOR_SIN_VECINOS"
            ),
            "mac": mac,
            "estado": _clean(result.get("estado")),
            "estado_vecinos": estado_vecinos,
            "vecinos": vecinos,
            "total_vecinos": len(vecinos),
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "codigo": "DIAGNOSTICADOR_TIMEOUT",
            "mac": mac,
            "vecinos": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "DIAGNOSTICADOR_ERROR",
            "mac": mac,
            "vecinos": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


# HFC_DIAGNOSTICADOR_CONCURRENCY_GUARD_V1
def _diagnosticador_mac(
    mac: str,
    timeout_sec: float | None = None,
) -> dict[str, object]:

    acquired = _HFC_DIAGNOSTICADOR_SEMAPHORE.acquire(
        timeout=_HFC_RESOURCE_QUEUE_TIMEOUT_SECONDS
    )

    if not acquired:
        return {
            "ok": False,
            "codigo": "DIAGNOSTICADOR_CONCURRENCY_TIMEOUT",
            "mac": mac,
            "vecinos": [],
        }

    try:
        return _diagnosticador_mac_unlocked(
            mac,
            timeout_sec=timeout_sec,
        )
    finally:
        _HFC_DIAGNOSTICADOR_SEMAPHORE.release()


# HFC_ADAPTIVE_CACHE_PRIORITY_V1
# Preferencia efimera: NO persiste MAC real ni estado en disco.
import hashlib as _hfc_priority_hashlib
import threading as _hfc_priority_threading

_HFC_PRIORITY_LOCK = _hfc_priority_threading.Lock()
_HFC_PRIORITY_BY_NODE: dict[str, dict[str, str]] = {}


def _hfc_priority_node_key(node: Any) -> str:
    return str(node or "").strip().upper()


def _hfc_priority_mac_sha256(value: Any) -> str:
    normalized = "".join(
        ch
        for ch in str(value or "").upper()
        if ch in "0123456789ABCDEF"
    )
    if len(normalized) != 12:
        return ""
    return _hfc_priority_hashlib.sha256(
        normalized.encode("ascii")
    ).hexdigest()


def _hfc_priority_candidate_hash(candidate: Any) -> str:
    if not isinstance(candidate, dict):
        return ""
    return _hfc_priority_mac_sha256(
        candidate.get("mac")
    )


def _hfc_priority_reorder(
    node: Any,
    candidates: Any,
    generation: Any,
) -> list[dict[str, Any]]:
    ordered = list(candidates or [])
    node_key = _hfc_priority_node_key(node)
    generation_key = str(generation or "").strip()

    if not ordered or not node_key or not generation_key:
        return ordered

    with _HFC_PRIORITY_LOCK:
        state = dict(
            _HFC_PRIORITY_BY_NODE.get(node_key) or {}
        )

        if state.get("generation") != generation_key:
            _HFC_PRIORITY_BY_NODE.pop(node_key, None)
            return ordered

        preferred_hash = str(
            state.get("mac_sha256") or ""
        )

    if not preferred_hash:
        return ordered

    preferred_index = None

    for index, candidate in enumerate(ordered):
        if (
            _hfc_priority_candidate_hash(candidate)
            == preferred_hash
        ):
            preferred_index = index
            break

    if preferred_index in (None, 0):
        return ordered

    preferred = ordered.pop(preferred_index)
    ordered.insert(0, preferred)
    return ordered


def _hfc_priority_remember_success(
    node: Any,
    generation: Any,
    result: Any,
) -> bool:
    if not isinstance(result, dict) or not result.get("ok"):
        return False

    node_key = _hfc_priority_node_key(node)
    generation_key = str(generation or "").strip()

    candidate = result.get("candidate")
    mac_value = ""

    if isinstance(candidate, dict):
        mac_value = candidate.get("mac") or ""

    if not mac_value:
        mac_value = result.get("mac") or ""

    mac_sha256 = _hfc_priority_mac_sha256(mac_value)

    if not node_key or not generation_key or not mac_sha256:
        return False

    with _HFC_PRIORITY_LOCK:
        _HFC_PRIORITY_BY_NODE[node_key] = {
            "generation": generation_key,
            "mac_sha256": mac_sha256,
        }

    return True


def _hfc_priority_clear_for_tests() -> None:
    with _HFC_PRIORITY_LOCK:
        _HFC_PRIORITY_BY_NODE.clear()
# HFC_ADAPTIVE_CACHE_PRIORITY_V1_END

def _try_candidates(
    candidates: list[dict[str, str]],
    max_attempts: int,
    deadline: float | None = None,
) -> dict[str, Any]:
    attempts = []
    budget_exhausted = False

    for candidate in candidates[:max_attempts]:
        mac = _clean(candidate.get("mac")).upper()
        if not mac:
            continue

        remaining = None
        if deadline is not None:
            remaining = deadline - time.perf_counter()
            if remaining <= 5:
                budget_exhausted = True
                break

        started_attempt = time.perf_counter()
        result = _diagnosticador_mac(mac, timeout_sec=remaining)

        # HFC_DIAGNOSTICADOR_OBSERVABILIDAD_V1
        attempts.append(
            {
                "mac": mac,
                "codigo": _clean(result.get("codigo")),
                "ok": bool(result.get("ok")),
                "estado": _clean(result.get("estado")),
                "estado_vecinos": _clean(
                    result.get("estado_vecinos")
                ),
                "total_vecinos": int(
                    result.get("total_vecinos") or 0
                ),
                "error": _clean(result.get("error")),
                "stderr": _clean(
                    result.get("stderr")
                )[-500:],
                "duracion_seg": round(
                    time.perf_counter() - started_attempt,
                    2,
                ),
            }
        )

        if result.get("ok"):
            result["candidate"] = candidate
            result["attempts"] = attempts
            result["budget_exhausted"] = budget_exhausted
            return result

    return {
        "ok": False,
        "codigo": (
            "HFC_PRESUPUESTO_AGOTADO"
            if budget_exhausted
            else "DIAGNOSTICADOR_CANDIDATOS_SIN_EXITO"
        ),
        "vecinos": [],
        "attempts": attempts,
        "budget_exhausted": budget_exhausted,
    }


# HFC_PATHTRAK_PARALLEL_DIAGNOSTICADOR_V1
def _try_candidates_parallel(
    candidates: list[dict[str, str]],
    max_attempts: int,
) -> dict[str, Any]:
    """
    Prueba en paralelo las MAC provenientes de PathTrak.

    Se limita exclusivamente al fallback PathTrak.
    CMTS/cache conservan el comportamiento existente.
    """

    selected_candidates = []

    for candidate in candidates[:max_attempts]:

        if not isinstance(candidate, dict):
            continue

        mac = _clean(
            candidate.get("mac")
        ).upper()

        if not mac:
            continue

        selected = dict(candidate)
        selected["mac"] = mac

        selected_candidates.append(
            selected
        )

    if not selected_candidates:
        return {
            "ok": False,
            "codigo": "DIAGNOSTICADOR_CANDIDATOS_SIN_EXITO",
            "vecinos": [],
            "attempts": [],
            "parallel": True,
            "workers": 0,
        }

    workers = min(
        3,
        len(selected_candidates),
        max_attempts,
    )

    attempts = []
    completed_results = []

    def run_candidate(
        index: int,
        candidate: dict[str, str],
    ):
        mac = _clean(
            candidate.get("mac")
        ).upper()

        started_attempt = time.perf_counter()

        result = _diagnosticador_mac(
            mac
        )

        if not isinstance(result, dict):
            result = {}

        duration = round(
            time.perf_counter()
            - started_attempt,
            2,
        )

        return (
            index,
            candidate,
            result,
            duration,
        )

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="atlas_pt_diag",
    ) as executor:

        futures = [
            executor.submit(
                run_candidate,
                index,
                candidate,
            )
            for index, candidate
            in enumerate(
                selected_candidates,
                start=1,
            )
        ]

        for future in as_completed(
            futures
        ):
            try:
                (
                    index,
                    candidate,
                    result,
                    duration,
                ) = future.result()

            except Exception as exc:
                attempts.append(
                    {
                        "orden": 0,
                        "mac": "",
                        "codigo": "DIAGNOSTICADOR_PARALLEL_ERROR",
                        "ok": False,
                        "duracion_seg": 0,
                        "error": (
                            f"{type(exc).__name__}: {exc}"
                        ),
                    }
                )
                continue

            attempt = {
                "orden": index,
                "mac": _clean(
                    candidate.get("mac")
                ).upper(),
                "codigo": _clean(
                    result.get("codigo")
                ),
                "ok": bool(
                    result.get("ok")
                ),
                "estado": _clean(
                    result.get("estado")
                ),
                "estado_vecinos": _clean(
                    result.get("estado_vecinos")
                ),
                "total_vecinos": int(
                    result.get("total_vecinos") or 0
                ),
                "error": _clean(
                    result.get("error")
                ),
                "stderr": _clean(
                    result.get("stderr")
                )[-500:],
                "duracion_seg": duration,
            }

            attempts.append(
                attempt
            )

            completed_results.append(
                (
                    index,
                    candidate,
                    result,
                    duration,
                )
            )

    attempts.sort(
        key=lambda x: int(
            x.get("orden") or 999
        )
    )

    winners = [
        item
        for item in completed_results
        if bool(item[2].get("ok"))
        and isinstance(
            item[2].get("vecinos"),
            list,
        )
        and bool(
            item[2].get("vecinos")
        )
    ]

    if winners:

        # Priorizar la primera respuesta satisfactoria
        # observada por duración.
        winners.sort(
            key=lambda item: (
                float(item[3]),
                int(item[0]),
            )
        )

        (
            index,
            candidate,
            result,
            duration,
        ) = winners[0]

        final = dict(
            result
        )

        final["candidate"] = candidate
        final["attempts"] = attempts
        final["parallel"] = True
        final["workers"] = workers
        final["winner_order"] = index
        final["winner_duration_sec"] = duration

        return final

    return {
        "ok": False,
        "codigo": "DIAGNOSTICADOR_CANDIDATOS_SIN_EXITO",
        "vecinos": [],
        "attempts": attempts,
        "parallel": True,
        "workers": workers,
    }


def _pathtrak_url_from_capture_legacy(node: str) -> tuple[str, dict[str, Any]]:
    try:
        from app.integrations.pathtrak.pathtrak_spectrum import capturar_spectrum
        capture_func = capturar_spectrum
    except Exception:
        capture_func = None

    if capture_func is None:
        return "", {
            "ok": False,
            "codigo": "PATHTRAK_NO_DISPONIBLE",
        }

    try:
        try:
            result = capture_func(
                node,
                tipo_captura="qoe",
            )
        except TypeError:
            result = capture_func(node, "qoe")

    except Exception as exc:
        return "", {
            "ok": False,
            "codigo": "PATHTRAK_CAPTURE_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not isinstance(result, dict):
        return "", {
            "ok": False,
            "codigo": "PATHTRAK_CAPTURE_INVALIDA",
        }

    url = _clean(result.get("url"))

    if "/node/health" in url:
        return url, result

    raw = result.get("raw")
    if isinstance(raw, dict):
        for key in ("url", "page_url", "node_url"):
            candidate = _clean(raw.get(key))
            if "/node/health" in candidate:
                return candidate, result

    return "", result

# HFC_PATHTRAK_CDP_RETRY_V1
# Reintento limitado solo a establecer la conexión con el Chrome remoto.
def _connect_pathtrak_cdp_retry(pt, playwright, nombre_region):
    for attempt in (1, 2):
        try:
            return pt.conectar_pathtrak_cdp(
                playwright,
                nombre_region=nombre_region,
                reuse_existing=False,
            )
        except Exception as exc:
            print(
                "[PATHTRAK-CDP] "
                f"region={nombre_region} intento={attempt}/2 "
                f"error={type(exc).__name__}: {exc}"
            )
            if attempt == 2:
                raise
            time.sleep(1)


# HFC_PATHTRAK_FAST_FIRST_V1
# FALLBACK_LEGACY_OBLIGATORIO
def _pathtrak_url_fast(node: str, opcion_indice: int = 0) -> tuple[str, dict[str, Any]]:
    # Resolver exclusivamente una URL PNM /node/health.
    # Cualquier resultado no certificado deja URL vacia para que
    # el wrapper ejecute la ruta legacy intacta.
    node = _clean(node).upper()

    if not node:
        return "", {
            "ok": False,
            "codigo": "PATHTRAK_FAST_NODO_VACIO",
        }

    try:
        from playwright.sync_api import sync_playwright
        from app.integrations.pathtrak import pathtrak_spectrum as pt
    except Exception as exc:
        return "", {
            "ok": False,
            "codigo": "PATHTRAK_FAST_IMPORT_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }

    urls = [
        ("CENTRO", pt.PATHTRAK_CENTRO_URL),
        ("REGIONALES", pt.PATHTRAK_REGIONALES_URL),
    ]

    only_region = os.getenv(
        "PATHTRAK_ONLY_REGION",
        "",
    ).strip().upper()

    region_preferida = ""

    if only_region:
        urls = [
            (nombre, url)
            for nombre, url in urls
            if nombre.upper() == only_region
        ]

        if not urls:
            return "", {
                "ok": False,
                "codigo": "PATHTRAK_FAST_REGION_INVALIDA",
                "nodo": node,
            }

        region_preferida = only_region

    else:
        try:
            urls, region_preferida = (
                pt.ordenar_urls_por_region_cache(
                    nodo=node,
                    urls=urls,
                )
            )
        except Exception:
            # Si falla la lectura del cache, mantener orden historico.
            region_preferida = ""

    try:
        with sync_playwright() as playwright:
            for region_index, (nombre_region, url_region) in enumerate(urls):
                browser = None
                context = None
                dashboard = None
                qoe_page = None
                owned_pages = []
                browser_owned = True

                try:
                    # HFC_PATHTRAK_REMOTE_CDP_FAST_V1
                    browser_owned = True

                    if (
                        getattr(
                            pt,
                            "PATHTRAK_BROWSER_MODE",
                            "local",
                        )
                        == "remote_cdp"
                    ):
                        (
                            browser,
                            context,
                            dashboard,
                        ) = _connect_pathtrak_cdp_retry(
                            pt, playwright, nombre_region,
                        )

                        browser_owned = False
                        owned_pages.append(dashboard)
                        # Solo popups cuyo origen es esta página, nunca
                        # páginas globales de otras consultas CDP.
                        dashboard.on("popup", lambda popup: owned_pages.append(popup))

                    else:
                        launch_options: dict[str, Any] = {
                            "headless": pt.PATHTRAK_HEADLESS,
                            "slow_mo": (
                                250
                                if not pt.PATHTRAK_HEADLESS
                                else 0
                            ),
                            "args": [
                                "--ignore-certificate-errors",
                                "--allow-insecure-localhost",
                                "--disable-extensions",
                                "--disable-features=Translate",
                                "--start-maximized",
                            ],
                        }

                        if pt.PATHTRAK_PROXY:
                            launch_options["proxy"] = {
                                "server": pt.PATHTRAK_PROXY,
                            }
                        else:
                            launch_options["args"].extend(
                                [
                                    "--no-proxy-server",
                                    "--proxy-bypass-list=*",
                                    "--disable-http2",
                                    "--disable-quic",
                                ]
                            )

                        browser = playwright.chromium.launch(
                            **launch_options
                        )

                        context = browser.new_context(
                            ignore_https_errors=True,
                            http_credentials={
                                "username": pt.PATHTRAK_USER,
                                "password": pt.PATHTRAK_PASSWORD,
                            },
                            viewport={
                                "width": 1920,
                                "height": 1080,
                            },
                            accept_downloads=False,
                            locale="es-CO",
                        )

                        dashboard = context.new_page()

                    pt.login_pathtrak(
                        dashboard,
                        url_region,
                    )

                    qoe_page, estado = (
                        pt.seleccionar_nodo_y_esperar_qoe(
                            context,
                            dashboard,
                            node,
                            timeout_ms=60000,
                            owned_pages=(owned_pages if not browser_owned else None),
                            opcion_indice=opcion_indice,
                        )
                    )

                    direct_url = _clean(
                        (estado or {}).get("url")
                        or qoe_page.url
                    )

                    tipo_vista = _clean(
                        (estado or {}).get("tipo")
                    ).lower()

                    if (
                        tipo_vista == "qoe_pnm"
                        and "/node/health"
                        in direct_url.lower()
                    ):
                        # Preservar refresco del cache de region
                        # solo despues de resolver exitosamente el nodo.
                        try:
                            pt.guardar_region_cache(
                                nodo=node,
                                region=nombre_region,
                            )
                        except Exception:
                            pass

                        return direct_url, {
                            "ok": True,
                            "codigo": "PATHTRAK_FAST_NODE_HEALTH_OK",
                            "tipo": "qoe",
                            "tipo_vista": tipo_vista,
                            "nodo": node,
                            "region": nombre_region,
                            "region_preferida": (
                                region_preferida or None
                            ),
                            "url": direct_url,
                            "fast_path": True,
                            "opcion_indice": opcion_indice,
                        }

                    # Nodo localizado pero vista fuera del contrato PNM.
                    # No adivinar: volver al procedimiento legacy.
                    return "", {
                        "ok": False,
                        "codigo": (
                            "PATHTRAK_FAST_VISTA_NO_CERTIFICADA"
                        ),
                        "nodo": node,
                        "region": nombre_region,
                        "tipo_vista": tipo_vista,
                        "url": direct_url,
                        "opcion_indice": opcion_indice,
                    }

                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    error_lower = error.lower()

                    no_estaba_en_region = any(
                        marker in error_lower
                        for marker in (
                            "no se encontraron datos",
                            "no apareciÃ³ la coincidencia exacta",
                            "no aparecio la coincidencia exacta",
                            "no apareciÃ³ nodo",
                            "no aparecio nodo",
                            "no contiene el nodo",
                            "no aparece como resultado tipo nodo",
                            "lista apareciÃ³, pero no contiene",
                            "probablemente solo hay mÃ³dems",
                            "probablemente solo hay modems",
                            "sin coincidencia exacta",
                        )
                    )

                    # HFC_PATHTRAK_REGION_FALLBACK_NO_EXACT_V1
                    # Si la búsqueda exacta no encuentra el nodo en esta región,
                    # continuar con la siguiente región aunque el mensaje tenga
                    # tildes UTF-8 distintas a las variantes históricas.
                    if (
                        'coincidencia exacta' in error_lower
                        and 'no apareci' in error_lower
                    ):
                        no_estaba_en_region = True

                    print(
                        "[PATHTRAK-FAST] "
                        f"nodo={node} region={nombre_region} "
                        f"etapa=login_o_busqueda error={error[:500]}"
                    )
                    # Si la primera región falla durante la navegación o
                    # lectura, intentar la otra con una pestaña propia.
                    # finally cerrará solo las páginas de este intento.
                    if no_estaba_en_region or region_index < len(urls) - 1:
                        continue

                    return "", {
                        "ok": False,
                        "codigo": "PATHTRAK_FAST_ERROR",
                        "nodo": node,
                        "region": nombre_region,
                        "error": error,
                    }

                finally:
                    if browser_owned:
                        if context is not None:
                            try:
                                context.close()
                            except Exception:
                                pass

                        if browser is not None:
                            try:
                                browser.close()
                            except Exception:
                                pass

                    else:
                        # Ambas páginas pertenecen a esta consulta: el
                        # dashboard se creó con reuse_existing=False y
                        # seleccionar_nodo_y_esperar_qoe crea su probe.
                        # Nunca cerrar el browser/contexto CDP compartido.
                        if qoe_page is not None:
                            owned_pages.append(qoe_page)
                        closed_ids = set()
                        for owned_page in reversed(owned_pages):
                            if owned_page is None or id(owned_page) in closed_ids:
                                continue
                            closed_ids.add(id(owned_page))
                            try:
                                if not owned_page.is_closed():
                                    owned_page.close()
                                    print("[PATHTRAK-CLEANUP] qoe_owned_page_closed")
                            except Exception as close_exc:
                                print(
                                    "[PATHTRAK-CLEANUP] "
                                    f"qoe_page_close_error={type(close_exc).__name__}: {close_exc}"
                                )

    except Exception as exc:
        return "", {
            "ok": False,
            "codigo": "PATHTRAK_FAST_PLAYWRIGHT_ERROR",
            "nodo": node,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return "", {
        "ok": False,
        "codigo": "PATHTRAK_FAST_NODO_NO_RESUELTO",
        "nodo": node,
    }


# HFC_PATHTRAK_FALLBACK_LEGACY_V1
def _pathtrak_url_from_capture(node: str) -> tuple[str, dict[str, Any]]:
    # Wrapper FAST-FIRST con fallback legacy obligatorio.
    try:
        fast_url, fast_result = _pathtrak_url_fast(node)
    except Exception as exc:
        fast_url = ""
        fast_result = {
            "ok": False,
            "codigo": "PATHTRAK_FAST_WRAPPER_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }

    if (
        fast_url
        and "/node/health" in fast_url.lower()
    ):
        return fast_url, fast_result

    # FALLBACK_LEGACY_OBLIGATORIO
    legacy_url, legacy_result = _pathtrak_url_from_capture_legacy(node)

    if (
        legacy_url
        and "/node/health" in legacy_url.lower()
    ):
        return legacy_url, legacy_result

    # HFC_PATHTRAK_NODE_TRAILING_SPACE_FALLBACK_V1
    #
    # Algunos nodos cortos pueden no disparar correctamente el
    # autocompletado de PathTrak con el texto exacto.
    #
    # Ejemplo certificado:
    #   EDE   -> no resuelve
    #   "EDE " -> REGIONALES resuelve correctamente
    #
    # IMPORTANTE:
    # este espacio NO se usa de forma normal.
    # Solo se prueba despues de fallar FAST + LEGACY normal.
    node_clean = _clean(node).upper()

    if node_clean:
        node_with_space = node_clean + " "

        retry_url, retry_result = (
            _pathtrak_url_from_capture_legacy(
                node_with_space
            )
        )

        if (
            retry_url
            and "/node/health" in retry_url.lower()
        ):
            retry_payload = (
                dict(retry_result)
                if isinstance(retry_result, dict)
                else {}
            )

            retry_payload[
                "node_space_fallback_used"
            ] = True

            retry_payload[
                "node_original"
            ] = node_clean

            retry_payload[
                "node_query_used"
            ] = node_with_space

            retry_payload[
                "codigo_fallback"
            ] = (
                "PATHTRAK_NODE_TRAILING_SPACE_FALLBACK_OK"
            )

            return retry_url, retry_payload

    return legacy_url, legacy_result



def _pathtrak_macs_csv_legacy(node: str) -> dict[str, Any]:
    direct_url, capture = _pathtrak_url_from_capture(node)

    if not direct_url:
        return {
            "ok": False,
            "codigo": "PATHTRAK_URL_NODO_NO_RESUELTA",
            "nodo": node,
            "macs": [],
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "PATHTRAK_PLAYWRIGHT_NO_DISPONIBLE",
            "nodo": node,
            "macs": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    user = os.getenv("PATHTRAK_USER", "").strip() or os.getenv("PATHTRAK_USERNAME", "").strip()
    password = os.getenv("PATHTRAK_PASSWORD", "").strip() or os.getenv("PATHTRAK_PASS", "").strip()
    proxy = os.getenv("PATHTRAK_PROXY", "").strip()

    mac_re = re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
    ip_re = re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
    )

    def visible(locator):
        try:
            return locator.count() > 0 and locator.first.is_visible()
        except Exception:
            return False

    try:
        with tempfile.TemporaryDirectory(prefix="atlas_pathtrak_export_") as temp_dir:
            temp_dir_path = Path(temp_dir)
            download_path = None

            with sync_playwright() as playwright:
                # HFC_PATHTRAK_REMOTE_CDP_EXPORT_V1
                from app.integrations.pathtrak import pathtrak_spectrum as pt

                browser_owned = True
                browser = None
                context = None
                page = None

                if (
                    getattr(
                        pt,
                        "PATHTRAK_BROWSER_MODE",
                        "local",
                    )
                    == "remote_cdp"
                ):
                    (
                        browser,
                        context,
                        page,
                    ) = pt.conectar_pathtrak_cdp(
                        playwright,
                        nombre_region="EXPORT",
                    )

                    browser_owned = False

                else:
                    launch: dict[str, Any] = {
                        "headless": pt.PATHTRAK_HEADLESS,
                        "args": ["--ignore-certificate-errors","--disable-gpu","--no-first-run"],
                    }

                    if CHROME_EXE.exists():
                        launch["executable_path"] = str(CHROME_EXE)

                    if proxy:
                        launch["proxy"] = {"server": proxy}

                    browser = playwright.chromium.launch(**launch)
                    context = browser.new_context(
                        ignore_https_errors=True,
                        viewport={"width": 1800, "height": 1200},
                        accept_downloads=True,
                    )
                    page = context.new_page()

                def _close_pathtrak_export():
                    if browser_owned:
                        try:
                            if context is not None:
                                context.close()
                        except Exception:
                            pass

                        try:
                            if browser is not None:
                                _close_pathtrak_export()
                        except Exception:
                            pass

                    else:
                        try:
                            if (
                                page is not None
                                and not page.is_closed()
                            ):
                                page.close()
                        except Exception:
                            pass


                page.goto(direct_url, wait_until="domcontentloaded", timeout=HFC_PATHTRAK_PAGE_TIMEOUT_MS)
                page.wait_for_timeout(2500)

                pass_input = page.locator('input[type="password"]')

                if visible(pass_input) and user and password:
                    user_inputs = page.locator('input[type="text"],input[type="email"],input:not([type])')
                    if user_inputs.count() > 0:
                        user_inputs.first.fill(user)
                    pass_input.first.fill(password)
                    pass_input.first.press("Enter")
                    page.wait_for_timeout(5500)
                    page.goto(direct_url, wait_until="domcontentloaded", timeout=HFC_PATHTRAK_PAGE_TIMEOUT_MS)
                    page.wait_for_timeout(3000)

                for selector in (
                    'button:has-text("Aceptar")',
                    '[role="button"]:has-text("Aceptar")',
                    'button:has-text("OK")',
                ):
                    button = page.locator(selector)
                    if visible(button):
                        try:
                            button.first.click(timeout=2500)
                            page.wait_for_timeout(800)
                        except Exception:
                            pass

                export_button = None

                for _ in range(HFC_PATHTRAK_EXPORT_WAIT_SEC):
                    for selector in (
                        'button:has-text("Exportar todo")',
                        '[role="button"]:has-text("Exportar todo")',
                        'text=Exportar todo',
                    ):
                        locator = page.locator(selector)
                        if visible(locator):
                            export_button = locator.first
                            break
                    if export_button is not None:
                        break
                    page.wait_for_timeout(1000)

                if export_button is None:
                    _close_pathtrak_export()
                    return {
                        "ok": False,
                        "codigo": "PATHTRAK_EXPORTAR_TODO_NO_ENCONTRADO",
                        "nodo": node,
                        "macs": [],
                        "url": direct_url,
                    }

                try:
                    with page.expect_download(timeout=HFC_PATHTRAK_DOWNLOAD_TIMEOUT_MS) as info:
                        export_button.click(timeout=5000)
                    download = info.value
                except Exception:
                    download = None

                if download is None:
                    _close_pathtrak_export()
                    return {
                        "ok": False,
                        "codigo": "PATHTRAK_EXPORT_DOWNLOAD_NO_CONFIRMADO",
                        "nodo": node,
                        "macs": [],
                        "url": direct_url,
                    }

                filename = download.suggested_filename or "pathtrak_export.csv"
                download_path = temp_dir_path / filename
                download.save_as(str(download_path))
                _close_pathtrak_export()

            raw = download_path.read_bytes()
            export_text = None

            for encoding in ("utf-8-sig","utf-8","latin-1"):
                try:
                    export_text = raw.decode(encoding)
                    break
                except Exception:
                    pass

            if export_text is None:
                return {
                    "ok": False,
                    "codigo": "PATHTRAK_EXPORT_NO_DECODIFICABLE",
                    "nodo": node,
                    "macs": [],
                }

            rows = []
            seen = set()

            for line in export_text.splitlines():
                match = mac_re.search(line)
                if not match:
                    continue

                mac = match.group(0).upper().replace("-", ":")
                if mac in seen:
                    continue

                seen.add(mac)
                ips = ip_re.findall(line)

                rows.append({
                    "mac": mac,
                    "ip": ips[0] if ips else "",
                    "upstream": "",
                })

                if len(rows) >= MAX_PATHTRAK_MAC_ATTEMPTS:
                    break

            return {
                "ok": bool(rows),
                "codigo": "PATHTRAK_EXPORT_MACS_OK" if rows else "PATHTRAK_EXPORT_SIN_MACS",
                "nodo": node,
                "macs": rows,
                "total_macs": len(rows),
                "url": direct_url,
                "fuente": "EXPORTAR_TODO",
            }

    except Exception as exc:
        return {
            "ok": False,
            "codigo": "PATHTRAK_EXPORT_ERROR",
            "nodo": node,
            "macs": [],
            "url": direct_url,
            "error": f"{type(exc).__name__}: {exc}",
        }



# HFC_PATHTRAK_DOM_LIMITED_V1
def _pathtrak_macs_unlocked_single(node: str, opcion_indice: int = 0) -> dict[str, Any]:
    # Modo local conserva el mecanismo historico.
    try:
        from app.integrations.pathtrak import pathtrak_spectrum as pt
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "PATHTRAK_IMPORT_ERROR",
            "nodo": node,
            "macs": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    browser_mode = getattr(
        pt,
        "PATHTRAK_BROWSER_MODE",
        "local",
    )

    if browser_mode != "remote_cdp":
        return _pathtrak_macs_csv_legacy(
            node
        )

    # En remote_cdp NO usar el wrapper legacy:
    # el SOCKS/Chromium local ya fue descartado.
    try:
        direct_url, capture = (
            _pathtrak_url_fast(
                node,
                opcion_indice=opcion_indice,
            )
        )
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "PATHTRAK_REMOTE_CDP_FAST_ERROR",
            "nodo": node,
            "macs": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not direct_url:
        return {
            "ok": False,
            "codigo": (
                _clean(
                    (
                        capture
                        if isinstance(
                            capture,
                            dict,
                        )
                        else {}
                    ).get(
                        "codigo"
                    )
                )
                or "PATHTRAK_REMOTE_CDP_URL_NO_RESUELTA"
            ),
            "nodo": node,
            "macs": [],
            "capture": capture,
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "PATHTRAK_PLAYWRIGHT_NO_DISPONIBLE",
            "nodo": node,
            "macs": [],
            "url": direct_url,
            "error": f"{type(exc).__name__}: {exc}",
        }

    mac_re = re.compile(
        r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b"
    )

    ip_re = re.compile(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    )

    rows: list[
        dict[str, str]
    ] = []

    seen: set[str] = set()

    try:

        with sync_playwright() as playwright:

            (
                _browser,
                _context,
                page,
            ) = _connect_pathtrak_cdp_retry(
                pt, playwright, "DOM",
            )

            # HFC_PATHTRAK_OWNED_PAGE_CLOSE_V1
            # Esta consulta crea su propia pestaña; no toca paginas compartidas.
            try:
                current_url = (
                    page.url or ""
                )

                if (
                    direct_url
                    not in current_url
                ):
                    page.goto(
                        direct_url,
                        wait_until="domcontentloaded",
                        timeout=HFC_PATHTRAK_PAGE_TIMEOUT_MS,
                    )

                # Esperar unicamente la tabla necesaria.
                try:
                    page.wait_for_selector(
                        "#gridTable",
                        state="attached",
                        timeout=min(
                            HFC_PATHTRAK_PAGE_TIMEOUT_MS,
                            15000,
                        ),
                    )
                except Exception:
                    pass

                page.wait_for_timeout(
                    700
                )

                # Modal de licencia / confirmacion.
                for selector in (
                    'button:has-text("Aceptar")',
                    '[role="button"]:has-text("Aceptar")',
                    'button:has-text("OK")',
                ):
                    try:
                        button = page.locator(
                            selector
                        )

                        if (
                            button.count() > 0
                            and button.first.is_visible()
                        ):
                            button.first.click(
                                timeout=2500
                            )

                            page.wait_for_timeout(
                                300
                            )

                            break

                    except Exception:
                        pass

                grid = page.locator(
                    "#gridTable"
                )

                if grid.count() < 1:
                    return {
                        "ok": False,
                        "codigo": "PATHTRAK_DOM_GRID_NO_ENCONTRADO",
                        "nodo": node,
                        "macs": [],
                        "url": direct_url,
                        "capture": capture,
                    }

                def collect_visible() -> None:

                    candidates = page.locator(
                        "#gridTable .ag-row"
                    )

                    count = candidates.count()

                    for index in range(
                        count
                    ):

                        if (
                            len(rows)
                            >= MAX_PATHTRAK_MAC_ATTEMPTS
                        ):
                            return

                        row = candidates.nth(
                            index
                        )

                        try:

                            if not row.is_visible():
                                continue

                            text = (
                                row.inner_text(
                                    timeout=800
                                )
                                or ""
                            ).strip()

                        except Exception:
                            continue

                        if not text:
                            continue

                        match = mac_re.search(
                            text
                        )

                        if not match:
                            continue

                        mac = (
                            match.group(0)
                            .upper()
                            .replace(
                                "-",
                                ":",
                            )
                        )

                        if mac in seen:
                            continue

                        ips = ip_re.findall(
                            text
                        )

                        seen.add(
                            mac
                        )

                        rows.append(
                            {
                                "mac": mac,
                                "ip": (
                                    ips[0]
                                    if ips
                                    else ""
                                ),
                                "upstream": "",
                            }
                        )

                # Primero exclusivamente lo visible.
                collect_visible()

                # Solo si hay menos de las candidatas requeridas,
                # desplazar un poco la tabla.
                if (
                    len(rows)
                    < MAX_PATHTRAK_MAC_ATTEMPTS
                ):

                    viewport = page.locator(
                        "#gridTable .ag-body-viewport"
                    ).first

                    if (
                        viewport.count()
                        > 0
                    ):

                        for _ in range(
                            3
                        ):

                            if (
                                len(rows)
                                >= MAX_PATHTRAK_MAC_ATTEMPTS
                            ):
                                break

                            viewport.evaluate(
                                """
                                el => {
                                    el.scrollTop =
                                        Math.min(
                                            el.scrollHeight,
                                            el.scrollTop +
                                            Math.max(
                                                200,
                                                el.clientHeight * 0.8
                                            )
                                        );
                                }
                                """
                            )

                            page.wait_for_timeout(
                                300
                            )

                            collect_visible()

                            # HFC_PATHTRAK_WAIT_MACS_READY_V1
                            # PathTrak puede renderizar #gridTable antes de publicar filas.
                            # Salir inmediatamente si aparece una MAC; declarar vacío solo
                            # tras estabilidad sin loader o al alcanzar el tope de 35 s.
                            if not rows:
                                wait_started = time.perf_counter()
                                wait_deadline = wait_started + 35.0
                                stable_empty_checks = 0

                                while (
                                    not rows
                                    and time.perf_counter() < wait_deadline
                                ):
                                    page.wait_for_timeout(750)
                                    collect_visible()

                                    if rows:
                                        break

                                    loading_visible = False

                                    try:
                                        body_text = (
                                            page.locator('body').inner_text(timeout=1000)
                                            or ''
                                        ).lower()

                                        loading_visible = bool(
                                            'cargando...' in body_text
                                            or 'cargando…' in body_text
                                            or 'loading...' in body_text
                                        )
                                    except Exception:
                                        pass

                                    if not loading_visible:
                                        for loading_selector in (
                                            '.ag-overlay-loading-center',
                                            '.ag-overlay-loading-wrapper',
                                            '[class*=loading-overlay]',
                                            '[class*=loadingOverlay]',
                                        ):
                                            try:
                                                loc = page.locator(loading_selector)
                                                if (
                                                    loc.count() > 0
                                                    and loc.first.is_visible()
                                                ):
                                                    loading_visible = True
                                                    break
                                            except Exception:
                                                continue

                                    elapsed_wait = time.perf_counter() - wait_started

                                    if loading_visible or elapsed_wait < 8.0:
                                        stable_empty_checks = 0
                                        continue

                                    try:
                                        rendered_rows = page.locator(
                                            '#gridTable .ag-row'
                                        ).count()
                                    except Exception:
                                        rendered_rows = 0

                                    if rendered_rows > 0:
                                        stable_empty_checks = 0
                                        continue

                                    stable_empty_checks += 1

                                    if stable_empty_checks >= 3:
                                        break

                return {
                    "ok": bool(rows),
                    "codigo": (
                        "PATHTRAK_DOM_MACS_OK"
                        if rows
                        else "PATHTRAK_DOM_SIN_MACS"
                    ),
                    "nodo": node,
                    "macs": rows[
                        :MAX_PATHTRAK_MAC_ATTEMPTS
                    ],
                    "total_macs": len(rows),
                    "url": direct_url,
                    "fuente": "DOM_LIMITADO",
                    "capture": capture,
                    "opcion_indice": opcion_indice,
                }

            finally:
                try:
                    if not page.is_closed():
                        page.close()
                except Exception as close_exc:
                    print(
                        "[PATHTRAK-CLEANUP] "
                        f"own_page_close_error={type(close_exc).__name__}: {close_exc}"
                    )
    except Exception as exc:

        return {
            "ok": False,
            "codigo": "PATHTRAK_DOM_ERROR",
            "nodo": node,
            "macs": [],
            "url": direct_url,
            "capture": capture,
            "error": (
                f"{type(exc).__name__}: {exc}"
            ),
        }



# HFC_PATHTRAK_MULTI_DOM_OPTIONS_V2
def _pathtrak_macs_unlocked(node: str) -> dict[str, Any]:
    resultados: list[dict[str, Any]] = []

    for opcion_indice in range(0, 2):
        resultado = _pathtrak_macs_unlocked_single(
            node,
            opcion_indice=opcion_indice,
        )

        if isinstance(resultado, dict):
            resultado.setdefault(
                "opcion_indice",
                opcion_indice,
            )

            resultados.append(resultado)

            if resultado.get("macs"):
                resultado["codigo"] = (
                    resultado.get("codigo")
                    or "PATHTRAK_DOM_MACS_OK"
                )

                resultado["opciones_probadas"] = resultados

                return resultado

            codigo = str(
                resultado.get("codigo") or ""
            ).upper()

            if codigo in {
                "PATHTRAK_FAST_NODO_NO_RESUELTO",
                "PATHTRAK_FAST_ERROR",
                "PATHTRAK_REMOTE_CDP_URL_NO_RESUELTA",
            }:
                break

    if resultados:
        ultimo = resultados[-1]
        ultimo["opciones_probadas"] = resultados
        return ultimo

    return {
        "ok": False,
        "codigo": "PATHTRAK_DOM_SIN_OPCIONES",
        "nodo": node,
        "macs": [],
        "opciones_probadas": [],
    }



# HFC_PATHTRAK_CONCURRENCY_GUARD_V1
def _pathtrak_macs(
    node: str,
) -> dict[str, Any]:

    acquired = _HFC_PATHTRAK_SEMAPHORE.acquire(
        timeout=_HFC_RESOURCE_QUEUE_TIMEOUT_SECONDS
    )

    if not acquired:
        return {
            "ok": False,
            "codigo": "PATHTRAK_CONCURRENCY_TIMEOUT",
            "nodo": node,
            "macs": [],
        }

    try:
        return _pathtrak_macs_unlocked(
            node
        )
    finally:
        _HFC_PATHTRAK_SEMAPHORE.release()


def consultar_direcciones_hfc(
    wo: str,
    helix_result: dict[str, Any] | None = None,
    helix_data: dict[str, Any] | None = None,
    consulted_wo: str = "",
) -> dict[str, Any]:
    # DIRECCIONES_PERF_HFC_V1
    # DIRECCIONES_HFC_CACHE_FIRST_V1
    started = time.perf_counter()
    deadline = started + float(HFC_TOTAL_BUDGET_SEC)

    # HFC_TIMING_OBSERVABILIDAD_V1
    timing_hfc: dict[str, float] = {}

    def _timing_start() -> float:
        return time.perf_counter()

    def _timing_end(name: str, started_at: float) -> None:
        timing_hfc[name] = round(
            time.perf_counter() - started_at,
            3,
        )
    original_wo = _clean(wo).upper()

    candidates = _wo_candidates(original_wo)
    if not candidates:
        return {
            "ok": False,
            "codigo": "WO_INVALIDA",
            "wo": original_wo,
            "tipo_red": "HFC",
        }

    if (
        isinstance(helix_result, dict)
        and helix_result
        and isinstance(helix_data, dict)
        and helix_data
    ):
        helix = helix_result
        data = helix_data
        consulted_wo = _clean(consulted_wo).upper() or original_wo
    else:
        helix, data, consulted_wo = _helix_hfc(original_wo)

    tipo_red = _clean(
        data.get("tipo_red")
        or helix.get("tipo_red")
    ).upper()

    tipo_elemento = _clean(
        data.get("tipo_elemento")
        or helix.get("tipo_elemento")
    ).upper()

    node = _clean(
        data.get("nodo_detectado")
        or helix.get("nodo_detectado")
    ).upper()

    if tipo_red != "HFC":
        return {
            "ok": False,
            "codigo": "WO_NO_HFC",
            "wo": original_wo,
            "wo_consultada": consulted_wo,
            "tipo_red": tipo_red,
        }

    if tipo_elemento and tipo_elemento != "NODO":
        return {
            "ok": False,
            "codigo": "HFC_ELEMENTO_NO_NODO",
            "wo": original_wo,
            "tipo_red": "HFC",
            "tipo_elemento": tipo_elemento,
        }

    if not node:
        return {
            "ok": False,
            "codigo": "HFC_NODO_NO_IDENTIFICADO",
            "wo": original_wo,
            "tipo_red": "HFC",
        }

    _t_snapshot = _timing_start()
    snapshot = _snapshot_node(node)
    _timing_end("snapshot_node", _t_snapshot)

    _t_cache = _timing_start()
    cache_result = query_hfc_node_macs_cache(
        node,
        limit=max(10, MAX_CMTS_MAC_ATTEMPTS * 4),
        timeout_sec=8,
    )
    _timing_end("cache_query", _t_cache)

    cache_candidates = _valid_macs(cache_result)
    _hfc_cache_generation = _clean(cache_result.get("generated_at"))
    cache_candidates = _hfc_priority_reorder(
        node,
        cache_candidates,
        _hfc_cache_generation,
    )

    cmts_result: dict[str, Any] = {}
    cmts_candidates: list[dict[str, str]] = []
    selected_result: dict[str, Any] = {}

    # HFC_PRESERVAR_INTENTOS_POR_ETAPA_V1
    cache_attempts: list[dict[str, Any]] = []
    cmts_attempts: list[dict[str, Any]] = []
    pathtrak_attempts: list[dict[str, Any]] = []

    source = "HFC_AUTO_CACHE" if cache_candidates else "CMTS"
    cache_used = bool(cache_candidates)
    live_cmts_used = False
    fallback_pathtrak = False
    fallback_reason = ""
    pathtrak_result: dict[str, Any] = {}

    if cache_candidates:
        _t_cache_diag = _timing_start()

        selected_result = _try_candidates(
            cache_candidates,
            MAX_CMTS_MAC_ATTEMPTS,
            deadline=deadline,
        )

        _timing_end(
            "cache_diagnosticador",
            _t_cache_diag,
        )

        cache_attempts = list(
            selected_result.get("attempts", [])
        )
    if cache_candidates and selected_result.get("ok"):
        _hfc_priority_remember_success(
            node,
            _hfc_cache_generation,
            selected_result,
        )

    if not selected_result.get("ok") and (deadline - time.perf_counter()) > 25:
        live_cmts_used = True

        _t_cmts = _timing_start()
        cmts_result = _cmts_macs(node, snapshot)
        _timing_end("cmts_macs", _t_cmts)

        cmts_candidates = _valid_macs(cmts_result)
        if cmts_candidates:
            source = "CMTS"
            selected_result = _try_candidates(
                cmts_candidates,
                MAX_CMTS_MAC_ATTEMPTS,
                deadline=deadline,
            )

            cmts_attempts = list(
                selected_result.get("attempts", [])
            )

    # HFC_PATHTRAK_OWN_BUDGET_V1
    if not selected_result.get("ok"):
        fallback_pathtrak = True

        fallback_reason = (
            _clean(cmts_result.get("codigo") or "CMTS_SIN_MACS")
            if not cmts_candidates
            else "CMTS_MACS_SIN_VECINOS"
        )

        _t_pathtrak = _timing_start()
        pathtrak_result = _pathtrak_macs(node)
        _timing_end("pathtrak_macs", _t_pathtrak)

        pathtrak_candidates = _valid_macs(pathtrak_result)

        if not pathtrak_candidates:
            raw = pathtrak_result.get("macs")
            if isinstance(raw, list):
                pathtrak_candidates = [
                    {
                        "mac": _clean(item.get("mac")).upper(),
                        "ip": _clean(item.get("ip")),
                        "upstream": "",
                    }
                    for item in raw
                    if isinstance(item, dict)
                    and re.fullmatch(
                        r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}",
                        _clean(item.get("mac")).upper(),
                    )
                ]

        # HFC_PATHTRAK_DEDUP_CANDIDATOS_V1
        # No volver a diagnosticar MACs que ya fueron probadas
        # previamente desde cache o CMTS.
        attempted_macs = {
            _clean(attempt.get("mac")).upper()
            for attempt in (
                cache_attempts
                + cmts_attempts
            )
            if isinstance(attempt, dict)
            and _clean(attempt.get("mac"))
        }

        pathtrak_candidates_original = len(
            pathtrak_candidates
        )

        pathtrak_candidates = [
            candidate
            for candidate in pathtrak_candidates
            if _clean(
                candidate.get("mac")
            ).upper() not in attempted_macs
        ]

        if pathtrak_candidates:
            source = "PATHTRAK"

            # HFC_PATHTRAK_SECUENCIAL_STOP_PRIMER_EXITO_V1
            selected_result = _try_candidates(
                pathtrak_candidates,
                MAX_PATHTRAK_MAC_ATTEMPTS,
                deadline=None,
            )

            pathtrak_attempts = list(
                selected_result.get("attempts", [])
            )

        elif pathtrak_candidates_original > 0:
            selected_result = {
                "ok": False,
                "codigo": "PATHTRAK_SIN_CANDIDATOS_NUEVOS",
                "vecinos": [],
                "attempts": [],
            }
        else:
            selected_result = {
                "ok": False,
                "codigo": _clean(
                    pathtrak_result.get("codigo")
                    or "PATHTRAK_SIN_MACS"
                ),
                "vecinos": [],
            }

    elif not selected_result.get("ok"):
        selected_result = {
            "ok": False,
            "codigo": "HFC_PRESUPUESTO_AGOTADO",
            "vecinos": [],
            "attempts": selected_result.get("attempts", []),
        }

    if not selected_result.get("ok"):

        # HFC_SEMANTICA_INCONCLUSO_V1
        # No declarar "sin vecinos" cuando realmente la consulta
        # no pudo confirmar el resultado.
        all_attempts = (
            cache_attempts
            + cmts_attempts
            + pathtrak_attempts
        )

        inconclusive_neighbor_states = {
            "VECINOS_MODAL_NO_ABRIO",
            "VECINOS_TIMEOUT",
            "TIMEOUT",
            "ERROR",
            "ERROR_CONSULTA_VECINOS",
        }

        inconclusive_states_found = sorted({
            _clean(attempt.get("estado_vecinos")).upper()
            for attempt in all_attempts
            if isinstance(attempt, dict)
            and _clean(attempt.get("estado_vecinos")).upper()
            in inconclusive_neighbor_states
        })

        inconclusive_codes = sorted({
            _clean(attempt.get("codigo")).upper()
            for attempt in all_attempts
            if isinstance(attempt, dict)
            and (
                "TIMEOUT" in _clean(attempt.get("codigo")).upper()
                or "ERROR" in _clean(attempt.get("codigo")).upper()
            )
        })

        # Un fallo técnico de PathTrak no demuestra ausencia de vecinos.
        pathtrak_code = _clean(pathtrak_result.get("codigo")).upper()
        pathtrak_capture = pathtrak_result.get("capture")
        if not isinstance(pathtrak_capture, dict):
            pathtrak_capture = {}
        pathtrak_error = _clean(
            pathtrak_result.get("error")
            or pathtrak_capture.get("error")
        )
        pathtrak_technical_failure = bool(
            fallback_pathtrak
            and not pathtrak_result.get("ok")
            and pathtrak_code not in {
                "PATHTRAK_EXPORT_SIN_MACS",
                "PATHTRAK_DOM_SIN_MACS",
                "PATHTRAK_SIN_CANDIDATOS_NUEVOS",
            }
        )
        resultado_inconcluso = bool(
            inconclusive_states_found
            or inconclusive_codes
            or pathtrak_technical_failure
        )

        codigo_final_hfc = (
            "HFC_DIRECCIONES_INCONCLUSO"
            if resultado_inconcluso
            else "HFC_DIRECCIONES_SIN_VECINOS"
        )

        return {
            "ok": False,
            "codigo": codigo_final_hfc,
            "wo": original_wo,
            "wo_consultada": consulted_wo,
            "tipo_red": "HFC",
            "nodo": node,
            "fuente_mac": source,
            "cache_used": cache_used,
            "cache_codigo": _clean(cache_result.get("codigo")),
            "cache_total_macs": int(cache_result.get("total_macs") or 0),
            "live_cmts_used": live_cmts_used,
            "fallback_pathtrak": fallback_pathtrak,
            "motivo_fallback": fallback_reason,
            "cmts_codigo": _clean(cmts_result.get("codigo")),
            "diagnosticador_codigo": _clean(selected_result.get("codigo")),
            "pathtrak_codigo": pathtrak_code,
            "pathtrak_error": pathtrak_error[:500],

            "resultado_inconcluso": resultado_inconcluso,
            "motivos_inconclusos": (
                inconclusive_states_found
                + inconclusive_codes
            ),

            "intentos_diagnosticador": (
                cache_attempts
                + cmts_attempts
                + pathtrak_attempts
            ),

            "intentos_cache": cache_attempts,
            "intentos_cmts": cmts_attempts,
            "intentos_pathtrak": pathtrak_attempts,

            "cantidad_intentos_cache": len(cache_attempts),
            "cantidad_intentos_cmts": len(cmts_attempts),
            "cantidad_intentos_pathtrak": len(pathtrak_attempts),

            "presupuesto_total_seg": HFC_TOTAL_BUDGET_SEC,

            "timing_hfc": {
                **timing_hfc,
                "total_hfc": round(
                    time.perf_counter() - started,
                    3,
                ),
            },

            "direcciones": {
                "ok": False,
                "codigo": (
                    "DIRECCIONES_INCONCLUSO"
                    if resultado_inconcluso
                    else "DIRECCIONES_SIN_DATOS"
                ),
                "clientes": [],
            },
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    vecinos = selected_result.get("vecinos")
    if not isinstance(vecinos, list):
        vecinos = []

    candidate = selected_result.get("candidate")
    if not isinstance(candidate, dict):
        candidate = {}

    reference_mac = _clean(
        candidate.get("mac")
        or selected_result.get("mac")
    ).upper()

    return {
        "ok": bool(vecinos),
        "codigo": (
            "HFC_DIRECCIONES_OK"
            if vecinos
            else "HFC_DIRECCIONES_SIN_VECINOS"
        ),
        "tipo_respuesta": "direccion_clientes_hfc",
        "wo": original_wo,
        "wo_consultada": consulted_wo,
        "tipo_red": "HFC",
        "nodo": node,
        "elemento_red": "",
        "gpon_olt": "",
        "id_troncal": "",
        "nombre_comercial": "",
        "serial_referencia": "",
        "mac_referencia": reference_mac,
        "fuente_mac": source,
        "cache_used": cache_used,
        "cache_codigo": _clean(cache_result.get("codigo")),
        "cache_total_macs": int(cache_result.get("total_macs") or 0),
        "cache_generated_at": _clean(cache_result.get("generated_at")),
        "live_cmts_used": live_cmts_used,
        "fallback_pathtrak": fallback_pathtrak,
        "motivo_fallback": fallback_reason,
        "cmts": _clean(snapshot.get("cmts")),
        "ip_cmts": _clean(snapshot.get("ip")),
        "vendor": _clean(snapshot.get("vendor")),
        "intentos_diagnosticador": selected_result.get("attempts", []),
        "presupuesto_total_seg": HFC_TOTAL_BUDGET_SEC,

        "timing_hfc": {
            **timing_hfc,
            "total_hfc": round(
                time.perf_counter() - started,
                3,
            ),
        },

        "direcciones": {
            "ok": bool(vecinos),
            "codigo": "DIRECCIONES_OK" if vecinos else "DIRECCIONES_SIN_DATOS",
            "clientes_encontrados": len(vecinos),
            "clientes": vecinos,
        },
        "duracion_seg": round(time.perf_counter() - started, 2),
    }
