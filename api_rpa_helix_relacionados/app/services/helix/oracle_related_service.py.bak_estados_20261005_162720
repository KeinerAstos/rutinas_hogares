from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.services.helix.oracle_repository import OracleHelixRepository


INC_RE = re.compile(r"^INC\d{12}$", re.I)
TAS_RE = re.compile(r"^TAS\d{12}$", re.I)
WO_RE = re.compile(r"^WO\d{7,}$", re.I)
BOGOTA = ZoneInfo("America/Bogota")
WO_STATES = {0: "Assigned", 8: "Closed"}


def clean(value) -> str:
    if value is None:
        return ""
    try:
        value = value.read()
    except (AttributeError, OSError):
        pass
    return re.sub(r"\s+", " ", str(value).strip())


def _date(value) -> str:
    if value is None or value == "":
        return ""
    try:
        if isinstance(value, datetime):
            dt = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        elif isinstance(value, date):
            dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        else:
            dt = datetime.fromtimestamp(float(value), timezone.utc)
        return dt.astimezone(BOGOTA).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _alarm_active(row) -> bool | None:
    state = _number(row.get("estado"))
    cleared = row.get("fecha_hora_clareo") or row.get("epoch_fh_clareo")
    has_cleared = bool(cleared)
    if isinstance(cleared, (int, float)) and cleared <= 0:
        has_cleared = False
    if state == 1 and has_cleared:
        return None
    if state == 1:
        return True
    if state == 0 or has_cleared:
        return False
    return None


def _alarm(row) -> dict:
    active = _alarm_active(row)
    return {
        "dn": clean(row.get("r_ticketnro")),
        "node": clean(row.get("node")),
        "creada": _date(row.get("create_date")),
        "insertada": "",
        "estado": clean(row.get("ztmp_estado") or row.get("estado")),
        "nomb_alarma": clean(row.get("nomb_alarma")),
        "num_alarma": clean(row.get("num_alarma")),
        "fecha_hora_clareo": _date(row.get("fecha_hora_clareo") or row.get("epoch_fh_clareo")),
        "tecnologia": clean(row.get("tecnologia")),
        "cancelado_por": clean(row.get("canceladopor")),
        "relacionado_por": clean(row.get("relacionadopor")),
        "sector_eb": clean(row.get("sector_eb")),
        "serverserial": clean(row.get("serverserial")),
        "consec_nbr": clean(row.get("consec_nbr")),
        "activa": active is True,
        "estado_codigo": _number(row.get("estado")),
        "status_codigo": _number(row.get("status")),
    }


def _related(row: dict, kind: str) -> dict:
    code = _number(row.get("status"))
    state = WO_STATES.get(code, "PENDIENTE_VALIDAR") if kind == "WO" else "PENDIENTE_VALIDAR"
    return {
        "id": clean(row.get("work_order_id" if kind == "WO" else "task_id")),
        "tipo": kind if kind == "WO" else "TA",
        "tipo_relacion": "",
        "titulo": clean(row.get("summary")),
        "estado": state,
        "usuario_asignado": "",
        "grupo_asignado": "",
        "crear_fecha": _date(row.get("submit_date" if kind == "WO" else "create_date")),
        "tipo_ticket": "",
        "estado_codigo": code,
        **({"state_codigo": _number(row.get("state"))} if kind == "TA" else {}),
    }


def _error(ticket: str, kind: str, worker: int, error: str, duration: float) -> dict:
    return {
        "inc": "", "ticket_consultado": ticket, "tipo_consulta": kind,
        "tas_origen": ticket if kind == "TAS" else "", "titulo": "",
        "estado": "ERROR", "estado_incidente": "", "fecha_creacion_incidente": "",
        "alarmas": {"consultado": False, "total": None, "activas": None,
                    "canceladas": None, "clareadas": None, "capturadas": 0,
                    "captura_completa": False, "detalle": [], "activas_detalle": [],
                    "clareadas_detalle": [], "source": "ORACLE:ARADMIN.INT_NETCOOL_ALARMAS",
                    "error": "NO_CONSULTADO_POR_ERROR_TICKET"},
        "alarmas_total": None, "alarmas_activas_total": None,
        "alarmas_canceladas_total": None, "alarmas_clareadas_total": None,
        "alarmas_activas_detalle": [], "alarmas_clareadas_detalle": [],
        "alarmas_detalle": [], "total_wo": 0, "wo_relacionadas": [],
        "total_ta": 0, "ta_relacionadas": [], "relacionados_detalle": [],
        "duracion_seg": round(duration, 2), "worker": worker, "frame_url": "",
        "fuente_datos": "ORACLE_REPLICA", "error": error,
    }


def consultar_ticket(ticket: str, worker: int = 0) -> dict:
    started = time.perf_counter()
    ticket = clean(ticket).upper()
    kind = "INC" if INC_RE.fullmatch(ticket) else "TAS" if TAS_RE.fullmatch(ticket) else "WO" if WO_RE.fullmatch(ticket) else ""
    if not kind:
        return _error(ticket, "", worker, "IDENTIFICADOR_INVALIDO", time.perf_counter() - started)
    repo = OracleHelixRepository()
    tas_origen = ticket if kind == "TAS" else ""
    try:
        if kind == "TAS":
            parent = repo.resolve_task_parent(ticket)
            inc = clean((parent or {}).get("rootrequestname")).upper()
            if not parent or not INC_RE.fullmatch(inc):
                raise LookupError("INC_PADRE_NO_ENCONTRADO")
            form = clean(parent.get("rootrequestformname"))
            if form and form.upper() != "HPD:HELP DESK":
                raise LookupError("TAS_NO_PERTENECE_A_HPD_HELP_DESK")
        elif kind == "WO":
            parent = repo.resolve_work_order_parent(ticket)
            inc = clean((parent or {}).get("root_incident")).upper()
            if not parent or not INC_RE.fullmatch(inc):
                raise LookupError("INC_PADRE_NO_ENCONTRADO")
        else:
            inc = ticket
        incident = repo.get_incident(inc)
        if not incident:
            raise LookupError("INCIDENTE_NO_ENCONTRADO")
    except Exception as exc:
        prefix = "ORACLE_DATABASE_ERROR" if not isinstance(exc, LookupError) else str(exc)
        return _error(ticket, kind, worker, f"{prefix}: {exc}", time.perf_counter() - started)

    errors = []
    tasks = []
    work_orders = []
    alarm_rows = []
    for label, method, target in (
        ("tas", repo.get_tasks_by_incident, tasks),
        ("wo", repo.get_work_orders_by_incident, work_orders),
        ("alarmas", repo.get_alarms_by_incident, alarm_rows),
    ):
        try:
            target.extend(method(inc))
        except Exception as exc:
            errors.append(f"{label}: ORACLE_DATABASE_ERROR: {exc}")

    details = [_related(row, "WO") for row in work_orders] + [_related(row, "TA") for row in tasks]
    alarms = [_alarm(row) for row in alarm_rows]
    active = [item for item in alarms if item["activa"]]
    cleared = [item for item in alarms if _alarm_active({"estado": item["estado_codigo"], "fecha_hora_clareo": item["fecha_hora_clareo"]}) is False]
    unknown = len(alarms) - len(active) - len(cleared)
    alarm_error = next((e for e in errors if e.startswith("alarmas:")), "")
    alarm_obj = {
        "consultado": not bool(alarm_error), "total": None if alarm_error else len(alarms),
        "activas": None if alarm_error else len(active), "canceladas": None if alarm_error else len(cleared),
        "clareadas": None if alarm_error else len(cleared), "capturadas": len(alarms),
        "captura_completa": not bool(alarm_error) and unknown == 0,
        "detalle": alarms, "activas_detalle": active, "clareadas_detalle": cleared,
        "source": "ORACLE:ARADMIN.INT_NETCOOL_ALARMAS", "error": alarm_error,
    }
    short = clean(incident.get("short_description"))
    title = short if short and short != "." else clean(incident.get("description"))
    inc_status = clean(incident.get("status_incident")) or "PENDIENTE_VALIDAR"
    if not details:
        status = "SIN_RELACIONADOS" if not any(e.startswith(("tas:", "wo:")) for e in errors) else "OK"
    else:
        status = "OK"
    return {
        "inc": inc, "titulo": title, "estado": status, "estado_incidente": inc_status,
        "estado_codigo_incidente": _number(incident.get("status")),
        "fecha_creacion_incidente": _date(incident.get("submit_date")),
        "alarmas": alarm_obj, "alarmas_total": alarm_obj["total"],
        "alarmas_activas_total": alarm_obj["activas"],
        "alarmas_canceladas_total": alarm_obj["canceladas"],
        "alarmas_clareadas_total": alarm_obj["clareadas"],
        "alarmas_activas_detalle": active, "alarmas_clareadas_detalle": cleared,
        "alarmas_detalle": alarms, "total_wo": len(work_orders),
        "wo_relacionadas": [item["id"] for item in details if item["tipo"] == "WO"],
        "total_ta": len(tasks), "ta_relacionadas": [item["id"] for item in details if item["tipo"] == "TA"],
        "relacionados_detalle": details, "duracion_seg": round(time.perf_counter() - started, 2),
        "worker": worker, "frame_url": "", "fuente_datos": "ORACLE_REPLICA", "error": "",
        "ticket_consultado": ticket, "tipo_consulta": kind, "tas_origen": tas_origen,
        "errores_parciales": errors,
        **({"titulo_tarea": clean(parent.get("summary")), "estado_tarea": "PENDIENTE_VALIDAR"} if kind == "TAS" else {}),
    }
