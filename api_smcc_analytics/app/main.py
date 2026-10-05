from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.analytics import router as analytics_router
from app.services.analytics_service import SmccAnalyticsService


app = FastAPI(
    title="ATLAS SMCC Analytics API",
    version="1.1.0",
)


@app.get("/health")
def health():
    return SmccAnalyticsService().health()


app.include_router(analytics_router)