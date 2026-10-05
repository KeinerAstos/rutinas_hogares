from __future__ import annotations

import os
import re
import time
from typing import Any

from app.infrastructure.ssh_jump import SSHJumpSession
from app.infrastructure.telnet_jump import TelnetJumpSession
# ATLAS_HELIX_WO_SUMMARY_EXTERNAL_8023_V1
from app.services.helix.incident_related_batch_http_client import consultar_resumen_ot as consultar_resumen_ot_helix
from app.services.deco.inventory_red_service import consultar_equipo
from app.services.maximo.service import (
    resolver_cuenta_acs_desde_identificador,
    resolver_cuentas_acs_batch,
    consultar_vecinos_por_cuenta,
)
from app.services.ftth import vm_ftth_direct_service as vm_ftth
from app.config.ftth_settings import (
    get_ftth_direcciones_settings,
    get_ftth_jump_settings,
)


def _find_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    required = {
        "elemento_red",
        "rack",
        "shelf",
        "slot",
        "tipo_red",
        "es_ftth",
    }

    if required.intersection(value.keys()):
        return value

    for key in ("data", "resultado", "respuesta", "detalle"):
        child = value.get(key)
        if isinstance(child, dict):
            found = _find_payload(child)
            if found:
                return found

    return value


def _clean(value: Any) -> str:
    return str(value or "").strip()


# FTTH_HAC_GPON_SEMANTICS_V1_START
def _build_gpon_olt(
    data: dict[str, Any],
    elemento: str = "",
) -> str:
    elemento_up = _clean(elemento).upper()

    if elemento_up.startswith("HAC-"):
        # Huawei / HAC:
        # FRAME / SLOT / PORT.
        # Esta semantica permanece sin cambios.
        frame = _clean(
            data.get("frame")
            or data.get("rack")
        )

        slot = _clean(
            data.get("slot")
        )

        pon = _clean(
            data.get("port")
        )

        parts = [
            frame,
            slot,
            pon,
        ]

        if all(parts):
            return "/".join(parts)

        return ""

    # FTTH_ZTE_RACK_SLOT_PORT_V2
    #
    # ZTE / ZAC:
    #
    # Helix publica:
    #   RACK
    #   SHELF
    #   SLOT
    #   PORT
    #
    # La interfaz GPON correcta usa:
    #
    #   RACK / SLOT / PORT
    #
    # Ejemplo discriminante real:
    #
    #   RACK=1
    #   SHELF=1
    #   SLOT=5
    #   PORT=15
    #
    #   => gpon_olt-1/5/15
    #
    # SHELF NO debe sustituir SLOT cuando SLOT
    # esta presente.
    #
    # Fallback controlado:
    # si un payload historico no trae SLOT,
    # pero SI trae SHELF + PORT, SHELF puede
    # ocupar la segunda posicion.
    #
    # PORT nunca se infiere desde SLOT:
    # sin PORT real se devuelve cadena vacia
    # para evitar consultar otra PON.

    rack = _clean(
        data.get("rack")
        or data.get("frame")
    )

    slot = _clean(
        data.get("slot")
    )

    shelf = _clean(
        data.get("shelf")
        or data.get("subslot")
    )

    pon = _clean(
        data.get("port")
    )

    if rack and slot and pon:
        return "/".join(
            [
                rack,
                slot,
                pon,
            ]
        )

    if (
        rack
        and not slot
        and shelf
        and pon
    ):
        return "/".join(
            [
                rack,
                shelf,
                pon,
            ]
        )

    return ""
# FTTH_HAC_GPON_SEMANTICS_V1_END


def _select_inventory_equipment(result: dict[str, Any], expected: str) -> dict[str, Any]:
    if not isinstance(result, dict) or not result.get("ok"):
        return {}

    response = result.get("respuesta")
    if not isinstance(response, dict):
        return {}

    rows = response.get("equipos")
    if not isinstance(rows, list):
        return {}

    expected_upper = expected.strip().upper()

    exact = [
        row for row in rows
        if isinstance(row, dict)
        and _clean(row.get("nombre_equipo")).upper() == expected_upper
    ]

    valid_exact = [
        row for row in exact
        if row.get("ip_valida") and _clean(row.get("ip_equipo"))
    ]

    if valid_exact:
        return valid_exact[0]

    valid = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("ip_valida")
        and _clean(row.get("ip_equipo"))
    ]

    return valid[0] if valid else {}


def _parse_description(output: str) -> dict[str, str]:
    text = str(output or "")
    match = re.search(
        r"Description\s+is\s+(.+?)(?:\r?\n|$)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return {
            "descripcion_troncal": "",
            "id_troncal": "",
            "nombre_comercial": "",
        }

    description = match.group(1).strip().rstrip(".")

    comercial = ""
    commercial_match = re.search(r"\(([^()]*)\)\s*$", description)
    if commercial_match:
        comercial = commercial_match.group(1).strip()
        trunk_id = description[:commercial_match.start()].strip()
    else:
        trunk_id = description

    return {
        "descripcion_troncal": description,
        "id_troncal": trunk_id,
        "nombre_comercial": comercial,
    }


def _parse_state(output: str) -> dict[str, str]:
    states: dict[str, str] = {}

    for line in str(output or "").splitlines():
        match = re.match(
            r"\s*(\d+/\d+/\d+):(\d+)\s+"
            r"\S+\s+\S+\s+(\S+)",
            line,
            re.IGNORECASE,
        )
        if not match:
            continue

        onu = match.group(2)
        phase = match.group(3).strip()
        states[onu] = phase

    return states


def _parse_baseinfo(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    for line in str(output or "").splitlines():
        match = re.search(
            r"gpon_onu-\d+/\d+/\d+:(\d+).*?\bSN:([A-Z0-9]+)",
            line,
            re.IGNORECASE,
        )

        if not match:
            continue

        rows.append(
            {
                "onu": match.group(1),
                "serial": match.group(2).upper(),
            }
        )

    return rows


# FTTH_EMPRESAS_NEGOCIOS_V1_START
def _parse_zte_onu_detail(
    output: str,
) -> dict[str, str]:
    result: dict[str, str] = {}

    wanted = {
        "name": "name",
        "description": "description",
        "type": "type",
        "serial number": "serial",
        "authentication mode": "authentication_mode",
        "sn bind": "sn_bind",
    }

    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        normalized = key.strip().lower()

        if normalized in wanted:
            result[wanted[normalized]] = value.strip()

    return result


def _ftth_empresas_evidence(
    detail: dict[str, Any],
) -> dict[str, Any]:
    name = _clean(detail.get("name"))
    description = _clean(detail.get("description"))
    service_profile = _clean(
        detail.get("service_profile")
    )
    line_profile = _clean(
        detail.get("line_profile")
    )

    text = " ".join(
        part
        for part in (
            name,
            description,
            service_profile,
            line_profile,
        )
        if part
    ).upper()

    # Nomenclatura tecnica observada en Hogares:
    # 172.30.61.41_1-1-1-4-1
    technical_home = bool(
        re.fullmatch(
            r"\s*\d{1,3}(?:\.\d{1,3}){3}"
            r"_\d+(?:-\d+){4}\s*",
            name,
            re.IGNORECASE,
        )
    )

    reasons: list[str] = []
    score = 0

    if technical_home:
        return {
            "strong": False,
            "score": 0,
            "reasons": ["NOMENCLATURA_TECNICA_HOGARES"],
            "name": name,
            "description": description,
        }

    institutional_terms = (
        "FISCALIA",
        "FUNDACION",
        "UNIVERSIDAD",
        "COLEGIO",
        "ALCALDIA",
        "HOSPITAL",
        "BANCO",
        "EMPRESA",
        "EMPRESAS",
        "NEGOCIO",
        "NEGOCIOS",
        "ASOCIACION",
        "HOTELERA",
        "CORPORATIVO",
        "CORPORATIVA",
        "PYMES",
        "PYME",
    )

    for term in institutional_terms:
        if term in text:
            score += 2
            reasons.append(f"TERMINO_{term}")

    # Huawei: un service-profile explícitamente empresarial
    # constituye evidencia fuerte por sí solo.
    service_profile_up = service_profile.upper()

    if any(
        token in service_profile_up
        for token in (
            "PYME",
            "PYMES",
            "EMPRESA",
            "NEGOCIO",
            "CORPORAT",
        )
    ):
        score += 3
        reasons.append(
            "PERFIL_SERVICIO_EMPRESARIAL"
        )

    if re.search(
        r"(?:^|[_\s.\-])"
        r"(?:S\.?A\.?S?|LTDA|LIMITADA)"
        r"(?:$|[_\s.\-])",
        text,
        re.IGNORECASE,
    ):
        score += 2
        reasons.append("RAZON_SOCIAL")

    # Codigos observados en circuitos empresariales reales.
    if re.search(
        r"(?:FGI|FSHU|ACV)\d+",
        text,
        re.IGNORECASE,
    ):
        score += 1
        reasons.append("CODIGO_SERVICIO_EMPRESARIAL")

    return {
        "strong": score >= 2,
        "score": score,
        "reasons": reasons,
        "name": name,
        "description": description,
    }


# FTTH_HUAWEI_EMPRESAS_NEGOCIOS_V2_START
def _parse_huawei_ont_detail_empresas(
    output: str,
) -> dict[str, str]:
    raw = str(output or "")

    result: dict[str, str] = {}

    wanted = {
        "sn": "serial",
        "description": "description",
        "line profile name": "line_profile",
        "service profile name": "service_profile",
        "run state": "run_state",
        "control flag": "control",
        "ont-id": "ont_id",
    }

    for raw_line in raw.splitlines():
        line = raw_line.strip()

        if ":" not in line:
            continue

        key, value = line.split(":", 1)

        normalized = key.strip().lower()

        if normalized in wanted:
            result[
                wanted[normalized]
            ] = value.strip()

    return result


def _huawei_ont_detail_raw_empresas(
    session,
    command: str,
    hard: float = 40.0,
) -> str:
    # Se usa el selector RAW corregido:
    # HUAWEI_RAW_CHANNEL_SHELL_FIRST_V1.
    channel = session._find_raw_channel()

    if channel is None:
        raise RuntimeError(
            "HUAWEI_REAL_SHELL_NOT_FOUND"
        )

    # Vaciar únicamente residuos previos.
    for _ in range(20):
        try:
            if not channel.recv_ready():
                break
            channel.recv(65535)
        except Exception:
            break

    channel.send(
        str(command)
        + "\r"
    )

    parts: list[str] = []

    started = time.monotonic()
    last_data = started

    confirm_handled = 0
    more_handled = 0

    confirm_re = re.compile(
        r"\{\s*<cr>.*?\}:",
        re.IGNORECASE
        | re.DOTALL,
    )

    more_re = re.compile(
        r"----\s*More\s*"
        r"\(\s*Press\s*'Q'\s*to\s*break\s*\)"
        r"\s*----",
        re.IGNORECASE,
    )

    while (
        time.monotonic()
        - started
        < float(hard)
    ):
        try:
            ready = channel.recv_ready()
        except Exception:
            ready = False

        if ready:
            data = channel.recv(
                65535
            )

            chunk = (
                data.decode(
                    "utf-8",
                    errors="replace",
                )
                if isinstance(data, bytes)
                else str(data)
            )

            if not chunk:
                continue

            parts.append(
                chunk
            )

            last_data = (
                time.monotonic()
            )

            joined = "".join(
                parts
            )

            confirmations = len(
                confirm_re.findall(
                    joined
                )
            )

            while (
                confirm_handled
                < confirmations
            ):
                channel.send("\r")
                confirm_handled += 1

            pages = len(
                more_re.findall(
                    joined
                )
            )

            while (
                more_handled
                < pages
            ):
                if more_handled >= 40:
                    raise RuntimeError(
                        "HUAWEI_DETAIL_TOO_MANY_PAGES"
                    )

                channel.send(" ")
                more_handled += 1

            continue

        if parts:
            silence = (
                time.monotonic()
                - last_data
            )

            tail = "".join(
                parts
            )[-800:].rstrip()

            if (
                silence >= 0.8
                and re.search(
                    r"(?:\([^)]+\))?[>#]\s*$",
                    tail,
                )
            ):
                break

            if silence >= 3.0:
                break

        time.sleep(
            0.03
        )

    return "".join(
        parts
    )
# FTTH_HUAWEI_EMPRESAS_NEGOCIOS_V2_HELPERS_END


def _clasificar_ftth_empresas(
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    usable = [
        item
        for item in details
        if isinstance(item, dict)
        and item.get("detail_ok")
    ]

    strong = [
        item
        for item in usable
        if isinstance(
            item.get("evidence"),
            dict,
        )
        and item["evidence"].get("strong")
    ]

    inspected = len(usable)
    strong_count = len(strong)

    if inspected <= 0:
        required = 0
        detected = False

    elif inspected <= 2:
        required = inspected
        detected = strong_count >= required

    else:
        # Mayoría conservadora del 60%.
        # 3 -> 2
        # 4 -> 3
        # 5 -> 3
        required = max(
            2,
            (inspected * 3 + 4) // 5,
        )
        detected = strong_count >= required

    return {
        "detectado": detected,
        "inspeccionados": inspected,
        "evidencia_fuerte": strong_count,
        "minimo_requerido": required,
        "detalles": details,
    }
# FTTH_EMPRESAS_NEGOCIOS_V1_END

# FTTH_HUAWEI_EMPRESAS_NEGOCIOS_V2
# Extiende la evidencia Empresas/Negocios a Huawei HAC.
# Nunca bloquea ACS antes de probar los candidatos.


def _choose_serial(
    baseinfo: list[dict[str, str]],
    states: dict[str, str],
) -> dict[str, str]:
    if not baseinfo:
        return {}

    for row in baseinfo:
        phase = _clean(states.get(row.get("onu", "")))
        if phase.lower() == "working":
            selected = dict(row)
            selected["phase_state"] = phase
            selected["criterio"] = "WORKING"
            return selected

    selected = dict(baseinfo[0])
    selected["phase_state"] = _clean(states.get(selected.get("onu", "")))
    selected["criterio"] = "PRIMER_SERIAL_DISPONIBLE"
    return selected


def _credentials(vendor: str) -> tuple[str, str]:
    prefix = vendor.strip().upper()
    user = (
        os.getenv(f"{prefix}_SSH_USER")
        or os.getenv("OLT_SSH_USER")
        or ""
    )
    password = (
        os.getenv(f"{prefix}_SSH_PASS")
        or os.getenv("OLT_SSH_PASS")
        or ""
    )

    if not user or not password:
        raise RuntimeError(
            f"Faltan credenciales SSH para {prefix or 'OLT'}."
        )

    return user, password


# FTTH_HUAWEI_DIRECCIONES_V1_START
def _choose_huawei_serial(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not rows:
        return {}

    for row in rows:
        serial = _clean(
            row.get("sn")
            or row.get("serial")
            or row.get("serial_number")
        )

        state = _clean(
            row.get("run_state")
            or row.get("state")
            or row.get("status")
        )

        if serial and state.lower() == "online":
            selected = dict(row)
            selected["serial"] = serial.upper()
            selected["criterio"] = "ONLINE"
            return selected

    for row in rows:
        serial = _clean(
            row.get("sn")
            or row.get("serial")
            or row.get("serial_number")
        )

        if serial:
            selected = dict(row)
            selected["serial"] = serial.upper()
            selected["criterio"] = "PRIMER_SERIAL_DISPONIBLE"
            return selected

    return {}



# FTTH_HUAWEI_JUMP_BRIDGE_V2_START

# FTTH_ZTE_JUMP_CENTRAL_V1_START
def _ftth_zte_telnet_jump_session(
    ip: str,
    user: str,
    password: str,
    *,
    port: int = 23,
    connect_timeout: int | None = None,
):
    # Sesion ZTE/ZAC exclusiva del flujo FTTH.
    # El jump host/port se obtiene solo de la configuracion FTTH.
    settings = get_ftth_jump_settings()

    effective_connect_timeout = (
        int(connect_timeout)
        if connect_timeout is not None
        else settings.connect_timeout
    )

    session = TelnetJumpSession(
        ip,
        user,
        password,
        port=port,
        connect_timeout=effective_connect_timeout,
    )

    session.jump_host = settings.host
    session.jump_port = settings.port

    return session
# FTTH_ZTE_JUMP_CENTRAL_V1_END


def _ftth_huawei_jump_session(
    ip: str,
    username: str,
    password: str,
    *,
    port: int = 22,
    connect_timeout: int | None = None,
):
    """
    Sesión Huawei del flujo Direcciones FTTH.

    El jump server se obtiene exclusivamente de la
    configuración central FTTH.
    """

    settings = get_ftth_jump_settings()

    effective_connect_timeout = (
        int(connect_timeout)
        if connect_timeout is not None
        else settings.connect_timeout
    )

    session = vm_ftth.SSHJumpSession(
        ip,
        username,
        password,
        port=port,
        connect_timeout=effective_connect_timeout,
    )

    target = session

    # El runtime Huawei puede envolver SSHJumpSession.
    for _ in range(4):

        inner = getattr(
            target,
            "_inner",
            None,
        )

        if inner is None:
            break

        target = inner

    target.jump_host = settings.host
    target.jump_port = settings.port

    return session
# FTTH_HUAWEI_JUMP_BRIDGE_V2_END


def _consultar_huawei_pon(
    ip: str,
    gpon: str,
) -> dict[str, Any]:
    parts = [part.strip() for part in str(gpon or "").split("/")]

    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise RuntimeError(
            f"Puerto GPON Huawei invalido: {gpon}"
        )

    frame, slot, pon = [int(part) for part in parts]

    username, password = _credentials("HUAWEI")

    with _ftth_huawei_jump_session(
        ip,
        username,
        password,
        port=22,
        connect_timeout=15,
    ) as session:

        # Reutilizamos exactamente el motor Huawei validado de VM FTTH.
        raw = vm_ftth._vm_huawei_query_pon_v47(
            session,
            slot,
            pon,
        )

    rows = vm_ftth._vh_parse_ont_port_v48(
        raw,
        f"{frame}/{slot}/{pon}",
    )

    selected = _choose_huawei_serial(rows)

    return {
        "raw": raw,
        "rows": rows,
        "selected": selected,
        "frame": frame,
        "slot": slot,
        "pon": pon,
    }
# FTTH_HUAWEI_DIRECCIONES_V1_END


def consultar_direcciones_troncal_ftth(
    wo: str,
    helix_result: dict[str, Any] | None = None,
    helix_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # DIRECCIONES_SINGLE_HELIX_FTTH_V4
    started = time.perf_counter()
    work_order = _clean(wo).upper()

    if not re.fullmatch(r"WO\d{13}", work_order):
        return {
            "ok": False,
            "codigo": "WO_INVALIDA",
            "wo": work_order,
            "respuesta": "La WO debe tener formato WO seguido de 13 digitos.",
        }

    if (
        isinstance(helix_result, dict)
        and helix_result
        and isinstance(helix_data, dict)
        and helix_data
    ):
        helix = helix_result
    else:
        helix = consultar_resumen_ot_helix(work_order)
        helix_data = _find_payload(helix)

    if not helix_data:
        return {
            "ok": False,
            "codigo": "HELIX_SIN_DATOS",
            "wo": work_order,
            "helix": helix,
        }

    tipo_red = _clean(helix_data.get("tipo_red")).upper()
    tipo_elemento = _clean(helix_data.get("tipo_elemento")).upper()
    titulo_ot = _clean(helix_data.get("titulo_ot")).upper()

    is_troncal = bool(
        helix_data.get("es_troncal")
        or tipo_elemento == "TRONCAL"
        or "TRONCAL GPON" in titulo_ot
    )

    is_ftth = bool(
        helix_data.get("es_ftth")
        or tipo_red == "FTTH"
        or is_troncal
    )

    if not is_ftth:
        return {
            "ok": False,
            "codigo": "WO_NO_FTTH",
            "wo": work_order,
            "tipo_red": _clean(helix_data.get("tipo_red")),
        }

    elemento = _clean(helix_data.get("elemento_red"))
    gpon = _build_gpon_olt(helix_data, elemento)

    if not elemento:
        return {
            "ok": False,
            "codigo": "HELIX_SIN_ELEMENTO_RED",
            "wo": work_order,
            "helix_ftth": helix_data,
        }

    if not gpon:
        return {
            "ok": False,
            "codigo": "HELIX_SIN_PUERTO_GPON",
            "wo": work_order,
            "elemento_red": elemento,
            "rack": _clean(helix_data.get("rack")),
            "shelf": _clean(helix_data.get("shelf")),
            "slot": _clean(helix_data.get("slot")),
            "port": _clean(helix_data.get("port")),
        }

    inventory = consultar_equipo(elemento)
    equipment = _select_inventory_equipment(inventory, elemento)

    if not equipment:
        return {
            "ok": False,
            "codigo": "KOU_REQUERIDO",
            "wo": work_order,
            "elemento_red": elemento,
            "gpon_olt": gpon,
            "inventario": inventory,
            "respuesta": (
                "ATLAS no tiene una IP valida para el elemento. "
                "Se requiere consultar KOU."
            ),
        }

    ip = _clean(equipment.get("ip_equipo"))
    vendor = _clean(equipment.get("vendor")).upper()

    if vendor not in {"ZTE", "HUAWEI"}:
        return {
            "ok": False,
            "codigo": "VENDOR_FTTH_NO_SOPORTADO",
            "wo": work_order,
            "elemento_red": elemento,
            "ip": ip,
            "vendor": vendor,
            "gpon_olt": gpon,
        }

    trunk = {
        "descripcion_troncal": "",
        "id_troncal": "",
        "nombre_comercial": "",
    }

    baseinfo: list[dict[str, Any]] = []
    selected: dict[str, Any] = {}

    if vendor == "ZTE":
        username, password = _credentials(vendor)

        with _ftth_zte_telnet_jump_session(
            ip,
            username,
            password,
            port=23,
        ) as session:
            session.execute("terminal length 0", timeout=10)

            interface_output = session.execute(
                f"show interface gpon_olt-{gpon}",
                timeout=35,
            )
            state_output = session.execute(
                f"show gpon onu state gpon_olt-{gpon}",
                timeout=35,
            )
            baseinfo_output = session.execute(
                f"show gpon onu baseinfo gpon_olt-{gpon}",
                timeout=35,
            )

        trunk = _parse_description(interface_output)
        states = _parse_state(state_output)
        baseinfo = _parse_baseinfo(baseinfo_output)

        # FTTH_ZTE_WORKING_CANDIDATES_V1
        # _parse_baseinfo solo entrega ONU + serial. El clasificador multi-ONU
        # necesita tambien el estado para poder priorizar todas las ONU working.
        for row in baseinfo:
            onu_id = _clean(row.get("onu"))
            phase_state = _clean(states.get(onu_id))
            row["phase_state"] = phase_state
            # Compatibilidad con el clasificador generico FTTH (Huawei/ZTE).
            row["run_state"] = phase_state

        selected = _choose_serial(baseinfo, states)

        if not selected:
            return {
                "ok": False,
                "codigo": "ZTE_SIN_SERIALES",
                "wo": work_order,
                "elemento_red": elemento,
                "ip": ip,
                "vendor": vendor,
                "gpon_olt": gpon,
                **trunk,
            }

    elif vendor == "HUAWEI":
        try:
            huawei = _consultar_huawei_pon(ip, gpon)
        except Exception as exc:
            return {
                "ok": False,
                "codigo": "HUAWEI_CONSULTA_PON_ERROR",
                "wo": work_order,
                "elemento_red": elemento,
                "ip": ip,
                "vendor": vendor,
                "gpon_olt": gpon,
                "error_tipo": type(exc).__name__,
                "error": str(exc),
            }

        baseinfo = huawei.get("rows") or []
        selected = huawei.get("selected") or {}

        if not selected:
            return {
                "ok": False,
                "codigo": "HUAWEI_SIN_SERIALES",
                "wo": work_order,
                "elemento_red": elemento,
                "ip": ip,
                "vendor": vendor,
                "gpon_olt": gpon,
            }

    # FTTH_MULTI_ONU_DIRECCIONES_V1
    #
    # Una PON puede tener varias ONU.
    # El primer serial utilizable en OLT no necesariamente tiene
    # una cuenta mycust04 asociada en ACS.
    #
    # Estrategia:
    # - conservar el candidato principal
    # - priorizar ONU online/working
    # - probar hasta el límite configurado de candidatos
    # - detener al primer resultado valido
    # - no repetir ante fallas globales de autenticacion

    primary_serial = _clean(
        selected.get("serial")
    ).upper()

    candidates: list[dict[str, Any]] = []
    seen_serials: set[str] = set()

    def add_candidate(
        serial_value: Any,
        row: dict[str, Any] | None = None,
        criterio: str = "",
    ) -> None:
        serial_candidate = _clean(serial_value).upper()

        if not serial_candidate:
            return

        if serial_candidate in seen_serials:
            return

        seen_serials.add(serial_candidate)

        candidate = dict(row or {})
        candidate["serial"] = serial_candidate
        candidate["criterio"] = (
            _clean(candidate.get("criterio"))
            or criterio
        )

        candidates.append(candidate)

    add_candidate(
        primary_serial,
        selected,
        _clean(selected.get("criterio"))
        or "SELECCION_PRINCIPAL",
    )

    online_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []

    for row in baseinfo:
        if not isinstance(row, dict):
            continue

        row_serial = _clean(
            row.get("sn")
            or row.get("serial")
            or row.get("serial_number")
        ).upper()

        if not row_serial:
            continue

        state = _clean(
            row.get("phase_state")
            or row.get("run_state")
            or row.get("state")
            or row.get("status")
        ).lower()

        if (
            "working" in state
            or "online" in state
            or state == "up"
        ):
            online_rows.append(row)
        else:
            other_rows.append(row)

    for row in online_rows:
        add_candidate(
            row.get("sn")
            or row.get("serial")
            or row.get("serial_number"),
            row,
            "ONU_ONLINE",
        )

    for row in other_rows:
        add_candidate(
            row.get("sn")
            or row.get("serial")
            or row.get("serial_number"),
            row,
            "SERIAL_DISPONIBLE",
        )

    ftth_direcciones_settings = (
        get_ftth_direcciones_settings()
    )

    candidates = candidates[
        :ftth_direcciones_settings.acs_max_candidates
    ]

    # FTTH_EMPRESAS_NEGOCIOS_CANDIDATES_V1
    #
    # IMPORTANTE:
    # Esta metadata NO corta el flujo de ACS.
    # Solo se conserva como evidencia para una decision posterior,
    # una vez agotados todos los candidatos disponibles.
    empresas_details: list[dict[str, Any]] = []

    if vendor == "ZTE" and candidates:
        try:
            username_detail, password_detail = _credentials("ZTE")

            with _ftth_zte_telnet_jump_session(
                ip,
                username_detail,
                password_detail,
                port=23,
            ) as detail_session:
                try:
                    detail_session.execute(
                        "terminal length 0",
                        timeout=10,
                    )
                except Exception:
                    pass

                for candidate in candidates:
                    onu_id = _clean(
                        candidate.get("onu")
                        or candidate.get("ont_id")
                    )

                    serial_detail = _clean(
                        candidate.get("serial")
                    ).upper()

                    detail_item: dict[str, Any] = {
                        "onu": onu_id,
                        "serial": serial_detail,
                        "detail_ok": False,
                    }

                    if onu_id:
                        try:
                            raw_detail = detail_session.execute(
                                (
                                    "show gpon onu detail-info "
                                    f"gpon_onu-{gpon}:{onu_id}"
                                ),
                                timeout=30,
                            )

                            parsed_detail = _parse_zte_onu_detail(
                                raw_detail
                            )

                            detail_item.update(parsed_detail)
                            detail_item["detail_ok"] = True

                            candidate["onu_name"] = _clean(
                                parsed_detail.get("name")
                            )
                            candidate["onu_description"] = _clean(
                                parsed_detail.get("description")
                            )
                            candidate["onu_type"] = _clean(
                                parsed_detail.get("type")
                            )

                        except Exception as exc:
                            detail_item["detail_error"] = (
                                f"{type(exc).__name__}: {exc}"
                            )

                    detail_item["evidence"] = (
                        _ftth_empresas_evidence(
                            detail_item
                        )
                    )

                    empresas_details.append(
                        detail_item
                    )

        except Exception as exc:
            empresas_details.append(
                {
                    "detail_ok": False,
                    "detail_error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "evidence": {
                        "strong": False,
                        "score": 0,
                        "reasons": [],
                    },
                }
            )

    # FTTH_HUAWEI_EMPRESAS_NEGOCIOS_V2
    #
    # Igual que ZTE:
    # esto es únicamente evidencia.
    # ACS se consulta posteriormente para TODOS los candidatos
    # disponibles antes de emitir EMPRESAS Y NEGOCIOS.
    if vendor == "HUAWEI" and candidates:
        try:
            gpon_parts = [
                _clean(part)
                for part in gpon.split("/")
            ]

            if len(gpon_parts) != 3:
                raise RuntimeError(
                    "GPON_HUAWEI_INVALIDA"
                )

            huawei_slot = int(
                gpon_parts[1]
            )
            huawei_pon = int(
                gpon_parts[2]
            )

            username_detail, password_detail = (
                _credentials(
                    "HUAWEI"
                )
            )

            with _ftth_huawei_jump_session(
                ip,
                username_detail,
                password_detail,
                port=22,
                connect_timeout=15,
            ) as detail_session:
                vm_ftth._vm_huawei_enter_gpon_v47(
                    detail_session,
                    huawei_slot,
                )

                for candidate in candidates:
                    ont_id = _clean(
                        candidate.get("ont_id")
                        or candidate.get("onu")
                    )

                    serial_detail = _clean(
                        candidate.get("serial")
                    ).upper()

                    detail_item: dict[str, Any] = {
                        "onu": ont_id,
                        "serial": serial_detail,
                        "vendor": "HUAWEI",
                        "detail_ok": False,
                    }

                    if ont_id:
                        try:
                            raw_detail = (
                                _huawei_ont_detail_raw_empresas(
                                    detail_session,
                                    (
                                        "display ont info "
                                        f"{huawei_pon} "
                                        f"{int(ont_id)}"
                                    ),
                                    hard=40.0,
                                )
                            )

                            parsed_detail = (
                                _parse_huawei_ont_detail_empresas(
                                    raw_detail
                                )
                            )

                            detail_item.update(
                                parsed_detail
                            )

                            detail_ok = bool(
                                _clean(
                                    parsed_detail.get(
                                        "serial"
                                    )
                                )
                                or _clean(
                                    parsed_detail.get(
                                        "description"
                                    )
                                )
                                or _clean(
                                    parsed_detail.get(
                                        "service_profile"
                                    )
                                )
                            )

                            detail_item[
                                "detail_ok"
                            ] = detail_ok

                            candidate[
                                "onu_description"
                            ] = _clean(
                                parsed_detail.get(
                                    "description"
                                )
                            )

                            candidate[
                                "onu_service_profile"
                            ] = _clean(
                                parsed_detail.get(
                                    "service_profile"
                                )
                            )

                            candidate[
                                "onu_line_profile"
                            ] = _clean(
                                parsed_detail.get(
                                    "line_profile"
                                )
                            )

                        except Exception as exc:
                            detail_item[
                                "detail_error"
                            ] = (
                                f"{type(exc).__name__}: "
                                f"{exc}"
                            )

                    detail_item["evidence"] = (
                        _ftth_empresas_evidence(
                            detail_item
                        )
                    )

                    empresas_details.append(
                        detail_item
                    )

        except Exception as exc:
            empresas_details.append(
                {
                    "vendor": "HUAWEI",
                    "detail_ok": False,
                    "detail_error": (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                    "evidence": {
                        "strong": False,
                        "score": 0,
                        "reasons": [],
                    },
                }
            )

    empresas_summary = _clasificar_ftth_empresas(
        empresas_details
    )

    # FTTH_FIRST_ACS_ACCOUNT_SINGLE_DIAG_V1
    direction_attempts: list[dict[str, Any]] = []
    directions: dict[str, Any] = {}
    selected_direction = dict(selected)
    selected_account = ""

    global_stop_codes = {
        "ACS_LOGIN_ERROR",
        "ACS_CREDENCIALES_INVALIDAS",
        "ACS_TIMEOUT",
        "DIAGNOSTICADOR_LOGIN_ERROR",
        "DIAGNOSTICADOR_CREDENCIALES_INVALIDAS",
        "DIAGNOSTICADOR_TIMEOUT",
        "DIRECCIONES_IDENTIFICADOR_ERROR",
        "DIRECCIONES_RESPUESTA_INVALIDA",
        "CREDENCIALES_FALTANTES",
        "CREDENCIALES_INVALIDAS",
    }

    # FTTH_ACS_BATCH_RUNNER_V2_START
    serials_batch = [
        _clean(candidate.get("serial")).upper()
        for candidate in candidates
        if _clean(candidate.get("serial"))
    ]

    acs_batch = resolver_cuentas_acs_batch(
        serials_batch,
        timeout_sec=ftth_direcciones_settings.acs_batch_timeout_sec,
    )

    if not isinstance(acs_batch, dict):
        acs_batch = {
            "ok": False,
            "codigo": "DIRECCIONES_RESPUESTA_INVALIDA",
            "cuenta_consulta": "",
            "resultados": [],
        }

    batch_items = acs_batch.get("resultados") or []
    if not isinstance(batch_items, list):
        batch_items = []

    for position, acs_only in enumerate(batch_items):
        if position >= len(candidates):
            break

        candidate = candidates[position]

        if not isinstance(acs_only, dict):
            acs_only = {
                "ok": False,
                "codigo": "DIRECCIONES_RESPUESTA_INVALIDA",
                "cuenta_consulta": "",
                "intentos_acs": [],
            }

        serial_candidate = _clean(candidate.get("serial")).upper()
        acs_code = _clean(acs_only.get("codigo")).upper()
        account_candidate = _clean(acs_only.get("cuenta_consulta"))

        direction_attempts.append({
            "serial": serial_candidate,
            "codigo": acs_code,
            "acs_codigo": acs_code,
            "ok": bool(acs_only.get("ok") and account_candidate),
            "criterio": _clean(candidate.get("criterio")),
            "cuenta_consulta": account_candidate,
            "clientes_encontrados": 0,
            "diagnosticador_ejecutado": False,
        })

        directions = acs_only
        selected_direction = candidate

        if acs_only.get("ok") and account_candidate:
            selected_account = account_candidate
            break

        if acs_code in global_stop_codes:
            break

    if not direction_attempts and candidates:
        batch_code = _clean(acs_batch.get("codigo")).upper()
        selected_direction = candidates[0]
        directions = acs_batch
        direction_attempts.append({
            "serial": _clean(selected_direction.get("serial")).upper(),
            "codigo": batch_code or "DIRECCIONES_RESPUESTA_INVALIDA",
            "acs_codigo": batch_code or "DIRECCIONES_RESPUESTA_INVALIDA",
            "ok": False,
            "criterio": _clean(selected_direction.get("criterio")),
            "cuenta_consulta": "",
            "clientes_encontrados": 0,
            "diagnosticador_ejecutado": False,
        })
    # FTTH_ACS_BATCH_RUNNER_V2_END

    if selected_account:
        diagnosticador_once = consultar_vecinos_por_cuenta(
            selected_account
        )

        if not isinstance(diagnosticador_once, dict):
            diagnosticador_once = {
                "ok": False,
                "codigo": "DIRECCIONES_RESPUESTA_INVALIDA",
                "cuenta_consulta": selected_account,
                "clientes": [],
                "clientes_encontrados": 0,
            }

        directions = diagnosticador_once

        if direction_attempts:
            last_attempt = direction_attempts[-1]

            diag_code = _clean(
                diagnosticador_once.get("codigo")
            ).upper()

            last_attempt["diagnosticador_ejecutado"] = True
            last_attempt["diagnosticador_codigo"] = diag_code
            last_attempt["codigo"] = diag_code
            last_attempt["ok"] = bool(
                diagnosticador_once.get("ok")
            )
            last_attempt["clientes_encontrados"] = int(
                diagnosticador_once.get(
                    "clientes_encontrados"
                )
                or 0
            )

    serial = _clean(
        selected_direction.get("serial")
        or primary_serial
    ).upper()

    directions_ok = bool(
        directions.get("ok")
    )

    attempted_codes = {
        _clean(
            item.get("codigo")
        ).upper()
        for item in direction_attempts
        if isinstance(item, dict)
    }

    global_failure = bool(
        attempted_codes.intersection(
            global_stop_codes
        )
    )

    all_candidates_attempted = bool(
        candidates
        and len(direction_attempts) == len(candidates)
    )

    empresas_sin_info = bool(
        vendor in {"ZTE", "HUAWEI"}
        and not directions_ok
        and not global_failure
        and all_candidates_attempted
        and empresas_summary.get("detectado")
    )

    final_code = (
        "FTTH_TRONCAL_DIRECCIONES_OK"
        if directions_ok
        else (
            "FTTH_EMPRESAS_NEGOCIOS_SIN_INFO"
            if empresas_sin_info
            else "FTTH_TRONCAL_DIRECCIONES_PARCIAL"
        )
    )

    return {
        "ok": directions_ok,
        "codigo": final_code,
        "tipo_respuesta": "direccion_clientes_troncal_ftth",
        "wo": work_order,
        "elemento_red": elemento,
        "ip_olt": ip,
        "vendor": vendor,
        "gpon_olt": gpon,
        "nodo": _clean(directions.get("nodo")),
        "rack": _clean(helix_data.get("rack")),
        "shelf": _clean(helix_data.get("shelf")),
        "slot": _clean(helix_data.get("slot")),
        "port": _clean(helix_data.get("port")),
        **trunk,
        "serial_referencia": serial,
        "onu_referencia": (
            selected_direction.get("onu")
            or selected_direction.get("ont_id")
            or selected.get("onu")
            or selected.get("ont_id")
            or ""
        ),
        "estado_onu_referencia": (
            selected_direction.get("phase_state")
            or selected_direction.get("run_state")
            or selected.get("phase_state")
            or selected.get("run_state")
            or ""
        ),
        "criterio_serial": (
            selected_direction.get("criterio")
            or selected.get("criterio", "")
        ),
        "onus_detectadas": len(baseinfo),
        "serial_principal": primary_serial,
        "seriales_candidatos": [
            _clean(item.get("serial")).upper()
            for item in candidates
        ],
        "intentos_direcciones_ftth": direction_attempts,
        "clasificacion_empresas_negocios": empresas_summary,
        "direcciones": directions,
        "duracion_seg": round(time.perf_counter() - started, 2),
    }
