import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.paths import DATA_DIR, DISPOSITIVOS_DATA_DIR
from app.services.csv_reader import read_csv_rows


ACTUAL_JSON = DISPOSITIVOS_DATA_DIR / "actual.json"
HISTORICO_CSV = DATA_DIR / "historico_vips.csv"


def _to_number(value: Any) -> Any:
    if value in (None, ""):
        return value
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return value


def _vendor(row: dict[str, Any]) -> str:
    current = str(row.get("vendor") or "").strip().upper()
    if current:
        return current

    olt = str(row.get("olt") or "").strip().upper()
    if olt.startswith("ZAC-"):
        return "ZTE"
    if olt.startswith("HAC-"):
        return "HUAWEI"
    return "DESCONOCIDO"


def _status(row: dict[str, Any]) -> str:
    current = str(row.get("estado_normalizado") or row.get("estado") or "").strip().upper()
    if current in {"ONLINE", "UP", "WORKING", "ACTIVO", "OPERATIVO"}:
        return "OPERATIVO"
    if current in {"OFFLINE", "DOWN", "LOS", "CAIDO", "CAÍDO"}:
        return "CAIDO"
    if current in {"ERROR", "FALLO"}:
        return "ERROR"
    return current or "REVISAR"


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["vendor"] = _vendor(normalized)
    normalized["estado_normalizado"] = _status(normalized)

    for field in ("olt_rx", "olt_tx", "ont_rx", "ont_tx", "input", "output"):
        normalized[field] = _to_number(normalized.get(field))

    return normalized


def _load_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("rows") or payload.get("items") or []
        return rows if isinstance(rows, list) else []
    return []


def _latest_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    date_field = next(
        (field for field in ("fecha_consulta", "fecha") if any(row.get(field) for row in rows)),
        None,
    )
    if not date_field:
        return rows

    latest = max(str(row.get(date_field) or "") for row in rows)
    return [row for row in rows if str(row.get(date_field) or "") == latest]


def list_devices() -> dict[str, Any]:
    if ACTUAL_JSON.is_file():
        rows = _load_json(ACTUAL_JSON)
        source = ACTUAL_JSON
    else:
        rows = _latest_batch(read_csv_rows(HISTORICO_CSV))
        source = HISTORICO_CSV

    data = [_normalize(row) for row in rows]
    source_date = datetime.fromtimestamp(source.stat().st_mtime).isoformat(timespec="seconds") if source.is_file() else None

    return {
        "ok": True,
        "source": "atlas_backend",
        "estado_datos": "disponible" if source.is_file() else "sin_datos",
        "total": len(data),
        "fecha_consulta": source_date,
        "data": data,
    }


def summarize_devices() -> dict[str, Any]:
    result = list_devices()
    rows = result["data"]
    return {
        "ok": True,
        "source": "atlas_backend",
        "total": len(rows),
        "operativos": sum(row["estado_normalizado"] == "OPERATIVO" for row in rows),
        "caidos": sum(row["estado_normalizado"] == "CAIDO" for row in rows),
        "revisar": sum(row["estado_normalizado"] == "REVISAR" for row in rows),
        "errores": sum(row["estado_normalizado"] == "ERROR" for row in rows),
        "zte": sum(row["vendor"] == "ZTE" for row in rows),
        "huawei": sum(row["vendor"] == "HUAWEI" for row in rows),
        "fecha_consulta": result["fecha_consulta"],
    }
