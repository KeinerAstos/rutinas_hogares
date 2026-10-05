from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from app.infrastructure.ssh_jump import (
    SSHJumpSession as _InfrastructureSSHJumpSession,
)
from app.config.ftth_settings import get_ftth_jump_settings


def SSHJumpSession(*args, **kwargs):
    session = _InfrastructureSSHJumpSession(*args, **kwargs)
    settings = get_ftth_jump_settings()

    session.jump_host = settings.host
    session.jump_port = settings.port

    return session

APP_DIR = Path(__file__).resolve().parents[1]
INVENTORY_PATH = APP_DIR / "data" / "vm_ftth_olts.json"

PORT_RE = re.compile(r"^\d+/\d+/\d+$")
ZTE_PORT_RE = re.compile(r"gpon_olt-(\d+/\d+/\d+)", re.I)
GENERIC_PORT_RE = re.compile(r"(?<!\d)(\d+/\d+/\d+)(?!\d)")
IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


class VmFtthError(RuntimeError):
    pass


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _vendor_from_name(name: str) -> str:
    up = _clean(name).upper()
    if up.startswith("HAC-") or "MA5800" in up:
        return "HUAWEI"
    if up.startswith("ZAC-") or "C600" in up or "C650" in up or "ZTC" in up:
        return "ZTE"
    return "AUTO"


def _credentials(vendor: str) -> tuple[str, str]:
    vendor = vendor.upper()
    if vendor == "HUAWEI":
        user = os.getenv("HUAWEI_SSH_USER") or os.getenv("OLT_SSH_USER") or ""
        password = os.getenv("HUAWEI_SSH_PASS") or os.getenv("OLT_SSH_PASS") or ""
    else:
        user = os.getenv("ZTE_SSH_USER") or os.getenv("OLT_SSH_USER") or ""
        password = os.getenv("ZTE_SSH_PASS") or os.getenv("OLT_SSH_PASS") or ""
    if not user or not password:
        raise VmFtthError(f"CREDENCIALES_{vendor}_NO_CONFIGURADAS")
    return user, password


def _load_inventory() -> list[dict[str, Any]]:
    if not INVENTORY_PATH.exists():
        raise VmFtthError("INVENTARIO_OLT_NO_DISPONIBLE")
    import json
    data = json.loads(INVENTORY_PATH.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise VmFtthError("INVENTARIO_OLT_INVALIDO")
    out = []
    for row in data:
        if not isinstance(row, dict):
            continue
        name = _clean(row.get("olt") or row.get("name"))
        ip = _clean(row.get("ip"))
        if name and ip:
            item: dict[str, Any] = {
                "olt": name,
                "ip": ip,
                "vendor": _vendor_from_name(name),
            }

            capabilities = row.get("atlas_capabilities")
            if isinstance(capabilities, dict):
                item["atlas_capabilities"] = dict(capabilities)

            out.append(item)
    return out


def listar_olts_vm_ftth() -> dict[str, Any]:
    rows = _load_inventory()
    return {"ok": True, "count": len(rows), "items": rows}


def _resolve_olt(payload: dict[str, Any]) -> dict[str, Any]:
    name = _clean(payload.get("olt"))
    ip = _clean(payload.get("ip"))
    rows = _load_inventory()

    by_name = {r["olt"].upper(): r for r in rows}
    by_ip = {r["ip"]: r for r in rows}

    row = None
    if name:
        row = by_name.get(name.upper())
    if row is None and ip:
        row = by_ip.get(ip)

    if row is None:
        raise VmFtthError("OLT_NO_EXISTE_EN_INVENTARIO")

    if ip and row["ip"] != ip:
        raise VmFtthError("IP_OLT_NO_COINCIDE_CON_INVENTARIO")

    return row


def _prepare_session(session: SSHJumpSession, vendor: str) -> None:
    if vendor == "ZTE":
        session.execute("terminal length 0", timeout=10, idle_timeout=0.7)
        return

    # Huawei: solo cambia modo CLI para comandos display. No modifica configuración.
    for command in ("enable", "scroll", "config"):
        try:
            session.execute(command, timeout=10, idle_timeout=0.7)
        except Exception:
            pass


def _looks_error(text: str) -> bool:
    up = text.upper()
    tokens = (
        "INVALID INPUT",
        "UNRECOGNIZED COMMAND",
        "FAILURE:",
        "ERROR:",
        "INCOMPLETE COMMAND",
        "TOO MANY PARAMETERS",
        "COMMAND NOT FOUND",
    )
    return any(t in up for t in tokens)


def _extract_ports(text: str, vendor: str) -> list[str]:
    ports = set()
    if vendor == "ZTE":
        ports.update(ZTE_PORT_RE.findall(text or ""))
    ports.update(GENERIC_PORT_RE.findall(text or ""))
    return sorted((p for p in ports if PORT_RE.match(p)), key=_port_sort_key)


def _port_sort_key(port: str) -> tuple[int, int, int]:
    try:
        return tuple(int(x) for x in port.split("/"))
    except Exception:
        return (999, 999, 999)


def _discover_zte(session: SSHJumpSession) -> tuple[list[str], list[dict[str, Any]]]:
    attempts = []
    commands = (
        "show gpon onu state",
        "show interface gpon_olt",
    )
    ports = set()

    for command in commands:
        try:
            raw = session.execute(command, timeout=35, idle_timeout=1.2)
            found = _extract_ports(raw, "ZTE")
            attempts.append({"command": command, "ok": not _looks_error(raw), "ports": len(found)})
            ports.update(found)
            if ports:
                break
        except Exception as exc:
            attempts.append({"command": command, "ok": False, "error": type(exc).__name__})

    return sorted(ports, key=_port_sort_key), attempts


def _discover_huawei(session: SSHJumpSession) -> tuple[list[str], list[dict[str, Any]]]:
    attempts = []
    ports = set()

    # Primera opción: comandos globales que en distintas revisiones MA5800
    # pueden devolver referencias completas frame/slot/port.
    for command in ("display ont info summary 0", "display port desc 0"):
        try:
            raw = session.execute(command, timeout=40, idle_timeout=1.2)
            found = _extract_ports(raw, "HUAWEI")
            attempts.append({"command": command, "ok": not _looks_error(raw), "ports": len(found)})
            ports.update(found)
            if ports:
                return sorted(ports, key=_port_sort_key), attempts
        except Exception as exc:
            attempts.append({"command": command, "ok": False, "error": type(exc).__name__})

    # Fallback seguro: descubrir slots GPON desde display board 0 y consultar
    # cada slot. Si el equipo reporta puertos válidos, los extraemos.
    try:
        board = session.execute("display board 0", timeout=30, idle_timeout=1.0)
        attempts.append({"command": "display board 0", "ok": not _looks_error(board)})
    except Exception as exc:
        attempts.append({"command": "display board 0", "ok": False, "error": type(exc).__name__})
        board = ""

    slots = set()
    for line in board.splitlines():
        up = line.upper()
        if "GP" not in up:
            continue
        m = re.match(r"\s*(\d+)\s+", line)
        if m:
            slots.add(int(m.group(1)))

    for slot in sorted(slots):
        command = f"display ont info summary 0/{slot}"
        try:
            raw = session.execute(command, timeout=40, idle_timeout=1.2)
            found = _extract_ports(raw, "HUAWEI")
            attempts.append({"command": command, "ok": not _looks_error(raw), "ports": len(found)})
            ports.update(found)

            # Algunas salidas listan solo el número de puerto dentro del slot.
            if not found and not _looks_error(raw):
                local_ports = set()
                for line in raw.splitlines():
                    m = re.match(r"\s*(\d+)\s+", line)
                    if m:
                        n = int(m.group(1))
                        if 0 <= n <= 31:
                            local_ports.add(n)
                for n in local_ports:
                    ports.add(f"0/{slot}/{n}")
        except Exception as exc:
            attempts.append({"command": command, "ok": False, "error": type(exc).__name__})

    return sorted(ports, key=_port_sort_key), attempts



def _run_with_session_retry(
    ip: str,
    user: str,
    password: str,
    vendor: str,
    callback,
    attempts: int = 3,
):
    last_exc: Exception | None = None
    delays = (0.0, 1.5, 3.0)

    for idx in range(attempts):
        if idx:
            time.sleep(delays[min(idx, len(delays) - 1)])

        try:
            with SSHJumpSession(
                ip,
                user,
                password,
                connect_timeout=25,
            ) as session:
                _prepare_session(session, vendor)
                return callback(session), idx + 1
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()

            retryable = any(
                token in msg
                for token in (
                    "timeout opening channel",
                    "timed out",
                    "timeout",
                    "channel",
                    "banner",
                    "connection reset",
                    "connection aborted",
                )
            )

            if not retryable or idx >= attempts - 1:
                raise

    if last_exc is not None:
        raise last_exc
    raise VmFtthError("SSH_JUMP_RETRY_SIN_RESULTADO")


def descubrir_puertos_vm_ftth(payload: dict[str, Any]) -> dict[str, Any]:
    olt = _resolve_olt(payload)
    vendor = _clean(payload.get("vendor")).upper() or olt["vendor"]
    if vendor == "AUTO":
        vendor = olt["vendor"]
    if vendor not in {"ZTE", "HUAWEI"}:
        raise VmFtthError("FABRICANTE_NO_IDENTIFICADO")

    user, password = _credentials(vendor)
    t0 = time.monotonic()

    def _do(session: SSHJumpSession):
        if vendor == "ZTE":
            return _discover_zte(session)
        return _discover_huawei(session)

    (ports, command_attempts), jump_attempts = _run_with_session_retry(
        olt["ip"],
        user,
        password,
        vendor,
        _do,
        attempts=3,
    )

    return {
        "ok": True,
        "olt": olt["olt"],
        "ip": olt["ip"],
        "vendor": vendor,
        "ports": ports,
        "count": len(ports),
        "attempts": command_attempts,
        "jump_attempts": jump_attempts,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


def _parse_description(text: str) -> str:
    for line in (text or "").splitlines():
        if re.search(r"description", line, re.I):
            if ":" in line:
                value = line.split(":", 1)[1].strip()
                if value:
                    return value
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) > 1:
                return parts[-1].strip()
    return ""


def _parse_zte_state(text: str) -> dict[str, int]:
    counts = {"working": 0, "dyinggasp": 0, "los": 0, "other": 0}
    rows = 0
    for line in (text or "").splitlines():
        up = line.upper()
        if "GPON_ONU-" not in up and "GPON-ONU_" not in up and not re.search(r"\d+/\d+/\d+:\d+", line):
            continue
        if "WORKING" in up:
            counts["working"] += 1
        elif "DYINGGASP" in up:
            counts["dyinggasp"] += 1
        elif re.search(r"\bLOS\b", up):
            counts["los"] += 1
        else:
            counts["other"] += 1
        rows += 1
    counts["total"] = rows
    return counts


def _parse_zte_power(text: str) -> dict[str, Any]:
    values = []
    for line in (text or "").splitlines():
        if not re.search(r"gpon_onu|gpon-onu|dbm", line, re.I):
            continue
        nums = re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:dBm)?", line, re.I)
        negatives = []
        for raw in nums:
            try:
                value = float(raw)
            except Exception:
                continue
            if -60.0 <= value <= 5.0:
                negatives.append(value)
        if negatives:
            values.append(negatives[-1])

    return {
        "samples": len(values),
        "attenuated": sum(1 for v in values if v <= -27.0),
        "min_dbm": min(values) if values else None,
        "max_dbm": max(values) if values else None,
        "avg_dbm": round(sum(values) / len(values), 2) if values else None,
    }


def _parse_huawei_summary(text: str) -> dict[str, Any]:
    online = offline = 0
    for line in (text or "").splitlines():
        up = line.upper()
        if re.search(r"\bONLINE\b", up):
            online += 1
        elif re.search(r"\bOFFLINE\b", up):
            offline += 1
    total = online + offline
    return {"total": total, "working": online, "offline": offline}


def _health_from_counts(vendor: str, state: dict[str, Any], power: dict[str, Any]) -> str:
    total = int(state.get("total") or 0)
    working = int(state.get("working") or 0)
    attenuated = int(power.get("attenuated") or 0)

    if total <= 0:
        return "SIN_DATOS"

    pct = (working / total) * 100.0
    if pct <= 10:
        return "CAIDA"
    if pct < 85 or attenuated > 0:
        return "ATENUADA"
    return "OPERATIVA"


def _query_zte_port(session: SSHJumpSession, port: str, checks: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "port": port,
        "description": "",
        "state": {},
        "power": {},
        "status": "SIN_DATOS",
        "errors": [],
    }

    if "trunk" in checks:
        try:
            raw = session.execute(
                f"show interface gpon_olt-{port} | include Description",
                timeout=20,
                idle_timeout=0.8,
            )
            result["description"] = _parse_description(raw)
        except Exception as exc:
            result["errors"].append(f"TRUNK:{type(exc).__name__}")

    if "power" in checks:
        try:
            raw = session.execute(
                f"show pon power onu-rx gpon_olt-{port}",
                timeout=30,
                idle_timeout=1.0,
            )
            result["power"] = _parse_zte_power(raw)
        except Exception as exc:
            result["errors"].append(f"POWER:{type(exc).__name__}")

    if "state" in checks:
        try:
            raw = session.execute(
                f"show gpon onu state gpon_olt-{port} | include working|DyingGasp|LOS",
                timeout=30,
                idle_timeout=1.0,
            )
            result["state"] = _parse_zte_state(raw)
        except Exception as exc:
            result["errors"].append(f"STATE:{type(exc).__name__}")

    result["status"] = _health_from_counts("ZTE", result["state"], result["power"])
    return result


def _query_huawei_port(session: SSHJumpSession, port: str, checks: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "port": port,
        "description": "",
        "state": {},
        "power": {},
        "status": "SIN_DATOS",
        "errors": [],
    }

    summary_raw = ""
    if "state" in checks or "power" in checks:
        try:
            summary_raw = session.execute(
                f"display ont info summary {port}",
                timeout=35,
                idle_timeout=1.0,
            )
            result["state"] = _parse_huawei_summary(summary_raw)
            # El aplicativo original usa este mismo comando tanto para Estado
            # como para Potencias. Conservamos esa semántica y no inventamos
            # un segundo comando no validado.
            if "power" in checks:
                result["power"] = {"source": "display ont info summary", "samples": None}
        except Exception as exc:
            result["errors"].append(f"SUMMARY:{type(exc).__name__}")

    if "trunk" in checks:
        try:
            raw = session.execute(
                f"display port desc {port}",
                timeout=25,
                idle_timeout=0.9,
            )
            result["description"] = _parse_description(raw)
        except Exception as exc:
            result["errors"].append(f"TRUNK:{type(exc).__name__}")

    result["status"] = _health_from_counts("HUAWEI", result["state"], result["power"])
    return result


def diagnosticar_vm_ftth(payload: dict[str, Any]) -> dict[str, Any]:
    olt = _resolve_olt(payload)
    vendor = _clean(payload.get("vendor")).upper() or olt["vendor"]
    if vendor == "AUTO":
        vendor = olt["vendor"]
    if vendor not in {"ZTE", "HUAWEI"}:
        raise VmFtthError("FABRICANTE_NO_IDENTIFICADO")

    mode = (_clean(payload.get("mode")) or "specific").lower()
    if mode not in {"specific", "all", "active"}:
        raise VmFtthError("MODO_INVALIDO")

    checks_raw = payload.get("checks") or ["state", "power", "trunk"]
    checks = {_clean(x).lower() for x in checks_raw if _clean(x)}
    checks &= {"state", "power", "trunk"}
    if not checks:
        raise VmFtthError("SIN_VALIDACIONES")

    requested_ports = []
    for item in payload.get("ports") or []:
        p = _clean(item)
        if p and PORT_RE.match(p):
            requested_ports.append(p)
    requested_ports = sorted(set(requested_ports), key=_port_sort_key)

    user, password = _credentials(vendor)
    t0 = time.monotonic()

    def _do_diag(session: SSHJumpSession):
        discovery = []

        if mode == "specific":
            ports = requested_ports
            if not ports:
                raise VmFtthError("SIN_PUERTOS_ESPECIFICOS")
        else:
            if vendor == "ZTE":
                ports, discovery = _discover_zte(session)
            else:
                ports, discovery = _discover_huawei(session)
            if not ports:
                raise VmFtthError("NO_SE_PUDIERON_DESCUBRIR_PUERTOS")

        max_ports = 256
        if len(ports) > max_ports:
            raise VmFtthError(f"DEMASIADOS_PUERTOS:{len(ports)}")

        results = []
        for port in ports:
            if vendor == "ZTE":
                row = _query_zte_port(session, port, checks)
            else:
                row = _query_huawei_port(session, port, checks)

            if mode == "active":
                state = row.get("state") or {}
                if int(state.get("working") or 0) <= 0:
                    continue
            results.append(row)

        return ports, discovery, results

    (ports, discovery, results), jump_attempts = _run_with_session_retry(
        olt["ip"],
        user,
        password,
        vendor,
        _do_diag,
        attempts=3,
    )

    totals = {
        "ports": len(results),
        "onus": sum(int((r.get("state") or {}).get("total") or 0) for r in results),
        "working": sum(int((r.get("state") or {}).get("working") or 0) for r in results),
        "dyinggasp": sum(int((r.get("state") or {}).get("dyinggasp") or 0) for r in results),
        "los": sum(int((r.get("state") or {}).get("los") or 0) for r in results),
        "attenuated": sum(int((r.get("power") or {}).get("attenuated") or 0) for r in results),
        "operativa": sum(1 for r in results if r.get("status") == "OPERATIVA"),
        "atenuada": sum(1 for r in results if r.get("status") == "ATENUADA"),
        "caida": sum(1 for r in results if r.get("status") == "CAIDA"),
    }

    return {
        "ok": True,
        "olt": olt["olt"],
        "ip": olt["ip"],
        "vendor": vendor,
        "mode": mode,
        "checks": sorted(checks),
        "discovery": discovery,
        "totals": totals,
        "results": results,
        "jump_attempts": jump_attempts,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }
_vm_ftth_base_v84 = diagnosticar_vm_ftth


def health_vm_ftth() -> dict[str, Any]:
    try:
        rows = _load_inventory()
        inventory_ok = len(rows) == 696
    except Exception:
        rows = []
        inventory_ok = False

    return {
        "ok": inventory_ok,
        "inventory_count": len(rows),
        "ssh_jump": True,
        "zte_credentials": bool((os.getenv("ZTE_SSH_USER") or os.getenv("OLT_SSH_USER")) and (os.getenv("ZTE_SSH_PASS") or os.getenv("OLT_SSH_PASS"))),
        "huawei_credentials": bool((os.getenv("HUAWEI_SSH_USER") or os.getenv("OLT_SSH_USER")) and (os.getenv("HUAWEI_SSH_PASS") or os.getenv("OLT_SSH_PASS"))),
        "flow": "ATLAS -> backend_hogares -> noc_cable -> OLT",
    }

# VM_FTTH_FAST_CACHE_V2_7_START
import copy as _v7_copy
import re as _v7_re
import threading as _v7_threading
import time as _v7_time
from collections import defaultdict as _v7_defaultdict

_V7_CACHE_TTL = 15.0
_V7_CACHE = {}
_V7_INFLIGHT = {}
_V7_LOCK = _v7_threading.RLock()

_V7_ONU_RE = _v7_re.compile(
    r"^\s*(\d+/\d+/\d+):(\d+)\s+.*?\b(working|DyingGasp|LOS)\b",
    _v7_re.I,
)

def _v7_port_key(port):
    try:
        return tuple(int(x) for x in port.split("/"))
    except Exception:
        return (999,999,999)

def _v7_status(state):
    total = int(state.get("total") or 0)
    working = int(state.get("working") or 0)
    dying = int(state.get("dyinggasp") or 0)
    los = int(state.get("los") or 0)

    if total <= 0:
        return "SIN_DATOS"

    pct = (working / total) * 100.0

    if pct <= 5.0:
        return "CAIDA"

    if los > 0 or dying > 0 or pct < 85.0:
        return "ATENUADA"

    return "OPERATIVA"

def _v7_last_nonempty(raw):
    for line in reversed((raw or "").splitlines()):
        line = line.strip()
        if line:
            return line
    return ""

def _v7_parse(raw, mode):
    grouped = _v7_defaultdict(lambda: {
        "total": 0,
        "working": 0,
        "dyinggasp": 0,
        "los": 0,
    })

    states = 0

    for line in (raw or "").splitlines():
        m = _V7_ONU_RE.search(line)
        if not m:
            continue

        port = m.group(1)
        phase = m.group(3).lower()

        row = grouped[port]
        row["total"] += 1
        states += 1

        if phase == "working":
            row["working"] += 1
        elif phase == "dyinggasp":
            row["dyinggasp"] += 1
        elif phase == "los":
            row["los"] += 1

    results = []

    for port in sorted(grouped.keys(), key=_v7_port_key):
        st = dict(grouped[port])

        if mode == "active" and int(st.get("working") or 0) <= 0:
            continue

        results.append({
            "port": port,
            "description": "",
            "state": st,
            "power": {},
            "status": _v7_status(st),
            "errors": [],
            "fast_summary": True,
        })

    totals = {
        "ports": len(results),
        "onus": sum(int(r["state"]["total"]) for r in results),
        "working": sum(int(r["state"]["working"]) for r in results),
        "dyinggasp": sum(int(r["state"]["dyinggasp"]) for r in results),
        "los": sum(int(r["state"]["los"]) for r in results),
        "attenuated": 0,
        "operativa": sum(1 for r in results if r["status"] == "OPERATIVA"),
        "atenuada": sum(1 for r in results if r["status"] == "ATENUADA"),
        "caida": sum(1 for r in results if r["status"] == "CAIDA"),
    }

    return results, totals, states

def _v7_scan_uncached(olt, mode):
    started = _v7_time.monotonic()

    user, password = _credentials("ZTE")

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=7,
    ) as session:

        try:
            session.execute(
                "terminal length 0",
                timeout=4,
                idle_timeout=0.35,
            )
        except Exception:
            pass

        raw = session.execute(
            "show gpon onu state | include working|DyingGasp|LOS",
            timeout=30,
            idle_timeout=0.35,
        )

    elapsed = _v7_time.monotonic() - started
    last = _v7_last_nonempty(raw)
    prompt_ok = bool(last.endswith("#"))

    results, totals, states = _v7_parse(raw, mode)

    if not prompt_ok:
        raise VmFtthError(
            f"VM_FTTH_RESPUESTA_INCOMPLETA:SIN_PROMPT_FINAL:"
            f"ESTADOS={states}:ELAPSED={round(elapsed,2)}"
        )

    if states <= 0:
        raise VmFtthError("VM_FTTH_SIN_ESTADOS_VALIDOS")

    return {
        "ok": True,
        "fast": True,
        "cache_hit": False,
        "cache_age_s": 0.0,
        "cache_ttl_s": _V7_CACHE_TTL,
        "deduplicated": False,
        "prewarmed": False,
        "complete": True,
        "prompt_ok": True,
        "olt": olt["olt"],
        "ip": olt["ip"],
        "vendor": "ZTE",
        "mode": mode,
        "checks": ["state"],
        "detail_deferred": ["power", "trunk"],
        "elapsed_s": round(elapsed, 2),
        "states_received": states,
        "totals": totals,
        "results": results,
    }

def _v7_key(olt, mode):
    return f'{olt["ip"]}|{mode}'

def _v7_cached_copy(entry, age):
    result = _v7_copy.deepcopy(entry["result"])
    result["cache_hit"] = True
    result["cache_age_s"] = round(age, 2)
    result["elapsed_s"] = 0.0
    return result

def _v7_get_valid_cache(key):
    now = _v7_time.monotonic()

    with _V7_LOCK:
        entry = _V7_CACHE.get(key)

        if not entry:
            return None

        age = now - entry["ts"]

        if age > _V7_CACHE_TTL:
            _V7_CACHE.pop(key, None)
            return None

        return _v7_cached_copy(entry, age)

def _v7_fast_cached(olt, mode):
    key = _v7_key(olt, mode)

    cached = _v7_get_valid_cache(key)
    if cached is not None:
        return cached

    with _V7_LOCK:
        event = _V7_INFLIGHT.get(key)

        if event is None:
            event = _v7_threading.Event()
            _V7_INFLIGHT[key] = event
            owner = True
        else:
            owner = False

    if not owner:
        wait_start = _v7_time.monotonic()

        event.wait(timeout=35)

        cached = _v7_get_valid_cache(key)

        if cached is not None:
            cached["deduplicated"] = True
            cached["waited_for_existing_s"] = round(
                _v7_time.monotonic() - wait_start,
                2,
            )
            return cached

        raise VmFtthError("VM_FTTH_DEDUP_SIN_RESULTADO")

    try:
        result = _v7_scan_uncached(olt, mode)

        with _V7_LOCK:
            _V7_CACHE[key] = {
                "ts": _v7_time.monotonic(),
                "result": _v7_copy.deepcopy(result),
            }

        return result

    finally:
        with _V7_LOCK:
            current = _V7_INFLIGHT.pop(key, None)

        if current is not None:
            current.set()

def precalentar_vm_ftth(payload):
    olt = _resolve_olt(payload)

    vendor = (
        _clean(payload.get("vendor")).upper()
        or olt["vendor"]
    )

    if vendor == "AUTO":
        vendor = olt["vendor"]

    if vendor != "ZTE":
        return {
            "ok": True,
            "started": False,
            "reason": "PREWARM_SOLO_ZTE_POR_AHORA",
            "olt": olt["olt"],
            "vendor": vendor,
        }

    mode = (
        _clean(payload.get("mode"))
        or "all"
    ).lower()

    if mode not in {"all","active"}:
        mode = "all"

    key = _v7_key(olt, mode)

    cached = _v7_get_valid_cache(key)

    if cached is not None:
        return {
            "ok": True,
            "started": False,
            "cache_hit": True,
            "cache_age_s": cached.get("cache_age_s", 0.0),
            "olt": olt["olt"],
            "vendor": vendor,
        }

    with _V7_LOCK:
        already = key in _V7_INFLIGHT

    if already:
        return {
            "ok": True,
            "started": False,
            "already_running": True,
            "olt": olt["olt"],
            "vendor": vendor,
        }

    def worker():
        try:
            _v7_fast_cached(olt, mode)
        except Exception:
            pass

    thread = _v7_threading.Thread(
        target=worker,
        daemon=True,
        name=f"vm-ftth-prewarm-{olt['ip']}",
    )

    thread.start()

    return {
        "ok": True,
        "started": True,
        "olt": olt["olt"],
        "vendor": vendor,
        "mode": mode,
        "cache_ttl_s": _V7_CACHE_TTL,
    }

_vm_ftth_deep_v7 = diagnosticar_vm_ftth

def diagnosticar_vm_ftth(payload: dict[str, Any]) -> dict[str, Any]:
    olt = _resolve_olt(payload)

    vendor = (
        _clean(payload.get("vendor")).upper()
        or olt["vendor"]
    )

    if vendor == "AUTO":
        vendor = olt["vendor"]

    mode = (
        _clean(payload.get("mode"))
        or "specific"
    ).lower()

    fast = bool(payload.get("fast", True))

    if fast and vendor == "ZTE" and mode in {"all","active"}:
        return _v7_fast_cached(olt, mode)

    return _vm_ftth_deep_v7(payload)

def estado_cache_vm_ftth():
    now = _v7_time.monotonic()

    with _V7_LOCK:
        cache = {
            key: {
                "age_s": round(now - value["ts"], 2),
                "valid": (now - value["ts"]) <= _V7_CACHE_TTL,
            }
            for key,value in _V7_CACHE.items()
        }

        inflight = list(_V7_INFLIGHT.keys())

    return {
        "ok": True,
        "ttl_s": _V7_CACHE_TTL,
        "cache": cache,
        "inflight": inflight,
    }
# VM_FTTH_FAST_CACHE_V2_7_END

# VM_FTTH_HUAWEI_FAST_V4_1_START
_VM_FTTH_SSHJUMP_BASE_V41 = SSHJumpSession

class _VmFtthHuaweiFastSSHJumpSessionV41:
    def __init__(self, *args, **kwargs):
        self._inner = _VM_FTTH_SSHJUMP_BASE_V41(*args, **kwargs)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._inner.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @staticmethod
    def _needs_huawei_cr(text):
        s=str(text or '').lower()
        return ('{ <cr>' in s or '||<k> }:' in s or '{<cr>' in s)

    @staticmethod
    def _has_more(text):
        s=str(text or '').lower()
        return ('---- more' in s and 'press' in s and 'q' in s)

    def _find_raw_channel(self):
        preferred=('shell','_shell','_channel','channel','chan','_chan')
        for name in preferred:
            try:
                obj=getattr(self._inner,name,None)
            except Exception:
                obj=None
            if obj is not None and all(hasattr(obj,m) for m in ('send','recv_ready','recv')):
                return obj
        try:
            values=list(vars(self._inner).values())
        except Exception:
            values=[]
        for obj in values:
            if obj is not None and all(hasattr(obj,m) for m in ('send','recv_ready','recv')):
                return obj
        return None

    @staticmethod
    def _clean_more_markers(text):
        import re as _re
        s=str(text or '')
        s=_re.sub(
            r"----\s*More\s*\(\s*Press\s*'Q'\s*to\s*break\s*\)\s*----",
            '',
            s,
            flags=_re.I,
        )
        return s

    def _read_after_space(self, channel, timeout, idle_timeout):
        import time as _time
        parts=[]
        started=_time.monotonic()
        last_data=started
        hard=min(max(float(timeout),3.0),12.0)
        idle=max(min(float(idle_timeout),1.2),0.35)

        while (_time.monotonic()-started) < hard:
            try:
                ready=channel.recv_ready()
            except Exception:
                ready=False

            if ready:
                data=channel.recv(65535)
                if isinstance(data,bytes):
                    chunk=data.decode('utf-8','replace')
                else:
                    chunk=str(data)
                if chunk:
                    parts.append(chunk)
                    last_data=_time.monotonic()
                    joined=''.join(parts)
                    if self._has_more(joined):
                        break
                continue

            if parts and (_time.monotonic()-last_data) >= idle:
                break

            _time.sleep(0.03)

        return ''.join(parts)

    def _continue_more_pages(self, text, timeout, idle_timeout):
        if not self._has_more(text):
            return text

        channel=self._find_raw_channel()
        if channel is None:
            raise RuntimeError('VM_FTTH_HUAWEI_RAW_CHANNEL_NOT_FOUND')

        output=str(text or '')

        for _page in range(1,65):
            if not self._has_more(output[-500:]):
                break

            # SmartAX: SPACE continua una pagina; ENTER solo una linea.
            channel.send(' ')
            chunk=self._read_after_space(channel,timeout,idle_timeout)
            output += chunk

            if not chunk:
                break

        return self._clean_more_markers(output)

    def execute(self, command, timeout=35, idle_timeout=1.0):
        first=self._inner.execute(
            command,
            timeout=timeout,
            idle_timeout=idle_timeout,
        )

        combined=first

        if self._needs_huawei_cr(first):
            second=self._inner.execute(
                '',
                timeout=timeout,
                idle_timeout=max(float(idle_timeout),1.0),
            )
            combined=str(first or '')+'\n'+str(second or '')

        if self._has_more(combined):
            combined=self._continue_more_pages(
                combined,
                timeout,
                idle_timeout,
            )

        return combined

    def enable(self, secret=''):
        return self._inner.enable(secret)

SSHJumpSession = _VmFtthHuaweiFastSSHJumpSessionV41
# VM_FTTH_HUAWEI_FAST_V4_1_END

# VM_FTTH_HUAWEI_DISCOVERY_V4_3_START
import re as _vm_re_v43

_VM_HUAWEI_SLOT_SUMMARY_V43 = True
_VM_HUAWEI_PORT_SUMMARY_V43 = True

def _vm_huawei_service_slots_v43(board_text):
    slots=[]
    for line in str(board_text or '').splitlines():
        parts=line.split()
        if len(parts)<3 or not parts[0].isdigit():
            continue
        board=parts[1].upper()
        if any(x in board for x in ('GPHF','GPFD','GPBD','GPBH','FLHF')):
            slots.append((int(parts[0]),board))
    return slots

def _vm_huawei_ports_from_board_v43(slot, text):
    ports=[]
    in_port_table=False
    for line in str(text or '').splitlines():
        low=line.lower()
        if 'port' in low and 'type' in low and ('optical' in low or 'status' in low):
            in_port_table=True
            continue
        if not in_port_table:
            continue
        parts=line.split()
        if len(parts)>=2 and parts[0].isdigit() and parts[1].upper() in ('GPON','XGPON','XGSPON'):
            ports.append(f'0/{int(slot)}/{int(parts[0])}')
    return ports

def _vm_huawei_discover_ports_v43(session):
    board_all=session.execute('display board 0',timeout=20,idle_timeout=0.8)
    slots=_vm_huawei_service_slots_v43(board_all)
    ports=[]
    boards={}

    for slot,board_name in slots:
        detail=session.execute(
            f'display board 0/{slot}',
            timeout=20,
            idle_timeout=0.8,
        )
        boards[str(slot)]={'board':board_name,'raw':detail}
        ports.extend(_vm_huawei_ports_from_board_v43(slot,detail))

    dedup=[]
    seen=set()
    for port in ports:
        if port not in seen:
            seen.add(port)
            dedup.append(port)

    return {
        'slots':[x[0] for x in slots],
        'boards':boards,
        'ports':dedup,
        'board_raw':board_all,
    }

def _vm_huawei_summary_command_v43(port=None,slot=None):
    if slot is not None and _VM_HUAWEI_SLOT_SUMMARY_V43:
        return f'display ont info summary 0/{int(slot)}'
    if port is not None and _VM_HUAWEI_PORT_SUMMARY_V43:
        return f'display ont info summary {port}'
    return None
# VM_FTTH_HUAWEI_DISCOVERY_V4_3_END

# VM_FTTH_HUAWEI_CONTRACT_V4_7_START
import time as _vm_time_v47


# HUAWEI_RAW_CHANNEL_SHELL_FIRST_V1
# En SSHJumpSession:
#   channel = tunel direct-tcpip hacia la OLT
#   shell   = CLI interactiva real de Huawei
# Los helpers RAW/paginador deben priorizar shell.
_VM_HUAWEI_COMMAND_PATH_V47 = 'EXECUTE'

def _vm_huawei_raw_channel_v47(session):
    fn=getattr(session,'_find_raw_channel',None)
    if callable(fn):
        ch=fn()
        if ch is not None:
            return ch
    target=getattr(session,'_inner',session)
    for name in ('shell','_shell','_channel','channel','chan','_chan'):
        obj=getattr(target,name,None)
        if obj is not None and all(hasattr(obj,m) for m in ('send','recv_ready','recv')):
            return obj
    for obj in list(vars(target).values()):
        if obj is not None and all(hasattr(obj,m) for m in ('send','recv_ready','recv')):
            return obj
    raise RuntimeError('HUAWEI_RAW_CHANNEL_NOT_FOUND')

def _vm_huawei_raw_exec_v47(session,command,hard=14,idle=0.8):
    ch=_vm_huawei_raw_channel_v47(session)
    ch.send(str(command)+'\r')
    parts=[]
    start=_vm_time_v47.monotonic()
    last=start
    while _vm_time_v47.monotonic()-start < float(hard):
        if ch.recv_ready():
            data=ch.recv(65535)
            txt=data.decode('utf-8','replace') if isinstance(data,bytes) else str(data)
            if txt:
                parts.append(txt)
                last=_vm_time_v47.monotonic()
            continue
        if parts and _vm_time_v47.monotonic()-last >= float(idle):
            break
        _vm_time_v47.sleep(0.03)
    return ''.join(parts)

def _vm_huawei_exec_v47(session,command,timeout=16,idle_timeout=0.8):
    if _VM_HUAWEI_COMMAND_PATH_V47 == 'RAW':
        return _vm_huawei_raw_exec_v47(
            session,command,hard=timeout,idle=idle_timeout
        )
    return session.execute(
        command,timeout=timeout,idle_timeout=idle_timeout
    )

def _vm_huawei_enter_gpon_v47(session,slot):
    enable=_vm_huawei_exec_v47(session,'enable',timeout=10,idle_timeout=0.8)
    if '#' not in str(enable or ''):
        raise RuntimeError('HUAWEI_ENABLE_FAIL')

    # VM_FTTH_HUAWEI_SCROLL_FAST_PON_V1
    scroll=_vm_huawei_exec_v47(
        session,'scroll',timeout=10,idle_timeout=0.8
    )
    scroll_low=str(scroll or '').lower()
    if 'unknown command' in scroll_low or 'error locates at' in scroll_low:
        raise RuntimeError('HUAWEI_SCROLL_FAIL')

    cfg=_vm_huawei_exec_v47(session,'config',timeout=16,idle_timeout=0.8)
    low=str(cfg or '').lower()
    if 'unknown command' in low or 'error locates at' in low:
        raise RuntimeError('HUAWEI_CONFIG_FAIL')
    inter=_vm_huawei_exec_v47(
        session,f'interface gpon 0/{int(slot)}',timeout=16,idle_timeout=0.8
    )
    low=str(inter or '').lower()
    if 'unknown command' in low or 'error locates at' in low:
        raise RuntimeError('HUAWEI_INTERFACE_GPON_FAIL')
    return inter

def _vm_huawei_query_pon_v47(session,slot,pon):
    _vm_huawei_enter_gpon_v47(session,slot)
    return _vm_huawei_exec_v47(
        session,
        f'display ont info {int(pon)} all',
        timeout=24,
        idle_timeout=0.9,
    )
# VM_FTTH_HUAWEI_CONTRACT_V4_7_END

# VM_FTTH_HUAWEI_FAST_ENGINE_V4_8_START
import re as _vh_re_v48
import time as _vh_time_v48
import threading as _vh_threading_v48

_VM_HUAWEI_CACHE_TTL_V48=30.0
_VM_HUAWEI_CACHE_V48={}
_VM_HUAWEI_CACHE_LOCK_V48=_vh_threading_v48.RLock()
_VM_HUAWEI_BOARD_TOKENS_V48=("GPHF","GPFD","GPBD","GPBH","FLHF")

def _vh_clean_more_v48(text):
    return _vh_re_v48.sub(
        r"----\s*More\s*\(\s*Press\s*'Q'\s*to\s*break\s*\)\s*----",
        "",
        str(text or ""),
        flags=_vh_re_v48.I,
    )

def _vh_raw_channel_v48(session):
    fn=getattr(session,"_find_raw_channel",None)
    if callable(fn):
        try:
            ch=fn()
        except Exception:
            ch=None
        if ch is not None:
            return ch

    target=getattr(session,"_inner",session)

    for name in ("shell","_shell","_channel","channel","chan","_chan"):
        try:
            obj=getattr(target,name,None)
        except Exception:
            obj=None
        if obj is not None and all(hasattr(obj,m) for m in ("send","recv_ready","recv")):
            return obj

    try:
        values=list(vars(target).values())
    except Exception:
        values=[]

    for obj in values:
        if obj is not None and all(hasattr(obj,m) for m in ("send","recv_ready","recv")):
            return obj

    raise RuntimeError("HUAWEI_RAW_CHANNEL_NOT_FOUND")

def _vh_raw_exec_v48(
    session,
    command,
    hard=12.0,
    idle=0.30,
    confirm_cr=True,
):
    ch=_vh_raw_channel_v48(session)

    # Vaciar solo residuos que ya estaban listos ANTES del comando.
    try:
        for _ in range(12):
            if not ch.recv_ready():
                break
            ch.recv(65535)
    except Exception:
        pass

    # Huawei: enviar UN CR inicialmente.
    ch.send(str(command)+"\r")

    parts=[]
    started=_vh_time_v48.monotonic()
    last_data=started
    confirm_sent=False
    more_count=0
    last_more_signature=None

    while (
        _vh_time_v48.monotonic()-started
        < float(hard)
    ):
        try:
            ready=ch.recv_ready()
        except Exception as exc:
            raise OSError(
                "HUAWEI_RAW_RECV_READY_FAIL:"
                +type(exc).__name__
                +":"
                +str(exc)
            ) from exc

        if ready:
            try:
                data=ch.recv(65535)
            except Exception as exc:
                raise OSError(
                    "HUAWEI_RAW_RECV_FAIL:"
                    +type(exc).__name__
                    +":"
                    +str(exc)
                ) from exc

            txt=(
                data.decode("utf-8","replace")
                if isinstance(data,bytes)
                else str(data)
            )

            if txt:
                parts.append(txt)
                last_data=_vh_time_v48.monotonic()

                joined="".join(parts)
                tail=joined[-800:]
                low_tail=tail.lower()

                # CR adicional SOLO cuando Huawei lo solicita.
                if (
                    confirm_cr
                    and not confirm_sent
                    and (
                        "{ <cr>" in low_tail
                        or "||<k> }:" in low_tail
                        or "{<cr>" in low_tail
                    )
                ):
                    try:
                        ch.send("\r")
                    except Exception as exc:
                        raise OSError(
                            "HUAWEI_RAW_CONFIRM_SEND_FAIL:"
                            +type(exc).__name__
                            +":"
                            +str(exc)
                        ) from exc
                    confirm_sent=True
                    continue

                # Paginador SmartAX: SPACE, no ENTER.
                if (
                    "---- more" in low_tail
                    and "press 'q' to break" in low_tail
                ):
                    signature=tail[-180:]

                    if signature!=last_more_signature:
                        try:
                            ch.send(" ")
                        except Exception as exc:
                            raise OSError(
                                "HUAWEI_RAW_MORE_SEND_FAIL:"
                                +type(exc).__name__
                                +":"
                                +str(exc)
                            ) from exc

                        last_more_signature=signature
                        more_count+=1

                        if more_count>80:
                            raise RuntimeError(
                                "HUAWEI_RAW_TOO_MANY_PAGES"
                            )

                    continue

            continue

        if (
            parts
            and _vh_time_v48.monotonic()-last_data
            >= float(idle)
        ):
            joined="".join(parts)
            tail=joined[-500:].rstrip()

            # Finalizar al detectar prompt Huawei real.
            if _vh_re_v48.search(
                r"(?:\([^)]+\))?[>#]\s*$",
                tail,
            ):
                break

            # Si hubo datos pero no prompt, dar un margen extra.
            if (
                _vh_time_v48.monotonic()-last_data
                >= max(float(idle)*3.0,0.90)
            ):
                break

        _vh_time_v48.sleep(0.02)

    return _vh_clean_more_v48(
        "".join(parts)
    )


def _vh_invalid_v48(text):
    low=str(text or "").lower()
    return any(token in low for token in (
        "unknown command",
        "too many parameters",
        "error locates at",
        "incomplete command",
    ))

def _vh_parse_boards_v48(text):
    boards=[]
    for line in str(text or "").splitlines():
        parts=line.split()
        if len(parts)<3 or not parts[0].isdigit():
            continue
        slot=int(parts[0])
        model=parts[1].upper()
        if any(token in model for token in _VM_HUAWEI_BOARD_TOKENS_V48):
            boards.append({"slot":slot,"model":model})
    return boards

def _vh_parse_board_ports_v48(text):
    indexes=[]
    in_table=False

    for line in str(text or "").splitlines():
        low=line.lower()

        if "port" in low and "type" in low:
            in_table=True
            continue

        if not in_table:
            continue

        parts=line.split()

        if len(parts)>=2 and parts[0].isdigit() and parts[1].upper() in ("GPON","XGPON","XGSPON"):
            indexes.append(int(parts[0]))

    return sorted(set(indexes))

def _vh_parse_ont_port_v48(text,expected_port):
    rows=[]
    _,slot_s,pon_s=expected_port.split("/")
    slot_expected=int(slot_s)
    pon_expected=int(pon_s)

    pat=_vh_re_v48.compile(
        r"^\s*0/\s*(\d+)/(\d+)\s+"
        r"(\d+)\s+"
        r"(\S+)\s+"
        r"(\S+)\s+"
        r"(online|offline)\s+"
        r"(\S+)\s+"
        r"(\S+)\s+"
        r"(\S+)",
        _vh_re_v48.I,
    )

    for line in str(text or "").splitlines():
        m=pat.search(line)
        if not m:
            continue

        slot=int(m.group(1))
        pon=int(m.group(2))

        if slot!=slot_expected or pon!=pon_expected:
            continue

        rows.append({
            "ont_id":int(m.group(3)),
            "sn":m.group(4),
            "control":m.group(5),
            "run_state":m.group(6).lower(),
            "config_state":m.group(7).lower(),
            "match_state":m.group(8).lower(),
            "protect_side":m.group(9).lower(),
        })

    return rows

def _vh_port_result_v48(port,rows):
    total=len(rows)
    online=sum(1 for row in rows if row.get("run_state")=="online")
    offline=max(0,total-online)

    if total<=0:
        status="SIN_DATOS"
    elif online<=0:
        status="CAIDA"
    elif (online/total)<0.85:
        status="ATENUADA"
    else:
        status="OPERATIVA"

    return {
        "port":port,
        "description":"",
        "state":{
            "total":total,
            "working":online,
            "online":online,
            "offline":offline,
            "dyinggasp":0,
            "los":0,
            "other":offline,
        },
        "power":{},
        "status":status,
        "errors":[],
        "fast_summary":True,
        "onus":rows,
    }

def _vh_totals_v48(results):
    return {
        "ports":len(results),
        "onus":sum(int(r.get("state",{}).get("total") or 0) for r in results),
        "working":sum(int(r.get("state",{}).get("working") or 0) for r in results),
        "online":sum(int(r.get("state",{}).get("online") or 0) for r in results),
        "offline":sum(int(r.get("state",{}).get("offline") or 0) for r in results),
        "dyinggasp":0,
        "los":0,
        "attenuated":0,
        "operativa":sum(1 for r in results if r.get("status")=="OPERATIVA"),
        "atenuada":sum(1 for r in results if r.get("status")=="ATENUADA"),
        "caida":sum(1 for r in results if r.get("status")=="CAIDA"),
    }

def _vh_cache_get_v48(ip,mode):
    key=(str(ip),str(mode))
    with _VM_HUAWEI_CACHE_LOCK_V48:
        item=_VM_HUAWEI_CACHE_V48.get(key)
        if not item:
            return None

        age=_vh_time_v48.monotonic()-item["ts"]

        if age>_VM_HUAWEI_CACHE_TTL_V48:
            _VM_HUAWEI_CACHE_V48.pop(key,None)
            return None

        value=dict(item["value"])
        value["cache_hit"]=True
        value["cache_age_s"]=round(age,2)
        return value

def _vh_cache_set_v48(ip,mode,value):
    key=(str(ip),str(mode))
    with _VM_HUAWEI_CACHE_LOCK_V48:
        _VM_HUAWEI_CACHE_V48[key]={
            "ts":_vh_time_v48.monotonic(),
            "value":dict(value),
        }

def _vh_fast_huawei_v48(olt,mode):
    cached=_vh_cache_get_v48(
        olt["ip"],
        mode,
    )

    if cached is not None:
        return cached

    started=_vh_time_v48.monotonic()
    deadline=started+65.0
    user,password=_credentials("HUAWEI")

    results=[]
    errors=[]
    ports=[]
    phase="INIT"

    try:
        # SESION 1: discovery confiable.
        phase="BOARD_DISCOVERY_CONNECT"

        with SSHJumpSession(
            olt["ip"],
            user,
            password,
            connect_timeout=8,
        ) as discovery_session:

            phase="BOARD_DISCOVERY_COMMAND"

            board_all=discovery_session.execute(
                "display board 0",
                timeout=20,
                idle_timeout=1.0,
            )

        phase="BOARD_PARSE"

        if _vh_invalid_v48(board_all):
            raise RuntimeError(
                "HUAWEI_DISPLAY_BOARD_FAIL:"
                +repr(str(board_all or "")[-300:])
            )

        boards=_vh_parse_boards_v48(
            board_all
        )

        if not boards:
            raise RuntimeError(
                "HUAWEI_SIN_BOARDS_GPON:"
                +repr(str(board_all or "")[-500:])
            )

        known_model_ports={
            "H901GPHF":list(range(8)),
            "H902FLHF":list(range(8)),
        }

        unknown_models=sorted({
            board["model"]
            for board in boards
            if board["model"]
            not in known_model_ports
        })

        if unknown_models:
            raise RuntimeError(
                "HUAWEI_BOARD_MODEL_NO_MAPEADO:"
                +",".join(unknown_models)
            )

        model_ports={
            model:list(indexes)
            for model,indexes
            in known_model_ports.items()
        }

        for board in boards:
            for pon in model_ports.get(
                board["model"],
                [],
            ):
                ports.append(
                    f"0/{board['slot']}/{pon}"
                )

        if not ports:
            raise RuntimeError(
                "HUAWEI_SIN_PUERTOS_DESCUBIERTOS"
            )

        # SESION 2: camino estable probado en V4.7.
        phase="SCAN_CONNECT"

        with SSHJumpSession(
            olt["ip"],
            user,
            password,
            connect_timeout=8,
        ) as session:

            phase="ENABLE"

            enable=session.execute(
                "enable",
                timeout=10,
                idle_timeout=0.8,
            )

            if "#" not in str(enable or ""):
                raise RuntimeError(
                    "HUAWEI_ENABLE_NO_CONFIRMADO:"
                    +repr(str(enable or "")[-160:])
                )

            phase="CONFIG"

            config=session.execute(
                "config",
                timeout=16,
                idle_timeout=0.8,
            )

            if _vh_invalid_v48(config):
                raise RuntimeError(
                    "HUAWEI_CONFIG_FAIL:"
                    +repr(str(config or "")[-240:])
                )

            current_slot=None

            for index,port in enumerate(ports):
                # Nunca dejar que PHP/browser mate la peticiÃ³n.
                remaining=deadline-_vh_time_v48.monotonic()

                if remaining<12.0:
                    errors.append(
                        "HUAWEI_DEADLINE_REACHED:"
                        +str(index)
                        +"/"
                        +str(len(ports))
                    )
                    break

                _,slot_s,pon_s=port.split("/")
                slot=int(slot_s)
                pon=int(pon_s)

                if slot!=current_slot:
                    phase=f"INTERFACE_SLOT_{slot}"

                    inter=session.execute(
                        f"interface gpon 0/{slot}",
                        timeout=min(12,max(6,int(remaining)-2)),
                        idle_timeout=0.8,
                    )

                    if _vh_invalid_v48(inter):
                        errors.append(
                            f"{port}:INTERFACE_GPON_FAIL"
                        )
                        current_slot=None
                        continue

                    current_slot=slot

                phase=f"ONT_{slot}_{pon}"

                try:
                    raw=session.execute(
                        f"display ont info {pon} all",
                        timeout=min(18,max(8,int(remaining)-2)),
                        idle_timeout=0.8,
                    )
                except Exception as ont_exc:
                    errors.append(
                        f"{port}:{type(ont_exc).__name__}:{ont_exc}"
                    )

                    # Socket cerrado => devolver lo recopilado,
                    # no perder todo el diagnÃ³stico.
                    if (
                        "socket is closed"
                        in str(ont_exc).lower()
                    ):
                        break

                    continue

                if _vh_invalid_v48(raw):
                    results.append({
                        "port":port,
                        "description":"",
                        "state":{
                            "total":0,
                            "working":0,
                            "online":0,
                            "offline":0,
                            "dyinggasp":0,
                            "los":0,
                            "other":0,
                        },
                        "power":{},
                        "status":"ERROR",
                        "errors":[
                            "HUAWEI_ONT_COMMAND_FAIL"
                        ],
                        "fast_summary":True,
                        "onus":[],
                    })
                    continue

                rows=_vh_parse_ont_port_v48(
                    raw,
                    port,
                )

                row=_vh_port_result_v48(
                    port,
                    rows,
                )

                if (
                    mode=="active"
                    and int(
                        row["state"].get("total")
                        or 0
                    )<=0
                ):
                    continue

                results.append(row)

        completed_ports=len(results)
        full_complete=(
            completed_ports>=len(ports)
        )

        phase="BUILD_RESPONSE"

        value={
            "ok":True,
            "fast":True,
            "partial":not full_complete,
            "cache_hit":False,
            "elapsed_s":round(
                _vh_time_v48.monotonic()
                -started,
                2,
            ),
            "olt":olt["olt"],
            "ip":olt["ip"],
            "vendor":"HUAWEI",
            "mode":mode,
            "checks":["state"],
            "detail_deferred":[
                "power",
                "trunk",
            ],
            "boards":boards,
            "model_ports":model_ports,
            "discovered_ports":len(ports),
            "completed_ports":completed_ports,
            "remaining_ports":max(
                0,
                len(ports)-completed_ports,
            ),
            "totals":_vh_totals_v48(
                results
            ),
            "results":results,
            "errors":errors,
        }

        _vh_cache_set_v48(
            olt["ip"],
            mode,
            value,
        )

        return value

    except Exception as exc:
        raise RuntimeError(
            f"HUAWEI_PHASE={phase};"
            f"{type(exc).__name__}:"
            f"{exc}"
        ) from exc


_vm_ftth_before_huawei_v48=diagnosticar_vm_ftth

def diagnosticar_vm_ftth(payload: dict[str, Any]) -> dict[str, Any]:
    olt=_resolve_olt(payload)

    vendor=(
        _clean(payload.get("vendor")).upper()
        or olt["vendor"]
    )

    if vendor=="AUTO":
        vendor=olt["vendor"]

    mode=(_clean(payload.get("mode")) or "specific").lower()
    fast=bool(payload.get("fast",True))
    olt_name=str(olt.get("olt") or "").upper()

    is_huawei=(vendor=="HUAWEI" or "MA5800" in olt_name)

    if fast and is_huawei and mode in {"all","active"}:
        try:
            return _vh_fast_huawei_v48(olt,mode)
        except Exception as exc:
            return {
                "ok":False,
                "fast":True,
                "partial":False,
                "error":f"{type(exc).__name__}:{exc}",
                "elapsed_s":0,
                "olt":olt["olt"],
                "ip":olt["ip"],
                "vendor":"HUAWEI",
                "mode":mode,
                "totals":{
                    "ports":0,
                    "onus":0,
                    "working":0,
                    "online":0,
                    "offline":0,
                    "dyinggasp":0,
                    "los":0,
                    "attenuated":0,
                    "operativa":0,
                    "atenuada":0,
                    "caida":0,
                },
                "results":[],
            }

    return _vm_ftth_before_huawei_v48(payload)
# VM_FTTH_HUAWEI_FAST_ENGINE_V4_8_END

# VM_FTTH_HUAWEI_GLOBAL_V5_0_START
import re as _v5_re
import time as _v5_time
import threading as _v5_threading

_V5_CACHE_TTL=30.0
_V5_CACHE={}
_V5_LOCK=_v5_threading.RLock()

_V5_PORT_HEADER=_v5_re.compile(
    r"In port\s+0/\s*(\d+)/(\d+),"
    r"\s*the total of ONTs are:\s*(\d+),"
    r"\s*online:\s*(\d+)",
    _v5_re.I,
)

_V5_ROW=_v5_re.compile(
    r"^\s*(\d+)\s+"
    r"(\S+)\s+"
    r"(online|offline)\s+"
    r"(\S+|-)\s+"
    r"([+-]?\d+(?:\.\d+)?|-)/"
    r"([+-]?\d+(?:\.\d+)?|-)",
    _v5_re.I,
)

def _v5_prepare_session(session):
    session.execute(
        "scroll",
        timeout=8,
        idle_timeout=0.6,
    )

    try:
        session.execute(
            "scroll 512",
            timeout=8,
            idle_timeout=0.6,
        )
    except Exception:
        pass

    session.execute(
        "undo smart",
        timeout=8,
        idle_timeout=0.6,
    )

    enable=session.execute(
        "enable",
        timeout=10,
        idle_timeout=0.8,
    )

    if "#" not in str(enable or ""):
        raise RuntimeError(
            "HUAWEI_ENABLE_NO_CONFIRMADO"
        )

def _v5_parse(raw):
    ports={}
    current=None

    for line in str(raw or "").splitlines():
        hm=_V5_PORT_HEADER.search(line)

        if hm:
            current=(
                f"0/{int(hm.group(1))}/"
                f"{int(hm.group(2))}"
            )

            ports.setdefault(current,{
                "reported_total":int(hm.group(3)),
                "reported_online":int(hm.group(4)),
                "onus":[],
            })
            continue

        rm=_V5_ROW.search(line)

        if rm and current:
            ports[current]["onus"].append({
                "ont_id":int(rm.group(1)),
                "sn":rm.group(2),
                "run_state":rm.group(3).lower(),
                "distance":rm.group(4),
                "rx":rm.group(5),
                "tx":rm.group(6),
            })

    return ports

def _v5_results(ports,mode):
    results=[]

    def key(p):
        return tuple(
            int(x) for x in p.split("/")
        )

    for port in sorted(ports,key=key):
        onus=ports[port]["onus"]
        total=len(onus)
        online=sum(
            1 for x in onus
            if x["run_state"]=="online"
        )
        offline=total-online

        if total<=0:
            status="SIN_DATOS"
        elif online<=0:
            status="CAIDA"
        elif online/total<0.85:
            status="ATENUADA"
        else:
            status="OPERATIVA"

        if mode=="active" and total<=0:
            continue

        results.append({
            "port":port,
            "description":"",
            "state":{
                "total":total,
                "working":online,
                "online":online,
                "offline":offline,
                "dyinggasp":0,
                "los":0,
                "other":offline,
            },
            "power":{
                "rx_values":[
                    x["rx"] for x in onus
                    if x["rx"]!="-"
                ]
            },
            "status":status,
            "errors":[],
            "fast_summary":True,
            "onus":onus,
        })

    return results

def _v5_totals(results):
    return {
        "ports":len(results),
        "onus":sum(
            x["state"]["total"]
            for x in results
        ),
        "working":sum(
            x["state"]["working"]
            for x in results
        ),
        "online":sum(
            x["state"]["online"]
            for x in results
        ),
        "offline":sum(
            x["state"]["offline"]
            for x in results
        ),
        "dyinggasp":0,
        "los":0,
        "attenuated":0,
        "operativa":sum(
            1 for x in results
            if x["status"]=="OPERATIVA"
        ),
        "atenuada":sum(
            1 for x in results
            if x["status"]=="ATENUADA"
        ),
        "caida":sum(
            1 for x in results
            if x["status"]=="CAIDA"
        ),
    }

def _v5_scan(olt,mode):
    key=(str(olt["ip"]),str(mode))

    with _V5_LOCK:
        item=_V5_CACHE.get(key)

        if item:
            age=_v5_time.monotonic()-item["ts"]

            if age<=_V5_CACHE_TTL:
                value=dict(item["value"])
                value["cache_hit"]=True
                value["cache_age_s"]=round(age,2)
                return value

    started=_v5_time.monotonic()
    user,password=_credentials("HUAWEI")

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=8,
    ) as session:

        _v5_prepare_session(session)

        raw=session.execute(
            "display ont info option run-state 0 all",
            timeout=55,
            idle_timeout=1.2,
        )

    if "---- More" in raw:
        raise RuntimeError(
            "HUAWEI_V5_PAGING_STILL_ACTIVE"
        )

    ports=_v5_parse(raw)

    if len(ports)<2:
        raise RuntimeError(
            "HUAWEI_V5_TOO_FEW_PORTS:"
            +str(len(ports))
        )

    results=_v5_results(ports,mode)
    totals=_v5_totals(results)

    if totals["onus"]<=0:
        raise RuntimeError(
            "HUAWEI_V5_ZERO_ONUS"
        )

    value={
        "ok":True,
        "fast":True,
        "partial":False,
        "cache_hit":False,
        "aggregate":True,
        "elapsed_s":round(
            _v5_time.monotonic()-started,
            2,
        ),
        "olt":olt["olt"],
        "ip":olt["ip"],
        "vendor":"HUAWEI",
        "mode":mode,
        "checks":["state","power"],
        "aggregate_command":
            "display ont info option "
            "run-state 0 all",
        "session_prep":[
            "scroll",
            "scroll 512",
            "undo smart",
        ],
        "discovered_ports":len(results),
        "completed_ports":len(results),
        "remaining_ports":0,
        "totals":totals,
        "results":results,
        "errors":[],
    }

    with _V5_LOCK:
        _V5_CACHE[key]={
            "ts":_v5_time.monotonic(),
            "value":dict(value),
        }

    return value

_vm_ftth_before_v5=diagnosticar_vm_ftth

def diagnosticar_vm_ftth(
    payload: dict[str, Any]
) -> dict[str, Any]:

    olt=_resolve_olt(payload)

    vendor=(
        _clean(payload.get("vendor")).upper()
        or olt["vendor"]
    )

    if vendor=="AUTO":
        vendor=olt["vendor"]

    mode=(
        _clean(payload.get("mode"))
        or "specific"
    ).lower()

    fast=bool(
        payload.get("fast",True)
    )

    olt_name=str(
        olt.get("olt")
        or ""
    ).upper()

    is_huawei=(
        vendor=="HUAWEI"
        or "MA5800" in olt_name
    )

    if (
        fast
        and is_huawei
        and mode in {"all","active"}
    ):
        try:
            return _v5_scan(
                olt,
                mode,
            )
        except Exception:
            return _vm_ftth_before_v5(
                payload
            )

    return _vm_ftth_before_v5(
        payload
    )
# VM_FTTH_HUAWEI_GLOBAL_V5_0_END

# VM_FTTH_HUAWEI_GLOBAL_V5_2_START
import re as _v5_re
import time as _v5_time
import threading as _v5_threading

_V5_CACHE_TTL=180.0
_V5_CACHE={}
_V5_LOCK=_v5_threading.RLock()

_V5_PORT_HEADER=_v5_re.compile(
    r"In port\s+0/\s*(\d+)/(\d+),"
    r"\s*the total of ONTs are:\s*(\d+),"
    r"\s*online:\s*(\d+)",
    _v5_re.I,
)

_V5_ROW=_v5_re.compile(
    r"^\s*(\d+)\s+"
    r"(\S+)\s+"
    r"(online|offline)\s+"
    r"(\S+|-)\s+"
    r"([+-]?\d+(?:\.\d+)?|-)/"
    r"([+-]?\d+(?:\.\d+)?|-)",
    _v5_re.I,
)

def _v5_prepare_session(session):
    session.execute(
        "scroll",
        timeout=8,
        idle_timeout=0.6,
    )

    session.execute(
        "undo smart",
        timeout=8,
        idle_timeout=0.6,
    )

    enable=session.execute(
        "enable",
        timeout=10,
        idle_timeout=0.8,
    )

    if "#" not in str(enable or ""):
        raise RuntimeError(
            "HUAWEI_ENABLE_NO_CONFIRMADO"
        )

def _v5_parse(raw):
    ports={}
    current=None

    for line in str(raw or "").splitlines():
        hm=_V5_PORT_HEADER.search(line)

        if hm:
            current=(
                f"0/{int(hm.group(1))}/"
                f"{int(hm.group(2))}"
            )

            ports.setdefault(current,{
                "reported_total":int(hm.group(3)),
                "reported_online":int(hm.group(4)),
                "onus":[],
            })
            continue

        rm=_V5_ROW.search(line)

        if rm and current:
            ports[current]["onus"].append({
                "ont_id":int(rm.group(1)),
                "sn":rm.group(2),
                "run_state":rm.group(3).lower(),
                "distance":rm.group(4),
                "rx":rm.group(5),
                "tx":rm.group(6),
            })

    return ports

def _v5_results(ports,mode):
    results=[]

    def key(p):
        return tuple(
            int(x) for x in p.split("/")
        )

    for port in sorted(ports,key=key):
        onus=ports[port]["onus"]
        total=len(onus)
        online=sum(
            1 for x in onus
            if x["run_state"]=="online"
        )
        offline=total-online

        if total<=0:
            status="SIN_DATOS"
        elif online<=0:
            status="CAIDA"
        elif online/total<0.85:
            status="ATENUADA"
        else:
            status="OPERATIVA"

        if mode=="active" and total<=0:
            continue

        results.append({
            "port":port,
            "description":"",
            "state":{
                "total":total,
                "working":online,
                "online":online,
                "offline":offline,
                "dyinggasp":0,
                "los":0,
                "other":offline,
            },
            "power":{
                "rx_values":[
                    x["rx"] for x in onus
                    if x["rx"]!="-"
                ]
            },
            "status":status,
            "errors":[],
            "fast_summary":True,
            "onus":onus,
        })

    return results

def _v5_totals(results):
    return {
        "ports":len(results),
        "onus":sum(
            x["state"]["total"]
            for x in results
        ),
        "working":sum(
            x["state"]["working"]
            for x in results
        ),
        "online":sum(
            x["state"]["online"]
            for x in results
        ),
        "offline":sum(
            x["state"]["offline"]
            for x in results
        ),
        "dyinggasp":0,
        "los":0,
        "attenuated":0,
        "operativa":sum(
            1 for x in results
            if x["status"]=="OPERATIVA"
        ),
        "atenuada":sum(
            1 for x in results
            if x["status"]=="ATENUADA"
        ),
        "caida":sum(
            1 for x in results
            if x["status"]=="CAIDA"
        ),
    }

def _v5_scan(olt,mode):
    key=(str(olt["ip"]),str(mode))

    with _V5_LOCK:
        item=_V5_CACHE.get(key)

        if item:
            age=_v5_time.monotonic()-item["ts"]

            if age<=_V5_CACHE_TTL:
                value=dict(item["value"])
                value["cache_hit"]=True
                value["cache_age_s"]=round(age,2)
                return value

    started=_v5_time.monotonic()
    user,password=_credentials("HUAWEI")

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=8,
    ) as session:

        _v5_prepare_session(session)

        raw=session.execute(
            "display ont info option run-state 0 all",
            timeout=80,
            idle_timeout=1.2,
        )

    if "---- More" in raw:
        raise RuntimeError(
            "HUAWEI_V5_PAGING_STILL_ACTIVE"
        )

    if not _v5_re.search(
        r"[>#]\s*$",
        str(raw or "").rstrip(),
    ):
        raise RuntimeError(
            "HUAWEI_V5_NO_FINAL_PROMPT"
        )

    ports=_v5_parse(raw)

    if len(ports)<2:
        raise RuntimeError(
            "HUAWEI_V5_TOO_FEW_PORTS:"
            +str(len(ports))
        )

    slots={
        int(p.split("/")[1])
        for p in ports
    }

    if len(slots)<2:
        raise RuntimeError(
            "HUAWEI_V5_ONLY_ONE_SLOT:"
            +",".join(
                str(x) for x in sorted(slots)
            )
        )

    results=_v5_results(ports,mode)
    totals=_v5_totals(results)

    if totals["onus"]<=0:
        raise RuntimeError(
            "HUAWEI_V5_ZERO_ONUS"
        )

    value={
        "ok":True,
        "fast":True,
        "partial":False,
        "cache_hit":False,
        "aggregate":True,
        "elapsed_s":round(
            _v5_time.monotonic()-started,
            2,
        ),
        "olt":olt["olt"],
        "ip":olt["ip"],
        "vendor":"HUAWEI",
        "mode":mode,
        "checks":["state","power"],
        "aggregate_command":
            "display ont info option "
            "run-state 0 all",
        "session_prep":[
            "scroll",
            "undo smart",
        ],
        "discovered_ports":len(results),
        "completed_ports":len(results),
        "remaining_ports":0,
        "totals":totals,
        "results":results,
        "errors":[],
    }

    with _V5_LOCK:
        _V5_CACHE[key]={
            "ts":_v5_time.monotonic(),
            "value":dict(value),
        }

    return value

_vm_ftth_before_v5=diagnosticar_vm_ftth

def diagnosticar_vm_ftth(
    payload: dict[str, Any]
) -> dict[str, Any]:

    olt=_resolve_olt(payload)

    vendor=(
        _clean(payload.get("vendor")).upper()
        or olt["vendor"]
    )

    if vendor=="AUTO":
        vendor=olt["vendor"]

    mode=(
        _clean(payload.get("mode"))
        or "specific"
    ).lower()

    fast=bool(
        payload.get("fast",True)
    )

    olt_name=str(
        olt.get("olt")
        or ""
    ).upper()

    is_huawei=(
        vendor=="HUAWEI"
        or "MA5800" in olt_name
    )

    if (
        fast
        and is_huawei
        and mode in {"all","active"}
    ):
        try:
            return _v5_scan(
                olt,
                mode,
            )
        except Exception:
            return _vm_ftth_before_v5(
                payload
            )

    return _vm_ftth_before_v5(
        payload
    )
# VM_FTTH_HUAWEI_GLOBAL_V5_2_END

# VM_FTTH_DIRECT_DISPATCH_V8_4_START
import re as _v84_re
import time as _v84_time
import threading as _v84_threading

_V84_ZTE_CACHE = {}
_V84_ZTE_LOCK = _v84_threading.RLock()
_V84_ZTE_TTL = 30.0

def _v84_zte_fast(olt, mode):
    key = (str(olt.get("ip") or ""), str(mode))

    with _V84_ZTE_LOCK:
        item = _V84_ZTE_CACHE.get(key)
        if item:
            age = _v84_time.monotonic() - item["ts"]
            if age <= _V84_ZTE_TTL:
                value = dict(item["value"])
                value["cache_hit"] = True
                value["cache_age_s"] = round(age, 2)
                return value

    started = _v84_time.monotonic()
    user, password = _credentials("ZTE")

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=8,
    ) as session:
        try:
            session.execute(
                "terminal length 0",
                timeout=8,
                idle_timeout=0.5,
            )
        except Exception:
            pass

        raw = session.execute(
            "show gpon onu state | include working|DyingGasp|LOS",
            timeout=32,
            idle_timeout=0.35,
        )

    pat = _v84_re.compile(
        r"^\s*(\d+/\d+/\d+):(\d+)\s+.*?\b(working|DyingGasp|LOS)\b",
        _v84_re.I,
    )

    grouped = {}

    for line in str(raw or "").splitlines():
        m = pat.search(line)
        if not m:
            continue

        port = m.group(1)
        state = m.group(3).lower()

        d = grouped.setdefault(
            port,
            {"working": 0, "dyinggasp": 0, "los": 0, "total": 0},
        )

        d["total"] += 1

        if state == "working":
            d["working"] += 1
        elif state == "dyinggasp":
            d["dyinggasp"] += 1
        elif state == "los":
            d["los"] += 1

    if not grouped:
        raise RuntimeError("ZTE_V84_ZERO_ROWS")

    def pkey(port):
        return tuple(int(x) for x in port.split("/"))

    results = []

    for port in sorted(grouped, key=pkey):
        d = grouped[port]
        total = d["total"]
        working = d["working"]

        if total <= 0:
            status = "SIN_DATOS"
        elif working <= 0:
            status = "CAIDA"
        elif working / total < 0.85:
            status = "ATENUADA"
        else:
            status = "OPERATIVA"

        results.append({
            "port": port,
            "description": "",
            "state": {
                "total": total,
                "working": working,
                "online": working,
                "offline": total - working,
                "dyinggasp": d["dyinggasp"],
                "los": d["los"],
                "other": 0,
            },
            "power": {"rx_values": []},
            "status": status,
            "errors": [],
            "fast_summary": True,
        })

    totals = {
        "ports": len(results),
        "onus": sum(x["state"]["total"] for x in results),
        "working": sum(x["state"]["working"] for x in results),
        "online": sum(x["state"]["online"] for x in results),
        "offline": sum(x["state"]["offline"] for x in results),
        "dyinggasp": sum(x["state"]["dyinggasp"] for x in results),
        "los": sum(x["state"]["los"] for x in results),
        "attenuated": 0,
        "operativa": sum(1 for x in results if x["status"] == "OPERATIVA"),
        "atenuada": sum(1 for x in results if x["status"] == "ATENUADA"),
        "caida": sum(1 for x in results if x["status"] == "CAIDA"),
    }

    value = {
        "ok": True,
        "fast": True,
        "partial": False,
        "cache_hit": False,
        "elapsed_s": round(_v84_time.monotonic() - started, 2),
        "olt": olt["olt"],
        "ip": olt["ip"],
        "vendor": "ZTE",
        "mode": mode,
        "discovered_ports": len(results),
        "completed_ports": len(results),
        "remaining_ports": 0,
        "totals": totals,
        "results": results,
        "errors": [],
    }

    with _V84_ZTE_LOCK:
        _V84_ZTE_CACHE[key] = {
            "ts": _v84_time.monotonic(),
            "value": dict(value),
        }

    return value


def diagnosticar_vm_ftth(payload):
    olt = _resolve_olt(payload)

    vendor = (
        _clean(payload.get("vendor")).upper()
        or str(olt.get("vendor") or "").upper()
    )

    if vendor == "AUTO":
        vendor = str(olt.get("vendor") or "").upper()

    olt_name = str(olt.get("olt") or "").upper()

    if not vendor:
        if "MA5800" in olt_name:
            vendor = "HUAWEI"
        elif "C600" in olt_name or "ZTE" in olt_name:
            vendor = "ZTE"

    mode = (_clean(payload.get("mode")) or "specific").lower()
    fast = bool(payload.get("fast", True))

    if fast and vendor == "ZTE" and mode in {"all", "active"}:
        return _v84_zte_fast(olt, mode)

    if (
        fast
        and (vendor == "HUAWEI" or "MA5800" in olt_name)
        and mode in {"all", "active"}
    ):
        return _v5_scan(olt, mode)

    return _vm_ftth_base_v84(payload)
# VM_FTTH_DIRECT_DISPATCH_V8_4_END

# VM_FTTH_HUAWEI_EXEC_GUARD_V9_4_START
_vm_ftth_execute_before_v94 = SSHJumpSession.execute

def _vm_ftth_execute_v94(
    self,
    command,
    timeout=35,
    idle_timeout=1.0,
):
    _vm_command_v94 = str(command or "").strip()

    if (
        _vm_command_v94
        == "display ont info option run-state 0 all"
    ):
        timeout = max(float(timeout), 120.0)
        idle_timeout = max(float(idle_timeout), 6.0)

    return _vm_ftth_execute_before_v94(
        self,
        command,
        timeout=timeout,
        idle_timeout=idle_timeout,
    )

SSHJumpSession.execute = _vm_ftth_execute_v94
# VM_FTTH_HUAWEI_EXEC_GUARD_V9_4_END

# VM_FTTH_HUAWEI_APART_SUMMARY_V9_5_START
import re as _v95_re
import time as _v95_time
import threading as _v95_threading

_V95_APART_CAPABILITY = "huawei_apart_summary_v95"
_V95_CACHE = {}
_V95_LOCK = _v95_threading.RLock()
_V95_TTL = 180.0

_vm_ftth_before_apart_v95 = diagnosticar_vm_ftth


def _v95_apart_summary(olt, mode):
    key = (
        str(olt.get("ip") or ""),
        str(mode),
    )

    with _V95_LOCK:
        cached = _V95_CACHE.get(key)

        if cached:
            age = _v95_time.monotonic() - cached["ts"]

            if age <= _V95_TTL:
                value = dict(cached["value"])
                value["cache_hit"] = True
                value["cache_age_s"] = round(age, 2)
                return value

    started = _v95_time.monotonic()
    user, password = _credentials("HUAWEI")

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=12,
    ) as session:
        try:
            session.execute(
                "scroll",
                timeout=10,
                idle_timeout=0.8,
            )
        except Exception:
            pass

        try:
            session.execute(
                "undo smart",
                timeout=10,
                idle_timeout=0.8,
            )
        except Exception:
            pass

        enable_out = session.execute(
            "enable",
            timeout=12,
            idle_timeout=0.8,
        )

        if not _v95_re.search(
            r"[>#]\s*$",
            str(enable_out or "").strip(),
        ):
            raise RuntimeError(
                "HUAWEI_APART_ENABLE_NO_CONFIRMADO"
            )

        raw = session.execute(
            "display ont info summary 0",
            timeout=150,
            idle_timeout=8.0,
        )

    text = str(raw or "")
    lines = text.splitlines()

    header_re = _v95_re.compile(
        r"In port\s+(\d+/\d+/\d+),\s+"
        r"the total of ONTs are:\s*(\d+),\s+online:\s*(\d+)",
        _v95_re.I,
    )

    prompt_re = _v95_re.compile(
        r"^[A-Za-z0-9_.:\-]+(?:\([^)]+\))?[>#]\s*$"
    )

    port_data = {}

    for line in lines:
        match = header_re.search(line)

        if not match:
            continue

        port = match.group(1)
        total = int(match.group(2))
        online = int(match.group(3))
        offline = max(0, total - online)

        if total <= 0:
            status = "SIN_DATOS"
        elif online <= 0:
            status = "CAIDA"
        elif (online / total) < 0.85:
            status = "ATENUADA"
        else:
            status = "OPERATIVA"

        port_data[port] = {
            "total": total,
            "online": online,
            "offline": offline,
            "status": status,
        }

    if not port_data:
        raise RuntimeError(
            "HUAWEI_APART_SUMMARY_SIN_PUERTOS"
        )

    nonempty = [
        line.strip()
        for line in lines
        if line.strip()
    ]

    last_line = nonempty[-1] if nonempty else ""
    final_prompt = bool(prompt_re.match(last_line))

    def port_key(port):
        return tuple(
            int(x)
            for x in port.split("/")
        )

    results = []

    for port in sorted(port_data, key=port_key):
        data = port_data[port]

        results.append({
            "port": port,
            "description": "",
            "state": {
                "total": data["total"],
                "working": data["online"],
                "online": data["online"],
                "offline": data["offline"],
                "dyinggasp": 0,
                "los": 0,
                "other": data["offline"],
            },
            "power": {
                "rx_values": [],
            },
            "status": data["status"],
            "errors": (
                []
                if final_prompt
                else ["HUAWEI_APART_SUMMARY_SIN_PROMPT_FINAL"]
            ),
            "fast_summary": True,
        })

    totals = {
        "ports": len(results),
        "onus": sum(
            item["state"]["total"]
            for item in results
        ),
        "working": sum(
            item["state"]["working"]
            for item in results
        ),
        "online": sum(
            item["state"]["online"]
            for item in results
        ),
        "offline": sum(
            item["state"]["offline"]
            for item in results
        ),
        "dyinggasp": 0,
        "los": 0,
        "other": sum(
            item["state"]["other"]
            for item in results
        ),
        "attenuated": 0,
        "operativa": sum(
            1
            for item in results
            if item["status"] == "OPERATIVA"
        ),
        "atenuada": sum(
            1
            for item in results
            if item["status"] == "ATENUADA"
        ),
        "caida": sum(
            1
            for item in results
            if item["status"] == "CAIDA"
        ),
    }

    value = {
        "ok": True,
        "fast": True,
        "partial": not final_prompt,
        "cache_hit": False,
        "aggregate": True,
        "elapsed_s": round(
            _v95_time.monotonic() - started,
            2,
        ),
        "olt": olt["olt"],
        "ip": olt["ip"],
        "vendor": "HUAWEI",
        "mode": mode,
        "checks": {
            "final_prompt": final_prompt,
            "summary_fallback": True,
        },
        "aggregate_command":
            "display ont info summary 0",
        "discovered_ports": len(results),
        "completed_ports": len(results),
        "remaining_ports": 0,
        "totals": totals,
        "results": results,
        "errors": (
            []
            if final_prompt
            else ["HUAWEI_APART_SUMMARY_SIN_PROMPT_FINAL"]
        ),
    }

    with _V95_LOCK:
        _V95_CACHE[key] = {
            "ts": _v95_time.monotonic(),
            "value": dict(value),
        }

    return value


def diagnosticar_vm_ftth(payload):
    olt = _resolve_olt(payload)

    vendor = (
        _clean(payload.get("vendor")).upper()
        or str(olt.get("vendor") or "").upper()
    )

    if vendor == "AUTO":
        vendor = str(
            olt.get("vendor") or ""
        ).upper()

    mode = (
        _clean(payload.get("mode"))
        or "specific"
    ).lower()

    fast = bool(
        payload.get("fast", True)
    )

    if (
        fast
        and vendor == "HUAWEI"
        and mode in {"all", "active"}
        and bool(
            (olt.get("atlas_capabilities") or {}).get(
                _V95_APART_CAPABILITY
            )
        )
    ):
        return _v95_apart_summary(
            olt,
            mode,
        )

    return _vm_ftth_before_apart_v95(
        payload
    )
# VM_FTTH_HUAWEI_APART_SUMMARY_V9_5_END

# VM_FTTH_ZTE_EMPTY_230285_V11_5_START
import re as _zteempty_re
import time as _zteempty_time

_ZTEEMPTY_PROMPT_RE = _zteempty_re.compile(
    r"^[A-Za-z0-9_.:\-]+(?:\([^)]+\))?[>#]\s*$"
)

_ZTEEMPTY_MESSAGE = (
    "%Error 230285: No related information to show."
)


def _zteempty_last(text):
    for line in reversed(
        str(text or "").splitlines()
    ):
        value = line.strip()
        if value:
            return value
    return ""


def _zteempty_exact_230285(text):
    for line in str(text or "").splitlines():
        if line.strip() == _ZTEEMPTY_MESSAGE:
            return True
    return False


def _zteempty_confirm_and_return(payload):
    started = _zteempty_time.monotonic()

    olt = _resolve_olt(payload)

    vendor = str(
        payload.get("vendor")
        or olt.get("vendor")
        or ""
    ).upper()

    if vendor == "AUTO":
        vendor = str(
            olt.get("vendor")
            or ""
        ).upper()

    if vendor != "ZTE":
        raise RuntimeError(
            "ZTE_EMPTY_CONFIRM_NOT_ZTE"
        )

    user, password = _credentials("ZTE")

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=12,
    ) as session:
        try:
            session.execute(
                "terminal length 0",
                timeout=12,
                idle_timeout=0.8,
            )
        except Exception:
            pass

        raw = session.execute(
            "show gpon onu state",
            timeout=20,
            idle_timeout=1.2,
        )

    last_line = _zteempty_last(raw)

    if not _ZTEEMPTY_PROMPT_RE.match(
        last_line
    ):
        raise RuntimeError(
            "ZTE_EMPTY_CONFIRM_NO_FINAL_PROMPT"
        )

    if not _zteempty_exact_230285(raw):
        raise RuntimeError(
            "ZTE_EMPTY_CONFIRM_230285_NOT_FOUND"
        )

    totals = {
        "ports": 0,
        "onus": 0,
        "working": 0,
        "online": 0,
        "offline": 0,
        "dyinggasp": 0,
        "los": 0,
        "attenuated": 0,
        "operativa": 0,
        "atenuada": 0,
        "caida": 0,
        "other": 0,
    }

    return {
        "ok": True,
        "fast": True,
        "complete": True,
        "partial": False,
        "cache_hit": False,
        "cache_age_s": 0.0,
        "deduplicated": False,
        "strategy": "zte_verified_empty_230285",
        "aggregate": True,
        "olt": (
            olt.get("olt")
            or payload.get("olt")
            or ""
        ),
        "ip": (
            olt.get("ip")
            or payload.get("ip")
            or ""
        ),
        "vendor": "ZTE",
        "mode": str(
            payload.get("mode")
            or "all"
        ).lower(),
        "elapsed_s": round(
            _zteempty_time.monotonic()
            - started,
            2,
        ),
        "discovered_ports": 0,
        "completed_ports": 0,
        "remaining_ports": 0,
        "totals": totals,
        "results": [],
        "errors": [],
        "empty_reason": "NO_RELATED_ONU_INFORMATION",
        "empty_confirmed": True,
    }

_vm_ftth_before_zte_empty_v115 = diagnosticar_vm_ftth


def diagnosticar_vm_ftth(payload):
    try:
        return _vm_ftth_before_zte_empty_v115(
            payload
        )
    except RuntimeError as exc:
        if "ZTE_V84_ZERO_ROWS" not in str(exc):
            raise

        try:
            return _zteempty_confirm_and_return(
                payload
            )
        except Exception:
            raise exc

# VM_FTTH_ZTE_EMPTY_230285_V11_5_END

# VM_FTTH_HUAWEI_FLSF_EMPTY_FAST_V11_7_START
import re as _hflsf_re
import time as _hflsf_time

_HFLSF_PROMPT_RE = _hflsf_re.compile(
    r"^[A-Za-z0-9_.:\-]+(?:\([^)]+\))?[>#]\s*$"
)

_HFLSF_BOARD_RE = _hflsf_re.compile(
    r"^\s*(\d+)\s+H901FLSF\s+",
    _hflsf_re.I,
)

_HFLSF_EMPTY_TEXT = "Failure: The ONT does not exist"


def _hflsf_last(text):
    for line in reversed(str(text or "").splitlines()):
        value = line.strip()
        if value:
            return value
    return ""


def _hflsf_slots(text):
    slots = []
    seen = set()

    for line in str(text or "").splitlines():
        match = _HFLSF_BOARD_RE.match(line)

        if not match:
            continue

        slot = int(match.group(1))

        if slot not in seen:
            seen.add(slot)
            slots.append(slot)

    return slots


def _hflsf_exact_empty(text):
    for line in str(text or "").splitlines():
        if line.strip() == _HFLSF_EMPTY_TEXT:
            return True
    return False


def _hflsf_fast_empty(payload):
    started = _hflsf_time.monotonic()

    olt = _resolve_olt(payload)

    vendor = str(
        payload.get("vendor")
        or olt.get("vendor")
        or ""
    ).upper()

    if vendor == "AUTO":
        vendor = str(olt.get("vendor") or "").upper()

    if vendor not in ("HUAWEI", "MA5800", "HAC"):
        return None

    user, password = _credentials("HUAWEI")

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=12,
    ) as session:
        try:
            session.execute(
                "enable",
                timeout=8,
                idle_timeout=0.7,
            )
            session.execute(
                "config",
                timeout=8,
                idle_timeout=0.7,
            )
        except Exception:
            return None

        for command in ("scroll", "undo smart"):
            try:
                session.execute(
                    command,
                    timeout=8,
                    idle_timeout=0.7,
                )
            except Exception:
                pass

        try:
            board_raw = session.execute(
                "display board 0",
                timeout=18,
                idle_timeout=1.1,
            )
        except Exception:
            return None

        if not _HFLSF_PROMPT_RE.match(_hflsf_last(board_raw)):
            return None

        slots = _hflsf_slots(board_raw)

        if not slots:
            return None

        verified = []

        for slot in slots:
            command = "display ont info summary 0/" + str(slot)

            try:
                raw = session.execute(
                    command,
                    timeout=15,
                    idle_timeout=1.2,
                )
            except Exception:
                return None

            if not _HFLSF_PROMPT_RE.match(_hflsf_last(raw)):
                return None

            if not _hflsf_exact_empty(raw):
                return None

            verified.append(slot)

    if not verified:
        return None

    totals = {
        "ports": 0,
        "onus": 0,
        "working": 0,
        "online": 0,
        "offline": 0,
        "dyinggasp": 0,
        "los": 0,
        "attenuated": 0,
        "operativa": 0,
        "atenuada": 0,
        "caida": 0,
        "other": 0,
    }

    return {
        "ok": True,
        "fast": True,
        "complete": True,
        "partial": False,
        "cache_hit": False,
        "cache_age_s": 0.0,
        "deduplicated": False,
        "strategy": "huawei_flsf_verified_empty_fast",
        "aggregate": True,
        "olt": olt.get("olt") or payload.get("olt") or "",
        "ip": olt.get("ip") or payload.get("ip") or "",
        "vendor": "HUAWEI",
        "mode": str(payload.get("mode") or "all").lower(),
        "elapsed_s": round(_hflsf_time.monotonic() - started, 2),
        "discovered_ports": 0,
        "completed_ports": 0,
        "remaining_ports": 0,
        "totals": totals,
        "results": [],
        "errors": [],
        "empty_reason": "NO_ONT_ON_H901FLSF",
        "empty_confirmed": True,
        "flsf_slots": verified,
    }

_vm_ftth_before_hflsf_v117 = diagnosticar_vm_ftth


def diagnosticar_vm_ftth(payload):
    try:
        fast_empty = _hflsf_fast_empty(payload)
    except Exception:
        fast_empty = None

    if fast_empty is not None:
        return fast_empty

    return _vm_ftth_before_hflsf_v117(payload)

# VM_FTTH_HUAWEI_FLSF_EMPTY_FAST_V11_7_END

# VM_FTTH_ZTE_SPECIFIC_REAL_PARAMS_V11_8_START
import re as _z118_re
import time as _z118_time

_Z118_RX_ATT_DBM = -27.0
_Z118_OPER_PCT = 85.0

_Z118_ONU_RE = _z118_re.compile(
    r"(?:gpon[-_]onu[_-])?(\d+/\d+/\d+):(\d+)",
    _z118_re.I,
)
_Z118_STATE_RE = _z118_re.compile(
    r"\b(working|dyinggasp|los|offline|online|logging|syncmib)\b",
    _z118_re.I,
)
_Z118_POWER_RE = _z118_re.compile(
    r"gpon[_-]onu[-_](\d+/\d+/\d+):(\d+)\s+(-?\d+(?:\.\d+)?)\s*\(dbm\)",
    _z118_re.I,
)
_Z118_DESC_RE = _z118_re.compile(
    r"Description\s+(?:is\s+)?(.+)$",
    _z118_re.I,
)


def _z118_clean(value):
    return "".join(
        ch for ch in str(value or "")
        if ch == "\t" or ord(ch) >= 32
    ).strip()


def _z118_pct(num, den):
    if den <= 0:
        return 0.0
    return round((num * 100.0) / den, 2)


def _z118_parse_state(text, port):
    counts = {
        "working": 0,
        "dyinggasp": 0,
        "los": 0,
        "other": 0,
    }
    working_ids = set()

    for line in str(text or "").splitlines():
        value = _z118_clean(line)
        match = _Z118_ONU_RE.search(value)
        if not match or match.group(1) != port:
            continue

        onu_id = int(match.group(2))
        state_match = _Z118_STATE_RE.search(value)
        state = state_match.group(1).lower() if state_match else "other"

        if state in ("working", "online"):
            counts["working"] += 1
            working_ids.add(onu_id)
        elif state == "dyinggasp":
            counts["dyinggasp"] += 1
        elif state == "los":
            counts["los"] += 1
        else:
            counts["other"] += 1

    counts["registered"] = (
        counts["working"] + counts["dyinggasp"] + counts["los"]
    )
    return counts, working_ids


def _z118_parse_power(text, port, working_ids):
    values = {}
    for line in str(text or "").splitlines():
        value = _z118_clean(line)
        match = _Z118_POWER_RE.search(value)
        if not match or match.group(1) != port:
            continue

        onu_id = int(match.group(2))
        if onu_id not in working_ids:
            continue

        values[onu_id] = float(match.group(3))

    attenuated = {
        onu_id: rx
        for onu_id, rx in values.items()
        if rx <= _Z118_RX_ATT_DBM
    }
    return values, attenuated


def _z118_parse_desc(text):
    for line in str(text or "").splitlines():
        value = _z118_clean(line)
        match = _Z118_DESC_RE.search(value)
        if match:
            return match.group(1).strip()
    return ""


def _z118_state_observation(counts):
    w = counts["working"]
    d = counts["dyinggasp"]
    l = counts["los"]

    if d > w and d > l:
        return "CAIDA", "TRK Caida por Energia"
    if l > w and l > d:
        return "CAIDA", "TRK Caida por ruptura de fibra"
    return None, None


def _z118_specific(payload):
    # VM_FTTH_DISPATCH_GUARD_V11_12_ZTE
    _z118_fast_raw = payload.get("fast")
    _z118_fast_requested = (
        _z118_fast_raw is True
        or str(_z118_fast_raw or "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    _z118_mode = str(payload.get("mode") or "").strip().lower()
    if (
        _z118_fast_requested
        or _z118_mode in (
            "fast",
            "quick",
            "barrido",
            "barrido_rapido",
            "barrido rapido",
        )
    ):
        return None
    raw_ports = payload.get("ports") or []
    ports = []

    for raw in raw_ports:
        value = str(raw or "").strip()
        if not _z118_re.fullmatch(r"\d+/\d+/\d+", value):
            continue
        if value not in ports:
            ports.append(value)

    if not ports:
        return None

    olt = _resolve_olt(payload)
    vendor = str(
        payload.get("vendor") or olt.get("vendor") or ""
    ).upper()

    if vendor == "AUTO":
        vendor = str(olt.get("vendor") or "").upper()

    if vendor not in ("ZTE", "C600"):
        return None

    started = _z118_time.monotonic()
    user, password = _credentials("ZTE")

    rows = []
    errors = []

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=12,
    ) as session:
        try:
            session.execute(
                "terminal length 0",
                timeout=10,
                idle_timeout=0.7,
            )
        except Exception:
            pass

        for port in ports:
            port_started = _z118_time.monotonic()

            try:
                state_raw = session.execute(
                    "show gpon onu state gpon_olt-" + port,
                    timeout=25,
                    idle_timeout=1.3,
                )
                power_raw = session.execute(
                    "show pon power onu-rx gpon_olt-" + port,
                    timeout=30,
                    idle_timeout=1.5,
                )
                desc_raw = session.execute(
                    "show interface gpon_olt-"
                    + port
                    + " | include Description",
                    timeout=18,
                    idle_timeout=1.0,
                )

                counts, working_ids = _z118_parse_state(
                    state_raw,
                    port,
                )
                powers, attenuated = _z118_parse_power(
                    power_raw,
                    port,
                    working_ids,
                )
                description = _z118_parse_desc(desc_raw)

                working = counts["working"]
                att_count = len(attenuated)
                good_count = max(0, working - att_count)

                pct_oper = _z118_pct(good_count, working)
                pct_att = _z118_pct(att_count, working)

                state_code, state_obs = _z118_state_observation(counts)

                if state_code is not None:
                    status = state_code
                    observation = state_obs
                elif working <= 0:
                    status = "CAIDA"
                    observation = "TRK Caida"
                elif pct_oper > _Z118_OPER_PCT:
                    status = "OPERATIVA"
                    observation = "TRK Operativa"
                else:
                    status = "ATENUADA"
                    observation = "TRK Atenuada"

                avg_power = (
                    round(sum(powers.values()) / len(powers), 3)
                    if powers else None
                )

                rows.append({
                    "port": port,
                    "pon": port,
                    "description": description,
                    "trunk": description,
                    "id_port": description,
                    "onus": working,
                    "working": working,
                    "registered_onus": counts["registered"],
                    "dyinggasp": counts["dyinggasp"],
                    "los": counts["los"],
                    "attenuated": att_count,
                    "power_attenuated": att_count,
                    "power_samples": len(powers),
                    "avg_power": avg_power,
                    "avg_rx_dbm": avg_power,
                    "pct_operativa": pct_oper,
                    "pct_atenuada": pct_att,
                    "status": status,
                    "estado": status,
                    "observation": observation,
                    "trk_status": observation,
                    "rx_threshold_dbm": _Z118_RX_ATT_DBM,
                    "seconds": round(
                        _z118_time.monotonic() - port_started,
                        2,
                    ),
                })

            except Exception as exc:
                errors.append({
                    "port": port,
                    "error": f"{type(exc).__name__}:{exc}",
                })

    totals = {
        "ports": len(rows),
        "onus": sum(row["onus"] for row in rows),
        "working": sum(row["working"] for row in rows),
        "online": sum(row["working"] for row in rows),
        "offline": sum(
            row["dyinggasp"] + row["los"] for row in rows
        ),
        "dyinggasp": sum(row["dyinggasp"] for row in rows),
        "los": sum(row["los"] for row in rows),
        "attenuated": sum(row["attenuated"] for row in rows),
        "operativa": sum(1 for row in rows if row["status"] == "OPERATIVA"),
        "atenuada": sum(1 for row in rows if row["status"] == "ATENUADA"),
        "caida": sum(1 for row in rows if row["status"] == "CAIDA"),
        "other": 0,
    }

    return {
        "ok": len(errors) == 0,
        "fast": False,
        "complete": len(errors) == 0,
        "partial": len(errors) > 0,
        "cache_hit": False,
        "cache_age_s": 0.0,
        "deduplicated": False,
        "strategy": "zte_specific_real_params_v118",
        "aggregate": False,
        "olt": olt.get("olt") or payload.get("olt") or "",
        "ip": olt.get("ip") or payload.get("ip") or "",
        "vendor": "ZTE",
        "mode": "specific",
        "elapsed_s": round(_z118_time.monotonic() - started, 2),
        "discovered_ports": len(ports),
        "completed_ports": len(rows),
        "remaining_ports": len(errors),
        "totals": totals,
        "results": rows,
        "errors": errors,
        "parameters": {
            "onus_display": "working",
            "rx_attenuated_dbm": _Z118_RX_ATT_DBM,
            "pct_denominator": "working",
            "trk_operativa_pct_gt": _Z118_OPER_PCT,
            "dyinggasp_los": "separate_observation",
        },
    }

_vm_ftth_before_z118 = diagnosticar_vm_ftth

def diagnosticar_vm_ftth(payload):
    try:
        specific = _z118_specific(payload)
    except Exception:
        specific = None

    if specific is not None:
        return specific

    return _vm_ftth_before_z118(payload)

# VM_FTTH_ZTE_SPECIFIC_REAL_PARAMS_V11_8_END

# VM_FTTH_HUAWEI_SPECIFIC_REAL_PARAMS_V11_9_START
import re as _h119_re
import time as _h119_time
from datetime import datetime as _h119_datetime
from datetime import timedelta as _h119_timedelta

_H119_RX_ATT_DBM = -27.0
_H119_OPER_PCT = 85.0
_H119_MAX_AGE_DAYS = 30

_H119_STATE_RE = _h119_re.compile(
    r"^\s*(\d+)\s+"
    r"(online|offline)\s+"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}|-)\s+"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}|-)\s+"
    r"(.+?)\s*$",
    _h119_re.I,
)

_H119_POWER_RE = _h119_re.compile(
    r"^\s*(\d+)\s+.*?\s+"
    r"(-?\d+(?:\.\d+)?|-)\/"
    r"(-?\d+(?:\.\d+)?|-)\s+"
    r"(.+?)\s*$",
    _h119_re.I,
)


def _h119_clean(value):
    return "".join(
        ch
        for ch in str(value or "")
        if ch == "\t" or ord(ch) >= 32
    ).strip()


def _h119_pct(num, den):
    if den <= 0:
        return 0.0
    return round((num * 100.0) / den, 2)


def _h119_dt(value):
    text = str(value or "").strip()
    if not text or text == "-":
        return None

    try:
        return _h119_datetime.strptime(
            text,
            "%Y-%m-%d %H:%M:%S",
        )
    except Exception:
        return None


def _h119_parse_desc(text, port):
    frame, slot, pon = port.split("/")

    pattern = _h119_re.compile(
        r"^\s*"
        + _h119_re.escape(frame)
        + r"\s*/\s*"
        + _h119_re.escape(slot)
        + r"\s*/\s*"
        + _h119_re.escape(pon)
        + r"\s+\S+\s+(.+?)\s*$",
        _h119_re.I,
    )

    for line in str(text or "").splitlines():
        match = pattern.match(_h119_clean(line))
        if match:
            return match.group(1).strip()

    return ""


def _h119_parse_summary(text):
    states = {}
    powers = {}

    for line in str(text or "").splitlines():
        value = _h119_clean(line)

        if not value:
            continue

        state_match = _H119_STATE_RE.match(value)

        if state_match:
            ont_id = int(state_match.group(1))

            states[ont_id] = {
                "state": state_match.group(2).lower(),
                "last_up": state_match.group(3),
                "last_down": state_match.group(4),
                "down_cause": state_match.group(5).strip(),
            }
            continue

        power_match = _H119_POWER_RE.match(value)

        if power_match:
            ont_id = int(power_match.group(1))
            rx_raw = power_match.group(2)
            tx_raw = power_match.group(3)

            powers[ont_id] = {
                "rx_dbm": float(rx_raw) if rx_raw != "-" else None,
                "tx_dbm": float(tx_raw) if tx_raw != "-" else None,
                "description": power_match.group(4).strip(),
            }

    rows = []

    for ont_id in sorted(set(states) | set(powers)):
        state = states.get(ont_id, {})
        power = powers.get(ont_id, {})

        up_dt = _h119_dt(state.get("last_up"))
        down_dt = _h119_dt(state.get("last_down"))

        changes = [
            item
            for item in (up_dt, down_dt)
            if item is not None
        ]

        last_change = max(changes) if changes else None

        rows.append({
            "ont_id": ont_id,
            "state": state.get("state"),
            "last_up": state.get("last_up"),
            "last_down": state.get("last_down"),
            "last_change": last_change,
            "down_cause": state.get("down_cause"),
            "rx_dbm": power.get("rx_dbm"),
            "tx_dbm": power.get("tx_dbm"),
            "description": power.get("description"),
        })

    return rows


def _h119_specific(payload):
    # VM_FTTH_DISPATCH_GUARD_V11_12_HUAWEI
    _h119_fast_raw = payload.get("fast")
    _h119_fast_requested = (
        _h119_fast_raw is True
        or str(_h119_fast_raw or "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    _h119_mode = str(payload.get("mode") or "").strip().lower()
    if (
        _h119_fast_requested
        or _h119_mode in (
            "fast",
            "quick",
            "barrido",
            "barrido_rapido",
            "barrido rapido",
        )
    ):
        return None
    raw_ports = payload.get("ports") or []
    ports = []

    for raw in raw_ports:
        value = str(raw or "").strip()

        if not _h119_re.fullmatch(r"\d+/\d+/\d+", value):
            continue

        if value not in ports:
            ports.append(value)

    if not ports:
        return None

    olt = _resolve_olt(payload)

    vendor = str(
        payload.get("vendor")
        or olt.get("vendor")
        or ""
    ).upper()

    if vendor == "AUTO":
        vendor = str(olt.get("vendor") or "").upper()

    if vendor not in ("HUAWEI", "MA5800", "HAC"):
        return None

    started = _h119_time.monotonic()
    now = _h119_datetime.now()
    cutoff = now - _h119_timedelta(days=_H119_MAX_AGE_DAYS)

    user, password = _credentials("HUAWEI")

    rows_out = []
    errors = []

    with SSHJumpSession(
        olt["ip"],
        user,
        password,
        connect_timeout=12,
    ) as session:

        for command in ("enable", "config", "scroll", "undo smart"):
            try:
                session.execute(
                    command,
                    timeout=8,
                    idle_timeout=0.7,
                )
            except Exception:
                pass

        for port in ports:
            port_started = _h119_time.monotonic()

            try:
                desc_raw = session.execute(
                    "display port desc " + port,
                    timeout=15,
                    idle_timeout=1.0,
                )

                summary_raw = session.execute(
                    "display ont info summary " + port,
                    timeout=35,
                    idle_timeout=1.7,
                )

                parsed = _h119_parse_summary(summary_raw)
                description = _h119_parse_desc(desc_raw, port)

                registered_total = len(parsed)

                registered_online = sum(
                    1
                    for row in parsed
                    if row.get("state") == "online"
                )

                registered_offline = sum(
                    1
                    for row in parsed
                    if row.get("state") == "offline"
                )

                selected = [
                    row
                    for row in parsed
                    if (
                        row.get("state") == "online"
                        and row.get("rx_dbm") is not None
                        and row.get("last_change") is not None
                        and row["last_change"] >= cutoff
                    )
                ]

                attenuated = [
                    row
                    for row in selected
                    if row["rx_dbm"] <= _H119_RX_ATT_DBM
                ]

                valid_count = len(selected)
                att_count = len(attenuated)
                good_count = max(0, valid_count - att_count)

                pct_oper = _h119_pct(good_count, valid_count)
                pct_att = _h119_pct(att_count, valid_count)

                if registered_total > 0 and registered_online == 0:
                    status = "CAIDA"
                    observation = "TRK Caida"
                elif valid_count <= 0:
                    status = "OTRO"
                    observation = "SIN DATOS VALIDOS"
                elif pct_oper > _H119_OPER_PCT:
                    status = "OPERATIVA"
                    observation = "TRK Operativa"
                else:
                    status = "ATENUADA"
                    observation = "TRK Atenuada"

                rx_values = [
                    row["rx_dbm"]
                    for row in selected
                    if row.get("rx_dbm") is not None
                ]

                avg_power = (
                    round(sum(rx_values) / len(rx_values), 3)
                    if rx_values
                    else None
                )

                rows_out.append({
                    "port": port,
                    "pon": port,
                    "description": description,
                    "trunk": description,
                    "id_port": description,
                    "onus": valid_count,
                    "working": valid_count,
                    "online": valid_count,
                    "registered_onus": registered_total,
                    "registered_online": registered_online,
                    "registered_offline": registered_offline,
                    "offline": 0,
                    "dyinggasp": 0,
                    "los": 0,
                    "attenuated": att_count,
                    "power_attenuated": att_count,
                    "power_samples": valid_count,
                    "avg_power": avg_power,
                    "avg_rx_dbm": avg_power,
                    "pct_operativa": pct_oper,
                    "pct_atenuada": pct_att,
                    "status": status,
                    "estado": status,
                    "observation": observation,
                    "trk_status": observation,
                    "rx_threshold_dbm": _H119_RX_ATT_DBM,
                    "max_last_change_days": _H119_MAX_AGE_DAYS,
                    "seconds": round(
                        _h119_time.monotonic() - port_started,
                        2,
                    ),
                })

            except Exception as exc:
                errors.append({
                    "port": port,
                    "error": f"{type(exc).__name__}:{exc}",
                })

    totals = {
        "ports": len(rows_out),
        "onus": sum(row["onus"] for row in rows_out),
        "working": sum(row["working"] for row in rows_out),
        "online": sum(row["online"] for row in rows_out),
        "offline": 0,
        "dyinggasp": 0,
        "los": 0,
        "attenuated": sum(row["attenuated"] for row in rows_out),
        "operativa": sum(
            1 for row in rows_out
            if row["status"] == "OPERATIVA"
        ),
        "atenuada": sum(
            1 for row in rows_out
            if row["status"] == "ATENUADA"
        ),
        "caida": sum(
            1 for row in rows_out
            if row["status"] == "CAIDA"
        ),
        "other": sum(
            1 for row in rows_out
            if row["status"] == "OTRO"
        ),
    }

    return {
        "ok": len(errors) == 0,
        "fast": False,
        "complete": len(errors) == 0,
        "partial": len(errors) > 0,
        "cache_hit": False,
        "cache_age_s": 0.0,
        "deduplicated": False,
        "strategy": "huawei_specific_summary_real_params_v119",
        "aggregate": False,
        "olt": olt.get("olt") or payload.get("olt") or "",
        "ip": olt.get("ip") or payload.get("ip") or "",
        "vendor": "HUAWEI",
        "mode": "specific",
        "elapsed_s": round(_h119_time.monotonic() - started, 2),
        "discovered_ports": len(ports),
        "completed_ports": len(rows_out),
        "remaining_ports": len(errors),
        "totals": totals,
        "results": rows_out,
        "errors": errors,
        "parameters": {
            "client_filter": "online+rx+last_change<=30d",
            "rx_attenuated_dbm": _H119_RX_ATT_DBM,
            "pct_denominator": "valid_filtered_online_onts",
            "trk_operativa_pct_gt": _H119_OPER_PCT,
            "summary_command": "display ont info summary <port>",
        },
    }
_vm_ftth_before_h119 = diagnosticar_vm_ftth

def diagnosticar_vm_ftth(payload):
    try:
        specific = _h119_specific(payload)
    except Exception:
        specific = None

    if specific is not None:
        return specific

    return _vm_ftth_before_h119(payload)
# VM_FTTH_HUAWEI_SPECIFIC_REAL_PARAMS_V11_9_END
