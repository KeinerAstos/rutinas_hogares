import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import deco, dispositivos, health, hfc_analytics, maximo, hfc_rf, mesa_ayuda, smcc_tracking
from app.api.routes import queue_status  # ATLAS_PRIORITY_QUEUE_V1_1
from app.api.routes import xpertrak_dashboard  # XPERTRAK_DASHBOARD_EXPORT_V1_1
from app.api.routes import bitacora_helix_gateway  # ATLAS_BITACORA_HELIX_GATEWAY_V1
from app.core.paths import ensure_runtime_dirs
from app.services.storage.cleanup_service import (
    SCREENSHOT_CLEANUP_INTERVAL_SECONDS,
    cleanup_expired_screenshots_fail_open,
)
from app.core.settings import settings


# ATLAS_STORAGE_SCREENSHOT_CLEANUP_48H_V1
async def _screenshot_cleanup_worker() -> None:
    while True:
        await asyncio.sleep(
            SCREENSHOT_CLEANUP_INTERVAL_SECONDS
        )
        await asyncio.to_thread(
            cleanup_expired_screenshots_fail_open
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_runtime_dirs()

    # Limpieza inicial, fail-open.
    await asyncio.to_thread(
        cleanup_expired_screenshots_fail_open
    )

    cleanup_task = asyncio.create_task(
        _screenshot_cleanup_worker()
    )

    try:
        yield
    finally:
        cleanup_task.cancel()

        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Servicios operativos locales de Hogares para CentralNOC.",
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api", tags=["Salud"])
app.include_router(maximo.router)
app.include_router(dispositivos.router, prefix="/api/vips", tags=["Dispositivos"])
app.include_router(deco.router)
app.include_router(mesa_ayuda.router)
app.include_router(smcc_tracking.router)
app.include_router(queue_status.router)  # ATLAS_PRIORITY_QUEUE_V1_1
app.include_router(hfc_analytics.router)
app.include_router(hfc_rf.router)
app.include_router(xpertrak_dashboard.router)  # XPERTRAK_DASHBOARD_EXPORT_V1_1
app.include_router(bitacora_helix_gateway.router)  # ATLAS_BITACORA_HELIX_GATEWAY_V1

# ATLAS_HELIX_CONTROL_MAIN_V14
@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "app": settings.app_name,
        "version": settings.app_version,
        "health": "/api/health",
        "docs": "/docs",
    }

# BEGIN BACKEND_HOGARES_UTF8_JSON_CHARSET
# Fuerza UTF-8 explicito para clientes legacy como Windows PowerShell 5.1.
@app.middleware("http")
async def _ensure_utf8_json_charset(request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.lower().startswith("application/json"):
        response.headers["content-type"] = "application/json; charset=utf-8"
    return response
# END BACKEND_HOGARES_UTF8_JSON_CHARSET
# ATLAS_HELIX_RELACIONADOS_EXTERNAL_8023_ONLY_V2

