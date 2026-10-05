# -*- coding: utf-8 -*-
"""
Servicio central de Operación para Chat DECO.

Fuentes:
- Incidentes activos: CSV vigente de Servicios Fijos.
- Bitácora Mesa de Ayuda: base actual consultada mediante PHP CLI.

No modifica registros. Todas las operaciones son de lectura.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.paths import (
    DASHBOARD_HOGAR_DATA_DIR,
    OPERATION_BRIDGE,
    PHP_EXECUTABLE,
)


INCIDENTS_DATA_DIR = DASHBOARD_HOGAR_DATA_DIR
PHP_BRIDGE = OPERATION_BRIDGE


def _normalizar(value: Any) -> str:
    text = str(value or "").strip().upper()

    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )

    return re.sub(r"\s+", " ", text)


def _normalizar_cabecera(value: Any) -> str:
    return re.sub(
        r"[^a-z0-9]+",
        "",
        _normalizar(value).lower(),
    )


HEADER_MAP = {
    "prioridad": "prioridad",
    "crearfecha": "fecha",
    "fechacrear": "fecha",
    "fechadecreacion": "fecha",
    "fechacreacion": "fecha",
    "mostrarid": "id",
    "id": "id",
    "identificador": "id",
    "estado": "estado",
    "nombrecompletodecliente": "cliente",
    "nombrecliente": "cliente",
    "cliente": "cliente",
    "resumen": "resumen",
    "nivel1decategoriaoperacional": "categoria_1",
    "nivel1categoriaoperacional": "categoria_1",
    "nivel1categoria": "categoria_1",
    "categorianivel1": "categoria_1",
    "nivel2decategoriaoperacional": "categoria_2",
    "nivel2categoriaoperacional": "categoria_2",
    "nivel2categoria": "categoria_2",
    "categorianivel2": "categoria_2",
    "nivel3decategoriaoperacional": "categoria_3",
    "nivel3categoriaoperacional": "categoria_3",
    "nivel3categoria": "categoria_3",
    "categorianivel3": "categoria_3",
    "flujodecreacion": "flujo",
    "flujocreacion": "flujo",
    "ubicacion": "ubicacion",
    "localizacion": "ubicacion",
}


def _cabecera_canonica(value: Any) -> str:
    key = _normalizar_cabecera(value)

    if key in HEADER_MAP:
        return HEADER_MAP[key]

    if "nivel1" in key and "categor" in key:
        return "categoria_1"

    if "nivel2" in key and "categor" in key:
        return "categoria_2"

    if "nivel3" in key and "categor" in key:
        return "categoria_3"

    if "flujo" in key and "creaci" in key:
        return "flujo"

    if "ubicaci" in key or "localiz" in key:
        return "ubicacion"

    return key


def _csv_vigente() -> Path:
    candidates = sorted(
        INCIDENTS_DATA_DIR.glob(
            "incidentes_activos_servicios_fijos_*.csv"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for path in candidates:
        if path.is_file() and path.stat().st_size > 200:
            return path

    base = (
        INCIDENTS_DATA_DIR
        / "incidentes_activos_servicios_fijos.csv"
    )

    if base.is_file():
        return base

    raise FileNotFoundError(
        "No se encontró el CSV vigente de incidentes activos."
    )


def _detectar_delimitador(sample: str) -> str:
    candidates = [",", ";", "\t", "|"]

    return max(
        candidates,
        key=lambda delimiter: sample.count(delimiter),
    )


def _detectar_tecnologia(record: dict[str, Any]) -> str:
    content = _normalizar(
        " ".join(
            [
                str(record.get("resumen", "")),
                str(record.get("categoria_1", "")),
                str(record.get("categoria_2", "")),
                str(record.get("categoria_3", "")),
                str(record.get("flujo", "")),
                str(record.get("ubicacion", "")),
            ]
        )
    )

    ftth_terms = (
        "FTTH",
        "GPON",
        "OLT",
        "PON",
        "ONU",
        "ONT",
        "LOSI",
        "FIBRA",
    )

    if any(term in content for term in ftth_terms):
        return "FTTH"

    hfc_terms = (
        "HFC",
        "CMTS",
        "CABLE MODEM",
        "DOCSIS",
        "SERVICE GROUP",
        "RFOG",
        "UPSTREAM",
        "DOWNSTREAM",
    )

    if any(term in content for term in hfc_terms):
        return "HFC"

    return "OTROS"


def _leer_incidentes() -> tuple[Path, list[dict[str, Any]]]:
    path = _csv_vigente()

    with path.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as file:
        first_line = file.readline()

        if not first_line:
            raise ValueError("El CSV de incidentes está vacío.")

        delimiter = _detectar_delimitador(first_line)
        file.seek(0)

        reader = csv.reader(
            file,
            delimiter=delimiter,
        )

        raw_headers = next(reader)
        headers = [
            _cabecera_canonica(header)
            for header in raw_headers
        ]

        records: list[dict[str, Any]] = []

        for row in reader:
            if not row or not any(str(value).strip() for value in row):
                continue

            if len(row) < len(headers):
                row.extend(
                    [""] * (len(headers) - len(row))
                )

            row = row[: len(headers)]
            record = dict(zip(headers, row))

            incident_id = str(
                record.get("id", "")
            ).strip()

            category_1 = _normalizar(
                record.get("categoria_1", "")
            )

            status = _normalizar(
                record.get("estado", "")
            )

            if not incident_id.upper().startswith("INC"):
                continue

            if "SERVICIOS FIJOS" not in category_1:
                continue

            if "CERRADO" in status or "CANCELADO" in status:
                continue

            record["tecnologia"] = _detectar_tecnologia(
                record
            )

            records.append(record)

    return path, records


def consultar_incidentes(
    mensaje: str,
) -> dict[str, Any]:
    path, records = _leer_incidentes()
    text = _normalizar(mensaje)

    incident_match = re.search(
        r"\bINC\d{5,20}\b",
        text,
    )

    if incident_match:
        incident_id = incident_match.group(0)

        item = next(
            (
                record
                for record in records
                if _normalizar(record.get("id")) == incident_id
            ),
            None,
        )

        if not item:
            return {
                "ok": False,
                "tipo_respuesta": "operacion_incidente_detalle",
                "codigo": "INCIDENTE_NO_ENCONTRADO",
                "respuesta": (
                    f"No encontré {incident_id} dentro del CSV "
                    "vigente de incidentes activos."
                ),
                "consulta": incident_id,
                "fuente": path.name,
            }

        return {
            "ok": True,
            "tipo_respuesta": "operacion_incidente_detalle",
            "codigo": "INCIDENTE_ACTIVO_ENCONTRADO",
            "respuesta": item,
            "consulta": incident_id,
            "fuente": {
                "tipo": "csv",
                "archivo": path.name,
                "actualizado_en": datetime.fromtimestamp(
                    path.stat().st_mtime
                ).isoformat(),
            },
        }

    filtered = list(records)

    if "FTTH" in text:
        filtered = [
            record
            for record in filtered
            if record.get("tecnologia") == "FTTH"
        ]

    if "CRITIC" in text:
        filtered = [
            record
            for record in filtered
            if "CRIT" in _normalizar(
                record.get("prioridad")
            )
        ]

    if "ALTA" in text or "ALTOS" in text:
        filtered = [
            record
            for record in filtered
            if "ALTA" in _normalizar(
                record.get("prioridad")
            )
        ]

    statuses = Counter(
        str(record.get("estado") or "Sin estado")
        for record in filtered
    )

    priorities = Counter(
        str(record.get("prioridad") or "Sin prioridad")
        for record in filtered
    )

    technologies = Counter(
        str(record.get("tecnologia") or "OTROS")
        for record in filtered
    )

    items = filtered[:20]

    return {
        "ok": True,
        "tipo_respuesta": "operacion_incidentes_activos",
        "codigo": "INCIDENTES_ACTIVOS_OK",
        "respuesta": {
            "total": len(filtered),
            "estados": dict(statuses.most_common()),
            "prioridades": dict(priorities.most_common()),
            "tecnologias": dict(technologies.most_common()),
            "items": items,
        },
        "fuente": {
            "tipo": "csv",
            "archivo": path.name,
            "actualizado_en": datetime.fromtimestamp(
                path.stat().st_mtime
            ).isoformat(),
            "tamano_bytes": path.stat().st_size,
        },
        "filtros": {
            "ftth": "FTTH" in text,
            "criticos": "CRITIC" in text,
            "altos": "ALTA" in text or "ALTOS" in text,
        },
    }


def _ejecutar_php(
    action: str,
    value: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    if not PHP_EXECUTABLE.is_file():
        raise FileNotFoundError(
            f"No se encontró PHP CLI: {PHP_EXECUTABLE}"
        )

    if not PHP_BRIDGE.is_file():
        raise FileNotFoundError(
            f"No se encontró el puente PHP: {PHP_BRIDGE}"
        )

    process = subprocess.run(
        [
            str(PHP_EXECUTABLE),
            str(PHP_BRIDGE),
            action,
            value,
            str(limit),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=40,
        check=False,
    )

    raw = process.stdout.strip()

    if not raw:
        raise RuntimeError(
            process.stderr.strip()
            or "El puente PHP no devolvió información."
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Respuesta PHP inválida: {raw[:500]}"
        ) from exc

    return payload


def consultar_bitacora(
    mensaje: str,
) -> dict[str, Any]:
    text = _normalizar(mensaje)

    ot_match = re.search(
        r"\bWO\d{13}\b",
        text,
    )

    if ot_match and "SEGUIMIENTO" in text:
        numero_ot = ot_match.group(0)
        payload = _ejecutar_php(
            "seguimientos_ot",
            numero_ot,
        )

        return {
            **payload,
            "tipo_respuesta": "operacion_bitacora_seguimientos",
            "codigo": (
                "BITACORA_SEGUIMIENTOS_OK"
                if payload.get("ok")
                else payload.get(
                    "codigo",
                    "BITACORA_SEGUIMIENTOS_ERROR",
                )
            ),
            "consulta": numero_ot,
            "fuente": "front_office_ntt",
        }

    if ot_match:
        numero_ot = ot_match.group(0)
        payload = _ejecutar_php(
            "buscar_ot",
            numero_ot,
        )

        return {
            **payload,
            "tipo_respuesta": "operacion_bitacora_ot",
            "codigo": (
                "BITACORA_OT_OK"
                if payload.get("ok")
                else payload.get(
                    "codigo",
                    "BITACORA_OT_ERROR",
                )
            ),
            "consulta": numero_ot,
            "fuente": "front_office_ntt",
        }

    payload = _ejecutar_php(
        "listar_ots",
        "",
        30,
    )

    rows = payload.get("data", [])
    active = [
        row
        for row in rows
        if str(row.get("estado_gestion", "")).upper()
        not in {"CERRADO", "CANCELADO"}
    ]

    overdue = [
        row
        for row in active
        if row.get("proximo_seguimiento")
        and str(row.get("proximo_seguimiento"))
        < datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]

    return {
        **payload,
        "tipo_respuesta": "operacion_bitacora_resumen",
        "codigo": (
            "BITACORA_OPERATIVA_OK"
            if payload.get("ok")
            else payload.get(
                "codigo",
                "BITACORA_OPERATIVA_ERROR",
            )
        ),
        "respuesta": {
            "consultadas": len(rows),
            "activas": len(active),
            "seguimientos_vencidos_estimados": len(overdue),
            "ots": rows,
        },
        "fuente": "front_office_ntt",
    }


def ejecutar(
    mensaje: str,
) -> dict[str, Any]:
    text = _normalizar(mensaje)

    if (
        "INCIDENTE" in text
        or re.search(r"\bINC\d{5,20}\b", text)
    ):
        return consultar_incidentes(mensaje)

    if (
        "BITACORA" in text
        or "MESA DE AYUDA" in text
        or "SEGUIMIENTO" in text
    ):
        return consultar_bitacora(mensaje)

    return {
        "ok": False,
        "tipo_respuesta": "operacion_no_reconocida",
        "codigo": "OPERACION_NO_RECONOCIDA",
        "respuesta": (
            "No identifiqué la consulta operativa. Usa, por ejemplo: "
            "'incidentes activos', 'buscar incidente INC...', "
            "'bitácora operativa', 'bitácora OT WO...' o "
            "'seguimientos bitácora OT WO...'."
        ),
    }


def health() -> dict[str, Any]:
    csv_path = None

    try:
        csv_path = _csv_vigente()
        csv_ok = True
    except Exception:
        csv_ok = False

    try:
        database = _ejecutar_php("health")
    except Exception as exc:
        database = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "ok": csv_ok and bool(database.get("ok")),
        "servicio": "operation_service",
        "modo": "solo_lectura",
        "incidentes": {
            "ok": csv_ok,
            "csv": str(csv_path) if csv_path else None,
        },
        "bitacora": database,
    }

