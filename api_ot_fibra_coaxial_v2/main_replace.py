from __future__ import annotations

import os
import re
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from ot_live_session_manager import (
    SESSION_TTL_SECONDS,
    live_ot_session_manager,
)

WO_RE = re.compile(r"^WO\d{13,14}$", re.IGNORECASE)


# ATLAS_8027_OT_COMMIT_CONFIG_V1
def commit_enabled() -> bool:
    raw = str(os.getenv("OT_COMMIT_ENABLED", "true")).strip().lower()
    if raw in {"1", "true", "yes", "si", "sí", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError("OT_COMMIT_ENABLED debe ser booleano.")


def normalize_wo(value: str) -> str:
    wo = str(value or "").strip().upper()
    if not WO_RE.fullmatch(wo):
        raise HTTPException(status_code=422, detail="WO_INVALIDA")
    return wo


def normalize_tipo(value: str) -> str:
    tipo = str(value or "").strip().upper()
    if tipo == "COAXIAL":
        tipo = "COAX"
    if tipo not in {"FIBRA", "COAX"}:
        raise HTTPException(status_code=422, detail="TIPO_INVALIDO")
    return tipo


class OtRequest(BaseModel):
    wo: str
    tipo: str
    expected_incident: str = ""
    helix_username: str = ""
    helix_password: str = ""
    session_id: str = ""


class CancelRequest(BaseModel):
    wo: str = ""
    tipo: str = ""
    session_id: str = ""


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await live_ot_session_manager.shutdown()


app = FastAPI(
    title="ATLAS - API OT Fibra / Coaxial",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "api_ot_fibra_coaxial",
        "version": "2.0.0",
        "port": 8027,
        "commit_enabled": commit_enabled(),
        "confirmation_ttl_seconds": SESSION_TTL_SECONDS,
        "live_session": live_ot_session_manager.snapshot(),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    required = {
        "main": BASE_DIR / "main.py",
        "session_manager": BASE_DIR / "ot_live_session_manager.py",
        "smartit": BASE_DIR / "smartit_scraper.py",
        "helix_runner": BASE_DIR / "helix_runner.py",
        "env": BASE_DIR / ".env",
        "requirements": BASE_DIR / "requirements.txt",
    }
    files = {name: path.exists() for name, path in required.items()}
    return {
        "ok": all(files.values()),
        "service": "api_ot_fibra_coaxial",
        "version": "2.0.0",
        "port": 8027,
        "commit_enabled": commit_enabled(),
        "confirmation_ttl_seconds": SESSION_TTL_SECONDS,
        "live_session": live_ot_session_manager.snapshot(),
        "files": files,
    }


@app.post("/dry-run")
async def dry_run(req: OtRequest) -> dict[str, Any]:
    wo = normalize_wo(req.wo)
    tipo = normalize_tipo(req.tipo)
    result = await live_ot_session_manager.prepare(
        wo,
        tipo,
        helix_username=str(req.helix_username or "").strip(),
        helix_password=str(req.helix_password or ""),
    )
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="DRYRUN_RESPUESTA_INVALIDA")
    return result


@app.post("/commit")
async def commit(req: OtRequest) -> dict[str, Any]:
    wo = normalize_wo(req.wo)
    tipo = normalize_tipo(req.tipo)
    if not commit_enabled():
        raise HTTPException(status_code=403, detail="OT_COMMIT_DISABLED")

    # Compatibilidad: session_id es opcional. Como solo existe una sesion viva,
    # los consumidores actuales pueden seguir enviando WO/tipo/INC sin cambios.
    result = await live_ot_session_manager.commit(
        wo,
        tipo,
        expected_incident=str(req.expected_incident or "").strip().upper(),
        session_id=str(req.session_id or "").strip(),
    )
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="COMMIT_RESPUESTA_INVALIDA")
    return result


@app.post("/cancel")
async def cancel(req: CancelRequest) -> dict[str, Any]:
    tipo = ""
    if str(req.tipo or "").strip():
        tipo = normalize_tipo(req.tipo)
    wo = str(req.wo or "").strip().upper()
    if wo and not WO_RE.fullmatch(wo):
        raise HTTPException(status_code=422, detail="WO_INVALIDA")
    return await live_ot_session_manager.cancel(
        wo=wo,
        tipo=tipo,
        session_id=str(req.session_id or "").strip(),
    )
