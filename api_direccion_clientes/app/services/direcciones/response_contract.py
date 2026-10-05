from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "ATLAS_MULTICANAL_V1"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _first_client(resultado: dict[str, Any]) -> dict[str, Any]:
    direcciones = resultado.get("direcciones")
    if not isinstance(direcciones, dict):
        return {}

    clientes = direcciones.get("clientes")
    if not isinstance(clientes, list) or not clientes:
        return {}

    cliente = clientes[0]
    return cliente if isinstance(cliente, dict) else {}


def construir_respuesta_multicanal(
    wo: str,
    resultado: dict[str, Any],
) -> dict[str, Any]:

    if not isinstance(resultado, dict):
        resultado = {}

    wo = _clean(wo).upper()
    codigo = _clean(resultado.get("codigo"))
    router = _clean(resultado.get("router_direcciones"))

    mensaje_atlas = ""
    mensaje_smcc = ""
    proceso = "DIRECCIONES"
    subtipo = router or codigo

    mostrar_atlas = True
    enviar_smcc = False
    auto_send = False
    requiere_confirmacion = False

    datos: dict[str, Any] = {}

    # ATLAS_MULTICANAL_CGE_V1
    # ATLAS_MULTICANAL_CGE_DESCRIPCION_DIRECTA_V1
    if codigo in {
        "DIRECCIONES_CGE_RECLAMACION_USUARIO_OK",
        "DIRECCIONES_CGE_DESCRIPCION_DIRECTA_OK",
    }:
        cliente = _first_client(resultado)

        cuenta = _clean(cliente.get("cuenta"))
        nombre = _clean(cliente.get("nombre_cliente"))
        municipio = _clean(cliente.get("municipio"))
        direccion = _clean(cliente.get("direccion"))
        tecnologia = _clean(cliente.get("subtipo_trabajo"))
        troncal = _clean(cliente.get("id_troncal"))

        datos = {
            "cuenta": cuenta,
            "cliente": nombre,
            "municipio": municipio,
            "direccion": direccion,
            "tecnologia": tecnologia,
            "troncal": troncal,
        }

        tipo_gestion_cge = (
            "CGE - Reclamación Usuario"
            if codigo == "DIRECCIONES_CGE_RECLAMACION_USUARIO_OK"
            else "CGE - Dirección desde descripción"
        )

        mensaje_atlas = (
            "✅ Dirección de cliente encontrada\n\n"
            f"WO: {wo}\n"
            f"Tipo de gestión: {tipo_gestion_cge}\n"
            f"Tecnología: {tecnologia or 'NO DISPONIBLE'}\n\n"
            f"Cuenta: {cuenta or 'NO DISPONIBLE'}\n"
            f"Cliente: {nombre or 'NO DISPONIBLE'}\n"
            f"Municipio: {municipio or 'NO DISPONIBLE'}\n"
            f"Dirección: {direccion or 'NO DISPONIBLE'}\n\n"
            f"Troncal: {troncal or 'NO DISPONIBLE'}"
        )

        mensaje_smcc = (
            f"Se valida la información de la orden {wo}.\n\n"
            f"Cuenta: {cuenta}\n"
            f"Cliente: {nombre}\n"
            f"Municipio: {municipio}\n"
            f"Dirección: {direccion}\n"
            f"Tecnología: {tecnologia}\n"
            f"Troncal: {troncal}."
        )

        enviar_smcc = bool(direccion)
        auto_send = bool(direccion)

    # ATLAS_MULTICANAL_HFC_DIRECCIONES_OK_V1
    elif codigo == "HFC_DIRECCIONES_OK":
        subtipo = "HFC"

        direcciones_payload = resultado.get("direcciones")
        if not isinstance(direcciones_payload, dict):
            direcciones_payload = {}

        raw_clientes = direcciones_payload.get("clientes")
        if not isinstance(raw_clientes, list):
            raw_clientes = []

        clientes = []
        for item in raw_clientes:
            if not isinstance(item, dict):
                continue

            cuenta = _clean(
                item.get("cuenta_rr")
                or item.get("cuenta")
                or item.get("cuenta_consulta")
            )
            direccion = _clean(item.get("direccion"))
            mac = _clean(item.get("mac"))

            if not direccion:
                continue

            clientes.append(
                {
                    "cuenta": cuenta,
                    "mac": mac,
                    "direccion": direccion,
                }
            )

        nodo = _clean(
            resultado.get("nodo")
            or resultado.get("elemento_red")
        )
        fuente_mac = _clean(resultado.get("fuente_mac"))
        fallback_pathtrak = bool(
            resultado.get("fallback_pathtrak")
        )
        motivo_fallback = _clean(
            resultado.get("motivo_fallback")
        )

        datos = {
            "nodo": nodo,
            "fuente_mac": fuente_mac,
            "fallback_pathtrak": fallback_pathtrak,
            "motivo_fallback": motivo_fallback,
            "clientes_encontrados": len(clientes),
            "clientes": clientes,
        }

        lineas = []
        for cliente in clientes:
            cuenta = _clean(cliente.get("cuenta"))
            direccion = _clean(cliente.get("direccion"))

            lineas.append(
                f"- Cuenta {cuenta}" + (f": {direccion}" if direccion else "")
            )

        mensaje_atlas = (
            "✅ Direcciones HFC encontradas\n\n"
            f"WO: {wo}\n"
            f"Nodo: {nodo or 'NO DISPONIBLE'}\n"
            f"Clientes encontrados: {len(clientes)}"
        )

        if lineas:
            mensaje_atlas += (
                "\n\nDirecciones:\n"
                + "\n".join(lineas)
            )

        if clientes:
            mensaje_smcc = (
                f"WO: {wo}\n"
                "Gestión: Direcciones HFC\n"
                f"Nodo: {nodo or 'NO DISPONIBLE'}\n"
                f"Clientes encontrados: {len(clientes)}\n\n"
                "Direcciones:\n"
                + "\n".join(lineas)
            )
            enviar_smcc = True
            auto_send = True
        else:
            mensaje_smcc = ""
            enviar_smcc = False
            auto_send = False

    # ATLAS_MULTICANAL_FTTH_ZTE_DIRECCIONES_OK_V1
    elif codigo == "FTTH_TRONCAL_DIRECCIONES_OK":
        subtipo = "FTTH"

        direcciones_payload = resultado.get("direcciones")
        if not isinstance(direcciones_payload, dict):
            direcciones_payload = {}

        raw_clientes = direcciones_payload.get("clientes")
        if not isinstance(raw_clientes, list):
            raw_clientes = []

        clientes = []
        for item in raw_clientes:
            if not isinstance(item, dict):
                continue

            cuenta = _clean(
                item.get("cuenta_rr")
                or item.get("cuenta")
                or item.get("cuenta_consulta")
            )
            direccion = _clean(item.get("direccion"))
            mac = _clean(item.get("mac"))

            if not direccion:
                continue

            clientes.append(
                {
                    "cuenta": cuenta,
                    "mac": mac,
                    "direccion": direccion,
                }
            )

        elemento = _clean(resultado.get("elemento_red"))
        gpon = _clean(resultado.get("gpon_olt"))
        vendor = _clean(resultado.get("vendor"))
        troncal = _clean(resultado.get("id_troncal"))
        nombre_comercial = _clean(
            resultado.get("nombre_comercial")
        )

        datos = {
            "elemento_red": elemento,
            "gpon_olt": gpon,
            "vendor": vendor,
            "id_troncal": troncal,
            "nombre_comercial": nombre_comercial,
            "clientes_encontrados": len(clientes),
            "clientes": clientes,
        }

        lineas = []
        for cliente in clientes:
            cuenta = _clean(cliente.get("cuenta"))
            direccion = _clean(cliente.get("direccion"))

            if cuenta:
                lineas.append(
                    f"- Cuenta {cuenta}: {direccion}"
                )
            else:
                lineas.append(f"- {direccion}")

        mensaje_atlas = (
            "✅ Direcciones FTTH encontradas\n\n"
            f"WO: {wo}\n"
            f"OLT: {elemento or 'NO DISPONIBLE'}\n"
            f"GPON: {gpon or 'NO DISPONIBLE'}\n"
            f"Vendor: {vendor or 'NO DISPONIBLE'}\n"
            f"Clientes encontrados: {len(clientes)}"
        )

        if lineas:
            mensaje_atlas += (
                "\n\nDirecciones:\n"
                + "\n".join(lineas)
            )

        if clientes:
            mensaje_smcc = (
                f"WO: {wo}\n"
                "Gestión: Direcciones FTTH\n"
                f"OLT: {elemento or 'NO DISPONIBLE'}\n"
                f"GPON: {gpon or 'NO DISPONIBLE'}\n"
                f"Vendor: {vendor or 'NO DISPONIBLE'}\n"
                f"Clientes encontrados: {len(clientes)}\n\n"
                "Direcciones:\n"
                + "\n".join(lineas)
            )
            enviar_smcc = True
            auto_send = True
        else:
            mensaje_smcc = ""
            enviar_smcc = False
            auto_send = False

    # ATLAS_MULTICANAL_GES_DIRECCIONES_OK_V1
    elif codigo == "GES_DIRECCIONES_DESDE_NOTAS":
        subtipo = "GES_NOTAS_HELIX"

        direcciones_payload = resultado.get("direcciones")
        if not isinstance(direcciones_payload, dict):
            direcciones_payload = {}

        raw_clientes = direcciones_payload.get("clientes")
        if not isinstance(raw_clientes, list):
            raw_clientes = []

        clientes = []
        for item in raw_clientes:
            if not isinstance(item, dict):
                continue

            cuenta = _clean(
                item.get("cuenta")
                or item.get("cuenta_rr")
                or item.get("serial")
            )
            direccion = _clean(item.get("direccion"))

            if not cuenta:
                continue

            clientes.append(
                {
                    "cuenta": cuenta,
                    "direccion": direccion,
                }
            )

        fuente = _clean(
            resultado.get("fuente")
            or direcciones_payload.get("fuente")
            or "NOTAS_HELIX"
        )

        datos = {
            "fuente": fuente,
            "clientes_encontrados": len(clientes),
            "clientes": clientes,
        }

        lineas = []
        for cliente in clientes:
            cuenta = _clean(cliente.get("cuenta"))
            direccion = _clean(cliente.get("direccion"))

            lineas.append(
                f"- Cuenta {cuenta}" + (f": {direccion}" if direccion else "")
            )

        mensaje_atlas = (
            "✅ Cuentas GES encontradas\n\n"
            f"WO: {wo}\n"
            f"Cuentas afectadas: {len(clientes)}"
        )

        if lineas:
            mensaje_atlas += (
                "\n\nCuentas y direcciones disponibles:\n"
                + "\n".join(lineas)
            )

        if clientes:
            mensaje_smcc = (
                f"WO: {wo}\n"
                "Gestión: GES - Cuentas afectadas\n"
                f"Cuentas encontradas: {len(clientes)}\n\n"
                "Cuentas y direcciones disponibles:\n"
                + "\n".join(lineas)
            )
            enviar_smcc = True
            auto_send = True
        else:
            mensaje_smcc = ""
            enviar_smcc = False
            auto_send = False

    # ATLAS_MULTICANAL_CAIDA_TOTAL_SDS_V1
    # ATLAS_MULTICANAL_FTTH_DIRECCIONES_OK_V2
    elif codigo == "FTTH_CAIDA_TOTAL_TRONCAL_DIRECCIONES_OK":
        subtipo = "FTTH"
        elemento = _clean(resultado.get("elemento_red"))
        gpon = _clean(resultado.get("gpon_olt"))
        vendor = _clean(resultado.get("vendor"))
        troncal = _clean(resultado.get("id_troncal"))
        nodo = _clean(resultado.get("nodo"))

        direcciones_payload = resultado.get("direcciones")
        if not isinstance(direcciones_payload, dict):
            direcciones_payload = {}

        clientes_raw = direcciones_payload.get("clientes") or []
        if not isinstance(clientes_raw, list):
            clientes_raw = []

        clientes = []
        lineas = []

        for cliente in clientes_raw:
            if not isinstance(cliente, dict):
                continue

            cuenta = _clean(
                cliente.get("cuenta_rr")
                or cliente.get("cuenta")
                or cliente.get("cuenta_consulta")
            )
            mac = _clean(cliente.get("mac"))
            direccion = _clean(cliente.get("direccion"))

            if not direccion:
                continue

            clientes.append(
                {
                    "cuenta": cuenta,
                    "mac": mac,
                    "direccion": direccion,
                }
            )

            detalle = f"{len(clientes)}. {direccion}"
            if cuenta:
                detalle += f" | Cuenta: {cuenta}"
            if mac:
                detalle += f" | MAC: {mac}"
            lineas.append(detalle)

        clientes_encontrados = len(clientes)

        datos = {
            "elemento_red": elemento,
            "gpon_olt": gpon,
            "vendor": vendor,
            "id_troncal": troncal,
            "nodo": nodo,
            "cuenta_consulta": _clean(
                direcciones_payload.get("cuenta_consulta")
            ),
            "clientes_encontrados": clientes_encontrados,
            "clientes": clientes,
        }

        resumen_base = (
            _clean(resultado.get("respuesta"))
            or (
                "Se encontraron "
                f"{clientes_encontrados} direcciones de clientes."
            )
        )

        lista_texto = "\n".join(lineas)

        if lista_texto:
            mensaje_atlas = (
                resumen_base
                + "\n\nDirecciones de Clientes:\n"
                + lista_texto
            )

            mensaje_smcc = (
                f"Se valida la orden {wo}.\n\n"
                "Se identifica CAÍDA TOTAL de troncal GPON.\n"
                f"Elemento: {elemento or 'NO DISPONIBLE'}.\n"
                f"GPON: {gpon or 'NO DISPONIBLE'}.\n"
                f"ID Troncal: {troncal or 'NO DISPONIBLE'}.\n\n"
                "Validar la afectación desde SDS.\n\n"
                f"Direcciones de Clientes encontradas "
                f"({clientes_encontrados}):\n"
                + lista_texto
            )

            enviar_smcc = True
            auto_send = True
        else:
            mensaje_atlas = resumen_base
            mensaje_smcc = ""
            enviar_smcc = False
            auto_send = False

    elif codigo == "FTTH_CAIDA_TOTAL_TRONCAL_SDS":
        subtipo = "FTTH_CAIDA_TOTAL"
        elemento = _clean(resultado.get("elemento_red"))
        gpon = _clean(resultado.get("gpon_olt"))
        vendor = _clean(resultado.get("vendor"))
        troncal = _clean(resultado.get("id_troncal"))
        descripcion_troncal = _clean(resultado.get("descripcion_troncal"))
        nombre_comercial = _clean(resultado.get("nombre_comercial"))
        # ATLAS_MULTICANAL_TRUNK_SOURCE_V1
        fuente_troncal = _clean(
            resultado.get("fuente_troncal")
        )
        troncal_texto = troncal or "NO DISPONIBLE AUTOMATICAMENTE"

        datos = {
            "elemento_red": elemento,
            "gpon_olt": gpon,
            "vendor": vendor,
            "id_troncal": troncal,
            "descripcion_troncal": descripcion_troncal,
            "nombre_comercial": nombre_comercial,
            "fuente_troncal": fuente_troncal,
            "requiere_sds": True,
            "consulta_direcciones": False,
        }

        mensaje_atlas = (
            "⚠️ Caída total de troncal GPON\n\n"
            f"WO: {wo}\n"
            f"Elemento: {elemento or 'NO DISPONIBLE'}\n"
            f"GPON: {gpon or 'NO DISPONIBLE'}\n"
            f"ID Troncal: {troncal_texto}\n\n"
            "Para este tipo de afectación no aplica consulta de Direcciones de Clientes.\n"
            "Validar la afectación desde SDS."
        )

        mensaje_smcc = (
            f"Se valida la orden {wo}.\n\n"
            "Se identifica CAÍDA TOTAL de troncal GPON.\n"
            f"ID Troncal: {troncal_texto}.\n"
            f"Elemento: {elemento or 'NO DISPONIBLE'}.\n"
            f"GPON: {gpon or 'NO DISPONIBLE'}.\n\n"
            "Validar la afectación desde SDS. "
            "No aplica consulta de Direcciones de Clientes."
        )

        enviar_smcc = True
        auto_send = True

    else:
        mensaje_atlas = (
            _clean(resultado.get("respuesta"))
            or codigo
            or "Resultado recibido sin mensaje operativo definido."
        )

        mensaje_smcc = ""
        enviar_smcc = False
        auto_send = False

    return {
        "contrato_version": CONTRACT_VERSION,
        "ok": bool(resultado.get("ok")),
        "codigo": codigo,
        "proceso": proceso,
        "subtipo": subtipo,
        "wo": wo,
        "datos": datos,
        "respuesta": {
            "mensaje_atlas": mensaje_atlas,
            "mensaje_smcc": mensaje_smcc,
        },
        "acciones": {
            "mostrar_atlas": mostrar_atlas,
            "enviar_smcc": enviar_smcc,
            "auto_send": auto_send,
            "requiere_confirmacion": requiere_confirmacion,
        },
    }
