from __future__ import annotations

# Compatibilidad temporal.
#
# La implementación HTTP real de PathTrak 8024 para Mesa de Ayuda
# fue movida a:
#
#     app.clients.pathtrak
#
# El runtime PathTrak de DECO no forma parte de este movimiento.

from app.clients.pathtrak import (
    capturar_pathtrak,
)

__all__ = [
    "capturar_pathtrak",
]