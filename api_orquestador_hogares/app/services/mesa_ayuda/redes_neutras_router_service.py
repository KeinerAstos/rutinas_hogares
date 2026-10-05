from __future__ import annotations

# Compatibilidad temporal.
#
# La implementación HTTP real de Redes Neutras 8022 fue movida a:
#
#     app.clients.redes_neutras
#
# Este módulo se conserva para no romper consumidores existentes
# mientras se completa la reorganización del orquestador 8011.

from app.clients.redes_neutras import (
    consultar_redes_neutras_por_wo,
)

__all__ = [
    "consultar_redes_neutras_por_wo",
]