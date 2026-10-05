from __future__ import annotations

import csv
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

TABLE_TITLE = "Lista de prioridad de cancelación de clientes"
DAILY_HEALTH_TEXT = "Salud diaria del nodo"
CURRENT_HEALTH_TEXT = "Salud actual del nodo"
ACTIONS_TEXT = "Acciones"
SHOW_ALL_TEXT = "Mostrar todo"
EXPORT_TABLE_TEXT = "Exportar tabla"
LOADING_TEXTS = ("Cargando...", "Loading...")

DEFAULT_TABLE_TIMEOUT_MS = 150000
DEFAULT_SHOW_ALL_TIMEOUT_MS = 420000
DEFAULT_DOWNLOAD_TIMEOUT_MS = 120000

# XPERTrak REGIONALES puede declarar N pero materializar N-1 filas tras
# "Mostrar todo", aun cuando el indicador "Cargando..." ya desapareció.
# Se acepta SOLO una diferencia máxima de 1 y únicamente si el estado
# permanece estable durante varias verificaciones consecutivas.
SHOW_ALL_OFF_BY_ONE_MAX = 1
SHOW_ALL_OFF_BY_ONE_STABLE_POLLS = 5
DEFAULT_DOWNLOAD_DIR = (
    r"C:\xampp\htdocs\rutinas_hogares\api_orquestador_hogares\data\xpertrak_dashboard"
)

_EXPORT_LOCK = threading.Lock()
_PT_MODULE = None
_PT_LOCK = threading.Lock()


def _get_pt():
    """Carga PathTrak solo bajo demanda; no durante el import de FastAPI."""
    global _PT_MODULE
    if _PT_MODULE is not None:
        return _PT_MODULE

    with _PT_LOCK:
        if _PT_MODULE is None:
            from app.services.pathtrak import pathtrak_spectrum
            _PT_MODULE = pathtrak_spectrum

    return _PT_MODULE


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _settings() -> dict[str, Any]:
    download_dir = Path(
        os.getenv(
            "XPERTRAK_DASHBOARD_DOWNLOAD_DIR",
            DEFAULT_DOWNLOAD_DIR,
        ).strip()
        or DEFAULT_DOWNLOAD_DIR
    )
    return {
        "table_timeout_ms": _env_int(
            "XPERTRAK_DASHBOARD_TABLE_TIMEOUT_MS",
            DEFAULT_TABLE_TIMEOUT_MS,
            10000,
            300000,
        ),
        "show_all_timeout_ms": _env_int(
            "XPERTRAK_DASHBOARD_SHOW_ALL_TIMEOUT_MS",
            DEFAULT_SHOW_ALL_TIMEOUT_MS,
            30000,
            600000,
        ),
        "download_timeout_ms": _env_int(
            "XPERTRAK_DASHBOARD_DOWNLOAD_TIMEOUT_MS",
            DEFAULT_DOWNLOAD_TIMEOUT_MS,
            10000,
            300000,
        ),
        "download_dir": download_dir,
    }


def health() -> dict[str, Any]:
    cfg = _settings()
    try:
        pt = _get_pt()
    except Exception as exc:
        return {
            "ok": False,
            "codigo": "XPERTRAK_DASHBOARD_PATHTRAK_IMPORT_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "download_dir": str(cfg["download_dir"]),
            "busy": _EXPORT_LOCK.locked(),
        }

    return {
        "ok": True,
        "codigo": "XPERTRAK_DASHBOARD_READY",
        "credentials_configured": bool(pt.PATHTRAK_USER and pt.PATHTRAK_PASSWORD),
        "centro_configured": bool(pt.PATHTRAK_CENTRO_URL),
        "regionales_configured": bool(pt.PATHTRAK_REGIONALES_URL),
        "proxy_configured": bool(pt.PATHTRAK_PROXY),
        "headless": bool(pt.PATHTRAK_HEADLESS),
        "download_dir": str(cfg["download_dir"]),
        "busy": _EXPORT_LOCK.locked(),
    }


def _first_visible(locator, max_count: int = 200):
    try:
        count = locator.count()
    except Exception:
        return None

    for index in range(min(count, max_count)):
        try:
            item = locator.nth(index)
            if item.is_visible():
                return item
        except Exception:
            continue

    return None


def _exact_visible_dropdown_anchor(scope, text: str):
    try:
        anchors = scope.locator("a.dropdown-item")
        count = anchors.count()
    except Exception:
        return None

    for index in range(min(count, 200)):
        item = anchors.nth(index)
        try:
            if not item.is_visible():
                continue
            if (item.inner_text(timeout=1500) or "").strip() == text:
                return item
        except Exception:
            continue

    return None


def _counter_from_text(text: str) -> tuple[Optional[int], Optional[int]]:
    matches = re.findall(
        r"(?<!\d)(\d[\d.,]*)\s+de\s+(\d[\d.,]*)(?!\d)",
        text or "",
        flags=re.IGNORECASE,
    )

    parsed: list[tuple[int, int]] = []
    for shown_raw, total_raw in matches:
        try:
            shown = int(re.sub(r"[^\d]", "", shown_raw))
            total = int(re.sub(r"[^\d]", "", total_raw))
            parsed.append((shown, total))
        except Exception:
            continue

    if not parsed:
        return None, None

    # El texto recibido SIEMPRE debe pertenecer a la tarjeta objetivo.
    # Si el componente llegara a exponer más de un contador, se toma el mayor.
    return max(parsed, key=lambda item: item[1])


def _get_table_card(page):
    title = _first_visible(page.get_by_text(TABLE_TITLE, exact=True))
    if title is None:
        return None

    return title.locator(
        "xpath=ancestor::div["
        "contains(concat(' ', normalize-space(@class), ' '), ' card-component ') "
        "and contains(concat(' ', normalize-space(@class), ' '), ' list ')"
        "][1]"
    )


def _card_loading_visible(card) -> bool:
    for text in LOADING_TEXTS:
        try:
            loc = card.get_by_text(text, exact=True)
            count = loc.count()
        except Exception:
            continue

        for index in range(min(count, 20)):
            try:
                if loc.nth(index).is_visible():
                    return True
            except Exception:
                continue

    return False


def _card_state(page):
    card = _get_table_card(page)
    if card is None:
        return None, None, None, True

    try:
        text = card.inner_text(timeout=5000)
    except Exception:
        return card, None, None, True

    shown, total = _counter_from_text(text)
    loading = _card_loading_visible(card)
    return card, shown, total, loading


def _wait_card_ready(
    page,
    timeout_ms: int,
    error_code: str,
    stable_required: int = 2,
) -> tuple[Any, int, int]:
    deadline = time.monotonic() + timeout_ms / 1000.0
    stable = 0
    last_counter: tuple[Optional[int], Optional[int]] = (None, None)

    while time.monotonic() < deadline:
        card, shown, total, loading = _card_state(page)
        current = (shown, total)

        if (
            card is not None
            and shown is not None
            and total is not None
            and total > 0
            and not loading
        ):
            if current == last_counter:
                stable += 1
            else:
                stable = 1
                last_counter = current

            if stable >= stable_required:
                return card, int(shown), int(total)
        else:
            stable = 0
            last_counter = current

        page.wait_for_timeout(900)

    raise RuntimeError(error_code)


def _metric_button(card):
    for text in (DAILY_HEALTH_TEXT, CURRENT_HEALTH_TEXT):
        item = _first_visible(card.get_by_role("button", name=text, exact=True))
        if item is not None:
            return item, text
    return None, None


def _wait_metric_selected(page, expected: str, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000.0

    while time.monotonic() < deadline:
        card = _get_table_card(page)
        if card is not None:
            item = _first_visible(
                card.get_by_role("button", name=expected, exact=True)
            )
            if item is not None:
                return card, item
        page.wait_for_timeout(500)

    raise RuntimeError("XPERTRAK_FILTRO_SALUD_DIARIA_NO_CONFIRMADO")


def _select_daily_health(page, timeout_ms: int) -> None:
    card, _, _ = _wait_card_ready(
        page,
        timeout_ms,
        "XPERTRAK_TABLA_CARGA_INICIAL_TIMEOUT",
    )

    button, current_metric = _metric_button(card)
    if button is None:
        raise RuntimeError("XPERTRAK_FILTRO_METRICA_NO_ENCONTRADO")

    if current_metric == DAILY_HEALTH_TEXT:
        return

    button.click(timeout=7000)
    page.wait_for_timeout(400)

    # XPERTrak también dibuja el mismo texto dentro de un SVG. Por eso la
    # opción válida se identifica explícitamente por a.dropdown-item.
    daily_item = _exact_visible_dropdown_anchor(page, DAILY_HEALTH_TEXT)
    if daily_item is None:
        raise RuntimeError("XPERTRAK_FILTRO_SALUD_DIARIA_NO_ENCONTRADO")

    daily_item.click(timeout=7000)
    _wait_metric_selected(page, DAILY_HEALTH_TEXT, timeout_ms)


def _open_actions(card) -> None:
    button = _first_visible(
        card.get_by_role("button", name=ACTIONS_TEXT, exact=True)
    )
    if button is None:
        raise RuntimeError("XPERTRAK_ACCIONES_NO_ENCONTRADO")

    button.click(timeout=7000)


def _find_action_item(page, card, text: str):
    item = _exact_visible_dropdown_anchor(card, text)
    if item is not None:
        return item
    return _exact_visible_dropdown_anchor(page, text)


def _wait_filter_refresh(page, timeout_ms: int) -> tuple[Any, int, int]:
    _wait_metric_selected(page, DAILY_HEALTH_TEXT, timeout_ms)
    return _wait_card_ready(
        page,
        timeout_ms,
        "XPERTRAK_FILTRO_SALUD_DIARIA_TIMEOUT",
    )


def _wait_show_all_complete(
    page,
    initial_shown: int,
    expected_total: int,
    timeout_ms: int,
) -> tuple[int, int]:
    deadline = time.monotonic() + timeout_ms / 1000.0
    stable = 0
    last_state: tuple[Optional[int], Optional[int], bool] | None = None
    last_observed: tuple[Optional[int], Optional[int], bool] | None = None

    while time.monotonic() < deadline:
        card, shown, total, loading = _card_state(page)
        state = (shown, total, loading)
        last_observed = state

        exact_complete = (
            card is not None
            and shown is not None
            and total is not None
            and total > 0
            and shown >= total
            and total >= expected_total
            and not loading
        )

        off_by_one_complete = False
        if (
            card is not None
            and shown is not None
            and total is not None
            and total > 0
            and total >= expected_total
            and not loading
        ):
            difference = int(total) - int(shown)
            off_by_one_complete = (
                0 < difference <= SHOW_ALL_OFF_BY_ONE_MAX
                and int(shown) >= int(expected_total) - SHOW_ALL_OFF_BY_ONE_MAX
            )

        if exact_complete or off_by_one_complete:
            if state == last_state:
                stable += 1
            else:
                stable = 1
                last_state = state

            required_polls = (
                2
                if exact_complete
                else SHOW_ALL_OFF_BY_ONE_STABLE_POLLS
            )

            if stable >= required_polls:
                return int(shown), int(total)
        else:
            stable = 0
            last_state = state

        page.wait_for_timeout(2000)

    last_shown = last_observed[0] if last_observed else None
    last_total = last_observed[1] if last_observed else None
    last_loading = last_observed[2] if last_observed else None

    raise RuntimeError(
        "XPERTRAK_MOSTRAR_TODO_TIMEOUT:"
        f"initial={initial_shown}:expected={expected_total}:"
        f"last_shown={last_shown}:last_total={last_total}:"
        f"loading={last_loading}"
    )


def _decode_csv(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except Exception:
            continue
    raise RuntimeError("XPERTRAK_CSV_NO_DECODIFICABLE")


def _inspect_csv(path: Path) -> dict[str, Any]:
    text, encoding = _decode_csv(path)
    sample = text[:8192]

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ","

    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        raise RuntimeError("XPERTRAK_CSV_VACIO")

    headers = [str(value).strip() for value in rows[0]]
    data_rows = [row for row in rows[1:] if any(str(value).strip() for value in row)]

    return {
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": headers,
        "registros": len(data_rows),
        "size_bytes": path.stat().st_size,
    }


def _launch_browser(playwright):
    pt = _get_pt()
    options: dict[str, Any] = {
        "headless": bool(pt.PATHTRAK_HEADLESS),
        "slow_mo": 250 if not pt.PATHTRAK_HEADLESS else 0,
        "args": [
            "--ignore-certificate-errors",
            "--allow-insecure-localhost",
            "--disable-extensions",
            "--disable-features=Translate",
            "--start-maximized",
        ],
    }

    if pt.PATHTRAK_PROXY:
        options["proxy"] = {"server": pt.PATHTRAK_PROXY}
    else:
        options["args"].extend(
            [
                "--no-proxy-server",
                "--proxy-bypass-list=*",
                "--disable-http2",
                "--disable-quic",
            ]
        )

    return playwright.chromium.launch(**options)


def _export_region(playwright, region: str, url: str) -> dict[str, Any]:
    pt = _get_pt()
    cfg = _settings()
    started = time.monotonic()
    browser = None
    context = None

    try:
        if not url:
            return {
                "ok": False,
                "codigo": "XPERTRAK_REGION_URL_NO_CONFIGURADA",
                "region": region,
            }

        browser = _launch_browser(playwright)
        context = browser.new_context(
            ignore_https_errors=True,
            http_credentials={
                "username": pt.PATHTRAK_USER,
                "password": pt.PATHTRAK_PASSWORD,
            },
            viewport={"width": 1920, "height": 1200},
            accept_downloads=True,
            locale="es-CO",
        )
        page = context.new_page()

        # Reutiliza exactamente el login productivo que ya usa QoE/Ruido.
        pt.login_pathtrak(page, url)

        card, initial_shown, initial_total = _wait_card_ready(
            page,
            cfg["table_timeout_ms"],
            "XPERTRAK_TABLA_CARGA_INICIAL_TIMEOUT",
        )

        _select_daily_health(page, cfg["table_timeout_ms"])

        card, shown_after_filter, total_after_filter = _wait_filter_refresh(
            page,
            cfg["table_timeout_ms"],
        )

        _open_actions(card)
        page.wait_for_timeout(350)

        show_all_item = _find_action_item(page, card, SHOW_ALL_TEXT)
        if show_all_item is None:
            raise RuntimeError("XPERTRAK_MOSTRAR_TODO_NO_ENCONTRADO")

        show_all_item.click(timeout=7000)

        shown_all, total_all = _wait_show_all_complete(
            page,
            shown_after_filter,
            total_after_filter,
            cfg["show_all_timeout_ms"],
        )

        card = _get_table_card(page)
        if card is None:
            raise RuntimeError("XPERTRAK_TABLA_NO_VISIBLE_ANTES_EXPORTAR")

        _open_actions(card)
        page.wait_for_timeout(350)

        export_item = _find_action_item(page, card, EXPORT_TABLE_TEXT)
        if export_item is None:
            raise RuntimeError("XPERTRAK_EXPORTAR_TABLA_NO_ENCONTRADO")

        with page.expect_download(timeout=cfg["download_timeout_ms"]) as info:
            export_item.click(timeout=7000)

        download = info.value

        cfg["download_dir"].mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_path = cfg["download_dir"] / (
            f"salud_diaria_{region.lower()}_{stamp}.csv"
        )
        download.save_as(str(final_path))

        csv_info = _inspect_csv(final_path)

        shown_all = int(shown_all)
        total_all = int(total_all)
        csv_records = int(csv_info["registros"])
        ui_difference = total_all - shown_all

        ui_difference_accepted = (
            0 <= ui_difference <= SHOW_ALL_OFF_BY_ONE_MAX
        )

        # La fuente de verdad final es el CSV descargado. Para evitar aceptar
        # exportaciones incompletas, sus filas deben coincidir exactamente
        # con uno de los dos valores observados en la UI (mostrados o total)
        # y nunca diferir en más de 1 del total declarado por XPERTrak.
        accepted_csv_counts = {shown_all, total_all}
        csv_difference_from_declared = abs(total_all - csv_records)
        csv_valid = (
            ui_difference_accepted
            and csv_records in accepted_csv_counts
            and csv_difference_from_declared <= SHOW_ALL_OFF_BY_ONE_MAX
        )

        base_result = {
            "region": region,
            "archivo": str(final_path),
            "contador_inicial": {
                "mostrados": initial_shown,
                "total": initial_total,
            },
            "contador_salud_diaria": {
                "mostrados": shown_after_filter,
                "total": total_after_filter,
            },
            "contador_mostrar_todo": {
                "mostrados": shown_all,
                "total": total_all,
            },
            "discrepancia_ui": {
                "detectada": ui_difference != 0,
                "diferencia": ui_difference,
                "maximo_aceptado": SHOW_ALL_OFF_BY_ONE_MAX,
                "aceptada": ui_difference_accepted,
            },
            "fuente_verdad_final": "CSV",
            "csv": csv_info,
            "duracion_seg": round(time.monotonic() - started, 2),
        }

        if not csv_valid:
            return {
                "ok": False,
                "codigo": "XPERTRAK_CSV_INCOMPLETO",
                "registros_csv": csv_records,
                "registros_mostrados_ui": shown_all,
                "registros_declarados_ui": total_all,
                "diferencia_csv_vs_declarado": csv_difference_from_declared,
                **base_result,
            }

        result = {
            "ok": True,
            "codigo": "XPERTRAK_SALUD_DIARIA_REGION_OK",
            "registros": csv_records,
            **base_result,
        }

        if ui_difference != 0:
            result["advertencia"] = (
                "XPERTRAK_UI_OFF_BY_ONE_ACEPTADO:"
                f"mostrados={shown_all}:declarados={total_all}:"
                f"csv={csv_records}"
            )

        return result

    except Exception as exc:
        return {
            "ok": False,
            "codigo": "XPERTRAK_SALUD_DIARIA_REGION_ERROR",
            "region": region,
            "error": f"{type(exc).__name__}: {exc}",
            "duracion_seg": round(time.monotonic() - started, 2),
        }
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def exportar_salud_diaria_ambas() -> dict[str, Any]:
    if not _EXPORT_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "codigo": "XPERTRAK_EXPORT_EN_PROGRESO",
            "error": "Ya existe una exportacion XPERTrak en curso.",
        }

    started = time.monotonic()

    try:
        try:
            pt = _get_pt()
        except Exception as exc:
            return {
                "ok": False,
                "codigo": "XPERTRAK_DASHBOARD_PATHTRAK_IMPORT_ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            }

        if not pt.PATHTRAK_USER or not pt.PATHTRAK_PASSWORD:
            return {
                "ok": False,
                "codigo": "XPERTRAK_CREDENCIALES_NO_CONFIGURADAS",
            }

        # Playwright también se carga solo cuando realmente se solicita exportar.
        from playwright.sync_api import sync_playwright

        resultados: list[dict[str, Any]] = []
        with sync_playwright() as playwright:
            resultados.append(
                _export_region(
                    playwright,
                    "CENTRO",
                    pt.PATHTRAK_CENTRO_URL,
                )
            )
            resultados.append(
                _export_region(
                    playwright,
                    "REGIONALES",
                    pt.PATHTRAK_REGIONALES_URL,
                )
            )

        ok = all(item.get("ok") for item in resultados)

        return {
            "ok": ok,
            "codigo": (
                "XPERTRAK_SALUD_DIARIA_EXPORT_OK"
                if ok
                else "XPERTRAK_SALUD_DIARIA_EXPORT_PARCIAL_O_ERROR"
            ),
            "resultados": resultados,
            "duracion_total_seg": round(time.monotonic() - started, 2),
        }
    finally:
        _EXPORT_LOCK.release()
