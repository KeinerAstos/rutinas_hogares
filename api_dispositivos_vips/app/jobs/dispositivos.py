import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo

from app.core.paths import DATA_DIR, DISPOSITIVOS_DATA_DIR, DISPOSITIVOS_INPUT
from app.infrastructure.ssh import SSHSession
from app.parsers.dispositivos import (
    normalize_status,
    parse_huawei_port,
    parse_huawei_power,
    parse_huawei_traffic,
    parse_zte_port,
    parse_zte_power,
    parse_zte_state,
    parse_zte_traffic,
)


ACTUAL_JSON = DISPOSITIVOS_DATA_DIR / "actual.json"
HISTORICO_CSV = DATA_DIR / "historico_vips.csv"
ESCALAMIENTO_ACTUAL_CSV = DATA_DIR / "casos_escalamiento_actual.csv"
ESCALAMIENTO_HISTORICO_CSV = DATA_DIR / "casos_escalamiento_historico.csv"
BOGOTA = ZoneInfo("America/Bogota")
FIELDS = [
    "fecha_consulta", "nombre", "vendor", "olt", "ip", "puerto", "serial",
    "estado", "estado_normalizado", "uptime", "olt_rx", "olt_tx", "ont_rx",
    "ont_tx", "input", "output", "consulta_ok", "error",
]


def _now() -> str:
    return datetime.now(BOGOTA).strftime("%Y-%m-%d %H:%M:%S")


def _workers() -> int:
    try:
        value = int(os.getenv("DISPOSITIVOS_MAX_WORKERS", "12"))
    except ValueError:
        value = 12
    return max(1, min(value, 32))


def _load_targets() -> list[dict[str, Any]]:
    if not DISPOSITIVOS_INPUT.is_file():
        raise FileNotFoundError(f"No existe el inventario de Dispositivos: {DISPOSITIVOS_INPUT}")
    payload = json.loads(DISPOSITIVOS_INPUT.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("El inventario de Dispositivos debe ser una lista JSON.")

    required = {"nombre", "vendor", "olt", "ip", "serial"}
    for index, target in enumerate(payload, start=1):
        missing = required.difference(target)
        if missing:
            raise ValueError(f"Registro {index} incompleto; faltan: {', '.join(sorted(missing))}")
    return payload


def _credentials(vendor: str) -> tuple[str, str, str]:
    prefix = vendor.upper()
    username = os.getenv(f"{prefix}_SSH_USER") or os.getenv("OLT_SSH_USER") or ""
    password = os.getenv(f"{prefix}_SSH_PASS") or os.getenv("OLT_SSH_PASS") or ""
    secret = os.getenv(f"{prefix}_SSH_SECRET") or os.getenv("OLT_SSH_SECRET") or ""
    if not username or not password:
        raise RuntimeError(f"Faltan credenciales SSH para {prefix} en el archivo .env.")
    return username, password, secret


def _base_result(target: dict[str, Any], fecha: str) -> dict[str, Any]:
    return {
        "fecha_consulta": fecha,
        "nombre": target["nombre"],
        "vendor": target["vendor"].upper(),
        "olt": target["olt"],
        "ip": target["ip"],
        "puerto": "",
        "serial": target["serial"],
        "estado": "",
        "estado_normalizado": "ERROR",
        "uptime": "",
        "olt_rx": 0.0,
        "olt_tx": 0.0,
        "ont_rx": 0.0,
        "ont_tx": 0.0,
        "input": 0,
        "output": 0,
        "consulta_ok": False,
        "error": "",
    }


def _query_zte(session: SSHSession, target: dict[str, Any], result: dict[str, Any]) -> None:
    port = parse_zte_port(session.execute(f"show gpon onu by sn {target['serial']}"))
    power = parse_zte_power(session.execute(f"show pon power attenuation gpon_onu-{port}"))
    traffic = parse_zte_traffic(session.execute(f"show interface gpon_onu-{port}"))
    state = parse_zte_state(session.execute(f"show gpon onu detail-info gpon_onu-{port}"))

    result.update(power, traffic, state)
    result["puerto"] = port


def _query_huawei(session: SSHSession, target: dict[str, Any], result: dict[str, Any]) -> None:
    port_data = parse_huawei_port(
        session.execute(
            f"display ont info by-sn {target['serial']} | include F/S/P|ONT-ID|Run.state|ONT.online.duration",
            timeout=40,
        )
    )
    session.execute("config", timeout=10)
    session.execute(f"interface gpon {port_data['frame']}/{port_data['slot']}", timeout=10)
    power = parse_huawei_power(
        session.execute(f"display ont optical-info {port_data['puerto']} {port_data['ont']}", timeout=40)
    )
    traffic = parse_huawei_traffic(
        session.execute(f"display ont traffic {port_data['puerto']} {port_data['ont']}", timeout=40)
    )

    result.update(power, traffic)
    result["puerto"] = (
        f"{port_data['frame']}/{port_data['slot']}/{port_data['puerto']} {port_data['ont']}"
    )
    result["estado"] = port_data["estado"]
    result["uptime"] = port_data["uptime"]


def _query_target(session: SSHSession, target: dict[str, Any], fecha: str) -> dict[str, Any]:
    result = _base_result(target, fecha)
    try:
        if target["vendor"].upper() == "ZTE":
            _query_zte(session, target, result)
        elif target["vendor"].upper() == "HUAWEI":
            _query_huawei(session, target, result)
        else:
            raise ValueError(f"Vendor no soportado: {target['vendor']}")

        result["estado_normalizado"] = normalize_status(result["estado"])
        result["consulta_ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _query_group(targets: list[dict[str, Any]], fecha: str) -> list[dict[str, Any]]:
    vendor = targets[0]["vendor"].upper()
    username, password, secret = _credentials(vendor)

    try:
        with SSHSession(targets[0]["ip"], username, password) as session:
            if vendor == "ZTE":
                session.execute("terminal length 0", timeout=10)
            elif vendor == "HUAWEI":
                session.enable(secret)
                session.execute("scroll", timeout=10)

            return [_query_target(session, target, fecha) for target in targets]
    except Exception as exc:
        failed = []
        for target in targets:
            result = _base_result(target, fecha)
            result["error"] = str(exc)
            failed.append(result)
        return failed


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]], *, append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.is_file() and path.stat().st_size > 0
    mode = "a" if append else "w"
    encoding = "utf-8" if append and exists else "utf-8-sig"
    with path.open(mode, encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def run_dispositivos_update() -> dict[str, Any]:
    targets = _load_targets()
    fecha = _now()
    rows: list[dict[str, Any]] = []

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for target in targets:
        key = (target["vendor"].upper(), target["ip"])
        groups.setdefault(key, []).append(target)

    with ThreadPoolExecutor(max_workers=_workers(), thread_name_prefix="atlas-vip") as executor:
        futures = [executor.submit(_query_group, group, fecha) for group in groups.values()]
        for future in as_completed(futures):
            rows.extend(future.result())

    order = {target["serial"]: index for index, target in enumerate(targets)}
    rows.sort(key=lambda row: order.get(row["serial"], 999999))
    payload = {"ok": True, "fecha_consulta": fecha, "total": len(rows), "data": rows}
    _write_json_atomic(ACTUAL_JSON, payload)
    _write_csv(HISTORICO_CSV, rows, append=True)

    escalamiento = [row for row in rows if row["consulta_ok"] and float(row["ont_rx"] or 0) <= -25.0]
    _write_csv(ESCALAMIENTO_ACTUAL_CSV, escalamiento, append=False)
    if escalamiento:
        _write_csv(ESCALAMIENTO_HISTORICO_CSV, escalamiento, append=True)

    exitosos = sum(bool(row["consulta_ok"]) for row in rows)
    return {
        "ok": exitosos == len(rows),
        "fecha_consulta": fecha,
        "total": len(rows),
        "exitosos": exitosos,
        "errores": len(rows) - exitosos,
        "casos_escalamiento": len(escalamiento),
        "max_workers": _workers(),
        "sesiones_ssh": len(groups),
    }
