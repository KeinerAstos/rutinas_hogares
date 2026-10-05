from fastapi import APIRouter

from app.services.helix.wo_summary_service import (
    consultar_resumen_ot_helix,
)


router = APIRouter(
    tags=["HELIX WO SUMMARY"],
)


@router.get(
    "/api/deco/helix/ot/{ot}/resumen"
)
def consultar_resumen_wo(
    ot: str,
):
    return consultar_resumen_ot_helix(
        ot
    )
