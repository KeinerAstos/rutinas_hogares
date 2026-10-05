from __future__ import annotations

# Compatibilidad temporal.
#
# La implementación HTTP real de Helix Relacionados 8023 fue movida a:
#
#     app.clients.helix_relacionados
#
# Este módulo se conserva para no romper consumidores existentes
# mientras se completa la reorganización del orquestador 8011.

from app.clients.helix_relacionados import (
    consultar_incidente,
    consultar_resumen_ot,
    iniciar_job,
    obtener_job,
)

__all__ = [
    "iniciar_job",
    "obtener_job",
    "consultar_incidente",
    "consultar_resumen_ot",
]