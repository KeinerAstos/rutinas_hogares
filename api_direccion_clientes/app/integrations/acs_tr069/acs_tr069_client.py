#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from getpass import getpass
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from bs4 import BeautifulSoup
except Exception as exc:
    print(json.dumps({"ok": False, "estado": "BS4_NO_INSTALADO", "error": str(exc)}, ensure_ascii=False))
    sys.exit(2)

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception as exc:
    print(json.dumps({"ok": False, "estado": "PLAYWRIGHT_NO_INSTALADO", "error": str(exc)}, ensure_ascii=False))
    sys.exit(2)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ACS_URL = os.getenv("ACS_TR069_URL", "").strip()


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "si", "sí", "on"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", unescape(str(value))).strip()


def norm(value: Any) -> str:
    value = clean_text(value).lower()
    return (
        value.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )


def safe_prefix(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "", clean_text(value))[:80] or "acs"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def debug_step(message: str) -> None:
    print(f"[ACS] {datetime.now().strftime('%H:%M:%S')} - {message}", file=sys.stderr, flush=True)


@dataclass
class ACSResult:
    ok: bool
    estado: str
    mensaje: str
    url_inicial: str
    url_final: str
    serial: str
    screenshot: str = ""
    html: str = ""
    error: Optional[str] = None
    duracion_seg: Optional[float] = None
    datos: Optional[Dict[str, Any]] = None
    plantilla_acs: Optional[str] = None
    selectores: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "estado": self.estado,
            "mensaje": self.mensaje,
            "url_inicial": self.url_inicial,
            "url_final": self.url_final,
            "serial": self.serial,
            "screenshot": self.screenshot,
            "html": self.html,
            "error": self.error,
            "duracion_seg": self.duracion_seg,
            "datos": self.datos or {},
            "plantilla_acs": self.plantilla_acs or "",
            "selectores": self.selectores or {},
        }


def all_contexts(page) -> List[Tuple[Any, str]]:
    contexts: List[Tuple[Any, str]] = [(page, "page")]
    for frame in page.frames:
        try:
            contexts.append((frame, f"frame:{frame.name or frame.url}"))
        except Exception:
            continue
    return contexts


def first_visible(locator, timeout_ms: int = 700):
    try:
        count = locator.count()
    except Exception:
        return None

    for idx in range(count):
        item = locator.nth(idx)
        try:
            item.wait_for(state="visible", timeout=timeout_ms)
            return item
        except Exception:
            continue
    return None


def find_first(page, selectors: Iterable[str], timeout_ms: int = 1000):
    """
    Busca un selector sin quedarse pegado recorriendo todos los iframes.

    ACS tiene muchos frames. En la versión anterior cada selector podía esperar
    varios segundos por cada frame, por eso parecía que no hacía nada.
    """
    last_error = None
    started = time.perf_counter()

    # Timeout total real para toda la búsqueda.
    total_timeout = min(max(timeout_ms, 1500), 12000)

    # Timeout pequeño por selector/frame.
    per_selector_timeout = min(max(int(timeout_ms / 10), 250), 900)

    contexts = priority_contexts(page) if "priority_contexts" in globals() else all_contexts(page)

    while (time.perf_counter() - started) * 1000 < total_timeout:
        for context, label in contexts:
            for selector in selectors:
                try:
                    locator = context.locator(selector)
                    item = first_visible(locator, timeout_ms=per_selector_timeout)

                    if item:
                        return context, label, item, selector

                except Exception as exc:
                    last_error = exc
                    continue

        page.wait_for_timeout(250)

    raise RuntimeError(
        f"No encontré selector rápido {list(selectors)} después de "
        f"{round(time.perf_counter() - started, 1)}s. Último error: {last_error}"
    )


def wait_soft_network(page, timeout_ms: int = 8000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def set_input_value(locator, value: str, delay_ms: int = 35) -> str:
    locator.scroll_into_view_if_needed(timeout=5000)
    locator.click(timeout=5000)

    try:
        locator.press("Control+A", timeout=2000)
        locator.press("Backspace", timeout=2000)
    except Exception:
        try:
            locator.fill("", timeout=2000)
        except Exception:
            pass

    try:
        locator.type(value, delay=delay_ms, timeout=20000)
    except Exception:
        locator.fill(value, timeout=8000)

    try:
        locator.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }"""
        )
    except Exception:
        pass

    try:
        locator.press("Tab", timeout=2000)
    except Exception:
        pass

    locator.page.wait_for_timeout(450)

    try:
        return clean_text(locator.input_value(timeout=1500))
    except Exception:
        try:
            return clean_text(locator.evaluate("el => el.value || ''"))
        except Exception:
            return ""

def select_value(locator, value: str, wait_after_ms: int = 900) -> str:
    locator.scroll_into_view_if_needed(timeout=5000)

    try:
        locator.select_option(value=value, timeout=8000)
    except Exception:
        locator.evaluate(
            """(el, value) => {
                el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )

    try:
        locator.evaluate(
            """el => {
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }"""
        )
    except Exception:
        pass

    locator.page.wait_for_timeout(wait_after_ms)

    try:
        return clean_text(locator.input_value(timeout=1000))
    except Exception:
        return value

def click_input_button(locator, timeout_ms: int = 12000, allow_force_enable: bool = True) -> None:
    locator.scroll_into_view_if_needed(timeout=5000)
    deadline = time.perf_counter() + timeout_ms / 1000

    while time.perf_counter() < deadline:
        try:
            disabled = locator.evaluate("el => !!el.disabled")
            if not disabled:
                locator.click(timeout=5000)
                return
        except Exception:
            pass
        locator.page.wait_for_timeout(400)

    if allow_force_enable:
        try:
            locator.evaluate("el => { el.disabled = false; el.removeAttribute('disabled'); }")
            locator.click(timeout=5000, force=True)
            return
        except Exception:
            pass

    locator.click(timeout=5000, force=True)


def click_any(page, selectors: Iterable[str], timeout_ms: int = 1500) -> str:
    _ctx, label, item, selector = find_first(page, selectors, timeout_ms=timeout_ms)
    click_input_button(item, timeout_ms=8000, allow_force_enable=True)
    return f"{label}:{selector}"


def get_all_visible_text(page) -> str:
    chunks: List[str] = []

    for context, _label in all_contexts(page):
        try:
            text = context.locator("body").inner_text(timeout=1000)
            if text:
                chunks.append(text)
        except Exception:
            continue

    return "\n".join(chunks)


def wait_for_text(page, patterns: Iterable[str], timeout_ms: int = 30000) -> bool:
    wanted = [norm(p) for p in patterns]
    deadline = time.perf_counter() + timeout_ms / 1000

    while time.perf_counter() < deadline:
        text_norm = norm(get_all_visible_text(page))
        if any(p in text_norm for p in wanted):
            return True
        page.wait_for_timeout(800)

    return False


def get_all_html(page) -> str:
    parts: List[str] = []

    for context, label in all_contexts(page):
        try:
            html = context.evaluate("() => document.body ? document.body.outerHTML : document.documentElement.outerHTML")
            parts.append(f"\n<!-- CONTEXT {label} -->\n{html}")
        except Exception:
            try:
                parts.append(f"\n<!-- CONTEXT {label} -->\n{context.content()}")
            except Exception:
                pass

    return "\n".join(parts)


def save_evidence(page, prefix: str) -> Tuple[str, str]:
    """Compatibilidad del contrato: la API no persiste evidencias."""
    return "", ""



def close_already_logged_popup_if_present(page, timeout_ms: int = 12000) -> bool:
    """
    Cierra el popup de ACS:
    'This user is already logged in, continue?'

    Si aparece, presiona OK para continuar la sesión.
    """
    deadline = time.perf_counter() + timeout_ms / 1000

    wanted_texts = [
        "already logged in",
        "this user is already logged in",
        "continue?",
    ]

    ok_selectors = [
        "#btnOk_btn",
        "input[name='btnOk$btn']",
        "input[value='OK']",
        "button:has-text('OK')",
        "text=OK",
    ]

    while time.perf_counter() < deadline:
        try:
            visible_text_norm = norm(get_all_visible_text(page))
            popup_detected = any(norm(x) in visible_text_norm for x in wanted_texts)

            if not popup_detected:
                page.wait_for_timeout(500)
                continue

            debug_step("Popup de sesión existente detectado. Presionando OK para continuar...")

            for context, label in all_contexts(page):
                for selector in ok_selectors:
                    try:
                        loc = context.locator(selector).first

                        if loc.count() <= 0:
                            continue

                        if loc.is_visible(timeout=1000):
                            try:
                                disabled = loc.evaluate(
                                    "el => !!el.disabled || el.getAttribute('disabled') !== null"
                                )
                            except Exception:
                                disabled = False

                            if disabled:
                                try:
                                    loc.evaluate(
                                        "el => { el.disabled = false; el.removeAttribute('disabled'); }"
                                    )
                                except Exception:
                                    pass

                            loc.scroll_into_view_if_needed(timeout=3000)
                            loc.click(timeout=5000, force=True)
                            page.wait_for_timeout(2500)

                            debug_step(f"Popup de sesión existente cerrado en {label}:{selector}")
                            return True

                    except Exception:
                        continue

            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(2500)
                debug_step("Popup de sesión existente cerrado con Enter.")
                return True
            except Exception:
                pass

        except Exception:
            pass

        page.wait_for_timeout(500)

    return False

def login_acs(page, url: str, username: str, password: str, timeout_ms: int) -> Dict[str, Any]:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    wait_soft_network(page, timeout_ms=10000)

    selectors: Dict[str, Any] = {}

    _ctx, label, user_input, user_selector = find_first(
        page,
        ["#txtName", "input[name='txtName']"],
        timeout_ms=10000,
    )
    selectors["user"] = f"{label}:{user_selector}"
    set_input_value(user_input, username)

    _ctx, label, pass_input, pass_selector = find_first(
        page,
        ["#txtPassword", "input[name='txtPassword']"],
        timeout_ms=5000,
    )
    selectors["password"] = f"{label}:{pass_selector}"
    set_input_value(pass_input, password)

    _ctx, label, login_btn, login_selector = find_first(
        page,
        ["#btnLogin_btn", "input[name='btnLogin$btn']"],
        timeout_ms=5000,
    )
    selectors["login"] = f"{label}:{login_selector}"
    click_input_button(login_btn, timeout_ms=15000, allow_force_enable=True)

    # ACS puede mostrar: "This user is already logged in, continue?".
    # Si aparece, damos OK automáticamente para no bloquear el diagnóstico.
    page.wait_for_timeout(1200)
    selectors["already_logged_popup"] = close_already_logged_popup_if_present(
        page,
        timeout_ms=15000,
    )

    wait_soft_network(page, timeout_ms=15000)

    logged = wait_for_text(
        page,
        ["Search", "Device Update", "Subscriber info", "ddlSearchOption"],
        timeout_ms=45000,
    )

    if not logged:
        # Segundo intento por si el popup apareció tarde.
        selectors["already_logged_popup_retry"] = close_already_logged_popup_if_present(
            page,
            timeout_ms=8000,
        )

        wait_soft_network(page, timeout_ms=15000)

        logged = wait_for_text(
            page,
            ["Search", "Device Update", "Subscriber info", "ddlSearchOption"],
            timeout_ms=45000,
        )

    if not logged:
        raise RuntimeError(
            "No se confirmó ingreso a ACS después del login. "
            "Puede haber quedado un popup o la página no cargó Search/Device Update."
        )

    page.wait_for_timeout(700)

    # ACS_SEARCH_SELECT_RESILIENT_V1
    # No damos por terminado el login solo por encontrar texto gen?rico.
    # Debe existir realmente el formulario Search.
    if not acs_search_form_ready(
        page,
        timeout_ms=12000,
    ):
        raise RuntimeError(
            "Login ACS completado visualmente, "
            "pero el formulario Search by no est? disponible."
        )

    return selectors


# ACS_SEARCH_RECOVERY_V1_START
# ACS_SEARCH_SELECT_RESILIENT_V1_START
def find_acs_search_select(
    page,
    timeout_ms: int = 12000,
):
    """
    Encuentra el combo Search by de ACR69/Friendly.

    1. Mantiene compatibilidad con los IDs hist?ricos.
    2. Si cambian ID/name, identifica el SELECT por sus opciones
       Serial Number / MAC address.
    """

    started = time.perf_counter()

    total_timeout = min(
        max(int(timeout_ms), 2000),
        15000,
    )

    historical_selectors = [
        "#ddlSearchOption",
        "select[name='ddlSearchOption']",
    ]

    last_error = None

    while (
        time.perf_counter() - started
    ) * 1000 < total_timeout:

        contexts = priority_contexts(page)

        # ------------------------------------------------------------------
        # 1. Selectores hist?ricos
        # ------------------------------------------------------------------

        for context, label in contexts:

            for selector in historical_selectors:

                try:
                    locators = context.locator(
                        selector
                    )

                    count = locators.count()

                    for index in range(count):

                        locator = locators.nth(
                            index
                        )

                        if locator.is_visible(
                            timeout=500
                        ):
                            return (
                                context,
                                label,
                                locator,
                                selector,
                            )

                except Exception as exc:
                    last_error = exc

        # ------------------------------------------------------------------
        # 2. Fallback sem?ntico:
        #    buscar SELECT cuyas opciones correspondan al Search by.
        # ------------------------------------------------------------------

        for context, label in contexts:

            try:
                selects = context.locator(
                    "select"
                )

                count = selects.count()

                for index in range(count):

                    select = selects.nth(index)

                    try:
                        if not select.is_visible(
                            timeout=400
                        ):
                            continue
                    except Exception:
                        continue

                    try:
                        options = select.locator(
                            "option"
                        )

                        option_values = []
                        option_texts = []

                        for option_index in range(
                            options.count()
                        ):
                            option = options.nth(
                                option_index
                            )

                            option_values.append(
                                norm(
                                    option.get_attribute(
                                        "value"
                                    )
                                    or ""
                                )
                            )

                            option_texts.append(
                                norm(
                                    option.text_content(
                                        timeout=500
                                    )
                                    or ""
                                )
                            )

                        all_values = " ".join(
                            option_values
                        )

                        all_texts = " ".join(
                            option_texts
                        )

                        has_serial = (
                            "serial" in all_values
                            or "serial number" in all_texts
                            or "serial" in all_texts
                        )

                        has_mac = (
                            "macaddress" in all_values
                            or "mac address" in all_texts
                            or "mac" in all_texts
                        )

                        if (
                            has_serial
                            and has_mac
                        ):
                            selector_info = (
                                "semantic-select:"
                                f"index={index}"
                            )

                            return (
                                context,
                                label,
                                select,
                                selector_info,
                            )

                    except Exception as exc:
                        last_error = exc
                        continue

            except Exception as exc:
                last_error = exc

        page.wait_for_timeout(
            300
        )

    raise RuntimeError(
        "No se encontr? el selector Search by de ACS "
        "(Serial Number / MAC address) despu?s de "
        f"{round(time.perf_counter() - started, 1)}s. "
        f"?ltimo error: {last_error}"
    )
# ACS_SEARCH_SELECT_RESILIENT_V1_END



def acs_search_form_ready(
    page,
    timeout_ms: int = 2500,
) -> bool:
    """Confirma que ACS tiene disponible el formulario Search."""
    try:
        find_acs_search_select(
            page,
            timeout_ms=timeout_ms,
        )
        return True
    except Exception:
        return False


def recover_acs_search_page(
    page,
    url: str,
    username: str,
    password: str,
    timeout_ms: int,
) -> Dict[str, Any]:
    """
    Recupera el formulario Search tras un fallo de candidato.

    Mantiene el mismo BrowserContext/cookies. Primero intenta usar la
    pagina actual; luego navega a la URL central. Solo vuelve a ejecutar
    login_acs() si realmente aparece el formulario de login.
    """
    started = time.perf_counter()
    result: Dict[str, Any] = {
        "ok": False,
        "estado": "ACS_SEARCH_RECOVERY_ERROR",
        "login_performed": False,
        "strategy": "",
        "error": "",
    }

    if acs_search_form_ready(page, timeout_ms=2200):
        result["ok"] = True
        result["estado"] = "ACS_SEARCH_READY_CURRENT_PAGE"
        result["strategy"] = "CURRENT_PAGE"
        result["duracion_seg"] = round(time.perf_counter() - started, 2)
        return result

    try:
        debug_step(
            "Recuperando formulario Search ACS despues de fallo de candidato..."
        )

        # ACS_SEARCH_RECOVERY_ERR_ABORTED_V1
        goto_error = ""

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=min(max(int(timeout_ms), 15000), 60000),
            )
        except Exception as exc:
            goto_error = f"{type(exc).__name__}: {exc}"
            if "ERR_ABORTED" not in goto_error.upper():
                raise
            debug_step("ACS devolvio ERR_ABORTED durante recovery; validando estado real de la pagina.")
            try:
                page.wait_for_timeout(2500)
            except Exception:
                pass

        wait_soft_network(page, timeout_ms=10000)
        close_already_logged_popup_if_present(page, timeout_ms=8000)

        if acs_search_form_ready(page, timeout_ms=7000):
            result["ok"] = True
            result["estado"] = "ACS_SEARCH_RECOVERED_SESSION"
            result["strategy"] = ("GOTO_ERR_ABORTED_REUSE_SESSION" if goto_error else "GOTO_REUSE_SESSION")
            result["goto_error"] = goto_error
            result["duracion_seg"] = round(time.perf_counter() - started, 2)
            return result

        login_visible = False
        try:
            find_first(
                page,
                [
                    "#txtName",
                    "input[name='txtName']",
                ],
                timeout_ms=2500,
            )
            login_visible = True
        except Exception:
            login_visible = False

        if login_visible:
            debug_step(
                "La recuperacion regreso al login ACS. Relogueando dentro del mismo BrowserContext..."
            )
            login_acs(
                page,
                url,
                username,
                password,
                timeout_ms,
            )
            result["login_performed"] = True

            if acs_search_form_ready(page, timeout_ms=7000):
                result["ok"] = True
                result["estado"] = "ACS_SEARCH_RECOVERED_RELOGIN"
                result["strategy"] = "RELOGIN_SAME_CONTEXT"
                result["duracion_seg"] = round(time.perf_counter() - started, 2)
                return result

        result["error"] = "FORMULARIO_SEARCH_NO_RECUPERADO"

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["duracion_seg"] = round(time.perf_counter() - started, 2)
    return result
# ACS_SEARCH_RECOVERY_V1_END

# ACS_DEVICE_FOUND_REAL_MAC_V1_START
def detect_acs_device_info(
    page,
    expected_serial: str = "",
) -> Dict[str, Any]:
    """
    Detecta si ACS ya naveg? a DeviceInfo y extrae Serial/MAC visibles.
    No depende de que Subscriber info tenga mycust04.
    """

    expected = clean_text(
        expected_serial
    ).upper()

    for context, label in iter_acs_data_contexts(
        page
    ):
        try:
            serial = ""

            serial_selectors = [
                "#rptDeviceInfo_ctl00_lblValue",
                "#tblDeviceInfo span[id$='_lblValue']",
            ]

            for selector in serial_selectors:
                locators = context.locator(
                    selector
                )

                for index in range(
                    locators.count()
                ):
                    value = clean_text(
                        locators.nth(
                            index
                        ).text_content(
                            timeout=500
                        )
                        or ""
                    ).upper()

                    if value:
                        if (
                            not expected
                            or value == expected
                        ):
                            serial = value
                            break

                if serial:
                    break

            if not serial:
                continue

            mac = ""

            try:
                rows = context.locator(
                    "#tblDeviceInfo tr"
                )

                for index in range(
                    rows.count()
                ):
                    row = rows.nth(index)

                    row_text = clean_text(
                        row.text_content(
                            timeout=500
                        )
                        or ""
                    )

                    if "mac address" not in row_text.lower():
                        continue

                    values = row.locator(
                        "span[id$='_lblValue']"
                    )

                    if values.count():
                        mac = clean_text(
                            values.nth(0).text_content(
                                timeout=500
                            )
                            or ""
                        )

                    if mac:
                        break

            except Exception:
                pass

            return {
                "found": True,
                "serial": serial,
                "mac": mac,
                "context": label,
            }

        except Exception:
            continue

    return {
        "found": False,
        "serial": "",
        "mac": "",
        "context": "",
    }


def wait_for_acs_device_or_account(
    page,
    expected_serial: str,
    timeout_ms: int = 60000,
) -> Dict[str, Any]:

    deadline = (
        time.perf_counter()
        + timeout_ms / 1000.0
    )

    while time.perf_counter() < deadline:

        account, account_context, account_selector = (
            wait_for_acs_account(
                page,
                timeout_ms=500,
            )
        )

        if account:
            return {
                "account": account,
                "account_context": account_context,
                "account_selector": account_selector,
                "device_found": True,
                "serial": expected_serial,
                "mac": "",
            }

        device = detect_acs_device_info(
            page,
            expected_serial=expected_serial,
        )

        if device.get("found"):
            # ACS_DEVICE_FOUND_WAIT_ACCOUNT_V1
            # Device Update puede aparecer antes de que Subscriber info
            # publique mycust04. Esperar hasta 5 s adicionales, sin
            # superar el deadline original de la busqueda.
            account_wait_deadline = min(
                deadline,
                time.perf_counter() + 5.0,
            )

            while time.perf_counter() < account_wait_deadline:
                account, account_context, account_selector = (
                    wait_for_acs_account(
                        page,
                        timeout_ms=500,
                    )
                )

                if account:
                    return {
                        "account": account,
                        "account_context": account_context,
                        "account_selector": account_selector,
                        "device_found": True,
                        "serial": device.get("serial", ""),
                        "mac": device.get("mac", ""),
                        "device_context": device.get("context", ""),
                    }

                page.wait_for_timeout(250)

            return {
                "account": "",
                "account_context": "",
                "account_selector": "",
                "device_found": True,
                "serial": device.get("serial", ""),
                "mac": device.get("mac", ""),
                "device_context": device.get("context", ""),
            }

        page.wait_for_timeout(250)

    return {
        "account": "",
        "device_found": False,
        "serial": "",
        "mac": "",
    }
# ACS_DEVICE_FOUND_REAL_MAC_V1_END



def wait_for_acs_account(
    page,
    timeout_ms: int = 60000,
) -> Tuple[str, str, str]:
    """
    Espera a que el UpdatePanel de ACS publique la cuenta mycust04.
    """
    deadline = time.perf_counter() + timeout_ms / 1000

    account_selectors = [
        "#rptUserInfo_ctl03_lblValue",
        "#tblAccountInfo span[id$='_lblValue']",
    ]

    while time.perf_counter() < deadline:
        # Se recalculan p?ginas y frames en cada vuelta porque ACS usa AJAX.
        for context, label in iter_acs_data_contexts(page):
            for selector in account_selectors:
                try:
                    locators = context.locator(selector)

                    for index in range(locators.count()):
                        locator = locators.nth(index)

                        value = clean_text(
                            locator.text_content(timeout=1000) or ""
                        )

                        # El selector alternativo debe pertenecer a mycust04.
                        if selector != "#rptUserInfo_ctl03_lblValue":
                            try:
                                row = locator.locator(
                                    "xpath=ancestor::tr[1]"
                                )

                                row_text = norm(
                                    row.text_content(timeout=1000) or ""
                                )

                                if "mycust04" not in row_text:
                                    continue

                            except Exception:
                                continue

                        account = re.sub(r"\D+", "", value)

                        if account:
                            return account, label, selector

                except Exception:
                    continue

        page.wait_for_timeout(500)

    return "", "", ""

def acs_no_data_found(page) -> bool:
    """Detecta la respuesta final sin resultados en cualquier frame de ACS."""
    for context, _label in iter_acs_data_contexts(page):
        try:
            locator = context.locator("#pager2_lblPagerTotal")
            for index in range(locator.count()):
                text = norm(locator.nth(index).text_content(timeout=700) or "")
                if text in {"no data found", "no se encontraron datos", "sin datos"}:
                    return True
        except Exception:
            continue
    return False



# ACS_FRESH_RESPONSE_FAST_FAIL_V2_START
def _acs_fast_fail_settings() -> Dict[str, int]:
    """
    Lee exclusivamente parámetros ACS del .env central.

    Se lee el archivo en cada proceso ACS para que una futura
    modificación de tiempos no requiera modificar este código.
    """

    env_file = (
        Path(__file__).resolve().parents[3]
        / ".env"
    )

    if not env_file.is_file():
        raise RuntimeError(
            "ACS_ENV_CENTRAL_NO_EXISTE"
        )

    required = {
        "ACS_SEARCH_HARD_TIMEOUT_MS",
        "ACS_SEARCH_POLL_MS",
        "ACS_SEARCH_ACCOUNT_PROBE_MS",
        "ACS_SEARCH_FRESH_TEXT_CONFIRMATIONS",
        "ACS_SEARCH_FALLBACK_NO_DATA_MIN_MS",
        "ACS_SEARCH_FALLBACK_CONFIRMATIONS",
    }

    raw_values: Dict[str, str] = {}

    content = env_file.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )

    for raw_line in content.splitlines():

        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split(
            "=",
            1,
        )

        key = key.strip()

        if key not in required:
            continue

        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        raw_values[key] = value.strip()

    result: Dict[str, int] = {}

    for name in required:

        raw = raw_values.get(
            name,
            "",
        )

        if not raw:
            raise RuntimeError(
                f"{name}_NO_CONFIGURADO"
            )

        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"{name}_INVALIDO"
            ) from exc

        if value <= 0:
            raise RuntimeError(
                f"{name}_INVALIDO"
            )

        result[name] = value

    return result


def _acs_install_ajax_marker(
    page,
) -> Dict[str, int]:
    """
    Instala un contador en los PageRequestManager ASP.NET
    existentes antes de pulsar Search.
    """

    baseline: Dict[str, int] = {}

    javascript = """
    () => {
        try {
            if (
                !window.Sys ||
                !window.Sys.WebForms ||
                !window.Sys.WebForms.PageRequestManager
            ) {
                return null;
            }

            const prm =
                window.Sys.WebForms.PageRequestManager.getInstance();

            if (!prm) {
                return null;
            }

            if (!window.__atlasAcsAjaxMarkerV2) {

                window.__atlasAcsAjaxMarkerV2 = {
                    seq: 0,
                    active: false
                };

                prm.add_beginRequest(
                    function () {
                        window.__atlasAcsAjaxMarkerV2.active = true;
                    }
                );

                prm.add_endRequest(
                    function () {
                        window.__atlasAcsAjaxMarkerV2.seq += 1;
                        window.__atlasAcsAjaxMarkerV2.active = false;
                    }
                );
            }

            return {
                seq: Number(
                    window.__atlasAcsAjaxMarkerV2.seq || 0
                ),
                active: Boolean(
                    window.__atlasAcsAjaxMarkerV2.active
                )
            };
        }
        catch (_) {
            return null;
        }
    }
    """

    for context, label in iter_acs_data_contexts(
        page
    ):
        try:
            state = context.evaluate(
                javascript
            )

            if isinstance(state, dict):
                baseline[label] = int(
                    state.get("seq")
                    or 0
                )

        except Exception:
            continue

    return baseline


def _acs_fresh_ajax_response(
    page,
    baseline: Dict[str, int],
) -> Tuple[bool, str]:

    if not baseline:
        return False, ""

    javascript = """
    () => {
        try {
            const marker =
                window.__atlasAcsAjaxMarkerV2;

            if (!marker) {
                return null;
            }

            return {
                seq: Number(marker.seq || 0),
                active: Boolean(marker.active)
            };
        }
        catch (_) {
            return null;
        }
    }
    """

    for context, label in iter_acs_data_contexts(
        page
    ):

        if label not in baseline:
            continue

        try:
            state = context.evaluate(
                javascript
            )

            if not isinstance(state, dict):
                continue

            seq = int(
                state.get("seq")
                or 0
            )

            active = bool(
                state.get("active")
            )

            if (
                not active
                and seq > baseline[label]
            ):
                return True, label

        except Exception:
            continue

    return False, ""


def _acs_device_was_found_from_context(
    page,
    context_label_expected: str,
) -> str:
    """
    Lee hfDeviceWasFound únicamente del contexto que confirmó
    el nuevo AJAX. Así evitamos utilizar un false viejo de otro frame.
    """

    if not context_label_expected:
        return ""

    for context, label in iter_acs_data_contexts(
        page
    ):

        if label != context_label_expected:
            continue

        try:
            locator = context.locator(
                "#hfDeviceWasFound"
            )

            count = locator.count()

            for index in range(count):

                value = clean_text(
                    locator.nth(index).input_value(
                        timeout=300
                    )
                ).lower()

                if value in {
                    "true",
                    "false",
                }:
                    return value

        except Exception:
            return ""

    return ""


# ACS_FRESH_RESPONSE_FAST_FAIL_V2_END


def search_device(
    page,
    value: str,
    search_by: str = "serial",
) -> Dict[str, Any]:
    """
    Busca un equipo en ACS esperando el resultado real del UpdatePanel.

    ACS puede conservar temporalmente el mensaje 'No data found'
    mientras procesa una nueva b?squeda. Por eso:

    1. Se comprueba primero si apareci? mycust04.
    2. No se acepta 'No data found' durante los primeros 18 segundos.
    3. Despu?s se exige que el mensaje aparezca tres veces consecutivas.
    """
    requested_search_by = norm(search_by)

    option_values = {
        "serial": "serial",
        "mac": "macaddress",
        "macaddress": "macaddress",
    }

    if requested_search_by not in option_values:
        raise ValueError(
            "search_by debe ser 'serial', 'mac' o 'macaddress'."
        )

    search_by = (
        "mac"
        if requested_search_by in {"mac", "macaddress"}
        else "serial"
    )

    option_value = option_values[requested_search_by]
    value = clean_text(value).upper()

    selectors: Dict[str, Any] = {
        "search_by": search_by,
        "option_value_requested": option_value,
        "value": value,
    }

    _ctx, label, ddl, selector = find_acs_search_select(
        page,
        timeout_ms=15000,
    )

    selectors["ddlSearchOption"] = f"{label}:{selector}"

    selected_option = select_value(
        ddl,
        option_value,
    )

    selectors["ddlSearchOption_value"] = selected_option

    if norm(selected_option) != norm(option_value):
        raise RuntimeError(
            "ACS no seleccion? la opci?n esperada: "
            f"esperado={option_value}, obtenido={selected_option}"
        )

    _ctx, label, input_device, selector = find_first(
        page,
        [
            "#tbDeviceID",
            "input[name='tbDeviceID']",
        ],
        timeout_ms=10000,
    )

    selectors["tbDeviceID"] = f"{label}:{selector}"

    written_value = set_input_value(
        input_device,
        value,
    )

    selectors["value_written"] = written_value

    if clean_text(written_value).upper() != value:
        set_input_value(
            input_device,
            value,
        )

        written_value = clean_text(
            input_device.input_value(timeout=2000)
        ).upper()

        selectors["value_rewritten"] = written_value

    if written_value != value:
        raise RuntimeError(
            f"ACS no conserv? el valor buscado. "
            f"Esperado={value}, detectado={written_value}"
        )

    _ctx, label, search_btn, selector = find_first(
        page,
        [
            "#btnSearch_btn",
            "input[name='btnSearch$btn']",
        ],
        timeout_ms=10000,
    )

    selectors["search"] = f"{label}:{selector}"

    # ACS_FAST_FAIL_V2:
    # marcar el nuevo ciclo AJAX ANTES de realizar el único click Search.
    settings = _acs_fast_fail_settings()

    ajax_baseline = _acs_install_ajax_marker(
        page
    )

    click_input_button(
        search_btn,
        timeout_ms=15000,
        allow_force_enable=True,
    )

    search_started = time.perf_counter()

    hard_timeout_sec = (
        settings[
            "ACS_SEARCH_HARD_TIMEOUT_MS"
        ]
        / 1000.0
    )

    fallback_min_sec = (
        settings[
            "ACS_SEARCH_FALLBACK_NO_DATA_MIN_MS"
        ]
        / 1000.0
    )

    poll_ms = settings[
        "ACS_SEARCH_POLL_MS"
    ]

    account_probe_ms = settings[
        "ACS_SEARCH_ACCOUNT_PROBE_MS"
    ]

    fresh_text_required = settings[
        "ACS_SEARCH_FRESH_TEXT_CONFIRMATIONS"
    ]

    fallback_required = settings[
        "ACS_SEARCH_FALLBACK_CONFIRMATIONS"
    ]

    deadline = (
        search_started
        + hard_timeout_sec
    )

    fresh_text_confirmations = 0
    fallback_confirmations = 0

    selectors[
        "ajax_marker_contexts"
    ] = len(ajax_baseline)

    while time.perf_counter() < deadline:

        # ACS_DEVICE_FOUND_REAL_MAC_V1
        # ACR69 puede encontrar el equipo y navegar a DeviceInfo
        # aunque Subscriber info/mycust04 est? vac?o.

        detected = wait_for_acs_device_or_account(
            page,
            expected_serial=value,
            timeout_ms=account_probe_ms,
        )

        account = clean_text(
            detected.get("account")
        )

        if account:

            elapsed = round(
                time.perf_counter()
                - search_started,
                2,
            )

            selectors["account"] = account

            selectors["account_source"] = (
                f"{detected.get('account_context', '')}:"
                f"{detected.get('account_selector', '')}"
            )

            selectors["device_found"] = True
            selectors["wait_elapsed_sec"] = elapsed

            return selectors

        if detected.get("device_found"):

            elapsed = round(
                time.perf_counter()
                - search_started,
                2,
            )

            selectors["device_found"] = True
            selectors["device_serial"] = clean_text(
                detected.get("serial")
            )
            selectors["device_mac"] = clean_text(
                detected.get("mac")
            )
            selectors["device_context"] = clean_text(
                detected.get("device_context")
            )
            selectors["wait_elapsed_sec"] = elapsed

            return selectors

        elapsed = (
            time.perf_counter()
            - search_started
        )

        fresh_response, fresh_source = (
            _acs_fresh_ajax_response(
                page,
                ajax_baseline,
            )
        )

        if fresh_response:

            selectors[
                "fresh_ajax_response"
            ] = True

            selectors[
                "fresh_ajax_source"
            ] = fresh_source

            device_found = (
                _acs_device_was_found_from_context(
                    page,
                    fresh_source,
                )
            )

            selectors[
                "hfDeviceWasFound"
            ] = device_found

            # ACS_TRUE_SUPPRESS_NODATA_V1
            #
            # Si el AJAX actual ya confirma hfDeviceWasFound=true,
            # cualquier texto "No data found" visible en ese instante
            # se considera contenido viejo/transitorio de Friendly.
            #
            # No se permite convertir esta b?squeda en NO_DATA.
            # Seguimos esperando Account o DeviceInfo real.

            if device_found == "true":

                selectors[
                    "device_found_signal"
                ] = True

                selectors[
                    "no_data_suppressed"
                ] = "hfDeviceWasFound_true"

                fresh_text_confirmations = 0
                fallback_confirmations = 0

                page.wait_for_timeout(
                    250
                )

                continue

            # Señal fuerte:
            # AJAX NUEVO terminado + hidden del mismo contexto = false.
            # ACS_FALSE_GRACE_DEVICEINFO_V1_1
            #
            # Friendly puede publicar primero un false en el UpdatePanel
            # y enseguida navegar al DeviceInfo real.
            #
            # No declaramos No data inmediatamente.
            if device_found == "false":

                grace_deadline = (
                    time.perf_counter()
                    + 3.0
                )

                positive_device = None

                while (
                    time.perf_counter()
                    < grace_deadline
                ):

                    positive_device = (
                        detect_acs_device_info(
                            page,
                            expected_serial=(
                                value
                                if search_by == "serial"
                                else ""
                            ),
                        )
                    )

                    if positive_device.get(
                        "found"
                    ):
                        break

                    page.wait_for_timeout(
                        200
                    )

                if (
                    positive_device
                    and positive_device.get(
                        "found"
                    )
                ):

                    selectors[
                        "device_found"
                    ] = True

                    selectors[
                        "device_serial"
                    ] = clean_text(
                        positive_device.get(
                            "serial"
                        )
                    )

                    selectors[
                        "device_mac"
                    ] = clean_text(
                        positive_device.get(
                            "mac"
                        )
                    )

                    selectors[
                        "device_context"
                    ] = clean_text(
                        positive_device.get(
                            "context"
                        )
                    )

                    selectors[
                        "hfDeviceWasFound"
                    ] = (
                        "true_after_false_grace"
                    )

                    selectors[
                        "wait_elapsed_sec"
                    ] = round(
                        time.perf_counter()
                        - search_started,
                        2,
                    )

                    debug_step(
                        "ACS recibio hfDeviceWasFound=false "
                        "pero DeviceInfo aparecio durante "
                        "la ventana de gracia."
                    )

                    return selectors

                selectors[
                    "no_data_found"
                ] = True

                selectors[
                    "no_data_source"
                ] = "hfDeviceWasFound"

                selectors[
                    "wait_elapsed_sec"
                ] = round(
                    time.perf_counter()
                    - search_started,
                    2,
                )

                debug_step(
                    "ACS confirmo hfDeviceWasFound=false "
                    "y no aparecio DeviceInfo durante "
                    "la ventana de gracia."
                )

                return selectors

            # Segunda señal:
            # respuesta AJAX nueva + texto No data found.
            if acs_no_data_found(page):

                fresh_text_confirmations += 1

                if (
                    fresh_text_confirmations
                    >= fresh_text_required
                ):

                    selectors[
                        "no_data_found"
                    ] = True

                    selectors[
                        "no_data_source"
                    ] = "fresh_ajax_no_data_text"

                    selectors[
                        "no_data_confirmations"
                    ] = fresh_text_confirmations

                    selectors[
                        "wait_elapsed_sec"
                    ] = round(
                        elapsed,
                        2,
                    )

                    return selectors

            else:
                fresh_text_confirmations = 0

        # Fallback histórico y conservador.
        # Solo se usa cuando no logramos aprovechar la señal AJAX.
        if (
            elapsed >= fallback_min_sec
            and acs_no_data_found(page)
        ):

            fallback_confirmations += 1

            if (
                fallback_confirmations
                >= fallback_required
            ):

                selectors[
                    "no_data_found"
                ] = True

                selectors[
                    "no_data_source"
                ] = "fallback_text"

                selectors[
                    "no_data_confirmations"
                ] = fallback_confirmations

                selectors[
                    "wait_elapsed_sec"
                ] = round(
                    elapsed,
                    2,
                )

                return selectors

        else:
            fallback_confirmations = 0

        page.wait_for_timeout(
            poll_ms
        )

    raise RuntimeError(
        "ACS no publicó mycust04 ni confirmó "
        "No data found dentro del timeout configurado."
    )


def extract_section(text: str, start: str, stops: Iterable[str]) -> str:
    lines = [clean_text(x) for x in text.splitlines() if clean_text(x)]
    start_norm = norm(start)
    stop_norms = [norm(s) for s in stops]

    inside = False
    out: List[str] = []

    for line in lines:
        ln = norm(line)

        if not inside and start_norm in ln:
            inside = True
            continue

        if inside and any(s in ln for s in stop_norms):
            break

        if inside:
            out.append(line)

    return "\n".join(out)


def parse_key_value_lines(section_text: str) -> Dict[str, str]:
    data: Dict[str, str] = {}

    known_prefixes = [
        "User name",
        "User location",
        "mycust03",
        "mycust04",
        "Serial number",
        "Manufacturer",
        "Manufacturer OUI",
        "Product class",
        "Hardware version",
        "Firmware version",
        "IP address",
        "Pending",
        "Sent",
        "Completed",
        "Rejected",
        "Failed",
    ]

    for raw_line in section_text.splitlines():
        line = clean_text(raw_line)

        if not line:
            continue

        if ":" in line:
            k, v = line.split(":", 1)
            data[clean_text(k)] = clean_text(v)
            continue

        for prefix in known_prefixes:
            if norm(line).startswith(norm(prefix)):
                data[prefix] = clean_text(line[len(prefix):])
                break

    return data


def clean_lines_from_text(text: str) -> List[str]:
    return [clean_text(x) for x in text.splitlines() if clean_text(x)]


def translate_enabled_status(value: str) -> str:
    value_norm = norm(value)

    if value_norm in {"enabled", "enable", "active", "activo"}:
        return "Activo"

    if value_norm in {"disabled", "disable", "deshabilitado"}:
        return "Deshabilitado"

    if value_norm in {"checking", "verificando"}:
        return "Verificando"

    if value_norm in {"error"}:
        return "Error"

    return clean_text(value)


def extract_value_after_label(lines: List[str], label_patterns: Iterable[str]) -> str:
    patterns = [norm(x) for x in label_patterns]

    ignored_values = {
        "",
        ".",
        ":",
        "-",
    }

    for idx, line in enumerate(lines):
        line_clean = clean_text(line)
        line_norm = norm(line_clean)

        for pattern in patterns:
            if line_norm == pattern:
                # Busca el siguiente valor útil, no cualquier título/cubo.
                for nxt in lines[idx + 1: idx + 6]:
                    nxt_clean = clean_text(nxt)
                    nxt_norm = norm(nxt_clean)

                    if not nxt_clean or nxt_norm in ignored_values:
                        continue

                    if nxt_norm in {
                        "device history",
                        "tools",
                        "wan",
                        "ports",
                        "diagnostics",
                        "connection troubleshooter",
                        "software upgrade",
                        "configuration backup/restore",
                        "quick fix",
                    }:
                        continue

                    return nxt_clean

            if line_norm.startswith(pattern):
                value = clean_text(line_clean[len(pattern):]).strip(" :\t")
                if value:
                    return value

    return ""

def parse_wifi_status(lines: List[str]) -> List[Dict[str, str]]:
    wifi_rows: List[Dict[str, str]] = []

    start_idx = None
    for idx, line in enumerate(lines):
        if norm(line) == "wi-fi access point":
            start_idx = idx + 1
            break

    if start_idx is None:
        return wifi_rows

    stop_words = {
        "voip",
        "connectivity failure",
        "reboot amount",
        "registered",
        "last connect",
        "uptime",
        "url for ping",
        "wan ip",
    }

    pending_ssid = ""

    for line in lines[start_idx:]:
        line_norm = norm(line)

        if any(line_norm.startswith(x) for x in stop_words):
            break

        if line_norm in {"enabled", "disabled", "enable", "disable", "activo", "deshabilitado"}:
            if pending_ssid:
                wifi_rows.append(
                    {
                        "ssid": pending_ssid,
                        "estado": translate_enabled_status(line),
                    }
                )
                pending_ssid = ""
            continue

        match = re.match(r"^(.*?)\s+(Enabled|Disabled|Enable|Disable|Activo|Deshabilitado)$", line, re.I)
        if match:
            wifi_rows.append(
                {
                    "ssid": clean_text(match.group(1)),
                    "estado": translate_enabled_status(match.group(2)),
                }
            )
            pending_ssid = ""
            continue

        if line and not line_norm.isdigit():
            pending_ssid = line

    return wifi_rows



def parse_network_map(lines: List[str]) -> List[List[str]]:
    """
    Extrae bloques del Network map.

    Cada bloque inicia con una IP LAN y conserva datos siguientes:
    hostname, MAC, canal, banda, señal, etc.

    ACS a veces muestra primero los nombres de cubos del Main y luego las IPs.
    Por eso se ignoran títulos del menú y se captura desde la primera IP LAN
    hasta antes de Serial / Wi-Fi access point / VoIP.
    """
    blocks: List[List[str]] = []

    start_idx = None
    for idx, line in enumerate(lines):
        if norm(line) == "network map":
            start_idx = idx + 1
            break

    if start_idx is None:
        start_idx = 0

    stop_markers = {
        "serial",
        "disconnected from the management server",
        "connected to the management server",
        "wi-fi access point",
        "connectivity failure",
        "reboot amount",
        "registered",
        "last connect",
        "uptime",
        "url for ping",
        "wan ip",
        "access point",
        "trace",
        "ns lookup",
        "ping",
        "speed diagnostics",
        "slow internet",
    }

    ignored = {
        "network map",
        "device status",
        "wan",
        "wireless status",
        "dhcp parameters",
        "device resources",
        "ports",
        "diagnostics",
        "connection troubleshooter",
        "tools",
        "voip",
        "device history",
        "software upgrade",
        "configuration backup/restore",
        "quick fix",
        "neighboring wi-fi diagnostics",
        "bytesreceived",
        "bytes received",
        "bytes received1",
        "main",
        "wi-fi 2.4ghz",
        "wi-fi 5ghz",
        "device settings",
        "custom rpc",
        "provision manager",
        "file download and upload",
        "activity and logs",
    }

    ip_re = re.compile(r"\b((?:192\.168|10\.|172\.(?:1[6-9]|2[0-9]|3[0-1]))\.\d{1,3}\.\d{1,3})\b")
    current: List[str] = []
    started_collecting = False

    for line in lines[start_idx:]:
        line_clean = clean_text(line)
        line_norm = norm(line_clean)

        if not line_clean:
            continue

        # Si ya empezamos a recoger dispositivos, al llegar a Serial o secciones del equipo paramos.
        if started_collecting and any(line_norm.startswith(x) for x in stop_markers):
            break

        if line_norm in ignored:
            continue

        match_ip = ip_re.search(line_clean)

        if match_ip:
            if current:
                blocks.append(current)

            ip = match_ip.group(1)
            current = [ip]
            started_collecting = True

            extra = clean_text(line_clean.replace(ip, ""))
            if extra:
                current.append(extra)

            continue

        if current and started_collecting:
            # Evitar meter basura larga del portal.
            if len(line_clean) <= 80:
                current.append(line_clean)

    if current:
        blocks.append(current)

    # Fallback final: si no encontró nada desde Network map, buscar IPs LAN en todas las líneas.
    if not blocks:
        current = []
        for line in lines:
            line_clean = clean_text(line)
            match_ip = ip_re.search(line_clean)

            if match_ip:
                if current:
                    blocks.append(current)
                current = [match_ip.group(1)]
                continue

            if current and len(current) < 6 and len(line_clean) <= 80:
                line_norm = norm(line_clean)
                if any(line_norm.startswith(x) for x in stop_markers):
                    break
                if line_norm not in ignored:
                    current.append(line_clean)

        if current:
            blocks.append(current)

    return blocks



def parse_wan_info(lines: List[str]) -> Dict[str, str]:
    """
    Extrae información del bloque WAN que aparece en Main.

    Esta información es operativamente útil aunque el IPPing diagnostics falle.
    """
    wan_ip = ""
    wan_estado = ""
    connection_enabled = ""
    dns_servers: List[str] = []

    ip_re = re.compile(r"\b((?:10|172\.(?:1[6-9]|2[0-9]|3[0-1])|192\.168)\.\d{1,3}\.\d{1,3})\b")

    for idx, line in enumerate(lines):
        line_clean = clean_text(line)
        line_norm = norm(line_clean)

        if line_norm == "wan ip":
            nearby = " ".join(lines[idx + 1: idx + 4])
            match = ip_re.search(nearby)

            if match:
                wan_ip = match.group(1)

            nearby_norm = norm(nearby)
            if "active" in nearby_norm or "activo" in nearby_norm:
                wan_estado = "Activo"
            elif "inactive" in nearby_norm or "inactivo" in nearby_norm:
                wan_estado = "Inactivo"
            continue

        if line_norm.startswith("wan ip"):
            match = ip_re.search(line_clean)

            if match:
                wan_ip = match.group(1)

            if "active" in line_norm or "activo" in line_norm:
                wan_estado = "Activo"
            elif "inactive" in line_norm or "inactivo" in line_norm:
                wan_estado = "Inactivo"
            continue

        if line_norm == "connection enabled":
            value = ""
            for candidate in lines[idx + 1: idx + 4]:
                candidate_clean = clean_text(candidate)
                candidate_norm = norm(candidate_clean)

                if candidate_norm in {"true", "false", "enabled", "disabled", "activo", "inactivo"}:
                    value = candidate_clean
                    break

                if candidate_clean in {"✓", "✔"}:
                    value = "true"
                    break

            if value:
                connection_enabled = translate_enabled_status(value)
            continue

        if line_norm.startswith("connection enabled"):
            value = clean_text(line_clean[len("Connection enabled"):]).strip(" :\t")
            if value:
                connection_enabled = translate_enabled_status(value)
            continue

        if line_norm == "dns servers":
            for candidate in lines[idx + 1: idx + 5]:
                candidate_clean = clean_text(candidate)
                if ip_re.search(candidate_clean):
                    dns_servers.append(candidate_clean)
            continue

    if not wan_estado and wan_ip:
        wan_estado = "Detectado"

    return {
        "ip": wan_ip or "no data",
        "estado": wan_estado or "no data",
        "connection_enabled": connection_enabled or "no data",
        "dns_servers": ", ".join(dns_servers) if dns_servers else "no data",
    }

def parse_estado_equipo(text: str, device_info: Dict[str, str]) -> Dict[str, Any]:
    lines = clean_lines_from_text(text)

    serial = (
        device_info.get("Serial number")
        or extract_value_after_label(lines, ["Serial"])
        or "no data"
    )

    connected_to_acs = ""

    for idx, line in enumerate(lines):
        line_norm = norm(line)

        if "disconnected from the management server" in line_norm:
            joined_next = " ".join(lines[idx: idx + 4])
            joined_next_norm = norm(joined_next)

            if "checking" in joined_next_norm:
                connected_to_acs = "Verificando"
            else:
                connected_to_acs = "Desconectado"

            break

        if "connected to the management server" in line_norm:
            connected_to_acs = "Conectado"
            break

        if line_norm == "connected to acs" and idx + 1 < len(lines):
            connected_to_acs = translate_enabled_status(lines[idx + 1])
            break

    if not connected_to_acs:
        connected_to_acs = "Verificando"

    wifi = parse_wifi_status(lines)
    wan = parse_wan_info(lines)

    voip = "no data"

    for idx, line in enumerate(lines):
        line_clean = clean_text(line)
        line_norm = norm(line_clean)

        # ACS a veces lo entrega en una sola línea: "VoIP Error".
        if line_norm.startswith("voip "):
            value = clean_text(line_clean[4:])
            if value:
                voip = translate_enabled_status(value)
                break

        if line_norm == "voip":
            nearby = lines[idx + 1: idx + 8]

            found_here = ""

            for candidate in nearby:
                candidate_clean = clean_text(candidate)
                candidate_norm = norm(candidate_clean)

                if candidate_norm in {
                    "error",
                    "enabled",
                    "disabled",
                    "active",
                    "activo",
                    "deshabilitado",
                    "registered",
                    "up",
                    "down",
                }:
                    found_here = translate_enabled_status(candidate_clean)
                    break

            # No cortar con el VoIP del menú/cubo si no encontró estado real.
            if found_here:
                voip = found_here
                break

    connectivity_failure = extract_value_after_label(
        lines,
        ["Connectivity failure", "Falla de Conectividad"],
    ) or "0"

    reboot_amount = extract_value_after_label(
        lines,
        ["Reboot amount", "Cantidad de reinicios"],
    ) or "0"

    registered = extract_value_after_label(lines, ["Registered", "Registrado"])
    last_connect = extract_value_after_label(lines, ["Last connect", "Última conexión", "Ultima conexion"])
    uptime = extract_value_after_label(lines, ["Uptime"])

    firmware_version = device_info.get("Firmware version", "")
    firmware_status = ""

    # Si la sección Device information no está visible al final, intentamos extraer
    # la versión desde el texto general o desde nombres de firmware como
    # GN630_HGU_4EWV_NAND_V1.0.0.7_0222-213642_tclinux.bin.
    if not firmware_version:
        fw_match = re.search(r"\bV\d+(?:\.\d+){1,5}\b", text, re.I)
        if fw_match:
            firmware_version = fw_match.group(0)

    text_norm = norm(text)
    if "firmware es la mas actualizada" in text_norm or "firmware is up to date" in text_norm:
        firmware_status = "La versión de firmware es la más actualizada"
    elif firmware_version:
        firmware_status = f"Versión actual: {firmware_version}"
    else:
        firmware_status = "no data"

    return {
        "serial": serial,
        "connected_to_acs": connected_to_acs,
        "wan": wan,
        "wan_ip": wan.get("ip", "no data"),
        "wan_estado": wan.get("estado", "no data"),
        "wifi": wifi,
        "voip": voip,
        "connectivity_failure_24h": connectivity_failure,
        "reboot_amount_24h": reboot_amount,
        "firmware": firmware_status,
        "registered": registered or "no data",
        "last_connect": last_connect or "no data",
        "uptime": uptime or "no data",
    }

def iter_acs_data_contexts(page):
    """
    Recorre todas las p?ginas abiertas y sus frames.

    Los contextos se generan nuevamente despu?s de las cargas AJAX.
    """
    contexts = []
    seen = set()

    try:
        pages = list(page.context.pages)
    except Exception:
        pages = [page]

    if page not in pages:
        pages.insert(0, page)

    for page_index, current_page in enumerate(pages):
        candidates = [
            (
                current_page,
                f"page[{page_index}]",
            )
        ]

        try:
            candidates.extend(
                (
                    frame,
                    (
                        f"page[{page_index}]/"
                        f"frame[{frame_index}] "
                        f"{frame.name or frame.url}"
                    ),
                )
                for frame_index, frame
                in enumerate(current_page.frames)
            )
        except Exception:
            pass

        for context, label in candidates:
            identity = id(context)

            if identity in seen:
                continue

            seen.add(identity)
            contexts.append((context, label))

    return contexts


def extract_locator_text(locator) -> str:
    try:
        value = locator.text_content(timeout=1500)
        value = clean_text(value or "")

        if value:
            return value
    except Exception:
        pass

    try:
        value = locator.get_attribute("value", timeout=1000)
        return clean_text(value or "")
    except Exception:
        return ""


def extract_table_key_values(page, table_selector: str) -> Dict[str, str]:
    """
    Extrae las parejas etiqueta/valor de una tabla ubicada en cualquier
    frame de ACS.
    """
    for context, _label in iter_acs_data_contexts(page):
        data: Dict[str, str] = {}

        try:
            tables = context.locator(table_selector)

            if tables.count() == 0:
                continue

            rows = tables.first.locator("tr")
            row_count = rows.count()
        except Exception:
            continue

        for index in range(row_count):
            try:
                row = rows.nth(index)

                name = row.locator(
                    "span.left_information_name"
                ).first

                value = row.locator(
                    "span.left_information_value"
                ).first

                if name.count() == 0 or value.count() == 0:
                    continue

                key = extract_locator_text(name).rstrip(":").strip()
                val = extract_locator_text(value)

                if key and val:
                    data[key] = val

            except Exception:
                continue

        if data:
            return data

    return {}


def extract_first_element_text(
    page,
    selectors: Iterable[str],
) -> str:
    """
    Busca cada selector primero en la p?gina y despu?s en todos los frames.
    """
    for selector in selectors:
        for context, _label in iter_acs_data_contexts(page):
            try:
                locator = context.locator(selector).first

                if locator.count() == 0:
                    continue

                value = extract_locator_text(locator)

                if value:
                    return value

            except Exception:
                continue

    return ""


def extract_acs_visible_data(page) -> Dict[str, Any]:
    text = get_all_visible_text(page)

    subscriber_section = extract_section(
        text,
        "Subscriber info",
        ["Device information", "Device tasks", "Main"],
    )

    device_section = extract_section(
        text,
        "Device information",
        ["Device tasks", "Main", "Diagnostics"],
    )

    tasks_section = extract_section(
        text,
        "Device tasks",
        ["Main", "Diagnostics", "Add"],
    )

    # Se conserva el m?todo anterior como respaldo.
    subscriber_info = parse_key_value_lines(subscriber_section)
    device_information = parse_key_value_lines(device_section)
    device_tasks = parse_key_value_lines(tasks_section)

    # M?todo principal: leer las parejas de spans dentro de cada fila.
    subscriber_info.update(
        extract_table_key_values(page, "#tblAccountInfo")
    )

    device_information.update(
        extract_table_key_values(page, "#tblDeviceInfo")
    )

    # mycust04 corresponde a la cuenta utilizada por el Diagnosticador.
    cuenta = clean_text(subscriber_info.get("mycust04", ""))

    # Respaldo con el ID observado en ACS.
    if not cuenta:
        cuenta = extract_first_element_text(
            page,
            [
                "#rptUserInfo_ctl03_lblValue",
                "span[id^='rptUserInfo_'][id$='_lblValue']",
            ],
        )

    # La cuenta debe enviarse al Diagnosticador sin espacios ni s?mbolos.
    cuenta_numerica = re.sub(r"\D+", "", cuenta)

    if cuenta_numerica:
        cuenta = cuenta_numerica
        subscriber_info["mycust04"] = cuenta

    lines = clean_lines_from_text(text)

    return {
        "cuenta": cuenta,
        "subscriber_info": subscriber_info,
        "device_information": device_information,
        "device_tasks": device_tasks,
        "estado_equipo": parse_estado_equipo(
            text,
            device_information,
        ),
        "network_map": parse_network_map(lines),
        "visible_text_sample": text[:4000],
    }


def priority_contexts(page) -> List[Tuple[Any, str]]:
    """
    ACS tiene muchos iframes. Priorizamos frmDesktop porque ahí están
    la búsqueda, los tabs principales y getWorkedFrame().
    """
    contexts = all_contexts(page)

    def rank(item: Tuple[Any, str]) -> int:
        label = item[1].lower()

        if "frmdesktop" in label:
            return 0

        if "diagnostic" in label or "diagnos" in label:
            return 1

        if label == "page":
            return 2

        return 3

    return sorted(contexts, key=rank)


def context_label(context, default: str = "context") -> str:
    try:
        if hasattr(context, "name") and context.name:
            return f"frame:{context.name}"
    except Exception:
        pass

    try:
        if hasattr(context, "url") and context.url:
            return f"frame:{context.url}"
    except Exception:
        pass

    return default


def get_desktop_contexts(page) -> List[Tuple[Any, str]]:
    """
    Devuelve primero frmDesktop. Ese frame es el que contiene los tabs
    tabMain_cell1Diagnostics y la función getWorkedFrame().
    """
    out: List[Tuple[Any, str]] = []

    for context, label in all_contexts(page):
        if "frmdesktop" in label.lower():
            out.append((context, label))

    # Fallbacks por si ACS cambia el nombre del frame.
    for context, label in all_contexts(page):
        if label == "page" and all(x[1] != label for x in out):
            out.append((context, label))

    return out or all_contexts(page)


def js_exists(context, selector: str) -> bool:
    try:
        return bool(
            context.evaluate(
                "(selector) => !!document.querySelector(selector)",
                selector,
            )
        )
    except Exception:
        return False



def js_click_selector(context, selector: str) -> bool:
    """
    Clic rápido por JS, SIN duplicar el onclick.

    Importante:
    En ACS los botones Add/Create/OK ejecutan funciones en onclick.
    Si se hace dispatchEvent('click') y luego el.click(), ACS crea dos diagnósticos.
    Esta versión solo ejecuta un click real.
    """
    try:
        return bool(
            context.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return false;

                    try {
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                    } catch (e) {}

                    try {
                        el.disabled = false;
                        el.removeAttribute('disabled');
                    } catch (e) {}

                    const opts = { bubbles: true, cancelable: true, view: window };

                    // Eventos visuales previos. No se dispara click manual.
                    try { el.dispatchEvent(new MouseEvent('mouseover', opts)); } catch (e) {}
                    try { el.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (e) {}
                    try { el.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (e) {}

                    // Un único click real.
                    try {
                        el.click();
                        return true;
                    } catch (e) {
                        return false;
                    }
                }""",
                selector,
            )
        )
    except Exception:
        return False

def get_worked_frame_details(page) -> List[Dict[str, Any]]:
    """
    Pregunta a ACS cuál es el frame activo real mediante getWorkedFrame().
    Esto evita hacer clic en botones Add de otros cubos como Configuration backup/restore.
    """
    details: List[Dict[str, Any]] = []

    js = """() => {
        const results = [];

        function collect(wf, source) {
            try {
                if (!wf) return;

                const fe = wf.frameElement;
                const doc = wf.document;
                const bodyText = doc && doc.body ? (doc.body.innerText || '').slice(0, 2500) : '';

                results.push({
                    source,
                    id: fe ? (fe.id || '') : '',
                    name: fe ? (fe.name || '') : '',
                    src: fe ? (fe.getAttribute('src') || '') : '',
                    href: wf.location ? (wf.location.href || '') : '',
                    title: doc ? (doc.title || '') : '',
                    bodyText,
                    hasAdd: !!(doc && doc.querySelector("#btnAdd_btn, input[name='btnAdd$btn'], input[value='Add']")),
                    hasDiagType: !!(doc && doc.querySelector("#ddlDiagType, select[name='ddlDiagType']")),
                    hasResultOutput: !!(doc && doc.querySelector("#tblResultOutput"))
                });
            } catch (e) {}
        }

        try {
            if (typeof getWorkedFrame === 'function') collect(getWorkedFrame(), 'self.getWorkedFrame');
        } catch (e) {}

        try {
            if (window.parent && typeof window.parent.getWorkedFrame === 'function') collect(window.parent.getWorkedFrame(), 'parent.getWorkedFrame');
        } catch (e) {}

        try {
            if (window.top && typeof window.top.getWorkedFrame === 'function') collect(window.top.getWorkedFrame(), 'top.getWorkedFrame');
        } catch (e) {}

        return results;
    }"""

    for context, label in get_desktop_contexts(page):
        try:
            found = context.evaluate(js)
            if isinstance(found, list):
                for item in found:
                    if isinstance(item, dict):
                        item["context_source"] = label
                        details.append(item)
        except Exception:
            continue

    # Quitar duplicados conservando orden.
    seen = set()
    unique: List[Dict[str, Any]] = []
    for item in details:
        key = (
            clean_text(item.get("id")),
            clean_text(item.get("name")),
            clean_text(item.get("href")),
            clean_text(item.get("src")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def worked_detail_is_diagnostics(detail: Dict[str, Any]) -> bool:
    combined = norm(
        " ".join(
            [
                detail.get("id", ""),
                detail.get("name", ""),
                detail.get("src", ""),
                detail.get("href", ""),
                detail.get("title", ""),
                detail.get("bodyText", ""),
            ]
        )
    )

    if detail.get("hasDiagType"):
        return True

    if "diagnostic" in combined or "diagnos" in combined:
        return True

    # El listado de diagnósticos normalmente trae botón Add y texto IPPing.
    if detail.get("hasAdd") and ("ipping" in combined or "trace" in combined or "ns lookup" in combined):
        return True

    return False


def frame_matches_detail(frame, detail: Dict[str, Any]) -> bool:
    try:
        frame_name = clean_text(frame.name)
    except Exception:
        frame_name = ""

    try:
        frame_url = clean_text(frame.url)
    except Exception:
        frame_url = ""

    wanted = [
        clean_text(detail.get("id")),
        clean_text(detail.get("name")),
    ]

    for item in wanted:
        if item and item == frame_name:
            return True

    src = clean_text(detail.get("src"))
    href = clean_text(detail.get("href"))

    if href and href == frame_url:
        return True

    if href and href in frame_url:
        return True

    if src:
        src_tail = src.split("?")[0].split("/")[-1].lower()
        if src_tail and src_tail in frame_url.lower():
            return True

    return False


def get_contexts_from_details(page, details: List[Dict[str, Any]]) -> List[Tuple[Any, str]]:
    out: List[Tuple[Any, str]] = []

    for detail in details:
        for frame in page.frames:
            try:
                if frame_matches_detail(frame, detail):
                    label = f"frame:{frame.name or frame.url}"
                    if all(existing[1] != label for existing in out):
                        out.append((frame, label))
            except Exception:
                continue

    return out


def get_diagnostics_contexts(page) -> List[Tuple[Any, str]]:
    """
    Devuelve solo los contextos que realmente parecen ser Diagnostics.
    No devuelve cualquier frame con #btnAdd_btn, porque varios cubos tienen Add.
    """
    out: List[Tuple[Any, str]] = []

    details = [d for d in get_worked_frame_details(page) if worked_detail_is_diagnostics(d)]
    out.extend(get_contexts_from_details(page, details))

    # Fallback: frames cuyo nombre o URL indique diagnostics.
    for context, label in priority_contexts(page):
        low = label.lower()
        if "diagnostic" in low or "diagnos" in low:
            if all(existing[1] != label for existing in out):
                out.append((context, label))

    return out



def js_click_diagnostics_tab(context) -> bool:
    """
    Clic exacto al tab superior Diagnostics.
    En esta ACS el ID correcto observado es tabMain_cell1Diagnostics.

    Se evita hacer dispatchEvent('click') + el.click() para no duplicar acciones.
    """
    try:
        return bool(
            context.evaluate(
                """() => {
                    let el = document.querySelector(
                        "#tabMain_cell1Diagnostics, td#tabMain_cell1Diagnostics"
                    );

                    if (!el) {
                        el = document.querySelector(
                            "td[serviceid='Diagnostics'][id*='tabMain_cell1']"
                        );
                    }

                    if (!el) {
                        const tabs = Array.from(document.querySelectorAll("td[serviceid='Diagnostics']"));

                        for (const tab of tabs) {
                            const text = (tab.textContent || "").trim();
                            const id = (tab.id || "").toLowerCase();

                            if (text === "Diagnostics" && id.includes("tabmain")) {
                                el = tab;
                                break;
                            }
                        }
                    }

                    if (!el) return false;

                    try {
                        el.scrollIntoView({ block: "center", inline: "center" });
                    } catch (e) {}

                    const opts = { bubbles: true, cancelable: true, view: window };

                    try { el.dispatchEvent(new MouseEvent("mouseover", opts)); } catch (e) {}
                    try { el.dispatchEvent(new MouseEvent("mousedown", opts)); } catch (e) {}
                    try { el.dispatchEvent(new MouseEvent("mouseup", opts)); } catch (e) {}

                    // Un solo click real. El onclick de ACS se encarga de usSetTabSelected.
                    try {
                        el.click();
                    } catch (e) {
                        try {
                            if (typeof usSetTabSelected === 'function') {
                                usSetTabSelected(
                                    el,
                                    new MouseEvent("click", opts),
                                    'tabMain_tblTabs',
                                    'tabMain_hfTab',
                                    'lnkTab',
                                    'HideFrmButtons'
                                );
                            } else {
                                return false;
                            }
                        } catch (e2) {
                            return false;
                        }
                    }

                    try {
                        const hf = document.querySelector('#tabMain_hfTab');
                        if (hf) {
                            hf.value = 'Diagnostics';
                            hf.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    } catch (e) {}

                    return true;
                }"""
            )
        )
    except Exception:
        return False

def is_diagnostics_tab_selected(page) -> bool:
    for context, _label in get_desktop_contexts(page):
        try:
            state = context.evaluate(
                """() => {
                    const tab = document.querySelector('#tabMain_cell1Diagnostics, td[serviceid="Diagnostics"][id*="tabMain"]');
                    const hf = document.querySelector('#tabMain_hfTab');
                    return {
                        tabClass: tab ? (tab.className || '') : '',
                        hfValue: hf ? (hf.value || '') : '',
                        tabText: tab ? (tab.textContent || '') : ''
                    };
                }"""
            )
            if not isinstance(state, dict):
                continue

            if "active" in norm(state.get("tabClass", "")):
                return True

            if "diagnostic" in norm(state.get("hfValue", "")):
                return True

        except Exception:
            continue

    return False


def has_visible_any(page, selectors: Iterable[str], timeout_ms: int = 800) -> bool:
    """
    Búsqueda rápida general por JS.
    OJO: para Diagnostics usamos diagnostics_view_ready(), no esta función global.
    """
    deadline = time.perf_counter() + timeout_ms / 1000

    while time.perf_counter() < deadline:
        for context, _label in priority_contexts(page):
            for selector in selectors:
                if js_exists(context, selector):
                    return True

        page.wait_for_timeout(150)

    return False


def diagnostics_view_ready(page, timeout_ms: int = 30000) -> bool:
    """
    Confirma que estamos dentro del tab Diagnostics real.
    No acepta Add de otros cubos. Solo valida el frame activo de getWorkedFrame()
    o frames cuyo nombre/URL indique diagnostics.
    """
    deadline = time.perf_counter() + timeout_ms / 1000

    while time.perf_counter() < deadline:
        contexts = get_diagnostics_contexts(page)

        for context, _label in contexts:
            if js_exists(context, "#ddlDiagType, select[name='ddlDiagType']"):
                return True

            if js_exists(context, "#btnAdd_btn, input[name='btnAdd$btn'], input[value='Add']"):
                return True

            try:
                text = norm(context.locator("body").inner_text(timeout=700))
                if "ipping diagnostics" in text and "diagnostics" in text:
                    return True
            except Exception:
                pass

        page.wait_for_timeout(500)

    return False


def invoke_add_diagnostic_js(page) -> Optional[str]:
    """
    Fallback ACS.
    Debe ejecutarse desde frmDesktop, porque getWorkedFrame() apunta al tab activo.
    """
    for context, label in get_desktop_contexts(page):
        try:
            ok = context.evaluate(
                """() => {
                    try {
                        if (typeof getWorkedFrame === 'function') {
                            const wf = getWorkedFrame();

                            if (wf && typeof wf.AddDiagnos === 'function') {
                                wf.AddDiagnos();
                                return true;
                            }

                            if (wf && wf.document) {
                                const btn = wf.document.querySelector("#btnAdd_btn, input[name='btnAdd$btn'], input[value='Add']");
                                if (btn) {
                                    btn.disabled = false;
                                    btn.removeAttribute('disabled');
                                    btn.click();
                                    return true;
                                }
                            }
                        }
                        return false;
                    } catch (e) {
                        return false;
                    }
                }"""
            )

            if ok:
                page.wait_for_timeout(1200)
                return f"{label}:js:getWorkedFrame().AddDiagnos()"

        except Exception:
            continue

    return None


def find_first_in_contexts(
    page,
    contexts: List[Tuple[Any, str]],
    selectors: Iterable[str],
    timeout_ms: int = 8000,
):
    """
    Busca únicamente dentro de los contextos indicados.
    Así no toma campos ni botones de otro cubo.
    """
    last_error = None
    started = time.perf_counter()
    total_timeout = min(max(timeout_ms, 1500), 12000)
    per_selector_timeout = min(max(int(timeout_ms / 10), 250), 900)

    while (time.perf_counter() - started) * 1000 < total_timeout:
        for context, label in contexts:
            for selector in selectors:
                try:
                    item = first_visible(context.locator(selector), timeout_ms=per_selector_timeout)
                    if item:
                        return context, label, item, selector
                except Exception as exc:
                    last_error = exc
                    continue

        page.wait_for_timeout(250)

    raise RuntimeError(
        f"No encontré selector {list(selectors)} en contextos Diagnostics después de "
        f"{round(time.perf_counter() - started, 1)}s. Último error: {last_error}"
    )


def click_any_in_contexts(
    page,
    contexts: List[Tuple[Any, str]],
    selectors: Iterable[str],
    timeout_ms: int = 8000,
) -> str:
    _ctx, label, item, selector = find_first_in_contexts(page, contexts, selectors, timeout_ms=timeout_ms)
    click_input_button(item, timeout_ms=8000, allow_force_enable=True)
    return f"{label}:{selector}"


def get_diagnostic_form_contexts(page) -> List[Tuple[Any, str]]:
    """
    Contextos donde está el formulario Add Diagnostics.
    """
    out: List[Tuple[Any, str]] = []

    for context, label in get_diagnostics_contexts(page):
        if all(existing[1] != label for existing in out):
            out.append((context, label))

    # Si el formulario ya existe en otro frame, pero tiene ddlDiagType,
    # lo agregamos porque ese selector sí es específico del diagnóstico.
    for context, label in priority_contexts(page):
        if js_exists(context, "#ddlDiagType, select[name='ddlDiagType']"):
            if all(existing[1] != label for existing in out):
                out.append((context, label))

    return out


def click_diagnostics_tab(page) -> str:
    """
    Abre el tab superior Diagnostics usando #tabMain_cell1Diagnostics.
    Nunca usa como prueba un botón Add de otro cubo.
    """
    debug_step("Dando click en tab Diagnostics...")

    deadline = time.perf_counter() + 45

    while time.perf_counter() < deadline:
        for context, label in get_desktop_contexts(page):
            if js_click_diagnostics_tab(context):
                debug_step(f"Click enviado a Diagnostics en {label}")

                page.wait_for_timeout(1200)
                wait_soft_network(page, timeout_ms=8000)

                if diagnostics_view_ready(page, timeout_ms=12000):
                    debug_step("Vista Diagnostics cargada.")
                    return f"{label}:#tabMain_cell1Diagnostics"

        page.wait_for_timeout(1000)

    details = get_worked_frame_details(page)
    raise RuntimeError(
        "No pude abrir el tab Diagnostics real con #tabMain_cell1Diagnostics. "
        f"WorkedFrame detectado: {details}"
    )


def open_add_diagnostic(page) -> str:
    """
    Después de abrir Diagnostics, da click en Add del frame activo real.
    Evita doble Add: si un selector recibió click, no intenta otro selector.
    """
    debug_step("Buscando botón Add dentro del frame Diagnostics...")

    if not diagnostics_view_ready(page, timeout_ms=3000):
        click_diagnostics_tab(page)

    contexts = get_diagnostics_contexts(page)

    if not contexts:
        debug_step("No pude mapear frame Diagnostics. Intentando AddDiagnos() por JS...")

        js_result = invoke_add_diagnostic_js(page)

        if js_result:
            deadline = time.perf_counter() + 12

            while time.perf_counter() < deadline:
                if get_diagnostic_form_contexts(page):
                    debug_step("Formulario Add Diagnostics cargado por JS.")
                    return js_result

                page.wait_for_timeout(500)

        raise RuntimeError("No pude identificar el frame real de Diagnostics para presionar Add.")

    selectors_add = [
        "#btnAdd_btn",
        "input[name='btnAdd$btn']",
        "input[value='Add']",
    ]

    for context, label in contexts:
        for selector in selectors_add:
            if not js_exists(context, selector):
                continue

            ok = js_click_selector(context, selector)

            if not ok:
                continue

            debug_step(f"Click enviado a Add en {label}:{selector}")

            # IMPORTANTE:
            # Después de un click exitoso NO seguimos con otros selectores.
            # Si seguimos, ACS puede recibir doble Add o cambiar al formulario incorrecto.
            deadline = time.perf_counter() + 12

            while time.perf_counter() < deadline:
                form_contexts = get_diagnostic_form_contexts(page)

                if form_contexts:
                    debug_step("Formulario Add Diagnostics cargado.")
                    return f"{label}:{selector}"

                page.wait_for_timeout(500)

            raise RuntimeError(
                f"Se hizo click en Add ({label}:{selector}), pero no cargó ddlDiagType. "
                "No se intentará otro Add para evitar doble creación."
            )

    debug_step("No abrió Add por selector. Intentando AddDiagnos() por JS en frmDesktop...")

    js_result = invoke_add_diagnostic_js(page)

    if js_result:
        deadline = time.perf_counter() + 12

        while time.perf_counter() < deadline:
            if get_diagnostic_form_contexts(page):
                debug_step("Formulario Add Diagnostics cargado por JS.")
                return js_result

            page.wait_for_timeout(500)

    raise RuntimeError(
        "No pude abrir Add Diagnostics. Ya no se está haciendo clic en otros cubos; "
        "falló dentro del frame Diagnostics activo."
    )

def button_contexts(page) -> List[Tuple[Any, str]]:
    """
    ACS suele poner Add/Create/OK en frmButtons.
    Por eso para acciones de botones priorizamos ese frame.
    """
    contexts = all_contexts(page)

    def rank(item: Tuple[Any, str]) -> int:
        label = item[1].lower()

        if "frmbuttons" in label:
            return 0

        if "frmdesktop" in label:
            return 1

        if "diagnostics" in label:
            return 2

        if label == "page":
            return 3

        return 4

    return sorted(contexts, key=rank)



def wait_create_button_ready(page, timeout_ms: int = 45000):
    """
    Espera a que ACS habilite Create naturalmente.
    No quitamos disabled porque eso crea pruebas mal armadas.
    """
    selectors = [
        "#btnSendUpdate_btn",
        "input[name='btnSendUpdate$btn']",
        "input[value='Create']",
    ]

    deadline = time.perf_counter() + timeout_ms / 1000
    last_state = None

    while time.perf_counter() < deadline:
        for context, label in button_contexts(page):
            for selector in selectors:
                try:
                    loc = context.locator(selector).first

                    if loc.count() <= 0:
                        continue

                    visible = loc.is_visible(timeout=1000)
                    disabled = loc.evaluate("el => !!el.disabled || el.getAttribute('disabled') !== null")

                    last_state = {
                        "label": label,
                        "selector": selector,
                        "visible": visible,
                        "disabled": disabled,
                    }

                    if visible and not disabled:
                        return context, label, loc, selector

                except Exception:
                    continue

        page.wait_for_timeout(800)

    raise RuntimeError(f"Create nunca quedó habilitado naturalmente. Último estado: {last_state}")

def click_create_diagnostic(page) -> str:
    """
    Presiona Create como humano.
    IMPORTANTE:
    No se usa JS.
    No se quita disabled.
    No se ejecuta getWorkedFrame().Create().
    """
    debug_step("Esperando que Create quede habilitado naturalmente...")

    _ctx, label, create_btn, selector = wait_create_button_ready(page, timeout_ms=45000)

    debug_step(f"Create habilitado en {label}:{selector}. Dando click real...")

    create_btn.scroll_into_view_if_needed(timeout=5000)
    create_btn.click(timeout=10000)

    page.wait_for_timeout(1200)

    return f"{label}:{selector}"



def click_ok_if_appears(page) -> str:
    selectors = [
        "#btnOk_btn",
        "input[name='btnOk$btn']",
        "input[value='OK']",
    ]

    deadline = time.perf_counter() + 15

    while time.perf_counter() < deadline:
        for context, label in button_contexts(page):
            for selector in selectors:
                try:
                    loc = context.locator(selector).first

                    if loc.count() <= 0:
                        continue

                    if loc.is_visible(timeout=1000):
                        disabled = loc.evaluate("el => !!el.disabled || el.getAttribute('disabled') !== null")

                        if not disabled:
                            debug_step(f"Click real enviado a OK en {label}:{selector}")
                            loc.scroll_into_view_if_needed(timeout=3000)
                            loc.click(timeout=5000)
                            page.wait_for_timeout(700)
                            return f"{label}:{selector}"

                except Exception:
                    continue

        page.wait_for_timeout(500)

    return "NO_APARECIO"


def choose_connection(page, preferred_interface: str, contexts: Optional[List[Tuple[Any, str]]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"requested": preferred_interface}

    contexts = contexts or get_diagnostic_form_contexts(page) or priority_contexts(page)

    _ctx, label, ddl, selector = find_first_in_contexts(
        page,
        contexts,
        ["#ddlConnection", "select[name='ddlConnection']"],
        timeout_ms=8000,
    )
    result["selector"] = f"{label}:{selector}"

    try:
        options = ddl.evaluate(
            """el => Array.from(el.options).map(o => ({value:o.value, text:o.textContent.trim()}))"""
        )
    except Exception:
        options = []

    result["options"] = options

    values = [x.get("value", "") for x in options]
    chosen = preferred_interface if preferred_interface in values else ""

    result["chosen"] = chosen or "Default route"
    select_value(ddl, chosen)

    return result


def create_ipping_diagnostic(
    page,
    host: str,
    repetitions: int,
    preferred_interface: str,
) -> Dict[str, Any]:
    selectors: Dict[str, Any] = {}

    debug_step("Iniciando IPPing diagnostics...")

    selectors["tab_diagnostics"] = click_diagnostics_tab(page)

    if not diagnostics_view_ready(page, timeout_ms=15000):
        raise RuntimeError("Se intentó abrir Diagnostics, pero no apareció Add ni ddlDiagType.")

    selectors["worked_frame_before_add"] = get_worked_frame_details(page)

    selectors["add"] = open_add_diagnostic(page)

    debug_step("Esperando formulario IPPing...")
    form_contexts = get_diagnostic_form_contexts(page)

    if not form_contexts:
        raise RuntimeError("Se presionó Add, pero no pude ubicar el formulario de diagnóstico con ddlDiagType.")

    debug_step("Seleccionando IPPing diagnostics...")
    _ctx, label, diag_type, selector = find_first_in_contexts(
        page,
        form_contexts,
        ["#ddlDiagType", "select[name='ddlDiagType']"],
        timeout_ms=8000,
    )
    selectors["ddlDiagType"] = f"{label}:{selector}"
    select_value(diag_type, "IPPingDiagnostic")

    wait_soft_network(page, timeout_ms=12000)
    page.wait_for_timeout(1200)

    # Después del postback al seleccionar IPPingDiagnostic puede cambiar el frame/formulario.
    form_contexts = get_diagnostic_form_contexts(page) or form_contexts

    debug_step("Escribiendo host...")
    _ctx, label, host_input, selector = find_first_in_contexts(
        page,
        form_contexts,
        [
            "#txtHost",
            "input[name='txtHost']",
            "input[id*='Host']",
            "input[name*='Host']",
        ],
        timeout_ms=8000,
    )
    selectors["host"] = f"{label}:{selector}"
    selectors["host_written"] = set_input_value(host_input, host)

    debug_step("Seleccionando interface...")
    selectors["connection"] = choose_connection(page, preferred_interface, contexts=form_contexts)

    debug_step("Escribiendo repeticiones...")
    _ctx, label, reps_input, selector = find_first_in_contexts(
        page,
        form_contexts,
        [
            "#txtNumberRepetitions",
            "input[name='txtNumberRepetitions']",
            "input[id*='NumberRepetitions']",
        ],
        timeout_ms=8000,
    )
    selectors["repetitions"] = f"{label}:{selector}"
    selectors["repetitions_written"] = set_input_value(reps_input, str(repetitions))

    try:
        _ctx, label, timeout_input, selector = find_first_in_contexts(
            page,
            form_contexts,
            ["#txtTimeout", "input[name='txtTimeout']", "input[id*='Timeout']"],
            timeout_ms=2000,
        )
        try:
            current = clean_text(timeout_input.input_value(timeout=1000))
        except Exception:
            current = ""
        if not current:
            set_input_value(timeout_input, "5000")
        selectors["timeout"] = f"{label}:{selector}"
    except Exception:
        pass

    
    debug_step("Esperando estabilización del formulario antes de Create...")
    page.wait_for_timeout(1200)
    debug_step("Presionando Create...")
    selectors["create"] = click_create_diagnostic(page)

    page.wait_for_timeout(700)

    selectors["confirm_ok"] = click_ok_if_appears(page)

    wait_soft_network(page, timeout_ms=10000)
    page.wait_for_timeout(1200)

    return selectors


def parse_diagnostic_rows_from_html(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: List[Dict[str, str]] = []

    for tr in soup.select("tr[rowkey]"):
        cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all("td")]

        if len(cells) < 4:
            continue

        state = ""
        diag_type = ""
        created = ""
        completed = ""
        joined = " | ".join(cells)

        for cell in cells:
            cn = norm(cell)
            if cn in {"pending", "completed", "failed", "sent", "rejected", "no info"}:
                state = cell
                break

        for cell in cells:
            if "diagnostics" in norm(cell):
                diag_type = cell
                break

        date_cells = [c for c in cells if re.search(r"\d{1,2}/\d{1,2}/\d{4}", c)]

        if date_cells:
            created = date_cells[0]

        if len(date_cells) >= 2:
            completed = date_cells[1]

        rows.append(
            {
                "rowkey": tr.get("rowkey", ""),
                "state": state,
                "diagnostics_type": diag_type,
                "created": created,
                "completed": completed,
                "text": joined,
            }
        )

    return rows


def get_diagnostic_rows(page) -> List[Dict[str, str]]:
    return parse_diagnostic_rows_from_html(get_all_html(page))


def refresh_diagnostics_list(page) -> str:
    """
    Refresca la lista de Diagnostics.
    ACS a veces deja la fila en Sent/Pending visualmente hasta refrescar el frame.
    """
    # Primero intentamos refrescar el frame donde está la tabla de diagnósticos.
    for context, label in priority_contexts(page):
        try:
            has_rows = context.evaluate(
                """() => !!document.querySelector("tr[rowkey]")"""
            )

            if has_rows:
                context.evaluate("() => window.location.reload()")
                page.wait_for_timeout(1800)
                debug_step(f"Tabla Diagnostics refrescada en {label}")
                return f"{label}:reload"
        except Exception:
            continue

    # Fallback: volver a hacer click en el tab Diagnostics.
    for context, label in priority_contexts(page):
        try:
            if js_click_diagnostics_tab(context):
                page.wait_for_timeout(700)
                debug_step(f"Tab Diagnostics refrescado en {label}")
                return f"{label}:tab_refresh"
        except Exception:
            continue

    return "NO_REFRESH"

def close_websocket_timeout_if_present(page) -> bool:
    """
    Cierra el popup de ACS/Friendly 'WebSocket timeout' si aparece.
    Esto evita que bloquee la lectura de la tabla Diagnostics.
    """
    try:
        text = get_all_visible_text(page)
        if "WebSocket timeout" not in text and "websocket timeout" not in text.lower():
            return False
    except Exception:
        return False

    debug_step("Popup WebSocket timeout detectado. Cerrando OK...")

    selectors = [
        "#btnOk_btn",
        "input[name='btnOk$btn']",
        "input[value='OK']",
        "button:has-text('OK')",
        "text=OK",
    ]

    for context, label in button_contexts(page):
        for selector in selectors:
            try:
                loc = context.locator(selector).first

                if loc.count() <= 0:
                    continue

                if loc.is_visible(timeout=1000):
                    loc.scroll_into_view_if_needed(timeout=3000)
                    loc.click(timeout=5000, force=True)
                    page.wait_for_timeout(2500)
                    debug_step(f"Popup WebSocket timeout cerrado en {label}:{selector}")
                    return True
            except Exception:
                continue

    try:
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)
        debug_step("Popup WebSocket timeout cerrado con Enter.")
        return True
    except Exception:
        return False


def wait_for_latest_ipping_finished(page, timeout_ms: int = 480000) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """
    Espera a que la prueba IPPing nueva pase de Pending/Sent a Completed/Failed.

    Default: 480000 ms = 8 minutos.
    """
    deadline = time.perf_counter() + timeout_ms / 1000
    last_rows: List[Dict[str, str]] = []
    latest_seen: Dict[str, str] = {}
    intentos = 0

    while time.perf_counter() < deadline:
        close_websocket_timeout_if_present(page)
        intentos += 1
        page.wait_for_timeout(2500)

        rows = get_diagnostic_rows(page)
        last_rows = rows

        ip_rows = [r for r in rows if "ipping" in norm(r.get("diagnostics_type", ""))]

        if ip_rows:
            latest = ip_rows[0]
            latest_seen = latest
            state = norm(latest.get("state", ""))

            debug_step(
                f"Revisando IPPing intento {intentos}: estado={latest.get('state')} rowkey={latest.get('rowkey')}"
            )

            if state in {"completed", "failed"}:
                latest["timeout"] = "false"
                return latest, rows

            # Cada 10 intentos refrescamos la tabla.
            # 10 intentos * 3 segundos ≈ cada 30 segundos.
            if intentos % 12 == 0:
                debug_step("IPPing sigue en Pending/Sent. Refrescando lista Diagnostics...")
                refresh_diagnostics_list(page)

        else:
            debug_step(f"Revisando IPPing intento {intentos}: no veo filas IPPing todavía")

            if intentos % 5 == 0:
                refresh_diagnostics_list(page)

    if latest_seen:
        latest_seen["timeout"] = "true"
        debug_step(
            f"IPPing no cambió a Completed/Failed dentro del timeout. "
            f"Último estado={latest_seen.get('state')} rowkey={latest_seen.get('rowkey')}"
        )
        return latest_seen, last_rows

    raise TimeoutError(f"La prueba IPPing no apareció en la tabla. Últimas filas: {last_rows[:3]}")

def click_diagnostic_row(page, rowkey: str) -> str:
    selectors = [f"tr[rowkey='{rowkey}']", f'tr[rowkey="{rowkey}"]']

    for context, label in all_contexts(page):
        for selector in selectors:
            try:
                item = first_visible(context.locator(selector), timeout_ms=1000)
                if item:
                    item.scroll_into_view_if_needed(timeout=5000)
                    item.click(timeout=5000)
                    page.wait_for_timeout(2000)
                    return f"{label}:{selector}"
            except Exception:
                continue

    raise RuntimeError(f"No pude hacer clic en la fila rowkey={rowkey}")


def parse_result_output(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#tblResultOutput")

    if not table:
        for candidate in soup.select("table"):
            text = norm(candidate.get_text(" ", strip=True))
            if "averageresponsetime" in text or "diagnosticsstate" in text:
                table = candidate
                break

    if not table:
        return {}

    result: Dict[str, str] = {}

    for tr in table.select("tr"):
        cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        cells = [c for c in cells if c]

        if not cells:
            continue

        if len(cells) >= 2:
            key, value = cells[0], cells[1]

            if norm(key) in {"parameter", "value", "diagnostics results"}:
                continue

            result[key] = value

    return result



def is_final_ipping_result(data: Dict[str, str]) -> bool:
    """
    Valida que el resultado abierto sea realmente el resultado final del IPPing,
    no el formulario de creación que queda en Requested.
    """
    if not data:
        return False

    diag_state = norm(data.get("DiagnosticsState", ""))
    success = clean_text(data.get("SuccessCount", ""))
    failure = clean_text(data.get("FailureCount", ""))
    avg = clean_text(data.get("AverageResponseTime", ""))

    if diag_state not in {"complete", "completed", "failed", "error"}:
        return False

    if not success and not failure:
        return False

    if diag_state in {"complete", "completed"} and not avg:
        return False

    return True


def wait_for_result_output(page, timeout_ms: int = 120000) -> Dict[str, str]:
    """
    Espera el resultado real del diagnóstico.
    No acepta DiagnosticsState=Requested porque eso es solo el formulario/tarea creada.
    """
    deadline = time.perf_counter() + timeout_ms / 1000
    last_data: Dict[str, str] = {}

    while time.perf_counter() < deadline:
        html = get_all_html(page)
        data = parse_result_output(html)

        if data:
            last_data = data
            debug_step(
                f"Resultado IPPing visible: DiagnosticsState={data.get('DiagnosticsState', '')}, "
                f"SuccessCount={data.get('SuccessCount', '')}, "
                f"FailureCount={data.get('FailureCount', '')}"
            )

            if is_final_ipping_result(data):
                return data

        page.wait_for_timeout(1200)

    debug_step(f"No apareció resultado final IPPing. Último resultado leído: {last_data}")
    return last_data

def build_acs_template(datos: Dict[str, Any]) -> str:
    estado = datos.get("estado_equipo") or {}
    network_map = datos.get("network_map") or []
    diag = datos.get("ipping_result") or {}
    resultado_operativo = datos.get("resultado_operativo") or {}
    wan = estado.get("wan") or {}

    lines: List[str] = []

    lines.append("# ==========================")
    lines.append("# ESTADO DEL EQUIPO")
    lines.append("# ==========================")
    lines.append("")
    lines.append(f"{'Serial':<28}{estado.get('serial', 'no data')}")
    lines.append(f"{'Connected to ACS':<28}{estado.get('connected_to_acs', 'no data')}")
    lines.append("")
    lines.append("WAN")
    lines.append("")
    lines.append(f"{'WAN IP':<28}{wan.get('ip', estado.get('wan_ip', 'no data'))}")
    lines.append(f"{'WAN Estado':<28}{wan.get('estado', estado.get('wan_estado', 'no data'))}")
    lines.append(f"{'Connection enabled':<28}{wan.get('connection_enabled', 'no data')}")
    lines.append("")
    lines.append("Wi-Fi")

    wifi_rows = estado.get("wifi") or []

    if wifi_rows:
        for row in wifi_rows:
            ssid = row.get("ssid", "no data")
            status = row.get("estado", "no data")
            lines.append(f"{ssid:<28}{status}")
    else:
        lines.append(f"{'no data':<28}no data")

    lines.append("")
    lines.append(f"{'VoIP':<28}{estado.get('voip', 'no data')}")
    lines.append("")
    lines.append(f"{'Falla de Conectividad (últimas 24h)':<45}{estado.get('connectivity_failure_24h', '0')}")
    lines.append(f"{'Cantidad de reinicios (últimas 24h)':<45}{estado.get('reboot_amount_24h', '0')}")
    lines.append("")
    lines.append("Firmware")
    lines.append("")
    lines.append(estado.get("firmware", "no data"))
    lines.append("")
    lines.append(f"{'Registrado:':<20}{estado.get('registered', 'no data')}")
    lines.append(f"{'Última conexión:':<20}{estado.get('last_connect', 'no data')}")
    lines.append(f"{'Uptime':<20}{estado.get('uptime', 'no data')}")
    lines.append("")
    lines.append("# ==========================")
    lines.append("# NETWORK MAP")
    lines.append("# ==========================")
    lines.append("")

    if network_map:
        for block in network_map:
            for item in block:
                lines.append(item)
            lines.append("")
    else:
        lines.append("no data")
        lines.append("")

    lines.append("# ==========================")
    lines.append("# PING INTERNET")
    lines.append("# ==========================")
    lines.append("")

    ordered = [
        "DiagnosticsState",
        "FailureCount",
        "Host",
        "Interface",
        "MaximumResponseTime",
        "MinimumResponseTime",
        "AverageResponseTime",
        "NumberOfRepetitions",
        "SuccessCount",
        "Timeout",
    ]

    for key in ordered:
        lines.append(f"{key:<28}{diag.get(key, 'no data')}")

    warning = diag.get("_warning") or diag.get("observacion") or resultado_operativo.get("mensaje", "")
    if warning:
        lines.append(f"{'Observación':<28}{warning}")

    lines.append("")
    lines.append("# ==========================")
    lines.append("# RESULTADO OPERATIVO ACS")
    lines.append("# ==========================")
    lines.append("")
    lines.append(f"{'ACS Main':<28}{'OK' if resultado_operativo.get('acs_main_ok') else 'NO CONFIRMADO'}")
    lines.append(f"{'IPPing':<28}{resultado_operativo.get('ipping_estado', 'no data')}")
    lines.append(f"{'Decisión':<28}{resultado_operativo.get('estado', 'no data')}")
    lines.append(f"{'Mensaje':<28}{resultado_operativo.get('mensaje', 'no data')}")

    return "\n".join(lines).rstrip() + "\n"

def consultar_acs_tr069(
    url: str,
    username: str,
    password: str,
    serial: str,
    host: str,
    repetitions: int,
    preferred_interface: str,
    headless: bool,
    slow_mo_ms: int,
    timeout_ms: int,
    ipping_wait_ms: int,
    hold_seconds: int,
    run_ipping: bool,
    search_by: str = "serial",
) -> ACSResult:
    started = time.perf_counter()
    serial = clean_text(serial).upper()
    selectors: Dict[str, Any] = {}
    screenshot = ""
    html = ""
    final_url = url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        try:
            debug_step("Iniciando login ACS...")
            selectors["login"] = login_acs(page, url, username, password, timeout_ms)

            requested_search_by = norm(search_by)
            if requested_search_by == "macaddress":
                requested_search_by = "mac"

            debug_step(
                f"Login OK. Buscando equipo por "
                f"{requested_search_by}..."
            )

            first_attempt = search_device(
                page,
                serial,
                requested_search_by,
            )

            search_attempts = [first_attempt]
            effective_search_by = (
                first_attempt.get("search_by")
                or requested_search_by
            )
            current_attempt = first_attempt

            # ACS_SERIAL_MAC_SAME_VALUE_V1_START
            # En Friendly/ACR69 un mismo identificador puede estar indexado
            # bajo Serial Number o MAC address, incluso si el texto no tiene
            # el formato clásico de una MAC.
            #
            # Si Serial Number devuelve No data found, se reutiliza
            # EXACTAMENTE el mismo valor y la misma sesión ACS, cambiando
            # únicamente el selector Search by a MAC address.
            can_retry_as_mac = bool(
                clean_text(serial)
            )

            if (
                current_attempt.get("no_data_found")
                and effective_search_by == "serial"
                and can_retry_as_mac
            ):
                debug_step(
                    f"Serial sin datos. Reintentando el mismo valor "
                    f"como MAC address: {serial}"
                )

                second_attempt = search_device(
                    page,
                    serial,
                    "mac",
                )
            # ACS_SERIAL_MAC_SAME_VALUE_V1_END

                search_attempts.append(second_attempt)
                current_attempt = second_attempt
                effective_search_by = "mac"

            selectors["search"] = current_attempt
            selectors["search_attempts"] = search_attempts
            selectors["search_by_requested"] = requested_search_by
            selectors["search_by_used"] = effective_search_by

            if current_attempt.get("no_data_found"):
                screenshot, html = save_evidence(
                    page,
                    f"acs_sin_resultados_{safe_prefix(serial)}",
                )

                attempted_types = [
                    item.get("search_by", "")
                    for item in search_attempts
                ]

                return ACSResult(
                    ok=False,
                    estado="ACS_SIN_RESULTADOS",
                    mensaje=(
                        f"ACS indico No data found para {serial}. "
                        f"Tipos consultados: "
                        f"{', '.join(attempted_types)}."
                    ),
                    url_inicial=url,
                    url_final=page.url,
                    serial=serial,
                    screenshot=screenshot,
                    html=html,
                    duracion_seg=round(
                        time.perf_counter() - started,
                        2,
                    ),
                    datos={
                        "search_by_requested": requested_search_by,
                        "search_by_used": effective_search_by,
                        "search_attempts": attempted_types,
                        "valor_consultado": serial,
                    },
                    selectores=selectors,
                )

            debug_step("Equipo cargado. Extrayendo datos visibles del Main...")
            datos = extract_acs_visible_data(page)

            # IMPORTANTE:
            # El Main de ACS es la base operativa. IPPing es una prueba adicional.
            # Si el Main cargó y tiene serial, ACS no debe considerarse fallido completo
            # solo porque IPPing termine en Failed/Sent/Pending.
            estado_inicial = datos.get("estado_equipo") or {}
            serial_main = clean_text(
                estado_inicial.get("serial")
                or (datos.get("device_information") or {}).get("Serial number")
                or serial
            )
            datos["main_ok"] = bool(serial_main and norm(serial_main) != "no data")

            if run_ipping:
                debug_step("Iniciando creación de IPPing diagnostics...")
                selectors["ipping_create"] = create_ipping_diagnostic(
                    page,
                    host=host,
                    repetitions=repetitions,
                    preferred_interface=preferred_interface,
                )

                debug_step("IPPing creado. Esperando Completed o Failed...")
                latest, rows = wait_for_latest_ipping_finished(
                    page,
                    timeout_ms=ipping_wait_ms,
                )
                debug_step(f"IPPing último estado detectado: {latest.get('state')}")

                selectors["diagnostic_rows"] = rows[:5]
                datos["latest_ipping_row"] = latest

                state_norm = norm(latest.get("state", ""))

                if latest.get("rowkey") and state_norm in {"completed", "failed"}:
                    result = {}

                    if state_norm == "failed":
                        # No abrir la fila cuando ACS ya marcó Failed:
                        # ACS normalmente muestra el formulario viejo en Requested y no métricas.
                        debug_step(
                            f"IPPing terminó en Failed rowkey={latest.get('rowkey')}. "
                            "No se abrirá la fila porque ACS suele mostrar Requested sin métricas."
                        )

                        result = {
                            "Diagnostics type": latest.get("diagnostics_type", "IPPing diagnostics"),
                            "DiagnosticsState": "Failed",
                            "Host": host,
                            "Interface": preferred_interface,
                            "NumberOfRepetitions": str(repetitions),
                            "SuccessCount": "no data",
                            "FailureCount": "no data",
                            "MinimumResponseTime": "no data",
                            "AverageResponseTime": "no data",
                            "MaximumResponseTime": "no data",
                            "Timeout": "5000",
                            "_rowkey": latest.get("rowkey", ""),
                            "_created": latest.get("created", ""),
                            "_completed": latest.get("completed", ""),
                            "_warning": (
                                "La tarea IPPing terminó en Failed. "
                                "ACS sí cargó Main; el fallo corresponde solo a la prueba Ping Internet."
                            ),
                        }

                    elif state_norm == "completed":
                        debug_step(f"Abriendo fila final IPPing rowkey={latest['rowkey']}...")

                        try:
                            selectors["open_result_row"] = click_diagnostic_row(
                                page,
                                latest["rowkey"],
                            )
                            result = wait_for_result_output(page, timeout_ms=60000)
                        except Exception as exc:
                            selectors["open_result_row_error"] = str(exc)
                            result = {}

                        if not is_final_ipping_result(result):
                            debug_step(
                                "La fila está Completed, pero el resultado aún no muestra métricas finales. "
                                "Reintentando..."
                            )

                            try:
                                refresh_diagnostics_list(page)
                                page.wait_for_timeout(3000)

                                selectors["open_result_row_retry"] = click_diagnostic_row(
                                    page,
                                    latest["rowkey"],
                                )
                                result_retry = wait_for_result_output(page, timeout_ms=90000)

                                if is_final_ipping_result(result_retry):
                                    result = result_retry
                            except Exception as exc:
                                selectors["open_result_row_retry_error"] = str(exc)

                            if not is_final_ipping_result(result):
                                result = {
                                    "Diagnostics type": latest.get("diagnostics_type", "IPPing diagnostics"),
                                    "DiagnosticsState": "Completed",
                                    "Host": host,
                                    "Interface": preferred_interface,
                                    "NumberOfRepetitions": str(repetitions),
                                    "SuccessCount": "no data",
                                    "FailureCount": "no data",
                                    "MinimumResponseTime": "no data",
                                    "AverageResponseTime": "no data",
                                    "MaximumResponseTime": "no data",
                                    "Timeout": "5000",
                                    "_rowkey": latest.get("rowkey", ""),
                                    "_created": latest.get("created", ""),
                                    "_completed": latest.get("completed", ""),
                                    "_warning": "La fila quedó Completed, pero ACS no mostró métricas finales.",
                                }

                else:
                    result = {
                        "DiagnosticsState": latest.get("state", "no data"),
                        "rowkey": latest.get("rowkey", ""),
                        "Created": latest.get("created", ""),
                        "Completed": latest.get("completed", ""),
                        "Host": host,
                        "Interface": preferred_interface,
                        "NumberOfRepetitions": str(repetitions),
                        "timeout": latest.get("timeout", "true"),
                        "observacion": (
                            "La tarea IPPing fue creada, pero ACS no cambió a Completed/Failed "
                            "dentro del tiempo máximo de espera. ACS Main sí queda como fuente principal."
                        ),
                    }

                datos["ipping_result"] = result

            else:
                datos["latest_ipping_row"] = {}
                datos["ipping_result"] = {}

            final_url = page.url
            screenshot, html = save_evidence(page, f"acs_serial_{safe_prefix(serial)}")

            estado_equipo = datos.get("estado_equipo") or {}
            latest = datos.get("latest_ipping_row") or {}
            ipping_result = datos.get("ipping_result") or {}

            serial_extraido = clean_text(
                estado_equipo.get("serial")
                or (datos.get("device_information") or {}).get("Serial number")
                or serial_main
                or serial
            )

            # La pantalla de resultado de IPPing puede ocultar Subscriber info y Device information.
            # Si ya logramos extraer el serial desde ESTADO DEL EQUIPO, la consulta base es válida.
            base_ok = bool(serial_extraido and norm(serial_extraido) != "no data")
            datos["main_ok"] = base_ok

            latest_state = norm(latest.get("state", ""))
            ipping_timeout = norm(latest.get("timeout", "")) == "true"
            diag_state = norm(ipping_result.get("DiagnosticsState", ""))

            if not base_ok:
                ok_final = False
                estado = "ACS_PARCIAL"
                mensaje = "ACS cargó parcialmente; no se logró extraer información mínima del equipo."

            elif run_ipping and latest_state == "completed":
                if is_final_ipping_result(ipping_result):
                    ok_final = True
                    estado = "ACS_OK"
                    mensaje = "ACS Main OK e IPPing completado con métricas finales."
                else:
                    ok_final = True
                    estado = "ACS_MAIN_OK_IPPING_COMPLETED_SIN_METRICAS"
                    mensaje = "ACS Main OK. La fila IPPing terminó en Completed, pero ACS no mostró métricas finales completas."

            elif run_ipping and latest_state == "failed":
                ok_final = True
                estado = "ACS_MAIN_OK_IPPING_FAILED"
                mensaje = "ACS Main OK. La prueba IPPing terminó en Failed; no se toma como falla total de ACS."

            elif run_ipping and latest_state in {"pending", "sent"}:
                ok_final = True
                estado = f"ACS_MAIN_OK_IPPING_{latest_state.upper()}"
                mensaje = "ACS Main OK. La prueba IPPing fue creada, pero aún no cambió a Completed/Failed."

            elif run_ipping and (ipping_timeout or diag_state == "requested"):
                ok_final = True
                estado = "ACS_MAIN_OK_IPPING_EN_PROCESO"
                mensaje = (
                    "ACS Main OK. La prueba IPPing quedó creada/enviada, pero ACS no entregó resultado final "
                    "dentro del tiempo de espera."
                )

            elif run_ipping:
                ok_final = True
                estado = "ACS_MAIN_OK_IPPING_SIN_RESULTADO"
                mensaje = "ACS Main OK. La prueba IPPing fue creada, pero no se obtuvo resultado parseable."

            else:
                ok_final = True
                estado = "ACS_MAIN_OK"
                mensaje = "Consulta ACS completada con datos del Main."

            datos["resultado_operativo"] = {
                "acs_main_ok": base_ok,
                "ipping_estado": latest_state or diag_state or "no ejecutado",
                "estado": estado,
                "mensaje": mensaje,
            }

            plantilla_acs = build_acs_template(datos)

            if hold_seconds > 0:
                page.wait_for_timeout(hold_seconds * 1000)

            return ACSResult(
                ok=ok_final,
                estado=estado,
                mensaje=mensaje,
                url_inicial=url,
                url_final=final_url,
                serial=serial,
                screenshot=screenshot,
                html=html,
                error=None,
                duracion_seg=round(time.perf_counter() - started, 2),
                datos=datos,
                plantilla_acs=plantilla_acs,
                selectores=selectors,
            )

        except Exception as exc:
            final_url = page.url if page else final_url

            try:
                screenshot, html = save_evidence(page, f"acs_error_{safe_prefix(serial)}")
            except Exception:
                pass

            if hold_seconds > 0 and not headless:
                page.wait_for_timeout(hold_seconds * 1000)

            return ACSResult(
                ok=False,
                estado="ACS_ERROR",
                mensaje="Falló la automatización ACS. Revisa error y evidencia.",
                url_inicial=url,
                url_final=final_url,
                serial=serial,
                screenshot=screenshot,
                html=html,
                error=str(exc),
                duracion_seg=round(time.perf_counter() - started, 2),
                datos={},
                plantilla_acs="",
                selectores=selectors,
            )

        finally:
            browser.close()

def main() -> int:
    load_env_file(BASE_DIR / ".env")
    load_env_file(BASE_DIR / ".env.local")

    parser = argparse.ArgumentParser(description="Automatización ACS TR069 Friendly One-IoT")
    parser.add_argument("--url", default=os.getenv("ACS_TR069_URL", DEFAULT_ACS_URL))
    parser.add_argument("--user", default=os.getenv("ACS_TR069_USER") or os.getenv("ACS_USER"))
    parser.add_argument("--password", default=os.getenv("ACS_TR069_PASSWORD") or os.getenv("ACS_PASSWORD"))
    parser.add_argument("--serial", default=os.getenv("ACS_TR069_SERIAL"))
    parser.add_argument(
        "--search-by",
        choices=("serial", "mac"),
        default=os.getenv("ACS_TR069_SEARCH_BY", "serial").strip().lower(),
    )
    parser.add_argument("--host", default=os.getenv("ACS_TR069_PING_HOST", "www.facebook.com"))
    parser.add_argument("--repetitions", type=int, default=int(os.getenv("ACS_TR069_PING_REPETITIONS", "3")))
    parser.add_argument("--interface", dest="preferred_interface", default=os.getenv("ACS_TR069_INTERFACE", "Device.IP.Interface.3"))
    parser.add_argument("--headless", action="store_true", default=env_bool("ACS_TR069_HEADLESS", False))
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=int(os.getenv("ACS_TR069_SLOW_MO_MS", "120")))
    
    parser.add_argument("--timeout-ms", type=int, default=int(os.getenv("ACS_TR069_TIMEOUT_MS", "180000")))
    parser.add_argument(
        "--ipping-wait-ms",
        type=int,
        default=int(os.getenv("ACS_TR069_IPPING_WAIT_MS", "480000")),
        help="Tiempo máximo esperando que IPPing pase de Pending/Sent a Completed/Failed.",
    )
    parser.add_argument("--hold-seconds", type=int, default=int(os.getenv("ACS_TR069_HOLD_SECONDS", "0")))
    parser.add_argument("--no-ipping", action="store_true", help="Solo login/búsqueda/lectura, sin crear IPPing")
    parser.add_argument("--json-output", action="store_true")

    args = parser.parse_args()

    if args.headed:
        args.headless = False

    username = args.user
    password = args.password
    serial = args.serial

    if not username and not args.json_output:
        username = input("Usuario ACS: ").strip()

    if not password and not args.json_output:
        password = getpass("Contraseña ACS: ")

    if not serial and not args.json_output:
        serial = input("Serial ONT: ").strip()

    if not username or not password or not serial:
        result = {
            "ok": False,
            "estado": "PARAMETROS_FALTANTES",
            "mensaje": "Configura ACS_TR069_USER, ACS_TR069_PASSWORD y ACS_TR069_SERIAL o pásalos por CLI.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    result = consultar_acs_tr069(
        url=args.url,
        username=username,
        password=password,
        serial=serial,
        host=args.host,
        repetitions=args.repetitions,
        preferred_interface=args.preferred_interface,
        headless=args.headless,
        slow_mo_ms=args.slow_mo,
        timeout_ms=args.timeout_ms,
        ipping_wait_ms=args.ipping_wait_ms,
        hold_seconds=args.hold_seconds,
        run_ipping=not args.no_ipping,
        search_by=args.search_by,
    ).as_dict()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
