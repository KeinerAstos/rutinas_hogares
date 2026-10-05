from __future__ import annotations

import re
from typing import Any

from app.services.ftth.troncal_direcciones import (
    consultar_direcciones_troncal_ftth,
)
from app.services.hfc.direcciones import (
    consultar_direcciones_hfc,
)
from app.services.helix.service import (
    consultar_resumen_ot_helix,
)
from app.services.direcciones.downstream import consultar_vecinos_por_cuenta


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    if {
        "tipo_red",
        "tipo_elemento",
        "nodo_detectado",
        "es_hfc",
        "es_ftth",
        "es_troncal",
        "elemento_red",
        "titulo_ot",
    }.intersection(value.keys()):
        return value

    for key in ("data", "resultado", "respuesta", "detalle"):
        found = _payload(value.get(key))
        if found:
            return found

    return {}


def _wo_candidates(wo: str) -> list[str]:
    value = _clean(wo).upper()

    if not re.fullmatch(r"WO\d{13,14}", value):
        return []

    digits = value[2:]

    if len(digits) == 14:
        canonical = "WO" + str(int(digits)).zfill(13)
        if canonical != value:
            return [canonical, value]

    return [value]


def _clasificar_wo(wo: str) -> dict[str, Any]:
    last = {}

    for candidate in _wo_candidates(wo):
        try:
            result = consultar_resumen_ot_helix(candidate)
        except Exception:
            continue

        if not isinstance(result, dict):
            continue

        last = result
        data = _payload(result)

        tipo_red = _clean(
            data.get("tipo_red")
            or result.get("tipo_red")
        ).upper()

        tipo_elemento = _clean(
            data.get("tipo_elemento")
            or result.get("tipo_elemento")
        ).upper()

        titulo = _clean(
            data.get("titulo_ot")
            or result.get("titulo_ot")
        ).upper()


        # DIRECCIONES_EMPRESAS_NEGOCIOS_CLASIFICACION_V1_2
        # EYN = Empresas y Negocios.
        #
        # EYN puede venir al comienzo o precedido por prioridad:
        #   EYN ...
        #   P3 EYN ...
        #
        # Se detecta como token independiente y tiene prioridad
        # absoluta antes de HFC / FTTH / ACS / Diagnosticador.
        es_empresas_negocios = bool(
            re.search(r"(?<![A-Z0-9])EYN(?![A-Z0-9])", titulo)
            or "EMPRESAS Y NEGOCIOS" in titulo
        )

        if es_empresas_negocios:
            return {
                "ok": True,
                "tipo_red": "EMPRESAS_NEGOCIOS",
                "wo_consultada": candidate,
                "helix_result": result,
                "helix_payload": data,
                "titulo_ot": titulo,
                "marker": "DIRECCIONES_EMPRESAS_NEGOCIOS_V1_1",
            }

        # DIRECCIONES_GES_NOTAS_V1
        es_ges = bool(
            re.match(
                r"^GES(?:\s|[-_:])",
                titulo,
            )
        )

        if es_ges:
            return {
                "ok": True,
                "tipo_red": "GES",
                "wo_consultada": candidate,
                "helix_result": result,
                "helix_payload": data,
                "titulo_ot": titulo,
                "marker": "DIRECCIONES_GES_NOTAS_V1",
            }
        es_hfc = bool(
            data.get("es_hfc")
            or result.get("es_hfc")
            or tipo_red == "HFC"
        )

        es_troncal = bool(
            data.get("es_troncal")
            or result.get("es_troncal")
            or tipo_elemento == "TRONCAL"
            or "TRONCAL GPON" in titulo
        )

        es_ftth = bool(
            data.get("es_ftth")
            or result.get("es_ftth")
            or tipo_red == "FTTH"
            or es_troncal
        )

        # DIRECCIONES_ROUTER_OLT_FTTH_FALLBACK_V2
        #
        # Solo aplica cuando las reglas existentes NO lograron
        # identificar HFC ni FTTH.
        #
        # ZAC-* identifica OLT ZTE FTTH.
        # HAC-* identifica OLT Huawei FTTH.
        #
        # HFC mantiene prioridad y no cambia.
        if not es_hfc and not es_ftth:
            olt_ftth_en_titulo = bool(
                "ZAC-" in titulo
                or "HAC-" in titulo
            )

            if olt_ftth_en_titulo:
                es_ftth = True

                # El servicio FTTH vuelve a validar estos campos.
                # Se normaliza solo la copia entregada downstream.
                data = dict(data)
                data["tipo_red"] = "FTTH"
                data["es_ftth"] = True

        if es_hfc:
            return {
                "ok": True,
                "tipo_red": "HFC",
                "wo_consultada": candidate,
                "helix_result": result,
                "helix_payload": data,
                "marker": "DIRECCIONES_SINGLE_HELIX_ROUTER_V4",
            }

        if es_ftth:
            return {
                "ok": True,
                "tipo_red": "FTTH",
                "wo_consultada": candidate,
                "helix_result": result,
                "helix_payload": data,
                "marker": "DIRECCIONES_SINGLE_HELIX_ROUTER_V4",
            }

    return {
        "ok": False,
        "tipo_red": "",
        "wo_consultada": "",
        "raw": last,
    }

def consultar_direcciones_por_wo(wo: str) -> dict[str, Any]:
    original = _clean(wo).upper()
    classification = _clasificar_wo(original)

    tipo_red = _clean(classification.get("tipo_red")).upper()
    resolved = _clean(
        classification.get("wo_consultada")
    ).upper() or original

    helix_result = classification.get("helix_result")
    if not isinstance(helix_result, dict):
        helix_result = {}

    helix_payload = classification.get("helix_payload")
    if not isinstance(helix_payload, dict):
        helix_payload = {}


    # DIRECCIONES_EMPRESAS_NEGOCIOS_RESPUESTA_V1_1
    # Corte terminal antes del gate de categoria operacional.
    # Una WO EYN puede venir como SERVICIOS FIJOS, pero sigue fuera
    # del alcance residencial de Mesa de Ayuda Hogares.
    if tipo_red == "EMPRESAS_NEGOCIOS":
        titulo_empresas = _clean(
            classification.get("titulo_ot")
            or helix_payload.get("titulo_ot")
        )

        categoria_empresas = _clean(
            helix_payload.get("categoria_operacional")
        )

        mensaje_empresas = (
            "La orden corresponde a Empresas y Negocios (EYN), "
            "por lo tanto es una gestion corporativa y no podemos "
            "realizar la consulta ni entregar informacion de "
            "direcciones de clientes."
        )

        return {
            "ok": False,
            "codigo": "DIRECCIONES_EMPRESAS_NEGOCIOS",
            "tipo_respuesta": "empresas_negocios",
            "wo": original,
            "tipo_red": "EMPRESAS_NEGOCIOS",
            "titulo_ot": titulo_empresas,
            "categoria_operacional": categoria_empresas,
            "router_direcciones": "BLOQUEADO_EMPRESAS_NEGOCIOS",
            "motivo": mensaje_empresas,
            "respuesta": mensaje_empresas,
            "direcciones": {
                "ok": False,
                "codigo": "DIRECCIONES_EMPRESAS_NEGOCIOS",
                "tipo_respuesta": "empresas_negocios",
                "clientes_encontrados": 0,
                "clientes": [],
                "consulta_ejecutada": False,
                "respuesta": mensaje_empresas,
            },
        }

    # DIRECCIONES_GES_NOTAS_V1
    if tipo_red == "GES":

        ges_result = (
            helix_payload.get("ges_direcciones")
            or helix_result.get("ges_direcciones")
        )

        if not isinstance(ges_result, dict):
            ges_result = {
                "ok": False,
                "codigo": "GES_SIN_NOTAS",
                "tipo_respuesta": "ges_direcciones_notas",
                "fuente": "NOTAS_HELIX",
                "notas_revisadas": 0,
                "notas_con_cuentas": 0,
                "clientes_encontrados": 0,
                "clientes": [],
                "respuesta": (
                    "No fue posible obtener las notas de Helix "
                    "para esta gestion GES."
                ),
            }

        clientes_ges = ges_result.get("clientes")

        if not isinstance(clientes_ges, list):
            clientes_ges = []

        # GES: una sola cuenta puntual sirve como entrada a Diagnosticador.
        # Las demás cuentas de Helix se conservan sin lanzar más consultas.
        cuenta_diagnosticador = next(
            (
                _clean(item.get("cuenta"))
                for item in clientes_ges
                if isinstance(item, dict) and _clean(item.get("cuenta"))
            ),
            "",
        )
        vecinos_consulta = {
            "cuenta_consulta": cuenta_diagnosticador,
            "consultado": False,
            "ok": False,
            "codigo": "GES_SIN_CUENTA_PARA_DIAGNOSTICADOR",
            "clientes_encontrados": 0,
            "clientes": [],
        }
        if cuenta_diagnosticador:
            try:
                consulta = consultar_vecinos_por_cuenta(cuenta_diagnosticador)
                if isinstance(consulta, dict):
                    vecinos = consulta.get("clientes")
                    if not isinstance(vecinos, list):
                        vecinos = []
                    vecinos_consulta = {
                        "cuenta_consulta": cuenta_diagnosticador,
                        "consultado": True,
                        "ok": bool(consulta.get("ok")),
                        "codigo": _clean(consulta.get("codigo")),
                        "clientes_encontrados": len(vecinos),
                        "clientes": vecinos,
                    }
            except Exception:
                vecinos_consulta["consultado"] = True
                vecinos_consulta["codigo"] = "GES_DIAGNOSTICADOR_ERROR"

        cantidad_ges = int(
            ges_result.get("clientes_encontrados")
            or len(clientes_ges)
            or 0
        )

        codigo_ges = (
            _clean(
                ges_result.get("codigo")
            )
            or "GES_SIN_NOTAS"
        )

        respuesta_ges = _clean(
            ges_result.get("respuesta")
        )

        return {
            "ok": bool(
                ges_result.get("ok")
            ),
            "codigo": codigo_ges,
            "tipo_respuesta": "ges_direcciones_notas",
            "wo": original,
            "tipo_red": "GES",
            "titulo_ot": _clean(
                classification.get("titulo_ot")
                or helix_payload.get("titulo_ot")
            ),
            "incidente_relacionado": _clean(
                helix_payload.get("incidente_relacionado")
                or helix_result.get("incidente_relacionado")
            ),
            "router_direcciones": "GES_NOTAS_HELIX",
            "fuente": "NOTAS_HELIX",
            "vecinos_consulta": vecinos_consulta,
            "respuesta": respuesta_ges,
            "direcciones": {
                "ok": bool(
                    ges_result.get("ok")
                ),
                "codigo": codigo_ges,
                "tipo_respuesta": "ges_direcciones_notas",
                "fuente": "NOTAS_HELIX",
                "clientes_encontrados": cantidad_ges,
                "clientes": clientes_ges,
                "notas_revisadas": int(
                    ges_result.get("notas_revisadas")
                    or 0
                ),
                "notas_con_cuentas": int(
                    ges_result.get("notas_con_cuentas")
                    or 0
                ),
                "consulta_ejecutada": True,
                "respuesta": respuesta_ges,
            },
        }
    # DIRECCIONES_GATE_SERVICIOS_FIJOS_V1
    #
    # Fail closed:
    # Direcciones SOLO aplica si Helix indica explícitamente
    # SERVICIOS FIJOS en Categoria operacional.
    gate_payload = helix_payload

    if not gate_payload:
        gate_payload = _payload(
            classification.get("raw")
        )

    if not isinstance(gate_payload, dict):
        gate_payload = {}

    categoria_operacional = _clean(
        gate_payload.get(
            "categoria_operacional"
        )
    )

    categoria_normalizada = (
        categoria_operacional.upper()
    )

    # DIRECCIONES_GATE_CATEGORIAS_PERMITIDAS_V1
    es_servicios_fijos = (
        categoria_normalizada.startswith(
            "SERVICIOS FIJOS"
        )
    )

    es_redes_neutras = (
        categoria_normalizada.startswith(
            "REDES NEUTRAS"
        )
    )

    categoria_permitida = (
        es_servicios_fijos
        or es_redes_neutras
    )

    # DIRECCIONES_GATE_CATEGORIA_NO_DISPONIBLE_V1
    if not categoria_normalizada:
        return {
            "ok": False,
            "codigo": "DIRECCIONES_CATEGORIA_NO_DISPONIBLE",
            "wo": original,
            "tipo_red": tipo_red,
            "router_direcciones": "BLOQUEADO_CATEGORIA_NO_DISPONIBLE",
            "categoria_operacional": "",
            "motivo": (
                "Helix no entrego la Categoria operacional. "
                "No se continua por seguridad."
            ),
            "direcciones": {
                "ok": False,
                "codigo": "DIRECCIONES_CATEGORIA_NO_DISPONIBLE",
                "categoria_operacional": "",
                "clientes_encontrados": 0,
                "clientes": [],
            },
        }

    if not categoria_permitida:
        return {
            "ok": False,
            # Se conserva el codigo historico para no romper consumidores
            # actuales de Mesa de Ayuda. La regla operativa ahora acepta
            # SERVICIOS FIJOS o REDES NEUTRAS.
            "codigo": "DIRECCIONES_NO_SERVICIOS_FIJOS",
            "wo": original,
            "tipo_red": tipo_red,
            "router_direcciones": "BLOQUEADO_CATEGORIA",
            "categoria_operacional": categoria_operacional,
            "categorias_permitidas": [
                "SERVICIOS FIJOS",
                "REDES NEUTRAS",
            ],
            "motivo": (
                "La Categoria operacional de Helix no corresponde a "
                "SERVICIOS FIJOS ni REDES NEUTRAS."
            ),
            "direcciones": {
                "ok": False,
                "codigo": "DIRECCIONES_NO_SERVICIOS_FIJOS",
                "categoria_operacional": categoria_operacional,
                "categorias_permitidas": [
                    "SERVICIOS FIJOS",
                    "REDES NEUTRAS",
                ],
                "clientes_encontrados": 0,
                "clientes": [],
            },
        }

    # DIRECCIONES_CGE_RECLAMACION_USUARIO_V4E
    titulo_cge = _clean(
        gate_payload.get("titulo_ot")
    ).upper()

    # DIRECCIONES_CGE_HFC_PRECEDENCE_V1
    # HFC ya identificado conserva prioridad, excepto Reclamacion de usuario.
    if titulo_cge.startswith("CGE_") and not (
        tipo_red == "HFC"
        and "RECLAM" not in categoria_operacional.upper()
    ):
        from app.services.direcciones.cge import (
            es_categoria_reclamacion_usuario,
            extraer_direccion_cge,
        )

        if es_categoria_reclamacion_usuario(
            categoria_operacional
        ):
            resultado_cge = extraer_direccion_cge(
                _clean(
                    gate_payload.get("descripcion_ot")
                    or helix_result.get("descripcion_ot")
                )
            )

            return {
                "ok": bool(resultado_cge.get("ok")),
                "codigo": _clean(resultado_cge.get("codigo")),
                "tipo_respuesta": "cge_reclamacion_usuario",
                "wo": original,
                "tipo_red": tipo_red,
                "tipo_elemento": _clean(
                    gate_payload.get("tipo_elemento")
                ),
                "titulo_ot": _clean(
                    gate_payload.get("titulo_ot")
                ),
                "categoria_operacional": categoria_operacional,
                "router_direcciones": "CGE_RECLAMACION_USUARIO",
                "fuente": "DESCRIPCION_HELIX",
                "direcciones": resultado_cge,
            }

        # DIRECCIONES_CGE_DESCRIPCION_DIRECTA_V1
        #
        # Algunas CGE contienen directamente en la descripcion:
        # - Cuenta
        # - Direccion
        #
        # El parser CGE existente valida y extrae estos datos.
        # Solo se responde directamente si AMBOS campos existen.
        # Si no, el flujo historico continua sin cambios.
        _descripcion_cge_directa = _clean(
            gate_payload.get("descripcion_ot")
            or helix_result.get("descripcion_ot")
        )

        if _descripcion_cge_directa:
            _resultado_cge_directo = extraer_direccion_cge(
                _descripcion_cge_directa
            )

            _clientes_cge_directo = (
                _resultado_cge_directo.get("clientes")
                if isinstance(_resultado_cge_directo, dict)
                else []
            )

            _cliente_cge_directo = (
                _clientes_cge_directo[0]
                if isinstance(_clientes_cge_directo, list)
                and _clientes_cge_directo
                and isinstance(_clientes_cge_directo[0], dict)
                else {}
            )

            _direccion_cge_directa = _clean(
                _cliente_cge_directo.get("direccion")
            )

            _cuenta_cge_directa = _clean(
                _cliente_cge_directo.get("cuenta")
            )

            if (
                bool(_resultado_cge_directo.get("ok"))
                and _direccion_cge_directa
                and _cuenta_cge_directa
            ):
                _resultado_cge_directo[
                    "codigo"
                ] = "DIRECCIONES_CGE_DESCRIPCION_DIRECTA_OK"

                _resultado_cge_directo[
                    "tipo_respuesta"
                ] = "cge_descripcion_directa"

                return {
                    "ok": True,
                    "codigo": "DIRECCIONES_CGE_DESCRIPCION_DIRECTA_OK",
                    "tipo_respuesta": "cge_descripcion_directa",
                    "wo": original,
                    "tipo_red": tipo_red,
                    "tipo_elemento": _clean(
                        gate_payload.get("tipo_elemento")
                    ),
                    "titulo_ot": _clean(
                        gate_payload.get("titulo_ot")
                    ),
                    "categoria_operacional": categoria_operacional,
                    "router_direcciones": "CGE_DESCRIPCION_DIRECTA",
                    "fuente": "DESCRIPCION_HELIX",
                    "direcciones": _resultado_cge_directo,
                }

        # DIRECCIONES_CGE_FTTH_CI_FALLBACK_V2
        #
        # CGE FTTH:
        #
        # 1. Se respetan primero los datos de topologia ya encontrados
        #    por el flujo normal.
        #
        # 2. Si faltan datos, se intenta completar SOLO los faltantes
        #    desde CI del INC.
        #
        # 3. CI nunca es obligatorio.
        #
        # 4. Si despues de eso no existe una topologia FTTH minima,
        #    se cae al return historico CGE_NO_SOPORTADO que esta
        #    inmediatamente debajo.
        if tipo_red == "FTTH":
            _cge_ftth_data = dict(helix_payload)

            # Gate puede contener datos mas recientes que helix_payload.
            for _field in (
                "elemento_red",
                "rack",
                "shelf",
                "slot",
                "port",
                "frame",
                "subslot",
                "ci",
                "descripcion_ci",
            ):
                _gate_value = gate_payload.get(_field)

                if (
                    not _clean(_cge_ftth_data.get(_field))
                    and _clean(_gate_value)
                ):
                    _cge_ftth_data[_field] = _gate_value

            _ci_fallback_usado = False

            _faltan_topologia = any(
                not _clean(_cge_ftth_data.get(_field))
                for _field in (
                    "elemento_red",
                    "rack",
                    "slot",
                    "port",
                )
            )

            if _faltan_topologia:
                _ci_value = _clean(
                    _cge_ftth_data.get("ci")
                )

                _ci_match = re.fullmatch(
                    r"(?i)\s*"
                    r"(?P<rack>\d+)-"
                    r"(?P<shelf>\d+)-"
                    r"(?P<slot>\d+)-"
                    r"(?:HFTH|FTTH)-"
                    r"(?P<port>\d+)_"
                    r"(?P<elemento>.+?)"
                    r"\s*",
                    _ci_value,
                )

                if _ci_match:
                    _ci_values = {
                        "rack": _ci_match.group("rack"),
                        "shelf": _ci_match.group("shelf"),
                        "slot": _ci_match.group("slot"),
                        "port": _ci_match.group("port"),
                        "elemento_red": (
                            _ci_match.group("elemento")
                            .strip()
                            .upper()
                        ),
                    }

                    for _field, _value in _ci_values.items():
                        # IMPORTANTE:
                        # CI solo completa valores AUSENTES.
                        # Nunca pisa topologia obtenida normalmente.
                        if not _clean(
                            _cge_ftth_data.get(_field)
                        ):
                            _cge_ftth_data[_field] = _value

                    _ci_fallback_usado = True

            # Dejamos que el servicio FTTH existente sea la autoridad.
            #
            # Para entrar aqui se requiere al menos elemento + puerto.
            # Los demas campos se entregan tal como fueron recuperados.
            _topologia_ftth_disponible = bool(
                _clean(
                    _cge_ftth_data.get("elemento_red")
                )
                and _clean(
                    _cge_ftth_data.get("port")
                )
            )

            if _topologia_ftth_disponible:
                _resultado_cge_ftth = (
                    consultar_direcciones_troncal_ftth(
                        resolved,
                        helix_result=helix_result,
                        helix_data=_cge_ftth_data,
                    )
                )

                if not isinstance(
                    _resultado_cge_ftth,
                    dict,
                ):
                    _resultado_cge_ftth = {
                        "ok": False,
                        "codigo": (
                            "FTTH_DIRECCIONES_RESPUESTA_INVALIDA"
                        ),
                        "wo": resolved,
                    }

                _resultado_cge_ftth.setdefault(
                    "tipo_red",
                    "FTTH",
                )

                _resultado_cge_ftth[
                    "router_direcciones"
                ] = "CGE_FTTH"

                _resultado_cge_ftth[
                    "router_wo_original"
                ] = original

                _resultado_cge_ftth[
                    "router_wo_consultada"
                ] = resolved

                _resultado_cge_ftth[
                    "ci_fallback_usado"
                ] = _ci_fallback_usado

                _resultado_cge_ftth[
                    "fuente_topologia_cge"
                ] = (
                    "CI_INC_FALLBACK"
                    if _ci_fallback_usado
                    else "HELIX_NORMAL"
                )

                if _ci_fallback_usado:
                    _resultado_cge_ftth["ci"] = _clean(
                        _cge_ftth_data.get("ci")
                    )

                    _resultado_cge_ftth[
                        "descripcion_ci"
                    ] = _clean(
                        _cge_ftth_data.get(
                            "descripcion_ci"
                        )
                    )

                return _resultado_cge_ftth

        return {
            "ok": False,
            "codigo": "DIRECCIONES_CGE_CATEGORIA_NO_SOPORTADA",
            "wo": original,
            "tipo_red": tipo_red,
            "titulo_ot": _clean(
                gate_payload.get("titulo_ot")
            ),
            "categoria_operacional": categoria_operacional,
            "router_direcciones": "CGE_NO_SOPORTADO",
            "direcciones": {
                "ok": False,
                "codigo": "DIRECCIONES_CGE_CATEGORIA_NO_SOPORTADA",
                "clientes_encontrados": 0,
                "clientes": [],
                "consulta_ejecutada": False,
            },
        }
    if tipo_red == "HFC":
        result = consultar_direcciones_hfc(
            original,
            helix_result=helix_result,
            helix_data=helix_payload,
            consulted_wo=resolved,
        )

        if not isinstance(result, dict):
            result = {
                "ok": False,
                "codigo": "HFC_DIRECCIONES_RESPUESTA_INVALIDA",
                "wo": original,
            }

        result.setdefault("tipo_red", "HFC")
        result["router_direcciones"] = "HFC"
        result["router_wo_original"] = original
        result["router_wo_consultada"] = resolved
        return result

    if tipo_red == "FTTH":
        result = consultar_direcciones_troncal_ftth(
            resolved,
            helix_result=helix_result,
            helix_data=helix_payload,
        )

        if not isinstance(result, dict):
            result = {
                "ok": False,
                "codigo": "FTTH_DIRECCIONES_RESPUESTA_INVALIDA",
                "wo": resolved,
            }

        result.setdefault("tipo_red", "FTTH")
        result["router_direcciones"] = "FTTH"
        result["router_wo_original"] = original
        result["router_wo_consultada"] = resolved
        return result

    return {
        "ok": False,
        "codigo": "DIRECCIONES_TIPO_RED_NO_IDENTIFICADO",
        "wo": original,
        "tipo_red": "",
        "router_direcciones": "SIN_CLASIFICAR",
        "direcciones": {
            "ok": False,
            "codigo": "DIRECCIONES_TIPO_RED_NO_IDENTIFICADO",
            "clientes_encontrados": 0,
            "clientes": [],
        },
    }
