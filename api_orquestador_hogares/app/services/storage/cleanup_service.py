from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from app.core.paths import TEMP_SCREENSHOTS_DIR


logger = logging.getLogger(__name__)

# ATLAS_STORAGE_SCREENSHOT_CLEANUP_48H_V1
SCREENSHOT_TTL_HOURS = 48
SCREENSHOT_TTL_SECONDS = SCREENSHOT_TTL_HOURS * 60 * 60

# El worker se ejecuta una vez por hora.
SCREENSHOT_CLEANUP_INTERVAL_SECONDS = 60 * 60


def cleanup_expired_screenshots(
    *,
    root: Path | None = None,
    ttl_seconds: int = SCREENSHOT_TTL_SECONDS,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """
    Elimina únicamente archivos temporales de screenshots con antigüedad
    superior al TTL.

    Nunca toca:
    - data
    - logs
    - maintenance/backups
    - screenshots legacy de backend\\screenshots
    - archivos de otros microservicios
    """

    target_root = Path(
        root if root is not None else TEMP_SCREENSHOTS_DIR
    ).resolve()

    now = float(
        time.time()
        if now_ts is None
        else now_ts
    )

    result: dict[str, Any] = {
        "ok": True,
        "root": str(target_root),
        "ttl_hours": round(float(ttl_seconds) / 3600, 2),
        "evaluados": 0,
        "eliminados": 0,
        "bytes_eliminados": 0,
        "errores": [],
    }

    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        result["ok"] = False
        result["errores"].append(
            f"mkdir:{type(exc).__name__}:{exc}"
        )
        return result

    try:
        files = [
            path
            for path in target_root.rglob("*")
            if path.is_file()
        ]
    except Exception as exc:
        result["ok"] = False
        result["errores"].append(
            f"scan:{type(exc).__name__}:{exc}"
        )
        return result

    for path in files:
        result["evaluados"] += 1

        try:
            stat = path.stat()
            age_seconds = now - float(stat.st_mtime)

            if age_seconds <= ttl_seconds:
                continue

            size = int(stat.st_size)

            path.unlink()

            result["eliminados"] += 1
            result["bytes_eliminados"] += size

        except FileNotFoundError:
            # Otro flujo ya pudo eliminarlo.
            continue

        except Exception as exc:
            result["ok"] = False
            result["errores"].append(
                f"{path.name}:{type(exc).__name__}:{exc}"
            )

    # Solo intenta quitar carpetas vacías debajo del root.
    try:
        directories = sorted(
            (
                path
                for path in target_root.rglob("*")
                if path.is_dir()
            ),
            key=lambda item: len(item.parts),
            reverse=True,
        )

        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass

    except Exception:
        # La limpieza de directorios vacíos no es crítica.
        pass

    return result


def cleanup_expired_screenshots_fail_open() -> dict[str, Any]:
    """
    Wrapper utilizado por el lifespan.

    Nunca propaga excepciones hacia FastAPI.
    """
    try:
        result = cleanup_expired_screenshots()

        logger.info(
            "SCREENSHOT_CLEANUP_48H "
            "ok=%s evaluados=%s eliminados=%s bytes=%s errores=%s",
            result.get("ok"),
            result.get("evaluados"),
            result.get("eliminados"),
            result.get("bytes_eliminados"),
            len(result.get("errores") or []),
        )

        return result

    except Exception as exc:
        logger.exception(
            "SCREENSHOT_CLEANUP_48H_ERROR: %s",
            exc,
        )

        return {
            "ok": False,
            "root": str(TEMP_SCREENSHOTS_DIR),
            "ttl_hours": SCREENSHOT_TTL_HOURS,
            "evaluados": 0,
            "eliminados": 0,
            "bytes_eliminados": 0,
            "errores": [
                f"{type(exc).__name__}:{exc}"
            ],
        }
