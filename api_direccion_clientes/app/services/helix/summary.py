from __future__ import annotations

import logging
import re
import time
from typing import Any

from playwright.async_api import Locator, Page, async_playwright

from app.services.helix.runtime.helix_runner import (
    PROFILE_DIR,
    click_global_search,
    click_incident_in_table,
    normalize_text,
    open_related_tab_and_read,
    open_work_order,
    find_exact_work_order_card,
)
from app.services.helix.smartit_scraper import (
    Settings,
    iniciar_sesion,
    locator_visible,
)

from app.services.helix.work_order_title_parser import parse_work_order_title

# HELIX_GES_DIRECCIONES_NOTAS_V1
from app.services.helix.runtime.helix_runner import read_activity_note_texts
from app.services.direcciones.ges import (
    extraer_cuentas_afectadas_desde_notas,
)


def _logger() -> logging.Logger:
    logger = logging.getLogger("helix_resumen_ot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    return logger


async def _element_value(locator: Locator) -> str:
    values: list[str] = []

    try:
        values.append((await locator.inner_text()).strip())
    except Exception:
        pass

    for attribute in ("aria-label", "title", "value"):
        try:
            values.append(
                (await locator.get_attribute(attribute) or "").strip()
            )
        except Exception:
            pass

    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if cleaned:
            return cleaned
    return ""



OFSC_BUTTON_SELECTORS = [
    'button:has-text("Integracion OFsC")',
    'button:has-text("Integración OFsC")',
    'button[automationid="1753826475"]',
    'button[testid="ar536870982"]',
]

OFSC_PANEL_SELECTORS = [
    'fieldset.dp:has(.dp-title:text-is("WOI:WorkOrder"))',
    'fieldset.dp:has-text("WOI:WorkOrder")',
]

OFSC_FIELDS = {
    "incidente_relacionado": '[id="536872189"]',
    "ciudad": '[id="536871198"]',
    "regional": '[id="536871205"]',
    "aliado": '[id="536871203"]',
    "estado_ofsc": '[id="536871033"]',
}


async def _first_visible_across_frames(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
) -> tuple[Any, Locator, str] | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in list(page.frames):
            for selector in selectors:
                try:
                    locator = frame.locator(selector)
                    count = min(await locator.count(), 20)
                    for index in range(count):
                        candidate = locator.nth(index)
                        if await locator_visible(candidate):
                            return frame, candidate, selector
                except Exception:
                    continue
        await page.wait_for_timeout(200)
    return None


async def _input_value(locator: Locator) -> str:
    try:
        value = await locator.input_value()
    except Exception:
        value = await _element_value(locator)
    return re.sub(r"\s+", " ", value or "").strip()


async def intentar_integracion_ofsc(
    page: Page,
    logger: logging.Logger,
) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "disponible": False,
        "con_datos": False,
        "incidente_relacionado": "",
        "aliado": "",
        "ciudad": "",
        "regional": "",
        "estado_ofsc": "",
        "duracion_seg": 0.0,
        "error": "",
    }

    found_button = await _first_visible_across_frames(
        page,
        OFSC_BUTTON_SELECTORS,
        3500,
    )
    if not found_button:
        result["error"] = "Boton Integracion OFsC no disponible."
        result["duracion_seg"] = round(time.monotonic() - started, 2)
        logger.info("Ruta OFsC no disponible; se usara fallback.")
        return result

    _, button, selector = found_button
    result["disponible"] = True
    logger.info("Abriendo Integracion OFsC con %s", selector)

    try:
        try:
            await button.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            await button.click(timeout=7000)
        except Exception:
            await button.click(force=True, timeout=7000)

        found_panel = await _first_visible_across_frames(
            page,
            OFSC_PANEL_SELECTORS,
            10000,
        )
        if not found_panel:
            result["error"] = "El panel WOI:WorkOrder no aparecio."
            return result

        _, panel, panel_selector = found_panel
        logger.info("Panel OFsC detectado con %s", panel_selector)

        for field, selector_field in OFSC_FIELDS.items():
            try:
                field_locator = panel.locator(selector_field).first
                if await field_locator.count():
                    result[field] = await _input_value(field_locator)
            except Exception as exc:
                logger.warning(
                    "No se pudo leer %s desde OFsC: %s",
                    field,
                    exc,
                )

        result["con_datos"] = any(
            str(result.get(field) or "").strip()
            for field in (
                "incidente_relacionado",
                "aliado",
                "ciudad",
                "regional",
            )
        )

        logger.info(
            "OFsC: incidente=%s aliado=%s ciudad=%s regional=%s "
            "estado_ofsc=%s",
            result["incidente_relacionado"],
            result["aliado"],
            result["ciudad"],
            result["regional"],
            result["estado_ofsc"],
        )
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("Fallo ruta rapida OFsC: %s", result["error"])
        return result
    finally:
        try:
            found_panel = await _first_visible_across_frames(
                page,
                OFSC_PANEL_SELECTORS,
                1200,
            )
            if found_panel:
                _, panel, _ = found_panel
                close = panel.locator(
                    'button.dp-close[aria-label="close"]'
                ).first
                if await close.count():
                    try:
                        await close.click(timeout=2500)
                    except Exception as normal_exc:
                        logger.warning(
                            "Cierre normal de OFsC bloqueado: %s; reintentando forzado.",
                            normal_exc,
                        )
                        await close.click(force=True, timeout=2500)
                    await panel.wait_for(state="hidden", timeout=3000)
                    logger.info("Panel OFsC cerrado.")
        except Exception as exc:
            logger.warning("No se pudo cerrar panel OFsC: %s", exc)

        result["duracion_seg"] = round(
            time.monotonic() - started,
            2,
        )


async def read_field_by_label(
    page: Page,
    label: str,
    timeout_ms: int,
    logger: logging.Logger,
) -> str:
    normalized_label = normalize_text(label)
    escaped = label.replace('"', '\\"')
    selectors = [
        f'[aria-label="{escaped}"]',
        f'[title="{escaped}"]',
        f'text="{escaped}"',
    ]
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        for frame in list(page.frames):
            for selector in selectors:
                try:
                    labels = frame.locator(selector)
                    count = min(await labels.count(), 80)
                except Exception:
                    continue

                for index in range(count):
                    label_node = labels.nth(index)
                    if not await locator_visible(label_node):
                        continue

                    value_label = normalize_text(
                        await _element_value(label_node)
                    )
                    if value_label != normalized_label:
                        continue

                    node_id = (
                        await label_node.get_attribute("id")
                    ) or ""

                    if node_id.endswith("_label"):
                        data_id = node_id[:-6] + "_data"
                        try:
                            paired = frame.locator(
                                f'[id="{data_id}"]'
                            ).first
                            if await locator_visible(paired):
                                value = await _element_value(paired)
                                if (
                                    value
                                    and normalize_text(value)
                                    != normalized_label
                                ):
                                    logger.info(
                                        "Campo %s=%s por label/data",
                                        label,
                                        value,
                                    )
                                    return value
                        except Exception:
                            pass

                    try:
                        row = label_node.locator(
                            "xpath=ancestor::div[contains("
                            "concat(' ', normalize-space(@class), ' '),"
                            " ' row ')][1]"
                        )
                        if await row.count():
                            candidates = row.locator(
                                '[id$="_data"], .defaultDataFont, '
                                'button[aria-label], '
                                '[role="article"][aria-label]'
                            )
                            candidate_count = min(
                                await candidates.count(),
                                40,
                            )
                            for candidate_index in range(
                                candidate_count
                            ):
                                candidate = candidates.nth(
                                    candidate_index
                                )
                                if not await locator_visible(candidate):
                                    continue
                                value = await _element_value(candidate)
                                normalized_value = normalize_text(value)
                                if (
                                    value
                                    and normalized_value
                                    != normalized_label
                                    and "EDITAR " not in normalized_value
                                ):
                                    logger.info(
                                        "Campo %s=%s por fila",
                                        label,
                                        value,
                                    )
                                    return value
                    except Exception:
                        pass

        await page.wait_for_timeout(250)

    logger.warning("No se encontro el campo %s", label)
    return ""




# HELIX_CAIDA_TOTAL_IMPACT_TRUNK_V1
def parse_descripcion_impacto_troncal(
    value: str,
) -> dict[str, str]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()

    empty = {
        "impacto_gpon": "",
        "impacto_id_troncal": "",
        "impacto_descripcion_troncal": "",
        "impacto_nombre_comercial": "",
    }

    if not text:
        return empty

    match = re.search(
        r"\b(\d+)\s*/\s*(\d+)\s*/\s*(\d+)"
        r"\s*[-–—:]\s*"
        r"(TRK\s+[A-Z0-9._-]+"
        r"(?:\s*\([^()]+\))?)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return empty

    gpon = (
        f"{int(match.group(1))}/"
        f"{int(match.group(2))}/"
        f"{int(match.group(3))}"
    )

    description = re.sub(
        r"\s+",
        " ",
        match.group(4),
    ).strip()

    commercial = ""

    commercial_match = re.search(
        r"\(([^()]*)\)\s*$",
        description,
    )

    if commercial_match:
        commercial = commercial_match.group(1).strip()
        trunk_id = description[
            :commercial_match.start()
        ].strip()
    else:
        trunk_id = description

    return {
        "impacto_gpon": gpon,
        "impacto_id_troncal": trunk_id,
        "impacto_descripcion_troncal": description,
        "impacto_nombre_comercial": commercial,
    }


# HELIX_AUTH_623_RELOAD_FIRST_V1
async def _helix_auth_623_visible(
    page: Page,
) -> bool:
    patterns = (
        "ARERR [623]",
        "ARERR[623]",
        "ERROR (623)",
        "ERROR DE AUTENTIFIC",
        "ERROR DE AUTENTIC",
        "46237202",
    )

    try:
        pages = list(page.context.pages)
    except Exception:
        pages = [page]

    for current_page in pages:
        try:
            frames = list(current_page.frames)
        except Exception:
            frames = []

        for frame in frames:
            try:
                body = await frame.locator(
                    "body"
                ).inner_text(
                    timeout=400,
                )
            except Exception:
                continue

            upper = str(body or "").upper()

            if any(
                pattern in upper
                for pattern in patterns
            ):
                return True

    return False


async def _recover_helix_623_after_search(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    work_order: str,
) -> bool:
    """
    Primer nivel de recovery:
        623 visible
        -> reload pagina actual
        -> validar sesion
        -> repetir busqueda WO una vez

    Si persiste, eleva HELIX_AUTH_TRANSIENT_623 para que
    service.py aplique el recovery mayor ya certificado:
    cerrar/reabrir navegador.
    """

    if not await _helix_auth_623_visible(
        page
    ):
        return False

    logger.warning(
        "HELIX_AUTH_623_RELOAD_FIRST_V1: "
        "ARERR 623 detectado; recargando pagina."
    )

    try:
        await page.reload(
            wait_until="domcontentloaded",
            timeout=min(
                cfg.timeout_ms,
                30000,
            ),
        )
    except Exception as exc:
        logger.warning(
            "Reload por ARERR 623 fallo: %s: %s",
            type(exc).__name__,
            exc,
        )

    await page.wait_for_timeout(
        500
    )

    # Si reload devolvio a login, iniciar_sesion lo resuelve.
    await iniciar_sesion(
        page,
        cfg,
        logger,
    )

    await click_global_search(
        page,
        cfg,
        logger,
        work_order,
    )

    await page.wait_for_timeout(
        300
    )

    if await _helix_auth_623_visible(
        page
    ):
        raise RuntimeError(
            "HELIX_AUTH_TRANSIENT_623"
        )

    logger.info(
        "HELIX_AUTH_623_RELOAD_FIRST_V1: "
        "recarga recupero la sesion."
    )

    return True


async def consultar_resumen_async(
    work_order: str,
    *,
    headless: bool,
) -> dict[str, Any]:
    logger = _logger()
    cfg = Settings.from_env()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "ok": False,
        "codigo": "HELIX_OT_RESUMEN_ERROR",
        "numero_ot": work_order,
        "estado_ot": "",
        "incidente_relacionado": "",
        "aliado": "",
        "ciudad": "",
        "regional": "",
        "categoria_operacional": "",
        # HELIX_CGE_DESCRIPTION_PAYLOAD_V4E
        "descripcion_ot": "",
        # HELIX_CGE_CI_OPTIONAL_FALLBACK_V2
        "ci": "",
        "descripcion_ci": "",
        # HELIX_CAIDA_TOTAL_IMPACT_TRUNK_V1
        "descripcion_impacto": "",
        "impacto_gpon": "",
        "impacto_id_troncal": "",
        "impacto_descripcion_troncal": "",
        "impacto_nombre_comercial": "",

        "estado_ofsc": "",
        "origen_datos": "",
        "uso_fallback": False,
        "tiempo_ofsc_seg": 0.0,
        "error": "",
    }

    # HELIX_TITLE_SINGLE_SESSION_V3
    # Defaults del contrato del parser, incluso si SmartIT no entrega titulo.
    payload.update(
        parse_work_order_title(
            "",
            source="",
        )
    )

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
        page = (
            context.pages[0]
            if context.pages
            else await context.new_page()
        )

        try:
            await iniciar_sesion(page, cfg, logger)
            resolved_work_order = await click_global_search(
                page,
                cfg,
                logger,
                work_order,
            )

            # HELIX_WILDCARD_RESOLVED_WO_V1
            #
            # Si la búsqueda fue realizada con %, click_global_search
            # devuelve el ID real de la única Work Order encontrada.
            if resolved_work_order:
                if resolved_work_order != work_order:
                    logger.info(
                        "HELIX_WILDCARD_RESOLVED_WO_V1: "
                        "query=%s resolved=%s",
                        work_order,
                        resolved_work_order,
                    )

                work_order = resolved_work_order
                payload["numero_ot"] = resolved_work_order

            # HELIX_AUTH_623_RELOAD_FIRST_V1
            # El ARERR 623 observado aparece precisamente en la
            # pantalla de resultados. Revisarlo antes de continuar.
            await _recover_helix_623_after_search(
                page,
                cfg,
                logger,
                work_order,
            )

            # HELIX_TITLE_SINGLE_SESSION_V3
            # Reutiliza la MISMA page/context/session del resumen.
            try:
                _, work_order_card, _ = await find_exact_work_order_card(
                    page,
                    work_order,
                    timeout_ms=min(cfg.timeout_ms, 20000),
                    logger=logger,
                )

                title_locator = work_order_card.locator(
                    "div.search-item-layout__title"
                ).first

                title_candidates: list[tuple[str, str]] = []

                async def _add_title_candidate(
                    source: str,
                    value: str | None,
                ) -> None:
                    cleaned = re.sub(
                        r"\s+",
                        " ",
                        str(value or ""),
                    ).strip()

                    if cleaned:
                        title_candidates.append(
                            (source, cleaned)
                        )

                try:
                    await _add_title_candidate(
                        "title_attr",
                        await title_locator.get_attribute(
                            "title"
                        ),
                    )
                except Exception:
                    pass

                try:
                    await _add_title_candidate(
                        "aria_label",
                        await title_locator.get_attribute(
                            "aria-label"
                        ),
                    )
                except Exception:
                    pass

                try:
                    await _add_title_candidate(
                        "text_content",
                        await title_locator.text_content(),
                    )
                except Exception:
                    pass

                try:
                    await _add_title_candidate(
                        "inner_text",
                        await title_locator.inner_text(),
                    )
                except Exception:
                    pass

                if title_candidates:
                    priority = {
                        "title_attr": 0,
                        "aria_label": 1,
                        "text_content": 2,
                        "inner_text": 3,
                    }
                    title_candidates.sort(
                        key=lambda item: (
                            priority.get(item[0], 99),
                            -len(item[1]),
                        )
                    )

                    title_source, title_value = (
                        title_candidates[0]
                    )

                    payload.update(
                        parse_work_order_title(
                            title_value,
                            source=title_source,
                        )
                    )

                    logger.info(
                        "Titulo WO %s extraido en sesion unica. fuente=%s nodo=%s red=%s",
                        work_order,
                        title_source,
                        payload.get("nodo_detectado") or "",
                        payload.get("tipo_red") or "",
                    )
                else:
                    logger.warning(
                        "La tarjeta de %s no entrego titulo util.",
                        work_order,
                    )

            except Exception as exc:
                # La ausencia de titulo NO debe romper el resumen tradicional.
                payload["error_titulo"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                logger.warning(
                    "No fue posible enriquecer titulo de %s: %s",
                    work_order,
                    exc,
                )
            page = await open_work_order(
                page,
                cfg,
                logger,
                work_order,
            )

            
            # HELIX_GES_DIRECCIONES_NOTAS_V1
            #
            # GES:
            # - no consulta CMTS
            # - no consulta OLT
            # - no consulta ACS
            # - no usa Diagnosticador
            # - reutiliza la misma sesion Helix
            # - lee CUENTAS AFECTADAS desde WorkLog/Actividad

            _ges_title = str(
                payload.get("titulo_ot") or ""
            ).strip().upper()

            _es_ges = bool(
                re.match(
                    r"^GES(?:\s|[-_:])",
                    _ges_title,
                )
            )

            if _es_ges:
                logger.info(
                    "HELIX_GES_DIRECCIONES_NOTAS_V1: "
                    "WO %s detectada como GES.",
                    work_order,
                )

                _ges_notes: list[dict[str, Any]] = []
                _ges_note_sources: list[str] = []

                # ------------------------------------------------------------
                # PRIMERO: notas de la WO
                # ------------------------------------------------------------
                try:
                    _wo_notes, _wo_note_count = await read_activity_note_texts(
                        page,
                        cfg,
                        logger,
                    )

                    for _note in _wo_notes:
                        if not isinstance(_note, dict):
                            continue

                        _copy = dict(_note)
                        _copy["origen"] = "WO"
                        _ges_notes.append(_copy)

                    if _wo_notes:
                        _ges_note_sources.append("WO")

                except Exception as _ges_wo_exc:
                    logger.warning(
                        "HELIX_GES_DIRECCIONES_NOTAS_V1: "
                        "fallo lectura notas WO: %s",
                        _ges_wo_exc,
                    )

                _ges_result = extraer_cuentas_afectadas_desde_notas(
                    _ges_notes
                )

                # ------------------------------------------------------------
                # SEGUNDO: si no aparecen cuentas en WO, buscar INC relacionado
                # ------------------------------------------------------------
                if not bool(
                    _ges_result.get("clientes_encontrados")
                ):
                    try:
                        (
                            _,
                            _ges_related_table,
                            _,
                            _ges_related_items,
                        ) = await open_related_tab_and_read(
                            page,
                            cfg,
                            logger,
                        )

                        if _ges_related_items:

                            _canonical_inc = str(
                                payload.get("incidente_relacionado")
                                or ""
                            ).strip().upper()

                            _ges_incident = None

                            if _canonical_inc:
                                _ges_incident = next(
                                    (
                                        _item
                                        for _item in _ges_related_items
                                        if str(
                                            _item.id or ""
                                        ).strip().upper()
                                        == _canonical_inc
                                    ),
                                    None,
                                )

                            if _ges_incident is None:
                                _ges_incident = next(
                                    (
                                        _item
                                        for _item in _ges_related_items
                                        if normalize_text(
                                            _item.tipo_relacion
                                        )
                                        == "CREADO POR"
                                    ),
                                    _ges_related_items[0],
                                )

                            payload["incidente_relacionado"] = (
                                _ges_incident.id
                            )

                            await click_incident_in_table(
                                _ges_related_table,
                                _ges_incident.id,
                                page,
                                cfg,
                                logger,
                            )

                            _inc_notes, _inc_note_count = (
                                await read_activity_note_texts(
                                    page,
                                    cfg,
                                    logger,
                                )
                            )

                            for _note in _inc_notes:
                                if not isinstance(_note, dict):
                                    continue

                                _copy = dict(_note)
                                _copy["origen"] = "INC"
                                _ges_notes.append(_copy)

                            if _inc_notes:
                                _ges_note_sources.append("INC")

                            _ges_result = (
                                extraer_cuentas_afectadas_desde_notas(
                                    _ges_notes
                                )
                            )

                    except Exception as _ges_inc_exc:
                        logger.warning(
                            "HELIX_GES_DIRECCIONES_NOTAS_V1: "
                            "fallback INC fallo: %s",
                            _ges_inc_exc,
                        )

                payload["es_ges"] = True
                payload["tipo_red"] = "GES"
                payload["origen_datos"] = "NOTAS_HELIX"
                payload["ges_direcciones"] = _ges_result
                payload["ges_fuentes_notas"] = _ges_note_sources
                payload["ges_notas_revisadas"] = len(_ges_notes)
                payload["ok"] = True
                payload["codigo"] = "HELIX_OT_RESUMEN_GES"

                logger.info(
                    "HELIX_GES_DIRECCIONES_NOTAS_V1: "
                    "codigo=%s clientes=%s notas=%s fuentes=%s",
                    _ges_result.get("codigo"),
                    _ges_result.get("clientes_encontrados"),
                    len(_ges_notes),
                    _ges_note_sources,
                )

                return payload
# HELIX_FTTH_DOM_DESCRIPTION_FALLBACK_V2
            #
            # SmartIT puede truncar title_attr antes de PORT:
            #
            #   ... FRAME=0 SLOT=2 SUBSLOT=65535 POR
            #
            # La vista completa conserva la Descripción completa.
            #
            # Esta lógica:
            # - sólo corre para FTTH cuando PORT falta;
            # - NO reemplaza datos ya válidos;
            # - NO infiere PORT desde SUBSLOT ni SLOT;
            # - extrae únicamente el bloque Descripción -> Categoría operacional.
            #
            # HELIX_TOPOLOGY_DOM_FALLBACK_TRONCAL_V1
            _es_troncal_ftth_context = bool(
                payload.get("es_ftth")
                or (
                    payload.get("es_troncal")
                    and not payload.get("es_hfc")
                    and not payload.get("es_mw")
                )
            )

            # HELIX_FTTH_DOM_PORT_CANONICAL_V3
            # SmartIT puede truncar title_attr incluso a mitad del PORT
            # (ejemplo observado: PORT=11 -> PORT=1).
            # Para FTTH cuya topologia inicial viene de title_attr,
            # contrastamos con la Descripcion completa antes de consultar OLT.
            _topologia_desde_title_attr = (
                str(payload.get("fuente_titulo") or "").strip().lower()
                == "title_attr"
            )

            if (
                _es_troncal_ftth_context
                and (
                    _topologia_desde_title_attr
                    or not str(payload.get("elemento_red") or "").strip()
                    or not str(payload.get("port") or "").strip()
                )
            ):
                try:
                    import re as _helix_re

                    _descripcion_dom = ""

                    # ----------------------------------------------------------
                    # 1. Contenedor principal observado en PWA Work Order
                    # ----------------------------------------------------------
                    for _frame in list(page.frames):
                        try:
                            _loc = _frame.locator(
                                '[id="304428481"]'
                            )

                            if await _loc.count() < 1:
                                continue

                            _txt = str(
                                await _loc.first.inner_text()
                                or ""
                            ).strip()

                            _upper = _txt.upper()

                            if (
                                "DESCRIP" in _upper
                                and
                                "CATEGOR" in _upper
                            ):
                                _descripcion_dom = _txt
                                break

                        except Exception:
                            continue

                    # ----------------------------------------------------------
                    # 2. Fallback genérico: buscar un contenedor visible
                    #    de vista completa sin depender exclusivamente del ID.
                    # ----------------------------------------------------------
                    if not _descripcion_dom:

                        for _frame in list(page.frames):
                            try:
                                _txt = str(
                                    await _frame.locator(
                                        "body"
                                    ).inner_text()
                                    or ""
                                ).strip()

                                _upper = _txt.upper()

                                if (
                                    "DESCRIP" in _upper
                                    and
                                    "CATEGOR" in _upper
                                    and
                                    str(
                                        payload.get(
                                            "elemento_red"
                                        )
                                        or ""
                                    ).upper()
                                    in _upper
                                ):
                                    _descripcion_dom = _txt
                                    break

                            except Exception:
                                continue

                    # ----------------------------------------------------------
                    # 3. Extraer únicamente sección Descripción
                    # ----------------------------------------------------------
                    if _descripcion_dom:

                        _match_desc = _helix_re.search(
                            r"(?:DESCRIPCI[ÓO]N|DESCRIPTION)"
                            r"\s*(.*?)"
                            r"(?="
                            r"CATEGOR[IÍ]A\s+OPERACIONAL"
                            r"|CATEGOR[IÍ]A\s+DE\s+PRODUCTOS"
                            r"|ASIGNADO\s+A"
                            r"|GRUPO\s+ASIGNADO"
                            r"|$"
                            r")",
                            _descripcion_dom,
                            flags=(
                                _helix_re.I
                                | _helix_re.S
                            ),
                        )

                        if _match_desc:

                            _descripcion_texto = str(
                                _match_desc.group(1)
                                or ""
                            ).strip()

                            _descripcion_texto = (
                                _helix_re.sub(
                                    r"\s+",
                                    " ",
                                    _descripcion_texto,
                                ).strip()
                            )

                            if _descripcion_texto:

                                # HELIX_CGE_DESCRIPTION_PAYLOAD_V4E
                                payload["descripcion_ot"] = _descripcion_texto

                                _parsed_desc = (
                                    parse_work_order_title(
                                        _descripcion_texto,
                                        source=(
                                            "dom_description"
                                        ),
                                    )
                                )

                                _campos = (
                                    "elemento_red",
                                    "rack",
                                    "shelf",
                                    "slot",
                                    "port",
                                    "frame",
                                    "subslot",
                                )

                                _recuperados = {}

                                for _campo in _campos:

                                    _actual = str(
                                        payload.get(_campo)
                                        or ""
                                    ).strip()

                                    _alterno = str(
                                        _parsed_desc.get(
                                            _campo
                                        )
                                        or ""
                                    ).strip()

                                    _debe_completar = bool(
                                        not _actual
                                        and _alterno
                                    )

                                    _debe_corregir_port_truncado = bool(
                                        _campo == "port"
                                        and _topologia_desde_title_attr
                                        and _actual
                                        and _alterno
                                        and _actual != _alterno
                                    )

                                    if (
                                        _debe_completar
                                        or _debe_corregir_port_truncado
                                    ):
                                        payload[_campo] = _alterno
                                        _recuperados[_campo] = _alterno

                                        if _debe_corregir_port_truncado:
                                            logger.warning(
                                                "PORT FTTH corregido desde "
                                                "Descripcion DOM: %s -> %s",
                                                _actual,
                                                _alterno,
                                            )

                                payload[
                                    "port_informado"
                                ] = bool(
                                    str(
                                        payload.get("port")
                                        or ""
                                    ).strip()
                                )

                                payload[
                                    "fuente_topologia_complementaria"
                                ] = (
                                    "dom_description"
                                )

                                if _recuperados:
                                    logger.info(
                                        "Topologia FTTH completada "
                                        "desde DOM Descripcion: %s",
                                        _recuperados,
                                    )

                                logger.info(
                                    "Descripcion DOM FTTH recuperada: %s",
                                    _descripcion_texto[:500],
                                )

                except Exception as _dom_desc_exc:

                    logger.warning(
                        "Fallback DOM Descripcion FTTH fallo: "
                        "%s: %s",
                        type(_dom_desc_exc).__name__,
                        _dom_desc_exc,
                    )

            # HELIX_CATEGORIA_OPERACIONAL_V1
            categoria_directa = await _first_visible_across_frames(
                page,
                [
                    '[id="ar304420021_data"]',
                    '[testid="ar304420021_data"]',
                ],
                5000,
            )

            if categoria_directa:
                (
                    _,
                    categoria_locator,
                    categoria_selector,
                ) = categoria_directa

                payload["categoria_operacional"] = (
                    await _element_value(
                        categoria_locator
                    )
                )
                if str(
                    payload.get("categoria_operacional") or ""
                ).strip().casefold() in {
                    "",
                    "none",
                    "none set",
                    "null",
                    "n/a",
                    "na",
                    "not set",
                }:
                    payload["categoria_operacional"] = ""

                logger.info(
                    "Categoria operacional leida directamente "
                    "con %s: %s",
                    categoria_selector,
                    payload["categoria_operacional"],
                )

            if not str(
                payload["categoria_operacional"]
                or ""
            ).strip():
                payload["categoria_operacional"] = (
                    await read_field_by_label(
                        page,
                        "Categoría operacional",
                        min(cfg.timeout_ms, 15000),
                        logger,
                    )
                )
                if str(
                    payload.get("categoria_operacional") or ""
                ).strip().casefold() in {
                    "",
                    "none",
                    "none set",
                    "null",
                    "n/a",
                    "na",
                    "not set",
                }:
                    payload["categoria_operacional"] = ""

            logger.info(
                "Categoria operacional final=%s",
                payload.get(
                    "categoria_operacional"
                ) or "",
            )

            # Ruta directa y estable para el estado de la OT.
            estado_directo = await _first_visible_across_frames(
                page,
                [
                    '[id="ar7_data"]',
                    '[testid="ar7_data"]',
                ],
                5000,
            )

            if estado_directo:
                _, estado_locator, estado_selector = estado_directo
                payload["estado_ot"] = await _element_value(
                    estado_locator
                )
                logger.info(
                    "Estado OT le?do directamente con %s: %s",
                    estado_selector,
                    payload["estado_ot"],
                )

            # Respaldo mediante lectura por etiqueta.
            if not str(payload["estado_ot"] or "").strip():
                payload["estado_ot"] = await read_field_by_label(
                    page,
                    "Estado",
                    min(cfg.timeout_ms, 15000),
                    logger,
                )

            # HELIX_DIRECCIONES_SKIP_OFSC_V1
            #
            # Direcciones NO necesita Aliado/Ciudad/Regional.
            # El INC puede obtenerse directamente desde
            # Elementos relacionados.
            #
            # CAIDA TOTAL / TRONCAL GPON conserva temporalmente OFsC
            # para proteger la logica certificada de Descripcion de Impacto.
            _ofsc_guard_title = str(
                payload.get("titulo_ot")
                or payload.get("descripcion_ot")
                or ""
            ).upper()

            _ofsc_required_caida_total = bool(
                "CAIDA TOTAL" in _ofsc_guard_title
                and "TRONCAL GPON" in _ofsc_guard_title
            )

            if _ofsc_required_caida_total:
                ofsc = await intentar_integracion_ofsc(
                    page,
                    logger,
                )

                payload["tiempo_ofsc_seg"] = (
                    ofsc["duracion_seg"]
                )

                payload["estado_ofsc"] = (
                    ofsc["estado_ofsc"]
                )

                for field in (
                    "incidente_relacionado",
                    "aliado",
                    "ciudad",
                    "regional",
                ):
                    value = str(
                        ofsc.get(field) or ""
                    ).strip()

                    if value:
                        payload[field] = value

            else:
                ofsc = {
                    "disponible": False,
                    "con_datos": False,
                    "incidente_relacionado": "",
                    "aliado": "",
                    "ciudad": "",
                    "regional": "",
                    "estado_ofsc": "",
                    "duracion_seg": 0.0,
                    "error": "",
                }

                payload["tiempo_ofsc_seg"] = 0.0
                payload["estado_ofsc"] = ""

                logger.info(
                    "HELIX_DIRECCIONES_SKIP_OFSC_V1: "
                    "Integracion OFsC omitida; "
                    "INC se obtendra desde Elementos relacionados."
                )

            # HELIX_CATEGORIA_RETRY_POST_OFSC_V1
            if not str(payload.get("categoria_operacional") or "").strip():
                logger.warning(
                    "Categoria operacional vacia tras primera lectura; "
                    "reintento acotado post-OFSC."
                )
                await page.wait_for_timeout(700)

                categoria_retry = await _first_visible_across_frames(
                    page,
                    [
                        '[id="ar304420021_data"]',
                        '[testid="ar304420021_data"]',
                    ],
                    3500,
                )

                if categoria_retry:
                    _, categoria_locator_retry, categoria_selector_retry = (
                        categoria_retry
                    )
                    payload["categoria_operacional"] = await _element_value(
                        categoria_locator_retry
                    )
                    if str(
                        payload.get("categoria_operacional") or ""
                    ).strip().casefold() in {
                        "",
                        "none",
                        "none set",
                        "null",
                        "n/a",
                        "na",
                        "not set",
                    }:
                        payload["categoria_operacional"] = ""
                    logger.info(
                        "Categoria operacional recuperada en retry con %s: %s",
                        categoria_selector_retry,
                        payload["categoria_operacional"],
                    )

                if not str(
                    payload.get("categoria_operacional") or ""
                ).strip():
                    payload["categoria_operacional"] = await read_field_by_label(
                        page,
                        "Categor\u00eda operacional",
                        min(cfg.timeout_ms, 6000),
                        logger,
                    )
                    if str(
                        payload.get("categoria_operacional") or ""
                    ).strip().casefold() in {
                        "",
                        "none",
                        "none set",
                        "null",
                        "n/a",
                        "na",
                        "not set",
                    }:
                        payload["categoria_operacional"] = ""

                logger.info(
                    "Categoria operacional post-retry=%s",
                    payload.get("categoria_operacional") or "",
                )

            # HELIX_DIRECCIONES_METADATA_OPCIONAL_V1
            #
            # Para Direcciones, Aliado / Ciudad / Regional no condicionan
            # la navegacion. Si OFsC los entrega se conservan, pero no se
            # navega ni se espera por su ausencia.
            # HELIX_CGE_DESCRIPTION_IS_ENOUGH_V1
            _titulo_para_esenciales = str(
                payload.get("titulo_ot") or ""
            ).strip().upper()

            _categoria_para_esenciales = str(
                payload.get("categoria_operacional") or ""
            ).strip().upper()

            _descripcion_para_esenciales = str(
                payload.get("descripcion_ot") or ""
            ).strip()

            _cge_descripcion_suficiente = bool(
                _titulo_para_esenciales.startswith("CGE_")
                and "SERVICIOS FIJOS" in _categoria_para_esenciales
                and "RECLAMACION" in _categoria_para_esenciales
                and "USUARIO" in _categoria_para_esenciales
                and _descripcion_para_esenciales
            )

            if _cge_descripcion_suficiente:
                essential_fields = ()

                logger.info(
                    "HELIX_CGE_DESCRIPTION_IS_ENOUGH_V1: "
                    "descripcion WO disponible; "
                    "INC relacionado no es obligatorio."
                )
            else:
                essential_fields = (
                    "incidente_relacionado",
                )

            missing_essential = [
                field
                for field in essential_fields
                if not str(payload.get(field) or "").strip()
            ]

            # HELIX_CAIDA_TOTAL_IMPACT_TRUNK_V1
            # Si OFSC ya resolvio los campos esenciales y la WO es
            # CAIDA TOTAL FTTH, abrir el INC canonico exclusivamente
            # para leer Descripcion de Impacto.
            _titulo_impacto = str(
                payload.get("titulo_ot")
                or payload.get("descripcion_ot")
                or ""
            ).upper()

            _es_caida_total_impacto = bool(
                "CAIDA TOTAL" in _titulo_impacto
                and "TRONCAL GPON" in _titulo_impacto
            )

            if (
                not missing_essential
                and _es_caida_total_impacto
                and str(
                    payload.get("incidente_relacionado")
                    or ""
                ).strip()
            ):
                try:
                    (
                        _,
                        _impact_related_table,
                        _,
                        _impact_related_items,
                    ) = await open_related_tab_and_read(
                        page,
                        cfg,
                        logger,
                    )

                    _impact_inc_id = str(
                        payload.get("incidente_relacionado")
                        or ""
                    ).strip().upper()

                    _impact_incident = next(
                        (
                            item
                            for item in _impact_related_items
                            if str(
                                item.id or ""
                            ).strip().upper()
                            == _impact_inc_id
                        ),
                        None,
                    )

                    if _impact_incident is None:
                        _impact_incident = next(
                            (
                                item
                                for item in _impact_related_items
                                if normalize_text(
                                    item.tipo_relacion
                                )
                                == "CREADO POR"
                            ),
                            (
                                _impact_related_items[0]
                                if _impact_related_items
                                else None
                            ),
                        )

                    if _impact_incident is not None:
                        await click_incident_in_table(
                            _impact_related_table,
                            _impact_incident.id,
                            page,
                            cfg,
                            logger,
                        )

                        _impact_text = (
                            await read_field_by_label(
                                page,
                                "Descripción de Impacto",
                                min(
                                    cfg.timeout_ms,
                                    12000,
                                ),
                                logger,
                            )
                        )

                        if not _impact_text:
                            _impact_text = (
                                await read_field_by_label(
                                    page,
                                    "Descripcion de Impacto",
                                    min(
                                        cfg.timeout_ms,
                                        6000,
                                    ),
                                    logger,
                                )
                            )

                        payload["descripcion_impacto"] = (
                            str(_impact_text or "").strip()
                        )

                        _impact_parsed = (
                            parse_descripcion_impacto_troncal(
                                _impact_text
                            )
                        )

                        payload.update(
                            _impact_parsed
                        )

                        logger.info(
                            "HELIX_CAIDA_TOTAL_IMPACT_TRUNK_V1: "
                            "inc=%s impacto=%s troncal=%s",
                            _impact_incident.id,
                            payload.get(
                                "descripcion_impacto"
                            ),
                            payload.get(
                                "impacto_id_troncal"
                            ),
                        )

                except Exception as _impact_exc:
                    logger.warning(
                        "Fallback Descripcion de Impacto fallo: "
                        "%s: %s",
                        type(_impact_exc).__name__,
                        _impact_exc,
                    )

            if not missing_essential:
                payload["origen_datos"] = "OFSC"
                missing = [
                    field
                    for field in ("estado_ot",)
                    if not str(payload.get(field) or "").strip()
                ]
                if missing:
                    payload["codigo"] = "HELIX_OT_RESUMEN_INCOMPLETO"
                    payload["error"] = (
                        "No fue posible extraer: " + ", ".join(missing)
                    )
                    return payload

                payload["ok"] = True
                payload["codigo"] = "HELIX_OT_RESUMEN_OK"
                return payload

            payload["uso_fallback"] = True
            payload["origen_datos"] = (
                "OFSC_MAS_ELEMENTOS_RELACIONADOS"
                if ofsc["con_datos"]
                else "ELEMENTOS_RELACIONADOS"
            )
            logger.info(
                "Direcciones: obteniendo INC desde Elementos relacionados. "
                "Faltan=%s",
                missing_essential,
            )

            _, related_table, _, related_items = (
                await open_related_tab_and_read(
                    page,
                    cfg,
                    logger,
                )
            )

            if not related_items:
                payload["codigo"] = (
                    "HELIX_SIN_INCIDENTES_RELACIONADOS"
                )
                payload["error"] = (
                    "La OT no tiene incidentes relacionados."
                )
                return payload

            # HELIX_INCIDENT_CANONICAL_SELECTION_V1
            canonical_incident = str(
                payload.get("incidente_relacionado") or ""
            ).strip().upper()

            incident = None

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

            if incident is None:
                incident = next(
                    (
                        item
                        for item in related_items
                        if normalize_text(item.tipo_relacion)
                        == "CREADO POR"
                    ),
                    related_items[0],
                )

            logger.info(
                "INC seleccionado para fallback: canonical=%s seleccionado=%s relacion=%s",
                canonical_incident,
                incident.id,
                incident.tipo_relacion,
            )

            # HELIX_INCIDENT_PAYLOAD_MATCH_SELECTED_V1
            payload["incidente_relacionado"] = incident.id

            await click_incident_in_table(
                related_table,
                incident.id,
                page,
                cfg,
                logger,
            )

            # HELIX_CGE_CI_OPTIONAL_FALLBACK_V2
            #
            # CI NO es obligatorio.
            #
            # Este bloque se ejecuta unicamente cuando el flujo actual YA
            # abrio el INC relacionado. No agrega navegacion adicional.
            #
            # Si no existe CI o falla su lectura, el flujo continua exactamente
            # como antes.
            _ci_cge_title = str(
                payload.get("titulo_ot") or ""
            ).strip().upper()

            if _ci_cge_title.startswith("CGE_"):
                try:
                    _ci_found = await _first_visible_across_frames(
                        page,
                        [
                            '[id="ar303497400_data"]',
                            '[testid="ar303497400_data"]',
                        ],
                        1800,
                    )

                    if _ci_found:
                        (
                            _,
                            _ci_locator,
                            _ci_selector,
                        ) = _ci_found

                        _ci_value = str(
                            await _element_value(_ci_locator)
                            or ""
                        ).strip()

                        if not _ci_value:
                            try:
                                _ci_value = str(
                                    await _ci_locator.get_attribute(
                                        "aria-label"
                                    )
                                    or ""
                                ).strip()
                            except Exception:
                                pass

                        _ci_value = re.sub(
                            r"^CI\s+",
                            "",
                            _ci_value,
                            flags=re.IGNORECASE,
                        ).strip()

                        if _ci_value:
                            payload["ci"] = _ci_value

                            logger.info(
                                "HELIX_CGE_CI_OPTIONAL_FALLBACK_V2: "
                                "CI recuperado con %s: %s",
                                _ci_selector,
                                _ci_value,
                            )

                    _ci_desc_found = await _first_visible_across_frames(
                        page,
                        [
                            '[id="ar536871186_data"]',
                            '[testid="ar536871186_data"]',
                        ],
                        1800,
                    )

                    if _ci_desc_found:
                        (
                            _,
                            _ci_desc_locator,
                            _ci_desc_selector,
                        ) = _ci_desc_found

                        _ci_desc_value = str(
                            await _element_value(
                                _ci_desc_locator
                            )
                            or ""
                        ).strip()

                        if not _ci_desc_value:
                            try:
                                _ci_desc_value = str(
                                    await _ci_desc_locator.get_attribute(
                                        "aria-label"
                                    )
                                    or ""
                                ).strip()
                            except Exception:
                                pass

                        if _ci_desc_value:
                            payload["descripcion_ci"] = (
                                _ci_desc_value
                            )

                            logger.info(
                                "HELIX_CGE_CI_OPTIONAL_FALLBACK_V2: "
                                "Descripcion CI recuperada con %s: %s",
                                _ci_desc_selector,
                                _ci_desc_value,
                            )

                except Exception as _ci_exc:
                    logger.info(
                        "HELIX_CGE_CI_OPTIONAL_FALLBACK_V2: "
                        "CI no disponible; se conserva flujo anterior. "
                        "%s: %s",
                        type(_ci_exc).__name__,
                        _ci_exc,
                    )

            # HELIX_CGE_INC_DESCRIPTION_FIELD_1000000151_V1
            #
            # SmartIT Incident:
            #   label = ar1000000151_label -> Description
            #   data  = ar1000000151_data
            #
            # El campo vive dentro de pwa-frame / targetForm=Incident.
            _cge_title_actual = str(
                payload.get("titulo_ot") or ""
            ).strip().upper()

            if (
                _cge_title_actual.startswith("CGE_")
                and not str(
                    payload.get("descripcion_ot") or ""
                ).strip()
            ):
                _cge_desc = ""
                _cge_desc_frame = ""

                try:
                    for _frame in page.frames:
                        _frame_url = str(
                            _frame.url or ""
                        )

                        if "targetForm=Incident" not in _frame_url:
                            continue

                        _locator = _frame.locator(
                            'div#ar1000000151_data[role="article"]'
                        ).first

                        if await _locator.count() < 1:
                            continue

                        try:
                            _cge_desc = str(
                                await _locator.inner_text(
                                    timeout=min(
                                        cfg.timeout_ms,
                                        10000,
                                    )
                                )
                                or ""
                            ).strip()
                        except Exception:
                            _cge_desc = ""

                        if not _cge_desc:
                            try:
                                _cge_desc = str(
                                    await _locator.get_attribute(
                                        "aria-label"
                                    )
                                    or ""
                                ).strip()
                            except Exception:
                                _cge_desc = ""

                        if not _cge_desc:
                            try:
                                _cge_desc = str(
                                    await _locator.get_attribute(
                                        "title"
                                    )
                                    or ""
                                ).strip()
                            except Exception:
                                _cge_desc = ""

                        if _cge_desc:
                            _cge_desc_frame = _frame_url
                            break

                    if _cge_desc:
                        payload["descripcion_ot"] = _cge_desc

                        logger.info(
                            "HELIX_CGE_INC_DESCRIPTION_FIELD_1000000151_V1: "
                            "inc=%s len=%s frame=%s",
                            payload.get("incidente_relacionado") or "",
                            len(_cge_desc),
                            _cge_desc_frame,
                        )
                    else:
                        logger.warning(
                            "HELIX_CGE_INC_DESCRIPTION_FIELD_1000000151_V1: "
                            "campo ar1000000151_data vacio/no encontrado inc=%s",
                            payload.get("incidente_relacionado") or "",
                        )

                except Exception as _cge_exc:
                    logger.warning(
                        "HELIX_CGE_INC_DESCRIPTION_FIELD_1000000151_V1: "
                        "error=%s: %s",
                        type(_cge_exc).__name__,
                        _cge_exc,
                    )

            # HELIX_DIRECCIONES_METADATA_OPCIONAL_V1
            logger.info(
                "Direcciones: Aliado/Ciudad/Regional son metadata opcional; "
                "sin esperas adicionales."
            )

            missing = [
                field
                for field in (
                    "estado_ot",
                )
                if not str(payload.get(field) or "").strip()
            ]

            if missing:
                payload["codigo"] = (
                    "HELIX_OT_RESUMEN_INCOMPLETO"
                )
                payload["error"] = (
                    "No fue posible extraer: "
                    + ", ".join(missing)
                )
                return payload

            payload["ok"] = True
            payload["codigo"] = "HELIX_OT_RESUMEN_OK"
            return payload

        finally:
            await context.close()

