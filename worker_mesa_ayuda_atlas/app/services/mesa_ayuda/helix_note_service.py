# -*- coding: utf-8 -*-
"""Contexto para preparar notas Helix desde Mesa de Ayuda.

V1:
- no escribe en Helix
- prepara metadatos para copiar manualmente la conversacion
- el destino operativo es siempre el INC relacionado, nunca la WO
"""

from __future__ import annotations

from typing import Any

CASO_TEMPORAL = "2183643903"
AGENTE = "ATLAS"

SALUDO = (
    "Te doy la bienvenida a nuestro canal de atenci\u00f3n en WhatsApp, "
    "mi nombre es ATLAS, y con gusto me encargar\u00e9 de tu solicitud "
    f"Caso #{CASO_TEMPORAL}"
)

SOP_POR_TIPO: dict[str, str] = {
    "DIRECCIONES_CLIENTES": "SOP_HOG_T1:Direccion de clientes",
    "ERROR_ESCALAMIENTO": "SOP_HOG_T1:Error de escalamiento",
    "GRAFICA_PATHTRAK": "SOP_HOG_T1:Solicitudes Gr\u00e1fica Pathtrak",
    "FILTRACION_RUIDO": "SOP_HOG_T1:Recepcion de informaci\u00f3n filtraci\u00f3n de ruido",
    "SOPORTE_APP_CONECTA": "SOP_HOG_T1:Soporte APP conecta por ca\u00edda de plataforma",
    "CANCELACION_OT": "SOP_HOG_T1:Cancelacion de OT",
    "GENERACION_OT_COAXIAL": "SOP_HOG_T1:Generacion de OT a COAX",
    "OTRO_TIPO_SOLICITUD": "SOP_HOG_T1:otro tipo de solicitud",
    "GENERACION_OT_FIBRA": "SOP_HOG_T1:Generacion de OT a Fibra",
    "REDES_NEUTRAS": "SOP_HOG_T1:Solicitudes redes neutras",
}

TIPOS_SIN_NOTA = {
    "CONFIRMACION_FTTH",
    "CONFIRMACION_HFC",
}


def contexto_nota_helix(session: dict[str, Any]) -> dict[str, Any]:
    tipo_codigo = str(session.get("tipo_codigo") or "").strip().upper()
    sop_titulo = SOP_POR_TIPO.get(tipo_codigo, "")

    # HELIX_NOTE_AUTO_FINAL_CONTEXT_V1
    wo = str(session.get("wo") or "").strip().upper()
    estado = str(session.get("estado") or "").strip().upper()
    solicitud_id = str(session.get("solicitud_id") or "").strip()
    terminal_nota = (
        estado.startswith("RESULTADO_")
        and estado not in {"RESULTADO_GENERACION_OT_DRYRUN"}
    )

    incidente_relacionado = ""
    for key in (
        "redes_neutras_incidente",
        "incidente_relacionado",
        "helix_incidente",
        "incidente",
    ):
        value = str(session.get(key) or "").strip().upper()
        if value.startswith("INC"):
            incidente_relacionado = value
            break

    publicar_automaticamente = bool(
        sop_titulo
        and wo
        and solicitud_id
        and terminal_nota
    )

    return {
        "habilitado": bool(sop_titulo),
        "caso": CASO_TEMPORAL,
        "agente": AGENTE,
        "saludo": SALUDO if sop_titulo else "",
        "sop_titulo": sop_titulo,
        "tipo_codigo": tipo_codigo,
        "tipo_nombre": str(session.get("tipo_nombre") or "").strip(),
        "wo": wo,
        "destino": "INC_RELACIONADO",
        "modo": "AUTO_AL_CIERRE" if publicar_automaticamente else "PREPARADO",
        "escritura_helix": bool(sop_titulo),
        "preparado_para_scraping": True,
        "terminal_nota": terminal_nota,
        "publicar_automaticamente": publicar_automaticamente,
        "incidente_relacionado": incidente_relacionado,
        "solicitud_id": solicitud_id,
    }