# -*- coding: utf-8 -*-
# ATLAS_HELIX_WO_RUNTIME_MINIMAL_8023_V1
# Generado desde runtime productivo 8011.
# Contiene solo dependencias utilizadas por summary.py.

from __future__ import annotations

from dataclasses import dataclass

import logging

import os

import re

import time

import unicodedata

from dataclasses import asdict, dataclass

from datetime import datetime

from pathlib import Path

from typing import Any, Iterable

from urllib.parse import unquote

from playwright.async_api import (
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.services.helix.smartit_scraper import (  # noqa: E402
    Settings,
    any_visible_across_frames,
    find_optional_visible_across_frames,
    find_visible_across_frames,
    frame_description,
    iniciar_sesion,
    locator_visible,
    take_screenshot,
)

# ATLAS_HELIX_WO_SUMMARY_PATHS_8023_V1

RUNTIME_DATA_DIR = Path(r"C:\xampp\htdocs\rutinas_hogares\api_direccion_clientes\runtime\helix\data")

SERVICE_ROOT = Path(__file__).resolve().parents[4]

RUNTIME_DIR = SERVICE_ROOT / "runtime"

RUNTIME_DATA_DIR = RUNTIME_DIR / "data"

PROFILE_DIR = RUNTIME_DATA_DIR / "perfil_navegador"

SCREENSHOT_DIR = (
    RUNTIME_DIR
    / "screenshots"
    / "helix_wo_summary"
)

SCREENSHOT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

PROFILE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


PROFILE_DIR = RUNTIME_DATA_DIR / "perfil_navegador"

SEARCH_BUTTON_SELECTORS = [
    "#header-search_button",
    'a[ux-id="global-search-icon"]',
    'a[aria-label="Buscar"]',
]

SEARCH_INPUT_SELECTORS = [
    "#globalSearchBox",
    'input[ux-id="search-text"]',
    'input[placeholder="Pulse Entrar para buscar"]',
]

FULL_WO_SELECTORS = [
    'a:has-text("Ver orden de trabajo completa")',
    'button:has-text("Ver orden de trabajo completa")',
    '[role="link"]:has-text("Ver orden de trabajo completa")',
    '*:text-is("Ver orden de trabajo completa")',
    'a:has-text("View full work order")',
    'button:has-text("View full work order")',
    '[role="link"]:has-text("View full work order")',
    '*:text-is("View full work order")',
]

RELATED_TAB_SELECTORS = [
    'button[role="tab"]:has-text("Elementos relacionados")',
    '[role="tab"]:has-text("Elementos relacionados")',
    '[data-testid$="_tab_2"]:has-text("Elementos relacionados")',
    '[data-testid*="_tab_"]:has-text("Elementos relacionados")',
    'button:has-text("Elementos relacionados")',
    '*:text-is("Elementos relacionados")',
    'button[role="tab"]:has-text("Related items")',
    '[role="tab"]:has-text("Related items")',
    '[data-testid$="_tab_2"]:has-text("Related items")',
    '[data-testid*="_tab_"]:has-text("Related items")',
    'button:has-text("Related items")',
    '*:text-is("Related items")',
]

FULL_VIEW_DISCOVERY_TIMEOUT_MS = max(
    12000,
    int(os.getenv("HELIX_FULL_VIEW_TIMEOUT_MS", "35000")),
)

RELATED_TAB_TIMEOUT_MS = max(
    15000,
    int(os.getenv("HELIX_RELATED_TAB_TIMEOUT_MS", "45000")),
)

SEARCH_OUTCOME_TIMEOUT_MS = max(
    30000,
    int(os.getenv("HELIX_SEARCH_OUTCOME_TIMEOUT_MS", "30000")),
)

SEARCH_ATTEMPTS = max(1, int(os.getenv("HELIX_SEARCH_ATTEMPTS", "2")))

NO_RESULTS_STABLE_MS = max(1500, int(os.getenv("HELIX_NO_RESULTS_STABLE_MS", "2500")))

NO_RESULTS_SELECTORS = [
    '*:text-is("No se encontraron resultados")',
    'text="No se encontraron resultados"',
    '[aria-label="No se encontraron resultados"]',
    '*:text-is("No results found")',
    'text="No results found"',
    '[aria-label="No results found"]',
]

LOADERS = [
    ".ng-busy-default-wrapper",
    ".loader-container",
    ".loader-section",
    ".full-loading-wrap",
    "adapt-busy-backdrop",
]

SEARCH_TRANSITION_LOADERS = LOADERS + [
    '[aria-busy="true"]',
    '[role="progressbar"]',
    '[class*="spinner"]',
]

class WorkOrderNotFoundError(RuntimeError):
    """La búsqueda terminó correctamente, pero Helix no encontró la OT."""

# HELIX_RELATED_ITEM_DATACLASS_V1
@dataclass
class RelatedItem:
    id: str
    tipo_relacion: str = ""
    titulo: str = ""
    estado: str = ""
    usuario_asignado: str = ""
    grupo_asignado: str = ""
    crear_fecha: str = ""
    tipo_ticket: str = ""

def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()

async def screenshot(page: Page, logger: logging.Logger, name: str) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        logger.info("Captura: %s", path)
    except Exception as exc:
        logger.warning("No se pudo guardar captura %s: %s", name, exc)

async def wait_dom_condition(
    page: Page,
    predicate,
    timeout_ms: int,
    description: str,
    logger: logging.Logger,
    poll_ms: int = 300,
):
    """Espera una condición real del DOM; timeout solo como techo de seguridad."""
    deadline = time.monotonic() + timeout_ms / 1000
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = await predicate()
            if value:
                logger.info("Condición lista: %s", description)
                return value
        except Exception as exc:
            last_error = exc
        await page.wait_for_timeout(poll_ms)
    if last_error:
        logger.warning("Último error esperando %s: %s", description, last_error)
    raise PlaywrightTimeoutError(
        f"No se cumplió la condición: {description} dentro de {timeout_ms} ms."
    )

async def wait_no_loader(page: Page, timeout_ms: int, logger: logging.Logger) -> None:
    stable = 0
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        busy = await any_visible_across_frames(page, LOADERS)
        if busy:
            stable = 0
        else:
            stable += 1
            if stable >= 3:
                logger.info("Vista estable: no hay loaders visibles.")
                return
        await page.wait_for_timeout(400)
    logger.warning("La vista no confirmó estabilidad; se continúa con validación del DOM.")

async def find_exact_work_order_card(
    page: Page,
    work_order: str,
    timeout_ms: int,
    logger: logging.Logger,
) -> tuple[Frame, Locator, str]:
    """Encuentra la tarjeta cuyo ID visible es exactamente la WO solicitada.

    Evita seleccionar un INC cuyo texto descriptivo mencione la misma WO.
    """
    escaped = work_order.replace('"', '\\"')
    selectors = [
        (
            'div.results-panel__item-layout[role="link"]'
            ':has(i.icon-workorder)'
            f':has(div.search-item-layout__id span:text-is("{escaped}"))'
        ),
        (
            'xpath=//div[@role="link" and '
            'contains(concat(" ", normalize-space(@class), " "), '
            '" results-panel__item-layout ")]'
            '[.//i[contains(concat(" ", normalize-space(@class), " "), '
            '" icon-workorder ")]'
            ' and .//div[contains(@class,"search-item-layout__id")]'
            f'//span[normalize-space(.)="{escaped}"]]'
        ),
    ]

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in list(page.frames):
            for selector in selectors:
                try:
                    card = frame.locator(selector).first
                    if await locator_visible(card):
                        logger.info(
                            "Tarjeta exacta de la OT %s encontrada con %s en %s",
                            work_order,
                            selector,
                            frame_description(frame),
                        )
                        return frame, card, selector
                except Exception:
                    continue
        await page.wait_for_timeout(250)

    raise PlaywrightTimeoutError(
        f"No se encontró la tarjeta exacta tipo Work Order para {work_order}."
    )

async def click_global_search(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    query: str,
) -> None:
    _, button, selector = await find_visible_across_frames(
        page,
        SEARCH_BUTTON_SELECTORS,
        cfg.timeout_ms,
        logger,
        "lupa de búsqueda global",
    )
    logger.info("Abriendo búsqueda global con %s", selector)
    await button.click()

    async def submit_query(attempt: int) -> None:
        _, search_input, input_selector = await find_visible_across_frames(
            page,
            SEARCH_INPUT_SELECTORS,
            cfg.timeout_ms,
            logger,
            "input de búsqueda global",
        )
        if attempt > 1:
            logger.warning(
                "Reintentando búsqueda exacta de %s (%s/%s).",
                query,
                attempt,
                SEARCH_ATTEMPTS,
            )
        logger.info("Escribiendo %s en %s", query, input_selector)
        await search_input.fill("")
        await search_input.fill(query)
        await search_input.press("Enter")

    for attempt in range(1, SEARCH_ATTEMPTS + 1):
        await submit_query(attempt)

        deadline = time.monotonic() + SEARCH_OUTCOME_TIMEOUT_MS / 1000
        no_results_since: float | None = None
        no_results_context: tuple[Frame, str] | None = None
        retry_requested = False

        while time.monotonic() < deadline:
            try:
                frame, _, result_selector = await find_exact_work_order_card(
                    page,
                    query,
                    timeout_ms=550,
                    logger=logger,
                )
                logger.info(
                    "Resultado Work Order exacto visible para %s usando %s en %s",
                    query,
                    result_selector,
                    frame_description(frame),
                )
                logger.info("Búsqueda global completada. URL=%s", page.url)
                return
            except PlaywrightTimeoutError:
                pass

            no_results = await find_optional_visible_across_frames(
                page,
                NO_RESULTS_SELECTORS,
                timeout_ms=250,
            )

            if no_results:
                frame, _, no_result_selector = no_results

                # HELIX_SEARCH_FALSE_NOT_FOUND_TIMER_RESET_V1
                # "No se encontraron resultados" NO es definitivo mientras
                # SmartIT siga mostrando una transicion de carga. El periodo
                # de estabilidad empieza solamente cuando ya no hay busy.
                busy = await any_visible_across_frames(
                    page,
                    SEARCH_TRANSITION_LOADERS,
                )

                if busy:
                    if no_results_since is not None:
                        logger.info(
                            "SmartIT sigue cargando para %s; se reinicia el "
                            "temporizador de ausencia estable.",
                            query,
                        )
                    no_results_since = None
                    no_results_context = None
                    await page.wait_for_timeout(200)
                    continue

                if no_results_since is None:
                    no_results_since = time.monotonic()
                    no_results_context = (frame, no_result_selector)
                    logger.info(
                        "Mensaje sin resultados detectado para %s sin loader "
                        "visible; iniciando confirmacion de estabilidad.",
                        query,
                    )

                stable_ms = int((time.monotonic() - no_results_since) * 1000)
                query_in_url = (
                    "/search/" in page.url.lower()
                    and query.lower() in page.url.lower()
                )

                if (
                    stable_ms >= NO_RESULTS_STABLE_MS
                    and query_in_url
                ):
                    # Última oportunidad: una tarjeta puede aparecer justo después
                    # del mensaje temporal de "sin resultados".
                    try:
                        frame, _, result_selector = await find_exact_work_order_card(
                            page,
                            query,
                            timeout_ms=min(2500, SEARCH_OUTCOME_TIMEOUT_MS),
                            logger=logger,
                        )
                        logger.info(
                            "La tarjeta exacta apareció durante la confirmación final "
                            "para %s usando %s en %s",
                            query,
                            result_selector,
                            frame_description(frame),
                        )
                        logger.info("Búsqueda global completada. URL=%s", page.url)
                        return
                    except PlaywrightTimeoutError:
                        pass

                    if attempt < SEARCH_ATTEMPTS:
                        logger.warning(
                            "Helix mantuvo 'No se encontraron resultados' para %s "
                            "durante %sms. Se repetirá la búsqueda antes de "
                            "declarar la OT inexistente.",
                            query,
                            stable_ms,
                        )
                        retry_requested = True
                        break

                    frame_ctx, selector_ctx = no_results_context or (frame, no_result_selector)
                    logger.info(
                        "Helix confirmó de forma estable que no existen resultados "
                        "para %s usando %s en %s",
                        query,
                        selector_ctx,
                        frame_description(frame_ctx),
                    )
                    await screenshot(page, logger, "ot_no_encontrada")
                    raise WorkOrderNotFoundError(
                        f"Helix no encontró la orden de trabajo {query}."
                    )
            else:
                no_results_since = None
                no_results_context = None

            await page.wait_for_timeout(200)

        # RN_SEARCH_STABILITY_V1_LAST_CHANCE
        # SmartIT puede pintar la tarjeta exacta justo al vencer la ventana principal.
        # Antes de repetir la consulta o declarar timeout, damos una ultima oportunidad
        # exclusivamente a la tarjeta Work Order exacta (ID + icon-workorder).
        try:
            frame, _, result_selector = await find_exact_work_order_card(
                page,
                query,
                timeout_ms=3000,
                logger=logger,
            )
            logger.info(
                "Tarjeta Work Order exacta confirmada en comprobacion final para %s usando %s en %s",
                query,
                result_selector,
                frame_description(frame),
            )
            logger.info("Busqueda global completada. URL=%s", page.url)
            return
        except PlaywrightTimeoutError:
            pass

        if retry_requested:
            await page.wait_for_timeout(700)
            continue

        if attempt < SEARCH_ATTEMPTS:
            logger.warning(
                "Helix no confirmó todavía un resultado definitivo para %s. "
                "Se realizará un segundo intento.",
                query,
            )
            await page.wait_for_timeout(700)
            continue

    raise PlaywrightTimeoutError(
        f"Helix no confirmó resultado ni ausencia estable de resultados para {query} "
        f"después de {SEARCH_ATTEMPTS} intento(s)."
    )

async def adopt_latest_helix_page(
    page: Page,
    known_page_ids: set[int],
    logger: logging.Logger,
) -> Page:
    """Adopta una pagina nueva si Helix abrió el detalle fuera de la pestaña actual."""
    candidates = [
        item
        for item in page.context.pages
        if id(item) not in known_page_ids and not item.is_closed()
    ]
    if not candidates:
        return page

    candidate = candidates[-1]
    try:
        await candidate.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    logger.info("Helix abrió una pagina adicional; se continuará en URL=%s", candidate.url)
    return candidate

async def find_related_tab_once(
    page: Page,
) -> tuple[Frame, Locator, str] | None:
    """Busca la pestaña por selector y por texto normalizado, sin esperar 240 s."""
    for frame in list(page.frames):
        for selector in RELATED_TAB_SELECTORS:
            try:
                locator = frame.locator(selector).first
                if await locator_visible(locator):
                    return frame, locator, selector
            except Exception:
                continue

        # Fallback semántico para variantes de Adapt/AR System.
        try:
            candidates = frame.locator(
                '[role="tab"], button, a, [role="link"], [tabindex], adapt-tab, li'
            )
            count = min(await candidates.count(), 240)
            for index in range(count):
                locator = candidates.nth(index)
                try:
                    if not await locator_visible(locator):
                        continue
                    text = normalize_text(await locator.inner_text())
                    aria = normalize_text(await locator.get_attribute("aria-label"))
                    title = normalize_text(await locator.get_attribute("title"))
                    if any(
                        any(
                            token in value
                            for token in ("ELEMENTOS RELACIONADOS", "RELATED ITEMS")
                        )
                        for value in (text, aria, title)
                        if value
                    ):
                        return frame, locator, "fallback_texto_normalizado"
                except Exception:
                    continue
        except Exception:
            continue
    return None

async def find_full_work_order_link_once(
    page: Page,
) -> tuple[Frame, Locator, str] | None:
    """Encuentra el enlace de vista completa aunque esté renderizado tardíamente."""
    for frame in list(page.frames):
        for selector in FULL_WO_SELECTORS:
            try:
                locator = frame.locator(selector).first
                if await locator.count() <= 0:
                    continue
                if await locator_visible(locator):
                    return frame, locator, selector
            except Exception:
                continue

        try:
            candidates = frame.locator('a, button, [role="link"]')
            count = min(await candidates.count(), 240)
            for index in range(count):
                locator = candidates.nth(index)
                try:
                    text = normalize_text(await locator.inner_text())
                    aria = normalize_text(await locator.get_attribute("aria-label"))
                    title = normalize_text(await locator.get_attribute("title"))
                    if any(
                        any(
                            token in value
                            for token in (
                                "VER ORDEN DE TRABAJO COMPLETA",
                                "VIEW FULL WORK ORDER",
                            )
                        )
                        for value in (text, aria, title)
                        if value
                    ):
                        return frame, locator, "fallback_texto_normalizado"
                except Exception:
                    continue
        except Exception:
            continue
    return None

def preview_work_order_frame(page: Page) -> Frame | None:
    for frame in list(page.frames):
        decoded = unquote(frame.url or "").upper()
        if "TARGETFORM=WORK ORDER" in decoded and "CONTEXT=PREVIEW" in decoded:
            return frame
    return None

async def log_frame_dom_summary(
    page: Page,
    logger: logging.Logger,
    prefix: str,
) -> None:
    """Deja evidencia breve del DOM sin volcar notas ni datos sensibles completos."""
    summaries: list[dict[str, Any]] = []
    for index, frame in enumerate(list(page.frames)):
        item: dict[str, Any] = {
            "index": index,
            "name": frame.name or "",
            "url": frame.url or "",
            "title": "",
            "body_preview": "",
        }
        try:
            item["title"] = await frame.title()
        except Exception:
            pass
        try:
            body_text = await frame.locator("body").inner_text(timeout=2500)
            body_text = re.sub(r"\s+", " ", body_text).strip()
            item["body_preview"] = body_text[:700]
        except Exception:
            pass
        summaries.append(item)
    logger.error("%s DOM/frames: %s", prefix, summaries)

def build_direct_work_order_url(page: Page) -> str:
    """Obtiene la URL PWA de la WO y elimina únicamente el modo PREVIEW."""
    candidates: list[str] = []
    for frame in list(page.frames):
        decoded = unquote(frame.url or "")
        upper = decoded.upper()
        if "TARGETFORM=WORK ORDER" in upper and "TARGETID=" in upper:
            candidates.append(decoded)

    if not candidates:
        return ""

    # Preferimos la URL que ya no tiene PREVIEW; de lo contrario usamos la preview.
    candidates.sort(key=lambda value: "CONTEXT=PREVIEW" in value.upper())
    full_url = candidates[0]
    full_url = re.sub(r"(?i)&context=PREVIEW(?=&|$)", "", full_url)
    full_url = re.sub(r"(?i)(\?)context=PREVIEW&?", r"\1", full_url)
    full_url = re.sub(r"(?i)&rand=[^&]*", "", full_url)
    full_url = full_url.replace("?&", "?").rstrip("?&")
    separator = "&" if "?" in full_url else "?"
    return f"{full_url}{separator}rand={time.time_ns()}"

async def open_direct_pwa_work_order(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> Page | None:
    """Abre la ficha PWA como página superior para evitar el PREVIEW anidado de SmartIT."""
    full_url = build_direct_work_order_url(page)
    if not full_url:
        logger.warning("No se pudo construir URL PWA directa de la Work Order.")
        return None

    logger.warning("Abriendo Work Order directamente en una nueva página PWA: %s", full_url)
    detail_page = await page.context.new_page()
    try:
        await detail_page.goto(
            full_url,
            wait_until="domcontentloaded",
            timeout=min(cfg.timeout_ms, 45000),
        )
        await detail_page.bring_to_front()
        await wait_no_loader(detail_page, min(cfg.timeout_ms, 30000), logger)

        deadline = time.monotonic() + 60
        reloaded = False
        while time.monotonic() < deadline:
            related = await find_related_tab_once(detail_page)
            if related:
                frame, _, selector = related
                logger.info(
                    "Vista completa PWA directa confirmada con %s en %s",
                    selector,
                    frame_description(frame),
                )
                return detail_page

            # El formulario puede montar primero login-light/fake-route y luego reemplazarlo.
            if not reloaded and time.monotonic() > deadline - 35:
                logger.warning("La PWA directa aún no muestra pestañas; se recargará una vez.")
                await detail_page.reload(
                    wait_until="domcontentloaded",
                    timeout=min(cfg.timeout_ms, 45000),
                )
                await wait_no_loader(detail_page, min(cfg.timeout_ms, 30000), logger)
                reloaded = True
            await detail_page.wait_for_timeout(500)

        await screenshot(detail_page, logger, "pwa_directa_sin_elementos_relacionados")
        await log_frame_dom_summary(detail_page, logger, "PWA directa sin pestaña")
    except Exception as exc:
        logger.warning("La apertura directa de PWA falló: %s", exc)
        try:
            await screenshot(detail_page, logger, "pwa_directa_error")
            await log_frame_dom_summary(detail_page, logger, "Error PWA directa")
        except Exception:
            pass

    try:
        await detail_page.close()
    except Exception:
        pass
    return None

async def wait_for_complete_work_order_view(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> Page:
    """Confirma vista completa y usa PWA top-level cuando SmartIT retiene el PREVIEW."""
    timeout_ms = min(cfg.timeout_ms, FULL_VIEW_DISCOVERY_TIMEOUT_MS)
    deadline = time.monotonic() + timeout_ms / 1000
    known_page_ids = {id(item) for item in page.context.pages}
    click_attempted = False

    while time.monotonic() < deadline:
        page = await adopt_latest_helix_page(page, known_page_ids, logger)
        known_page_ids = {id(item) for item in page.context.pages}

        related = await find_related_tab_once(page)
        if related:
            frame, _, selector = related
            logger.info(
                "Vista completa confirmada por pestaña de relacionados usando %s en %s",
                selector,
                frame_description(frame),
            )
            return page

        full_link = await find_full_work_order_link_once(page)
        if full_link:
            frame, locator, selector = full_link
            logger.info("Abriendo orden completa con %s en %s", selector, frame_description(frame))
            pages_before = {id(item) for item in page.context.pages}
            try:
                await locator.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            try:
                await locator.click(timeout=10000)
            except Exception as exc:
                logger.warning("Click normal de vista completa falló: %s. Se probará force=True.", exc)
                await locator.click(force=True, timeout=10000)
            click_attempted = True
            await page.wait_for_timeout(900)
            page = await adopt_latest_helix_page(page, pages_before, logger)
            await wait_no_loader(page, min(cfg.timeout_ms, 30000), logger)
            continue

        await page.wait_for_timeout(350)

    # En el remoto, SmartIT conserva un PREVIEW sin botón, aunque ya expone targetId.
    # Abrimos esa misma ficha PWA como página superior dentro del mismo contexto/cookies.
    direct_page = await open_direct_pwa_work_order(page, cfg, logger)
    if direct_page:
        return direct_page

    await screenshot(page, logger, "wo_sin_vista_completa")
    await log_frame_dom_summary(page, logger, "Fallo vista completa")
    preview = preview_work_order_frame(page)
    if preview:
        raise PlaywrightTimeoutError(
            "La Work Order quedó en PREVIEW y la apertura PWA directa no mostró "
            "Elementos relacionados."
        )
    if click_attempted:
        raise PlaywrightTimeoutError(
            "Se hizo clic en 'Ver orden de trabajo completa', pero ni esa navegación "
            "ni la PWA directa mostraron Elementos relacionados/Related items."
        )
    raise PlaywrightTimeoutError(
        "Helix no confirmó la vista completa y la apertura PWA directa tampoco "
        "mostró Elementos relacionados/Related items."
    )

async def open_work_order(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    work_order: str,
) -> Page:
    frame, card, selector = await find_exact_work_order_card(
        page,
        work_order,
        cfg.timeout_ms,
        logger,
    )
    logger.info(
        "Abriendo tarjeta Work Order exacta %s usando %s en %s",
        work_order,
        selector,
        frame_description(frame),
    )
    pages_before = {id(item) for item in page.context.pages}
    await card.scroll_into_view_if_needed()
    await card.click()
    await page.wait_for_timeout(700)
    page = await adopt_latest_helix_page(page, pages_before, logger)
    await wait_no_loader(page, min(cfg.timeout_ms, 30000), logger)

    page = await wait_for_complete_work_order_view(page, cfg, logger)
    logger.info("OT abierta en vista completa: %s", work_order)
    await screenshot(page, logger, "ot_abierta")
    return page

async def table_headers(table: Locator) -> list[str]:
    values: list[str] = []
    headers = table.locator("thead th")
    count = await headers.count()
    for index in range(count):
        text = (await headers.nth(index).inner_text()).strip()
        values.append(re.sub(r"\s+", " ", text))
    return values

async def find_table_by_headers(
    page: Page,
    required_headers: Iterable[str | Iterable[str]],
    timeout_ms: int,
    logger: logging.Logger,
    description: str,
) -> tuple[Frame, Locator, list[str]]:
    required_groups: list[list[str]] = []
    printable_required: list[list[str]] = []
    for value in required_headers:
        values = [value] if isinstance(value, str) else list(value)
        normalized = [normalize_text(item) for item in values if normalize_text(item)]
        if normalized:
            required_groups.append(normalized)
            printable_required.append([str(item) for item in values])

    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        for frame in list(page.frames):
            try:
                tables = frame.locator('table[role="grid"], adapt-table table, table')
                count = await tables.count()
                for index in range(min(count, 60)):
                    table = tables.nth(index)
                    if not await locator_visible(table):
                        continue
                    headers = await table_headers(table)
                    normalized_headers = [normalize_text(item) for item in headers]
                    if all(
                        any(
                            any(alias in header for alias in aliases)
                            for header in normalized_headers
                        )
                        for aliases in required_groups
                    ):
                        logger.info(
                            "%s encontrada en %s. Encabezados=%s",
                            description,
                            frame_description(frame),
                            headers,
                        )
                        return frame, table, headers
            except Exception:
                continue
        await page.wait_for_timeout(350)

    raise PlaywrightTimeoutError(
        f"No se encontró {description} con encabezados alternativos {printable_required}."
    )

def canonical_header(header: str) -> str:
    normalized = normalize_text(header)
    # Helix puede renderizar la misma tabla en español o inglés y agrega
    # IDs/textos de accesibilidad como "Ordenar Sin ordenar" o "Sort Unsorted".
    aliases: list[tuple[str, tuple[str, ...]]] = [
        ("TIPO DE RELACION", ("TIPO DE RELACION", "RELATIONSHIP TYPE", "RELATION TYPE", "RELATIONSHIP")),
        ("USUARIO ASIGNADO", ("USUARIO ASIGNADO", "ASSIGNED USER", "ASSIGNEE", "ASSIGNED TO")),
        ("GRUPO ASIGNADO", ("GRUPO ASIGNADO", "ASSIGNED GROUP", "ASSIGNEE GROUP", "SUPPORT GROUP")),
        ("CREAR FECHA", ("CREAR FECHA", "CREATE DATE", "CREATED DATE", "CREATED")),
        ("TIPO TICKET", ("TIPO TICKET", "TICKET TYPE")),
        ("TITULO", ("TITULO", "TITLE")),
        ("ESTADO", ("ESTADO", "STATUS")),
        ("ID", (" ID", "ID ", " ID ", "ID")),
    ]
    for canonical, names in aliases:
        if any(name in normalized for name in names):
            return canonical
    return normalized

def map_row(headers: list[str], values: list[str]) -> dict[str, str]:
    row: dict[str, str] = {}
    for index, header in enumerate(headers):
        key = canonical_header(header)
        value = values[index].strip() if index < len(values) else ""
        row[key] = value
    return row

async def read_related_items(table: Locator, headers: list[str]) -> list[RelatedItem]:
    results: list[RelatedItem] = []
    rows = table.locator("tbody tr")
    count = await rows.count()
    for index in range(count):
        row = rows.nth(index)
        cells = row.locator("td")
        cell_count = await cells.count()
        values = [
            re.sub(r"\s+", " ", (await cells.nth(i).inner_text()).strip())
            for i in range(cell_count)
        ]
        data = map_row(headers, values)
        item_id = data.get("ID", "")
        if not item_id:
            links = row.locator("a")
            if await links.count():
                item_id = (await links.first.inner_text()).strip()
        if not normalize_text(item_id).startswith("INC"):
            continue
        results.append(
            RelatedItem(
                id=item_id,
                tipo_relacion=data.get("TIPO DE RELACION", ""),
                titulo=data.get("TITULO", ""),
                estado=data.get("ESTADO", ""),
                usuario_asignado=data.get("USUARIO ASIGNADO", ""),
                grupo_asignado=data.get("GRUPO ASIGNADO", ""),
                crear_fecha=data.get("CREAR FECHA", ""),
                tipo_ticket=data.get("TIPO TICKET", ""),
            )
        )
    return results

async def open_related_tab_and_read(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> tuple[Frame, Locator, list[str], list[RelatedItem]]:
    timeout_ms = min(cfg.timeout_ms, RELATED_TAB_TIMEOUT_MS)
    deadline = time.monotonic() + timeout_ms / 1000
    found: tuple[Frame, Locator, str] | None = None

    while time.monotonic() < deadline:
        found = await find_related_tab_once(page)
        if found:
            break
        await page.wait_for_timeout(350)

    if not found:
        await screenshot(page, logger, "elementos_relacionados_no_encontrado")
        frame_urls = [frame.url for frame in page.frames]
        logger.error("Frames sin pestaña de relacionados: %s", frame_urls)
        raise PlaywrightTimeoutError(
            "No se encontró pestaña Elementos relacionados/Related items después de "
            f"{timeout_ms} ms. Se guardó captura y listado de frames."
        )

    frame_tab, tab, selector = found
    logger.info(
        "Abriendo pestaña de relacionados con %s en %s",
        selector,
        frame_description(frame_tab),
    )
    try:
        await tab.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    try:
        await tab.click(timeout=10000)
    except Exception as exc:
        logger.warning("Click normal de Elementos relacionados falló: %s. Se probará force=True.", exc)
        await tab.click(force=True, timeout=10000)
    await wait_no_loader(page, min(cfg.timeout_ms, 30000), logger)

    frame, table, headers = await find_table_by_headers(
        page,
        [
            ("ID",),
            ("Tipo de relación", "Relationship type", "Relation type", "Relationship"),
            ("Título", "Title"),
        ],
        min(cfg.timeout_ms, 60000),
        logger,
        "tabla de elementos relacionados",
    )
    items = await read_related_items(table, headers)
    logger.info("Incidentes relacionados encontrados: %s", len(items))
    for item in items:
        logger.info(
            "  INC=%s | relación=%s | estado=%s | grupo=%s",
            item.id,
            item.tipo_relacion,
            item.estado,
            item.grupo_asignado,
        )
    return frame, table, headers, items

async def click_incident_in_table(
    table: Locator,
    incident_id: str,
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> None:
    link = table.locator("a", has_text=incident_id).first
    if not await locator_visible(link):
        raise RuntimeError(f"No se encontró el enlace del incidente {incident_id} en la tabla.")
    logger.info("Abriendo incidente relacionado: %s", incident_id)
    await link.click()
    await wait_no_loader(page, cfg.timeout_ms, logger)

    async def incident_loaded():
        if incident_id in page.url:
            return True
        found = await find_optional_visible_across_frames(
            page,
            [f'text="{incident_id}"', f'a:has-text("{incident_id}")'],
            timeout_ms=400,
        )
        return bool(found)

    await wait_dom_condition(
        page,
        incident_loaded,
        cfg.timeout_ms,
        f"detalle del incidente {incident_id}",
        logger,
    )
    await screenshot(page, logger, "incidente_abierto")
