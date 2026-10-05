from fastapi import FastAPI
from app.api.routes.dispositivos import router as dispositivos_router

app = FastAPI(
    title="ATLAS - API Dispositivos VIPS",
    version="1.0",
)

app.include_router(dispositivos_router, prefix="/api/vips")

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "api_dispositivos_vips",
        "port": 8029,
    }