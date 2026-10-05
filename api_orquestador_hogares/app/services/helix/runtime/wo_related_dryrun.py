from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Frame, Locator, Page, async_playwright

from app.services.helix.runtime.helix_runner import (
    PROFILE_DIR,
    click_global_search,
    click_incident_in_table,
    find_related_tab_once,
    open_related_tab_and_read,
    open_work_order,
)
from app.services.helix.smartit_scraper import (
    Settings,
    iniciar_sesion,
    locator_visible,
)

# HELIX_WO_RELATED_DRYRUN_V1
# Seguridad: este modulo NO contiene ninguna funcion ni selector operativo
# que haga click sobre "Guardar". Su frontera termina despues de seleccionar
# Descripcion Tipo y leer la WO provisional.

RUNTIME_ROOT = Path(
    r"C:\xampp\htdocs\rutinas_hogares\api_direccion_clientes\runtime\diagnosticos\helix_wo_related_dryrun"
)

CREATE_RELATED_SELECTORS = [
    'button[data-testid="menu-item-test-id-4"]',
    'button:has-text("Crear relacionado")',
]

# HELIX_WO_RELATED_DRYRUN_FULL_INC_V1
FULL_INCIDENT_SELECTORS = [
    'button:has-text("Ver incidencia completa")',
    'button:has(span:has-text("Ver incidencia completa"))',
    'a:has-text("Ver incidencia completa")',
    '[role="button"]:has-text("Ver incidencia completa")',
    'button:has-text("View full incident")',
    'a:has-text("View full incident")',
    '[role="button"]:has-text("View full incident")',
    'button:has-text("Ver requerimiento completo")',
    'button:has-text("Ver Requerimiento Completo")',
    'button:has-text("View full request")',
]

WORK_ORDER_MENU_SELECTORS = [
    'button[data-testid="304423861"]',
    'button[role="menuitem"]:has-text("Orden de trabajo")',
    '[role="menuitem"]:has-text("Orden de trabajo")',
]

CREATE_VIEW_SELECTORS = [
    'h2#ar304421251',
    'h2[aria-label="Crear orden trabajo"]',
    'h2:has-text("Crear orden trabajo")',
    'text="Crear orden trabajo"',
]

DRAFT_WO_SELECTORS = [
    '#ar1000000182_data',
    '[testid="ar1000000182_data"]',
    '[data-testid="ar1000000182_data"]',
]

DESCRIPTION_TYPE_SELECTORS = [
    'input#536870983',
    'input[data-testid="536870983_input"]',
]

SAVE_BUTTON_SELECTORS = [
    'button:has-text("Guardar")',
    'button:has-text("Save")',
]

TYPE_CONFIG = {
    "FIBRA": {
        "query": "fibra",
        "option": "Correctivo Fibra Optica",
    },
    "COAX": {
        "query": "coax",
        "option": "Correctivo coaxial",
    },
}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def _logger(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger(f"helix_wo_related_dryrun_{run_dir.name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    file_handler = logging.FileHandler(
        run_dir / "dryrun.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


async def _first_visible_in_page(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
) -> tuple[Frame, Locator, str] | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in list(page.frames):
            for selector in selectors:
                try:
                    locator = frame.locator(selector).first
                    if await locator_visible(locator):
                        return frame, locator, selector
                except Exception:
                    continue
        await page.wait_for_timeout(200)
    return None


async def _first_visible_across_context(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
) -> tuple[Page, Frame, Locator, str] | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        candidates = [p for p in page.context.pages if not p.is_closed()]
        for candidate_page in reversed(candidates):
            found = await _first_visible_in_page(candidate_page, selectors, 350)
            if found:
                frame, locator, selector = found
                return candidate_page, frame, locator, selector
        await page.wait_for_timeout(200)
    return None


async def _disable_save_buttons(page: Page, logger: logging.Logger) -> int:
    disabled = 0
    for candidate_page in [p for p in page.context.pages if not p.is_closed()]:
        for frame in list(candidate_page.frames):
            for selector in SAVE_BUTTON_SELECTORS:
                try:
                    locators = frame.locator(selector)
                    count = min(await locators.count(), 20)
                    for index in range(count):
                        button = locators.nth(index)
                        try:
                            await button.evaluate(
                                """el => {
                                    el.setAttribute('disabled', 'disabled');
                                    el.setAttribute('aria-disabled', 'true');
                                    el.style.pointerEvents = 'none';
                                    el.dataset.atlasDryrunBlocked = 'true';
                                }"""
                            )
                            disabled += 1
                        except Exception:
                            continue
                except Exception:
                    continue
    logger.info("Barrera Guardar aplicada a %s elemento(s).", disabled)
    return disabled


async def _open_full_incident_view(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> Page:
    """
    Después de abrir el INC desde la tabla de relacionados, SmartIT puede
    dejarlo en una vista PREVIEW. En esa vista Helix renderiza acciones como
    'Crear relacionado', pero el contenedor padre queda hidden/display:none.

    Esta rutina reproduce la navegación operativa correcta:
    'Ver incidencia completa' -> vista completa del INC.

    Solo hace navegación. NO modifica datos.
    """

    logger.info(
        "INC: buscando accion 'Ver incidencia completa' antes de continuar."
    )

    found_full = await _first_visible_across_context(
        page,
        FULL_INCIDENT_SELECTORS,
        min(cfg.timeout_ms, 30000),
    )

    if not found_full:
        logger.warning(
            "INC: no se encontro 'Ver incidencia completa'. "
            "Se intentara promover el PREVIEW a incidentPV directo."
        )

        # HELIX_WO_RELATED_DRYRUN_INCIDENTPV_V2
        target_id = ""
        source_frame_url = ""

        for candidate_page in [
            p for p in page.context.pages if not p.is_closed()
        ]:
            for frame in list(candidate_page.frames):
                frame_url = str(frame.url or "")
                if "targetForm=Incident" not in frame_url:
                    continue

                match = re.search(
                    r"[?&]targetId=([^&]+)",
                    frame_url,
                    flags=re.IGNORECASE,
                )
                if not match:
                    continue

                target_id = match.group(1).strip()
                source_frame_url = frame_url
                break

            if target_id:
                break

        if not target_id:
            logger.warning(
                "INC: el PREVIEW no expuso targetId de Incident. "
                "Se continuara con la vista actual."
            )
            return page

        from app.config.endpoints_settings import (
            get_smartit_app_base_url,
        )

        direct_url = (
            get_smartit_app_base_url()
            + f"incidentPV/{target_id}"
        )

        logger.info(
            "INC: promoviendo PREVIEW a incidentPV directo. "
            "targetId=%s source=%s",
            target_id,
            source_frame_url,
        )

        try:
            await page.goto(
                direct_url,
                wait_until="domcontentloaded",
                timeout=min(cfg.timeout_ms, 45000),
            )
        except Exception as exc:
            logger.warning(
                "INC: navegación incidentPV terminó con espera parcial: %s",
                exc,
            )

        await page.wait_for_timeout(1200)

        deadline = (
            time.monotonic()
            + min(cfg.timeout_ms, 35000) / 1000
        )

        while time.monotonic() < deadline:
            try:
                related = await find_related_tab_once(page)
            except Exception:
                related = None

            if related:
                logger.info(
                    "INC: incidentPV directo listo. URL=%s",
                    page.url,
                )
                return page

            await page.wait_for_timeout(300)

        logger.warning(
            "INC: incidentPV directo no confirmó Elementos relacionados "
            "en el tiempo esperado. URL=%s",
            page.url,
        )
        return page

    full_page, _, full_button, full_selector = found_full
    logger.info(
        "INC: abriendo incidencia completa con %s.",
        full_selector,
    )

    pages_before = {
        id(candidate)
        for candidate in full_page.context.pages
        if not candidate.is_closed()
    }

    try:
        await full_button.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    await full_button.click(timeout=12000)
    await full_page.wait_for_timeout(1200)

    # Si Helix abrió una nueva pestaña/página, adoptarla.
    candidates = [
        candidate
        for candidate in full_page.context.pages
        if not candidate.is_closed()
    ]
    new_pages = [
        candidate
        for candidate in candidates
        if id(candidate) not in pages_before
    ]
    if new_pages:
        full_page = new_pages[-1]

    # Confirmar una página/frame donde exista la pestaña Elementos relacionados.
    deadline = time.monotonic() + min(cfg.timeout_ms, 35000) / 1000
    while time.monotonic() < deadline:
        candidates = [
            candidate
            for candidate in full_page.context.pages
            if not candidate.is_closed()
        ]

        for candidate in reversed(candidates):
            try:
                found_related = await find_related_tab_once(candidate)
            except Exception:
                found_related = None

            if found_related:
                logger.info(
                    "INC: vista completa lista. PAGE=%s",
                    candidate.url,
                )
                return candidate

        await full_page.wait_for_timeout(300)

    logger.warning(
        "INC: se pulso 'Ver incidencia completa', pero no se pudo "
        "confirmar Elementos relacionados en el tiempo esperado."
    )
    return full_page

async def _click_related_tab_on_incident(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> None:
    deadline = time.monotonic() + min(cfg.timeout_ms, 45000) / 1000
    found = None

    while time.monotonic() < deadline:
        found = await find_related_tab_once(page)
        if found:
            break
        await page.wait_for_timeout(300)

    if not found:
        raise RuntimeError(
            "No se encontro Elementos relacionados dentro del INC."
        )

    frame, tab, selector = found
    logger.info(
        "INC: abriendo Elementos relacionados con %s.",
        selector,
    )
    try:
        await tab.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    try:
        await tab.click(timeout=10000)
    except Exception:
        await tab.click(force=True, timeout=10000)

    await page.wait_for_timeout(900)


async def _read_text(locator: Locator) -> str:
    for getter in ("inner_text", "text_content", "input_value"):
        try:
            value = await getattr(locator, getter)()
            value = re.sub(r"\s+", " ", str(value or "")).strip()
            if value:
                return value
        except Exception:
            continue

    for attr in ("value", "aria-label", "title"):
        try:
            value = await locator.get_attribute(attr)
            value = re.sub(r"\s+", " ", str(value or "")).strip()
            if value:
                return value
        except Exception:
            continue
    return ""


async def _select_description_type(
    page: Page,
    tipo: str,
    cfg: Settings,
    logger: logging.Logger,
) -> dict[str, Any]:
    config = TYPE_CONFIG[tipo]

    found_input = await _first_visible_across_context(
        page,
        DESCRIPTION_TYPE_SELECTORS,
        min(cfg.timeout_ms, 30000),
    )
    if not found_input:
        raise RuntimeError("No se encontro el campo Descripcion Tipo.")

    page, _, input_box, selector = found_input
    logger.info("Descripcion Tipo detectada con %s.", selector)

    await _disable_save_buttons(page, logger)

    await input_box.click(timeout=7000)
    await input_box.fill(config["query"])
    logger.info("Descripcion Tipo: escrito '%s'.", config["query"])

    option_text = config["option"]
    option_selectors = [
        f'[role="option"]:has-text("{option_text}")',
        f'button:has-text("{option_text}")',
        f'li:has-text("{option_text}")',
        f'div:has-text("{option_text}")',
        f'text="{option_text}"',
    ]

    found_option = await _first_visible_across_context(
        page,
        option_selectors,
        min(cfg.timeout_ms, 30000),
    )
    if not found_option:
        raise RuntimeError(
            f"No se encontro la opcion esperada: {option_text}"
        )

    page, _, option, option_selector = found_option
    logger.info(
        "Seleccionando Descripcion Tipo '%s' con %s.",
        option_text,
        option_selector,
    )
    await option.click(timeout=10000)
    await page.wait_for_timeout(1000)

    blocked = await _disable_save_buttons(page, logger)

    input_value = ""
    try:
        input_value = await input_box.input_value()
    except Exception:
        input_value = await _read_text(input_box)

    return {
        "page": page,
        "query": config["query"],
        "option": option_text,
        "input_value": input_value,
        "save_buttons_blocked": blocked,
    }


async def run_dryrun(
    work_order: str,
    tipo: str,
    *,
    headless: bool,
    mantener_abierto: int,
) -> dict[str, Any]:
    tipo = _norm(tipo)
    if tipo not in TYPE_CONFIG:
        raise ValueError("Tipo invalido. Use FIBRA o COAX.")

    work_order = str(work_order or "").strip().upper()
    if not re.fullmatch(r"WO\d{13,14}", work_order):
        raise ValueError("WO invalida. Use WO seguida de 13 o 14 digitos.")

    run_dir = RUNTIME_ROOT / f"{datetime.now():%Y%m%d_%H%M%S}_{work_order}_{tipo}"
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = _logger(run_dir)

    payload: dict[str, Any] = {
        "ok": False,
        "modo": "DRY_RUN",
        "safe_mode": True,
        "allow_save_click": False,
        "reportar_como_creada": False,
        "wo_origen": work_order,
        "tipo": tipo,
        "incidente_relacionado": "",
        "wo_provisional": "",
        "descripcion_tipo_query": TYPE_CONFIG[tipo]["query"],
        "descripcion_tipo_seleccionada": "",
        "guardar_bloqueado": False,
        "codigo": "DRYRUN_INICIADO",
        "error": "",
        "output_dir": str(run_dir),
    }

    cfg = Settings.from_env()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(
        "DRY-RUN iniciado. WO=%s tipo=%s. Guardar NO permitido.",
        work_order,
        tipo,
    )

    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                slow_mo=cfg.slow_mo_ms,
                accept_downloads=False,
                viewport={"width": 1600, "height": 950},
                args=["--start-maximized", "--disable-notifications"],
            )
            context.set_default_timeout(cfg.timeout_ms)
            context.set_default_navigation_timeout(cfg.timeout_ms)
            page = context.pages[0] if context.pages else await context.new_page()

            try:
                # 1. Login y WO origen.
                await iniciar_sesion(page, cfg, logger)
                await click_global_search(page, cfg, logger, work_order)
                page = await open_work_order(
                    page,
                    cfg,
                    logger,
                    work_order,
                )

                # 2. WO -> INC relacionado.
                # HELIX_DRYRUN_INCIDENT_CANONICAL_OFSC_V1
                canonical_incident = ""

                try:
                    from app.services.helix.summary import (
                        intentar_integracion_ofsc,
                    )

                    ofsc_hint = await intentar_integracion_ofsc(
                        page,
                        logger,
                    )
                    canonical_incident = str(
                        ofsc_hint.get("incidente_relacionado") or ""
                    ).strip().upper()

                    logger.info(
                        "DRY-RUN hint OFSC incidente=%s",
                        canonical_incident,
                    )
                except Exception as ofsc_exc:
                    logger.warning(
                        "DRY-RUN no pudo obtener hint OFSC; "
                        "se conserva fallback de relacionados: %s",
                        ofsc_exc,
                    )

                _, related_table, _, related_items = await open_related_tab_and_read(
                    page,
                    cfg,
                    logger,
                )
                if not related_items:
                    raise RuntimeError(
                        "La WO origen no tiene incidentes relacionados."
                    )

                incident = None
                incident_source = ""

                if canonical_incident:
                    incident = next(
                        (
                            item
                            for item in related_items
                            if str(item.id or "").strip().upper()
                            == canonical_incident
                        ),
                        None,
                    )

                    # HELIX_DRYRUN_CANONICAL_INC_FAIL_CLOSED_V1
                    if incident is None:
                        related_ids = [
                            str(item.id or "").strip().upper()
                            for item in related_items
                            if str(item.id or "").strip()
                        ]
                        raise RuntimeError(
                            "INC_CANONICO_OFSC_NO_PRESENTE_EN_RELACIONADOS: "
                            f"canonical={canonical_incident} "
                            f"related={related_ids}"
                        )

                    incident_source = "OFSC"

                if incident is None:
                    incident = next(
                        (
                            item
                            for item in related_items
                            if _norm(item.tipo_relacion) == "CREADO POR"
                        ),
                        None,
                    )
                    if incident is not None:
                        incident_source = "RELACIONADOS_CREADO_POR"

                if incident is None:
                    incident = related_items[0]
                    incident_source = "RELACIONADOS_PRIMERO"

                logger.info(
                    "DRY-RUN INC seleccionado source=%s canonical=%s seleccionado=%s relacion=%s",
                    incident_source,
                    canonical_incident,
                    incident.id,
                    incident.tipo_relacion,
                )

                payload["incidente_relacionado"] = incident.id
                payload["incidente_fuente"] = incident_source

                await click_incident_in_table(
                    related_table,
                    incident.id,
                    page,
                    cfg,
                    logger,
                )

                # 3. INC preview -> Ver incidencia completa.
                # Esto reproduce la ruta manual real antes de usar
                # Elementos relacionados para crear la WO relacionada.
                page = await _open_full_incident_view(
                    page,
                    cfg,
                    logger,
                )

                # 4. INC completo -> Elementos relacionados.
                await _click_related_tab_on_incident(
                    page,
                    cfg,
                    logger,
                )

                # 4. Crear relacionado.
                found_create = await _first_visible_across_context(
                    page,
                    CREATE_RELATED_SELECTORS,
                    min(cfg.timeout_ms, 30000),
                )
                if not found_create:
                    raise RuntimeError(
                        "No se encontro el boton Crear relacionado."
                    )
                page, _, create_button, create_selector = found_create
                logger.info(
                    "Click DRY-RUN permitido: Crear relacionado (%s).",
                    create_selector,
                )
                await create_button.click(timeout=10000)
                await page.wait_for_timeout(600)

                # 5. Orden de trabajo.
                found_wo_menu = await _first_visible_across_context(
                    page,
                    WORK_ORDER_MENU_SELECTORS,
                    min(cfg.timeout_ms, 20000),
                )
                if not found_wo_menu:
                    raise RuntimeError(
                        "No se encontro Orden de trabajo en Crear relacionado."
                    )
                page, _, wo_menu, wo_menu_selector = found_wo_menu
                logger.info(
                    "Click DRY-RUN permitido: Orden de trabajo (%s).",
                    wo_menu_selector,
                )
                await wo_menu.click(timeout=10000)

                # 6. Esperar formulario Crear orden trabajo.
                found_form = await _first_visible_across_context(
                    page,
                    CREATE_VIEW_SELECTORS,
                    min(cfg.timeout_ms, 45000),
                )
                if not found_form:
                    raise RuntimeError(
                        "No aparecio la vista Crear orden trabajo."
                    )
                page, _, _, form_selector = found_form
                logger.info(
                    "Vista Crear orden trabajo confirmada con %s.",
                    form_selector,
                )

                blocked_initial = await _disable_save_buttons(page, logger)
                payload["guardar_bloqueado"] = blocked_initial > 0

                # 7. Leer WO provisional.
                found_draft = await _first_visible_across_context(
                    page,
                    DRAFT_WO_SELECTORS,
                    min(cfg.timeout_ms, 15000),
                )
                if found_draft:
                    page, _, draft_locator, draft_selector = found_draft
                    draft_wo = (await _read_text(draft_locator)).upper()
                    if re.search(r"WO\d{13,14}", draft_wo):
                        draft_wo = re.search(r"WO\d{13,14}", draft_wo).group(0)
                    payload["wo_provisional"] = draft_wo
                    logger.info(
                        "WO provisional visible: %s (%s).",
                        draft_wo,
                        draft_selector,
                    )
                else:
                    logger.warning("No se pudo leer WO provisional.")

                # 8. Descripcion Tipo.
                type_result = await _select_description_type(
                    page,
                    tipo,
                    cfg,
                    logger,
                )
                page = type_result["page"]
                payload["descripcion_tipo_seleccionada"] = type_result["option"]
                payload["guardar_bloqueado"] = (
                    payload["guardar_bloqueado"]
                    or type_result["save_buttons_blocked"] > 0
                )

                # Barrera final.
                await _disable_save_buttons(page, logger)

                screenshot_path = run_dir / "final_antes_de_guardar.png"
                try:
                    await page.screenshot(
                        path=str(screenshot_path),
                        full_page=True,
                    )
                    payload["screenshot"] = str(screenshot_path)
                except Exception as exc:
                    payload["screenshot_error"] = str(exc)

                payload["ok"] = True
                payload["codigo"] = "DRYRUN_LISTO_ANTES_DE_GUARDAR"

                logger.info(
                    "STOP DE SEGURIDAD. Tipo seleccionado=%s. "
                    "WO provisional=%s. NO se pulsa Guardar.",
                    payload["descripcion_tipo_seleccionada"],
                    payload["wo_provisional"],
                )

                if not headless and mantener_abierto > 0:
                    logger.info(
                        "Ventana se mantendra visible %s segundos para inspeccion.",
                        mantener_abierto,
                    )
                    await page.wait_for_timeout(mantener_abierto * 1000)

            finally:
                await context.close()

    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["codigo"] = "DRYRUN_FALLIDO"
        logger.exception("DRY-RUN fallido: %s", exc)

    result_path = run_dir / "resultado.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    payload["result_file"] = str(result_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DRY-RUN seguro: WO origen -> INC -> Crear relacionado -> "
            "Orden de trabajo -> Descripcion Tipo -> STOP antes de Guardar."
        )
    )
    parser.add_argument("--ot", required=True)
    parser.add_argument("--tipo", required=True, choices=["FIBRA", "COAX"])
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--mantener-abierto",
        type=int,
        default=30,
    )
    return parser


async def main_async(args: argparse.Namespace) -> int:
    payload = await run_dryrun(
        args.ot,
        args.tipo,
        headless=bool(args.headless),
        mantener_abierto=max(0, int(args.mantener_abierto)),
    )
    print(json.dumps(payload, ensure_ascii=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(build_parser().parse_args())))