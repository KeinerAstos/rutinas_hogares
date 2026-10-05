from __future__ import annotations

import threading
from typing import Any

from app.config.oracle_settings import OracleSettings


_POOL: Any = None
_POOL_LOCK = threading.Lock()


def get_pool():
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            return _POOL
        import oracledb

        cfg = OracleSettings.from_env()
        if not cfg.configured:
            raise RuntimeError("ORACLE_CONFIGURATION_MISSING")
        dsn = oracledb.makedsn(cfg.host, cfg.port, service_name=cfg.service)
        _POOL = oracledb.create_pool(
            user=cfg.user,
            password=cfg.password,
            dsn=dsn,
            min=cfg.pool_min,
            max=max(cfg.pool_min, cfg.pool_max),
            increment=cfg.pool_increment,
            getmode=oracledb.POOL_GETMODE_WAIT,
        )
    return _POOL


class OracleHelixRepository:
    def __init__(self) -> None:
        self.settings = OracleSettings.from_env()

    def _query(self, sql: str, binds: dict[str, Any], many: bool = False):
        conn = get_pool().acquire()
        try:
            conn.call_timeout = self.settings.call_timeout_ms
            with conn.cursor() as cursor:
                cursor.execute(sql, binds)
                names = [item[0].lower() for item in cursor.description or ()]
                rows = cursor.fetchall() if many else cursor.fetchone()
                if many:
                    return [dict(zip(names, row)) for row in rows]
                return dict(zip(names, rows)) if rows is not None else None
        finally:
            get_pool().release(conn)

    def resolve_task_parent(self, task_id: str) -> dict | None:
        return self._query(
            """SELECT TASK_ID, ROOTREQUESTNAME, ROOTREQUESTID,
                      ROOTREQUESTFORMNAME, ROOTREQUESTINSTANCEID,
                      STATUS, STATE, SUMMARY, CREATE_DATE
               FROM ARADMIN.TMS_TASK WHERE TASK_ID = :task_id""",
            {"task_id": task_id},
        )

    def resolve_work_order_parent(self, work_order_id: str) -> dict | None:
        return self._query(
            """SELECT WORK_ORDER_ID, ROOT_INCIDENT, STATUS, SUBMIT_DATE, SUMMARY
               FROM ARADMIN.WOI_WORKORDER WHERE WORK_ORDER_ID = :work_order_id""",
            {"work_order_id": work_order_id},
        )

    def get_incident(self, incident_number: str) -> dict | None:
        return self._query(
            """SELECT INCIDENT_NUMBER, SUBMIT_DATE, STATUS, STATUS_INCIDENT,
                      SHORT_DESCRIPTION, DESCRIPTION
               FROM ARADMIN.HPD_HELP_DESK WHERE INCIDENT_NUMBER = :incident_number""",
            {"incident_number": incident_number},
        )

    def get_tasks_by_incident(self, incident_number: str) -> list[dict]:
        return self._query(
            """SELECT TASK_ID, ROOTREQUESTNAME, STATUS, STATE, CREATE_DATE, SUMMARY
               FROM ARADMIN.TMS_TASK WHERE ROOTREQUESTNAME = :incident_number""",
            {"incident_number": incident_number}, many=True,
        )

    def get_work_orders_by_incident(self, incident_number: str) -> list[dict]:
        return self._query(
            """SELECT WORK_ORDER_ID, ROOT_INCIDENT, STATUS, SUBMIT_DATE, SUMMARY
               FROM ARADMIN.WOI_WORKORDER WHERE ROOT_INCIDENT = :incident_number""",
            {"incident_number": incident_number}, many=True,
        )

    def get_alarms_by_incident(self, incident_number: str) -> list[dict]:
        return self._query(
            """SELECT REQUEST_ID, INCIDENT_NUMBER, NUM_ALARMA, NOMB_ALARMA,
                      STATUS, ESTADO, ZTMP_ESTADO, CREATE_DATE,
                      FECHA_HORA_CLAREO, EPOCH_FH_CLAREO, R_TICKETNRO, NODE,
                      TECNOLOGIA, RELACIONADOPOR, CANCELADOPOR, SECTOR_EB,
                      SERVERSERIAL, CONSEC_NBR
               FROM ARADMIN.INT_NETCOOL_ALARMAS
               WHERE INCIDENT_NUMBER = :incident_number""",
            {"incident_number": incident_number}, many=True,
        )
