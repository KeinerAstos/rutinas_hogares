"""
Runtime aislado de PathTrak para DECO.

Responsabilidades:
- Ruta de screenshots PathTrak.
- Carga diferida de capturar_spectrum.
- Cache de la funcion cargada.
- Estado del ultimo error de carga.

No contiene reglas de negocio HFC ni responde mensajes del chat.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.paths import PATHTRAK_TEMP_SCREENSHOT_DIR


PATHTRAK_SCREENSHOT_DIR = PATHTRAK_TEMP_SCREENSHOT_DIR.resolve()

_capture_func: Optional[Callable[..., Any]] = None
_capture_error: Optional[str] = None


def get_capture_func():
    global _capture_func, _capture_error

    if _capture_func is not None:
        return _capture_func

    try:
        from app.services.pathtrak.pathtrak_spectrum import capturar_spectrum

        _capture_func = capturar_spectrum
        _capture_error = None
        return _capture_func

    except Exception:
        _capture_error = traceback.format_exc()
        _capture_func = None
        return None


def get_capture_error() -> Optional[str]:
    return _capture_error


def health() -> dict[str, Any]:
    capture = get_capture_func()

    return {
        "loaded": capture is not None,
        "error": _capture_error,
        "screenshot_dir": str(PATHTRAK_SCREENSHOT_DIR),
    }