"""Orquestacion del diagnostico profundo HFC."""

from typing import Any, Dict, List

_callbacks = {}

def configure(**callbacks):
    _callbacks.clear()
    _callbacks.update(callbacks)

def ejecutar_motor_noc_directo(mensaje):
    return _callbacks["motor"](mensaje)

def extraer_valor_respuesta(texto, etiqueta):
    return _callbacks["extraer_valor"](texto, etiqueta)

def ejecutar_hfc_real_para_nodo(nodo, incluir_pathtrak=False):
    return _callbacks["hfc_execute"](nodo, incluir_pathtrak=incluir_pathtrak)

def get_pathtrak_capture_func():
    return _callbacks["capture_factory"]()

def _convertir_captura_pathtrak(obj):
    return _callbacks["convert_capture"](obj)

def extraer_diagnostico_respuesta(texto):
    return _callbacks["extraer_diagnostico"](texto)

def clasificar_conclusion_profunda(info):
    return _callbacks["clasificar"](info)

def debe_reforzar_con_hfc_real(info):
    return _callbacks["reforzar"](info)

def compactar_hfc_real(resultado):
    return _callbacks["compactar"](resultado)

def info_desde_hfc_real(info_base, hfc):
    return _callbacks["info_hfc"](info_base, hfc)

def conclusion_desde_hfc_real(nodo, hfc):
    return _callbacks["conclusion_hfc"](nodo, hfc)


def hacer_diagnostico_profundo_nodos(nodos: List[str]) -> Dict[str, Any]:
    """
    Ejecuta diagnóstico profundo DECO.

    Flujo:
    1. Consulta motor base `estado nodo`.
    2. Si el motor base queda en REVISAR/Total 0 para CASA, ejecuta automáticamente HFC real V1.2.
    3. Si HFC real entrega dictamen fuerte, ese resultado manda sobre el motor base.
    4. Agrega PathTrak si está disponible.
    """

    resultados = []
    cap_func = get_pathtrak_capture_func()

    for nodo in nodos:
        nodo = str(nodo or "").strip().upper()
        print(f"[DECO][PROFUNDO] Iniciando diagnóstico profundo para nodo {nodo}")

        motor = ejecutar_motor_noc_directo(f"estado nodo {nodo}")
        respuesta_motor = motor.get("respuesta", "")

        info_base = {
            "nodo": extraer_valor_respuesta(respuesta_motor, "Nodo") or nodo,
            "cmts": extraer_valor_respuesta(respuesta_motor, "CMTS"),
            "ip": extraer_valor_respuesta(respuesta_motor, "IP"),
            "vendor": extraer_valor_respuesta(respuesta_motor, "Vendor"),
            "driver": extraer_valor_respuesta(respuesta_motor, "Driver"),
            "estado": extraer_valor_respuesta(respuesta_motor, "Estado"),
            "severidad": extraer_valor_respuesta(respuesta_motor, "Severidad"),
            "decision": extraer_valor_respuesta(respuesta_motor, "Decisión"),
            "total": extraer_valor_respuesta(respuesta_motor, "Total"),
            "online": extraer_valor_respuesta(respuesta_motor, "Online"),
            "offline": extraer_valor_respuesta(respuesta_motor, "Offline"),
            "init": extraer_valor_respuesta(respuesta_motor, "Init"),
            "pct_online": extraer_valor_respuesta(respuesta_motor, "% Online"),
            "diagnostico": extraer_diagnostico_respuesta(respuesta_motor),
        }

        info = dict(info_base)
        conclusion = clasificar_conclusion_profunda(info_base)
        hfc_real = None
        fuente_dictamen = "motor_base"

        if debe_reforzar_con_hfc_real(info_base):
            print(f"[DECO][PROFUNDO] Motor base no concluyente para {nodo}. Ejecutando HFC real V1.2...")
            hfc_resultado = ejecutar_hfc_real_para_nodo(nodo, incluir_pathtrak=False)
            hfc_real = compactar_hfc_real(hfc_resultado)

            if hfc_real.get("ok"):
                info = info_desde_hfc_real(info_base, hfc_real)
                conclusion = conclusion_desde_hfc_real(nodo, hfc_real)
                fuente_dictamen = "hfc_real"
            else:
                fuente_dictamen = "motor_base_hfc_real_fallo"

        if cap_func is None:
            qoe = {"ok": False, "error": "Módulo PathTrak no disponible."}
            ondas = {"ok": False, "error": "Módulo PathTrak no disponible."}
        else:
            try:
                print(f"[DECO][PROFUNDO] Capturando QoE {nodo}")
                qoe = cap_func(nodo, tipo_captura="qoe")
            except Exception as e:
                qoe = {"ok": False, "error": str(e)}

            try:
                print(f"[DECO][PROFUNDO] Capturando ondas {nodo}")
                ondas = cap_func(nodo, tipo_captura="ondas")
            except Exception as e:
                ondas = {"ok": False, "error": str(e)}

        resultados.append({
            "nodo": nodo,
            "motor_ok": motor.get("ok"),
            "fuente_dictamen": fuente_dictamen,
            "info_base": info_base,
            "info": info,
            "conclusion": conclusion,
            "hfc_real": hfc_real,
            "qoe": _convertir_captura_pathtrak(qoe),
            "ondas": _convertir_captura_pathtrak(ondas),
            "respuesta_cruda": respuesta_motor,
        })

    return {
        "ok": True,
        "tipo_respuesta": "diagnostico_profundo",
        "respuesta": {"total": len(resultados), "resultados": resultados},
    }
