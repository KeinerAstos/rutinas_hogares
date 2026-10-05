"""Ejecucion paralela de diagnostico HFC + captura QoE PathTrak."""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

_base_responder = None
_extract_node = None
_capture_qoe = None
_normalize_capture = None

def configure(*, base_responder, extract_node, capture_qoe, normalize_capture):
    global _base_responder
    global _extract_node
    global _capture_qoe
    global _normalize_capture
    _base_responder = base_responder
    _extract_node = extract_node
    _capture_qoe = capture_qoe
    _normalize_capture = normalize_capture

def responder_chat_paralelo(mensaje: str) -> Dict[str, Any]:
    mensaje_limpio = str(mensaje or "").strip()
    nodo_pathtrak = _extract_node(mensaje_limpio)

    if not nodo_pathtrak:
        return _base_responder(mensaje_limpio)

    # BEGIN PATHTRAK_PURO_V1
    texto_norm = (
        mensaje_limpio.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

    es_pathtrak_puro = (
        "pathtrak" in texto_norm
        and not any(
            termino in texto_norm
            for termino in (
                "diagnostico real",
                "diagnostico profundo",
                "hfc",
            )
        )
    )

    if es_pathtrak_puro:
        resp = _base_responder(
            f"captura qoe nodo {nodo_pathtrak}"
        )

        if isinstance(resp, dict):
            resp["alias_deco"] = True
            resp["alias_original"] = mensaje_limpio
            resp["alias_convertido"] = f"captura qoe nodo {nodo_pathtrak}"
            resp["metodo"] = "pathtrak_puro"

        return resp
    # END PATHTRAK_PURO_V1

    inicio = time.time()

    # Ejecutar diagnóstico HFC y captura QoE en paralelo para no sumar tiempos.
    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_diag = executor.submit(
            _base_responder,
            f"diagnóstico real nodo {nodo_pathtrak}",
        )
        fut_cap = executor.submit(
            _capture_qoe,
            nodo_pathtrak,
        )

        resp = fut_diag.result()
        captura_raw = fut_cap.result()

    captura = _normalize_capture(captura_raw)
    duracion = round(time.time() - inicio, 1)

    if isinstance(resp, dict):
        resp["metodo"] = "diagnostico_real_con_pathtrak"
        resp["tipo_operativo"] = "Diagnóstico HFC real + PathTrak"
        resp["fallback_deco"] = False
        resp["alias_deco"] = True
        resp["alias_original"] = mensaje_limpio
        resp["alias_convertido"] = f"diagnóstico real nodo {nodo_pathtrak} con pathtrak"
        resp["duracion_total_seg"] = duracion
        resp["pathtrak"] = captura

        resumen = resp.get("resumen")
        if isinstance(resumen, dict):
            resumen["tipo_operativo"] = "Diagnóstico HFC real + PathTrak"
            resumen["pathtrak_solicitado"] = True
            resumen["pathtrak_ok"] = captura.get("ok")
            resumen["pathtrak_url"] = captura.get("url")
            resumen["duracion_total_seg"] = duracion

        if isinstance(resp.get("respuesta"), str):
            texto = resp["respuesta"]

            if "Tipo        : Diagnóstico HFC real + PathTrak" not in texto:
                texto = texto.replace(
                    "Driver      : HFC_REAL_CASA\n",
                    "Driver      : HFC_REAL_CASA\nTipo        : Diagnóstico HFC real + PathTrak\n",
                    1,
                )

            if captura.get("url"):
                linea = f"PathTrak    : Captura QoE generada\nCaptura URL : {captura.get('url')}\n"
            else:
                linea = f"PathTrak    : No se pudo generar captura QoE ({captura.get('error') or 'sin URL retornada'})\n"

            if "PathTrak    :" not in texto:
                texto = texto.replace(
                    "JSON crudo",
                    linea + "JSON crudo",
                    1,
                )

            resp["respuesta"] = texto

    return resp
