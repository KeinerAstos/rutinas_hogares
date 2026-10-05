# -*- coding: utf-8 -*-
"""
Punto de composicion y compatibilidad del dominio DECO.

Este módulo reemplaza el uso local de chatbot_web.py.
Expone lógica reutilizable para:
- Chat normal NOC: estado nodo, incidente, cmts.
- Diagnóstico HFC real V1.2.
- Capturas PathTrak QoE/Ondas.

Requiere que en la raíz del proyecto existan:
  chatbot/
  diagnostic_engine/
  incident_sources/
  pathtrak/
  scripts/hfc_deep_real_probe.py
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.deco import pathtrak_runtime as _pathtrak_runtime
from app.services.deco import pathtrak_capture as _pathtrak_capture
from app.services.deco import pathtrak_parallel as _pathtrak_parallel
from app.services.deco import pathtrak_intent as _pathtrak_intent
from app.services.deco import hfc_intent as _hfc_intent
from app.services.deco import hfc_analysis as _hfc_analysis
from app.services.deco import hfc_runtime as _hfc_runtime
from app.services.deco import noc_core as _noc_core
from app.services.deco import responder_pipeline as _responder_pipeline
from app.services.deco import hfc_diagnostic as _hfc_diagnostic
from app.services.deco import pathtrak_response as _pathtrak_response


PROJECT_DIR = Path(__file__).resolve().parents[3]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

HFC_DEEP_SCRIPT = (PROJECT_DIR / "scripts" / "hfc_deep_real_probe.py").resolve()
HFC_DEEP_DIR = (PROJECT_DIR / "diagnostics" / "hfc_deep").resolve()
PATHTRAK_SCREENSHOT_DIR = _pathtrak_runtime.PATHTRAK_SCREENSHOT_DIR

HFC_DEEP_TIMEOUT_SECONDS = int(os.getenv("HFC_DEEP_TIMEOUT_SECONDS", "260"))
HFC_DEEP_DEFAULT_CMD_TIMEOUT = int(os.getenv("HFC_DEEP_CMD_TIMEOUT", "90"))

_chat_service = None
_chat_error = None

_SESSION = {
    "ultimo_cmts": None,
    "ultimo_ip": None,
}


def _load_dotenv_if_available():
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_DIR / ".env")
        load_dotenv(Path.cwd() / ".env")
    except Exception:
        pass


_load_dotenv_if_available()


def _sync_noc_core() -> None:
    _noc_core.configure(globals())

def get_chat_service(*args, **kwargs):
    _sync_noc_core()
    return _noc_core.get_chat_service(*args, **kwargs)


def get_pathtrak_capture_func():
    """Shim de compatibilidad hacia el runtime PathTrak aislado."""
    return _pathtrak_runtime.get_capture_func()

def health() -> Dict[str, Any]:
    svc = get_chat_service()
    cap = get_pathtrak_capture_func()

    return {
        "ok": svc is not None,
        "chat_loaded": svc is not None,
        "chat_error": _chat_error,
        "pathtrak_loaded": cap is not None,
        "pathtrak_error": _pathtrak_runtime.get_capture_error(),
        "hfc_script_exists": HFC_DEEP_SCRIPT.exists(),
        "project_dir": str(PROJECT_DIR),
    }


def extraer_valor_respuesta(*args, **kwargs):
    _sync_noc_core()
    return _noc_core.extraer_valor_respuesta(*args, **kwargs)


def ejecutar_motor_noc_directo(*args, **kwargs):
    _sync_noc_core()
    return _noc_core.ejecutar_motor_noc_directo(*args, **kwargs)


def detectar_diagnostico_real_hfc(mensaje: str) -> Optional[Dict[str, Any]]:
    """Shim de compatibilidad hacia hfc_intent."""
    return _hfc_intent.detectar_diagnostico_real_hfc(mensaje)


def detectar_intencion_pathtrak(mensaje: str) -> Optional[Dict[str, Any]]:
    """Shim de compatibilidad hacia pathtrak_intent."""
    return _pathtrak_intent.detectar_intencion_pathtrak(mensaje)



def _pathtrak_public_url(screenshot: Optional[str]) -> str:
    return _pathtrak_response._pathtrak_public_url(screenshot)


def construir_respuesta_pathtrak(resultado: Dict[str, Any]) -> Dict[str, Any]:
    return _pathtrak_response.construir_respuesta_pathtrak(resultado)



def _sync_hfc_runtime() -> None:
    _hfc_runtime.configure(
        project_dir=PROJECT_DIR, hfc_deep_dir=HFC_DEEP_DIR, hfc_deep_script=HFC_DEEP_SCRIPT,
        hfc_deep_timeout_seconds=HFC_DEEP_TIMEOUT_SECONDS, hfc_deep_default_cmd_timeout=HFC_DEEP_DEFAULT_CMD_TIMEOUT,
        motor_callback=ejecutar_motor_noc_directo, extraer_valor_callback=extraer_valor_respuesta,
        pathtrak_capture_callback=get_pathtrak_capture_func, pathtrak_public_url_callback=_pathtrak_public_url,
    )

def resolver_nodo_para_hfc_real(nodo: str) -> Dict[str, Any]:
    _sync_hfc_runtime()
    return _hfc_runtime.resolver_nodo_para_hfc_real(nodo)


def _extraer_json_path_de_stdout(stdout: str) -> str:
    return _hfc_runtime._extraer_json_path_de_stdout(stdout)


def _ultimo_json_hfc(cmts: str, nodo: str) -> Optional[Path]:
    _sync_hfc_runtime()
    return _hfc_runtime._ultimo_json_hfc(cmts,nodo)


def ejecutar_hfc_real_para_nodo(nodo: str, incluir_pathtrak: bool = False) -> Dict[str, Any]:
    _sync_hfc_runtime()
    return _hfc_runtime.ejecutar_hfc_real_para_nodo(nodo,incluir_pathtrak=incluir_pathtrak)


def hacer_diagnostico_real_hfc(nodos: List[str], incluir_pathtrak: bool = False) -> Dict[str, Any]:
    _sync_hfc_runtime()
    return _hfc_runtime.hacer_diagnostico_real_hfc(nodos,incluir_pathtrak=incluir_pathtrak)



def extraer_diagnostico_respuesta(texto: str) -> str:
    return _hfc_analysis.extraer_diagnostico_respuesta(texto)


def detectar_diagnostico_profundo(mensaje: str) -> Optional[Dict[str, Any]]:
    """Shim de compatibilidad hacia hfc_intent."""
    return _hfc_intent.detectar_diagnostico_profundo(mensaje)


def clasificar_conclusion_profunda(info: Dict[str, Any]) -> Dict[str, str]:
    return _hfc_analysis.clasificar_conclusion_profunda(info)


def _convertir_captura_pathtrak(captura: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return _pathtrak_response._convertir_captura_pathtrak(captura)


def _to_int(value, default=0):
    return _hfc_analysis._to_int(value, default)


def _is_casa_vendor(info):
    return _hfc_analysis._is_casa_vendor(info)


def debe_reforzar_con_hfc_real(info: Dict[str, Any]) -> bool:
    return _hfc_analysis.debe_reforzar_con_hfc_real(info)


def compactar_hfc_real(resultado: Dict[str, Any]) -> Dict[str, Any]:
    return _hfc_analysis.compactar_hfc_real(resultado)


def conclusion_desde_hfc_real(nodo: str, hfc: Dict[str, Any]) -> Dict[str, str]:
    return _hfc_analysis.conclusion_desde_hfc_real(nodo, hfc)


def info_desde_hfc_real(info_base: Dict[str, Any], hfc: Dict[str, Any]) -> Dict[str, Any]:
    return _hfc_analysis.info_desde_hfc_real(info_base, hfc)


def _sync_hfc_diagnostic() -> None:
    _hfc_diagnostic.configure(
        motor=ejecutar_motor_noc_directo,
        extraer_valor=extraer_valor_respuesta,
        hfc_execute=ejecutar_hfc_real_para_nodo,
        capture_factory=get_pathtrak_capture_func,
        convert_capture=_convertir_captura_pathtrak,
        extraer_diagnostico=extraer_diagnostico_respuesta,
        clasificar=clasificar_conclusion_profunda,
        reforzar=debe_reforzar_con_hfc_real,
        compactar=compactar_hfc_real,
        info_hfc=info_desde_hfc_real,
        conclusion_hfc=conclusion_desde_hfc_real,
    )

def hacer_diagnostico_profundo_nodos(nodos: List[str]) -> Dict[str, Any]:
    _sync_hfc_diagnostic()
    return _hfc_diagnostic.hacer_diagnostico_profundo_nodos(nodos)

def normalizar_txt(txt: str) -> str:
    return str(txt or "").strip().upper()


def es_solicitud_todos_nodos(*args, **kwargs):
    _sync_noc_core()
    return _noc_core.es_solicitud_todos_nodos(*args, **kwargs)


def extraer_cmts_de_mensaje(*args, **kwargs):
    _sync_noc_core()
    return _noc_core.extraer_cmts_de_mensaje(*args, **kwargs)


def recordar_cmts_desde_respuesta(*args, **kwargs):
    _sync_noc_core()
    return _noc_core.recordar_cmts_desde_respuesta(*args, **kwargs)


def listar_todos_nodos_cmts(*args, **kwargs):
    _sync_noc_core()
    return _noc_core.listar_todos_nodos_cmts(*args, **kwargs)


def _sync_responder_pipeline() -> None:
    _responder_pipeline.configure(globals())

def _responder_chat_base(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._responder_chat_base(*args, **kwargs)


# =============================================================================
# PATCH_DECO_ESTADO_NODO_AUTO_HFC_REAL_FINAL
# Caso:
#   "estado nodo 6601" usa el motor base del chatbot.
#   En CASA segmentado puede devolver 0 módems aunque el diagnóstico real sí funcione.
#
# Solución:
#   Si "estado nodo X" devuelve 0 + "No se pudo validar" + vendor CASA,
#   ejecutar automáticamente "diagnóstico real nodo X".
# =============================================================================

import re as _estado_nodo_fallback_re


def _estado_nodo_fallback_norm(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._estado_nodo_fallback_norm(*args, **kwargs)


def _estado_nodo_fallback_extraer_nodo(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._estado_nodo_fallback_extraer_nodo(*args, **kwargs)


def _estado_nodo_fallback_es_respuesta_fallida(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._estado_nodo_fallback_es_respuesta_fallida(*args, **kwargs)


# Guardamos la función original que ya existe arriba en este archivo.


def _responder_chat_estado_fallback(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._responder_chat_estado_fallback(*args, **kwargs)



# =============================================================================
# PATCH_DECO_HFC_REAL_RESPUESTA_COMPACTA_FINAL
# Objetivo:
#   Cuando el fallback "estado nodo X" ejecute diagnóstico real HFC,
#   no devolver el JSON gigante al frontend.
#   Devuelve una respuesta compacta y operativa para el chatbot.
# =============================================================================

def _deco_compacto_get(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_compacto_get(*args, **kwargs)


def _deco_compacto_counts(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_compacto_counts(*args, **kwargs)


def _deco_formatear_hfc_real_compacto(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_formatear_hfc_real_compacto(*args, **kwargs)




def _responder_chat_hfc_compacto(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._responder_chat_hfc_compacto(*args, **kwargs)



# =============================================================================
# PATCH_DECO_ALIAS_PATHTRAK_NODO_FINAL
# Objetivo:
#   Soportar frases naturales como:
#     - pathtrak del nodo 6601
#     - pathtrak nodo 6601
#     - validar pathtrak nodo 6601
#
#   Estas frases deben ejecutar el flujo correcto:
#     diagnóstico real nodo 6601 con pathtrak
# =============================================================================

import re as _deco_alias_pathtrak_re


def _deco_alias_pathtrak_norm(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_alias_pathtrak_norm(*args, **kwargs)


def _deco_alias_pathtrak_extraer_nodo(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_alias_pathtrak_extraer_nodo(*args, **kwargs)




def _responder_chat_pathtrak_presentacion(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._responder_chat_pathtrak_presentacion(*args, **kwargs)



# =============================================================================
# PATCH_DECO_ALIAS_PATHTRAK_META_FINAL
# Corrige metadata cuando el usuario usa:
#   pathtrak del nodo X
# =============================================================================






# =============================================================================
# PATCH_DECO_PATHTRAK_TEXTO_FRONT_FINAL
# Inserta una línea Tipo en la respuesta compacta cuando el comando viene de:
#   pathtrak del nodo X
# Esto permite que el frontend pinte "Diagnóstico HFC real + PathTrak".
# =============================================================================






# =============================================================================
# PATCH_DECO_PATHTRAK_CAPTURA_REAL_FINAL
# Corrige el flujo:
#   pathtrak del nodo X
#   diagnostico real nodo X con pathtrak
#
# Ahora no solo etiqueta "PathTrak"; también ejecuta captura QoE real y adjunta URL.
# =============================================================================

import time as _deco_pathtrak_time
import re as _deco_pathtrak_cap_re
from pathlib import Path as _deco_pathtrak_Path
from urllib.parse import quote as _deco_pathtrak_quote
from concurrent.futures import ThreadPoolExecutor as _deco_pathtrak_ThreadPoolExecutor


def _deco_pathtrak_cap_norm(txt: str) -> str:
    return _pathtrak_capture._deco_pathtrak_cap_norm(txt)


def _deco_pathtrak_cap_extraer_nodo(mensaje: str):
    return _pathtrak_capture._deco_pathtrak_cap_extraer_nodo(mensaje)


def _deco_pathtrak_buscar_imagen(obj):
    return _pathtrak_capture._deco_pathtrak_buscar_imagen(obj)




def _deco_pathtrak_capturar_qoe(nodo: str):
    return _pathtrak_capture._deco_pathtrak_capturar_qoe(nodo)




def _sync_pathtrak_parallel(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._sync_pathtrak_parallel(*args, **kwargs)

def _responder_chat_pathtrak_paralelo(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._responder_chat_pathtrak_paralelo(*args, **kwargs)



# =============================================================================
# PATCH_DECO_PATHTRAK_NO_DEBUG_AS_OK_FINAL
# Motivo:
#   Si PathTrak retorna ok=False, no se debe tomar "debug_..._error.png"
#   como captura QoE generada. Debe quedar como error/debug.
# =============================================================================

def _deco_pathtrak_url_from_file(imagen):
    return _pathtrak_capture._deco_pathtrak_url_from_file(imagen)


def _deco_pathtrak_buscar_debug(obj):
    return _pathtrak_capture._deco_pathtrak_buscar_debug(obj)


def _deco_pathtrak_normalizar_captura(resultado):
    return _pathtrak_capture._deco_pathtrak_normalizar_captura(resultado)



# PATCH_DECO_AUTH_CMTS_FRIENDLY
# Convierte errores técnicos de Paramiko/SSH por autenticación en una respuesta operativa clara para el chat.
def _deco_es_error_auth_cmts(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_es_error_auth_cmts(*args, **kwargs)


def _deco_extraer_nodo_desde_mensaje(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_extraer_nodo_desde_mensaje(*args, **kwargs)


def _deco_normalizar_error_auth_cmts(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline._deco_normalizar_error_auth_cmts(*args, **kwargs)


# Envolvemos responder_chat sin tocar el flujo interno del motor.
# Así cualquier respuesta con AuthenticationException sale clara en el chat.




# =============================================================================
# INTEGRACIÓN MAXIMO - EVIDENCIAS POR OT
# Cada consulta abre y cierra su propio navegador Playwright.
# =============================================================================


def responder_chat(*args, **kwargs):
    _sync_responder_pipeline()
    return _responder_pipeline.responder_chat(*args, **kwargs)
