"""Analisis, resumen y presentacion de resultados HFC."""

from typing import Any, Dict


def _to_int(value: Any, default: int = 0) -> int:
    try:
        txt = str(value or "").strip().replace("%", "").replace(",", ".")
        if not txt:
            return default
        return int(float(txt))
    except Exception:
        return default

def _is_casa_vendor(info: Dict[str, Any]) -> bool:
    vendor = f"{info.get('vendor', '')} {info.get('driver', '')}".upper()
    return "CASA" in vendor

def extraer_diagnostico_respuesta(texto: str) -> str:
    if not isinstance(texto, str):
        return ""

    marca = "Diagnóstico :"
    if marca not in texto:
        return ""

    return texto.split(marca, 1)[1].replace("=", "").strip()

def clasificar_conclusion_profunda(info: Dict[str, Any]) -> Dict[str, str]:
    """Genera conclusión ejecutiva según respuesta del motor base."""

    estado = str(info.get("estado") or "").upper()
    diagnostico = str(info.get("diagnostico") or "").lower()
    nodo = info.get("nodo") or "N/D"
    cmts = info.get("cmts") or "N/D"
    vendor = info.get("vendor") or "N/D"

    if "SERVICE_GROUP_NO_EXISTE" in estado or "does not exist" in diagnostico or "no se encontró service group" in diagnostico:
        return {
            "nivel": "REVISION_MARCACION",
            "badge": "Revisión de marcación",
            "resumen": (
                f"El nodo {nodo} no tiene asociación válida como service group en {cmts} ({vendor}). "
                "No se confirma caída masiva. Primero se debe validar macro, CMTS real, alias del nodo "
                "y posible migración o renombramiento."
            ),
            "accion": (
                "Validar marcación en macro/Máximo, confirmar si el nodo pertenece a otro CMTS "
                "y buscar alias alternos antes de escalar."
            ),
        }

    if "OK_SERVICE_GROUP_SIN_MODEMS" in estado or "sin módems" in diagnostico or "sin modems" in diagnostico:
        return {
            "nivel": "SG_SIN_MODEMS",
            "badge": "Service group sin módems",
            "resumen": (
                f"El service group del nodo {nodo} existe en {cmts}, pero no tiene módems asociados. "
                "Esto no confirma caída masiva. Puede ser nodo reservado, sin clientes, migrado "
                "o asociado lógicamente a otro grupo."
            ),
            "accion": "Validar QoE/PathTrak, ondas, inventario y si los módems están bajo otro service group.",
        }

    if "OK" in estado or "OPERATIVO" in estado:
        return {
            "nivel": "OPERATIVO",
            "badge": "Operativo",
            "resumen": f"El nodo {nodo} presenta estado operativo desde CMTS. Validar QoE y ondas para descartar degradación RF.",
            "accion": "No escalar como caída masiva salvo que QoE/ondas muestren degradación.",
        }

    if "REVISAR" in estado:
        return {
            "nivel": "REVISION",
            "badge": "Revisión técnica",
            "resumen": f"El nodo {nodo} requiere revisión adicional. La respuesta del motor no confirma caída ni normalidad completa.",
            "accion": "Cruzar CMTS, PathTrak, Máximo y macro antes de decidir escalamiento.",
        }

    return {
        "nivel": "INDETERMINADO",
        "badge": "Indeterminado",
        "resumen": f"No hay suficiente información para cerrar diagnóstico del nodo {nodo}. Se requiere cruce con PathTrak, macro y Máximo.",
        "accion": "Revisar manualmente la respuesta cruda del motor y las capturas generadas.",
    }

def debe_reforzar_con_hfc_real(info: Dict[str, Any]) -> bool:
    """
    Decide cuándo el diagnóstico profundo debe apoyarse en HFC real.

    El caso típico es CASA segmentado: el motor base consulta `show service group 6601`,
    devuelve Total 0 / REVISAR, pero HFC real descubre 6601A/6601B y módems reales.
    """

    estado = str(info.get("estado") or "").upper()
    decision = str(info.get("decision") or "").upper()
    diagnostico = str(info.get("diagnostico") or "").lower()
    total = _to_int(info.get("total"), 0)

    # Si el motor base no resolvio CMTS/IP/vendor, HFC_AUTO remoto
    # debe tener oportunidad de resolver el nodo antes de declararlo indeterminado.
    cmts = str(info.get("cmts") or "").strip()
    ip = str(info.get("ip") or "").strip()
    vendor = str(info.get("vendor") or "").strip()

    if not cmts or not ip or not vendor:
        return True

    if not _is_casa_vendor(info):
        return False

    señales_incertidumbre = [
        "REVISAR" in estado,
        "REVISAR" in decision,
        total == 0,
        "service group" in diagnostico,
        "no se pudo validar" in diagnostico,
        "no se encontró" in diagnostico,
        "does not exist" in diagnostico,
        "sin módems" in diagnostico,
        "sin modems" in diagnostico,
    ]

    return any(señales_incertidumbre)

def compactar_hfc_real(resultado: Dict[str, Any]) -> Dict[str, Any]:
    """Devuelve un resumen liviano del resultado HFC real para diagnóstico profundo."""

    if not resultado or not resultado.get("ok"):
        return {
            "ok": False,
            "error": resultado.get("error") if isinstance(resultado, dict) else "No ejecutado",
            "resolucion": resultado.get("resolucion") if isinstance(resultado, dict) else None,
        }

    data = resultado.get("data") or {}
    meta = data.get("metadata") or {}
    decision = data.get("decision") or {}
    totals = data.get("totals") or {}
    segmentos = []

    for s in data.get("segments") or []:
        segmentos.append({
            "name": s.get("name"),
            "exists": s.get("exists"),
            "upstreams": s.get("upstreams") or [],
            "upstream_suffixes": s.get("upstream_suffixes") or [],
            "qams_summary": s.get("qams_summary"),
            "modem_counts": s.get("modem_counts") or {},
        })

    total = _to_int(totals.get("total"), 0)
    online = _to_int(totals.get("online"), 0)
    pct = round((online / total) * 100, 1) if total else 0.0

    return {
        "ok": True,
        "nodo": meta.get("node") or resultado.get("nodo"),
        "cmts": meta.get("cmts") or resultado.get("cmts"),
        "ip": meta.get("ip") or resultado.get("ip"),
        "vendor": meta.get("vendor") or resultado.get("vendor"),
        "mode": data.get("mode"),
        "service_groups": data.get("service_groups") or [],
        "estado": decision.get("estado"),
        "decision": decision.get("decision"),
        "severidad": decision.get("severidad"),
        "confianza": decision.get("confianza"),
        "resumen": decision.get("resumen"),
        "accion": decision.get("accion"),
        "totals": totals,
        "pct_online": pct,
        "segments": segmentos,
        "show_cable_modem_rows_total": data.get("show_cable_modem_rows_total"),
        "json_path": resultado.get("json_path"),
        "duracion_seg": resultado.get("duracion_seg"),
        "fuente_resolucion": resultado.get("fuente_resolucion"),
    }

def conclusion_desde_hfc_real(nodo: str, hfc: Dict[str, Any]) -> Dict[str, str]:
    if not hfc.get("ok"):
        return {
            "nivel": "REVISION_HFC_REAL",
            "badge": "HFC real no concluyente",
            "resumen": f"No se pudo cerrar dictamen HFC real para el nodo {nodo}. Se conserva el diagnóstico base y se requiere revisión manual.",
            "accion": hfc.get("error") or "Revisar salida técnica del motor HFC real.",
        }

    estado = str(hfc.get("estado") or "").upper()
    decision = str(hfc.get("decision") or "").upper()
    total = _to_int((hfc.get("totals") or {}).get("total"), 0)
    online = _to_int((hfc.get("totals") or {}).get("online"), 0)
    pct = hfc.get("pct_online", 0.0)
    service_groups = ", ".join(hfc.get("service_groups") or []) or "N/D"

    if "OPERATIVO" in estado and "NO_ESCALAR" in decision:
        return {
            "nivel": "OK_HFC_REAL",
            "badge": "Operativo CMTS real",
            "resumen": (
                f"El nodo {nodo} está operativo desde CMTS. HFC real detectó {total} módems, "
                f"{online} online ({pct}%) y service groups {service_groups}."
            ),
            "accion": hfc.get("accion") or "No escalar como caída masiva. Mantener monitoreo.",
        }

    if "ESCALAR" in decision or "MASIVA" in decision:
        return {
            "nivel": "ALERTA_HFC_REAL",
            "badge": "Escalamiento sugerido",
            "resumen": hfc.get("resumen") or f"El diagnóstico HFC real detectó condición de afectación para el nodo {nodo}.",
            "accion": hfc.get("accion") or "Escalar según procedimiento NOC.",
        }

    return {
        "nivel": "REVISION_HFC_REAL",
        "badge": "Revisión con HFC real",
        "resumen": hfc.get("resumen") or f"El diagnóstico HFC real entregó estado {hfc.get('estado') or 'N/D'} para el nodo {nodo}.",
        "accion": hfc.get("accion") or "Cruzar resultado con PathTrak y operación antes de cerrar.",
    }

def info_desde_hfc_real(info_base: Dict[str, Any], hfc: Dict[str, Any]) -> Dict[str, Any]:
    """Reemplaza métricas débiles del motor base por métricas HFC reales."""

    info = dict(info_base or {})
    totals = hfc.get("totals") or {}
    total = _to_int(totals.get("total"), 0)
    online = _to_int(totals.get("online"), 0)
    offline = _to_int(totals.get("offline"), 0)
    init = _to_int(totals.get("init"), 0)
    pct = hfc.get("pct_online", 0.0)

    info.update({
        "nodo": hfc.get("nodo") or info.get("nodo"),
        "cmts": hfc.get("cmts") or info.get("cmts"),
        "ip": hfc.get("ip") or info.get("ip"),
        "vendor": hfc.get("vendor") or info.get("vendor"),
        "estado": hfc.get("estado") or info.get("estado"),
        "severidad": hfc.get("severidad") or info.get("severidad"),
        "decision": hfc.get("decision") or info.get("decision"),
        "total": str(total),
        "online": str(online),
        "offline": str(offline),
        "init": str(init),
        "pct_online": str(pct),
        "diagnostico": hfc.get("resumen") or info.get("diagnostico"),
    })

    return info

def _deco_compacto_counts(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}

    consolidado = data.get("consolidated_modem_counts")
    if isinstance(consolidado, dict):
        return consolidado

    segmentos = data.get("segments") or []
    total = online = init = offline = dbc = ranging = other = 0

    for seg in segmentos:
        c = seg.get("modem_counts") or {}
        total += int(c.get("total") or 0)
        online += int(c.get("online") or 0)
        init += int(c.get("init") or 0)
        offline += int(c.get("offline") or 0)
        dbc += int(c.get("dbc") or 0)
        ranging += int(c.get("ranging") or 0)
        other += int(c.get("other") or 0)

    return {
        "total": total,
        "online": online,
        "init": init,
        "offline": offline,
        "dbc": dbc,
        "ranging": ranging,
        "other": other,
    }

def _deco_formatear_hfc_real_compacto(resp: dict) -> dict:
    try:
        resultados = resp.get("respuesta", {}).get("resultados") or []
        if not resultados:
            return resp

        r = resultados[0]
        data = r.get("data") or {}
        metadata = data.get("metadata") or {}

        nodo = r.get("nodo") or metadata.get("node") or ""
        cmts = r.get("cmts") or metadata.get("cmts") or ""
        ip = r.get("ip") or metadata.get("ip") or ""
        vendor = r.get("vendor") or metadata.get("vendor") or ""

        counts = _deco_compacto_counts(data)
        total = int(counts.get("total") or 0)
        online = int(counts.get("online") or 0)
        init = int(counts.get("init") or 0)
        offline = int(counts.get("offline") or 0)
        dbc = int(counts.get("dbc") or 0)
        ranging = int(counts.get("ranging") or 0)
        other = int(counts.get("other") or 0)

        pct_online = round((online / total) * 100, 1) if total else 0.0

        segmentos = data.get("segments") or []
        segmentos_lineas = []

        for seg in segmentos:
            nombre = seg.get("name", "")
            upstreams = ", ".join(seg.get("upstreams") or [])
            c = seg.get("modem_counts") or {}
            segmentos_lineas.append(
                f"- {nombre} | US {upstreams} | "
                f"Total {c.get('total', 0)} | Online {c.get('online', 0)} | "
                f"INIT {c.get('init', 0)} | Offline {c.get('offline', 0)}"
            )

        estado = "OPERATIVO_CMTS"
        decision = "NO_ESCALAR_AUTOMATICO"
        severidad = "BAJA"

        if total == 0:
            estado = "REVISAR"
            decision = "REVISAR / POSIBLE ESCALAMIENTO"
            severidad = "MEDIA"
        elif offline > 0 or init > 0:
            estado = "REVISAR_DEGRADACION"
            decision = "REVISAR / VALIDAR AFECTACION"
            severidad = "MEDIA"

        txt_path = r.get("txt_path") or ""
        json_path = r.get("json_path") or ""

        respuesta = []
        respuesta.append("=" * 72)
        respuesta.append("RESPUESTA NOC")
        respuesta.append("=" * 72)
        respuesta.append(f"Nodo        : {nodo}")
        respuesta.append(f"CMTS        : {cmts}")
        respuesta.append(f"IP          : {ip}")
        respuesta.append(f"Vendor      : {vendor}")
        respuesta.append("Driver      : HFC_REAL_CASA")
        respuesta.append("-" * 72)
        respuesta.append(f"Estado      : {estado}")
        respuesta.append(f"Severidad   : {severidad}")
        respuesta.append(f"Decisión    : {decision}")
        respuesta.append("-" * 72)
        respuesta.append(f"Total       : {total}")
        respuesta.append(f"Online      : {online}")
        respuesta.append(f"Registered  : {online}")
        respuesta.append(f"Active      : {online}")
        respuesta.append(f"Offline     : {offline}")
        respuesta.append(f"Init        : {init}")
        respuesta.append(f"DBC         : {dbc}")
        respuesta.append(f"Ranging     : {ranging}")
        respuesta.append(f"Other       : {other}")
        respuesta.append(f"% Online    : {pct_online}")
        respuesta.append("-" * 72)
        respuesta.append("Diagnóstico :")

        if total > 0 and online == total and init == 0 and offline == 0:
            respuesta.append(
                f"El nodo {nodo} está operativo en CMTS. "
                f"Tiene {total} módems asociados por upstream real y {online}/{total} online."
            )
            respuesta.append("No escalar como caída masiva. Mantener monitoreo.")
        else:
            respuesta.append(
                f"El nodo {nodo} requiere revisión. "
                f"Total {total}, Online {online}, INIT {init}, Offline {offline}."
            )

        if segmentos_lineas:
            respuesta.append("")
            respuesta.append("Segmentos detectados:")
            respuesta.extend(segmentos_lineas)

        if txt_path:
            respuesta.append("")
            respuesta.append(f"TXT crudo    : {txt_path}")

        if json_path:
            respuesta.append(f"JSON crudo   : {json_path}")

        respuesta.append("=" * 72)

        return {
            "ok": True,
            "tipo_respuesta": "hfc_real_diagnostic_compacto",
            "metodo": resp.get("metodo") or "estado_nodo_auto_diagnostico_real",
            "fallback_deco": True,
            "respuesta": "\n".join(respuesta),
            "raw_paths": {
                "txt": txt_path,
                "json": json_path,
            },
            "resumen": {
                "nodo": nodo,
                "cmts": cmts,
                "ip": ip,
                "vendor": vendor,
                "estado": estado,
                "decision": decision,
                "severidad": severidad,
                "total": total,
                "online": online,
                "init": init,
                "offline": offline,
                "pct_online": pct_online,
                "segmentos": [s.get("name") for s in segmentos],
            },
        }

    except Exception as exc:
        resp["compact_error"] = f"{type(exc).__name__}: {exc}"
        return resp
