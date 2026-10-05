from __future__ import annotations

# Compatibilidad temporal.
#
# La implementación HTTP real de Confirmaciones 8026 fue movida a:
#
#     app.clients.confirmaciones
#
# Este módulo se conserva para no romper consumidores existentes
# mientras se completa la reorganización del orquestador 8011.

from app.clients.confirmaciones import (
    obtener_confirmacion,
)

__all__ = [
    "obtener_confirmacion",
]