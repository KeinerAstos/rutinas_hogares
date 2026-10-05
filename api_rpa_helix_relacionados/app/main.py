from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from app.config.oracle_settings import OracleSettings


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"

# CRITICO:
# incident_related_batch_service obtiene SMARTIT_URL durante import.
# Por eso el .env debe cargarse antes de importar el router.
load_dotenv(ENV_FILE, override=False)

from app.api.helix_relacionados import router as helix_relacionados_router
# ATLAS_HELIX_WO_SUMMARY_ROUTER_8023_V1
from app.api.helix_wo_summary import (
    router as helix_wo_summary_router,
)
from app.api.helix_note_publish import router as helix_note_publish_router


app = FastAPI(
    title="ATLAS API RPA Helix Relacionados",
    version="1.0.0",
    description=(
        "Microservicio aislado para el RPA Helix Relacionados "
        "extraído del backend principal de ATLAS."
    ),
)


app.include_router(helix_relacionados_router)
app.include_router(helix_wo_summary_router)


@app.get("/health")
def health() -> dict:
    required = (
        "SMARTIT_URL",
        "SMARTIT_USER",
        "SMARTIT_PASSWORD",
    )

    configured = {
        name: bool(str(os.getenv(name) or "").strip())
        for name in required
    }
    oracle = OracleSettings.from_env()

    return {
        "ok": all(configured.values()),
        "service": "api_rpa_helix_relacionados",
        "version": "1.0.0",
        "config": configured,
        "oracle": {
            "configured": oracle.configured,
            "host": bool(oracle.host),
            "service": bool(oracle.service),
            "user": bool(oracle.user),
            "password": bool(oracle.password),
        },
        "oracle_ok": oracle.configured,
        "controls": {
            "pausar": False,
            "reanudar": False,
            "detener": False,
            "mode": "paridad_8011_failsafe",
        },
    }

# ATLAS_HELIX_NOTE_PUBLISH_ROUTER_8023_V1
app.include_router(helix_note_publish_router)
