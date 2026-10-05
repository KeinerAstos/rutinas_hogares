from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.bitacora_settings import (
    BITACORA_ENGINE_DATA_DIR,
    BITACORA_LOG_DIR,
)

def load_env(path: Path):
    if not path.exists():
        return

    for raw in path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    ).splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split("=", 1)

        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )

load_env(PROJECT_ROOT / ".env")

from app.services.helix.smartit_scraper import (
    Settings,
    iniciar_sesion,
)

LOG_DIR = BITACORA_LOG_DIR
DATA_DIR = BITACORA_ENGINE_DATA_DIR

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("helix_bitacora_v5")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.propagate = False

fmt = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s"
)

fh = logging.FileHandler(
    LOG_DIR / "helix_bitacora_v5.log",
    encoding="utf-8",
)
fh.setFormatter(fmt)
logger.addHandler(fh)

# Visible en consola durante validacion.
sh = logging.StreamHandler(sys.stderr)
sh.setFormatter(fmt)
logger.addHandler(sh)

def norm(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()

def up(value):
    return norm(value).upper()

async def first_visible(page, selector):
    for frame in list(page.frames):
        try:
            loc = frame.locator(selector)

            for i in range(
                min(await loc.count(), 80)
            ):
                x = loc.nth(i)

                if await x.is_visible():
                    return frame, x
        except Exception:
            pass

    return None

async def preparar_caja_busqueda(page):
    logger.info("Preparando caja de busqueda.")

    for intento in range(100):
        found = await first_visible(
            page,
            "#globalSearchBox",
        )

        if found:
            logger.info(
                "Caja global visible."
            )
            return found[1]

        button = await first_visible(
            page,
            "#header-search_button",
        )

        if button:
            logger.info(
                "Lupa encontrada."
            )

            try:
                await button[1].click(
                    timeout=15000
                )
            except Exception as exc:
                logger.info(
                    "Click lupa no concluyo: %s",
                    exc,
                )

        if intento in (
            0,
            5,
            15,
            30,
            60,
        ):
            logger.info(
                "Esperando buscador. intento=%s frames=%s",
                intento + 1,
                len(page.frames),
            )

        await page.wait_for_timeout(250)

    raise RuntimeError(
        "No se pudo abrir globalSearchBox."
    )

async def buscar_incidente(page, inc):
    logger.info(
        "Buscando %s",
        inc,
    )

    caja = await preparar_caja_busqueda(
        page
    )

    await caja.fill("")
    await caja.fill(inc)
    await caja.press("Enter")

    panel = page.locator(
        "div.search__results-panel"
    )

    try:
        await panel.wait_for(
            state="visible",
            timeout=30000,
        )
    except Exception:
        pass

    async def encontrar_visible():
        for frame in list(page.frames):
            try:
                candidatos = frame.locator(
                    'div.results-panel__item-layout[role="link"]'
                )

                total = await candidatos.count()
            except Exception:
                continue

            for i in range(total):
                item = candidatos.nth(i)

                try:
                    if not await item.is_visible():
                        continue

                    id_span = item.locator(
                        "div.search-item-layout__id span"
                    )

                    if not await id_span.count():
                        continue

                    ticket_id = (
                        await id_span.first.inner_text()
                    ).strip().upper()

                    if ticket_id != inc.upper():
                        continue

                    logger.info(
                        "INC exacto encontrado. item=%s",
                        i,
                    )

                    await item.scroll_into_view_if_needed()

                    try:
                        await item.click(
                            timeout=20000
                        )
                    except Exception as exc:
                        logger.info(
                            "Click normal fallo: %s. Probando force=True.",
                            exc,
                        )

                        await item.click(
                            force=True,
                            timeout=10000,
                        )

                    await page.wait_for_timeout(
                        2200
                    )

                    logger.info(
                        "INC exacto ABIERTO."
                    )

                    return True
                except Exception as exc:
                    logger.info(
                        "Candidato no usable item=%s error=%s",
                        i,
                        exc,
                    )

        return False

    if await encontrar_visible():
        return

    mostrar_todo = None

    for frame in list(page.frames):
        try:
            loc = frame.locator(
                '[ux-id="show-all-link"]'
            )

            if (
                await loc.count()
                and await loc.first.is_visible()
            ):
                mostrar_todo = loc.first
                break
        except Exception:
            pass

    if mostrar_todo is not None:
        try:
            await mostrar_todo.click(
                timeout=15000
            )

            await page.wait_for_timeout(
                1800
            )

            logger.info(
                "Mostrar todo activado."
            )
        except Exception:
            pass

    for intento in range(30):
        if await encontrar_visible():
            return

        moved = False

        for frame in list(page.frames):
            try:
                viewport = frame.locator(
                    "div.results-panel__items-viewport"
                )

                if await viewport.count():
                    await viewport.first.evaluate(
                        "(el) => { el.scrollTop = el.scrollHeight; }"
                    )
                    moved = True
            except Exception:
                pass

        if not moved:
            try:
                await page.mouse.wheel(
                    0,
                    2400,
                )
            except Exception:
                pass

        await page.wait_for_timeout(
            700
        )

    raise RuntimeError(
        f"No encontre el ticket exacto {inc}."
    )

async def extraer_ci_principal(page):
    logger.info(
        "Buscando CI principal."
    )

    for intento in range(80):
        for frame in list(page.frames):
            try:
                loc = frame.locator(
                    "button#ar303497400_data"
                ).first

                if (
                    await loc.count()
                    and await loc.is_visible()
                ):
                    text = norm(
                        await loc.inner_text()
                    )

                    aria = norm(
                        await loc.get_attribute(
                            "aria-label"
                        )
                    )

                    value = text

                    if (
                        not value
                        and aria.upper().startswith("CI ")
                    ):
                        value = aria[3:].strip()

                    if value:
                        logger.info(
                            "CI_PRINCIPAL=%s",
                            value,
                        )

                        return value
            except Exception:
                pass

        if intento in (
            0,
            5,
            15,
            30,
            60,
        ):
            logger.info(
                "Esperando CI principal. intento=%s",
                intento + 1,
            )

        await page.wait_for_timeout(
            250
        )

    return ""

async def localizar_tab_config(page):
    logger.info(
        "Buscando Elementos de configuracion."
    )

    for _ in range(80):
        for frame in list(page.frames):
            try:
                candidates = frame.locator(
                    'button, a, [role="tab"], [role="button"]'
                )

                count = min(
                    await candidates.count(),
                    400,
                )
            except Exception:
                continue

            for i in range(count):
                x = candidates.nth(i)

                try:
                    if not await x.is_visible():
                        continue

                    text = norm(
                        await x.inner_text()
                    )

                    upper_text = text.upper()

                    if (
                        "ELEMENTOS DE CONFIGURACIÓN"
                        in upper_text
                        or
                        "ELEMENTOS DE CONFIGURACION"
                        in upper_text
                    ):
                        match = re.search(
                            r"(\d+)\s*$",
                            text,
                        )

                        total = (
                            int(match.group(1))
                            if match
                            else None
                        )

                        logger.info(
                            "TAB_CONFIG=%r COUNT=%r",
                            text,
                            total,
                        )

                        return frame, x, total
                except Exception:
                    pass

        await page.wait_for_timeout(
            250
        )

    return None

async def leer_tablas_visibles(page):
    tablas = []

    for frame_index, frame in enumerate(
        list(page.frames)
    ):
        try:
            all_tables = frame.locator(
                "table"
            )

            table_count = min(
                await all_tables.count(),
                80,
            )
        except Exception:
            continue

        for table_index in range(
            table_count
        ):
            table = all_tables.nth(
                table_index
            )

            try:
                if not await table.is_visible():
                    continue

                headers_loc = table.locator(
                    "thead th"
                )

                headers = []

                for h in range(
                    await headers_loc.count()
                ):
                    headers.append(
                        norm(
                            await headers_loc.nth(
                                h
                            ).inner_text()
                        )
                    )

                rows = table.locator(
                    "tbody tr"
                )

                row_count = await rows.count()

                if row_count <= 0:
                    continue

                data = []

                for r in range(row_count):
                    cells = rows.nth(
                        r
                    ).locator("td")

                    values = []

                    for c in range(
                        await cells.count()
                    ):
                        try:
                            values.append(
                                norm(
                                    await cells.nth(
                                        c
                                    ).inner_text()
                                )
                            )
                        except Exception:
                            values.append("")

                    if any(values):
                        data.append(values)

                if data:
                    tablas.append(
                        {
                            "frame": frame_index,
                            "table": table_index,
                            "headers": headers,
                            "rows": data,
                        }
                    )
            except Exception:
                pass

    return tablas

def tabla_config_correcta(tablas):
    best = None

    for table in tablas:
        headers = up(
            " ".join(
                table.get(
                    "headers",
                    [],
                )
            )
        )

        score = sum(
            1
            for token in (
                "TIPO DE ASOCI",
                "NOMBRE",
                "TIPO",
                "ESTADO",
                "DESCRIP",
            )
            if token in headers
        )

        candidate = (
            score,
            len(
                table.get(
                    "rows",
                    [],
                )
            ),
            table,
        )

        if (
            best is None
            or candidate[:2] > best[:2]
        ):
            best = candidate

    return (
        best[2]
        if best is not None
        else None
    )

def dedupe(items):
    result = []
    seen = set()

    for item in items:
        key = up(
            item.get("nombre")
        )

        if (
            not key
            or key in seen
        ):
            continue

        seen.add(key)
        result.append(item)

    return result

async def consultar(incident):
    cfg = Settings.from_env()

    result = {
        "ok": False,
        "incidente": incident,
        "ci_principal": "",
        "config_count_reportado": None,
        "elementos": [],
        "error": "",
    }

    # DURANTE VALIDACION usamos perfil aislado y navegador visible,
    # exactamente como la V5 que ya funciono.
    profile = (
        DATA_DIR
        / "perfil_validacion_v5"
    )

    profile.mkdir(
        parents=True,
        exist_ok=True,
    )

    async with async_playwright() as p:
        context = (
            await p.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=False,
                viewport={
                    "width": 1600,
                    "height": 950,
                },
                args=[
                    "--start-maximized",
                    "--disable-notifications",
                ],
            )
        )

        context.set_default_timeout(
            cfg.timeout_ms
        )

        context.set_default_navigation_timeout(
            cfg.timeout_ms
        )

        page = (
            context.pages[0]
            if context.pages
            else await context.new_page()
        )

        try:
            logger.info(
                "LOGIN HELIX."
            )

            await iniciar_sesion(
                page,
                cfg,
                logger,
            )

            await buscar_incidente(
                page,
                incident,
            )

            # La V5 que funciono NO necesitaba "Ver incidencia completa".
            # Despues de abrir el resultado, CI y la pestaña ya eran visibles.
            ci = await extraer_ci_principal(
                page
            )

            result[
                "ci_principal"
            ] = ci

            elementos = []

            if ci:
                elementos.append(
                    {
                        "nombre": ci,
                        "origen": "CI_PRINCIPAL",
                        "tipo_asociacion": "",
                        "tipo_helix": "",
                        "estado_helix": "",
                        "modelo": "",
                        "fabricante": "",
                        "descripcion": "",
                    }
                )

            tab = await localizar_tab_config(
                page
            )

            if tab is None:
                raise RuntimeError(
                    "No se encontro la pestaña Elementos de configuracion."
                )

            _, tab_loc, count = tab

            result[
                "config_count_reportado"
            ] = count

            if (
                count is not None
                and count > 0
            ):
                try:
                    await tab_loc.click(
                        timeout=15000
                    )
                except Exception:
                    await tab_loc.click(
                        force=True,
                        timeout=10000,
                    )

                await page.wait_for_timeout(
                    1600
                )

                tablas = await leer_tablas_visibles(
                    page
                )

                best = tabla_config_correcta(
                    tablas
                )

                if best is not None:
                    for row in best["rows"]:
                        # Confirmado por la prueba V5:
                        # col 0 = tipo asociacion
                        # col 1 = Nombre
                        # col 2 = Tipo
                        # col 3 = Estado
                        # col 4 = Modelo
                        # col 5 = Fabricante
                        # col 6 = Descripcion
                        if len(row) < 2:
                            continue

                        nombre = norm(
                            row[1]
                        )

                        if not nombre:
                            continue

                        elementos.append(
                            {
                                "nombre": nombre,
                                "origen": "ELEMENTOS_CONFIGURACION",
                                "tipo_asociacion": (
                                    row[0]
                                    if len(row) > 0
                                    else ""
                                ),
                                "tipo_helix": (
                                    row[2]
                                    if len(row) > 2
                                    else ""
                                ),
                                "estado_helix": (
                                    row[3]
                                    if len(row) > 3
                                    else ""
                                ),
                                "modelo": (
                                    row[4]
                                    if len(row) > 4
                                    else ""
                                ),
                                "fabricante": (
                                    row[5]
                                    if len(row) > 5
                                    else ""
                                ),
                                "descripcion": (
                                    row[6]
                                    if len(row) > 6
                                    else ""
                                ),
                            }
                        )

            result[
                "elementos"
            ] = dedupe(
                elementos
            )

            result["ok"] = bool(
                result["elementos"]
            )

        except Exception as exc:
            result["error"] = (
                f"{type(exc).__name__}: {exc}"
            )

            logger.exception(
                "FALLO INC=%s",
                incident,
            )

        finally:
            await context.close()

    return result

def main():
    if len(sys.argv) != 2:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Uso: incident_config_items_cli.py INC000..."
                },
                ensure_ascii=False,
            )
        )
        return 2

    incident = (
        sys.argv[1]
        .strip()
        .upper()
    )

    if not re.fullmatch(
        r"INC\d{6,12}",
        incident,
    ):
        print(
            json.dumps(
                {
                    "ok": False,
                    "incidente": incident,
                    "error": "INC_INVALIDO",
                },
                ensure_ascii=False,
            )
        )
        return 2

    result = asyncio.run(
        consultar(
            incident
        )
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
        )
    )

    return (
        0
        if result["ok"]
        else 2
    )

if __name__ == "__main__":
    raise SystemExit(main())