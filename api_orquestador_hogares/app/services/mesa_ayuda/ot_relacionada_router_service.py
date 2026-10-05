from __future__ import annotations

# Compatibilidad temporal.
#
# La implementación HTTP real de OT Relacionada 8027 fue movida a:
#
#     app.clients.ot_relacionada
#
# Este módulo se conserva para no romper consumidores existentes
# mientras se completa la reorganización del orquestador 8011.

from app.clients.ot_relacionada import (
    cancelar_ot_relacionada,
    confirmar_ot_relacionada,
    consultar_ot_relacionada_dryrun,
    creacion_ot_habilitada,
    estado_ot_relacionada_api,
)

__all__ = [
    "consultar_ot_relacionada_dryrun",
    "confirmar_ot_relacionada",
    "cancelar_ot_relacionada",
    "estado_ot_relacionada_api",
    "creacion_ot_habilitada",
]