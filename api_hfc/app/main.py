from fastapi import FastAPI

from app.api.routes.hfc_quick import router as quick_router
from app.api.routes.hfc_analytics import router as analytics_router
from app.api.routes.hfc_rf import router as rf_router

app = FastAPI(
    title="ATLAS - API HFC",
    version="1.0",
)

app.include_router(quick_router)
app.include_router(analytics_router)
app.include_router(rf_router)

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "api_hfc",
        "port": 8030,
    }