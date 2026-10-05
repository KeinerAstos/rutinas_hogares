from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from playwright.async_api import (
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

ROOT_DIR = Path(__file__).resolve().parent
BASE_DIR = ROOT_DIR
LOG_DIR = Path(r"C:\xampp\htdocs\rutinas_hogares\api_redes_neutras_v2\logs\smartit")
SCREENSHOT_DIR = ROOT_DIR / "screenshots"
DEFAULT_DOWNLOAD_DIR = ROOT_DIR / "descargas"

STATE_DIR = Path(r"C:\xampp\htdocs\rutinas_hogares\api_redes_neutras_v2\runtime\helix\data")
STATE_FILE = STATE_DIR / "credential_alert.json"
_STATE_LOCK = threading.RLock()

SEL = {
    "login_user": ["#user_login"],
    "login_password": ["#login_user_password"],
    "login_button": ["#login-jsp-btn"],
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _resolve_env_path(value: str, default: Path) -> Path:
    raw = value.strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def _resolve_optional_env_path(value: str) -> Path | None:
    raw = value.strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta configurar {name} en .env.")
    return value


def get_smartit_url() -> str:
    return _required("SMARTIT_URL")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _mask_user(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 3:
        return "*" * len(text)
    return text[:2] + ("*" * min(8, max(1, len(text) - 3))) + text[-1:]


def _default_credentials_alert() -> dict[str, Any]:
    return {"active": False, "code": "", "message": "", "detected_at": "", "user": ""}


def get_credentials_alert() -> dict[str, Any]:
    with _STATE_LOCK:
        if not STATE_FILE.exists():
            return _default_credentials_alert()
        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return _default_credentials_alert()
        result = _default_credentials_alert()
        if isinstance(payload, dict):
            result.update({
                "active": bool(payload.get("active")),
                "code": str(payload.get("code") or ""),
                "message": str(payload.get("message") or ""),
                "detected_at": str(payload.get("detected_at") or ""),
                "user": str(payload.get("user") or ""),
            })
        return result


def mark_credentials_invalid(username: str | None, detail: str = "SmartIT rechazo el usuario o la contraseña.") -> dict[str, Any]:
    payload = {
        "active": True,
        "code": "HELIX_CREDENCIALES_INVALIDAS",
        "message": "Helix rechazo las credenciales configuradas. Se requiere actualizar SMARTIT_USER / SMARTIT_PASSWORD.",
        "detected_at": _now(),
        "user": _mask_user(username),
        "detail": str(detail or ""),
    }
    with _STATE_LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(STATE_FILE)
    return get_credentials_alert()


def clear_credentials_alert() -> None:
    with _STATE_LOCK:
        try:
            STATE_FILE.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


@dataclass(frozen=True)
class Settings:
    url: str
    username: str
    password: str
    headless: bool
    timeout_ms: int
    slow_mo_ms: int
    keep_open_seconds: int
    download_timeout_ms: int
    output_dir: Path
    dashboard_csv: Path | None

    @classmethod
    def from_env(cls) -> "Settings":
        load_env(BASE_DIR / ".env")
        username = os.getenv("SMARTIT_USER", "").strip()
        password = os.getenv("SMARTIT_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("Faltan SMARTIT_USER o SMARTIT_PASSWORD en .env")
        return cls(
            url=get_smartit_url(),
            username=username,
            password=password,
            headless=as_bool(os.getenv("SMARTIT_HEADLESS", "false")),
            timeout_ms=int(os.getenv("SMARTIT_TIMEOUT_MS", "240000")),
            slow_mo_ms=int(os.getenv("SMARTIT_SLOW_MO_MS", "100")),
            keep_open_seconds=int(os.getenv("SMARTIT_KEEP_OPEN_SECONDS", "20")),
            download_timeout_ms=int(os.getenv("SMARTIT_DOWNLOAD_TIMEOUT_MS", "1800000")),
            output_dir=_resolve_env_path(os.getenv("SMARTIT_OUTPUT_DIR", "descargas"), DEFAULT_DOWNLOAD_DIR),
            dashboard_csv=_resolve_optional_env_path(os.getenv("SMARTIT_DASHBOARD_CSV", "")),
        )


async def take_screenshot(page: Page, logger: logging.Logger, name: str) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        logger.info("Captura: %s", path)
    except Exception as exc:
        logger.warning("No se pudo guardar captura: %s", exc)


def frame_description(frame: Frame) -> str:
    name = frame.name or "sin_nombre"
    return f"name={name!r} url={frame.url!r}"


async def _log_frames(page: Page, logger: logging.Logger, reason: str) -> None:
    logger.info("Frames detectados (%s): %s", reason, len(page.frames))
    for index, frame in enumerate(page.frames):
        logger.info("  frame[%s] %s", index, frame_description(frame))


async def locator_visible(locator: Locator) -> bool:
    try:
        return await locator.is_visible()
    except Exception:
        return False


async def _first_visible_in_frame(frame: Frame, selectors: Iterable[str]) -> tuple[Locator, str] | None:
    for selector in selectors:
        try:
            locator = frame.locator(selector)
            count = await locator.count()
            for index in range(min(count, 20)):
                candidate = locator.nth(index)
                if await locator_visible(candidate):
                    return candidate, selector
        except Exception:
            continue
    return None


async def find_visible_across_frames(
    page: Page,
    selectors: Iterable[str],
    timeout_ms: int,
    logger: logging.Logger,
    description: str,
) -> tuple[Frame, Locator, str]:
    deadline = time.monotonic() + timeout_ms / 1000
    last_frame_count = -1
    while time.monotonic() < deadline:
        frames = list(page.frames)
        if len(frames) != last_frame_count:
            last_frame_count = len(frames)
            logger.info("Buscando %s en %s frame(s).", description, last_frame_count)
        for frame in frames:
            found = await _first_visible_in_frame(frame, selectors)
            if found:
                locator, selector = found
                logger.info("%s encontrado con %s dentro de %s", description, selector, frame_description(frame))
                return frame, locator, selector
        await page.wait_for_timeout(300)
    await _log_frames(page, logger, f"timeout buscando {description}")
    raise PlaywrightTimeoutError(
        f"No se encontró {description} en la página ni en sus frames después de {timeout_ms} ms. Selectores: {list(selectors)}"
    )


async def find_optional_visible_across_frames(
    page: Page,
    selectors: Iterable[str],
    timeout_ms: int = 1500,
) -> tuple[Frame, Locator, str] | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in list(page.frames):
            found = await _first_visible_in_frame(frame, selectors)
            if found:
                locator, selector = found
                return frame, locator, selector
        await page.wait_for_timeout(200)
    return None


async def _visible_elements_across_frames(
    page: Page, selectors: Iterable[str]
) -> list[tuple[Frame, Locator, str]]:
    results: list[tuple[Frame, Locator, str]] = []
    seen: set[tuple[str, str, int]] = set()
    for frame in list(page.frames):
        for selector in selectors:
            try:
                locator = frame.locator(selector)
                count = await locator.count()
                for index in range(min(count, 100)):
                    candidate = locator.nth(index)
                    if not await locator_visible(candidate):
                        continue
                    key = (frame.url, selector, index)
                    if key not in seen:
                        seen.add(key)
                        results.append((frame, candidate, selector))
            except Exception:
                continue
    return results


async def any_visible_across_frames(page: Page, selectors: Iterable[str]) -> bool:
    return bool(await _visible_elements_across_frames(page, selectors))


async def iniciar_sesion(page: Page, cfg: Settings, logger: logging.Logger) -> None:
    logger.info("Abriendo SmartIT: %s", cfg.url)
    await page.goto(cfg.url, wait_until="domcontentloaded", timeout=cfg.timeout_ms)

    login_match = await find_optional_visible_across_frames(page, SEL["login_user"], timeout_ms=4000)
    if not login_match:
        logger.info("No hay formulario de login; se reutiliza la sesión del perfil.")
        return

    login_frame, user, _ = login_match
    password = login_frame.locator(SEL["login_password"][0])
    button = login_frame.locator(SEL["login_button"][0])
    await user.fill(cfg.username)
    await password.fill(cfg.password)
    await button.click()

    rejection_needles = (
        "incorrect username or password",
        "invalid username or password",
        "usuario o contraseña incorrect",
        "usuario o contrasena incorrect",
    )

    async def credential_rejection() -> str:
        for frame in list(page.frames):
            try:
                text = await frame.locator("body").inner_text(timeout=1200)
            except Exception:
                continue
            normalized = re.sub(r"\s+", " ", text or "").strip().casefold()
            for needle in rejection_needles:
                if needle.casefold() in normalized:
                    return needle
        return ""

    deadline = time.monotonic() + min(cfg.timeout_ms, 60000) / 1000
    while time.monotonic() < deadline:
        rejection = await credential_rejection()
        if rejection:
            mark_credentials_invalid(cfg.username, detail=rejection)
            await take_screenshot(page, logger, "error_login_credenciales")
            logger.error("HELIX_CREDENCIALES_INVALIDAS: SmartIT rechazo el usuario/password.")
            raise RuntimeError(
                "HELIX_CREDENCIALES_INVALIDAS: SmartIT rechazo el usuario o la contraseña configurados."
            )
        try:
            if not await user.is_visible():
                clear_credentials_alert()
                logger.info("Login completado.")
                return
        except Exception:
            clear_credentials_alert()
            logger.info("Login completado.")
            return
        await page.wait_for_timeout(400)

    await take_screenshot(page, logger, "error_login")
    raise RuntimeError(
        "HELIX_LOGIN_NO_CONFIRMADO: El login no terminó. Revisa VPN, disponibilidad de SmartIT o cambios de interfaz."
    )
