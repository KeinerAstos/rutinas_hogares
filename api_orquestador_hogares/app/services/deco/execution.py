# -*- coding: utf-8 -*-
from app.services.deco.bootstrap import responder_chat as responder_chat_deco
from app.services.maximo.service import (
    consultar_direcciones_clientes,
    consultar_direcciones_por_acs,
    consultar_direcciones_por_cuenta,
    consultar_evidencia_maximo,
    detectar_consulta_direcciones,
    detectar_consulta_maximo,
)


def ejecutar_chat(
    mensaje: str,
    conversation_id: str | None = None,
):
    """
    Ejecuta la logica de negocio de Chat DECO.

    Esta funcion no conoce FastAPI ni HTTPException.
    Conserva el mismo orden funcional que tenia deco_chat():
    Direcciones -> Inventario -> Maximo -> DECO heredado.
    """
    direccion = detectar_consulta_direcciones(
        mensaje,
        conversation_id=conversation_id,
    )

    if direccion:
        accion = direccion.get("accion")

        if accion == "esperar_identificador":
            return {
                "ok": True,
                "tipo_respuesta": "direccion_clientes",
                "codigo": "DIRECCIONES_ESPERANDO_IDENTIFICADOR",
                "respuesta": (
                    "Puedes consultar la dirección de clientes de cuatro formas:\n\n"
                    "• Por OT: direcciones OT5316547\n"
                    "• Por cuenta RR: cuenta 26186733\n"
                    "• Por serial: serial 2CECF7CD134F\n"
                    "• Por MAC: mac 2C:EC:F7:CD:13:4F"
                ),
                "clientes": [],
            }

        if accion == "ejecutar_cuenta":
            return consultar_direcciones_por_cuenta(
                direccion["cuenta"]
            )

        if accion == "ejecutar_acs":
            return consultar_direcciones_por_acs(
                direccion["identificador"],
                direccion["search_by"],
            )

        if accion == "ejecutar_ot":
            return consultar_direcciones_clientes(
                direccion["ot"]
            )

        raise RuntimeError(
            f"Acción de direcciones no soportada: {accion}"
        )

    # Se conserva como import diferido exactamente por compatibilidad.
    from app.services.deco.inventory_red_service import (
        consultar_equipo,
        detectar_consulta_equipo,
    )

    equipo_inventario = detectar_consulta_equipo(mensaje)

    if equipo_inventario:
        return consultar_equipo(equipo_inventario)

    ot = detectar_consulta_maximo(mensaje)

    if ot:
        return consultar_evidencia_maximo(ot)

    return responder_chat_deco(mensaje)
