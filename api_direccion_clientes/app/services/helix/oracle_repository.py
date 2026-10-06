from __future__ import annotations

from threading import Lock
from typing import Any

import oracledb

from app.config.oracle_settings import OracleSettings


class OracleRepository:
    def __init__(self, settings: OracleSettings | None = None) -> None:
        self.settings = settings or OracleSettings.from_env()
        self._pool: Any = None
        self._pool_lock = Lock()

    def _get_pool(self) -> Any:
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    cfg = self.settings
                    dsn = oracledb.makedsn(cfg.host, cfg.port, service_name=cfg.service)
                    self._pool = oracledb.create_pool(
                        user=cfg.user,
                        password=cfg.password,
                        dsn=dsn,
                        min=cfg.pool_min,
                        max=cfg.pool_max,
                        increment=cfg.pool_increment,
                        timeout=30,
                        wait_timeout=10000,
                        getmode=oracledb.POOL_GETMODE_WAIT,
                    )
        return self._pool

    def _select_one(self, sql: str, binds: dict[str, Any]) -> dict[str, Any] | None:
        connection = self._get_pool().acquire()
        try:
            connection.call_timeout = self.settings.call_timeout_ms
            with connection.cursor() as cursor:
                cursor.execute(sql, binds)
                row = cursor.fetchone()
                if row is None:
                    return None
                return {
                    str(column[0]).lower(): value
                    for column, value in zip(cursor.description, row)
                }
        finally:
            connection.close()

    def get_work_order(self, wo: str) -> dict[str, Any] | None:
        schema = self.settings.schema
        return self._select_one(
            f"""SELECT WORK_ORDER_ID, STATUS, SUMMARY, DETAILED_DESCRIPTION,
                       NODE, ROOT_INCIDENT, CATEGORIZATION_TIER_1,
                       CATEGORIZATION_TIER_2, CATEGORIZATION_TIER_3,
                       XCA_NOMBRE_ARTICULO_CONFIG,
                       XCA_DESCRIPCION_ARTICULO_CONFI, CIUDAD__C, REGIONAL,
                       ALIADO, SITE, ZTMP_STATUS_OFSC
                FROM {schema}.WOI_WORKORDER WHERE WORK_ORDER_ID = :wo""",
            {"wo": wo},
        )

    def get_incident(self, inc: str) -> dict[str, Any] | None:
        schema = self.settings.schema
        return self._select_one(
            f"""SELECT INCIDENT_NUMBER, STATUS, SHORT_DESCRIPTION, DESCRIPTION,
                       DETAILED_DECRIPTION, DESCRIPCION_DE_IMPACTO, HPD_CI,
                       HPD_CI_RECONID, HPD_CI_FORMNAME, CIUDAD, REGIONAL, SITE
                FROM {schema}.HPD_HELP_DESK WHERE INCIDENT_NUMBER = :inc""",
            {"inc": inc},
        )

    def get_ci(self, recon: str) -> dict[str, Any] | None:
        schema = self.settings.schema
        return self._select_one(
            f"""SELECT REQUEST_ID, NAME, ITEM, SHORT_DESCRIPTION, LABEL, CI_TYPE,
                       CLASS_ID, RECONCILIATION_IDENTITY, ASSET_ID_
                FROM {schema}.AST_BASEELEMENT
                WHERE RECONCILIATION_IDENTITY = :recon AND ROWNUM <= 1""",
            {"recon": recon},
        )


_repository: OracleRepository | None = None
_repository_lock = Lock()


def get_oracle_repository() -> OracleRepository:
    global _repository
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = OracleRepository()
    return _repository
