from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

# Ruta de esta API
ROOT_DIR = Path(__file__).resolve().parent
LOCAL_ENV = ROOT_DIR / ".env"


def _load_environment() -> None:
    """
    Carga exclusivamente el .env local de api_redes_neutras.
    No depende del .env general de ATLAS.
    """

    try:
        from dotenv import load_dotenv

        if LOCAL_ENV.exists():
            load_dotenv(
                dotenv_path=LOCAL_ENV,
                override=False,
            )

        return

    except Exception:
        pass

    # Fallback simple si python-dotenv no estuviera disponible.
    if not LOCAL_ENV.exists():
        return

    try:
        for raw_line in LOCAL_ENV.read_text(
            encoding="utf-8-sig",
            errors="replace",
        ).splitlines():

            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            if not key:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            os.environ.setdefault(
                key,
                value,
            )

    except Exception:
        pass


# IMPORTANTE:
# Cargar .env ANTES de importar redes_neutras.
_load_environment()


from redes_neutras import (
    consultar_redes_neutras_helix,
    helix_health,
)

logger = logging.getLogger(__name__)

SCREENSHOT_ROOT = ROOT_DIR / "screenshots"

SCREENSHOT_TTL_HOURS = 48
SCREENSHOT_TTL_SECONDS = SCREENSHOT_TTL_HOURS * 60 * 60

SCREENSHOT_CLEANUP_INTERVAL_SECONDS = 60 * 60

APP_VERSION = "2.0.0-min"

API_PORT = int(
    os.getenv(
        "RN_API_PORT",
        "8030",
    )
    or "8030"
)


def cleanup_expired_screenshots() -> dict[str, Any]:

    result: dict[str, Any] = {
        "ok": True,
        "root": str(SCREENSHOT_ROOT),
        "evaluados": 0,
        "eliminados": 0,
        "errores": [],
    }

    try:

        SCREENSHOT_ROOT.mkdir(
            parents=True,
            exist_ok=True,
        )

        now = time.time()

        for path in list(SCREENSHOT_ROOT.rglob("*")):

            if not path.is_file():
                continue

            result["evaluados"] += 1

            try:

                age_seconds = now - path.stat().st_mtime

                if age_seconds > SCREENSHOT_TTL_SECONDS:

                    path.unlink()

                    result["eliminados"] += 1

            except FileNotFoundError:
                pass

            except Exception as exc:

                result["ok"] = False

                result["errores"].append(
                    f"{path.name}:" f"{type(exc).__name__}:" f"{exc}"
                )

        directories = sorted(
            (path for path in SCREENSHOT_ROOT.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )

        for directory in directories:

            try:
                directory.rmdir()

            except OSError:
                pass

    except Exception as exc:

        result["ok"] = False

        result["errores"].append(f"{type(exc).__name__}:" f"{exc}")

    return result


def cleanup_expired_screenshots_fail_open() -> dict[str, Any]:

    try:

        return cleanup_expired_screenshots()

    except Exception as exc:

        logger.exception(
            "RN_SCREENSHOT_CLEANUP_48H_ERROR: %s",
            exc,
        )

        return {
            "ok": False,
            "root": str(SCREENSHOT_ROOT),
            "errores": [f"{type(exc).__name__}:" f"{exc}"],
        }


async def _screenshot_cleanup_worker() -> None:

    while True:

        await asyncio.sleep(SCREENSHOT_CLEANUP_INTERVAL_SECONDS)

        await asyncio.to_thread(cleanup_expired_screenshots_fail_open)


@asynccontextmanager
async def lifespan(
    _: FastAPI,
):

    await asyncio.to_thread(cleanup_expired_screenshots_fail_open)

    cleanup_task = asyncio.create_task(_screenshot_cleanup_worker())

    try:

        yield

    finally:

        cleanup_task.cancel()

        with suppress(asyncio.CancelledError):

            await cleanup_task


app = FastAPI(
    title="ATLAS - API Redes Neutras",
    description=(
        "Microservicio independiente para "
        "consultas de Redes Neutras "
        "en Helix/SmartIT."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)


class RedesNeutrasRequest(BaseModel):

    wo: str = Field(
        ...,
        min_length=3,
        description=("Orden de trabajo Helix. " "Ejemplo: WO0000005663946"),
    )


@app.get("/health")
def health() -> dict[str, Any]:

    try:

        helix = helix_health()

    except Exception as exc:

        helix = {
            "loaded": False,
            "error": (f"{type(exc).__name__}: " f"{exc}"),
        }

    return {
        "ok": True,
        "service": "api_redes_neutras",
        "version": APP_VERSION,
        "port": API_PORT,
        "root": str(ROOT_DIR),
        # Esto confirma que la API
        # encontró SU propio .env.
        "env_path": str(LOCAL_ENV),
        "env_exists": LOCAL_ENV.exists(),
        "helix": helix,
    }


@app.post("/api/redes-neutras/consultar")
def consultar_redes_neutras(
    request: RedesNeutrasRequest,
) -> dict[str, Any]:

    started = time.monotonic()

    wo = str(request.wo or "").strip().upper()

    try:

        result = consultar_redes_neutras_helix(
            wo,
            descargar=True,
            archivo="",
            todos=True,
        )

        if not isinstance(
            result,
            dict,
        ):

            result = {
                "ok": False,
                "tipo_respuesta": "helix_redes_neutras",
                "codigo": "HELIX_RESPUESTA_INVALIDA",
                "origen": "HELIX",
                "respuesta": (
                    "Helix devolvio una "
                    "respuesta no reconocida "
                    "para Redes Neutras."
                ),
                "ot": wo,
                "adjuntos": [],
                "archivos": [],
            }

        response = dict(result)

        response["_api"] = {
            "service": "api_redes_neutras",
            "version": APP_VERSION,
            "port": API_PORT,
            "duracion_api_seg": round(
                time.monotonic() - started,
                3,
            ),
        }

        return response

    except Exception as exc:

        return {
            "ok": False,
            "tipo_respuesta": "helix_redes_neutras",
            "codigo": "REDES_NEUTRAS_API_ERROR",
            "origen": "API_REDES_NEUTRAS",
            "respuesta": ("No fue posible completar " "la consulta de Redes Neutras."),
            "ot": wo,
            "adjuntos": [],
            "archivos": [],
            "error": (f"{type(exc).__name__}: " f"{exc}"),
            "_api": {
                "service": "api_redes_neutras",
                "version": APP_VERSION,
                "port": API_PORT,
                "duracion_api_seg": round(
                    time.monotonic() - started,
                    3,
                ),
            },
        }
