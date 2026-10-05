# Runtime HFC real extraido de deco_service.py

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.hfc.hfc_remote_service import query_hfc_snapshot, snapshot_to_hfc_result

PROJECT_DIR=None
HFC_DEEP_DIR=None
HFC_DEEP_SCRIPT=None
HFC_DEEP_TIMEOUT_SECONDS=None
HFC_DEEP_DEFAULT_CMD_TIMEOUT=None
_motor_callback=None
_extraer_valor_callback=None
_pathtrak_capture_callback=None
_pathtrak_public_url_callback=None

def configure(*,project_dir,hfc_deep_dir,hfc_deep_script,hfc_deep_timeout_seconds,hfc_deep_default_cmd_timeout,motor_callback,extraer_valor_callback,pathtrak_capture_callback,pathtrak_public_url_callback):
    global PROJECT_DIR,HFC_DEEP_DIR,HFC_DEEP_SCRIPT,HFC_DEEP_TIMEOUT_SECONDS,HFC_DEEP_DEFAULT_CMD_TIMEOUT
    global _motor_callback,_extraer_valor_callback,_pathtrak_capture_callback,_pathtrak_public_url_callback
    PROJECT_DIR=project_dir; HFC_DEEP_DIR=hfc_deep_dir; HFC_DEEP_SCRIPT=hfc_deep_script
    HFC_DEEP_TIMEOUT_SECONDS=hfc_deep_timeout_seconds; HFC_DEEP_DEFAULT_CMD_TIMEOUT=hfc_deep_default_cmd_timeout
    _motor_callback=motor_callback; _extraer_valor_callback=extraer_valor_callback
    _pathtrak_capture_callback=pathtrak_capture_callback; _pathtrak_public_url_callback=pathtrak_public_url_callback

def ejecutar_motor_noc_directo(mensaje):
    return _motor_callback(mensaje)

def extraer_valor_respuesta(texto,etiqueta):
    return _extraer_valor_callback(texto,etiqueta)

def get_pathtrak_capture_func():
    return _pathtrak_capture_callback() if _pathtrak_capture_callback else None

def _pathtrak_public_url(screenshot):
    return _pathtrak_public_url_callback(screenshot) if _pathtrak_public_url_callback else ""

def resolver_nodo_para_hfc_real(nodo: str) -> Dict[str, Any]:
    nodo = str(nodo or "").strip().upper()

    # Fuente principal FRONT-NTT: HFC_AUTO remoto en noc_cable.
    remoto = query_hfc_snapshot(nodo)
    if remoto.get("ok"):
        return {
            "ok": True,
            "nodo": nodo,
            "cmts": remoto.get("cmts", ""),
            "ip": remoto.get("ip", ""),
            "vendor": remoto.get("vendor", ""),
            "fuente": "hfc_auto.remote_snapshot",
            "respuesta_base": "",
            "remote_snapshot": remoto,
        }

    # Fallback heredado: motor normal "estado nodo".
    motor = ejecutar_motor_noc_directo(f"estado nodo {nodo}")
    respuesta = str(motor.get("respuesta", "") or "")

    cmts = extraer_valor_respuesta(respuesta, "CMTS")
    ip = extraer_valor_respuesta(respuesta, "IP")
    vendor = (
        extraer_valor_respuesta(respuesta, "Vendor")
        or extraer_valor_respuesta(respuesta, "Marca")
        or "CASA"
    )

    if cmts and ip:
        return {
            "ok": True,
            "nodo": nodo,
            "cmts": cmts,
            "ip": ip,
            "vendor": vendor,
            "fuente": "motor.estado_nodo",
            "respuesta_base": respuesta,
        }

    return {
        "ok": False,
        "nodo": nodo,
        "error": (
            f"No pude resolver CMTS/IP para el nodo {nodo} usando el motor normal. "
            "Verifica VPN y que 'estado nodo' entregue CMTS/IP."
        ),
        "respuesta_base": respuesta,
        "motor_ok": motor.get("ok"),
        "motor_error": motor.get("error"),
    }

def _extraer_json_path_de_stdout(stdout: str) -> str:
    for linea in str(stdout or "").splitlines():
        if linea.strip().upper().startswith("JSON"):
            partes = linea.split(":", 1)
            if len(partes) == 2:
                return partes[1].strip()

    return ""

def _ultimo_json_hfc(cmts: str, nodo: str) -> Optional[Path]:
    if not HFC_DEEP_DIR.exists():
        return None

    patron = f"*{cmts}*{nodo}*hfc_deep*.json"
    candidatos = list(HFC_DEEP_DIR.glob(patron))

    if not candidatos:
        candidatos = list(HFC_DEEP_DIR.glob(f"*{nodo}*hfc_deep*.json"))

    if not candidatos:
        return None

    return max(candidatos, key=lambda p: p.stat().st_mtime)

def ejecutar_hfc_real_para_nodo(nodo: str, incluir_pathtrak: bool = False) -> Dict[str, Any]:
    nodo = str(nodo or "").strip().upper()

    # HFC_REMOTE_FIRST_V2
    try:
        _snap = query_hfc_snapshot(nodo)
        if isinstance(_snap, dict) and _snap.get("ok"):
            _remote = snapshot_to_hfc_result(_snap)
            if isinstance(_remote, dict) and _remote.get("ok"):
                return _remote
    except Exception as _remote_exc:
        print(f"[DECO][HFC_REMOTE] Snapshot remoto fallo para {nodo}: {_remote_exc}")


    # Fuente principal FRONT-NTT: consolidado HFC_AUTO remoto.
    remoto = query_hfc_snapshot(nodo)
    resultado_remoto = snapshot_to_hfc_result(remoto)

    if resultado_remoto.get("ok"):
        pathtrak = None

        if incluir_pathtrak:
            cap_func = get_pathtrak_capture_func()
            pathtrak = {}

            if cap_func is None:
                pathtrak["qoe"] = {"ok": False, "error": "Módulo PathTrak no disponible."}
                pathtrak["ondas"] = {"ok": False, "error": "Módulo PathTrak no disponible."}
            else:
                for tipo in ["qoe", "ondas"]:
                    try:
                        captura = cap_func(nodo, tipo_captura=tipo)
                        screenshot = captura.get("screenshot") if captura else ""
                        pathtrak[tipo] = {
                            "ok": bool(captura and captura.get("ok")),
                            "region": captura.get("region") if captura else "",
                            "url": captura.get("url") if captura else "",
                            "screenshot": screenshot,
                            "public_url": _pathtrak_public_url(screenshot),
                            "error": captura.get("error") if captura else "",
                        }
                    except Exception as exc:
                        pathtrak[tipo] = {"ok": False, "error": str(exc)}

        resultado_remoto["pathtrak"] = pathtrak
        return resultado_remoto

    # Fallback heredado: diagnostico local anterior.
    if not HFC_DEEP_SCRIPT.exists():
        return {
            "ok": False,
            "nodo": nodo,
            "error": "No encontré scripts/hfc_deep_real_probe.py.",
            "script": str(HFC_DEEP_SCRIPT),
        }

    resolucion = resolver_nodo_para_hfc_real(nodo)

    if not resolucion.get("ok"):
        return {
            "ok": False,
            "nodo": nodo,
            "error": resolucion.get("error"),
            "resolucion": resolucion,
        }

    cmts = resolucion.get("cmts", "")
    ip = resolucion.get("ip", "")
    vendor = resolucion.get("vendor", "CASA") or "CASA"

    if not cmts or not ip:
        return {
            "ok": False,
            "nodo": nodo,
            "error": f"No tengo CMTS/IP suficiente para {nodo}.",
            "resolucion": resolucion,
        }

    cmd = [
        sys.executable,
        str(HFC_DEEP_SCRIPT),
        "--node", nodo,
        "--cmts", cmts,
        "--ip", ip,
        "--vendor", vendor,
        "--timeout", str(HFC_DEEP_DEFAULT_CMD_TIMEOUT),
    ]

    print(f"[DECO][HFC_REAL] Ejecutando: {' '.join(cmd)}")

    inicio = time.time()
    run_env = dict(os.environ)
    run_env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=HFC_DEEP_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
            env=run_env,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "nodo": nodo,
            "cmts": cmts,
            "ip": ip,
            "error": (
                f"El diagnóstico HFC real tardó más de {HFC_DEEP_TIMEOUT_SECONDS} segundos."
            ),
            "stdout": e.stdout,
            "stderr": e.stderr,
        }

    duracion = round(time.time() - inicio, 1)

    if proc.returncode != 0:
        return {
            "ok": False,
            "nodo": nodo,
            "cmts": cmts,
            "ip": ip,
            "error": f"El script HFC real terminó con código {proc.returncode}.",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "cmd": cmd,
            "duracion_seg": duracion,
        }

    json_path_txt = _extraer_json_path_de_stdout(proc.stdout)
    json_path = Path(json_path_txt) if json_path_txt else None

    if not json_path or not json_path.exists():
        json_path = _ultimo_json_hfc(cmts, nodo)

    if not json_path or not json_path.exists():
        return {
            "ok": False,
            "nodo": nodo,
            "cmts": cmts,
            "ip": ip,
            "error": "El script terminó OK, pero no encontré el JSON generado.",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duracion_seg": duracion,
        }

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "ok": False,
            "nodo": nodo,
            "cmts": cmts,
            "ip": ip,
            "error": "No pude leer el JSON generado por el diagnóstico HFC real.",
            "json_path": str(json_path),
            "traceback": traceback.format_exc(),
        }

    pathtrak = None

    if incluir_pathtrak:
        cap_func = get_pathtrak_capture_func()
        pathtrak = {}

        if cap_func is None:
            pathtrak["qoe"] = {"ok": False, "error": "Módulo PathTrak no disponible."}
            pathtrak["ondas"] = {"ok": False, "error": "Módulo PathTrak no disponible."}
        else:
            for tipo in ["qoe", "ondas"]:
                try:
                    captura = cap_func(nodo, tipo_captura=tipo)
                    screenshot = captura.get("screenshot") if captura else ""

                    pathtrak[tipo] = {
                        "ok": bool(captura and captura.get("ok")),
                        "region": captura.get("region") if captura else "",
                        "url": captura.get("url") if captura else "",
                        "screenshot": screenshot,
                        "public_url": _pathtrak_public_url(screenshot),
                        "error": captura.get("error") if captura else "",
                    }
                except Exception as e:
                    pathtrak[tipo] = {
                        "ok": False,
                        "error": str(e),
                    }

    return {
        "ok": True,
        "nodo": nodo,
        "cmts": cmts,
        "ip": ip,
        "vendor": vendor,
        "fuente_resolucion": resolucion.get("fuente"),
        "json_path": str(json_path),
        "duracion_seg": duracion,
        "stdout": proc.stdout,
        "data": data,
        "pathtrak": pathtrak,
    }

def hacer_diagnostico_real_hfc(nodos: List[str], incluir_pathtrak: bool = False) -> Dict[str, Any]:
    resultados = []

    for nodo in nodos:
        resultados.append(ejecutar_hfc_real_para_nodo(nodo, incluir_pathtrak=incluir_pathtrak))

    return {
        "ok": True,
        "tipo_respuesta": "hfc_real_diagnostic",
        "respuesta": {
            "total": len(resultados),
            "resultados": resultados,
        },
    }
