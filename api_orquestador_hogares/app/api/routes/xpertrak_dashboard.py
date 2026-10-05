from fastapi import APIRouter

from app.services import xpertrak_dashboard_export_service as service


router = APIRouter(
    prefix="/api/xpertrak/dashboard",
    tags=["XPERTrak Dashboard"],
)


@router.get("/health")
def xpertrak_dashboard_health():
    return service.health()


@router.post("/exportar-salud-diaria")
def xpertrak_exportar_salud_diaria():
    return service.exportar_salud_diaria_ambas()
