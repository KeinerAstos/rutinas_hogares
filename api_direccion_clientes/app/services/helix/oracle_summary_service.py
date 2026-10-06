from __future__ import annotations

import re
import time
from typing import Any

from app.services.helix.oracle_repository import OracleRepository, get_oracle_repository
from app.services.helix.summary import parse_descripcion_impacto_troncal
from app.services.helix.work_order_title_parser import parse_work_order_title


_WO_RE = re.compile(r"^WO\d{13,14}$", re.IGNORECASE)
_PARSER_FIELDS = (
    "tipo_red", "tipo_elemento", "nodo_detectado", "nodos_detectados",
    "estado_nodo", "es_hfc", "es_pathtrak", "es_ftth", "es_troncal",
    "es_mw", "tecnologia_pon", "elemento_red", "rack", "shelf", "slot",
    "port", "frame", "subslot", "port_informado", "parser_version",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _merge_topology(target: dict[str, Any], parsed: dict[str, Any]) -> None:
    for field in _PARSER_FIELDS:
        value = parsed.get(field)
        if value not in (None, "", [], False) and target.get(field) in (None, "", [], False):
            target[field] = value
    # Máscara/campos de la descripción completa corrigen los del resumen.
    for field in ("rack", "shelf", "slot", "port", "frame", "subslot"):
        if parsed.get(field):
            target[field] = parsed[field]


def _parse_into(target: dict[str, Any], value: Any, source: str) -> None:
    if _text(value):
        _merge_topology(target, parse_work_order_title(_text(value), source=source))


def _fill_ci_topology(target: dict[str, Any], name: Any, description: Any, source: str) -> None:
    combined = " ".join(part for part in (_text(name), _text(description)) if part)
    if not combined:
        return
    # AST/CI descriptions commonly encode BOARD=1-1-4_FGTH,PORT=10.
    board = re.search(r"\bBOARD\s*=\s*(\d+)-(\d+)-(\d+)(?:_[A-Z0-9]+)?", combined, re.I)
    if board:
        for key, value in zip(("rack", "shelf", "slot"), board.groups()):
            if not target.get(key):
                target[key] = value
    port = re.search(r"\bPORT\s*=\s*(\d+)", combined, re.I)
    if port and not target.get("port"):
        target["port"] = port.group(1)
    # The CI name carries the network element and often the FTTH board.
    element = re.search(r"\d+-\d+-\d+-[A-Z0-9]+-\d+_([A-Z0-9][A-Z0-9._-]+)", combined, re.I)
    if element and not target.get("elemento_red"):
        target["elemento_red"] = element.group(1).upper()
    if element and not target.get("tipo_red"):
        target["tipo_red"] = "FTTH"
        target["es_ftth"] = True
    _parse_into(target, combined, source)


def consultar_resumen_ot_oracle(
    ot: str, *, repository: OracleRepository | None = None
) -> dict[str, Any]:
    started = time.monotonic()
    normalized = _text(ot).upper()
    if not _WO_RE.fullmatch(normalized):
        return {"ok": False, "codigo": "HELIX_OT_INVALIDA", "origen": "ORACLE", "data": {}}
    repo = repository or get_oracle_repository()
    wo = repo.get_work_order(normalized)
    if not wo:
        return {"ok": False, "codigo": "ORACLE_WO_NO_ENCONTRADA", "origen": "ORACLE", "data": {"numero_ot": normalized}}

    summary = _text(wo.get("summary"))
    detail = _text(wo.get("detailed_description"))
    data: dict[str, Any] = {
        "numero_ot": normalized,
        "numero_ot_consultada": _text(wo.get("work_order_id")) or normalized,
        "uso_fallback_wo": False,
        "estado_ot": {0:"Assigned",1:"Pending",2:"Waiting Approval",3:"Planning",4:"In Progress",5:"Completed",6:"Rejected",7:"Cancelled",8:"Closed"}.get(wo.get("status"), _text(wo.get("status"))),
        "incidente_relacionado": _text(wo.get("root_incident")),
        "aliado": _text(wo.get("aliado")),
        "ciudad": _text(wo.get("ciudad__c")),
        "regional": _text(wo.get("regional")),
        "estado_ofsc": _text(wo.get("ztmp_status_ofsc")),
        "origen_datos": "ORACLE",
        "uso_fallback": False,
        "tiempo_ofsc_seg": None,
        "categoria_operacional": " > ".join(v for v in (_text(wo.get("categorization_tier_1")), _text(wo.get("categorization_tier_2")), _text(wo.get("categorization_tier_3"))) if v),
        "titulo_ot": summary,
        "fuente_titulo": "ORACLE.WOI_WORKORDER.SUMMARY",
        "descripcion_ot": detail,
        "ci": "",
        "descripcion_ci": "",
        "descripcion_impacto": "",
        "impacto_gpon": "",
        "impacto_id_troncal": "",
        "impacto_descripcion_troncal": "",
        "impacto_nombre_comercial": "",
        "es_ges": False,
        "ges_direcciones": [],
        "ges_fuentes_notas": [],
        "ges_notas_revisadas": False,
        "puertos_afectados": [],
        "fuente_topologia": "ORACLE.WOI_WORKORDER.SUMMARY",
        "oracle_ci_wo": _text(wo.get("xca_nombre_articulo_config")),
        "oracle_ci_inc": "",
    }
    _parse_into(data, summary, "ORACLE.WOI_WORKORDER.SUMMARY")
    _parse_into(data, detail, "ORACLE.WOI_WORKORDER.DETAILED_DESCRIPTION")
    # The free text can mistake phrases such as "NODOS AFECTADOS" for a
    # physical node. Prefer the validated ARADMIN.NODE field in that case.
    if data.get("tipo_red") == "FTTH" and data.get("nodo_detectado") == "AFECTADOS":
        data["nodo_detectado"] = _text(wo.get("node"))
        data["nodos_detectados"] = [data["nodo_detectado"]] if data["nodo_detectado"] else []
        data["estado_nodo"] = "UN_NODO" if data["nodo_detectado"] else "SIN_NODO"
    _fill_ci_topology(data, wo.get("xca_nombre_articulo_config"), wo.get("xca_descripcion_articulo_confi"), "ORACLE.WOI_WORKORDER.CI")
    data["ci"] = _text(wo.get("xca_nombre_articulo_config"))
    data["descripcion_ci"] = _text(wo.get("xca_descripcion_articulo_confi"))
    data["fuente_topologia"] = "ORACLE.WOI_WORKORDER"

    incident = None
    inc_number = _text(wo.get("root_incident"))
    if inc_number:
        incident = repo.get_incident(inc_number)
    if incident:
        for target, source in (("ci", "hpd_ci"), ("descripcion_impacto", "descripcion_de_impacto")):
            if _text(incident.get(source)):
                data[target] = _text(incident[source])
        data.update(parse_descripcion_impacto_troncal(data["descripcion_impacto"]))
        data["oracle_ci_inc"] = _text(incident.get("hpd_ci"))
        _parse_into(data, incident.get("description"), "ORACLE.HPD_HELP_DESK.DESCRIPTION")
        _parse_into(data, incident.get("detailed_decription"), "ORACLE.HPD_HELP_DESK.DETAILED_DECRIPTION")
        _fill_ci_topology(data, incident.get("hpd_ci"), "", "ORACLE.HPD_HELP_DESK.HPD_CI")
        topology_complete = (
            bool(data.get("nodo_detectado"))
            if data.get("tipo_red") == "HFC"
            else all(data.get(key) for key in ("rack", "shelf", "slot", "port"))
        )
        if not topology_complete and _text(incident.get("hpd_ci_reconid")):
            ci = repo.get_ci(_text(incident["hpd_ci_reconid"]))
            if ci:
                _fill_ci_topology(data, ci.get("name"), " ".join(_text(ci.get(key)) for key in ("item", "short_description", "label")), "ORACLE.AST_BASEELEMENT")
    if data.get("tipo_red") == "FTTH" and _text(data.get("nodo_detectado")).upper() in {"AFECTADO", "AFECTADOS"}:
        data["nodo_detectado"] = _text(wo.get("node"))
        data["nodos_detectados"] = [data["nodo_detectado"]] if data["nodo_detectado"] else []
        data["estado_nodo"] = "UN_NODO" if data["nodo_detectado"] else "SIN_NODO"

    # Final network classification trusts confirmed CI topology over title text.
    element = _text(data.get("elemento_red")).upper()
    if element.startswith(("ZAC-", "HAC-")):
        is_trunk = bool(data.get("es_troncal")) or data.get("tipo_elemento") == "TRONCAL"
        data["tipo_red"] = "FTTH"
        data["es_ftth"] = True
        data["es_hfc"] = False
        data["tipo_elemento"] = "TRONCAL" if is_trunk else "OLT_PON"
    elif data.get("tipo_red") == "HFC" or data.get("es_hfc") is True:
        data["tipo_red"] = "HFC"
        data["es_hfc"] = True
        data["es_ftth"] = False
        data["tipo_elemento"] = "NODO"

    data["port_informado"] = bool(data.get("port"))
    data["puertos_afectados"] = [p.strip() for p in str(data.get("port") or "").split(",") if p.strip()]
    if re.match(r"^GES(?:\s|[-_:])", summary, re.IGNORECASE):
        # SmartIT still owns the GES notes/address enrichment in this phase.
        data["es_ges"] = True
        data["tipo_red"] = "GES"
        data["codigo"] = "ORACLE_GES_SMARTIT_REQUIRED"
        return {"ok": True, "tipo_respuesta": "helix_resumen_ot", "codigo": "ORACLE_GES_SMARTIT_REQUIRED", "origen": "ORACLE", "respuesta": "La WO GES requiere el enriquecimiento temporal de SmartIT.", "data": data, "duracion_seg": round(time.monotonic() - started, 3), "error": ""}
    data["ok"] = True
    duration = round(time.monotonic() - started, 3)
    print(f"DIRECCIONES_ORACLE wo={normalized} status=OK ms={round(duration * 1000)}", flush=True)
    print(f"DIRECCIONES_ORACLE topology={data.get('tipo_red') or 'N/A'} element={data.get('elemento_red') or data.get('nodo_detectado') or 'N/A'} slot={data.get('slot') or 'N/A'} port={data.get('port') or 'N/A'}", flush=True)
    return {"ok": True, "tipo_respuesta": "helix_resumen_ot", "codigo": "HELIX_OT_RESUMEN_OK", "origen": "ORACLE", "respuesta": f"Resumen de {normalized} consultado correctamente en Oracle.", "data": data, "duracion_seg": duration, "error": ""}
