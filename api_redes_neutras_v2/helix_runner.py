from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
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

ROOT_DIR = Path(__file__).resolve().parent

from smartit import (  # noqa: E402
    Settings,
    any_visible_across_frames,
    find_optional_visible_across_frames,
    find_visible_across_frames,
    frame_description,
    iniciar_sesion,
    locator_visible,
    take_screenshot,
)

BACKEND_DIR = ROOT_DIR
RUNTIME_DATA_DIR = Path(
    os.getenv(
        "HELIX_RUNTIME_DATA_DIR",
        r"C:\xampp\htdocs\rutinas_hogares\api_redes_neutras_v2\runtime\helix\data",
    )
)

RESULT_DIR = Path(
    os.getenv(
        "HELIX_RESULT_DIR",
        str(RUNTIME_DATA_DIR / "resultados"),
    )
)

LOG_DIR = Path(
    os.getenv(
        "HELIX_LOG_DIR",
        r"C:\xampp\htdocs\rutinas_hogares\api_redes_neutras_v2\logs\helix_redes_neutras",
    )
)

SCREENSHOT_DIR = Path(
    os.getenv(
        "HELIX_SCREENSHOT_DIR",
        str(BACKEND_DIR / "screenshots" / "helix_redes_neutras"),
    )
)

DOWNLOAD_DIR = Path(
    os.getenv(
        "HELIX_DOWNLOAD_DIR",
        str(RUNTIME_DATA_DIR / "descargas"),
    )
)

PROFILE_DIR = Path(
    os.getenv(
        "HELIX_PROFILE_DIR",
        str(RUNTIME_DATA_DIR / "perfil_navegador"),
    )
)

SMARTIT_SEARCH_URL = (
    "https://miasistencia360-smartit.claro.com.co/" "smartit/app/#/search/{work_order}"
)

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
ACTIVITY_TAB_SELECTORS = [
    'button[role="tab"]:has-text("Actividad")',
    '[role="tab"]:has-text("Actividad")',
    'button[role="tab"]:has-text("Activity")',
    '[role="tab"]:has-text("Activity")',
    '*:text-is("Activity")',
]
ACTIVITY_PANEL_SELECTORS = [
    'adapt-tab-panel[title="Actividad"][role="tabpanel"]',
    'adapt-tab-panel[title="Actividad"]',
    'adapt-tab-panel[title="Activity"][role="tabpanel"]',
    'adapt-tab-panel[title="Activity"]',
    '[role="tabpanel"][aria-label="Activity"]',
    '[role="tabpanel"][aria-label="Actividad"]',
]
ATTACHMENT_DISCOVERY_TIMEOUT_MS = max(
    5000,
    int(os.getenv("HELIX_ATTACHMENTS_TIMEOUT_MS", "8000")),
)
ATTACHMENT_DISCOVERY_ATTEMPTS = max(
    2,
    int(os.getenv("HELIX_ATTACHMENTS_ATTEMPTS", "3")),
)
ATTACHMENT_DISCOVERY_RETRY_DELAY_MS = max(
    500,
    int(os.getenv("HELIX_ATTACHMENTS_RETRY_DELAY_MS", "2000")),
)
ACTIVITY_DISCOVERY_TIMEOUT_MS = max(
    2500,
    int(os.getenv("HELIX_ACTIVITY_ATTACHMENTS_TIMEOUT_MS", "5000")),
)
# RN_SEARCH_STABILITY_V1
# Prueba temporal: dar hasta 5 minutos a Helix y hacer un solo intento.
# Se dejan fijos para que un .env antiguo no vuelva a imponer 2 intentos.
SEARCH_OUTCOME_TIMEOUT_MS = 300000
SEARCH_ATTEMPTS = 1
NO_RESULTS_STABLE_MS = max(1500, int(os.getenv("HELIX_NO_RESULTS_STABLE_MS", "2500")))
NO_RESULTS_SELECTORS = [
    '*:text-is("No se encontraron resultados")',
    'text="No se encontraron resultados"',
    '[aria-label="No se encontraron resultados"]',
    '*:text-is("No results found")',
    'text="No results found"',
    '[aria-label="No results found"]',
]
FILE_NAME_PATTERN = re.compile(
    r"(?i)([^\\/:*?<>|\r\n]{1,180}\.(?:pdf|docx?|xlsx?|xlsm|csv|txt|zip|rar|7z|png|jpe?g|gif|bmp|tiff?|pptx?|msg|eml))"
)

LOADERS = [
    ".ng-busy-default-wrapper",
    ".loader-container",
    ".loader-section",
    ".full-loading-wrap",
    "adapt-busy-backdrop",
]

# HELIX_SEARCH_FALSE_NOT_FOUND_BUSY_GUARD_V1
# SmartIT puede mantener "No se encontraron resultados" visible mientras
# la busqueda global aun esta procesando. Estos selectores adicionales se
# usan SOLO para decidir si la ausencia ya puede considerarse estable.
SEARCH_TRANSITION_LOADERS = LOADERS + [
    '[aria-busy="true"]',
    '[role="progressbar"]',
    '[class*="spinner"]',
]


class WorkOrderNotFoundError(RuntimeError):
    """La búsqueda terminó correctamente, pero Helix no encontró la OT."""


# RN_WO_FIRST_STOP_V1
class WorkOrderDocumentationResolved(RuntimeError):
    """Control interno: la WO ya tenia documentacion; no navegar al INC."""


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


@dataclass
class AttachmentItem:
    indice: int
    nombre: str
    origen: str = ""
    registro_actividad: int = 0
    descargado: bool = False
    ruta_local: str = ""
    error: str = ""


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def setup_logger(worker_id: int = 1) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / (
        f"helix_redes_neutras_w{worker_id}_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
    )
    logger = logging.getLogger(f"helix_redes_neutras.worker_{worker_id}")
    logger.setLevel(logging.INFO)
    # Cerrar handlers de ejecuciones anteriores del mismo slot para no filtrar
    # descriptores de archivo durante pruebas de carga prolongadas.
    for old_handler in list(logger.handlers):
        try:
            old_handler.close()
        except Exception:
            pass
        logger.removeHandler(old_handler)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    for handler in (
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.info("Log: %s", log_path)
    return logger


async def screenshot(page: Page, logger: logging.Logger, name: str) -> None:
    # Screenshots deshabilitados.
    return


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
    logger.warning(
        "La vista no confirmó estabilidad; se continúa con validación del DOM."
    )


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
            ":has(i.icon-workorder)"
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


async def abrir_busqueda_directa_wo(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    work_order: str,
) -> bool:
    """Abre la ruta de búsqueda de la WO sin recargar SmartIT.

    Cambia únicamente el hash de la SPA ya autenticada. Así evitamos
    lupa -> input -> Enter y, al mismo tiempo, conservamos el estado de
    sesión que SmartIT necesita para abrir correctamente la Work Order.

    Si la tarjeta exacta no aparece, devuelve False para usar el flujo
    tradicional como respaldo.
    """
    logger.info(
        "Abriendo busqueda directa SPA de WO: %s",
        work_order,
    )

    try:
        # No usar page.goto() aquí: recargar SmartIT después del login puede
        # dejar la Work Order en PREVIEW. Solo cambiamos la ruta/hash interna
        # de la SPA autenticada.
        await page.evaluate(
            """
            (wo) => {
                window.location.hash = '/search/' + wo;
            }
            """,
            work_order,
        )

        await page.wait_for_timeout(1500)

        frame, _, selector = await find_exact_work_order_card(
            page,
            work_order,
            timeout_ms=SEARCH_OUTCOME_TIMEOUT_MS,
            logger=logger,
        )

        logger.info(
            "Busqueda directa SPA lista para %s usando %s en %s",
            work_order,
            selector,
            frame_description(frame),
        )
        logger.info("URL actual SmartIT: %s", page.url)
        return True

    except PlaywrightTimeoutError:
        logger.warning(
            "Busqueda directa SPA no encontro %s. " "Se usara buscador tradicional.",
            work_order,
        )
        return False

    except Exception as exc:
        logger.warning(
            "Error usando busqueda directa SPA para %s: %s",
            work_order,
            exc,
        )
        return False


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
                    "/search/" in page.url.lower() and query.lower() in page.url.lower()
                )

                if stable_ms >= NO_RESULTS_STABLE_MS and query_in_url:
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

                    frame_ctx, selector_ctx = no_results_context or (
                        frame,
                        no_result_selector,
                    )
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


async def click_exact_text_across_frames(
    page: Page,
    texts: Iterable[str],
    timeout_ms: int,
    logger: logging.Logger,
    description: str,
) -> tuple[Frame, Locator]:
    selectors: list[str] = []
    for text in texts:
        escaped = text.replace('"', '\\"')
        selectors.extend(
            [
                f'a:has-text("{escaped}")',
                f'button:has-text("{escaped}")',
                f'[role="link"]:has-text("{escaped}")',
            ]
        )
    frame, locator, selector = await find_visible_across_frames(
        page,
        selectors,
        timeout_ms,
        logger,
        description,
    )
    logger.info(
        "Click %s usando %s en %s", description, selector, frame_description(frame)
    )
    await locator.click()
    return frame, locator


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
    logger.info(
        "Helix abrió una pagina adicional; se continuará en URL=%s", candidate.url
    )
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


def full_work_order_frame(page: Page) -> Frame | None:
    """Reconoce la vista completa por URL, diferenciándola de context=PREVIEW."""
    for frame in list(page.frames):
        decoded = unquote(frame.url or "").upper()
        if (
            "TARGETFORM=WORK ORDER" in decoded
            and "MODE=GET" in decoded
            and "CONTEXT=PREVIEW" not in decoded
        ):
            return frame
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

    logger.warning(
        "Abriendo Work Order directamente en una nueva página PWA: %s", full_url
    )
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
                logger.warning(
                    "La PWA directa aún no muestra pestañas; se recargará una vez."
                )
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
            logger.info(
                "Abriendo orden completa con %s en %s",
                selector,
                frame_description(frame),
            )
            pages_before = {id(item) for item in page.context.pages}
            try:
                await locator.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            try:
                await locator.click(timeout=10000)
            except Exception as exc:
                logger.warning(
                    "Click normal de vista completa falló: %s. Se probará force=True.",
                    exc,
                )
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
        (
            "TIPO DE RELACION",
            ("TIPO DE RELACION", "RELATIONSHIP TYPE", "RELATION TYPE", "RELATIONSHIP"),
        ),
        (
            "USUARIO ASIGNADO",
            ("USUARIO ASIGNADO", "ASSIGNED USER", "ASSIGNEE", "ASSIGNED TO"),
        ),
        (
            "GRUPO ASIGNADO",
            ("GRUPO ASIGNADO", "ASSIGNED GROUP", "ASSIGNEE GROUP", "SUPPORT GROUP"),
        ),
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
        logger.warning(
            "Click normal de Elementos relacionados falló: %s. Se probará force=True.",
            exc,
        )
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
        raise RuntimeError(
            f"No se encontró el enlace del incidente {incident_id} en la tabla."
        )
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


async def read_attachments(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> tuple[Frame, Locator, list[AttachmentItem], bool]:
    """Lee adjuntos sin convertir una carga lenta en un falso "sin documentos".

    Retorna: frame, tabla, adjuntos, validacion_confirmada.
    """
    attachment_timeout_ms = min(cfg.timeout_ms, ATTACHMENT_DISCOVERY_TIMEOUT_MS)
    fallback_frame = page.main_frame
    fallback_table = page.locator("body")
    stable_passes = 0
    last_error: Exception | None = None

    for attempt in range(1, ATTACHMENT_DISCOVERY_ATTEMPTS + 1):
        logger.info(
            "Validando Adjuntos/Attachments intento %s/%s (hasta %.1f s).",
            attempt,
            ATTACHMENT_DISCOVERY_ATTEMPTS,
            attachment_timeout_ms / 1000,
        )

        await wait_no_loader(page, min(cfg.timeout_ms, 12000), logger)

        try:
            frame, table, _ = await find_table_by_headers(
                page,
                [("Adjunto", "Attachment")],
                attachment_timeout_ms,
                logger,
                "tabla de Adjuntos/Attachments",
            )
            fallback_frame = frame
            fallback_table = table

            attachments: list[AttachmentItem] = []
            rows = table.locator("tbody tr")
            count = await rows.count()

            for index in range(count):
                row = rows.nth(index)
                links = row.locator("a")
                link_count = await links.count()
                for link_index in range(link_count):
                    try:
                        name = re.sub(
                            r"\s+",
                            " ",
                            (await links.nth(link_index).inner_text()).strip(),
                        )
                    except Exception:
                        name = ""
                    if name:
                        attachments.append(
                            AttachmentItem(
                                indice=len(attachments) + 1,
                                nombre=name,
                                origen="TABLA_ADJUNTO",
                            )
                        )

            if attachments:
                logger.info("EVENTO_SSH_PENDIENTE: TABLA_ADJUNTOS_DETECTADA")
                logger.info(
                    "Adjuntos confirmados en intento %s: %s",
                    attempt,
                    len(attachments),
                )
                for item in attachments:
                    logger.info("  [%s] %s", item.indice, item.nombre)
                return frame, table, attachments, True

            stable_passes += 1
            logger.info(
                "Tabla de Adjuntos visible pero sin enlaces en intento %s/%s.",
                attempt,
                ATTACHMENT_DISCOVERY_ATTEMPTS,
            )

        except PlaywrightTimeoutError as exc:
            last_error = exc
            try:
                busy = await any_visible_across_frames(page, SEARCH_TRANSITION_LOADERS)
            except Exception:
                busy = True

            if not busy:
                stable_passes += 1

            logger.info(
                "Tabla de Adjuntos aun no visible en intento %s/%s (busy=%s).",
                attempt,
                ATTACHMENT_DISCOVERY_ATTEMPTS,
                busy,
            )

        except Exception as exc:
            last_error = exc
            logger.warning(
                "Error validando Adjuntos en intento %s/%s: %s",
                attempt,
                ATTACHMENT_DISCOVERY_ATTEMPTS,
                exc,
            )

        if attempt < ATTACHMENT_DISCOVERY_ATTEMPTS:
            await page.wait_for_timeout(ATTACHMENT_DISCOVERY_RETRY_DELAY_MS)

    confirmed_absence = stable_passes >= 2
    if confirmed_absence:
        logger.info(
            "Ausencia de Adjuntos confirmada despues de %s validaciones.",
            ATTACHMENT_DISCOVERY_ATTEMPTS,
        )
    else:
        logger.warning(
            "Validacion de Adjuntos inconclusa; no se declarara SIN_DOCUMENTOS. "
            "Ultimo error=%s",
            last_error,
        )

    return fallback_frame, fallback_table, [], confirmed_absence


def attachment_key(item: AttachmentItem) -> str:
    return normalize_text(item.nombre)


def merge_attachments(*groups: list[AttachmentItem]) -> list[AttachmentItem]:
    merged: list[AttachmentItem] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = attachment_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            item.indice = len(merged) + 1
            merged.append(item)
    return merged


async def ensure_activity_panel(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> tuple[Frame, Locator] | None:
    panel = await find_optional_visible_across_frames(
        page,
        ACTIVITY_PANEL_SELECTORS,
        timeout_ms=1200,
    )
    if panel:
        frame, locator, _ = panel
        return frame, locator

    tab = await find_optional_visible_across_frames(
        page,
        ACTIVITY_TAB_SELECTORS,
        timeout_ms=1500,
    )
    if tab:
        _, locator, selector = tab
        logger.info("Abriendo pestaña Actividad/Activity con %s", selector)
        await locator.click()
        await wait_no_loader(page, min(cfg.timeout_ms, 8000), logger)
        panel = await find_optional_visible_across_frames(
            page,
            ACTIVITY_PANEL_SELECTORS,
            timeout_ms=2500,
        )
        if panel:
            frame, locator, _ = panel
            return frame, locator

    logger.info(
        "No se encontró el panel Actividad/Activity; se continúa sin revisar notas."
    )
    return None


async def stable_activity_iframes(
    page: Page,
    panel: Locator,
    timeout_ms: int,
    logger: logging.Logger,
) -> Locator:
    frames = panel.locator(
        "div.records iframe, "
        'iframe[title="Campo de visualización"], '
        'iframe[title="Display field"], '
        'iframe[title*="display" i], '
        "iframe"
    )
    deadline = time.monotonic() + timeout_ms / 1000
    last_count = -1
    stable = 0
    while time.monotonic() < deadline:
        try:
            count = await frames.count()
        except Exception:
            count = 0
        if count == last_count:
            stable += 1
        else:
            last_count = count
            stable = 0
        if stable >= 3:
            logger.info("Registros de Actividad estabilizados: %s iframe(s).", count)
            return frames
        await page.wait_for_timeout(250)
    logger.info(
        "Registros de Actividad detectados al vencer espera: %s iframe(s).",
        max(last_count, 0),
    )
    return frames


async def extract_activity_frame_attachments(
    activity_frame: Frame,
    record_index: int,
    logger: logging.Logger,
) -> list[AttachmentItem]:
    found: list[AttachmentItem] = []
    candidates = activity_frame.locator(
        'a, button, [role="link"], [download], [title], [aria-label]'
    )
    try:
        count = min(await candidates.count(), 120)
    except Exception:
        return found

    for index in range(count):
        element = candidates.nth(index)
        try:
            tag = (await element.evaluate("el => el.tagName || ''")).upper()
            try:
                text_value = (await element.inner_text()).strip()
            except Exception:
                text_value = ""
            title = (await element.get_attribute("title")) or ""
            aria = (await element.get_attribute("aria-label")) or ""
            href = (await element.get_attribute("href")) or ""
            download = (await element.get_attribute("download")) or ""
            class_name = (await element.get_attribute("class")) or ""
            role = (await element.get_attribute("role")) or ""
        except Exception:
            continue

        raw = " ".join([text_value, title, aria, href, download, class_name])
        normalized = normalize_text(raw)
        if any(
            blocked in normalized
            for blocked in (
                "ADJUNTAR ARCHIVOS",
                "ARRASTRAR Y COLOCAR",
                "FILE CONTROL",
                "UPLOAD",
            )
        ):
            continue

        actionable = (
            tag in {"A", "BUTTON"}
            or normalize_text(role) == "LINK"
            or bool(download)
            or bool(href)
        )
        if not actionable:
            continue

        names = [
            re.sub(r"\s+", " ", match).strip(" .,:;()[]{}")
            for match in FILE_NAME_PATTERN.findall(raw)
        ]
        marker = any(
            token in normalized
            for token in (
                "DESCARGAR",
                "DOWNLOAD",
                "ATTACHMENT",
                "ADJUNTO",
                "DOCUMENTO",
            )
        ) or any(
            token in normalize_text(class_name)
            for token in ("PAPERCLIP", "ATTACH", "DOWNLOAD")
        )

        if names:
            for name in names:
                found.append(
                    AttachmentItem(
                        indice=0,
                        nombre=name,
                        origen="ACTIVIDAD",
                        registro_actividad=record_index,
                    )
                )
        elif marker:
            found.append(
                AttachmentItem(
                    indice=0,
                    nombre=f"Documento adjunto (actividad {record_index})",
                    origen="ACTIVIDAD",
                    registro_actividad=record_index,
                )
            )

    if found:
        logger.info(
            "Actividad %s: %s posible(s) documento(s) adjunto(s).",
            record_index,
            len(found),
        )
    return found


async def read_activity_attachments(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> tuple[list[AttachmentItem], int]:
    panel_data = await ensure_activity_panel(page, cfg, logger)
    if not panel_data:
        return [], 0

    _, panel = panel_data
    logger.info(
        "Revisando Actividad únicamente para validar documentos adjuntos "
        "(máximo %.1f segundos).",
        ACTIVITY_DISCOVERY_TIMEOUT_MS / 1000,
    )
    iframe_locators = await stable_activity_iframes(
        page,
        panel,
        ACTIVITY_DISCOVERY_TIMEOUT_MS,
        logger,
    )
    try:
        count = min(await iframe_locators.count(), 100)
    except Exception:
        count = 0

    attachments: list[AttachmentItem] = []
    for index in range(count):
        iframe = iframe_locators.nth(index)
        try:
            await iframe.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        try:
            handle = await iframe.element_handle()
            child_frame = await handle.content_frame() if handle else None
        except Exception:
            child_frame = None
        if child_frame is None:
            continue

        try:
            await child_frame.locator("body").wait_for(state="attached", timeout=1200)
        except Exception:
            pass
        attachments.extend(
            await extract_activity_frame_attachments(
                child_frame,
                index + 1,
                logger,
            )
        )

    attachments = merge_attachments(attachments)
    logger.info(
        "Validación de Actividad completada: registros=%s | documentos=%s.",
        count,
        len(attachments),
    )
    return attachments, count


# RN_INC_NOTE_ATTACHMENTS_V1_2
async def extract_note_frame_attachments(
    note_frame: Frame,
    record_index: int,
    logger: logging.Logger,
) -> list[AttachmentItem]:
    """
    Detecta archivos embebidos dentro de una Nota/WorkLog.

    Esta funcion es independiente de read_attachments(), que sigue
    manejando exclusivamente la tabla Adjuntos del ticket.
    """
    found: list[AttachmentItem] = []
    seen: set[str] = set()

    blocks = note_frame.locator(
        "#attachment-block .attachment_div, "
        ".attachment_div, "
        "#first-attachment, "
        "#second-attachment, "
        "#third-attachment"
    )

    try:
        count = min(await blocks.count(), 30)
    except Exception:
        count = 0

    for index in range(count):
        block = blocks.nth(index)

        try:
            try:
                text_value = (await block.inner_text()).strip()
            except Exception:
                text_value = ""

            title = (await block.get_attribute("title")) or ""
            aria = (await block.get_attribute("aria-label")) or ""

            data_name = (
                (await block.get_attribute("data-filename"))
                or (await block.get_attribute("data-file-name"))
                or ""
            )

            raw = " ".join(
                [
                    text_value,
                    title,
                    aria,
                    data_name,
                ]
            )

        except Exception:
            continue

        names = [
            re.sub(r"\s+", " ", match).strip(" .,:;()[]{}")
            for match in FILE_NAME_PATTERN.findall(raw)
        ]

        for name in names:
            key = normalize_text(name)

            if not key or key in seen:
                continue

            seen.add(key)

            found.append(
                AttachmentItem(
                    indice=0,
                    nombre=name,
                    origen="NOTA_ACTIVIDAD",
                    registro_actividad=record_index,
                )
            )

    # SmartIT tambi?n puede guardar el nombre ?nicamente en variables:
    #
    # attachmentName1 = `EVIDENCIA (84).docx`
    #
    # Por eso inspeccionamos el HTML de la nota como fallback.
    try:
        html = await note_frame.content()
    except Exception:
        html = ""

    if html:
        js_pattern = re.compile(r"""(?ix)
            attachmentName\d+
            \s*=\s*
            [`"']
            (
                [^`"']+
                \.
                (?:
                    pdf|docx?|xlsx?|xlsm|csv|txt|
                    zip|rar|7z|png|jpe?g|gif|bmp|
                    tiff?|pptx?|msg|eml
                )
            )
            [`"']
            """)

        for name in js_pattern.findall(html):
            name = re.sub(r"\s+", " ", name).strip()
            key = normalize_text(name)

            if not key or key in seen:
                continue

            seen.add(key)

            found.append(
                AttachmentItem(
                    indice=0,
                    nombre=name,
                    origen="NOTA_ACTIVIDAD",
                    registro_actividad=record_index,
                )
            )

    if found:
        logger.info(
            "RN_INC_NOTE_ATTACHMENTS_V1_2: " "nota=%s | documentos=%s | nombres=%s",
            record_index,
            len(found),
            [item.nombre for item in found],
        )

    return found


# RN_ACTIVITY_PAGINATION_V1_1
async def activity_pagination_state(
    panel: Locator,
) -> dict[str, int]:
    result = {
        "desde": 0,
        "hasta": 0,
        "total": 0,
        "pagina": 1,
        "tamano_pagina": 0,
    }

    try:
        text = re.sub(
            r"\s+",
            " ",
            await panel.inner_text(),
        ).strip()
    except Exception:
        text = ""

    patterns = [
        r"(?i)Registros\s+desde\s+(\d+)\s+hasta\s+(\d+)\s+de\s+(\d+)",
        r"(?i)Records?\s+from\s+(\d+)\s+to\s+(\d+)\s+of\s+(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        desde = int(match.group(1))
        hasta = int(match.group(2))
        total = int(match.group(3))

        # SmartIT usa normalmente 15 por pagina.
        # Para la ultima pagina no usamos su longitud para inferir
        # incorrectamente el numero de pagina.
        page_size = 15

        pagina = ((desde - 1) // page_size) + 1 if desde > 0 else 1

        result.update(
            {
                "desde": desde,
                "hasta": hasta,
                "total": total,
                "pagina": pagina,
                "tamano_pagina": page_size,
            }
        )

        break

    return result


# RN_ACTIVITY_PAGINATION_V1_1
async def goto_activity_page(
    page: Page,
    panel: Locator,
    target_page: int,
    logger: logging.Logger,
) -> bool:

    target_page = max(
        1,
        int(target_page or 1),
    )

    state = await activity_pagination_state(panel)

    if int(state.get("pagina") or 1) == target_page:
        return True

    nav = panel.locator('nav[aria-label="Page navigation"]').first

    button = (
        nav.locator("button.page-link")
        .filter(has_text=re.compile(rf"^\s*{target_page}\s*$"))
        .first
    )

    try:
        if await button.count() <= 0:
            logger.warning(
                "RN_ACTIVITY_PAGINATION_V1_1: " "boton pagina=%s no encontrado.",
                target_page,
            )
            return False

        await button.click()

    except Exception as exc:
        logger.warning(
            "RN_ACTIVITY_PAGINATION_V1_1: " "fallo click pagina=%s | %s",
            target_page,
            exc,
        )
        return False

    deadline = time.monotonic() + 10.0

    while time.monotonic() < deadline:

        await page.wait_for_timeout(250)

        new_state = await activity_pagination_state(panel)

        if int(new_state.get("pagina") or 0) == target_page:

            logger.info(
                "RN_ACTIVITY_PAGINATION_V1_1: " "pagina activa=%s | rango=%s-%s de %s",
                target_page,
                new_state.get("desde"),
                new_state.get("hasta"),
                new_state.get("total"),
            )

            return True

    logger.warning(
        "RN_ACTIVITY_PAGINATION_V1_1: " "timeout confirmando pagina=%s.",
        target_page,
    )

    return False


# RN_INC_NOTE_ATTACHMENTS_V1_2
# RN_ACTIVITY_PAGINATION_V1_1
async def read_note_attachments(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> tuple[list[AttachmentItem], int]:

    panel_data = await ensure_activity_panel(
        page,
        cfg,
        logger,
    )

    if not panel_data:
        logger.info(
            "RN_INC_NOTE_ATTACHMENTS_V1_2: " "panel Actividad/Activity no disponible."
        )
        return [], 0

    _, panel = panel_data

    state = await activity_pagination_state(panel)

    logger.info(
        "RN_ACTIVITY_PAGINATION_V1_1: "
        "estado inicial | pagina=%s | rango=%s-%s de %s",
        state.get("pagina"),
        state.get("desde"),
        state.get("hasta"),
        state.get("total"),
    )

    # Siempre arrancar en pagina 1.
    if not await goto_activity_page(
        page,
        panel,
        1,
        logger,
    ):
        logger.warning(
            "RN_ACTIVITY_PAGINATION_V1_1: " "no fue posible posicionar pagina 1."
        )

    attachments: list[AttachmentItem] = []

    total_records = 0
    page_number = 1

    while page_number <= 50:

        if page_number > 1:
            moved = await goto_activity_page(
                page,
                panel,
                page_number,
                logger,
            )

            if not moved:
                break

        await wait_no_loader(
            page,
            min(cfg.timeout_ms, 8000),
            logger,
        )

        iframe_locators = await stable_activity_iframes(
            page,
            panel,
            ACTIVITY_DISCOVERY_TIMEOUT_MS,
            logger,
        )

        try:
            count = min(
                await iframe_locators.count(),
                100,
            )
        except Exception:
            count = 0

        state = await activity_pagination_state(panel)

        logger.info(
            "RN_ACTIVITY_PAGINATION_V1_1: "
            "procesando pagina=%s | "
            "rango=%s-%s de %s | iframes=%s",
            page_number,
            state.get("desde"),
            state.get("hasta"),
            state.get("total"),
            count,
        )

        total_records += count

        for index in range(count):

            iframe = iframe_locators.nth(index)

            try:
                await iframe.scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass

            try:
                handle = await iframe.element_handle()

                child_frame = await handle.content_frame() if handle else None

            except Exception:
                child_frame = None

            if child_frame is None:
                continue

            try:
                await child_frame.locator("body").wait_for(
                    state="attached",
                    timeout=1200,
                )
            except Exception:
                pass

            page_items = await extract_note_frame_attachments(
                child_frame,
                index + 1,
                logger,
            )

            for item in page_items:
                item.origen = "NOTA_ACTIVIDAD_PAGINA_" f"{page_number}"

            attachments.extend(page_items)

        state = await activity_pagination_state(panel)

        hasta = int(state.get("hasta") or 0)

        total = int(state.get("total") or 0)

        if total <= 0 or hasta <= 0 or hasta >= total:
            break

        page_number += 1

    attachments = merge_attachments(attachments)

    logger.info(
        "RN_ACTIVITY_PAGINATION_V1_1: "
        "revision completa | registros=%s | "
        "documentos=%s | paginas=%s",
        total_records,
        len(attachments),
        page_number,
    )

    logger.info(
        "RN_INC_NOTE_ATTACHMENTS_V1_2: " "revision completa | notas=%s | documentos=%s",
        total_records,
        len(attachments),
    )

    return attachments, total_records


# RN_INC_NOTE_ATTACHMENTS_V1_2
async def download_note_attachment(
    page: Page,
    cfg: Settings,
    item: AttachmentItem,
    timeout_ms: int,
    logger: logging.Logger,
    work_order: str,
    incident_id: str,
) -> AttachmentItem:
    """
    Descarga un archivo desde la Nota/WorkLog donde fue detectado.
    """
    record_index = int(item.registro_actividad or 0)

    if record_index <= 0:
        item.error = "Documento de nota sin registro_actividad."
        return item

    panel_data = await ensure_activity_panel(
        page,
        cfg,
        logger,
    )

    if not panel_data:
        item.error = "No se encontro panel Actividad/Activity."
        return item

    _, panel = panel_data

    # RN_ACTIVITY_PAGINATION_V1_1
    activity_page = 1

    page_match = re.search(
        r"NOTA_ACTIVIDAD_PAGINA_(\d+)",
        str(item.origen or ""),
        flags=re.IGNORECASE,
    )

    if page_match:
        activity_page = int(page_match.group(1))

    moved = await goto_activity_page(
        page,
        panel,
        activity_page,
        logger,
    )

    if not moved:
        item.error = "No se pudo abrir pagina " f"{activity_page} de Actividad."
        return item

    await wait_no_loader(
        page,
        min(cfg.timeout_ms, 8000),
        logger,
    )

    logger.info(
        "RN_ACTIVITY_PAGINATION_V1_1: "
        "descarga | pagina=%s | "
        "registro_local=%s | archivo=%s",
        activity_page,
        record_index,
        item.nombre,
    )
    # RN_ACTIVITY_PAGINATION_V1_2
    #
    # SmartIT puede reconstruir el panel/iframes después de una descarga.
    # No reutilizamos ciegamente el Locator anterior.
    iframe_locators = None

    for rn_attempt in range(1, 3):

        logger.info(
            "RN_ACTIVITY_PAGINATION_V1_2: "
            "iframe discovery inicio | intento=%s | "
            "pagina=%s | registro_local=%s | archivo=%s",
            rn_attempt,
            activity_page,
            record_index,
            item.nombre,
        )

        # Re-adquirir el panel porque Angular puede haber
        # reconstruido el DOM después de la descarga anterior.
        fresh_panel_data = await ensure_activity_panel(
            page,
            cfg,
            logger,
        )

        if not fresh_panel_data:
            logger.warning(
                "RN_ACTIVITY_PAGINATION_V1_2: "
                "panel Activity no disponible | intento=%s",
                rn_attempt,
            )
            continue

        _, panel = fresh_panel_data

        # Verificar nuevamente la página actual.
        moved = await goto_activity_page(
            page,
            panel,
            activity_page,
            logger,
        )

        if not moved:
            logger.warning(
                "RN_ACTIVITY_PAGINATION_V1_2: "
                "no se pudo reposicionar pagina=%s | intento=%s",
                activity_page,
                rn_attempt,
            )
            continue

        try:
            iframe_locators = await asyncio.wait_for(
                stable_activity_iframes(
                    page,
                    panel,
                    ACTIVITY_DISCOVERY_TIMEOUT_MS,
                    logger,
                ),
                timeout=10.0,
            )

            logger.info(
                "RN_ACTIVITY_PAGINATION_V1_2: "
                "iframe discovery OK | intento=%s | pagina=%s",
                rn_attempt,
                activity_page,
            )

            break

        except asyncio.TimeoutError:
            logger.warning(
                "RN_ACTIVITY_PAGINATION_V1_2: "
                "iframe discovery TIMEOUT | intento=%s | pagina=%s",
                rn_attempt,
                activity_page,
            )

        except Exception as rn_exc:
            logger.warning(
                "RN_ACTIVITY_PAGINATION_V1_2: "
                "iframe discovery ERROR | intento=%s | pagina=%s | %s",
                rn_attempt,
                activity_page,
                rn_exc,
            )

        await page.wait_for_timeout(500)

    if iframe_locators is None:
        item.error = (
            "No fue posible reconstruir los registros de Actividad "
            f"para pagina {activity_page}."
        )
        return item

    try:
        iframe_count = await iframe_locators.count()
    except Exception as exc:
        item.error = str(exc)
        return item

    iframe_index = record_index - 1

    if iframe_index < 0 or iframe_index >= iframe_count:
        item.error = (
            f"Registro de nota {record_index} fuera de rango. " f"Total={iframe_count}."
        )
        return item

    iframe = iframe_locators.nth(iframe_index)

    try:
        await iframe.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass

    try:
        handle = await iframe.element_handle()
        note_frame = await handle.content_frame() if handle else None
    except Exception:
        note_frame = None

    if note_frame is None:
        item.error = f"No se pudo acceder a la nota {record_index}."
        return item

    blocks = note_frame.locator(
        "#attachment-block .attachment_div, "
        ".attachment_div, "
        "#first-attachment, "
        "#second-attachment, "
        "#third-attachment"
    )

    try:
        block_count = min(
            await blocks.count(),
            30,
        )
    except Exception:
        block_count = 0

    wanted = normalize_text(item.nombre)

    selected_block = None

    # Primera estrategia:
    # encontrar el nombre directamente en el DIV.
    for index in range(block_count):
        block = blocks.nth(index)

        try:
            block_text = normalize_text(await block.inner_text())
        except Exception:
            block_text = ""

        if wanted and wanted in block_text:
            selected_block = block
            break

    # Segunda estrategia:
    # attachmentNameN -> first/second/third-attachment.
    if selected_block is None:

        try:
            html = await note_frame.content()
        except Exception:
            html = ""

        if html and wanted:

            slot_ids = {
                1: "#first-attachment",
                2: "#second-attachment",
                3: "#third-attachment",
            }

            for slot in range(1, 4):

                pattern = re.compile(rf"""(?ix)
                    attachmentName{slot}
                    \s*=\s*
                    [`"']
                    ([^`"']+)
                    [`"']
                    """)

                match = pattern.search(html)

                if match and normalize_text(match.group(1)) == wanted:

                    candidate = note_frame.locator(slot_ids[slot]).first

                    try:
                        if await candidate.count() > 0:
                            selected_block = candidate
                            break
                    except Exception:
                        pass

    if selected_block is None:
        item.error = (
            f"No se encontro bloque descargable para "
            f"{item.nombre} en nota {record_index}."
        )
        return item

    # El HTML observado utiliza sendAttachmentEvent('attachmentN').
    click_target = None

    children = selected_block.locator(
        '[onclick*="sendAttachmentEvent"], ' 'a, button, [role="link"]'
    )

    try:
        child_count = min(
            await children.count(),
            20,
        )
    except Exception:
        child_count = 0

    for index in range(child_count):
        candidate = children.nth(index)

        try:
            if await locator_visible(candidate):
                click_target = candidate
                break
        except Exception:
            continue

    if click_target is None:
        click_target = selected_block

    target_dir = DOWNLOAD_DIR / work_order / incident_id

    target_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_name = (
        re.sub(
            r'[<>:"/\\|?*]+',
            "_",
            item.nombre,
        ).strip()
        or f"nota_{record_index}_adjunto.bin"
    )

    target_path = target_dir / safe_name

    logger.info(
        "RN_INC_NOTE_ATTACHMENTS_V1_2: " "solicitando descarga | nota=%s | archivo=%s",
        record_index,
        item.nombre,
    )

    try:

        async with page.expect_download(timeout=timeout_ms) as download_info:

            await click_target.click()

        download = await download_info.value

        suggested = str(download.suggested_filename or "").strip()

        if suggested:

            safe_suggested = re.sub(
                r'[<>:"/\\|?*]+',
                "_",
                suggested,
            ).strip()

            if safe_suggested:
                item.nombre = suggested
                target_path = target_dir / safe_suggested

        await download.save_as(str(target_path))

        item.descargado = True
        item.ruta_local = str(target_path)
        item.error = ""

        logger.info(
            "RN_INC_NOTE_ATTACHMENTS_V1_2: " "DESCARGA OK: %s",
            target_path,
        )

    except Exception as exc:

        item.error = str(exc)

        logger.warning(
            "RN_INC_NOTE_ATTACHMENTS_V1_2: " "descarga fallida para %s: %s",
            item.nombre,
            exc,
        )

        await screenshot(
            page,
            logger,
            "rn_inc_nota_descarga_fallida",
        )

    return item


async def _recover_work_order_page(
    context,
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    work_order: str,
) -> Page:
    """Recupera una vista util de la WO si SmartIT cerro/cambio la pagina al descargar."""
    alive_pages = []
    try:
        alive_pages = [
            candidate for candidate in context.pages if not candidate.is_closed()
        ]
    except Exception:
        alive_pages = []

    if not alive_pages:
        logger.warning(
            "[RN DOWNLOAD] contexto cerrado; el intento actual debe terminar "
            "de forma controlada y continuar por retry."
        )
        raise RuntimeError(
            "HELIX_CONTEXT_CLOSED: SmartIT cerro la pagina y el BrowserContext."
        )

    page = alive_pages[-1]

    # iniciar_sesion navega al inicio de SmartIT y reutiliza cookies si ya existen.
    await iniciar_sesion(page, cfg, logger)

    direct_ok = await abrir_busqueda_directa_wo(
        page,
        cfg,
        logger,
        work_order,
    )
    if not direct_ok:
        await click_global_search(page, cfg, logger, work_order)

    return await open_work_order(page, cfg, logger, work_order)


async def _download_to_disk_with_chromium(
    page: Page,
    link: Locator,
    target_dir: Path,
    target_path: Path,
    timeout_ms: int,
    logger: logging.Logger,
    attachment_name: str,
) -> tuple[bool, Path | None, str]:
    """
    Fuerza a Chromium a escribir la descarga directamente en target_dir.

    Esto evita depender de Download.save_as(), que en SmartIT puede fallar si la
    vista que disparo la descarga se cierra inmediatamente despues del click.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        target_path.unlink(missing_ok=True)
    except Exception:
        pass

    # Limpiar parciales antiguos del mismo nombre.
    for partial in (
        target_dir / f"{target_path.name}.crdownload",
        target_dir / f"{target_path.stem}.crdownload",
    ):
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass

    before: dict[str, tuple[int, int]] = {}
    try:
        for path in target_dir.iterdir():
            if not path.is_file():
                continue
            stat = path.stat()
            before[path.name] = (int(stat.st_size), int(stat.st_mtime_ns))
    except Exception:
        pass

    cdp = None
    cdp_error = ""
    try:
        cdp = await page.context.new_cdp_session(page)
        try:
            await cdp.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(target_dir),
                    "eventsEnabled": True,
                },
            )
        except Exception:
            await cdp.send(
                "Page.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(target_dir),
                },
            )
    except Exception as exc:
        cdp_error = f"No se pudo configurar descarga directa Chromium: {exc}"
        logger.warning(cdp_error)

    click_error = ""
    try:
        # No esperamos navegacion: algunos enlaces de SmartIT cierran/cambian la
        # vista que origina la descarga, pero Chromium puede seguir escribiendo.
        await link.click(
            timeout=min(max(5000, timeout_ms), 60000),
            no_wait_after=True,
        )
    except Exception as exc:
        click_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "El click de %s reporto error; se verificara igualmente el disco: %s",
            attachment_name,
            click_error,
        )

    # No dejar una petición colgada varios minutos si SmartIT cerró la vista
    # pero la descarga ni siquiera comenzó. El timeout general de SmartIT puede
    # ser muy alto (por ejemplo 30 min), por eso aquí usamos límites propios.
    max_wait_ms = max(
        15000,
        int(os.getenv("HELIX_DIRECT_DOWNLOAD_MAX_WAIT_MS", "60000")),
    )
    start_wait_ms = max(
        5000,
        int(os.getenv("HELIX_DIRECT_DOWNLOAD_START_TIMEOUT_MS", "15000")),
    )
    max_wait_ms = min(max_wait_ms, 120000)
    start_wait_ms = min(start_wait_ms, max_wait_ms)

    wait_started = time.monotonic()
    deadline = wait_started + max_wait_ms / 1000
    start_deadline = wait_started + start_wait_ms / 1000
    last_candidate: Path | None = None
    last_size = -1
    stable_checks = 0
    download_started = False

    try:
        while time.monotonic() < deadline:
            candidates: list[Path] = []
            partial_detected = False

            # Preferir el nombre esperado.
            if target_path.is_file():
                candidates.append(target_path)

            try:
                for path in target_dir.iterdir():
                    if not path.is_file():
                        continue
                    if path.suffix.lower() == ".crdownload":
                        partial_detected = True
                        download_started = True
                        continue
                    if path == target_path:
                        download_started = True
                        continue

                    try:
                        stat = path.stat()
                    except Exception:
                        continue

                    old = before.get(path.name)
                    changed = old is None or old != (
                        int(stat.st_size),
                        int(stat.st_mtime_ns),
                    )
                    if changed:
                        candidates.append(path)
            except Exception:
                pass

            # El archivo mas recientemente modificado es el mejor candidato.
            if candidates:
                download_started = True
                try:
                    candidates.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
                except Exception:
                    pass

                candidate = candidates[0]
                try:
                    size = candidate.stat().st_size
                except Exception:
                    size = 0

                if size > 0:
                    if last_candidate == candidate and last_size == size:
                        stable_checks += 1
                    else:
                        last_candidate = candidate
                        last_size = size
                        stable_checks = 1

                    # Tres lecturas estables (~0.75 s) para evitar tomar un
                    # archivo que Chromium aun esta terminando de escribir.
                    if stable_checks >= 3:
                        final_path = candidate

                        # Si Chrome genero otro nombre pero conocemos el nombre
                        # real del adjunto, normalizarlo al destino esperado.
                        if candidate != target_path:
                            try:
                                target_path.unlink(missing_ok=True)
                                candidate.replace(target_path)
                                final_path = target_path
                            except Exception:
                                final_path = candidate

                        logger.info(
                            "Adjunto descargado directamente por Chromium: %s (%s bytes)",
                            final_path,
                            final_path.stat().st_size,
                        )
                        return True, final_path, ""

            # Si después del tiempo de arranque no apareció ni archivo final
            # ni .crdownload, no tiene sentido esperar minutos con el navegador
            # ya cerrado. Fallar rápido permite liberar el worker y encolar retry.
            if not download_started and time.monotonic() >= start_deadline:
                page_closed = False
                try:
                    page_closed = page.is_closed()
                except Exception:
                    page_closed = True

                state = "pagina cerrada" if page_closed else "sin inicio de descarga"
                logger.warning(
                    "Descarga de %s no inicio en %sms (%s). Se libera el worker.",
                    attachment_name,
                    start_wait_ms,
                    state,
                )
                errors = [part for part in (click_error, cdp_error) if part]
                errors.append(f"La descarga no inicio en {start_wait_ms} ms ({state})")
                return False, None, " | ".join(errors)

            await asyncio.sleep(0.25)

    finally:
        if cdp is not None:
            try:
                await cdp.detach()
            except Exception:
                pass

    errors = [part for part in (click_error, cdp_error) if part]
    if not errors:
        errors.append(
            "Chromium no genero un archivo completo dentro del tiempo configurado"
        )
    return False, None, " | ".join(errors)


async def download_attachment(
    page: Page,
    table: Locator,
    item: AttachmentItem,
    timeout_ms: int,
    logger: logging.Logger,
    work_order: str,
    incident_id: str,
) -> AttachmentItem:
    target_dir = DOWNLOAD_DIR / work_order / incident_id
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", item.nombre).strip() or "adjunto.bin"
    target_path = target_dir / safe_name

    try:
        if target_path.is_file() and target_path.stat().st_size > 0:
            item.descargado = True
            item.ruta_local = str(target_path)
            item.error = ""
            logger.info(
                "[RN DOWNLOAD] archivo existente reutilizado: %s (%s bytes)",
                target_path,
                target_path.stat().st_size,
            )
            return item
    except OSError:
        pass

    try:
        link = table.locator("a", has_text=item.nombre).first
        if not await locator_visible(link):
            item.error = "No se encontro el enlace visible del adjunto."
            return item
    except Exception as exc:
        item.error = f"No se pudo localizar el adjunto: {exc}"
        return item

    logger.info("[RN DOWNLOAD] intentando archivo %s", item.nombre)
    logger.info("[RN DOWNLOAD] estrategia expect_download")

    start_timeout_ms = min(
        max(5000, int(os.getenv("HELIX_DIRECT_DOWNLOAD_START_TIMEOUT_MS", "15000"))),
        60000,
    )
    max_wait_ms = min(
        max(
            start_timeout_ms,
            int(os.getenv("HELIX_DIRECT_DOWNLOAD_MAX_WAIT_MS", "60000")),
        ),
        120000,
    )

    try:
        async with page.expect_download(timeout=start_timeout_ms) as download_info:
            await link.click(timeout=start_timeout_ms, no_wait_after=True)
        logger.info("[RN DOWNLOAD] descarga iniciada: %s", item.nombre)
        download = await download_info.value
        suggested = str(download.suggested_filename or "").strip()
        if suggested:
            safe_suggested = (
                re.sub(r'[<>:"/\\|?*]+', "_", suggested).strip() or safe_name
            )
            target_path = target_dir / safe_suggested
        await asyncio.wait_for(
            download.save_as(str(target_path)),
            timeout=max_wait_ms / 1000,
        )
        if target_path.is_file() and target_path.stat().st_size > 0:
            item.descargado = True
            item.ruta_local = str(target_path)
            item.nombre = target_path.name
            item.error = ""
            logger.info(
                "[RN DOWNLOAD] descarga completada: %s (%s bytes)",
                target_path,
                target_path.stat().st_size,
            )
            return item
        direct_error = "Playwright termino sin producir un archivo completo"
    except Exception as exc:
        direct_error = f"expect_download: {type(exc).__name__}: {exc}"
        logger.warning("[RN DOWNLOAD] expect_download fallo: %s", direct_error)

    if page.is_closed():
        logger.warning("[RN DOWNLOAD] contexto/pagina cerrado tras el click")
        item.error = direct_error
        return item

    logger.info("[RN DOWNLOAD] estrategia CDP")

    ok, downloaded_path, cdp_error = await _download_to_disk_with_chromium(
        page,
        link,
        target_dir,
        target_path,
        timeout_ms,
        logger,
        item.nombre,
    )

    if ok and downloaded_path is not None:
        item.descargado = True
        item.ruta_local = str(downloaded_path)
        item.nombre = downloaded_path.name
        item.error = ""
        return item

    if cdp_error:
        direct_error = f"{direct_error} | CDP: {cdp_error}"

    # Fallback de compatibilidad: si la pagina sigue viva y Chromium no dejo el
    # archivo en disco, intentamos el mecanismo Playwright anterior una vez.
    if not page.is_closed():
        try:
            link = table.locator("a", has_text=item.nombre).first
            if await locator_visible(link):
                logger.info("Fallback Playwright expect_download para %s", item.nombre)
                fallback_timeout_ms = min(max(15000, timeout_ms), 60000)
                async with page.expect_download(
                    timeout=fallback_timeout_ms
                ) as download_info:
                    await link.click()
                download = await download_info.value
                await download.save_as(str(target_path))

                if target_path.is_file() and target_path.stat().st_size > 0:
                    item.descargado = True
                    item.ruta_local = str(target_path)
                    item.error = ""
                    logger.info(
                        "Adjunto descargado por fallback Playwright: %s", target_path
                    )
                    return item
        except Exception as exc:
            direct_error = (
                f"{direct_error} | fallback Playwright: {type(exc).__name__}: {exc}"
                if direct_error
                else f"fallback Playwright: {type(exc).__name__}: {exc}"
            )

    item.error = direct_error or "No fue posible descargar el adjunto."
    logger.warning("No fue posible descargar %s: %s", item.nombre, item.error)

    try:
        if not page.is_closed():
            await screenshot(page, logger, "adjunto_descarga_fallida")
    except Exception:
        pass

    return item


def _rn_is_image_attachment(item: AttachmentItem) -> bool:
    # RN_MULTI_IMAGE_PDF_V1
    name = str(getattr(item, "nombre", "") or "").strip().lower()
    return name.endswith((".jpg", ".jpeg", ".png"))


def select_attachments(
    attachments: list[AttachmentItem],
    requested_name: str,
    download_all: bool,
) -> list[AttachmentItem]:
    if download_all:
        # RN_MULTI_IMAGE_PDF_V1
        # Mantener un solo documento para archivos normales.
        # Excepcion: si TODOS los adjuntos son imagenes y hay 2 o mas,
        # descargar todas para consolidarlas despues en un unico PDF.
        image_attachments = [
            item for item in attachments if _rn_is_image_attachment(item)
        ]
        if len(attachments) >= 2 and len(image_attachments) == len(attachments):
            return list(attachments)

        # RN_FIRST_DOCUMENT_FAST_V2
        return attachments[:1]

    if requested_name:
        wanted = normalize_text(requested_name)
        return [item for item in attachments if normalize_text(item.nombre) == wanted]
    if len(attachments) == 1:
        return attachments
    return []


def write_result(
    payload: dict[str, Any],
    work_order: str,
    worker_id: int = 1,
) -> Path:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULT_DIR / (
        f"resultado_{work_order}_w{worker_id}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


async def main_async(args: argparse.Namespace) -> int:
    worker_id = max(1, int(getattr(args, "worker_id", 1) or 1))
    logger = setup_logger(worker_id)
    logger.info(
        "Runtime Helix activo: V6_WO_INC_ATTACHMENTS | worker=%s",
        worker_id,
    )
    started = time.monotonic()
    payload: dict[str, Any] = {
        "ok": False,
        "origen": "HELIX",
        "tipo_consulta": "REDES_NEUTRAS",
        "ot": args.ot,
        "worker_id": worker_id,
        "ot_encontrada": False,
        "incidentes_relacionados": [],
        "incidente_abierto": "",
        "tabla_adjuntos_encontrada": False,
        "tabla_adjuntos_wo_encontrada": False,
        "tabla_adjuntos_inc_encontrada": False,
        "validacion_adjuntos_wo_confirmada": False,
        "validacion_adjuntos_inc_confirmada": False,
        "actividad_revisada": False,
        "registros_actividad_revisados": 0,
        "documentos_adjuntos_encontrados": False,
        "cantidad_documentos_adjuntos": 0,
        "cantidad_adjuntos_wo": 0,
        "cantidad_adjuntos_inc": 0,
        "requiere_seleccion": False,
        "adjuntos": [],
        "error": "",
    }

    try:
        cfg = Settings.from_env()
        headless = bool(args.headless)

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        worker_profile_dir = PROFILE_DIR / f"worker_{worker_id}"
        worker_profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "[RN WORKER] worker=%s | profile=%s",
            worker_id,
            worker_profile_dir,
        )

        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(worker_profile_dir),
                headless=headless,
                slow_mo=cfg.slow_mo_ms,
                accept_downloads=True,
                viewport={"width": 1600, "height": 950},
                args=["--start-maximized", "--disable-notifications"],
            )
            context.set_default_timeout(cfg.timeout_ms)
            context.set_default_navigation_timeout(cfg.timeout_ms)
            page = context.pages[0] if context.pages else await context.new_page()

            # La PWA cierra con window.close() la pagina que dispara ciertos
            # adjuntos. Esta pagina centinela mantiene vivo el BrowserContext.
            keeper_page = await context.new_page()
            await keeper_page.goto("about:blank")
            logger.info("[RN DOWNLOAD] pagina centinela creada para conservar contexto")

            try:
                await iniciar_sesion(page, cfg, logger)

                busqueda_directa_ok = await abrir_busqueda_directa_wo(
                    page,
                    cfg,
                    logger,
                    args.ot,
                )

                if not busqueda_directa_ok:
                    await click_global_search(page, cfg, logger, args.ot)

                page = await open_work_order(page, cfg, logger, args.ot)
                payload["ot_encontrada"] = True

                # ---------------------------------------------------------
                # V6: primero revisar adjuntos DIRECTOS de la WO.
                # Deben descargarse antes de navegar hacia un INC.
                # ---------------------------------------------------------
                logger.info("Revisando adjuntos directos de la WO %s.", args.ot)

                (
                    _,
                    wo_attachment_table,
                    wo_table_attachments,
                    wo_attachment_scan_confirmed,
                ) = await read_attachments(page, cfg, logger)
                payload["validacion_adjuntos_wo_confirmada"] = bool(
                    wo_attachment_scan_confirmed
                )
                # RN_FIRST_DOCUMENT_FAST_V2
                # Solo documentacion directa de WO.
                # Si no existe, continuar inmediatamente al INC.
                wo_activity_attachments: list[AttachmentItem] = []
                wo_activity_records = 0
                # RN_MULTI_IMAGE_PDF_V1
                # Mantener 1 documento excepto cuando toda la evidencia WO
                # sean 2+ imagenes destinadas a un solo PDF.
                wo_attachments = list(
                    select_attachments(
                        wo_table_attachments,
                        "",
                        True,
                    )
                )

                for item in wo_attachments:
                    item.origen = "WO_" + str(item.origen or "HELIX")

                payload["tabla_adjuntos_wo_encontrada"] = bool(wo_table_attachments)
                payload["cantidad_adjuntos_wo"] = len(wo_attachments)

                # Para Mesa de Ayuda se llama con todos=True.
                # Si se pide todos o un archivo específico, descargamos la WO
                # antes de abandonar esta vista.
                wo_downloaded: list[AttachmentItem] = []
                if args.descargar and (args.todos or args.archivo):
                    selected_wo = select_attachments(
                        wo_table_attachments,
                        args.archivo,
                        args.todos,
                    )
                    for selected_index, item in enumerate(selected_wo):
                        item.origen = "WO_" + str(item.origen or "TABLA_ADJUNTO")

                        if selected_index > 0:
                            try:
                                current_link = wo_attachment_table.locator(
                                    "a", has_text=item.nombre
                                ).first
                                need_recover = (
                                    page.is_closed()
                                    or not await locator_visible(current_link)
                                )
                            except Exception:
                                need_recover = True

                            if need_recover:
                                logger.warning(
                                    "La vista WO cambio tras una descarga. Recuperando %s para continuar.",
                                    args.ot,
                                )
                                page = await _recover_work_order_page(
                                    context, page, cfg, logger, args.ot
                                )
                                (
                                    _,
                                    wo_attachment_table,
                                    _,
                                    _,
                                ) = await read_attachments(page, cfg, logger)

                        wo_downloaded.append(
                            await download_attachment(
                                page,
                                wo_attachment_table,
                                item,
                                cfg.download_timeout_ms,
                                logger,
                                args.ot,
                                args.ot,
                            )
                        )

                # ---------------------------------------------------------
                # Después revisar INC relacionados, preservando flujo actual.
                # ---------------------------------------------------------
                # RN_WO_FIRST_STOP_V1_BEGIN
                # Regla operativa Mesa de Ayuda:
                # 1) revisar WO; 2) si tiene documentacion, responder desde WO y FIN;
                # 3) solo si WO no tiene documentacion, continuar al INC relacionado.
                if wo_attachments:
                    wo_payload_items: list[AttachmentItem] = []
                    for item in wo_attachments:
                        downloaded_match = next(
                            (
                                downloaded
                                for downloaded in wo_downloaded
                                if normalize_text(downloaded.nombre)
                                == normalize_text(item.nombre)
                            ),
                            None,
                        )
                        wo_payload_items.append(downloaded_match or item)

                    for index, item in enumerate(wo_payload_items, 1):
                        item.indice = index

                    payload["tabla_adjuntos_encontrada"] = bool(wo_table_attachments)
                    payload["tabla_adjuntos_wo_encontrada"] = bool(wo_table_attachments)
                    payload["tabla_adjuntos_inc_encontrada"] = False
                    payload["actividad_revisada"] = True
                    payload["registros_actividad_revisados"] = wo_activity_records
                    payload["documentos_adjuntos_encontrados"] = True
                    payload["cantidad_documentos_adjuntos"] = len(wo_payload_items)
                    payload["cantidad_adjuntos_wo"] = len(wo_attachments)
                    payload["cantidad_adjuntos_inc"] = 0
                    payload["incidentes_relacionados"] = []
                    payload["incidente_abierto"] = ""
                    payload["adjuntos"] = [asdict(item) for item in wo_payload_items]

                    if not args.descargar:
                        payload["ok"] = True
                        payload["codigo"] = "HELIX_DOCUMENTOS_ADJUNTOS_DETECTADOS"
                        payload["requiere_seleccion"] = len(wo_payload_items) > 1
                    elif args.todos or args.archivo:
                        if wo_downloaded:
                            payload["ok"] = all(
                                item.descargado for item in wo_downloaded
                            )
                            payload["codigo"] = (
                                "HELIX_ADJUNTOS_DESCARGADOS"
                                if payload["ok"]
                                else "HELIX_DESCARGA_ADJUNTO_FALLIDA"
                            )
                            payload["requiere_seleccion"] = not payload["ok"]
                        else:
                            # Documentacion detectada en WO (por ejemplo, Actividad),
                            # pero no hubo una descarga directa desde la tabla Adjuntos.
                            payload["ok"] = True
                            payload["codigo"] = "HELIX_REQUIERE_SELECCION_ADJUNTO"
                            payload["requiere_seleccion"] = True
                    else:
                        payload["ok"] = True
                        payload["codigo"] = "HELIX_REQUIERE_SELECCION_ADJUNTO"
                        payload["requiere_seleccion"] = True

                    logger.info(
                        "RN_WO_FIRST_STOP_V1: WO %s tiene %s documento(s). "
                        "No se consultara el INC relacionado.",
                        args.ot,
                        len(wo_payload_items),
                    )
                    await screenshot(page, logger, "resultado_wo_documentacion")
                    raise WorkOrderDocumentationResolved()
                # RN_WO_FIRST_STOP_V1_END

                _, related_table, _, related_items = await open_related_tab_and_read(
                    page, cfg, logger
                )
                payload["incidentes_relacionados"] = [
                    asdict(item) for item in related_items
                ]

                incident_attachments: list[AttachmentItem] = []
                incident_downloaded: list[AttachmentItem] = []
                activity_records = wo_activity_records
                inc_attachment_scan_confirmed = False
                incident = None

                if related_items:
                    if args.incidente:
                        incident = next(
                            (
                                item
                                for item in related_items
                                if normalize_text(item.id)
                                == normalize_text(args.incidente)
                            ),
                            None,
                        )
                        if incident is None:
                            raise RuntimeError(
                                f"El incidente solicitado {args.incidente} no aparece "
                                "en Elementos relacionados/Related items."
                            )
                    else:
                        incident = related_items[0]

                    payload["incidente_abierto"] = incident.id
                    await click_incident_in_table(
                        related_table,
                        incident.id,
                        page,
                        cfg,
                        logger,
                    )

                    (
                        _,
                        inc_attachment_table,
                        inc_table_attachments,
                        inc_attachment_scan_confirmed,
                    ) = await read_attachments(page, cfg, logger)
                    payload["validacion_adjuntos_inc_confirmada"] = bool(
                        inc_attachment_scan_confirmed
                    )

                    # RN_INC_NOTE_ATTACHMENTS_V1_2
                    # Tercer fallback:
                    # 1. WO
                    # 2. INC tabla Adjuntos
                    # 3. INC Notas / WorkLog
                    inc_note_attachments: list[AttachmentItem] = []
                    inc_note_records = 0

                    # RN_FIRST_DOCUMENT_FAST_V2
                    if inc_table_attachments:
                        logger.info(
                            "RN_FIRST_DOCUMENT_FAST_V2: "
                            "INC %s tiene %s adjunto(s) directo(s). "
                            "Se usa el primero y se omite Activity/WorkLog.",
                            incident.id,
                            len(inc_table_attachments),
                        )
                    else:
                        logger.info(
                            "RN_FIRST_DOCUMENT_FAST_V2: "
                            "INC %s sin adjuntos directos. "
                            "Se revisa Activity/WorkLog como fallback.",
                            incident.id,
                        )

                        inc_note_attachments, inc_note_records = (
                            await read_note_attachments(
                                page,
                                cfg,
                                logger,
                            )
                        )

                    incident_attachments = merge_attachments(
                        inc_table_attachments,
                        inc_note_attachments,
                    )

                    for item in incident_attachments:
                        item.origen = "INC_" + str(item.origen or "HELIX")

                    payload["tabla_adjuntos_inc_encontrada"] = bool(
                        inc_table_attachments
                    )
                    payload["cantidad_adjuntos_inc"] = len(incident_attachments)
                    # RN_INC_NOTE_ATTACHMENTS_V1_2
                    activity_records += inc_note_records

                    if args.descargar and (args.todos or args.archivo):

                        # RN_INC_NOTE_ATTACHMENTS_V1_2
                        selected_inc = select_attachments(
                            incident_attachments,
                            args.archivo,
                            args.todos,
                        )

                        for selected_index, item in enumerate(selected_inc):

                            origin_upper = str(item.origen or "").upper()

                            if "NOTA_ACTIVIDAD" in origin_upper:

                                incident_downloaded.append(
                                    await download_note_attachment(
                                        page,
                                        cfg,
                                        item,
                                        cfg.download_timeout_ms,
                                        logger,
                                        args.ot,
                                        incident.id,
                                    )
                                )

                            else:
                                if selected_index > 0:
                                    try:
                                        current_link = inc_attachment_table.locator(
                                            "a", has_text=item.nombre
                                        ).first
                                        need_recover = (
                                            page.is_closed()
                                            or not await locator_visible(current_link)
                                        )
                                    except Exception:
                                        need_recover = True

                                    if need_recover:
                                        logger.warning(
                                            "La vista INC cambio tras una descarga. Recuperando %s / %s.",
                                            args.ot,
                                            incident.id,
                                        )
                                        page = await _recover_work_order_page(
                                            context, page, cfg, logger, args.ot
                                        )
                                        _, related_table, _, refreshed_related = (
                                            await open_related_tab_and_read(
                                                page, cfg, logger
                                            )
                                        )
                                        refreshed_incident = next(
                                            (
                                                candidate
                                                for candidate in refreshed_related
                                                if normalize_text(candidate.id)
                                                == normalize_text(incident.id)
                                            ),
                                            None,
                                        )
                                        if refreshed_incident is None:
                                            raise RuntimeError(
                                                f"No fue posible reabrir {incident.id} para continuar descargas."
                                            )
                                        await click_incident_in_table(
                                            related_table,
                                            incident.id,
                                            page,
                                            cfg,
                                            logger,
                                        )
                                        (
                                            _,
                                            inc_attachment_table,
                                            _,
                                            _,
                                        ) = await read_attachments(page, cfg, logger)

                                incident_downloaded.append(
                                    await download_attachment(
                                        page,
                                        inc_attachment_table,
                                        item,
                                        cfg.download_timeout_ms,
                                        logger,
                                        args.ot,
                                        incident.id,
                                    )
                                )

                # ---------------------------------------------------------
                # Unir WO + INC sin perder archivos con el mismo nombre
                # provenientes de fuentes distintas.
                # ---------------------------------------------------------
                combined = list(wo_attachments) + list(incident_attachments)
                for index, item in enumerate(combined, 1):
                    item.indice = index

                # Reemplazar objetos detectados por sus versiones descargadas.
                downloaded_by_path = {}
                for item in wo_downloaded + incident_downloaded:
                    key = (
                        normalize_text(item.nombre),
                        str(item.ruta_local or ""),
                    )
                    downloaded_by_path[key] = item

                merged_payload_items: list[AttachmentItem] = []
                for item in combined:
                    candidates = [
                        downloaded
                        for downloaded in wo_downloaded + incident_downloaded
                        if normalize_text(downloaded.nombre)
                        == normalize_text(item.nombre)
                        and (
                            (
                                "WO_" in str(item.origen)
                                and "WO_" in str(downloaded.origen)
                            )
                            or (
                                "INC_" in str(item.origen)
                                and "INC_" in str(downloaded.origen)
                            )
                        )
                    ]
                    merged_payload_items.append(candidates[0] if candidates else item)

                payload["tabla_adjuntos_encontrada"] = bool(
                    wo_table_attachments or payload["tabla_adjuntos_inc_encontrada"]
                )
                payload["actividad_revisada"] = True
                payload["registros_actividad_revisados"] = activity_records
                payload["documentos_adjuntos_encontrados"] = bool(merged_payload_items)
                payload["cantidad_documentos_adjuntos"] = len(merged_payload_items)
                payload["adjuntos"] = [asdict(item) for item in merged_payload_items]

                if not merged_payload_items:
                    # RN_ATTACHMENT_FALSE_NEGATIVE_GUARD_V1
                    # Una vista lenta/inconclusa no equivale a "sin adjuntos".
                    # Si no logramos confirmar de forma estable las vistas que
                    # correspondian, generamos un fallo transitorio para que
                    # redes_neutras.py mande la WO al worker de reintentos.
                    if not wo_attachment_scan_confirmed:
                        raise RuntimeError(
                            "Helix no permitio confirmar de forma estable los "
                            "adjuntos directos de la Work Order."
                        )

                    if related_items and not inc_attachment_scan_confirmed:
                        raise RuntimeError(
                            "Helix no permitio confirmar de forma estable los "
                            "adjuntos del incidente relacionado."
                        )

                    payload["ok"] = True
                    payload["codigo"] = (
                        "HELIX_SIN_INCIDENTES_RELACIONADOS"
                        if not related_items
                        else "HELIX_SIN_DOCUMENTOS_ADJUNTOS"
                    )

                elif not args.descargar:
                    payload["ok"] = True
                    payload["codigo"] = "HELIX_DOCUMENTOS_ADJUNTOS_DETECTADOS"
                    payload["requiere_seleccion"] = len(merged_payload_items) > 1

                elif not (args.todos or args.archivo):
                    if len(merged_payload_items) == 1:
                        # Conservamos el contrato anterior: cuando solo existe
                        # uno puede seleccionarse automáticamente. Si está en
                        # Actividad y no en tabla, no se fuerza una descarga.
                        payload["ok"] = any(
                            item.descargado for item in merged_payload_items
                        )
                        payload["codigo"] = (
                            "HELIX_ADJUNTOS_DESCARGADOS"
                            if payload["ok"]
                            else "HELIX_REQUIERE_SELECCION_ADJUNTO"
                        )
                        payload["requiere_seleccion"] = not payload["ok"]
                    else:
                        payload["ok"] = True
                        payload["codigo"] = "HELIX_REQUIERE_SELECCION_ADJUNTO"
                        payload["requiere_seleccion"] = True

                else:
                    requested_downloads = wo_downloaded + incident_downloaded

                    if not requested_downloads:
                        payload["ok"] = True
                        payload["codigo"] = "HELIX_REQUIERE_SELECCION_ADJUNTO"
                        payload["requiere_seleccion"] = True
                    else:
                        payload["ok"] = all(
                            item.descargado for item in requested_downloads
                        )
                        payload["codigo"] = (
                            "HELIX_ADJUNTOS_DESCARGADOS"
                            if payload["ok"]
                            else "HELIX_DESCARGA_ADJUNTO_FALLIDA"
                        )

                logger.info(
                    "Adjuntos consolidados: WO=%s | INC=%s | TOTAL=%s.",
                    len(wo_attachments),
                    len(incident_attachments),
                    len(merged_payload_items),
                )

                await screenshot(page, logger, "resultado_final")

            finally:
                if not headless and args.mantener_abierto > 0:
                    logger.info(
                        "La ventana permanecerá abierta %s segundos para revisión.",
                        args.mantener_abierto,
                    )
                    await page.wait_for_timeout(args.mantener_abierto * 1000)
                await context.close()

    except WorkOrderDocumentationResolved:
        logger.info(
            "Resultado controlado RN_WO_FIRST_STOP_V1: documentacion resuelta directamente en la WO."
        )

    except WorkOrderNotFoundError as exc:
        payload["ok"] = True
        payload["ot_encontrada"] = False
        payload["codigo"] = "HELIX_OT_NO_ENCONTRADA"
        payload["error"] = ""
        logger.info("Resultado controlado: %s", exc)

    except Exception as exc:
        payload["error"] = str(exc)
        payload["codigo"] = "HELIX_PRUEBA_FALLIDA"
        logger.exception("Prueba fallida: %s", exc)

    payload["duracion_seg"] = round(time.monotonic() - started, 2)
    result_path = write_result(payload, args.ot, worker_id)
    # Permite que redes_neutras.py lea exactamente el JSON producido por esta
    # ejecución, incluso si la misma WO se consulta simultáneamente.
    args.result_path = str(result_path)
    logger.info("Resultado JSON: %s", result_path)
    return 0 if payload.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prueba local aislada de Redes Neutras en Helix: busca una OT, "
            "abre su INC relacionado y valida documentos adjuntos en tabla y Actividad."
        )
    )
    parser.add_argument("--ot", default="WO0000005455971")
    parser.add_argument("--incidente", default="")
    parser.add_argument(
        "--descargar",
        action="store_true",
        help="Activa la descarga. Sin esta opción solo enumera adjuntos.",
    )
    parser.add_argument(
        "--archivo",
        default="",
        help='Nombre exacto a descargar, por ejemplo "SDP.pdf".',
    )
    parser.add_argument(
        "--todos",
        action="store_true",
        help="Descarga todos los adjuntos encontrados.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ejecuta sin mostrar Chromium. Para la primera prueba se recomienda visible.",
    )
    parser.add_argument(
        "--mantener-abierto",
        type=int,
        default=20,
        help="Segundos que mantiene abierta la ventana visible al finalizar.",
    )
    parser.add_argument(
        "--worker-id",
        type=int,
        default=1,
        help="ID de worker/perfil Chromium para pruebas directas.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(build_parser().parse_args())))
