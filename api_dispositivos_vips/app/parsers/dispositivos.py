import re
from typing import Any


NUMBER = r"[-+]?\d+(?:[.,]\d+)?"


def _number(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value.strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def parse_zte_port(output: str) -> str:
    match = re.search(r"gpon_onu-([A-Za-z0-9_./:-]+)", output, re.IGNORECASE)
    if not match:
        raise ValueError("No se encontró el puerto de la ONT ZTE.")
    return match.group(1)


def parse_zte_power(output: str) -> dict[str, float]:
    up = re.search(rf"^\s*up\s+Rx\s*:\s*({NUMBER}|no signal)\s+Tx\s*:\s*({NUMBER})", output, re.I | re.M)
    down = re.search(rf"^\s*down\s+Tx\s*:\s*({NUMBER})\s+Rx\s*:\s*({NUMBER}|no signal)", output, re.I | re.M)
    if not up or not down:
        raise ValueError("No fue posible interpretar las potencias ZTE.")
    return {
        "olt_rx": _number(up.group(1)),
        "ont_tx": _number(up.group(2)),
        "olt_tx": _number(down.group(1)),
        "ont_rx": _number(down.group(2)),
    }


def parse_zte_traffic(output: str) -> dict[str, int]:
    input_match = re.search(r"^\s*Input rate\s*:\s*(\d+)", output, re.I | re.M)
    output_match = re.search(r"^\s*Output rate\s*:\s*(\d+)", output, re.I | re.M)
    if not input_match or not output_match:
        raise ValueError("No fue posible interpretar el tráfico ZTE.")

    # Conserva el contrato del módulo anterior: se cruzan input/output y se
    # convierten octetos por segundo a bits por segundo.
    return {
        "input": int(output_match.group(1)) * 8,
        "output": int(input_match.group(1)) * 8,
    }


def parse_zte_state(output: str) -> dict[str, str]:
    state = re.search(r"^\s*Phase state\s*:\s*(\S+)", output, re.I | re.M)
    uptime = re.search(r"^\s*Online Duration\s*:\s*(.+?)\s*$", output, re.I | re.M)
    if not state:
        raise ValueError("No fue posible interpretar el estado ZTE.")
    return {"estado": state.group(1).strip(), "uptime": uptime.group(1).strip() if uptime else ""}


def parse_huawei_port(output: str) -> dict[str, Any]:
    patterns = {
        "fsp": r"^\s*F/S/P\s*:\s*(\d+)/(\d+)/(\d+)",
        "ont": r"^\s*ONT-ID\s*:\s*(\d+)",
        "estado": r"^\s*Run state\s*:\s*(\S+)",
        "uptime": r"^\s*ONT online duration\s*:\s*(.+?)\s*$",
    }
    fsp = re.search(patterns["fsp"], output, re.I | re.M)
    ont = re.search(patterns["ont"], output, re.I | re.M)
    state = re.search(patterns["estado"], output, re.I | re.M)
    uptime = re.search(patterns["uptime"], output, re.I | re.M)
    if not fsp or not ont:
        raise ValueError("No se encontró el puerto o ID de la ONT Huawei.")
    return {
        "frame": int(fsp.group(1)),
        "slot": int(fsp.group(2)),
        "puerto": int(fsp.group(3)),
        "ont": int(ont.group(1)),
        "estado": state.group(1).strip() if state else "REVISAR",
        "uptime": uptime.group(1).strip() if uptime else "",
    }


def parse_huawei_power(output: str) -> dict[str, float]:
    ont_rx = re.search(rf"^\s*Rx optical power\(dBm\)\s*:\s*({NUMBER})", output, re.I | re.M)
    ont_tx = re.search(rf"^\s*Tx optical power\(dBm\)\s*:\s*({NUMBER})", output, re.I | re.M)
    olt_rx = re.search(rf"^\s*OLT Rx ONT optical power\(dBm\)\s*:\s*({NUMBER}|no signal)", output, re.I | re.M)
    if not ont_rx or not ont_tx or not olt_rx:
        raise ValueError("No fue posible interpretar las potencias Huawei.")
    return {
        "olt_rx": _number(olt_rx.group(1)),
        "olt_tx": 0.0,
        "ont_rx": _number(ont_rx.group(1)),
        "ont_tx": _number(ont_tx.group(1)),
    }


def parse_huawei_traffic(output: str) -> dict[str, int]:
    up = re.search(r"^\s*Up traffic \(kbps\)\s*:\s*(\d+)", output, re.I | re.M)
    down = re.search(r"^\s*Down traffic \(kbps\)\s*:\s*(\d+)", output, re.I | re.M)
    if not up or not down:
        raise ValueError("No fue posible interpretar el tráfico Huawei.")
    return {"input": int(down.group(1)) * 1000, "output": int(up.group(1)) * 1000}


def normalize_status(value: str) -> str:
    status = value.strip().lower()
    if status in {"working", "online", "up", "active", "activo", "operativo"}:
        return "OPERATIVO"
    if status in {"offline", "los", "dying-gasp", "down", "caido", "caído"}:
        return "CAIDO"
    if status in {"error", "fallo"}:
        return "ERROR"
    return "REVISAR"
