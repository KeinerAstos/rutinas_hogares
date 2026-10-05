from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any


# FTTH_ATP_SERIALS_V1_1


# FTTH_ATP_PROJECT_ROOT_FIX_V1
# atp_serials.py vive en app/services/ftth; parents[3] es el root del proyecto.
ROOT = Path(__file__).resolve().parents[3]
ATP_DATA_ROOT = ROOT / "data" / "ftth_atp"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def es_olt_atp(elemento: Any) -> bool:
    value = _clean(elemento).upper()

    if not value:
        return False

    return (
        ".ATP_" in value
        or "-ATP_" in value
        or ".ATP-" in value
        or "ATP_" in value
    )


def _normalizar_serial(value: Any) -> str:
    serial = _clean(value).upper()

    if not serial:
        return ""

    serial = re.sub(
        r"[^A-Z0-9]",
        "",
        serial,
    )

    if len(serial) < 8:
        return ""

    return serial


def _status_priority(status: Any) -> int:
    value = _clean(status).upper()

    if value in {
        "ONLINE",
        "WORKING",
        "UP",
        "ACTIVE",
    }:
        return 0

    if value in {
        "LOS",
        "OFFLINE",
        "DYING GASP",
        "DYING_GASP",
    }:
        return 1

    return 2


def buscar_seriales_atp(
    elemento: str,
    slot: Any,
    port: Any,
    limite: int = 3,
) -> dict[str, Any]:

    ne_name = _clean(elemento).upper()
    slot_value = _clean(slot)
    port_value = _clean(port)

    if not ne_name:
        return {
            "ok": False,
            "codigo": "ATP_SIN_ELEMENTO",
            "seriales": [],
        }

    if not slot_value or not port_value:
        return {
            "ok": False,
            "codigo": "ATP_SIN_SLOT_PORT",
            "seriales": [],
        }

    csv_files = sorted(
        ATP_DATA_ROOT.rglob("*.csv")
    )

    if not csv_files:
        return {
            "ok": False,
            "codigo": "ATP_DATA_NO_DISPONIBLE",
            "seriales": [],
        }

    encontrados = []
    seen = set()

    for csv_path in csv_files:

        with csv_path.open(
            "r",
            encoding="utf-8-sig",
            errors="replace",
            newline="",
        ) as handle:

            reader = csv.DictReader(handle)

            for row in reader:

                row_ne = _clean(
                    row.get("NE Name")
                ).upper()

                if row_ne != ne_name:
                    continue

                if _clean(row.get("Slot")) != slot_value:
                    continue

                if _clean(row.get("Port")) != port_value:
                    continue

                serial = _normalizar_serial(
                    row.get("MAC/SN")
                    or row.get("Authentication Value")
                )

                if not serial:
                    continue

                if serial in seen:
                    continue

                seen.add(serial)

                status = _clean(
                    row.get("Operational Status")
                )

                encontrados.append(
                    {
                        "onu": _clean(
                            row.get("ONU ID")
                        ),
                        "serial": serial,
                        "phase_state": status,
                        "run_state": status,
                        "criterio": (
                            "ATP_CSV_"
                            + (
                                status.upper()
                                if status
                                else "SIN_ESTADO"
                            )
                        ),
                        "operational_status": status,
                        "last_online_time": _clean(
                            row.get("Last Online Time")
                        ),
                        "last_offline_time": _clean(
                            row.get("Last Offline Time")
                        ),
                        "last_offline_reason": _clean(
                            row.get("Last Offline Reason")
                        ),
                        "archivo_fuente": csv_path.name,
                    }
                )

    encontrados.sort(
        key=lambda item: (
            _status_priority(
                item.get("operational_status")
            ),
            _clean(item.get("onu")),
        )
    )

    limite = max(
        1,
        min(int(limite or 3), 3),
    )

    seleccionados = encontrados[:limite]

    return {
        "ok": bool(seleccionados),
        "codigo": (
            "ATP_SERIALES_OK"
            if seleccionados
            else "ATP_SIN_SERIALES"
        ),
        "elemento_red": ne_name,
        "slot": slot_value,
        "port": port_value,
        "total_encontrados": len(encontrados),
        "seriales": seleccionados,
    }
