from fastapi import APIRouter

from app.core.paths import DATA_DIR, INPUT_DIR, RUNTIME_ROOT
from app.core.settings import settings


router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "runtime_root": str(RUNTIME_ROOT),
        "data_ready": DATA_DIR.is_dir(),
        "input_ready": INPUT_DIR.is_dir(),
        "max_parallel_jobs": settings.max_parallel_jobs,
    }
