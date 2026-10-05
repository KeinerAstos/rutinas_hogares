# -*- coding: utf-8 -*-
"""Motor conversacional de Mesa de Ayuda."""

from __future__ import annotations



from app.services.mesa_ayuda.confirmaciones_router_service import (
    obtener_confirmacion,
)
from app.services.mesa_ayuda.direcciones_router_service import consultar_direcciones_por_wo
import json
import re
from typing import Any
from uuid import uuid4

from app.services.mesa_ayuda.pathtrak_router_service import (
    capturar_pathtrak,
)
# ATLAS_HELIX_WO_SUMMARY_EXTERNAL_8023_V1
from app.services.helix.incident_related_batch_http_client import consultar_resumen_ot as consultar_resumen_ot_helix

from app.services.mesa_ayuda.ot_relacionada_router_service import (
    cancelar_ot_relacionada,
    confirmar_ot_relacionada,
    consultar_ot_relacionada_dryrun,
    creacion_ot_habilitada,
)

from app.services.mesa_ayuda.redes_neutras_router_service import (
    consultar_redes_neutras_por_wo,
)

from app.services.mesa_ayuda.catalogo import (
    obtener_opcion,
    opciones_publicas,
    texto_menu,
)
from app.services.mesa_ayuda.session_service import (
    guardar_sesion,
    nueva_sesion,
    obtener_sesion,
    stats as session_stats,
)
from app.services.mesa_ayuda.helix_note_service import contexto_nota_helix
# MESA_OT_RELACIONADA_DRYRUN_V1
# MESA_NOTA_HELIX_CONTEXT_V1

_WO_FULL = re.compile(r"^WO(\d{13,14})$", re.IGNORECASE)
_ONLY_DIGITS = re.compile(r"^\d{13,14}$")

_RESET_WORDS = {
    "inicio",
    "iniciar",
    "reiniciar",
    "nueva",
    "nuevo",
    "nueva solicitud",
    "menu",
    "men\u00fa",
    "volver",
    "volver al menu",
    "volver al men\u00fa",
    "atras",
    "atr\u00e1s",
}

_STATUS_WORDS = {
    "",
    "estado",
    "continuar",
}


def _normalizar_wo(value: str) -> str | None:
    text = re.sub(r"\s+", "", str(value or "").strip().upper())

    match = _WO_FULL.fullmatch(text)
    if match:
        return f"WO{match.group(1)}"

    if _ONLY_DIGITS.fullmatch(text):
        return f"WO{text}"

    return None


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in session.items()
        if not str(key).startswith("_")
    }


def _response(
    *,
    session: dict[str, Any],
    codigo: str,
    respuesta: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "tipo_respuesta": "mesa_ayuda_chat",
        "codigo": codigo,
        "estado": session.get("estado"),
        "respuesta": respuesta,
        "opciones": opciones_publicas(),
        "solicitud": {
            "id": session.get("solicitud_id"),
            "tipo_numero": session.get("tipo_numero"),
            "tipo_codigo": session.get("tipo_codigo"),
            "tipo_nombre": session.get("tipo_nombre"),
            "wo": session.get("wo"),
        },
        "sesion": _public_session(session),
        "nota_helix_contexto": contexto_nota_helix(session),
        "fase": "SIMULACION_V1_2",
    }


# MESA_AYUDA_PATHTRAK_V2
# MESA_PATHTRAK_DUAL_EVIDENCE_V4B
def _ejecutar_pathtrak_directo(
    nodo: str,
    tipo_captura: str = "qoe",
) -> dict[str, Any]:

    nodo = str(nodo or "").strip().upper()
    tipo_captura = str(
        tipo_captura or "qoe"
    ).strip().lower()

    # MESA_PATHTRAK_EXTERNAL_API_8024_V1
    result = capturar_pathtrak(
        nodo,
        tipo_captura=tipo_captura,
    )

    if not isinstance(result, dict):
        return {
            "ok": False,
            "codigo": "MESA_PATHTRAK_RESPUESTA_INVALIDA",
            "error": "PathTrak no devolvio una respuesta estructurada.",
        }

    return result


def _consultar_resumen_helix_con_reintento(
    wo: str,
    *,
    max_intentos: int = 2,
) -> dict:
    """Reintenta una sola vez si Helix lanza una excepcion transitoria."""
    ultimo_error = None

    for intento in range(1, max_intentos + 1):
        try:
            return consultar_resumen_ot_helix(wo)
        except Exception as exc:
            ultimo_error = exc
            if intento >= max_intentos:
                raise

    if ultimo_error is not None:
        raise ultimo_error

    return {}


def _procesar_confirmacion_claro_te_ayuda(
    session: dict,
    *,
    tipo: str,
) -> dict:

    tipo = str(tipo or "").strip().upper()

    # MESA_CONFIRMACIONES_EXTERNAL_API_8026_V1
    result = obtener_confirmacion(tipo)

    session["estado"] = "ESPERANDO_TIPO"
    session["wo"] = ""

    if not isinstance(result, dict):
        return {
            "ok": False,
            "codigo": "MESA_CONFIRMACIONES_RESPUESTA_INVALIDA",
            "estado": "ESPERANDO_TIPO",
            "respuesta": "La API de confirmaciones devolvio una respuesta invalida.",
            "volver_menu": True,
            "pide_wo": False,
        }

    result["estado"] = "ESPERANDO_TIPO"
    result["volver_menu"] = True
    result["pide_wo"] = False

    return result


def _procesar_grafica_pathtrak(
    *,
    session: dict[str, Any],
    wo: str,
) -> dict[str, Any]:
    helix = _consultar_resumen_helix_con_reintento(wo)
    if not isinstance(helix, dict) or not helix.get("ok"):
        result = _response(
            session=session,
            codigo=str(
                (helix or {}).get("codigo")
                or "MESA_PATHTRAK_HELIX_ERROR"
            ),
            respuesta=str(
                (helix or {}).get("respuesta")
                or (helix or {}).get("error")
                or "No fue posible validar la WO en Helix."
            ),
        )
        result["helix"] = helix
        return result

    data = helix.get("data")
    if not isinstance(data, dict):
        data = {}

    titulo = str(data.get("titulo_ot") or "").strip()
    tipo_red = str(data.get("tipo_red") or "").strip().upper()
    estado_nodo = str(data.get("estado_nodo") or "").strip().upper()
    nodo = str(data.get("nodo_detectado") or "").strip().upper()

    # MESA_PATHTRAK_SOC_QOE_HFC_COMPAT_V1
    #
    # Algunas WO SOC / Degradacion QoE pertenecientes a HFC no contienen
    # literalmente HFC/FOHFC en el titulo. Helix puede identificar correctamente
    # el nodo, pero dejar tipo_red vacio.
    #
    # Esta compatibilidad se aplica solamente al flujo Grafica PathTrak.
    categoria_operacional = str(
        data.get("categoria_operacional") or ""
    ).strip().upper()

    titulo_upper = titulo.upper()

    es_soc_qoe_pathtrak_hfc = (
        not tipo_red
        and categoria_operacional.startswith("SERVICIOS FIJOS")
        and estado_nodo == "UN_NODO"
        and bool(nodo)
        and (
            "QOE" in titulo_upper
            or "DEGRADACION > QOE" in categoria_operacional
        )
        and (
            "(SOC)" in titulo_upper
            or "DEGRADACION QOE" in titulo_upper
        )
        and not bool(data.get("es_ftth"))
        and not bool(data.get("es_mw"))
        and not bool(data.get("es_troncal"))
    )

    if es_soc_qoe_pathtrak_hfc:
        tipo_red = "HFC"

    session["helix_tipo_red"] = tipo_red
    session["helix_nodo"] = nodo
    session["helix_titulo"] = titulo

    if tipo_red != "HFC":
        session["estado"] = "RESULTADO_PATHTRAK"
        session = guardar_sesion(session)

        result = _response(
            session=session,
            codigo="MESA_PATHTRAK_WO_NO_HFC",
            respuesta=(
                "La WO fue validada, pero no corresponde "
                "a una solicitud HFC compatible con PathTrak.\n\n"
                f"WO: {wo}\n"
                f"Red: {tipo_red or 'NO_IDENTIFICADA'}"
            ),
        )
        result["helix"] = helix
        return result

    if estado_nodo != "UN_NODO" or not nodo:
        session["estado"] = "RESULTADO_PATHTRAK"
        session = guardar_sesion(session)

        result = _response(
            session=session,
            codigo="MESA_PATHTRAK_NODO_NO_UNICO",
            respuesta=(
                "Helix valido la WO, pero no identifico "
                "un unico nodo HFC para consultar PathTrak.\n\n"
                f"WO: {wo}"
            ),
        )
        result["helix"] = helix
        return result

    # MESA_PATHTRAK_QOE_RUIDO_COMBINADO_V1
    #
    # QoE y Ruido se solicitan en una sola sesion PathTrak.
    # La API puede devolver:
    #   - 2 capturas en funcionamiento normal.
    #   - 3 capturas cuando Spectrum presenta No RCI.
    pathtrak_result = _ejecutar_pathtrak_directo(
        nodo,
        "qoe_ruido",
    )

    if not isinstance(pathtrak_result, dict):
        pathtrak_result = {}

    respuesta_pathtrak = pathtrak_result.get("respuesta")

    if not isinstance(respuesta_pathtrak, dict):
        respuesta_pathtrak = {}

    capturas_api = respuesta_pathtrak.get("capturas")

    if not isinstance(capturas_api, list):
        capturas_api = []

    capturas = []

    for item in capturas_api:
        if not isinstance(item, dict):
            continue

        public_url = str(
            item.get("public_url") or ""
        ).strip()

        tipo_item = str(
            item.get("tipo") or ""
        ).strip()

        label = str(
            item.get("tipo_label")
            or tipo_item
            or "Evidencia PathTrak"
        ).strip()

        capturas.append(
            {
                "ok": bool(
                    item.get("ok")
                    and public_url
                ),
                "tipo": tipo_item,
                "label": label,
                "tipo_label": label,
                "region": str(
                    item.get("region")
                    or respuesta_pathtrak.get("region")
                    or ""
                ).strip().upper(),
                "url": item.get("url"),
                "screenshot": item.get("screenshot"),
                "public_url": public_url,
                "error": str(
                    item.get("error") or ""
                ).strip(),
                "codigo": str(
                    pathtrak_result.get("codigo")
                    or ""
                ).strip(),
            }
        )

    exitosas = [
        item
        for item in capturas
        if item.get("ok")
        and item.get("public_url")
    ]

    region = str(
        respuesta_pathtrak.get("region") or ""
    ).strip().upper()

    if not region:
        for item in capturas:
            if item.get("region"):
                region = item["region"]
                break

    estado_pathtrak = str(
        respuesta_pathtrak.get("estado") or ""
    ).strip().upper()

    pathtrak_ok = bool(
        pathtrak_result.get("ok") is True
        and exitosas
    )

    session["estado"] = "RESULTADO_PATHTRAK"
    session["pathtrak_ok"] = pathtrak_ok
    session = guardar_sesion(session)

    if pathtrak_ok and estado_pathtrak == "PARCIAL":
        codigo = "MESA_PATHTRAK_QOE_RUIDO_PARCIAL"

        detalle = str(
            respuesta_pathtrak.get("mensaje") or ""
        ).strip()

        respuesta_usuario = (
            "Evidencias PathTrak generadas.\n\n"
            f"WO: {wo}\n"
            f"Nodo: {nodo}\n"
            f"Region: {region or 'NO_IDENTIFICADA'}\n\n"
            f"{detalle or 'La captura de Spectrum fue parcial.'}"
        )

    elif pathtrak_ok:
        codigo = "MESA_PATHTRAK_QOE_RUIDO_OK"

        respuesta_usuario = (
            "Evidencias PathTrak listas.\n\n"
            f"WO: {wo}\n"
            f"Nodo: {nodo}\n"
            f"Region: {region or 'NO_IDENTIFICADA'}\n\n"
            "QoE y Ruido fueron generados correctamente."
        )

    else:
        codigo = "MESA_PATHTRAK_ERROR"

        error_pathtrak = str(
            pathtrak_result.get("error")
            or respuesta_pathtrak.get("mensaje")
            or "PathTrak no pudo generar las evidencias."
        ).strip()

        respuesta_usuario = (
            "Helix identifico el nodo, pero PathTrak "
            "no pudo generar las evidencias solicitadas.\n\n"
            f"WO: {wo}\n"
            f"Nodo: {nodo}\n\n"
            f"Detalle: {error_pathtrak}"
        )

    result = _response(
        session=session,
        codigo=codigo,
        respuesta=respuesta_usuario,
    )

    result["helix"] = helix

    result["pathtrak"] = {
        "ok": pathtrak_ok,
        "codigo": codigo,
        "estado": estado_pathtrak,
        "combinado": pathtrak_result,
    }

    result["resultado_operativo"] = {
        "tipo": "GRAFICA_PATHTRAK",
        "modo": "QOE_RUIDO_COMBINADO",
        "wo": wo,
        "nodo": nodo,
        "region": region,
        "titulo_ot": titulo,
        "estado": estado_pathtrak,
        "capturas": capturas,
        "public_urls": [
            item["public_url"]
            for item in exitosas
        ],
    }

    return result




# MESA_DIRECCIONES_FTTH_V2
def _procesar_direcciones_clientes(
    *,
    session: dict[str, Any],
    wo: str,
) -> dict[str, Any]:
    session["estado"] = "PROCESANDO_DIRECCIONES"
    session = guardar_sesion(session)

    try:
        result = consultar_direcciones_por_wo(wo)
    except Exception as exc:
        error_type = type(exc).__name__
        error_text = str(exc)
        combined = f"{error_type}: {error_text}".lower()

        ssh_unavailable = any(
            token in combined
            for token in (
                "opening channel",
                "connection refused",
                "no route to host",
                "ssh",
            )
        )

        if ssh_unavailable:
            error_code = "OLT_ACCESO_NO_DISPONIBLE"
        else:
            error_code = "FTTH_TRONCAL_EXEC_ERROR"

        result = {
            "ok": False,
            "codigo": error_code,
            "wo": wo,
            "error_tipo": error_type,
            "error": error_text,
        }

    code = str(result.get("codigo") or "")
    session["direcciones_codigo"] = code
    session["_direcciones_resultado"] = result

    # MESA_DIRECCIONES_INC_RELACIONADO_V13_F1_1
    #
    # Recupera de forma conservadora el INC relacionado que ya venga
    # dentro del resultado de Direcciones. Puede estar a cualquier
    # profundidad, por ejemplo dentro de helix_ftth.
    def _buscar_incidente_relacionado_v13(value: Any) -> str:
        if isinstance(value, dict):
            direct = str(
                value.get("incidente_relacionado") or ""
            ).strip().upper()

            if direct.startswith("INC"):
                return direct

            for child in value.values():
                found = _buscar_incidente_relacionado_v13(child)
                if found:
                    return found

        elif isinstance(value, list):
            for child in value:
                found = _buscar_incidente_relacionado_v13(child)
                if found:
                    return found

        return ""

    incidente_direcciones = _buscar_incidente_relacionado_v13(result)

    # MESA_DIRECCIONES_RESOLVER_INC_RESUMEN_HELIX_V13_F1_4
    #
    # El diagnosticador de Direcciones no siempre devuelve el INC.
    # En ese caso se reutiliza el resumen Helix de la WO, en modo lectura.
    #
    # IMPORTANTE:
    # esta consulta SOLO resuelve WO -> INC.
    # La escritura posterior de la nota continúa usando las credenciales
    # de sesión suministradas por la extensión SMCC.
    if not incidente_direcciones:
        try:
            resumen_helix_v13 = consultar_resumen_ot_helix(wo)

            if isinstance(resumen_helix_v13, dict):
                resumen_data_v13 = resumen_helix_v13.get("data")

                if not isinstance(resumen_data_v13, dict):
                    resumen_data_v13 = {}

                candidato_inc_v13 = str(
                    resumen_data_v13.get("incidente_relacionado")
                    or resumen_helix_v13.get("incidente_relacionado")
                    or ""
                ).strip().upper()

                if candidato_inc_v13.startswith("INC"):
                    incidente_direcciones = candidato_inc_v13

        except Exception:
            # Fail-closed:
            # si el resumen Helix no puede resolver el INC,
            # no se inventa ningún incidente y el publicador impedirá
            # el cierre automático.
            incidente_direcciones = ""

    if incidente_direcciones:
        session["incidente_relacionado"] = incidente_direcciones

    if result.get("ok"):
        trunk_id = str(result.get("id_troncal") or "").strip()
        commercial = str(result.get("nombre_comercial") or "").strip()
        serial = str(result.get("serial_referencia") or "").strip()
        olt = str(result.get("elemento_red") or "").strip()
        gpon = str(result.get("gpon_olt") or "").strip()

        directions = result.get("direcciones")
        if not isinstance(directions, dict):
            directions = {}

        clients = directions.get("clientes")
        if not isinstance(clients, list):
            clients = []

        # Una sola consulta de Diagnosticador, hecha por 8021 con una
        # cuenta puntual. Preservar los vecinos separados de las cuentas GES.
        vecinos_raw = result.get("vecinos_consulta")
        if not isinstance(vecinos_raw, dict):
            vecinos_raw = directions.get("vecinos_consulta")
        if not isinstance(vecinos_raw, dict):
            vecinos_raw = {}
        vecinos_items = vecinos_raw.get("clientes")
        if not isinstance(vecinos_items, list):
            vecinos_items = []
        vecinos_publicos = []
        for vecino in vecinos_items:
            if not isinstance(vecino, dict):
                continue
            vecinos_publicos.append({
                "cuenta_rr": str(vecino.get("cuenta_rr") or vecino.get("cuenta") or "").strip(),
                "direccion": str(vecino.get("direccion") or "").strip(),
                "mac": str(vecino.get("mac") or "").strip(),
            })
        vecinos_consulta = {
            "cuenta_consulta": str(vecinos_raw.get("cuenta_consulta") or "").strip(),
            "consultado": bool(vecinos_raw.get("consultado")),
            "ok": bool(vecinos_raw.get("ok")),
            "codigo": str(vecinos_raw.get("codigo") or "").strip(),
            "clientes_encontrados": len(vecinos_publicos),
            "clientes": vecinos_publicos,
        }

        lines = [
            "Consulta de direcciones completada.",
            "",
            f"WO: {wo}",
        ]

        if olt:
            lines.append(f"OLT: {olt}")
        if gpon:
            lines.append(f"GPON: {gpon}")
        if trunk_id:
            lines.append(f"Troncal: {trunk_id}")
        if commercial:
            lines.append(f"Nombre comercial: {commercial}")
        if serial:
            lines.append(f"Serial de referencia: {serial}")

        # MESA_DIRECCIONES_DETALLE_MENSAJE_V1_1
        # MESA_FTTH_NODO_DESDE_TRONCAL_V1
        nodo = str(result.get("nodo") or "").strip()

        if not nodo:
            trunk_for_node = str(
                result.get("troncal")
                or result.get("trunk_id")
                or locals().get("trunk_id")
                or ""
            ).strip()

            if trunk_for_node.upper().startswith("TRK "):
                nodo = trunk_for_node[4:].strip()
            elif trunk_for_node:
                nodo = trunk_for_node

        if nodo:
            lines.append(f"Nodo: {nodo}")

        # MESA_DIRECCIONES_UI_STRUCTURED_V1
        structured_clients: list[dict[str, str]] = []
        if clients:
            lines.extend(["", f"Clientes/vecinos encontrados: {len(clients)}"])

            for item in clients:
                if not isinstance(item, dict):
                    continue

                account = str(
                    item.get("cuenta_rr")
                    or item.get("cuenta")
                    or ""
                ).strip()

                address = str(item.get("direccion") or "").strip()
                mac = str(item.get("mac") or "").strip()
                serial_cliente = str(item.get("serial") or "").strip()
                estado_cliente = str(item.get("estado") or "").strip()

                structured_clients.append(
                    {
                        "cuenta": account,  # MESA_DIRECCIONES_RESTAURAR_CUENTA_V1_2
                        "direccion": address,
                        "mac": mac,
                        "serial": serial_cliente,
                        "estado": estado_cliente,
                    }
                )


                # MESA_GES_FORMATO_DIRECCIONES_V1_1
                es_ges_direcciones = bool(
                    str(result.get("tipo_red") or "").strip().upper() == "GES"
                    or str(result.get("router_direcciones") or "").strip().upper()
                    == "GES_NOTAS_HELIX"
                    or code == "GES_DIRECCIONES_DESDE_NOTAS"
                )

                if es_ges_direcciones:
                    if address and account:
                        lines.append(
                            f"{len(structured_clients)}. Direccion: {address} - - - Cuenta: {account}"
                        )
                    elif address:
                        lines.append(
                            f"{len(structured_clients)}. Direccion: {address}"
                        )
                else:
                    detail_parts: list[str] = []

                    if mac:
                        detail_parts.append(f"MAC: {mac}")

                    if address:
                        detail_parts.append(f"Direccion: {address}")

                    if detail_parts:
                        lines.append(
                            f"{len(structured_clients)}. "
                            + " | ".join(detail_parts)
                        )
            # Detalle incluido en el mensaje; panel estructurado conservado.
        else:
            lines.extend(
                [
                    "",
                    "La consulta finalizo, pero no devolvio vecinos/direcciones.",
                ]
            )

        session["estado"] = "RESULTADO_DIRECCIONES"
        session = guardar_sesion(session)

        # MESA_DIRECCIONES_MULTICANAL_V1
        canales = (
            result.get("canales")
            if isinstance(result.get("canales"), dict)
            else {}
        )

        canal_respuesta = (
            canales.get("respuesta")
            if isinstance(canales.get("respuesta"), dict)
            else {}
        )

        mensaje_atlas = str(
            canal_respuesta.get("mensaje_atlas")
            or ""
        ).strip()

        # El mensaje multicanal previo solo incluye las cuentas de Helix.
        # Añadir las direcciones de vecinos con su cuenta propia.
        if vecinos_publicos and str(result.get("tipo_red") or "").upper() == "GES":
            detalle_vecinos = [
                "Vecinos encontrados desde la cuenta "
                + (vecinos_consulta["cuenta_consulta"] or "consultada")
                + f": {len(vecinos_publicos)}"
            ]
            for indice, vecino in enumerate(vecinos_publicos, 1):
                detalle_vecinos.append(
                    f"{indice}. Cuenta vecino: {vecino['cuenta_rr'] or 'N/D'}"
                    f" - Direccion: {vecino['direccion'] or 'No disponible'}"
                )
            mensaje_atlas = (mensaje_atlas or "\n".join(lines)) + "\n\n" + "\n".join(detalle_vecinos)

        response = _response(
            session=session,
            codigo="MESA_DIRECCIONES_CLIENTES_OK",
            respuesta=(mensaje_atlas or "\n".join(lines)),
        )

        if canales:
            response["canales"] = canales
        response["resultado_direcciones"] = {
            "wo": wo,
            "olt": olt,
            "gpon": gpon,
            "nodo": nodo,
            "troncal": trunk_id,
            "nombre_comercial": commercial,
            "serial_referencia": serial,
            "clientes_encontrados": len(structured_clients),
            "clientes": structured_clients,
            "vecinos_consulta": vecinos_consulta,
        }
        return response

    session["estado"] = "RESULTADO_DIRECCIONES"
    session = guardar_sesion(session)

    # MESA_DIRECCIONES_EMPRESAS_NEGOCIOS_V1_2
    #
    # Corte terminal para EYN / Empresas y Negocios.
    # 8021 ya garantiza que no se ejecutan consultas residenciales.
    # Mesa debe conservar el mensaje corporativo y no mostrar el
    # fallback generico de error.
    if code == "DIRECCIONES_EMPRESAS_NEGOCIOS":
        mensaje_empresas = str(
            result.get("respuesta")
            or result.get("motivo")
            or ""
        ).strip()

        if not mensaje_empresas:
            mensaje_empresas = (
                "La orden corresponde a Empresas y Negocios (EYN), "
                "por lo tanto es una gestion corporativa y no podemos "
                "realizar la consulta ni entregar informacion de "
                "direcciones de clientes."
            )

        response = _response(
            session=session,
            codigo="MESA_DIRECCIONES_EMPRESAS_NEGOCIOS",
            respuesta=mensaje_empresas,
        )

        response["resultado_direcciones"] = {
            "wo": wo,
            "clientes_encontrados": 0,
            "clientes": [],
            "consulta_ejecutada": False,
            "tipo_respuesta": "empresas_negocios",
        }

        return response

    # MESA_DIRECCIONES_SERVICIOS_FIJOS_V1
    if code == "DIRECCIONES_NO_SERVICIOS_FIJOS":
        categoria = str(
            result.get("categoria_operacional")
            or ""
        ).strip()

        categoria_texto = (
            categoria
            if categoria
            else "NO DISPONIBLE"
        )

        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_NO_SERVICIOS_FIJOS",
            respuesta=(
                "No es posible continuar con la consulta de direcciones.\n\n"
                f"WO: {wo}\n"
                f"Categoria operacional: {categoria_texto}\n\n"
                "La opcion Direcciones de clientes aplica unicamente "
                "a ordenes cuya Categoria operacional corresponda "
                "a SERVICIOS FIJOS."
            ),
        )

    if code in ("OLT_ACCESO_NO_DISPONIBLE", "OLT_SSH_NO_DISPONIBLE"):
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_OLT_NO_DISPONIBLE",
            respuesta=(
                "No fue posible acceder a la OLT asociada a la WO.\n\n"
                f"WO: {wo}\n"
                "Helix e Inventario fueron consultados correctamente, pero la OLT "
                "no tiene disponible el acceso de gestión desde el servidor de consulta.\n\n"
                "Puede usar 'Volver al menu' o '+ Nueva solicitud'."
            ),
        )

    if code == "KOU_REQUERIDO":
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_KOU_REQUERIDO",
            respuesta=(
                "La WO fue identificada como FTTH, pero no se encontro una IP "
                "valida de OLT en Inventario.\n\n"
                f"WO: {wo}\n"
                "Se requiere completar la resolucion de IP mediante KOU."
            ),
        )

    if code == "WO_NO_FTTH":
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_WO_NO_FTTH",
            respuesta=(
                f"La WO {wo} no fue identificada como FTTH.\n\n"
                "La automatizacion ZTE de troncal no aplica."
            ),
        )

    if code == "ZTE_SIN_SERIALES":
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_SIN_SERIALES",
            respuesta=(
                "Se accedio a la OLT ZTE, pero no fue posible obtener un serial "
                "ONU utilizable para continuar hacia ACS/Diagnosticador.\n\n"
                f"WO: {wo}"
            ),
        )

    # MESA_ACS_LOGIN_ERROR_V1
    directions = result.get("direcciones")
    if not isinstance(directions, dict):
        directions = {}

    downstream_code = str(directions.get("codigo") or "")

    if code == "ACS_LOGIN_ERROR" or downstream_code == "ACS_LOGIN_ERROR":
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_ACS_LOGIN_ERROR",
            respuesta=(
                "No fue posible iniciar sesion en ACS con el usuario configurado.\n\n"
                f"WO: {wo}\n"
                "El acceso de la OLT y la obtencion del serial pudieron completarse, "
                "pero ACS rechazo o no confirmo el inicio de sesion.\n\n"
                "Valide si el usuario ACS esta activo, bloqueado, vencido, sin permisos "
                "o si sus credenciales cambiaron. La consulta de direcciones no puede "
                "continuar hasta recuperar el acceso a ACS.\n\n"
                "Puede usar 'Volver al menu' o '+ Nueva solicitud'."
            ),
        )

    # MESA_FTTH_EMPRESAS_NEGOCIOS_V1
    if code == "FTTH_EMPRESAS_NEGOCIOS_SIN_INFO":
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_EMPRESAS_NEGOCIOS_SIN_INFO",
            respuesta=(
                "Este servicio corresponde a EMPRESAS Y NEGOCIOS.\n\n"
                "ACS no devolvio informacion para los identificadores "
                "disponibles para esta consulta, por lo que no es posible "
                "consultar ni suministrar informacion del cliente.\n\n"
                f"WO: {wo}"
            ),
        )

    # MESA_DIRECCIONES_DOWNSTREAM_TIMEOUT_V1
    timeout_directions = result.get("direcciones")
    if not isinstance(timeout_directions, dict):
        timeout_directions = {}

    timeout_downstream_code = str(
        timeout_directions.get("codigo") or ""
    ).strip().upper()
    timeout_code = str(code or "").strip().upper()

    if (
        timeout_code == "ACS_TIMEOUT"
        or timeout_downstream_code == "ACS_TIMEOUT"
    ):
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_ACS_TIMEOUT",
            respuesta=(
                "La OLT y el serial ONU fueron obtenidos correctamente, "
                "pero ACS excedio el tiempo maximo permitido para responder.\n\n"
                f"WO: {wo}\n"
                "La consulta fue detenida para evitar repetir el mismo fallo "
                "sobre otros seriales."
            ),
        )

    if (
        timeout_code == "DIAGNOSTICADOR_TIMEOUT"
        or timeout_downstream_code == "DIAGNOSTICADOR_TIMEOUT"
    ):
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_DIAGNOSTICADOR_TIMEOUT",
            respuesta=(
                "La OLT, el serial ONU y la cuenta en ACS fueron obtenidos "
                "correctamente, pero Diagnosticador excedio el tiempo maximo "
                "permitido para responder.\n\n"
                f"WO: {wo}\n"
                "La consulta fue detenida para evitar repetir el mismo fallo "
                "sobre otros seriales."
            ),
        )

    if code == "ACS_SIN_CUENTA" or downstream_code == "ACS_SIN_CUENTA":
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_ACS_SIN_CUENTA",
            respuesta=(
                "ACS inicio sesion correctamente, pero no encontro una cuenta "
                "asociada al serial seleccionado.\n\n"
                f"WO: {wo}\n"
                "La consulta no pudo continuar hacia Diagnosticador."
            ),
        )

    # MESA_DIAGNOSTICADOR_LOGIN_ERROR_V1
    directions = result.get("direcciones")
    if not isinstance(directions, dict):
        directions = {}

    downstream_code = str(directions.get("codigo") or "")

    if (
        code == "DIAGNOSTICADOR_LOGIN_ERROR"
        or downstream_code == "DIAGNOSTICADOR_LOGIN_ERROR"
    ):
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_DIAGNOSTICADOR_LOGIN_ERROR",
            respuesta=(
                "No fue posible iniciar sesion en Diagnosticador con el usuario "
                "configurado.\n\n"
                f"WO: {wo}\n"
                "La OLT, el serial ONU y la cuenta en ACS fueron obtenidos "
                "correctamente, pero Diagnosticador rechazo la autenticacion.\n\n"
                "El usuario puede estar vencido, bloqueado, sin permisos o con "
                "credenciales desactualizadas. Actualice el usuario de Diagnosticador "
                "antes de continuar.\n\n"
                "Puede usar 'Volver al menu' o '+ Nueva solicitud'."
            ),
        )

    # MESA_DIRECCIONES_SIN_VECINOS_V1
    if code == "HFC_DIRECCIONES_SIN_VECINOS":
        nodo = str(result.get("nodo") or "").strip()

        lines = [
            "Consulta de direcciones completada.",
            "",
            f"WO: {wo}",
        ]

        if nodo:
            lines.append(f"Nodo: {nodo}")

        lines.extend(
            [
                "",
                "La consulta finalizo correctamente, pero no se encontraron vecinos/direcciones.",
            ]
        )

        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_CLIENTES_PARCIAL",
            respuesta="\n".join(lines),
        )

    return _response(
        session=session,
        codigo="MESA_DIRECCIONES_CLIENTES_PARCIAL",
        respuesta=(
            "La consulta de direcciones no pudo completarse.\n\n"
            f"WO: {wo}\n"
            f"Codigo tecnico: {code or 'SIN_CODIGO'}"
        ),
    )
# MESA_REDES_NEUTRAS_V2
def _procesar_redes_neutras(
    *,
    session: dict[str, Any],
    wo: str,
) -> dict[str, Any]:
    session["estado"] = "PROCESANDO_REDES_NEUTRAS"
    session = guardar_sesion(session)

    try:
        result = consultar_redes_neutras_por_wo(wo)
    except Exception as exc:
        result = {
            "ok": False,
            "codigo": "HELIX_SERVICE_ERROR",
            "respuesta": "No fue posible completar la consulta de Redes Neutras en Helix.",
            "error": f"{type(exc).__name__}: {exc}",
            "ot": wo,
            "adjuntos": [],
            "archivos": [],
        }

    if not isinstance(result, dict):
        result = {
            "ok": False,
            "codigo": "HELIX_RESPUESTA_INVALIDA",
            "respuesta": "Helix devolvio una respuesta no reconocida para Redes Neutras.",
            "ot": wo,
            "adjuntos": [],
            "archivos": [],
        }

    code = str(result.get("codigo") or "").strip()
    response_text = str(result.get("respuesta") or "").strip()
    incident = str(result.get("incidente") or "").strip()

    related = result.get("incidentes_relacionados")
    if not isinstance(related, list):
        related = []

    attachments = result.get("adjuntos")
    if not isinstance(attachments, list):
        attachments = []

    files = result.get("archivos")
    if not isinstance(files, list):
        files = []

    session["redes_neutras_codigo"] = code
    session["redes_neutras_ok"] = bool(result.get("ok"))
    session["redes_neutras_incidente"] = incident
    # MESA_REDES_NEUTRAS_PERSISTIR_ARCHIVOS_V1
    session["redes_neutras_archivos"] = files
    session["redes_neutras_adjuntos"] = attachments
    session["estado"] = "RESULTADO_REDES_NEUTRAS"
    session = guardar_sesion(session)

    if result.get("ok"):
        lines = [
            "Consulta de Redes Neutras completada.",
            "",
            f"WO: {wo}",
        ]

        if incident:
            lines.append(f"Incidente relacionado: {incident}")
        if related:
            lines.append(f"Incidentes relacionados encontrados: {len(related)}")

        lines.append(f"Adjuntos encontrados: {len(attachments)}")

        if files:
            lines.append(f"Archivos disponibles: {len(files)}")
        if response_text:
            lines.extend(["", response_text])

        response = _response(
            session=session,
            codigo="MESA_REDES_NEUTRAS_OK",
            respuesta="\n".join(lines),
        )
        response["helix"] = result
        # MESA_REDES_NEUTRAS_PROPAGAR_ARCHIVOS_V1
        response["archivos"] = files
        response["adjuntos"] = attachments
        response["resultado_operativo"] = {
            "tipo": "REDES_NEUTRAS",
            "wo": wo,
            "incidente": incident,
            "incidentes_relacionados": related,
            "cantidad_adjuntos": len(attachments),
            "cantidad_archivos": len(files),
            "requiere_seleccion": bool(result.get("requiere_seleccion")),
        }
        return response

    if code in {
        "HELIX_CREDENCIALES_INVALIDAS",
        "HELIX_LOGIN_ERROR",
        "LOGIN_RECHAZADO",
    }:
        response = _response(
            session=session,
            codigo="MESA_REDES_NEUTRAS_CREDENCIALES_INVALIDAS",
            respuesta=(
                "No fue posible iniciar sesion en Helix con el usuario configurado.\n\n"
                f"WO: {wo}\n"
                "Valide si el usuario esta activo, bloqueado, vencido, "
                "sin permisos o si sus credenciales cambiaron."
            ),
        )
        response["helix"] = result
        return response

    response = _response(
        session=session,
        codigo="MESA_REDES_NEUTRAS_PARCIAL",
        respuesta=(
            "La consulta de Redes Neutras no pudo completarse.\n\n"
            f"WO: {wo}\n"
            f"Codigo tecnico: {code or 'SIN_CODIGO'}"
            + (f"\n\n{response_text}" if response_text else "")
        ),
    )
    response["helix"] = result
    return response



# MESA_OT_RELACIONADA_DRYRUN_HELPER_V1

# ATLAS_MESA_OT_REQUEST_CREDENTIALS_V2
def _procesar_generacion_ot_relacionada_dryrun(
    *,
    session: dict[str, Any],
    wo: str,
    tipo: str,
    helix_username: str = "",
    helix_password: str = "",
) -> dict[str, Any]:
    tipo_norm = str(tipo or "").strip().upper()
    es_fibra = tipo_norm == "FIBRA"
    nombre = "Fibra" if es_fibra else "Coaxial"

    session["estado"] = "PROCESANDO_GENERACION_OT_DRYRUN"
    session = guardar_sesion(session)

    try:
        result = consultar_ot_relacionada_dryrun(
            wo,
            tipo_norm,
            helix_username=helix_username,
            helix_password=helix_password,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "codigo": "DRYRUN_SERVICE_ERROR",
            "modo": "DRY_RUN",
            "safe_mode": True,
            "allow_save_click": False,
            "reportar_como_creada": False,
            "wo_origen": wo,
            "tipo": tipo_norm,
            "incidente_relacionado": "",
            "wo_provisional": "",
            "descripcion_tipo_seleccionada": "",
            "guardar_bloqueado": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not isinstance(result, dict):
        result = {
            "ok": False,
            "codigo": "DRYRUN_RESPUESTA_INVALIDA",
            "modo": "DRY_RUN",
            "safe_mode": True,
            "allow_save_click": False,
            "reportar_como_creada": False,
            "wo_origen": wo,
            "tipo": tipo_norm,
            "incidente_relacionado": "",
            "wo_provisional": "",
            "descripcion_tipo_seleccionada": "",
            "guardar_bloqueado": False,
            "error": "El servicio DRY-RUN devolvió una respuesta no reconocida.",
        }

    code = str(result.get("codigo") or "").strip()
    incident = str(result.get("incidente_relacionado") or "").strip()
    provisional = str(result.get("wo_provisional") or "").strip()
    selected_type = str(
        result.get("descripcion_tipo_seleccionada") or ""
    ).strip()
    error = str(result.get("error") or "").strip()

    safe_contract = (
        result.get("safe_mode") is True
        and result.get("allow_save_click") is False
        and result.get("reportar_como_creada") is False
    )

    ok = (
        bool(result.get("ok"))
        and code == "DRYRUN_LISTO_ANTES_DE_GUARDAR"
        and bool(result.get("guardar_bloqueado"))
        and safe_contract
    )

    session["ot_dryrun_tipo"] = tipo_norm
    session["ot_dryrun_codigo"] = code
    session["ot_dryrun_ok"] = ok
    session["ot_dryrun_incidente"] = incident
    session["ot_dryrun_wo_provisional"] = provisional
    session["ot_dryrun_descripcion_tipo"] = selected_type
    session["ot_dryrun_guardar_bloqueado"] = bool(
        result.get("guardar_bloqueado")
    )
    session["ot_live_session_id"] = str(
        result.get("session_id") or ""
    ).strip()
    session["ot_live_expires_in_seconds"] = int(
        result.get("expires_in_seconds") or 0
    )
    session["ot_confirmacion_consumida"] = False

    if ok:
        session["estado"] = "ESPERANDO_CONFIRMACION_GENERACION_OT"
    else:
        session["estado"] = "RESULTADO_GENERACION_OT_DRYRUN"

    session = guardar_sesion(session)

    safe_payload = {
        "ok": ok,
        "codigo": code,
        "modo": "DRY_RUN",
        "safe_mode": True,
        "allow_save_click": False,
        "reportar_como_creada": False,
        "wo_origen": wo,
        "tipo": tipo_norm,
        "incidente_relacionado": incident,
        "wo_provisional": provisional,
        "descripcion_tipo_seleccionada": selected_type,
        "guardar_bloqueado": bool(
            result.get("guardar_bloqueado")
        ),
    }

    if ok:
        lines = [
            f"Validación de Generación de OT {nombre} completada.",
            "",
            f"WO origen: {wo}",
        ]

        if incident:
            lines.append(f"INC relacionado: {incident}")

        if provisional:
            lines.append(
                f"WO provisional: {provisional} (AÚN NO creada)"
            )

        if selected_type:
            lines.append(
                f"Descripción Tipo: {selected_type}"
            )

        lines.extend([
            "",
            "La OT está lista para Guardar, pero todavía NO se ha creado.",
            "",
            "Para crearla realmente escriba exactamente:",
            "CONFIRMAR CREAR OT",
            "",
            "Para abortar escriba:",
            "CANCELAR",
        ])

        response = _response(
            session=session,
            codigo=(
                "MESA_GENERACION_OT_FIBRA_CONFIRMACION_REQUERIDA"
                if es_fibra
                else "MESA_GENERACION_OT_COAX_CONFIRMACION_REQUERIDA"
            ),
            respuesta="\n".join(lines),
        )

        response["ot_relacionada_dryrun"] = safe_payload
        response["confirmacion_requerida"] = True
        response["frase_confirmacion"] = "CONFIRMAR CREAR OT"

        return response

    reason = (
        "Helix no mostró la acción 'Crear relacionado' para el INC abierto."
        if "crear relacionado" in error.lower()
        else "ATLAS no pudo completar la preparación del formulario de la OT."
    )

    response = _response(
        session=session,
        codigo=(
            "MESA_GENERACION_OT_FIBRA_DRYRUN_PARCIAL"
            if es_fibra
            else "MESA_GENERACION_OT_COAX_DRYRUN_PARCIAL"
        ),
        respuesta=(
            f"Generación de OT {nombre} - prueba segura.\n\n"
            f"WO origen: {wo}\n"
            + (f"INC relacionado: {incident}\n" if incident else "")
            + f"Estado: {reason}\n"
            f"Código técnico: {code or 'DRYRUN_FALLIDO'}\n\n"
            "No se creó ninguna OT y no se pulsó Guardar."
        ),
    )

    response["ot_relacionada_dryrun"] = safe_payload
    return response


def _procesar_confirmacion_generacion_ot(
    *,
    session: dict[str, Any],
    mensaje: str,
) -> dict[str, Any]:
    texto = str(mensaje or "").strip().upper()
    tipo = str(session.get("ot_dryrun_tipo") or "").strip().upper()
    nombre = "Fibra" if tipo == "FIBRA" else "Coaxial"
    wo = str(session.get("wo") or "").strip().upper()
    incident = str(
        session.get("ot_dryrun_incidente") or ""
    ).strip().upper()
    provisional = str(
        session.get("ot_dryrun_wo_provisional") or ""
    ).strip().upper()

    if texto == "CANCELAR":
        # ATLAS_OT_LIVE_CANCEL_8027_V1
        try:
            cancelar_ot_relacionada(
                wo=wo,
                tipo=tipo,
                session_id=str(
                    session.get("ot_live_session_id") or ""
                ).strip(),
            )
        except Exception:
            pass

        session["estado"] = "RESULTADO_GENERACION_OT_CANCELADA"
        session["ot_confirmacion_consumida"] = True
        session = guardar_sesion(session)

        return _response(
            session=session,
            codigo="MESA_GENERACION_OT_CANCELADA",
            respuesta=(
                f"Generación de OT {nombre} cancelada.\n\n"
                f"WO origen: {wo}\n"
                + (f"INC relacionado: {incident}\n" if incident else "")
                + (
                    f"WO provisional descartada: {provisional}\n"
                    if provisional
                    else ""
                )
                + "\nNo se pulsó Guardar y no se creó ninguna OT."
            ),
        )

    if texto != "CONFIRMAR CREAR OT":
        response = _response(
            session=session,
            codigo="MESA_GENERACION_OT_CONFIRMACION_REQUERIDA",
            respuesta=(
                f"La OT {nombre} sigue SIN crear.\n\n"
                f"WO origen: {wo}\n"
                + (f"INC relacionado: {incident}\n" if incident else "")
                + (
                    f"WO provisional: {provisional} (AÚN NO creada)\n"
                    if provisional
                    else ""
                )
                + "\nPara ejecutar Guardar escriba exactamente:\n"
                "CONFIRMAR CREAR OT\n\n"
                "Para abortar escriba:\n"
                "CANCELAR"
            ),
        )

        response["confirmacion_requerida"] = True
        response["frase_confirmacion"] = "CONFIRMAR CREAR OT"
        return response

    # ATLAS_OT_CREATE_DISABLED_V1
    if texto == "CONFIRMAR CREAR OT" and not creacion_ot_habilitada():
        session["estado"] = "RESULTADO_GENERACION_OT_CREACION_DESHABILITADA"
        session = guardar_sesion(session)

        return _response(
            session=session,
            codigo="MESA_GENERACION_OT_CREACION_DESHABILITADA",
            respuesta=(
                f"La validación de la OT {nombre} fue completada correctamente.\n\n"
                f"WO origen: {wo}\n"
                + (f"INC relacionado: {incident}\n" if incident else "")
                + (
                    f"WO provisional: {provisional} (AÚN NO creada)\n"
                    if provisional
                    else ""
                )
                + "\nLa creación real de la OT se encuentra deshabilitada actualmente."
            ),
        )
    if bool(session.get("ot_confirmacion_consumida")):
        return _response(
            session=session,
            codigo="MESA_GENERACION_OT_CONFIRMACION_YA_CONSUMIDA",
            respuesta=(
                "La confirmación de esta solicitud ya fue utilizada. "
                "No se ejecutará Guardar nuevamente."
            ),
        )

    session["ot_confirmacion_consumida"] = True
    session["estado"] = "PROCESANDO_CREACION_OT_CONFIRMADA"
    session = guardar_sesion(session)

    try:
        result = confirmar_ot_relacionada(
            wo,
            tipo,
            expected_incident=incident,
            session_id=str(
                session.get("ot_live_session_id") or ""
            ).strip(),
        )
    except Exception as exc:
        result = {
            "ok": False,
            "codigo": "COMMIT_SERVICE_ERROR",
            "wo_origen": wo,
            "tipo": tipo,
            "incidente_relacionado": incident,
            "wo_provisional": provisional,
            "wo_creada": "",
            "descripcion_tipo_seleccionada": "",
            "save_clicked": False,
            "save_confirmed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    code = str(result.get("codigo") or "").strip()
    save_clicked = bool(result.get("save_clicked"))
    save_confirmed = bool(result.get("save_confirmed"))
    created_wo = str(result.get("wo_creada") or "").strip().upper()

    final_inc = str(
        result.get("incidente_relacionado") or incident
    ).strip().upper()

    selected = str(
        result.get("descripcion_tipo_seleccionada")
        or session.get("ot_dryrun_descripcion_tipo")
        or ""
    ).strip()

    error = str(result.get("error") or "").strip()

    if (
        bool(result.get("ok"))
        and code == "OT_CREADA_VERIFICADA"
        and save_clicked
        and save_confirmed
        and created_wo
    ):
        session["estado"] = "RESULTADO_GENERACION_OT_CREADA"
        session["ot_creada"] = created_wo
        session["ot_commit_codigo"] = code
        session["ot_commit_save_clicked"] = True
        session["ot_commit_save_confirmed"] = True
        session = guardar_sesion(session)

        response = _response(
            session=session,
            codigo=(
                "MESA_GENERACION_OT_FIBRA_CREADA"
                if tipo == "FIBRA"
                else "MESA_GENERACION_OT_COAX_CREADA"
            ),
            respuesta=(
                f"✓ OT {nombre} creada y verificada correctamente.\n\n"
                f"WO origen: {wo}\n"
                f"INC relacionado: {final_inc}\n"
                f"WO creada: {created_wo}\n"
                f"Descripción Tipo: {selected}\n\n"
                "La confirmación fue consumida y no puede reutilizarse."
            ),
        )

        response["ot_relacionada_commit"] = {
            "ok": True,
            "codigo": code,
            "wo_origen": wo,
            "incidente_relacionado": final_inc,
            "wo_creada": created_wo,
            "descripcion_tipo_seleccionada": selected,
            "save_clicked": True,
            "save_confirmed": True,
        }

        return response

    resultado_incierto = code in {
        "COMMIT_TIMEOUT",
        "COMMIT_RESPUESTA_INVALIDA",
    }

    if save_clicked or resultado_incierto:
        session["estado"] = "RESULTADO_GENERACION_OT_GUARDADO_NO_VERIFICADO"
        session["ot_commit_codigo"] = code
        session["ot_commit_save_clicked"] = (
            True if save_clicked else None
        )
        session["ot_commit_save_confirmed"] = save_confirmed
        session["ot_creada"] = created_wo
        session = guardar_sesion(session)

        if save_clicked:
            encabezado = (
                "⚠ Guardar fue ejecutado, pero ATLAS no pudo verificar "
                "el resultado final."
            )
        else:
            encabezado = (
                "⚠ ATLAS perdió confirmación del resultado durante la operación. "
                "No es seguro afirmar si Guardar llegó a ejecutarse."
            )

        return _response(
            session=session,
            codigo="MESA_GENERACION_OT_GUARDADO_NO_VERIFICADO",
            respuesta=(
                encabezado
                + "\n\n"
                + f"WO origen: {wo}\n"
                + (f"INC relacionado: {final_inc}\n" if final_inc else "")
                + (f"WO detectada: {created_wo}\n" if created_wo else "")
                + f"Código técnico: {code or 'NO_VERIFICADO'}\n\n"
                "NO vuelva a confirmar esta solicitud. "
                "Revise Helix antes de realizar cualquier otro intento."
            ),
        )

    session["estado"] = "RESULTADO_GENERACION_OT_COMMIT_FALLIDO"
    session["ot_commit_codigo"] = code
    session["ot_commit_save_clicked"] = False
    session["ot_commit_save_confirmed"] = False
    session = guardar_sesion(session)

    return _response(
        session=session,
        codigo="MESA_GENERACION_OT_COMMIT_FALLIDO",
        respuesta=(
            f"No fue posible crear la OT {nombre}.\n\n"
            f"WO origen: {wo}\n"
            + (f"INC relacionado: {final_inc}\n" if final_inc else "")
            + f"Código técnico: {code or 'COMMIT_FALLIDO'}\n"
            + (f"Detalle: {error}\n" if error else "")
            + "\nGuardar NO fue ejecutado."
        ),
    )

def _prompt_current(session: dict[str, Any]) -> dict[str, Any]:
    state = str(
        session.get("estado") or "ESPERANDO_TIPO"
    )

    if state == "RESULTADO_DIRECCIONES":
        code = str(session.get("direcciones_codigo") or "")
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_RESULTADO_LISTO",
            respuesta=(
                "La solicitud de direcciones ya fue procesada.\n\n"
                f"WO: {session.get('wo')}\n"
                f"Codigo: {code or 'SIN_CODIGO'}\n\n"
                "Use 'Volver al menu' o '+ Nueva solicitud' para continuar."
            ),
        )

    if state == "ESPERANDO_TIPO":
        return _response(
            session=session,
            codigo="MESA_AYUDA_MENU",
            respuesta=texto_menu(),
        )

    if state == "ESPERANDO_WO":
        return _response(
            session=session,
            codigo="MESA_AYUDA_ESPERANDO_WO",
            respuesta=(
                f"Perfecto. Seleccionaste: "
                f"{session.get('tipo_nombre')}.\n\n"
                "Para continuar con la gestión, "
                "por favor indícame la Orden de Trabajo "
                "(OT / WO) correspondiente.\n"
                "Ejemplo: WO0000005464806"
            ),
        )

    # ATLAS_MESA_HELIX_CREDENTIAL_PROMPT_V2
    if state == "ESPERANDO_CREDENCIALES_HELIX":
        tipo_codigo = str(
            session.get("tipo_codigo") or ""
        ).strip().upper()

        nombre = (
            "Fibra"
            if tipo_codigo == "GENERACION_OT_FIBRA"
            else "Coaxial"
        )

        response = _response(
            session=session,
            codigo="MESA_GENERACION_OT_CREDENCIALES_REQUERIDAS",
            respuesta=(
                f"WO recibida: {session.get('wo')}\n\n"
                f"Para continuar con la Generación de OT "
                f"{nombre}, ingrese sus credenciales de "
                "Helix en el formulario seguro."
            ),
        )

        response["credenciales_requeridas"] = True

        return response

    if state == "ESPERANDO_CONFIRMACION_GENERACION_OT":
        tipo = str(
            session.get("ot_dryrun_tipo") or ""
        ).strip().upper()

        nombre = (
            "Fibra"
            if tipo == "FIBRA"
            else "Coaxial"
        )

        return _response(
            session=session,
            codigo="MESA_GENERACION_OT_CONFIRMACION_REQUERIDA",
            respuesta=(
                f"La OT {nombre} está lista pero "
                "AÚN NO ha sido creada.\n\n"
                f"WO origen: {session.get('wo')}\n"
                f"INC relacionado: "
                f"{session.get('ot_dryrun_incidente') or 'No confirmado'}\n"
                f"WO provisional: "
                f"{session.get('ot_dryrun_wo_provisional') or 'No disponible'}\n\n"
                "Para Guardar escriba exactamente:\n"
                "CONFIRMAR CREAR OT\n\n"
                "Para abortar escriba:\n"
                "CANCELAR"
            ),
        )

    if state == "RESULTADO_GENERACION_OT_DRYRUN":
        tipo = str(
            session.get("ot_dryrun_tipo") or ""
        ).strip().upper()

        nombre = (
            "Fibra"
            if tipo == "FIBRA"
            else "Coaxial"
        )

        provisional = str(
            session.get(
                "ot_dryrun_wo_provisional"
            ) or ""
        )

        descripcion = str(
            session.get(
                "ot_dryrun_descripcion_tipo"
            ) or ""
        )

        return _response(
            session=session,
            codigo="MESA_GENERACION_OT_DRYRUN_RESULTADO_LISTO",
            respuesta=(
                f"Ultima prueba Generacion OT "
                f"{nombre}.\n\n"
                f"WO origen: {session.get('wo')}\n"
                f"INC relacionado: "
                f"{session.get('ot_dryrun_incidente') or 'No confirmado'}\n"
                f"WO provisional: "
                f"{provisional or 'No disponible'}\n"
                f"Descripcion Tipo: "
                f"{descripcion or 'No seleccionada'}\n\n"
                "Modo: DRY-RUN. No se creo ninguna OT "
                "y Guardar permanece prohibido."
            ),
        )

    # MESA_PROMPT_RESULTADO_REDES_NEUTRAS_V1
    if state == "RESULTADO_REDES_NEUTRAS":
        code = str(
            session.get("redes_neutras_codigo") or ""
        ).strip()

        response = _response(
            session=session,
            codigo="MESA_REDES_NEUTRAS_RESULTADO_LISTO",
            respuesta=(
                "La solicitud de Redes Neutras ya fue procesada.\n\n"
                f"WO: {session.get('wo')}\n"
                f"Codigo: {code or 'SIN_CODIGO'}\n\n"
                "Use 'Volver al menu' o '+ Nueva solicitud' para continuar."
            ),
        )

        files = session.get("redes_neutras_archivos")
        attachments = session.get("redes_neutras_adjuntos")

        if isinstance(files, list):
            response["archivos"] = files

        if isinstance(attachments, list):
            response["adjuntos"] = attachments

        return response

    if state == "PENDIENTE_VALIDACION_HELIX":
        return _response(
            session=session,
            codigo="MESA_AYUDA_PENDIENTE_HELIX",
            respuesta=(
                "Solicitud recibida correctamente.\n\n"
                f"Tipo: {session.get('tipo_nombre')}\n"
                f"WO: {session.get('wo')}\n"
                f"Solicitud: {session.get('solicitud_id')}\n\n"
                "Estado de la simulación: pendiente "
                "de validación en Helix.\n"
                "La conexión con Helix se habilitará "
                "en la siguiente fase.\n\n"
                "Puede usar 'Volver al menú' o iniciar "
                "una nueva solicitud."
            ),
        )

    return _response(
        session=session,
        codigo="MESA_AYUDA_ESTADO_DESCONOCIDO",
        respuesta=(
            "La sesión qued? en un estado no reconocido. "
            "Use 'Nueva solicitud'."
        ),
    )


# ATLAS_SMCC_AUTH_REQUIRED_INTERNAL_V1
def _smcc_helix_auth_required_response(
    session: dict[str, Any],
) -> dict[str, Any]:
    """
    Señal interna para la extensión SMCC.

    Nunca pide credenciales al cliente.
    """
    response = _response(
        session=session,
        codigo="HELIX_AGENT_AUTH_REQUIRED",
        respuesta="",
    )

    response["visible_cliente"] = False
    response["internal"] = True
    response["accion_interna"] = (
        "REQUEST_AGENT_CREDENTIALS"
    )
    response["credenciales_requeridas"] = True

    return response


# ATLAS_MESA_CONVERSAR_CREDENTIALS_V2
def _conversar_impl(
    *,
    mensaje: str,
    conversation_id: str,
    user_id: str | None = None,
    canal: str = "ATLAS",
    helix_username: str = "",
    helix_password: str = "",
) -> dict[str, Any]:
    text = str(mensaje or "").strip()
    normalized = re.sub(r"\s+", " ", text).strip().lower()

    session = obtener_sesion(
        canal=canal,
        conversation_id=conversation_id,
    )

    if session is None or normalized in _RESET_WORDS:
        session = nueva_sesion(
            canal=canal,
            conversation_id=conversation_id,
            user_id=user_id,
        )

        return _response(
            session=session,
            codigo="MESA_AYUDA_MENU",
            respuesta=texto_menu(),
        )

    if normalized in _STATUS_WORDS:
        return _prompt_current(session)

    state = str(session.get("estado") or "ESPERANDO_TIPO")

    # ATLAS_MESA_HELIX_CREDENTIAL_STATE_V2
    if state == "ESPERANDO_CREDENCIALES_HELIX":
        username = str(helix_username or "").strip()
        password = str(helix_password or "")

        if not username or not password:
            # ATLAS_SMCC_CREDENTIAL_STATE_INTERNAL_V1
            if str(canal or "").strip().upper() == "SMCC_WHATSAPP":
                return _smcc_helix_auth_required_response(
                    session
                )

            return _prompt_current(session)

        tipo_codigo = str(
            session.get("tipo_codigo") or ""
        ).strip().upper()

        if tipo_codigo == "GENERACION_OT_FIBRA":
            tipo_ot = "FIBRA"
        elif tipo_codigo == "GENERACION_OT_COAXIAL":
            tipo_ot = "COAX"
        else:
            raise ValueError(
                "Tipo OT inválido para credenciales Helix."
            )

        wo_actual = str(
            session.get("wo") or ""
        ).strip().upper()

        if not wo_actual:
            raise ValueError(
                "La sesión no contiene WO para Generación OT."
            )

        return _procesar_generacion_ot_relacionada_dryrun(
            session=session,
            wo=wo_actual,
            tipo=tipo_ot,
            helix_username=username,
            helix_password=password,
        )

    if state == "ESPERANDO_CONFIRMACION_GENERACION_OT":
        return _procesar_confirmacion_generacion_ot(
            session=session,
            mensaje=mensaje,
        )

    if state == "ESPERANDO_TIPO":
        option_number = text.strip()

        if not re.fullmatch(r"\d", option_number):
            return _response(
                session=session,
                codigo="MESA_AYUDA_OPCION_INVALIDA",
                respuesta=(
                    "La opci\u00f3n no es v\u00e1lida.\n\n"
                    + texto_menu()
                ),
            )

        option = obtener_opcion(option_number)

        if not option:
            return _response(
                session=session,
                codigo="MESA_AYUDA_OPCION_INVALIDA",
                respuesta=(
                    "La opci\u00f3n no se encuentra en el cat\u00e1logo.\n\n"
                    + texto_menu()
                ),
            )

        session["tipo_numero"] = option_number
        session["tipo_codigo"] = option["codigo"]
        _mesa_tipo_confirmacion = str(session.get("tipo_codigo") or "").strip().upper()
        if _mesa_tipo_confirmacion == "CONFIRMACION_FTTH":
            return _procesar_confirmacion_claro_te_ayuda(session, tipo="FTTH")
        if _mesa_tipo_confirmacion == "CONFIRMACION_HFC":
            return _procesar_confirmacion_claro_te_ayuda(session, tipo="HFC")
        session["tipo_nombre"] = option["nombre"]
        session["estado"] = "ESPERANDO_WO"
        session = guardar_sesion(session)

        return _response(
            session=session,
            codigo="MESA_AYUDA_TIPO_SELECCIONADO",
            respuesta=(
                f"Perfecto. Seleccionaste: {option['nombre']}.\n\n"
                "Para continuar con la gestión, por favor indícame la Orden de Trabajo (OT / WO) correspondiente.\n"
                "Ejemplo: WO0000005464806"
            ),
        )

    if state == "ESPERANDO_WO":
        wo = _normalizar_wo(text)

        if not wo:
            return _response(
                session=session,
                codigo="MESA_AYUDA_WO_INVALIDA",
                respuesta=(
                    "⚠️ No pude identificar una Orden de Trabajo válida.\n\n"
                    "Por favor verifica la OT / WO ingresada e inténtalo nuevamente.\n"
                    "Ejemplo: WO0000005464806"
                ),
            )

        session["wo"] = wo
        session["solicitud_id"] = f"MAS-{uuid4().hex[:12].upper()}"

        if str(session.get("tipo_codigo") or "") == "GRAFICA_PATHTRAK":
            session["estado"] = "PROCESANDO_PATHTRAK"
            session = guardar_sesion(session)

            return _procesar_grafica_pathtrak(
                session=session,
                wo=wo,
            )

        if str(session.get("tipo_codigo") or "") == "DIRECCIONES_CLIENTES":
            return _procesar_direcciones_clientes(
                session=session,
                wo=wo,
            )

        # MESA_ROUTING_REDES_NEUTRAS_V2
        if str(session.get("tipo_codigo") or "") == "REDES_NEUTRAS":
            return _procesar_redes_neutras(
                session=session,
                wo=wo,
            )
        # MESA_ROUTING_OT_RELACIONADA_DRYRUN_V1
        _mesa_tipo_ot_relacionada = str(
            session.get("tipo_codigo") or ""
        ).strip().upper()

        # ATLAS_MESA_HELIX_CREDENTIAL_GATE_V2
        if _mesa_tipo_ot_relacionada in {
            "GENERACION_OT_FIBRA",
            "GENERACION_OT_COAXIAL",
        }:
            session["estado"] = "ESPERANDO_CREDENCIALES_HELIX"
            session = guardar_sesion(session)

            # ATLAS_SMCC_OT_AUTH_AUTOCONSUME_V1
            if str(canal or "").strip().upper() == "SMCC_WHATSAPP":
                username = str(
                    helix_username or ""
                ).strip()
                password = str(
                    helix_password or ""
                )

                if not username or not password:
                    return _smcc_helix_auth_required_response(
                        session
                    )

                tipo_ot = (
                    "FIBRA"
                    if _mesa_tipo_ot_relacionada
                    == "GENERACION_OT_FIBRA"
                    else "COAX"
                )

                wo_actual = str(
                    session.get("wo") or ""
                ).strip().upper()

                if not wo_actual:
                    raise ValueError(
                        "La sesión no contiene WO "
                        "para Generación OT."
                    )

                return _procesar_generacion_ot_relacionada_dryrun(
                    session=session,
                    wo=wo_actual,
                    tipo=tipo_ot,
                    helix_username=username,
                    helix_password=password,
                )

            # ATLAS / Chat DECO conserva el flujo existente.
            response = _prompt_current(session)
            response["credenciales_requeridas"] = True
            return response
        session["estado"] = "PENDIENTE_VALIDACION_HELIX"
        session = guardar_sesion(session)

        return _prompt_current(session)

    if state == "PENDIENTE_VALIDACION_HELIX":
        return _prompt_current(session)

    # MESA_STATE_REDES_NEUTRAS_V3
    if state == "RESULTADO_REDES_NEUTRAS":
        code = str(
            session.get("redes_neutras_codigo") or ""
        ).strip()

        files = session.get("redes_neutras_archivos")
        attachments = session.get("redes_neutras_adjuntos")

        if not isinstance(files, list):
            files = []

        if not isinstance(attachments, list):
            attachments = []

        response = _response(
            session=session,
            codigo="MESA_REDES_NEUTRAS_RESULTADO_LISTO",
            respuesta=(
                "La solicitud de Redes Neutras ya fue procesada.\n\n"
                f"WO: {session.get('wo')}\n"
                f"Codigo: {code or 'SIN_CODIGO'}\n\n"
                "Los archivos descargados continúan disponibles en esta conversación.\n\n"
                "Use 'Volver al menu' o '+ Nueva solicitud' para continuar."
            ),
        )

        # Mantener disponibles los documentos en el historial
        response["archivos"] = files
        response["adjuntos"] = attachments

        return response

    if state == "RESULTADO_DIRECCIONES":
        code = str(session.get("direcciones_codigo") or "")
        return _response(
            session=session,
            codigo="MESA_DIRECCIONES_RESULTADO_LISTO",
            respuesta=(
                "La solicitud de direcciones ya fue procesada.\n\n"
                f"WO: {session.get('wo')}\n"
                f"Codigo: {code or 'SIN_CODIGO'}\n\n"
                "Use 'Volver al menu' o '+ Nueva solicitud' para continuar."
            ),
        )
    if state == "RESULTADO_PATHTRAK":
        return _response(
            session=session,
            codigo="MESA_PATHTRAK_RESULTADO_LISTO",
            respuesta=(
                "La solicitud PathTrak ya fue procesada.\n\n"
                f"WO: {session.get('wo')}\n"
                f"Nodo: {session.get('helix_nodo') or 'No identificado'}\n\n"
                "Use 'Volver al menu' o '+ Nueva solicitud' para continuar."
            ),
        )

    session["estado"] = "ESPERANDO_TIPO"
    session = guardar_sesion(session)

    return _response(
        session=session,
        codigo="MESA_AYUDA_SESION_RECUPERADA",
        respuesta=texto_menu(),
    )



# ATLAS_MESA_CONVERSATION_TRACKING_V1
def conversar(
    *,
    mensaje: str,
    conversation_id: str,
    user_id: str | None = None,
    canal: str = "ATLAS",
    helix_username: str = "",
    helix_password: str = "",
) -> dict[str, Any]:
    """
    Punto unico de entrada de Mesa de Ayuda.

    El tracking es fail-open:
    un fallo de MySQL no debe impedir el funcionamiento del chatbot.
    """
    from app.services.mesa_ayuda.consultas_tracking_service import (
        finalizar_error,
        finalizar_ok,
        registrar_inicio,
    )

    tracking_context = registrar_inicio(
        mensaje=mensaje,
        conversation_id=conversation_id,
        user_id=user_id,
        canal=canal,
    )

    try:
        response = _conversar_impl(
            mensaje=mensaje,
            conversation_id=conversation_id,
            user_id=user_id,
            canal=canal,
            helix_username=helix_username,
            helix_password=helix_password,
        )

    except Exception as exc:
        finalizar_error(
            tracking_context,
            exc,
        )
        raise

    finalizar_ok(
        tracking_context,
        response,
    )

    return response

def health() -> dict[str, Any]:
    return {
        "ok": True,
        "servicio": "mesa_ayuda_conversation_service",
        "version": "MESA_PATHTRAK_DUAL_EVIDENCE_V4B",
        "persistencia": "memoria_proceso",
        "helix_conectado": True,
        "base_datos_escritura": False,
        "canales_preparados": ["ATLAS", "WHATSAPP"],
        **session_stats(),
    }


