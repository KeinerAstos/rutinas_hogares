from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from playwright.async_api import (
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

try:
    from app.services.helix.credential_state import (
        clear_credentials_alert,
        mark_credentials_invalid,
    )
except ImportError:
    from credential_state import (
        clear_credentials_alert,
        mark_credentials_invalid,
    )
BASE_DIR = Path(__file__).resolve().parents[3]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from app.core.paths import HELIX_TEMP_SCREENSHOT_DIR
except ImportError:
    HELIX_TEMP_SCREENSHOT_DIR = (
        BASE_DIR / "storage" / "temp" / "screenshots" / "helix"
    )
LOG_DIR = Path(r"C:\xampp\htdocs\rutinas_hogares\api_direccion_clientes\logs\smartit")
SCREENSHOT_DIR = HELIX_TEMP_SCREENSHOT_DIR
PROFILE_DIR = BASE_DIR / "perfil_navegador"
DEFAULT_DOWNLOAD_DIR = BASE_DIR / "descargas"
DEBUG_DIR = BASE_DIR / "debug"

DEFAULT_URL = ""

SEL = {
    "login_user": ["#user_login"],
    "login_password": ["#login_user_password"],
    "login_button": ["#login-jsp-btn"],
    "console": [
        'a[ux-id="navitem-dropdown"].navigation-bar__item-label:has-text("Consola")',
        'a[ux-id="navitem-dropdown"]:has-text("Consola")',
    ],
    "loaders": [
        ".ng-busy-default-wrapper",
        ".loader-container",
        ".loader-section",
    ],
    "remove_filter": [
        'rx-filter-tags a[role="button"][aria-label^="Quitar "]',
        'a[role="button"][aria-label^="Quitar "]',
        'a.close[aria-label^="Quitar "]',
    ],
    "filter_tags": [
        "rx-filter-tags adapt-tag",
        ".filter-tags__tag-text",
        ".a-tag.a-tag-active",
    ],
    "empty_grid": [".empty-state__container"],
    "filter_menu": [
        '[data-testid="adapt-af-1_menu"]',
        'button.advanced-filter__dropdown-anchor:has-text("Filtrar")',
        'button[adaptdropdownanchor]:has-text("Filtrar")',
        'button:has-text("Filtrar")',
    ],
    "filter_search": [
        '[data-testid="adapt-af-1_tag-field_search_input"]',
        'input[data-testid$="tag-field_search_input"]',
        '.advanced-filter__popover-container input[type="text"]',
    ],
    "filter_apply": ['[data-testid="adapt-af-1_footer_apply"]'],
    "filter_remove_all": [
        '[data-testid="adapt-af-1_footer_remove"]',
        'button:has-text("Quitar todo")',
    ],
    "filter_clear_header": [
        'button:has-text("Borrar")',
        'a:has-text("Borrar")',
        '[role="button"]:has-text("Borrar")',
    ],
    "filter_cancel": ['[data-testid="adapt-af-1_footer_cancel"]'],
    "filter_selected_tags": [
        '.advanced-filter__expression-tag-field .a-tag',
        '.advanced-filter__expression-tag-field adapt-tag',
        '.advanced-filter__popover-header .a-tag',
        '.adapt-mt .a-tag',
    ],
    "filter_controls": [
        'adapt-filter-controls.advanced-filter__filter-controls',
        'adapt-filter-controls',
    ],
    "table_ready": [
        '.ui-table-scrollable-view',
        'table.ui-table__virtual-scroll',
        '.ui-table-tbody',
    ],
    "export_button": [
        'button[adaptdropdownanchor]:has-text("Exportar")',
        'button.btn:has-text("Exportar")',
    ],
    "export_csv": [
        'button.toolbar-export-menu-item:has-text("CSV")',
        'button.dropdown-item:has-text("CSV")',
    ],
}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


def as_bool(value: str) -> bool:
    return value.strip().lower() in {
        "1", "true", "yes", "si", "sí", "on"
    }


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


def parse_iso_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"{field_name} debe usar formato AAAA-MM-DD. "
            f"Valor recibido: {value!r}"
        ) from exc


def weekly_windows(
    start_date: date,
    end_date: date,
    days_per_window: int = 7,
) -> list[tuple[date, date]]:
    if start_date > end_date:
        raise ValueError(
            "La fecha inicial no puede ser posterior a la fecha final."
        )
    if days_per_window < 1:
        raise ValueError("days_per_window debe ser mayor o igual a 1.")

    windows: list[tuple[date, date]] = []
    current = start_date

    while current <= end_date:
        current_end = min(
            current + timedelta(days=days_per_window - 1),
            end_date,
        )
        windows.append((current, current_end))
        current = current_end + timedelta(days=1)

    return windows


def date_slug(value: date) -> str:
    return value.strftime("%Y%m%d")


def format_date_es(value: date) -> str:
    return value.strftime("%d/%m/%Y")


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
            raise RuntimeError(
                "Faltan SMARTIT_USER o SMARTIT_PASSWORD en .env"
            )
        from app.config.endpoints_settings import get_smartit_url

        return cls(
            url=get_smartit_url(),
            username=username,
            password=password,
            headless=as_bool(
                os.getenv("SMARTIT_HEADLESS", "false")
            ),
            timeout_ms=int(
                os.getenv("SMARTIT_TIMEOUT_MS", "240000")
            ),
            slow_mo_ms=int(
                os.getenv("SMARTIT_SLOW_MO_MS", "100")
            ),
            keep_open_seconds=int(
                os.getenv("SMARTIT_KEEP_OPEN_SECONDS", "20")
            ),
            download_timeout_ms=int(
                os.getenv("SMARTIT_DOWNLOAD_TIMEOUT_MS", "1800000")
            ),
            output_dir=_resolve_env_path(
                os.getenv("SMARTIT_OUTPUT_DIR", "descargas"),
                DEFAULT_DOWNLOAD_DIR,
            ),
            dashboard_csv=_resolve_optional_env_path(
                os.getenv("SMARTIT_DASHBOARD_CSV", "")
            ),
        )


def setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"smartit_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger = logging.getLogger("smartit")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handlers = (
        logging.FileHandler(path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    )
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.info("Log: %s", path)
    return logger


async def take_screenshot(
    page: Page,
    logger: logging.Logger,
    name: str,
) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / (
        f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
    )
    try:
        await page.screenshot(path=str(path), full_page=True)
        logger.info("Captura: %s", path)
    except Exception as exc:
        logger.warning("No se pudo guardar captura: %s", exc)


def frame_description(frame: Frame) -> str:
    name = frame.name or "sin_nombre"
    return f"name={name!r} url={frame.url!r}"


async def log_frames(
    page: Page,
    logger: logging.Logger,
    reason: str,
) -> None:
    logger.info(
        "Frames detectados (%s): %s",
        reason,
        len(page.frames),
    )
    for index, frame in enumerate(page.frames):
        logger.info(
            "  frame[%s] %s",
            index,
            frame_description(frame),
        )


async def locator_visible(locator: Locator) -> bool:
    try:
        return await locator.is_visible()
    except Exception:
        return False


async def first_visible_in_frame(
    frame: Frame,
    selectors: Iterable[str],
) -> tuple[Locator, str] | None:
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
            logger.info(
                "Buscando %s en %s frame(s).",
                description,
                last_frame_count,
            )

        for frame in frames:
            found = await first_visible_in_frame(
                frame,
                selectors,
            )
            if found:
                locator, selector = found
                logger.info(
                    "%s encontrado con %s dentro de %s",
                    description,
                    selector,
                    frame_description(frame),
                )
                return frame, locator, selector

        await page.wait_for_timeout(300)

    await log_frames(page, logger, f"timeout buscando {description}")
    raise PlaywrightTimeoutError(
        f"No se encontró {description} en la página ni en sus frames "
        f"después de {timeout_ms} ms. Selectores: {list(selectors)}"
    )


async def find_optional_visible_across_frames(
    page: Page,
    selectors: Iterable[str],
    timeout_ms: int = 1500,
) -> tuple[Frame, Locator, str] | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in list(page.frames):
            found = await first_visible_in_frame(frame, selectors)
            if found:
                locator, selector = found
                return frame, locator, selector
        await page.wait_for_timeout(200)
    return None


async def visible_elements_across_frames(
    page: Page,
    selectors: Iterable[str],
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


async def any_visible_across_frames(
    page: Page,
    selectors: Iterable[str],
) -> bool:
    return bool(
        await visible_elements_across_frames(page, selectors)
    )


async def esperar_carga_tabla(
    page: Page,
    logger: logging.Logger,
    timeout_ms: int,
    motivo: str,
) -> None:
    """
    Espera el loader en la página principal o en cualquiera de sus
    iframes. Exige dos comprobaciones consecutivas sin loaders visibles.
    """
    logger.info("Esperando tabla: %s", motivo)
    start = time.monotonic()
    appearance_deadline = min(
        start + 10,
        start + timeout_ms / 1000,
    )
    loader_seen = False

    while time.monotonic() < appearance_deadline:
        if await any_visible_across_frames(
            page,
            SEL["loaders"],
        ):
            loader_seen = True
            logger.info("Loader detectado dentro de SmartIT.")
            break
        await page.wait_for_timeout(250)

    if not loader_seen:
        logger.info(
            "El loader no apareció; se valida igualmente la estabilidad."
        )

    deadline = start + timeout_ms / 1000
    stable_checks = 0

    while time.monotonic() < deadline:
        busy = await any_visible_across_frames(
            page,
            SEL["loaders"],
        )
        if busy:
            stable_checks = 0
        else:
            stable_checks += 1
            if stable_checks >= 2:
                await page.wait_for_timeout(1200)
                logger.info("Tabla estable.")
                return
        await page.wait_for_timeout(500)

    raise PlaywrightTimeoutError(
        f"La tabla no terminó de cargar durante: {motivo}"
    )


async def iniciar_sesion(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> None:
    # HELIX_LOGIN_CONFIRMATION_V13_F1_8
    logger.info("Abriendo SmartIT: %s", cfg.url)

    await page.goto(
        cfg.url,
        wait_until="domcontentloaded",
        timeout=cfg.timeout_ms,
    )

    login_match = await find_optional_visible_across_frames(
        page,
        SEL["login_user"],
        timeout_ms=4000,
    )

    if not login_match:
        logger.info(
            "No hay formulario de login; se reutiliza la sesión del perfil."
        )
        return

    login_frame, user, _ = login_match

    password = login_frame.locator(
        SEL["login_password"][0]
    )

    button = login_frame.locator(
        SEL["login_button"][0]
    )

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
        """
        Busca texto de rechazo como señal auxiliar.

        Un texto por sí solo NO certifica credenciales inválidas porque
        SmartIT puede mantener contenido residual durante la navegación.
        """
        for frame in list(page.frames):
            try:
                text = await frame.locator(
                    "body"
                ).inner_text(
                    timeout=1200
                )
            except Exception:
                continue

            normalized = re.sub(
                r"\s+",
                " ",
                text or "",
            ).strip().casefold()

            for needle in rejection_needles:
                if needle.casefold() in normalized:
                    return needle

        return ""

    async def login_user_visibility() -> tuple[str, str]:
        """
        Estados:
          VISIBLE   -> formulario sigue visible
          HIDDEN    -> formulario desapareció
          UNKNOWN   -> Playwright no pudo confirmar estado

        UNKNOWN nunca se interpreta como login correcto.
        """
        try:
            visible = await user.is_visible()

        except Exception as exc:
            return (
                "UNKNOWN",
                f"{type(exc).__name__}: {exc}",
            )

        return (
            "VISIBLE" if visible else "HIDDEN",
            "",
        )

    deadline = (
        time.monotonic()
        + min(cfg.timeout_ms, 60000) / 1000
    )

    last_visibility_error = ""
    rejection_seen = ""

    while time.monotonic() < deadline:

        # ------------------------------------------------------------------------------------------
        # 1. ÉXITO TIENE PRIORIDAD.
        # ------------------------------------------------------------------------------------------

        visibility, visibility_error = (
            await login_user_visibility()
        )

        if visibility == "HIDDEN":
            clear_credentials_alert()

            logger.info(
                "Login completado: "
                "el formulario de autenticación desapareció."
            )

            return

        if visibility == "UNKNOWN":
            last_visibility_error = (
                visibility_error
            )

            logger.warning(
                "No fue posible confirmar todavía "
                "la visibilidad del formulario de login: %s",
                visibility_error,
            )

        # ------------------------------------------------------------------------------------------
        # 2. EL RECHAZO DEBE SER PERSISTENTE.
        # ------------------------------------------------------------------------------------------

        rejection = (
            await credential_rejection()
        )

        if (
            rejection
            and visibility == "VISIBLE"
        ):
            rejection_seen = rejection

            logger.warning(
                "Mensaje de rechazo detectado. "
                "Se confirmará antes de declarar "
                "credenciales inválidas."
            )

            # SmartIT puede conservar mensajes antiguos mientras cambia
            # de vista. Dar tiempo para que termine la transición.
            await page.wait_for_timeout(2500)

            visibility_after, error_after = (
                await login_user_visibility()
            )

            if visibility_after == "HIDDEN":
                clear_credentials_alert()

                logger.info(
                    "Login completado durante "
                    "la confirmación del rechazo."
                )

                return

            if visibility_after == "UNKNOWN":
                last_visibility_error = (
                    error_after
                )

                logger.warning(
                    "No se pudo confirmar el formulario "
                    "durante la segunda validación: %s",
                    error_after,
                )

            confirmed_rejection = (
                await credential_rejection()
            )

            # Únicamente se declara contraseña inválida cuando:
            #
            #   A) el formulario continúa realmente visible
            #   B) el mensaje de rechazo sigue presente
            #
            # Si Playwright no puede confirmar A, no hacemos una
            # acusación falsa sobre las credenciales.
            if (
                visibility_after == "VISIBLE"
                and confirmed_rejection
            ):
                mark_credentials_invalid(
                    cfg.username,
                    detail=confirmed_rejection,
                )

                await take_screenshot(
                    page,
                    logger,
                    "error_login_credenciales",
                )

                logger.error(
                    "HELIX_CREDENCIALES_INVALIDAS: "
                    "rechazo persistente confirmado "
                    "con formulario de login visible."
                )

                raise RuntimeError(
                    "HELIX_CREDENCIALES_INVALIDAS: "
                    "SmartIT mantuvo visible el formulario "
                    "y el mensaje de rechazo de credenciales."
                )

            logger.info(
                "El rechazo no pudo confirmarse como "
                "persistente; continúa validación del login."
            )

        await page.wait_for_timeout(400)

    await take_screenshot(
        page,
        logger,
        "error_login",
    )

    details = []

    if rejection_seen:
        details.append(
            f"ultimo_rechazo={rejection_seen}"
        )

    if last_visibility_error:
        details.append(
            "error_visibilidad="
            + last_visibility_error
        )

    suffix = (
        " | " + " | ".join(details)
        if details
        else ""
    )

    raise RuntimeError(
        "HELIX_LOGIN_NO_CONFIRMADO: "
        "No fue posible certificar ni éxito ni "
        "rechazo persistente del login."
        + suffix
    )


async def abrir_consola(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> None:
    _, button, _ = await find_visible_across_frames(
        page,
        SEL["console"],
        cfg.timeout_ms,
        logger,
        "menú Consola",
    )
    await button.click()
    await esperar_carga_tabla(
        page,
        logger,
        cfg.timeout_ms,
        "apertura de Consola",
    )
    await log_frames(page, logger, "Consola cargada")


async def filtros_actuales(page: Page) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    matches = await visible_elements_across_frames(
        page,
        SEL["remove_filter"],
    )
    for _, locator, _ in matches:
        try:
            label = await locator.get_attribute("aria-label")
            if label and label not in seen:
                seen.add(label)
                names.append(label)
        except Exception:
            continue
    return names


async def count_visible_tags(page: Page) -> int:
    matches = await visible_elements_across_frames(
        page,
        SEL["filter_tags"],
    )
    return len(matches)


async def eliminar_filtros_actuales(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> int:
    """
    Quita un solo filtro por vuelta. Después espera la carga completa
    y vuelve a buscar los elementos en todos los frames de SmartIT.
    """
    removed = 0

    while removed < 50:
        match = await find_optional_visible_across_frames(
            page,
            SEL["remove_filter"],
            timeout_ms=1800,
        )
        if not match:
            break

        frame, button, selector = match
        label = (
            await button.get_attribute("aria-label")
            or f"filtro {removed + 1}"
        )
        logger.info(
            "Quitando filtro con %s en %s: %s",
            selector,
            frame_description(frame),
            label,
        )

        await button.click()
        removed += 1

        # Regla crítica: esperar después de CADA filtro.
        await esperar_carga_tabla(
            page,
            logger,
            cfg.timeout_ms,
            f"eliminación de {label}",
        )

    remaining = await filtros_actuales(page)
    remaining_tags = await count_visible_tags(page)

    if remaining:
        raise RuntimeError(
            "La limpieza no quedó completa. Filtros restantes: "
            + ", ".join(remaining)
        )

    # Algunas etiquetas pueden tener varios nodos visibles internos; solo
    # se usa como diagnóstico, no como motivo para detenerse si ya no hay X.
    if remaining_tags:
        logger.warning(
            "No quedan botones Quitar, pero se detectaron %s nodos visuales "
            "relacionados con etiquetas. Se continuará y se verificará "
            "el panel de filtros.",
            remaining_tags,
        )

    empty_visible = await any_visible_across_frames(
        page,
        SEL["empty_grid"],
    )
    logger.info(
        "Filtros eliminados=%s | vista vacía=%s",
        removed,
        empty_visible,
    )
    return removed


async def abrir_panel_filtros(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> None:
    frame, menu, selector = await find_visible_across_frames(
        page,
        SEL["filter_menu"],
        min(cfg.timeout_ms, 45000),
        logger,
        "botón Filtrar",
    )
    logger.info(
        "Abriendo Filtrar con %s dentro de %s",
        selector,
        frame_description(frame),
    )
    await menu.click()

    _, _, search_selector = await find_visible_across_frames(
        page,
        SEL["filter_search"],
        min(cfg.timeout_ms, 45000),
        logger,
        "campo de búsqueda del panel Filtrar",
    )
    logger.info(
        "Panel Filtrar abierto y listo. Campo=%s",
        search_selector,
    )


def normalize_ui_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return " ".join(normalized.casefold().split())


async def locator_enabled(locator: Locator) -> bool:
    try:
        return await locator.is_enabled()
    except Exception:
        return False


async def click_safely(locator: Locator) -> None:
    try:
        await locator.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        await locator.click()
        return
    except Exception:
        pass

    await locator.evaluate(
        """(element) => {
            const target = element.closest(
                'button, label, [role="option"], [role="checkbox"], '
                + '[role="radio"], a, .checkbox__label, .radio__label'
            ) || element;
            target.click();
        }"""
    )


async def selected_filter_texts(page: Page) -> list[str]:
    """
    Lee las etiquetas ubicadas en el bloque real "Filtros seleccionados".

    SmartIT cambia clases dinámicas entre ejecuciones. Por eso se parte
    del input estable data-testid=adapt-af-1_tag-field_search_input y se
    busca el contenedor adapt-mt correspondiente.
    """
    values: list[str] = []
    seen: set[str] = set()

    for frame in list(page.frames):
        try:
            inputs = frame.locator(
                '[data-testid="adapt-af-1_tag-field_search_input"]'
            )
            input_count = await inputs.count()

            for input_index in range(input_count):
                search_input = inputs.nth(input_index)

                container = search_input.locator(
                    "xpath=ancestor::div[contains(@class,"
                    " 'adapt-mt-container')][1]"
                )

                if await container.count() == 0:
                    continue

                tags = container.locator(
                    "adapt-tag, span.a-tag, .adapt-mt-tag"
                )
                tag_count = await tags.count()

                for tag_index in range(min(tag_count, 100)):
                    tag = tags.nth(tag_index)
                    if not await locator_visible(tag):
                        continue

                    text_value = " ".join(
                        (await tag.inner_text()).split()
                    )

                    if not text_value:
                        title_node = tag.locator("[title]").first
                        if await title_node.count():
                            text_value = (
                                await title_node.get_attribute("title")
                                or ""
                            ).strip()

                    if text_value and text_value not in seen:
                        seen.add(text_value)
                        values.append(text_value)
        except Exception:
            continue

    return values


async def wait_selected_filter(
    page: Page,
    field_name: str,
    value: str,
    timeout_ms: int,
) -> None:
    expected_field = normalize_ui_text(field_name)
    expected_value = normalize_ui_text(value)
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        tags = await selected_filter_texts(page)
        for tag in tags:
            normalized = normalize_ui_text(tag)
            if (
                expected_field in normalized
                and expected_value in normalized
            ):
                return
        await page.wait_for_timeout(250)

    raise PlaywrightTimeoutError(
        f"No apareció el filtro seleccionado: {field_name}: {value}"
    )


async def clear_filters_from_panel(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> None:
    """
    Limpia el panel usando el botón estable Quitar todo.

    No depende de haber detectado previamente las etiquetas, porque en
    SmartIT estas etiquetas pueden renderizarse con clases distintas.
    El estado del botón data-testid=adapt-af-1_footer_remove es la
    referencia principal: habilitado = hay filtros; deshabilitado =
    panel limpio.
    """
    current = await selected_filter_texts(page)
    logger.info(
        "Filtros visibles antes de limpiar (%s): %s",
        len(current),
        current or "no detectados por texto",
    )

    matches = await visible_elements_across_frames(
        page,
        SEL["filter_remove_all"],
    )

    chosen: tuple[Frame, Locator, str] | None = None
    for frame, locator, selector in matches:
        if await locator_enabled(locator):
            chosen = (frame, locator, selector)
            break

    if chosen is not None:
        frame, button, selector = chosen
        logger.info(
            "Limpiando TODOS los filtros con %s dentro de %s",
            selector,
            frame_description(frame),
        )
        await click_safely(button)

        deadline = time.monotonic() + min(
            cfg.timeout_ms,
            60000,
        ) / 1000

        while time.monotonic() < deadline:
            # El botón puede reconstruirse; volver a buscarlo.
            refreshed = await visible_elements_across_frames(
                page,
                SEL["filter_remove_all"],
            )

            enabled_found = False
            for _, candidate, _ in refreshed:
                if await locator_enabled(candidate):
                    enabled_found = True
                    break

            if not enabled_found:
                await page.wait_for_timeout(600)
                remaining = await selected_filter_texts(page)
                logger.info(
                    "Panel limpio. Etiquetas restantes=%s",
                    remaining or "ninguna",
                )
                return

            await page.wait_for_timeout(250)

        raise RuntimeError(
            "Quitar todo siguió habilitado después de 60 segundos."
        )

    # Respaldo para variantes donde el footer no expone Quitar todo.
    header_matches = await visible_elements_across_frames(
        page,
        SEL["filter_clear_header"],
    )

    for frame, button, selector in header_matches:
        if await locator_enabled(button):
            logger.info(
                "Usando acción Borrar con %s dentro de %s",
                selector,
                frame_description(frame),
            )
            await click_safely(button)
            await page.wait_for_timeout(700)
            return

    # Si Quitar todo existe pero está deshabilitado, el panel ya está limpio.
    if matches:
        logger.info(
            "Quitar todo está deshabilitado: el panel ya está limpio."
        )
        return

    raise RuntimeError(
        "No se encontró el control Quitar todo/Borrar del panel."
    )


async def find_filter_control(
    page: Page,
    field_name: str,
    timeout_ms: int,
    logger: logging.Logger,
) -> tuple[Frame, Locator]:
    expected = normalize_ui_text(field_name)
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        for frame in list(page.frames):
            for selector in SEL["filter_controls"]:
                try:
                    controls = frame.locator(selector)
                    count = await controls.count()
                    for index in range(min(count, 250)):
                        control = controls.nth(index)
                        label = control.locator(
                            ".advanced-filter__label .ellipsis"
                        ).first
                        if not await locator_visible(label):
                            continue
                        text = normalize_ui_text(
                            await label.inner_text()
                        )
                        if text == expected:
                            logger.info(
                                "Campo %s encontrado dentro de %s",
                                field_name,
                                frame_description(frame),
                            )
                            return frame, control
                except Exception:
                    continue
        await page.wait_for_timeout(300)

    raise PlaywrightTimeoutError(
        f"No se encontró el campo de filtro {field_name!r}."
    )


async def visible_exact_text_locator(
    root: Locator,
    text: str,
) -> Locator | None:
    try:
        candidates = root.get_by_text(text, exact=True)
        count = await candidates.count()
        for index in range(min(count, 100)):
            candidate = candidates.nth(index)
            if await locator_visible(candidate):
                return candidate
    except Exception:
        return None
    return None


async def selected_tag_is_visible(
    page: Page,
    field_name: str,
    value: str,
) -> bool:
    """
    Confirma la selección usando la etiqueta visible que SmartIT agrega
    en "Filtros seleccionados" y también en la barra superior.

    Ejemplos reales:
      Estado: Asignado
      Tipo de ticket: Incidencia
    """
    expected = f"{field_name}: {value}"
    expected_normalized = normalize_ui_text(expected)

    for frame in list(page.frames):
        # 1. Texto exacto visible.
        try:
            candidates = frame.get_by_text(expected, exact=True)
            count = await candidates.count()

            for index in range(min(count, 100)):
                candidate = candidates.nth(index)
                if not await locator_visible(candidate):
                    continue

                candidate_text = " ".join(
                    (await candidate.inner_text()).split()
                )
                if normalize_ui_text(candidate_text) == expected_normalized:
                    return True
        except Exception:
            pass

        # 2. Atributos title/aria-label usados por los tags de SmartIT.
        for selector in (
            f'[title="{expected}"]',
            f'[aria-label="Quitar {expected}"]',
        ):
            try:
                locator = frame.locator(selector)
                count = await locator.count()

                for index in range(min(count, 100)):
                    if await locator_visible(locator.nth(index)):
                        return True
            except Exception:
                continue

        # 3. Respaldo por componentes de etiqueta.
        try:
            tags = frame.locator(
                "adapt-tag, span.a-tag, "
                ".filter-tags__tag-text, .adapt-mt-tag"
            )
            count = await tags.count()

            for index in range(min(count, 200)):
                tag = tags.nth(index)
                if not await locator_visible(tag):
                    continue

                tag_text = " ".join(
                    (await tag.inner_text()).split()
                )
                if normalize_ui_text(tag_text) == expected_normalized:
                    return True
        except Exception:
            pass

    return False


async def wait_filter_selection_confirmed(
    page: Page,
    control: Locator,
    field_name: str,
    value: str,
    timeout_ms: int,
) -> str:
    """
    Primero valida el tag visible, que es la evidencia más estable en
    esta versión de SmartIT. Como respaldo intenta el checkbox real.
    """
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        if await selected_tag_is_visible(
            page,
            field_name,
            value,
        ):
            return "tag"

        try:
            if await filter_option_is_selected(control, value):
                return "checkbox"
        except Exception:
            # El panel puede reconstruir el control tras cada selección.
            pass

        await page.wait_for_timeout(250)

    raise PlaywrightTimeoutError(
        f"No se confirmó la selección: {field_name}: {value}"
    )


async def filter_option_is_selected(
    control: Locator,
    value: str,
) -> bool:
    """
    Verifica el checkbox/radio real de una opción dentro de un campo.
    No depende de que aparezca una etiqueta en Filtros seleccionados.
    """
    candidates = control.get_by_text(value, exact=True)
    count = await candidates.count()

    for index in range(min(count, 100)):
        candidate = candidates.nth(index)
        if not await locator_visible(candidate):
            continue

        # La opción normalmente está dentro de un label.
        label = candidate.locator("xpath=ancestor::label[1]")
        if await label.count():
            inputs = label.locator(
                'input[type="checkbox"], input[type="radio"]'
            )
            input_count = await inputs.count()

            for input_index in range(input_count):
                element = inputs.nth(input_index)
                try:
                    if await element.is_checked():
                        return True
                except Exception:
                    aria_checked = (
                        await element.get_attribute("aria-checked")
                        or ""
                    ).lower()
                    if aria_checked == "true":
                        return True

        # Respaldo para componentes que manejan aria-checked fuera del input.
        selectable = candidate.locator(
            "xpath=ancestor::*[@aria-checked][1]"
        )
        if await selectable.count():
            aria_checked = (
                await selectable.get_attribute("aria-checked")
                or ""
            ).lower()
            if aria_checked == "true":
                return True

    return False


async def wait_filter_option_selected(
    control: Locator,
    field_name: str,
    value: str,
    timeout_ms: int,
) -> None:
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        if await filter_option_is_selected(control, value):
            return
        await control.page.wait_for_timeout(250)

    raise PlaywrightTimeoutError(
        f"El checkbox no quedó seleccionado: "
        f"{field_name}: {value}"
    )


async def select_filter_value(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    field_name: str,
    value: str,
) -> None:
    frame, control = await find_filter_control(
        page,
        field_name,
        min(cfg.timeout_ms, 90000),
        logger,
    )

    header = control.locator("button.card-title").first
    await header.scroll_into_view_if_needed()

    expanded = (
        await header.get_attribute("aria-expanded")
        or "false"
    ).lower() == "true"

    if not expanded:
        logger.info("Abriendo campo: %s", field_name)
        await click_safely(header)

    deadline = time.monotonic() + min(
        cfg.timeout_ms,
        90000,
    ) / 1000
    option: Locator | None = None

    while time.monotonic() < deadline:
        option = await visible_exact_text_locator(control, value)

        # Algunas opciones se renderizan como overlay fuera del control.
        if option is None:
            panel = frame.locator(
                ".advanced-filter__popover-container"
            ).first
            option = await visible_exact_text_locator(panel, value)

        if option is not None:
            break

        await page.wait_for_timeout(300)

    if option is None:
        raise PlaywrightTimeoutError(
            f"No apareció la opción {value!r} para {field_name!r}."
        )

    logger.info("Seleccionando %s = %s", field_name, value)
    await click_safely(option)

    confirmation = await wait_filter_selection_confirmed(
        page,
        control,
        field_name,
        value,
        min(cfg.timeout_ms, 60000),
    )

    logger.info(
        "Selección confirmada por %s: %s: %s",
        confirmation,
        field_name,
        value,
    )


async def save_date_filter_debug(
    page: Page,
    control: Locator | None,
    logger: logging.Logger,
    label: str,
) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label).strip("_")
    html_path = DEBUG_DIR / f"crear_fecha_{safe_label}_{stamp}.html"

    try:
        if control is not None:
            html = await control.evaluate(
                "(element) => element.outerHTML"
            )
        else:
            html = "<!-- No se obtuvo el control Crear fecha -->"
        html_path.write_text(str(html), encoding="utf-8")
        logger.info("HTML de diagnóstico de fecha: %s", html_path)
    except Exception as exc:
        logger.warning(
            "No se pudo guardar HTML de fecha: %s",
            exc,
        )

    await take_screenshot(
        page,
        logger,
        f"crear_fecha_{safe_label}",
    )


async def date_filter_tag_texts(
    page: Page,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    for frame in list(page.frames):
        selectors = [
            '.filter-tags__tag-text[title*="Crear fecha"]',
            '[title^="Crear fecha:"]',
            '[aria-label^="Quitar Crear fecha:"]',
            'adapt-tag:has-text("Crear fecha")',
            '.a-tag:has-text("Crear fecha")',
        ]

        for selector in selectors:
            try:
                locator = frame.locator(selector)
                count = await locator.count()

                for index in range(min(count, 100)):
                    item = locator.nth(index)
                    if not await locator_visible(item):
                        continue

                    value = (
                        await item.get_attribute("title")
                        or await item.get_attribute("aria-label")
                        or " ".join((await item.inner_text()).split())
                    ).strip()

                    if value and value not in seen:
                        seen.add(value)
                        values.append(value)
            except Exception:
                continue

    return values


async def date_filter_is_selected(
    page: Page,
) -> bool:
    tags = await date_filter_tag_texts(page)
    return any(
        "crear fecha" in normalize_ui_text(tag)
        for tag in tags
    )


SPANISH_MONTH_NAMES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}

SPANISH_MONTH_ALIASES = {
    "ene": 1,
    "enero": 1,
    "feb": 2,
    "febrero": 2,
    "mar": 3,
    "marzo": 3,
    "abr": 4,
    "abril": 4,
    "may": 5,
    "mayo": 5,
    "jun": 6,
    "junio": 6,
    "jul": 7,
    "julio": 7,
    "ago": 8,
    "agosto": 8,
    "sep": 9,
    "sept": 9,
    "septiembre": 9,
    "oct": 10,
    "octubre": 10,
    "nov": 11,
    "noviembre": 11,
    "dic": 12,
    "diciembre": 12,
}


async def first_visible_in_locator(
    root: Locator,
    selector: str,
) -> Locator | None:
    try:
        locator = root.locator(selector)
        count = await locator.count()

        for index in range(min(count, 50)):
            candidate = locator.nth(index)
            if await locator_visible(candidate):
                return candidate
    except Exception:
        return None

    return None


def parse_calendar_month(text_value: str) -> int:
    normalized = normalize_ui_text(text_value)
    normalized = re.sub(r"[^a-zñ]+", " ", normalized).strip()

    for token in normalized.split():
        if token in SPANISH_MONTH_ALIASES:
            return SPANISH_MONTH_ALIASES[token]

    raise RuntimeError(
        f"No se pudo interpretar el mes visible del calendario: "
        f"{text_value!r}"
    )


async def read_calendar_month_year(
    control: Locator,
) -> tuple[int, int]:
    month_button = await first_visible_in_locator(
        control,
        '[data-testid$="_month"]',
    )
    year_button = await first_visible_in_locator(
        control,
        '[data-testid$="_year"]',
    )

    if month_button is None or year_button is None:
        raise RuntimeError(
            "No aparecieron los controles de mes y año del calendario."
        )

    month_text = " ".join(
        (await month_button.inner_text()).split()
    )
    year_text = " ".join(
        (await year_button.inner_text()).split()
    )

    month_number = parse_calendar_month(month_text)

    match = re.search(r"\b(20\d{2})\b", year_text)
    if not match:
        raise RuntimeError(
            f"No se pudo interpretar el año del calendario: "
            f"{year_text!r}"
        )

    return month_number, int(match.group(1))


async def click_calendar_control(
    page: Page,
    control: Locator,
    selector: str,
    description: str,
) -> None:
    button = await first_visible_in_locator(
        control,
        selector,
    )
    if button is None:
        raise RuntimeError(
            f"No apareció el control del calendario: {description}"
        )

    await click_safely(button)
    await page.wait_for_timeout(450)


async def navigate_calendar_to(
    page: Page,
    control: Locator,
    target_date: date,
    logger: logging.Logger,
) -> None:
    for _ in range(180):
        current_month, current_year = (
            await read_calendar_month_year(control)
        )

        current_index = current_year * 12 + current_month
        target_index = target_date.year * 12 + target_date.month
        difference = target_index - current_index

        if difference == 0:
            logger.info(
                "Calendario ubicado en %s/%s",
                target_date.month,
                target_date.year,
            )
            return

        if abs(difference) >= 12:
            selector = (
                '[data-testid$="_nextYear"]'
                if difference > 0
                else '[data-testid$="_prevYear"]'
            )
            await click_calendar_control(
                page,
                control,
                selector,
                "año siguiente/anterior",
            )
        else:
            selector = (
                '[data-testid$="_nextMonth"]'
                if difference > 0
                else '[data-testid$="_prevMonth"]'
            )
            await click_calendar_control(
                page,
                control,
                selector,
                "mes siguiente/anterior",
            )

    raise RuntimeError(
        f"No fue posible navegar el calendario hasta "
        f"{target_date.isoformat()}."
    )


async def click_calendar_day(
    page: Page,
    control: Locator,
    target_date: date,
    logger: logging.Logger,
) -> None:
    await navigate_calendar_to(
        page,
        control,
        target_date,
        logger,
    )

    month_name = SPANISH_MONTH_NAMES[target_date.month]
    aria_suffix = (
        f"{target_date.day} de {month_name} "
        f"de {target_date.year}"
    )
    selector = (
        f'button[aria-label$="{aria_suffix}"]'
        ':not([aria-disabled="true"])'
    )

    day_button = await first_visible_in_locator(
        control,
        selector,
    )

    if day_button is None:
        raise RuntimeError(
            f"No apareció el día {target_date.isoformat()} "
            f"en el calendario. Selector: {selector}"
        )

    aria_label = (
        await day_button.get_attribute("aria-label")
        or aria_suffix
    )

    logger.info(
        "Seleccionando fecha del calendario: %s",
        aria_label,
    )
    await click_safely(day_button)
    await page.wait_for_timeout(650)


async def endpoint_tab(
    control: Locator,
    endpoint: str,
) -> Locator:
    suffix = "_start" if endpoint == "start" else "_end"
    tab = await first_visible_in_locator(
        control,
        f'[data-testid$="{suffix}"]',
    )

    if tab is None:
        name = "Inicio" if endpoint == "start" else "Fin"
        raise RuntimeError(
            f"No apareció la pestaña {name} del rango de fecha."
        )

    return tab


async def endpoint_summary(
    control: Locator,
    endpoint: str,
) -> str:
    tab = await endpoint_tab(control, endpoint)
    return " ".join((await tab.inner_text()).split())


async def select_endpoint_tab(
    page: Page,
    control: Locator,
    endpoint: str,
) -> None:
    tab = await endpoint_tab(control, endpoint)
    await click_safely(tab)
    await page.wait_for_timeout(450)


async def set_active_endpoint_hour(
    page: Page,
    control: Locator,
    hour: int,
    logger: logging.Logger,
) -> None:
    hour_input = await first_visible_in_locator(
        control,
        'input[aria-label="horas"]'
        '[data-testid$="_hoursTimeSummary"]',
    )

    if hour_input is None:
        hour_input = await first_visible_in_locator(
            control,
            'input[aria-label="horas"]',
        )

    if hour_input is None:
        logger.warning(
            "No apareció el campo de hora; SmartIT conservará "
            "la hora predeterminada."
        )
        return

    value = f"{hour:02d}"
    logger.info("Configurando hora del rango: %s:00", value)

    await hour_input.scroll_into_view_if_needed()
    await hour_input.click()
    await hour_input.fill(value)
    await hour_input.press("Tab")
    await page.wait_for_timeout(500)


async def select_calendar_endpoint(
    page: Page,
    control: Locator,
    endpoint: str,
    target_date: date,
    hour: int,
    logger: logging.Logger,
) -> str:
    endpoint_name = (
        "Inicio" if endpoint == "start" else "Fin"
    )

    logger.info(
        "Configurando %s del rango: %s %02d:00",
        endpoint_name,
        target_date.isoformat(),
        hour,
    )

    await select_endpoint_tab(
        page,
        control,
        endpoint,
    )
    await click_calendar_day(
        page,
        control,
        target_date,
        logger,
    )

    # El componente puede cambiar automáticamente de Inicio a Fin
    # después de elegir el día. Se vuelve a seleccionar la pestaña
    # correcta antes de configurar la hora.
    await select_endpoint_tab(
        page,
        control,
        endpoint,
    )
    await set_active_endpoint_hour(
        page,
        control,
        hour,
        logger,
    )

    summary = await endpoint_summary(
        control,
        endpoint,
    )

    if "sin seleccionar" in normalize_ui_text(summary):
        raise RuntimeError(
            f"SmartIT no confirmó el valor de {endpoint_name}. "
            f"Resumen visible: {summary!r}"
        )

    logger.info(
        "%s confirmado por SmartIT: %s",
        endpoint_name,
        summary,
    )
    return summary


async def select_date_range(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    start_date: date,
    end_date: date,
) -> None:
    frame: Frame | None = None
    control: Locator | None = None
    label = (
        f"{date_slug(start_date)}_{date_slug(end_date)}"
    )

    try:
        frame, control = await find_filter_control(
            page,
            "Crear fecha",
            min(cfg.timeout_ms, 90000),
            logger,
        )

        header = control.locator("button.card-title").first
        await header.scroll_into_view_if_needed()

        expanded = (
            await header.get_attribute("aria-expanded")
            or "false"
        ).lower() == "true"

        if not expanded:
            logger.info("Abriendo campo: Crear fecha")
            await click_safely(header)
            await page.wait_for_timeout(900)

        calendar = await first_visible_in_locator(
            control,
            '[data-testid$="_calendar"]',
        )
        if calendar is None:
            raise RuntimeError(
                "Crear fecha se abrió, pero no apareció "
                "el calendario de SmartIT."
            )

        # El rango solicitado es inclusivo. Para no perder registros
        # creados entre las 23:00 y las 23:59 del último día, el Fin se
        # configura a las 00:00 del día siguiente. Los duplicados que
        # puedan existir en el límite se eliminan por Mostrar ID.
        exclusive_end = end_date + timedelta(days=1)

        start_summary = await select_calendar_endpoint(
            page,
            control,
            endpoint="start",
            target_date=start_date,
            hour=0,
            logger=logger,
        )

        end_summary = await select_calendar_endpoint(
            page,
            control,
            endpoint="end",
            target_date=exclusive_end,
            hour=0,
            logger=logger,
        )

        await page.wait_for_timeout(900)
        tags = await date_filter_tag_texts(page)

        logger.info(
            "Rango real configurado | solicitado=%s a %s "
            "| SmartIT Inicio=%s | SmartIT Fin=%s | tags=%s",
            start_date.isoformat(),
            end_date.isoformat(),
            start_summary,
            end_summary,
            tags or "sin etiqueta visible",
        )

        if not await date_filter_is_selected(page):
            # Algunos despliegues crean la etiqueta solo al aplicar,
            # pero las dos pestañas ya deben tener valor.
            start_check = await endpoint_summary(
                control,
                "start",
            )
            end_check = await endpoint_summary(
                control,
                "end",
            )

            if (
                "sin seleccionar"
                in normalize_ui_text(start_check)
                or "sin seleccionar"
                in normalize_ui_text(end_check)
            ):
                raise RuntimeError(
                    "El rango no quedó completo en Inicio y Fin."
                )

            logger.info(
                "El rango quedó confirmado en las pestañas "
                "Inicio/Fin; la etiqueta aparecerá al aplicar."
            )

    except Exception:
        await save_date_filter_debug(
            page,
            control,
            logger,
            label,
        )
        raise


async def apply_filters(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    expected_filters: list[tuple[str, str]],
) -> None:
    if len(expected_filters) < 2:
        raise RuntimeError(
            "La rutina debe definir al menos dos filtros."
        )

    confirmed_filters: list[str] = []

    for field_name, value in expected_filters:
        confirmed = await selected_tag_is_visible(
            page,
            field_name,
            value,
        )

        if not confirmed:
            try:
                _, control = await find_filter_control(
                    page,
                    field_name,
                    min(cfg.timeout_ms, 45000),
                    logger,
                )
                confirmed = await filter_option_is_selected(
                    control,
                    value,
                )
            except Exception:
                confirmed = False

        if not confirmed:
            raise RuntimeError(
                f"No está seleccionado: {field_name}: {value}"
            )

        confirmed_filters.append(f"{field_name}: {value}")

    logger.info(
        "Filtros confirmados antes de aplicar: %s",
        confirmed_filters,
    )

    _, button, selector = await find_visible_across_frames(
        page,
        SEL["filter_apply"],
        min(cfg.timeout_ms, 45000),
        logger,
        "botón Aplicar filtros",
    )

    deadline = time.monotonic() + min(
        cfg.timeout_ms,
        45000,
    ) / 1000
    while time.monotonic() < deadline:
        if await locator_enabled(button):
            break
        await page.wait_for_timeout(250)
    else:
        raise RuntimeError(
            "Aplicar filtros continuó deshabilitado."
        )

    logger.info("Aplicando filtros con %s", selector)
    await click_safely(button)

    await esperar_carga_tabla(
        page,
        logger,
        cfg.timeout_ms,
        "aplicación de filtros",
    )

    # La consulta puede devolver filas o un estado vacío.
    ready = await find_optional_visible_across_frames(
        page,
        SEL["table_ready"] + SEL["empty_grid"],
        timeout_ms=min(cfg.timeout_ms, 90000),
    )
    if not ready:
        raise RuntimeError(
            "Después de aplicar no apareció la tabla ni el estado vacío."
        )

    logger.info("Resultado de filtros cargado.")


async def download_csv(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    filename: str,
) -> Path:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    _, export_button, export_selector = (
        await find_visible_across_frames(
            page,
            SEL["export_button"],
            min(cfg.timeout_ms, 90000),
            logger,
            "botón Exportar",
        )
    )
    logger.info("Abriendo Exportar con %s", export_selector)
    await click_safely(export_button)

    _, csv_button, csv_selector = await find_visible_across_frames(
        page,
        SEL["export_csv"],
        min(cfg.timeout_ms, 45000),
        logger,
        "opción CSV",
    )
    logger.info(
        "Solicitando CSV con %s. Se esperará hasta %s minutos.",
        csv_selector,
        round(cfg.download_timeout_ms / 60000, 1),
    )

    async with page.expect_download(
        timeout=cfg.download_timeout_ms
    ) as download_info:
        await click_safely(csv_button)

    download = await download_info.value
    target = cfg.output_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.part"
    )

    if temporary.exists():
        temporary.unlink()

    await download.save_as(str(temporary))

    failure = await download.failure()
    if failure:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"SmartIT reportó un error descargando {filename}: "
            f"{failure}"
        )

    if not temporary.exists() or temporary.stat().st_size == 0:
        raise RuntimeError(
            f"El archivo descargado quedó vacío: {temporary}"
        )

    # Validación rápida de cabecera.
    with temporary.open(
        "r",
        encoding="utf-8-sig",
        newline="",
        errors="replace",
    ) as handle:
        first_line = handle.readline()
    if "Mostrar ID" not in first_line:
        raise RuntimeError(
            "El CSV descargado no contiene la cabecera Mostrar ID."
        )

    os.replace(temporary, target)
    logger.info(
        "CSV guardado: %s (%s bytes)",
        target,
        target.stat().st_size,
    )
    return target


def consolidate_csv_files(
    inputs: list[Path],
    output: Path,
    logger: logging.Logger,
) -> Path:
    rows_by_id: dict[str, dict[str, str]] = {}
    headers: list[str] = []
    header_seen: set[str] = set()

    for source in inputs:
        with source.open(
            "r",
            encoding="utf-8-sig",
            newline="",
            errors="replace",
        ) as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise RuntimeError(
                    f"CSV sin cabeceras: {source}"
                )

            for header in reader.fieldnames:
                if header not in header_seen:
                    header_seen.add(header)
                    headers.append(header)

            for row in reader:
                incident_id = (row.get("Mostrar ID") or "").strip()
                if not incident_id:
                    continue
                rows_by_id[incident_id] = {
                    key: value or ""
                    for key, value in row.items()
                    if key is not None
                }

    if "Mostrar ID" not in header_seen:
        raise RuntimeError(
            "No se encontró la columna Mostrar ID al consolidar."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")

    with temporary.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=headers,
            extrasaction="ignore",
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        for row in rows_by_id.values():
            writer.writerow(
                {header: row.get(header, "") for header in headers}
            )

    os.replace(temporary, output)
    logger.info(
        "Consolidado creado: %s | registros únicos=%s",
        output,
        len(rows_by_id),
    )
    return output


async def execute_report_routine(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    routine_name: str,
    filters: list[tuple[str, str]],
    filename: str,
    date_range: tuple[date, date] | None = None,
) -> Path:
    logger.info("=" * 72)
    logger.info("INICIANDO RUTINA: %s", routine_name)
    logger.info("=" * 72)

    await abrir_panel_filtros(page, cfg, logger)
    await clear_filters_from_panel(page, cfg, logger)

    for field_name, value in filters:
        await select_filter_value(
            page,
            cfg,
            logger,
            field_name,
            value,
        )

    if date_range is not None:
        await select_date_range(
            page,
            cfg,
            logger,
            date_range[0],
            date_range[1],
        )

    await apply_filters(
        page,
        cfg,
        logger,
        filters,
    )

    if date_range is None:
        await take_screenshot(
            page,
            logger,
            f"resultado_{routine_name}",
        )
    else:
        logger.info(
            "Captura omitida en barrido por fechas para evitar "
            "bloqueos de varios minutos."
        )

    result = await download_csv(
        page,
        cfg,
        logger,
        filename,
    )
    logger.info("RUTINA TERMINADA: %s", routine_name)
    return result


async def execute_two_routines(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    requested: str,
    consolidate: bool,
) -> list[Path]:
    routines = [
        {
            "name": "incidentes_asignados",
            "filters": [
                ("Estado", "Asignado"),
                ("Tipo de ticket", "Incidencia"),
            ],
            "filename": "incidentes_asignados.csv",
        },
        {
            "name": "incidentes_en_curso",
            "filters": [
                ("Estado", "En curso"),
                ("Tipo de ticket", "Incidencia"),
            ],
            "filename": "incidentes_en_curso.csv",
        },
    ]

    if requested != "ambos":
        routines = [
            routine
            for routine in routines
            if routine["name"] == requested
        ]

    downloaded: list[Path] = []
    for routine in routines:
        downloaded.append(
            await execute_report_routine(
                page,
                cfg,
                logger,
                routine["name"],
                routine["filters"],
                routine["filename"],
            )
        )

    if consolidate and len(downloaded) == 2:
        consolidated = consolidate_csv_files(
            downloaded,
            cfg.output_dir / "incidentes_activos.csv",
            logger,
        )

        if cfg.dashboard_csv:
            cfg.dashboard_csv.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            temporary = cfg.dashboard_csv.with_suffix(
                cfg.dashboard_csv.suffix + ".part"
            )
            shutil.copy2(consolidated, temporary)
            os.replace(temporary, cfg.dashboard_csv)
            logger.info(
                "Dashboard actualizado de forma atómica: %s",
                cfg.dashboard_csv,
            )

    return downloaded


def csv_row_count(path: Path) -> int:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
        errors="replace",
    ) as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def publish_dashboard_csv(
    source: Path,
    target: Path | None,
    logger: logging.Logger,
) -> None:
    """
    Publica el consolidado sin perder el resultado cuando Windows mantiene
    abierto el CSV del dashboard.

    - Reintenta el reemplazo atomico.
    - Usa un archivo temporal unico.
    - Si el destino continua bloqueado, conserva una copia pendiente y deja
      terminar correctamente el barrido.
    """
    if target is None:
        logger.info(
            "Publicacion al dashboard omitida. "
            "El consolidado local queda en: %s",
            source,
        )
        return

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{stamp}.part"
    )

    last_error: Exception | None = None

    for attempt in range(1, 13):
        try:
            temporary.unlink(missing_ok=True)
            shutil.copy2(source, temporary)
            os.replace(temporary, target)

            logger.info(
                "Dashboard actualizado de forma atomica: %s",
                target,
            )
            return

        except PermissionError as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)

            logger.warning(
                "El CSV del dashboard esta bloqueado "
                "(intento %s/12). Se reintentara en 5 segundos: %s",
                attempt,
                target,
            )
            time.sleep(5)

        except OSError as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)

            logger.warning(
                "No se pudo publicar el dashboard "
                "(intento %s/12). Se reintentara en 5 segundos: %s",
                attempt,
                exc,
            )
            time.sleep(5)

    pending = target.with_name(
        f"{target.stem}_PENDIENTE_{stamp}{target.suffix}"
    )
    shutil.copy2(source, pending)

    logger.warning(
        "El barrido termino correctamente, pero el archivo principal "
        "del dashboard siguio bloqueado. Se dejo una copia pendiente: %s "
        "| error=%s",
        pending,
        last_error,
    )


async def execute_window_status(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    status_name: str,
    start_date: date,
    end_date: date,
    parts_dir: Path,
) -> Path:
    status_slug = (
        "asignados"
        if normalize_ui_text(status_name) == "asignado"
        else "en_curso"
    )
    window_slug = (
        f"{date_slug(start_date)}_{date_slug(end_date)}"
    )
    routine_name = f"{status_slug}_{window_slug}"
    filename = f"{routine_name}.csv"
    existing = (
        cfg.output_dir
        / "semanal_calendario"
        / "partes"
        / filename
    )

    if (
        existing.exists()
        and existing.stat().st_size > 200
    ):
        try:
            rows = csv_row_count(existing)
            logger.info(
                "REANUDANDO: se reutiliza %s | filas=%s",
                existing,
                rows,
            )
            return existing
        except Exception:
            logger.warning(
                "El parcial existente no es válido y se "
                "volverá a descargar: %s",
                existing,
            )

    return await execute_report_routine(
        page,
        cfg,
        logger,
        routine_name=routine_name,
        filters=[
            ("Estado", status_name),
            ("Tipo de ticket", "Incidencia"),
        ],
        filename=str(
            Path("semanal_calendario")
            / "partes"
            / filename
        ),
        date_range=(start_date, end_date),
    )


async def execute_window_with_auto_split(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    status_name: str,
    start_date: date,
    end_date: date,
    threshold: int,
    parts_dir: Path,
    limit_dir: Path,
) -> list[Path]:
    weekly_file = await execute_window_status(
        page,
        cfg,
        logger,
        status_name,
        start_date,
        end_date,
        parts_dir,
    )
    rows = csv_row_count(weekly_file)

    logger.info(
        "Ventana %s | Estado=%s | filas=%s",
        f"{start_date} a {end_date}",
        status_name,
        rows,
    )

    if rows < threshold or start_date == end_date:
        if rows >= threshold and start_date == end_date:
            logger.warning(
                "ALERTA: el día %s para Estado=%s tiene %s filas. "
                "Puede existir un límite incluso a nivel diario.",
                start_date,
                status_name,
                rows,
            )
        return [weekly_file]

    logger.warning(
        "La ventana %s a %s alcanzó %s filas. "
        "Se dividirá automáticamente por días.",
        start_date,
        end_date,
        rows,
    )

    limit_dir.mkdir(parents=True, exist_ok=True)
    archived = limit_dir / weekly_file.name
    if archived.exists():
        archived.unlink()
    shutil.move(str(weekly_file), str(archived))

    daily_files: list[Path] = []
    current = start_date

    while current <= end_date:
        daily_file = await execute_window_status(
            page,
            cfg,
            logger,
            status_name,
            current,
            current,
            parts_dir,
        )
        daily_rows = csv_row_count(daily_file)

        logger.info(
            "Día %s | Estado=%s | filas=%s",
            current,
            status_name,
            daily_rows,
        )

        if daily_rows >= threshold:
            logger.warning(
                "ALERTA: %s filas en un solo día (%s, %s).",
                daily_rows,
                current,
                status_name,
            )

        daily_files.append(daily_file)
        current += timedelta(days=1)

    return daily_files


async def execute_weekly_sweep(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    start_date: date,
    end_date: date,
    days_per_window: int,
    threshold: int,
) -> list[Path]:
    sweep_root = cfg.output_dir / "semanal_calendario"
    parts_dir = sweep_root / "partes"
    limit_dir = sweep_root / "semanas_divididas"

    parts_dir.mkdir(parents=True, exist_ok=True)
    limit_dir.mkdir(parents=True, exist_ok=True)

    windows = weekly_windows(
        start_date,
        end_date,
        days_per_window,
    )

    logger.info(
        "BARRIDO SEMANAL | desde=%s | hasta=%s | "
        "ventanas=%s | días_por_ventana=%s | umbral=%s",
        start_date,
        end_date,
        len(windows),
        days_per_window,
        threshold,
    )

    all_parts: list[Path] = []

    for position, (window_start, window_end) in enumerate(
        windows,
        start=1,
    ):
        logger.info("#" * 72)
        logger.info(
            "VENTANA %s/%s: %s a %s",
            position,
            len(windows),
            window_start,
            window_end,
        )
        logger.info("#" * 72)

        for status_name in ("Asignado", "En curso"):
            status_parts = await execute_window_with_auto_split(
                page,
                cfg,
                logger,
                status_name,
                window_start,
                window_end,
                threshold,
                parts_dir,
                limit_dir,
            )
            all_parts.extend(status_parts)

    if not all_parts:
        raise RuntimeError(
            "El barrido terminó sin archivos para consolidar."
        )

    final_output = cfg.output_dir / "incidentes_activos.csv"
    consolidate_csv_files(
        all_parts,
        final_output,
        logger,
    )

    publish_dashboard_csv(
        final_output,
        cfg.dashboard_csv,
        logger,
    )

    logger.info(
        "BARRIDO SEMANAL COMPLETADO | partes=%s | salida=%s",
        len(all_parts),
        final_output,
    )
    return all_parts


async def main_async(args: argparse.Namespace) -> int:
    logger = setup_logger()
    try:
        cfg = Settings.from_env()
    except Exception as exc:
        logger.error("%s", exc)
        return 2

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context = None
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=cfg.headless,
                slow_mo=cfg.slow_mo_ms,
                accept_downloads=True,
                viewport={"width": 1600, "height": 950},
                args=[
                    "--start-maximized",
                    "--disable-notifications",
                ],
            )
            context.set_default_timeout(cfg.timeout_ms)
            context.set_default_navigation_timeout(
                cfg.timeout_ms
            )
            page = (
                context.pages[0]
                if context.pages
                else await context.new_page()
            )

            await iniciar_sesion(page, cfg, logger)
            await abrir_consola(page, cfg, logger)

            cfg.output_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            if args.solo_panel:
                await abrir_panel_filtros(
                    page,
                    cfg,
                    logger,
                )
                await take_screenshot(
                    page,
                    logger,
                    "panel_filtros_abierto",
                )
                logger.info("PRUEBA DE PANEL COMPLETADA.")
            elif args.barrido_semanal:
                if not args.fecha_desde or not args.fecha_hasta:
                    raise RuntimeError(
                        "El barrido semanal requiere "
                        "--fecha-desde y --fecha-hasta."
                    )

                start_date = parse_iso_date(
                    args.fecha_desde,
                    "--fecha-desde",
                )
                end_date = parse_iso_date(
                    args.fecha_hasta,
                    "--fecha-hasta",
                )

                downloaded = await execute_weekly_sweep(
                    page,
                    cfg,
                    logger,
                    start_date=start_date,
                    end_date=end_date,
                    days_per_window=args.dias_ventana,
                    threshold=args.umbral_division,
                )
                logger.info(
                    "PARTES DESCARGADAS: %s",
                    [str(path) for path in downloaded],
                )
            else:
                downloaded = await execute_two_routines(
                    page,
                    cfg,
                    logger,
                    requested=args.rutina,
                    consolidate=not args.sin_consolidar,
                )
                logger.info(
                    "ARCHIVOS DESCARGADOS: %s",
                    [str(path) for path in downloaded],
                )
                logger.info("RUTINAS SMARTIT COMPLETADAS.")

            if not cfg.headless and cfg.keep_open_seconds > 0:
                await page.wait_for_timeout(
                    cfg.keep_open_seconds * 1000
                )
            return 0

        except Exception as exc:
            logger.exception("Rutina fallida: %s", exc)
            if context and context.pages:
                await take_screenshot(
                    context.pages[0],
                    logger,
                    "error_general",
                )
            return 1

        finally:
            if context:
                await context.close()


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=(
            "SmartIT: aplica filtros, descarga Asignados y En curso "
            "y consolida ambos CSV."
        )
    )
    command.add_argument(
        "--solo-panel",
        action="store_true",
        help="Solo abre el panel Filtrar para diagnóstico.",
    )
    command.add_argument(
        "--rutina",
        choices=[
            "ambos",
            "incidentes_asignados",
            "incidentes_en_curso",
        ],
        default="ambos",
        help="Selecciona qué reporte ejecutar.",
    )
    command.add_argument(
        "--sin-consolidar",
        action="store_true",
        help="No genera incidentes_activos.csv.",
    )
    command.add_argument(
        "--barrido-semanal",
        action="store_true",
        help=(
            "Divide el rango indicado en ventanas semanales, "
            "descarga Asignados y En curso y consolida todo."
        ),
    )
    command.add_argument(
        "--fecha-desde",
        help="Fecha inicial inclusiva en formato AAAA-MM-DD.",
    )
    command.add_argument(
        "--fecha-hasta",
        help="Fecha final inclusiva en formato AAAA-MM-DD.",
    )
    command.add_argument(
        "--dias-ventana",
        type=int,
        default=7,
        help="Tamaño de cada ventana. Por defecto: 7 días.",
    )
    command.add_argument(
        "--umbral-division",
        type=int,
        default=4900,
        help=(
            "Si una descarga alcanza este número de filas, "
            "la ventana se vuelve a descargar por días."
        ),
    )
    return command


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(main_async(parser().parse_args()))
    )
