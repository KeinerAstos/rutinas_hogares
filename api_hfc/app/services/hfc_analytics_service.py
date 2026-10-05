import base64
import json
from typing import Any, Dict, Optional

import paramiko

from app.core.paths import HFC_ANALYTICS_SSH_KEYS
from app.config.hfc_endpoints_settings import get_hfc_analytics_settings


FINAL_VIEW = "hfc_node_operational_current"


def _ssh_key() -> str:
    for key in HFC_ANALYTICS_SSH_KEYS:
        if key.exists() and key.is_file():
            return str(key)

    raise RuntimeError(
        "No se encontro la llave SSH de noc_cable."
    )


def _remote_request(
    action: str,
    **kwargs: Any,
) -> Dict[str, Any]:

    cfg = get_hfc_analytics_settings()

    payload = {
        "action": action,
        "db": cfg.remote_db,
        **kwargs,
    }

    encoded_payload = base64.b64encode(
        json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")
    ).decode("ascii")

    remote_python = r"""
import base64
import json
import sqlite3
import sys

req = json.loads(
    base64.b64decode(
        sys.argv[2]
    ).decode("utf-8")
)

db = req["db"]
action = req["action"]

con = sqlite3.connect(db)
con.row_factory = sqlite3.Row


def scalar(sql, params=()):
    row = con.execute(
        sql,
        params,
    ).fetchone()

    if row is None:
        return None

    return row[0]


def rows(sql, params=()):
    return [
        dict(r)
        for r in con.execute(
            sql,
            params,
        ).fetchall()
    ]


exists = scalar(
    "SELECT COUNT(*) "
    "FROM sqlite_master "
    "WHERE type IN ('table','view') "
    "AND name='hfc_node_operational_snapshot'"
)

if not exists:
    raise RuntimeError(
        "No existe hfc_node_operational_snapshot."
    )


if action == "summary":
    # HFC_ANALYTICS_SUMMARY_TEMPBASE_V33
    # Materializar una sola vez la vista costosa para evitar multiples scans.
    con.execute("DROP TABLE IF EXISTS temp._hfc_summary_base")
    con.execute(
        "CREATE TEMP TABLE _hfc_summary_base AS "
        "SELECT tecnologia, cmts, hogares, fecha_hfc_auto, estado "
        "FROM hfc_node_operational_snapshot"
    )
    con.execute("CREATE INDEX IF NOT EXISTS temp.idx_hfc_summary_tecnologia ON _hfc_summary_base(tecnologia)")
    con.execute("CREATE INDEX IF NOT EXISTS temp.idx_hfc_summary_estado ON _hfc_summary_base(estado)")

    result = {
        "ok": True,
        "source": "HFC_ANALYTICS_LINUX",
        "view": "hfc_node_operational_current",

        "cmts_total": scalar(
            "SELECT COUNT(DISTINCT cmts) "
            "FROM _hfc_summary_base"
        ),

        "cmts_casa": scalar(
            "SELECT COUNT(DISTINCT cmts) "
            "FROM _hfc_summary_base "
            "WHERE tecnologia='CASA'"
        ),

        "cmts_arris": scalar(
            "SELECT COUNT(DISTINCT cmts) "
            "FROM _hfc_summary_base "
            "WHERE tecnologia='ARRIS'"
        ),

        "nodes_total": scalar(
            "SELECT COUNT(*) "
            "FROM _hfc_summary_base"
        ),

        "nodes_casa": scalar(
            "SELECT COUNT(*) "
            "FROM _hfc_summary_base "
            "WHERE tecnologia='CASA'"
        ),

        "nodes_arris": scalar(
            "SELECT COUNT(*) "
            "FROM _hfc_summary_base "
            "WHERE tecnologia='ARRIS'"
        ),

        "homes_total": scalar(
            "SELECT COALESCE(SUM(hogares),0) "
            "FROM _hfc_summary_base"
        ),

        "latest_hfc_auto": scalar(
            "SELECT MAX(fecha_hfc_auto) "
            "FROM _hfc_summary_base"
        ),

        "states": rows(
            "SELECT estado, COUNT(*) AS nodes "
            "FROM _hfc_summary_base "
            "GROUP BY estado "
            "ORDER BY nodes DESC"
        ),
    }


elif action == "cmts":

    technology = str(
        req.get("technology") or ""
    ).strip().upper()

    sql = (
        "SELECT "
        "tecnologia, "
        "cmts, "
        "COUNT(*) AS nodes, "
        "COALESCE(SUM(hogares),0) AS hogares, "
        "MIN(snr_up) AS snr_up_min, "
        "MAX(fec_pct) AS fec_pct_max, "
        "MAX(ufec_pct) AS ufec_pct_max, "
        "MAX(sumatoria) AS sumatoria_max, "
        "SUM(CASE WHEN estado='AFECTACION_ALTA' THEN 1 ELSE 0 END) AS afectacion_alta, "
        "SUM(CASE WHEN estado='AFECTACION' THEN 1 ELSE 0 END) AS afectacion, "
        "SUM(CASE WHEN estado='OBSERVACION' THEN 1 ELSE 0 END) AS observacion "
        "FROM hfc_node_operational_snapshot "
    )

    params = []

    if technology:
        sql += "WHERE tecnologia=? "
        params.append(technology)

    sql += (
        "GROUP BY tecnologia,cmts "
        "ORDER BY tecnologia,cmts"
    )

    data = rows(
        sql,
        params,
    )

    result = {
        "ok": True,
        "source": "HFC_ANALYTICS_LINUX",
        "view": "hfc_node_operational_current",
        "count": len(data),
        "items": data,
    }


elif action == "nodes":

    technology = str(
        req.get("technology") or ""
    ).strip().upper()

    cmts = str(
        req.get("cmts") or ""
    ).strip()

    search = str(
        req.get("search") or ""
    ).strip()

    limit = int(
        req.get("limit") or 500
    )

    offset = int(
        req.get("offset") or 0
    )

    limit = max(
        1,
        min(limit, 5000)
    )

    offset = max(
        0,
        offset
    )

    where = []
    params = []

    if technology:
        where.append(
            "tecnologia=?"
        )
        params.append(
            technology
        )

    if cmts:
        where.append(
            "cmts=?"
        )
        params.append(
            cmts
        )

    if search:
        where.append(
            "("
            "nodo LIKE ? "
            "OR cmts LIKE ? "
            "OR zona LIKE ? "
            "OR estado LIKE ? "
            "OR descripcion LIKE ?"
            ")"
        )

        q = "%" + search + "%"

        params.extend([
            q,
            q,
            q,
            q,
            q,
        ])

    sql = (
        "SELECT "
        "tecnologia, "
        "nodo, "
        "cmts, "
        "zona, "
        "regional, "
        "pl_pct, "
        "pl_legacy_flag, "
        "pl_legacy_ports, "
        "fec_pct, "
        "ufec_pct, "
        "qpsk_pct, "
        "snr_up, "
        "signal_quality, "
        "qoe, "
        "sumatoria, "
        "hogares, "
        "active_oper, "
        "registered, "
        "offline, "
        "porc_offline, "
        "estado, "
        "estado_desde, "
        "dias_estado, "
        "exclusion, "
        "exclusion_types, "
        "exclusion_reasons, "
        "main_upstream, "
        "main_diagnosis, "
        "utilization_max, "
        "impair, "
        "estado_operativo_hfc_auto, "
        "descripcion, "
        "tipo_match_hfc_auto, "
        "estado_enriquecimiento, "
        "fecha_hfc_auto "
        "FROM hfc_node_operational_snapshot "
    )

    if where:
        sql += (
            "WHERE "
            + " AND ".join(where)
            + " "
        )

    sql += (
        "ORDER BY "
        "CASE estado "
        "WHEN 'AFECTACION_ALTA' THEN 0 "
        "WHEN 'AFECTACION' THEN 1 "
        "WHEN 'OBSERVACION' THEN 2 "
        "WHEN 'SIN_DATOS_FEC' THEN 3 "
        "WHEN 'SIN_DATOS_RF' THEN 4 "
        "WHEN 'SIN_MATCH_HFC_AUTO' THEN 5 "
        "ELSE 6 "
        "END, "
        "COALESCE(sumatoria,0) DESC, "
        "cmts, "
        "nodo "
        "LIMIT ? OFFSET ?"
    )

    params.append(limit)
    params.append(offset)

    data = rows(
        sql,
        params,
    )

    result = {
        "ok": True,
        "source": "HFC_ANALYTICS_LINUX",
        "view": "hfc_node_operational_current",
        "count": len(data),
        "limit": limit,
        "offset": offset,
        "items": data,
    }


elif action == "node":

    name = str(
        req.get("name") or ""
    ).strip()

    cmts = str(
        req.get("cmts") or ""
    ).strip()

    sql = (
        "SELECT * "
        "FROM hfc_node_operational_snapshot "
        "WHERE UPPER(nodo)=UPPER(?) "
    )

    params = [name]

    if cmts:
        sql += "AND cmts=? "
        params.append(cmts)

    sql += (
        "ORDER BY tecnologia,cmts"
    )

    data = rows(
        sql,
        params,
    )

    result = {
        "ok": True,
        "source": "HFC_ANALYTICS_LINUX",
        "view": "hfc_node_operational_current",
        "node": name,
        "count": len(data),
        "items": data,
    }


else:
    raise RuntimeError(
        "Accion HFC Analytics no soportada: "
        + str(action)
    )


con.close()

print(
    json.dumps(
        result,
        ensure_ascii=False,
    )
)
"""

    encoded_code = base64.b64encode(
        remote_python.encode("utf-8")
    ).decode("ascii")

    command = (
        "python3 -c "
        "\"import base64,sys;"
        "exec(base64.b64decode(sys.argv[1]))\" "
        f"{encoded_code} {encoded_payload}"
    )

    client = paramiko.SSHClient()

    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy()
    )

    try:
        client.connect(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.user,
            key_filename=_ssh_key(),
            timeout=cfg.connect_timeout,
            banner_timeout=cfg.connect_timeout,
            auth_timeout=cfg.connect_timeout,
            look_for_keys=False,
            allow_agent=False,
        )

        _, stdout, stderr = client.exec_command(
            command,
            timeout=cfg.command_timeout,
        )

        raw = stdout.read().decode(
            "utf-8",
            errors="replace",
        ).strip()

        err = stderr.read().decode(
            "utf-8",
            errors="replace",
        ).strip()

        if not raw:
            raise RuntimeError(
                err or
                "Linux no devolvio informacion HFC."
            )

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Linux devolvio respuesta HFC no JSON: "
                + raw[-2000:]
            ) from exc

    finally:
        client.close()


def resumen() -> Dict[str, Any]:
    return _remote_request(
        "summary"
    )


def cmts(
    technology: Optional[str] = None,
) -> Dict[str, Any]:

    return _remote_request(
        "cmts",
        technology=technology,
    )


def nodos(
    technology: Optional[str] = None,
    cmts_name: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> Dict[str, Any]:

    # HFC_TICKET_ENRICHMENT_V47
    from app.services.hfc_ticket_enrichment_service import enrich_nodes_payload
    payload = _remote_request(
        "nodes",
        technology=technology,
        cmts=cmts_name,
        search=search,
        limit=limit,
        offset=offset,
    )
    return enrich_nodes_payload(payload)


def nodo(
    name: str,
    cmts_name: Optional[str] = None,
) -> Dict[str, Any]:

    return _remote_request(
        "node",
        name=name,
        cmts=cmts_name,
    )


# ============================================================================
# ATLAS_HFC_ANALYTICS_XPERTRAK_QOE_V1
# Enriquecimiento READ-ONLY de HFC Analytics con Puntuación QoE XPERTrak.
# - No modifica estado_red.json ni hfc_unificado_actual.csv.
# - Usa el último PAR CENTRO/REGIONALES válido (archivos cercanos en tiempo).
# - Match seguro: descripción exacta primero, código exacto después.
# - Si hay valores QoE conflictivos, deja qoe=None (fail-safe).
# ============================================================================

import csv as _xpt_csv
import re as _xpt_re
import threading as _xpt_threading
import unicodedata as _xpt_unicodedata
from datetime import datetime as _xpt_datetime
from pathlib import Path as _XptPath

_XPT_QOE_DIR = _XptPath(
    r"C:\ProgramData\CentralNOC\Dashboard_Hogar\data\xpertrak_dashboard"
)
_XPT_PAIR_MAX_DELTA_SEC = 20 * 60
_XPT_QOE_CACHE_LOCK = _xpt_threading.Lock()
_XPT_QOE_CACHE_SIGNATURE = None
_XPT_QOE_CACHE = {
    "by_desc": {},
    "by_code": {},
    "files": [],
    "rows": 0,
}


def _xpt_norm(value):
    value = "" if value is None else str(value)
    value = _xpt_unicodedata.normalize("NFKD", value)
    value = "".join(
        c for c in value
        if not _xpt_unicodedata.combining(c)
    )
    value = _xpt_re.sub(r"\s+", " ", value.upper()).strip()
    return value


def _xpt_desc_core(value):
    value = _xpt_norm(value)
    value = _xpt_re.sub(r"^(NODO|CLUSTER)\s+", "", value)
    value = _xpt_re.sub(r"\s*\(\s*", " (", value)
    value = _xpt_re.sub(r"\s*\)\s*$", ")", value)
    value = _xpt_re.sub(r"\s*,\s*", ",", value)
    return value.strip()


def _xpt_code(value):
    value = _xpt_norm(value)

    match = _xpt_re.match(
        r"^(?:NODO|CLUSTER)\s+(.+?)(?:\s+\(|$)",
        value,
    )
    if match:
        return match.group(1).strip()

    return _xpt_re.sub(
        r"\s*\([^)]*\)\s*$",
        "",
        value,
    ).strip()


def _xpt_parse_file_stamp(path):
    match = _xpt_re.search(
        r"_(\d{8})_(\d{6})\.csv$",
        path.name,
        flags=_xpt_re.IGNORECASE,
    )
    if match:
        try:
            return _xpt_datetime.strptime(
                match.group(1) + match.group(2),
                "%Y%m%d%H%M%S",
            ).timestamp()
        except Exception:
            pass

    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def _xpt_select_latest_pair():
    centros = sorted(
        _XPT_QOE_DIR.glob("salud_diaria_centro_*.csv"),
        key=_xpt_parse_file_stamp,
        reverse=True,
    )
    regionales = sorted(
        _XPT_QOE_DIR.glob("salud_diaria_regionales_*.csv"),
        key=_xpt_parse_file_stamp,
        reverse=True,
    )

    best = None
    best_score = -1.0

    for centro in centros[:30]:
        tc = _xpt_parse_file_stamp(centro)

        for regional in regionales[:30]:
            tr = _xpt_parse_file_stamp(regional)
            delta = abs(tc - tr)

            if delta > _XPT_PAIR_MAX_DELTA_SEC:
                continue

            score = max(tc, tr)
            if score > best_score:
                best = (centro, regional)
                best_score = score

    return best


def _xpt_decode_csv(path):
    raw = path.read_bytes()

    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except Exception:
            pass

    raise RuntimeError(
        "XPERTRAK_QOE_CSV_DECODE_ERROR:" + str(path)
    )


def _xpt_qoe_value(value):
    value = "" if value is None else str(value).strip()

    if not value:
        return None

    try:
        numeric = float(value)
        if numeric.is_integer():
            return int(numeric)
        return numeric
    except Exception:
        return None


def _xpt_build_safe_index():
    pair = _xpt_select_latest_pair()

    if not pair:
        return {
            "signature": ("NO_PAIR",),
            "by_desc": {},
            "by_code": {},
            "files": [],
            "rows": 0,
        }

    signature_parts = []
    for path in pair:
        stat = path.stat()
        signature_parts.append(
            (str(path), stat.st_size, stat.st_mtime_ns)
        )

    signature = tuple(signature_parts)

    raw_by_desc = {}
    raw_by_code = {}
    rows_count = 0

    for path in pair:
        text = _xpt_decode_csv(path)
        reader = _xpt_csv.DictReader(text.splitlines())

        for row in reader:
            rows_count += 1
            raw_node = row.get("Nodo", "")
            qoe = _xpt_qoe_value(row.get("Puntuación"))

            if qoe is None:
                continue

            desc = _xpt_desc_core(raw_node)
            code = _xpt_code(raw_node)

            if desc:
                raw_by_desc.setdefault(desc, set()).add(qoe)

            if code:
                raw_by_code.setdefault(code, set()).add(qoe)

    by_desc = {
        key: next(iter(values))
        for key, values in raw_by_desc.items()
        if len(values) == 1
    }
    by_code = {
        key: next(iter(values))
        for key, values in raw_by_code.items()
        if len(values) == 1
    }

    return {
        "signature": signature,
        "by_desc": by_desc,
        "by_code": by_code,
        "files": [str(p) for p in pair],
        "rows": rows_count,
    }


def _xpt_qoe_cache():
    global _XPT_QOE_CACHE_SIGNATURE
    global _XPT_QOE_CACHE

    pair = _xpt_select_latest_pair()

    if not pair:
        signature = ("NO_PAIR",)
    else:
        signature = tuple(
            (
                str(path),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in pair
        )

    if signature == _XPT_QOE_CACHE_SIGNATURE:
        return _XPT_QOE_CACHE

    with _XPT_QOE_CACHE_LOCK:
        if signature == _XPT_QOE_CACHE_SIGNATURE:
            return _XPT_QOE_CACHE

        built = _xpt_build_safe_index()
        _XPT_QOE_CACHE = {
            "by_desc": built["by_desc"],
            "by_code": built["by_code"],
            "files": built["files"],
            "rows": built["rows"],
        }
        _XPT_QOE_CACHE_SIGNATURE = built["signature"]

    return _XPT_QOE_CACHE


def _xpt_qoe_for_dashboard_row(row, cache):
    if not isinstance(row, dict):
        return None

    description = _xpt_desc_core(row.get("descripcion"))
    if description and description in cache["by_desc"]:
        return cache["by_desc"][description]

    code = _xpt_norm(row.get("nodo"))
    if code and code in cache["by_code"]:
        return cache["by_code"][code]

    return None


_HFC_ANALYTICS_NODOS_BASE_XPERTRAK_QOE_V1 = nodos


def nodos(*args, **kwargs):
    payload = _HFC_ANALYTICS_NODOS_BASE_XPERTRAK_QOE_V1(
        *args,
        **kwargs,
    )

    if not isinstance(payload, dict):
        return payload

    items = payload.get("items")
    if not isinstance(items, list):
        return payload

    cache = _xpt_qoe_cache()

    matched = 0
    for row in items:
        if not isinstance(row, dict):
            continue

        qoe = _xpt_qoe_for_dashboard_row(row, cache)
        row["qoe"] = qoe

        if qoe is not None:
            matched += 1

    payload["qoe_xpertrak"] = {
        "enabled": True,
        "matched_page": matched,
        "items_page": len(items),
        "source_files": cache.get("files", []),
        "source_rows": cache.get("rows", 0),
        "match_policy": "DESC_EXACT_THEN_CODE_EXACT_FAIL_SAFE",
    }

    return payload

# END_ATLAS_HFC_ANALYTICS_XPERTRAK_QOE_V1
