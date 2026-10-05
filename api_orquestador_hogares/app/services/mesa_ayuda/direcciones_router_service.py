from __future__ import annotations

# Compatibilidad temporal.
#
# La implementación HTTP real de Direcciones 8021 fue movida a:
#
#     app.clients.direcciones
#
# Este módulo se conserva para no romper consumidores existentes
# mientras se completa la reorganización del orquestador 8011.

from app.clients.direcciones import (
    consultar_direcciones_por_wo,
)

__all__ = [
    "consultar_direcciones_por_wo",
]