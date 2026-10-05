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
                    await close.click(timeout=5000)
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
            await click_global_search(
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

            if (
                _es_troncal_ftth_context
                and (
                    not str(payload.get("elemento_red") or "").strip()
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

                                    if (
                                        not _actual
                                        and _alterno
                                    ):
                                        payload[_campo] = (
                                            _alterno
                                        )

                                        _recuperados[
                                            _campo
                                        ] = _alterno

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

            ofsc = await intentar_integracion_ofsc(page, logger)
            payload["tiempo_ofsc_seg"] = ofsc["duracion_seg"]
            payload["estado_ofsc"] = ofsc["estado_ofsc"]

            for field in (
                "incidente_relacionado",
                "aliado",
                "ciudad",
                "regional",
            ):
                value = str(ofsc.get(field) or "").strip()
                if value:
                    payload[field] = value

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

                logger.info(
                    "Categoria operacional post-retry=%s",
                    payload.get("categoria_operacional") or "",
                )

            essential_fields = (
                "incidente_relacionado",
                "aliado",
                "ciudad",
                "regional",
            )
            missing_essential = [
                field
                for field in essential_fields
                if not str(payload.get(field) or "").strip()
            ]

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
                "OFsC incompleto. Faltan %s; usando Elementos relacionados.",
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

            if not payload["aliado"]:
                payload["aliado"] = await read_field_by_label(
                    page,
                    "Aliado",
                    min(cfg.timeout_ms, 15000),
                    logger,
                )
            if not payload["ciudad"]:
                payload["ciudad"] = await read_field_by_label(
                    page,
                    "Ciudad",
                    min(cfg.timeout_ms, 15000),
                    logger,
                )
            if not payload["regional"]:
                payload["regional"] = await read_field_by_label(
                    page,
                    "Regional",
                    min(cfg.timeout_ms, 15000),
                    logger,
                )

            missing = [
                field
                for field in (
                    "estado_ot",
                    "aliado",
                    "ciudad",
                    "regional",
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
