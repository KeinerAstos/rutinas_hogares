from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

import pymysql

from app.core.settings import settings


logger = logging.getLogger(__name__)


_ALLOWED_TABLES = {
    "mesa_ayuda_consultas_chatbot",
    "mesa_ayuda_consultas_chatbot_test",
}

_CASE_RE = re.compile(
    r"\b(WO\d{5,}|INC\d{5,})\b",
    re.IGNORECASE,
)


def _table_name() -> str:
    table = str(
        settings.chatbot_db_table
        or "mesa_ayuda_consultas_chatbot_test"
    ).strip()

    if table not in _ALLOWED_TABLES:
        raise ValueError(
            f"Tabla tracking no permitida: {table}"
        )

    return table


def _connection():
    return pymysql.connect(
        host=str(settings.chatbot_db_host),
        port=int(settings.chatbot_db_port),
        user=str(settings.chatbot_db_user),
        password=str(settings.chatbot_db_password),
        database=str(settings.chatbot_db_name),
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=3,
        read_timeout=8,
        write_timeout=8,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _text(value: Any, limit: int | None = None) -> str:
    result = str(value or "").strip()

    if limit and len(result) > limit:
        result = result[:limit]

    return result


def _detectar_caso(*values: Any) -> tuple[str | None, str | None]:
    for value in values:
        text = _text(value)

        if not text:
            continue

        match = _CASE_RE.search(text)

        if not match:
            continue

        caso = match.group(1).upper()

        if caso.startswith("WO"):
            return caso, "WO"

        if caso.startswith("INC"):
            return caso, "INC"

    return None, None


def _warn(action: str, exc: Exception) -> None:
    logger.warning(
        "CHAT_TRACKING_MYSQL action=%s error=%s detail=%s",
        action,
        type(exc).__name__,
        str(exc)[:500],
    )


def registrar_inicio(
    *,
    mensaje: str,
    conversation_id: str,
    user_id: str | None = None,
    canal: str = "ATLAS",
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex

    context: dict[str, Any] = {
        "request_id": request_id,
        "conversation_id": _text(conversation_id, 128),
        "user_id": _text(user_id, 128),
        "canal": _text(canal, 40) or "ATLAS",
        "mensaje": _text(mensaje),
        "_started_monotonic": time.perf_counter(),
        "persistido": False,
    }

    caso, tipo_caso = _detectar_caso(mensaje)

    try:
        table = _table_name()

        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO `{table}` (
                        request_id,
                        session_id,
                        usuario,
                        canal,
                        caso,
                        tipo_caso,
                        consulta,
                        estado,
                        fecha_inicio
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        NOW(3)
                    )
                    """,
                    (
                        request_id,
                        context["conversation_id"],
                        context["user_id"] or None,
                        context["canal"],
                        caso,
                        tipo_caso,
                        context["mensaje"],
                        "PROCESANDO",
                    ),
                )

        context["persistido"] = True

    except Exception as exc:
        _warn("registrar_inicio", exc)

    return context


def finalizar_ok(
    context: dict[str, Any] | None,
    response: dict[str, Any] | None,
) -> bool:
    if not context or not context.get("persistido"):
        return False

    try:
        response = response if isinstance(response, dict) else {}

        solicitud = response.get("solicitud")
        if not isinstance(solicitud, dict):
            solicitud = {}

        sesion = response.get("sesion")
        if not isinstance(sesion, dict):
            sesion = {}

        respuesta = _text(
            response.get("respuesta"),
        )

        codigo = _text(
            response.get("codigo"),
            150,
        )

        estado_sesion = _text(
            response.get("estado")
            or sesion.get("estado"),
            100,
        )

        caso, tipo_caso = _detectar_caso(
            solicitud.get("id"),
            solicitud.get("wo"),
            sesion.get("solicitud_id"),
            sesion.get("wo"),
            context.get("mensaje"),
            respuesta,
        )

        flujo = _text(
            sesion.get("tipo_codigo")
            or solicitud.get("tipo_codigo"),
            100,
        )

        opcion = _text(
            solicitud.get("tipo_nombre")
            or sesion.get("tipo_nombre")
            or flujo,
            100,
        )

        elapsed = max(
            0,
            int(
                (
                    time.perf_counter()
                    - float(
                        context.get(
                            "_started_monotonic",
                            time.perf_counter(),
                        )
                    )
                )
                * 1000
            ),
        )

        table = _table_name()

        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE `{table}`
                    SET
                        caso = COALESCE(%s, caso),
                        tipo_caso = COALESCE(%s, tipo_caso),
                        respuesta = %s,
                        codigo_respuesta = %s,
                        estado_sesion = %s,
                        opcion = %s,
                        flujo = %s,
                        estado = %s,
                        fecha_fin = NOW(3),
                        duracion_ms = %s,
                        resultado_resumen = %s,
                        error_tipo = NULL,
                        error_detalle = NULL
                    WHERE request_id = %s
                    """,
                    (
                        caso,
                        tipo_caso,
                        respuesta,
                        codigo or None,
                        estado_sesion or None,
                        opcion or None,
                        flujo or None,
                        "OK",
                        elapsed,
                        _text(respuesta, 2000),
                        context["request_id"],
                    ),
                )

                updated = cur.rowcount

        return updated == 1

    except Exception as exc:
        _warn("finalizar_ok", exc)
        return False


def finalizar_error(
    context: dict[str, Any] | None,
    exc: Exception,
) -> bool:
    if not context or not context.get("persistido"):
        return False

    try:
        elapsed = max(
            0,
            int(
                (
                    time.perf_counter()
                    - float(
                        context.get(
                            "_started_monotonic",
                            time.perf_counter(),
                        )
                    )
                )
                * 1000
            ),
        )

        table = _table_name()

        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE `{table}`
                    SET
                        estado = %s,
                        fecha_fin = NOW(3),
                        duracion_ms = %s,
                        error_tipo = %s,
                        error_detalle = %s
                    WHERE request_id = %s
                    """,
                    (
                        "ERROR",
                        elapsed,
                        type(exc).__name__[:150],
                        _text(exc, 3000),
                        context["request_id"],
                    ),
                )

                updated = cur.rowcount

        return updated == 1

    except Exception as tracking_exc:
        _warn("finalizar_error", tracking_exc)
        return False


def health() -> dict[str, Any]:
    result = {
        "ok": False,
        "database": str(settings.chatbot_db_name),
        "table": "",
    }

    try:
        table = _table_name()
        result["table"] = table

        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                row = cur.fetchone()

        result["ok"] = bool(
            row and int(row.get("ok", 0)) == 1
        )

    except Exception as exc:
        result["error"] = (
            f"{type(exc).__name__}: {str(exc)[:300]}"
        )

    return result
