"""HFC Quick de ATLAS sobre la fuente HFC Analytics actual.

Las rutas públicas se conservan. La obtención de datos se realiza mediante
hfc_analytics_service y el puente SSH ya administrado por API HFC.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.services import hfc_analytics_service as analytics


_AFFECTED_STATES = {
    "AFECTACION_ALTA",
    "AFECTACION",
    "OBSERVACION",
}


def _clean(value: Any) -> str:
    return str(value or "").strip().upper()


def _error(
    codigo: str,
    error: str,
    **extra: Any,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "codigo": codigo,
        "error": error,
        "fuente": "HFC_ANALYTICS_LINUX",
    }
    result.update(extra)
    return result


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _items(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    value = payload.get("items")

    if not isinstance(value, list):
        return []

    return [
        row
        for row in value
        if isinstance(row, dict)
    ]


def _raw_nodes(
    *,
    cmts: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
) -> Dict[str, Any]:
    return analytics._remote_request(
        "nodes",
        cmts=cmts,
        search=search,
        limit=limit,
        offset=0,
    )


def _aggregate_modems(
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:

    items = list(rows)

    active = sum(
        _as_int(row.get("active_oper"))
        for row in items
    )

    registered = sum(
        _as_int(row.get("registered"))
        for row in items
    )

    offline = sum(
        _as_int(row.get("offline"))
        for row in items
    )

    init = 0
    disable = 0

    total = registered + offline

    percentages = [
        _as_float(row.get("porc_offline"))
        for row in items
        if row.get("porc_offline") not in (None, "")
    ]

    if total > 0:
        pct_offline = round(
            (offline / total) * 100.0,
            2,
        )
    elif percentages:
        pct_offline = round(
            sum(percentages) / len(percentages),
            2,
        )
    else:
        pct_offline = 0.0

    pct_online = round(
        max(0.0, 100.0 - pct_offline),
        2,
    )

    return {
        "total": total,
        "online": active,
        "active": active,
        "registered": registered,
        "init": init,
        "offline": offline,
        "disable": disable,
        "pct_online": pct_online,
        "pct_offline": pct_offline,
    }


def estado_nodo(nodo: str) -> Dict[str, Any]:

    node = _clean(nodo)

    if not node:
        return _error(
            "HFC_NODO_REQUERIDO",
            "Debe indicar un nodo.",
        )

    try:
        payload = analytics.nodo(node)
    except Exception as exc:
        return _error(
            "HFC_ANALYTICS_UNAVAILABLE",
            f"{type(exc).__name__}: {exc}",
            nodo=node,
        )

    rows = _items(payload)

    if not rows:
        return _error(
            "HFC_NODO_NO_ENCONTRADO",
            "No se encontró información HFC para el nodo.",
            nodo=node,
        )

    base = rows[0]
    totals = _aggregate_modems(rows)

    estados = [
        _clean(row.get("estado"))
        for row in rows
        if row.get("estado")
    ]

    if "AFECTACION_ALTA" in estados:
        estado = "AFECTACION_ALTA"
    elif "AFECTACION" in estados:
        estado = "AFECTACION"
    elif "OBSERVACION" in estados:
        estado = "OBSERVACION"
    elif estados:
        estado = estados[0]
    else:
        estado = ""

    fecha = max(
        (
            str(row.get("fecha_hfc_auto") or "")
            for row in rows
        ),
        default="",
    )

    descripcion = (
        base.get("descripcion")
        or base.get("main_diagnosis")
        or ""
    )

    return {
        "ok": True,
        "tipo_respuesta": "hfc_estado_nodo",
        "intencion": "HFC_ESTADO_NODO",
        "nodo": base.get("nodo") or node,
        "cmts": base.get("cmts") or "",
        "ip": "",
        "vendor": base.get("tecnologia") or "",
        "tipo_elemento": "NODO_HFC",
        **totals,
        "estado": estado,
        "decision": estado,
        "severidad": "",
        "observacion": descripcion,
        "descripcion": descripcion,
        "fecha_reporte": fecha,
        "edad_minutos": None,
        "stale": False,
        "fuente": "HFC_ANALYTICS_LINUX",
    }


def modems_nodo(nodo: str) -> Dict[str, Any]:

    base = estado_nodo(nodo)

    if not isinstance(base, dict):
        return _error(
            "HFC_MODEMS_BAD_RESPONSE",
            "Respuesta HFC inválida.",
        )

    if not base.get("ok"):
        return base

    return {
        "ok": True,
        "tipo_respuesta": "hfc_modems_nodo",
        "intencion": "HFC_MODEMS_NODO",
        "nodo": base.get("nodo") or "",
        "cmts": base.get("cmts") or "",
        "ip": base.get("ip") or "",
        "vendor": base.get("vendor") or "",
        "total": base.get("total", 0),
        "online": base.get("online", 0),
        "active": base.get("active", 0),
        "registered": base.get("registered", 0),
        "init": base.get("init", 0),
        "offline": base.get("offline", 0),
        "disable": base.get("disable", 0),
        "pct_online": base.get("pct_online", 0.0),
        "pct_offline": base.get("pct_offline", 0.0),
        "estado": base.get("estado") or "",
        "observacion": base.get("observacion") or "",
        "fecha_reporte": base.get("fecha_reporte") or "",
        "edad_minutos": base.get("edad_minutos"),
        "stale": bool(base.get("stale", False)),
        "fuente": base.get("fuente") or "HFC_ANALYTICS_LINUX",
    }


def estado_cmts(cmts: str) -> Dict[str, Any]:

    name = _clean(cmts)

    if not name:
        return _error(
            "HFC_CMTS_REQUERIDO",
            "Debe indicar un CMTS.",
        )

    try:
        payload = _raw_nodes(
            cmts=name,
            limit=5000,
        )
    except Exception as exc:
        return _error(
            "HFC_CMTS_UNAVAILABLE",
            f"{type(exc).__name__}: {exc}",
            cmts=name,
        )

    rows = _items(payload)

    if not rows:
        return _error(
            "HFC_CMTS_NO_ENCONTRADO",
            "No se encontró información para el CMTS.",
            cmts=name,
        )

    state_counts: Dict[str, int] = {}

    for row in rows:
        state = _clean(row.get("estado")) or "SIN_ESTADO"
        state_counts[state] = state_counts.get(state, 0) + 1

    return {
        "ok": True,
        "tipo_respuesta": "hfc_estado_cmts",
        "intencion": "HFC_ESTADO_CMTS",
        "cmts": name,
        "nodos": len(rows),
        "hogares": sum(
            _as_int(row.get("hogares"))
            for row in rows
        ),
        "estados": state_counts,
        "afectados": sum(
            1
            for row in rows
            if _clean(row.get("estado")) in _AFFECTED_STATES
        ),
        "fuente": "HFC_ANALYTICS_LINUX",
    }


def nodos_cmts(cmts: str) -> Dict[str, Any]:

    name = _clean(cmts)

    if not name:
        return _error(
            "HFC_CMTS_REQUERIDO",
            "Debe indicar un CMTS.",
        )

    try:
        payload = _raw_nodes(
            cmts=name,
            limit=5000,
        )
    except Exception as exc:
        return _error(
            "HFC_CMTS_NODOS_UNAVAILABLE",
            f"{type(exc).__name__}: {exc}",
            cmts=name,
        )

    rows = _items(payload)

    return {
        "ok": True,
        "tipo_respuesta": "hfc_nodos_cmts",
        "intencion": "HFC_NODOS_CMTS",
        "cmts": name,
        "count": len(rows),
        "items": rows,
        "fuente": "HFC_ANALYTICS_LINUX",
    }


def buscar_nodo(nodo: str) -> Dict[str, Any]:

    value = _clean(nodo)

    if not value:
        return _error(
            "HFC_NODO_REQUERIDO",
            "Debe indicar un nodo.",
        )

    try:
        payload = _raw_nodes(
            search=value,
            limit=200,
        )
    except Exception as exc:
        return _error(
            "HFC_BUSCAR_NODO_UNAVAILABLE",
            f"{type(exc).__name__}: {exc}",
            nodo=value,
        )

    rows = [
        row
        for row in _items(payload)
        if value in _clean(row.get("nodo"))
    ]

    return {
        "ok": True,
        "tipo_respuesta": "hfc_buscar_nodo",
        "intencion": "HFC_BUSCAR_NODO",
        "consulta": value,
        "count": len(rows),
        "items": rows,
        "fuente": "HFC_ANALYTICS_LINUX",
    }


def afectados_cmts(cmts: str) -> Dict[str, Any]:

    name = _clean(cmts)

    if not name:
        return _error(
            "HFC_CMTS_REQUERIDO",
            "Debe indicar un CMTS.",
        )

    try:
        payload = _raw_nodes(
            cmts=name,
            limit=5000,
        )
    except Exception as exc:
        return _error(
            "HFC_AFECTADOS_CMTS_UNAVAILABLE",
            f"{type(exc).__name__}: {exc}",
            cmts=name,
        )

    rows = [
        row
        for row in _items(payload)
        if _clean(row.get("estado")) in _AFFECTED_STATES
    ]

    return {
        "ok": True,
        "tipo_respuesta": "hfc_afectados_cmts",
        "intencion": "HFC_AFECTADOS_CMTS",
        "cmts": name,
        "count": len(rows),
        "items": rows,
        "fuente": "HFC_ANALYTICS_LINUX",
    }


def nodos_criticos_zona(zona: str) -> Dict[str, Any]:

    value = _clean(zona)

    if not value:
        return _error(
            "HFC_ZONA_REQUERIDA",
            "Debe indicar una zona.",
        )

    try:
        payload = _raw_nodes(
            search=value,
            limit=5000,
        )
    except Exception as exc:
        return _error(
            "HFC_ZONA_UNAVAILABLE",
            f"{type(exc).__name__}: {exc}",
            zona=value,
        )

    rows = [
        row
        for row in _items(payload)
        if _clean(row.get("zona")) == value
        and _clean(row.get("estado")) in _AFFECTED_STATES
    ]

    return {
        "ok": True,
        "tipo_respuesta": "hfc_nodos_criticos_zona",
        "intencion": "HFC_NODOS_CRITICOS_ZONA",
        "zona": value,
        "count": len(rows),
        "items": rows,
        "fuente": "HFC_ANALYTICS_LINUX",
    }


def buscar_cmts(texto: str) -> Dict[str, Any]:

    value = _clean(texto)

    if not value:
        return _error(
            "HFC_CMTS_BUSQUEDA_REQUERIDA",
            "Debe indicar un texto para buscar CMTS.",
        )

    try:
        payload = _raw_nodes(
            search=value,
            limit=5000,
        )
    except Exception as exc:
        return _error(
            "HFC_BUSCAR_CMTS_UNAVAILABLE",
            f"{type(exc).__name__}: {exc}",
            consulta=value,
        )

    seen = set()
    result = []

    for row in _items(payload):

        cmts = _clean(row.get("cmts"))

        if (
            not cmts
            or value not in cmts
            or cmts in seen
        ):
            continue

        seen.add(cmts)

        result.append({
            "cmts": row.get("cmts") or cmts,
            "tecnologia": row.get("tecnologia") or "",
            "zona": row.get("zona") or "",
            "regional": row.get("regional") or "",
        })

    result.sort(
        key=lambda item: str(
            item.get("cmts") or ""
        )
    )

    return {
        "ok": True,
        "tipo_respuesta": "hfc_buscar_cmts",
        "intencion": "HFC_BUSCAR_CMTS",
        "consulta": value,
        "count": len(result),
        "items": result,
        "fuente": "HFC_ANALYTICS_LINUX",
    }


def historial_nodo(nodo: str) -> Dict[str, Any]:

    value = _clean(nodo)

    if not value:
        return _error(
            "HFC_NODO_REQUERIDO",
            "Debe indicar un nodo.",
        )

    # Existe una fuente histórica remota, pero no se fabrica un contrato
    # hasta conocer con certeza sus columnas.
    return _error(
        "HFC_HISTORIAL_PENDIENTE_MIGRACION",
        "El histórico HFC todavía no ha sido migrado al contrato nuevo.",
        nodo=value,
    )
