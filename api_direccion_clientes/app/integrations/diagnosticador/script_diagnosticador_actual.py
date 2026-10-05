#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnosticador_login.py
------------------------------------------------------------
Automatización local del Diagnosticador residencial usando
Python + Playwright.

Funciones:
  1) Probar login.
  2) Consultar por MAC o Cuenta Matriz.
  3) Esperar a que Vaadin cargue toda la información.
  4) Extraer del HTML la misma información que hoy se pega
     manualmente en la plantilla del FTTH NOC Assistant.

Uso recomendado:
  python scraping/diagnosticador_login.py --headed --query-type mac --query 4485DA0A6A6E
  python scraping/diagnosticador_login.py --headed --query-type cuenta --query 426873

También puede ser llamado por server.js desde:
  POST /api/diagnosticador/login
  POST /api/diagnosticador/consultar
"""

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
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception as exc:  # pragma: no cover
    print(
        json.dumps(
            {
                "ok": False,
                "estado": "PLAYWRIGHT_NO_INSTALADO",
                "error": "No se pudo importar Playwright. Ejecuta: pip install -r requirements_scraping.txt && python -m playwright install chromium",
                "detalle": str(exc),
            },
            ensure_ascii=False,
        )
    )
    sys.exit(2)

try:
    from bs4 import BeautifulSoup
except Exception as exc:  # pragma: no cover
    print(
        json.dumps(
            {
                "ok": False,
                "estado": "BS4_NO_INSTALADO",
                "error": "No se pudo importar BeautifulSoup. Ejecuta: pip install -r requirements_scraping.txt",
                "detalle": str(exc),
            },
            ensure_ascii=False,
        )
    )
    sys.exit(2)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_URL = os.getenv("DIAGNOSTICADOR_URL", "").strip()


# ---------------------------------------------------------------------
# Utilidades generales
# ---------------------------------------------------------------------
def load_env_file(path: Path) -> None:
    """Carga .env simple sin depender de python-dotenv."""
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


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
    value = (
        value.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )
    return value


def normalize_mac(value: str) -> str:
    value = clean_text(value).upper()
    hex_only = re.sub(r"[^0-9A-F]", "", value)
    if len(hex_only) == 12:
        return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))
    return value


def normalize_query_value(tipo: str, value: str) -> str:
    value = clean_text(value)
    if tipo == "mac":
        return normalize_mac(value)
    if tipo == "cuenta":
        return re.sub(r"\D", "", value)
    return value


def safe_prefix_value(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "", value.replace(":", ""))[:80] or "consulta"


# ---------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------
@dataclass
class LoginResult:
    ok: bool
    estado: str
    url_inicial: str
    url_final: str
    screenshot: str
    html: str
    mensaje: str
    error: Optional[str] = None
    login_context: Optional[str] = None
    duracion_seg: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "estado": self.estado,
            "url_inicial": self.url_inicial,
            "url_final": self.url_final,
            "screenshot": self.screenshot,
            "html": self.html,
            "mensaje": self.mensaje,
            "error": self.error,
            "login_context": self.login_context,
            "duracion_seg": self.duracion_seg,
        }


@dataclass
class ConsultaResult(LoginResult):
    consulta_tipo: Optional[str] = None
    consulta_valor: Optional[str] = None
    plantilla: Optional[str] = None
    datos: Optional[Dict[str, Any]] = None
    selectores: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        data = super().as_dict()
        data.update(
            {
                "consulta": {
                    "tipo": self.consulta_tipo,
                    "valor": self.consulta_valor,
                },
                "plantilla": self.plantilla,
                "datos": self.datos or {},
                "selectores": self.selectores or {},
            }
        )
        return data


# ---------------------------------------------------------------------
# Selectores Playwright / Vaadin
# ---------------------------------------------------------------------
def first_visible(locator, timeout_ms: int = 1000):
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


def all_contexts(page) -> List[Tuple[Any, str]]:
    candidates: List[Tuple[Any, str]] = [(page, "page")]
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        candidates.append((frame, f"frame:{frame.name or frame.url}"))
    return candidates


def fill_by_selectors(context, selectors: Iterable[str], value: str, field_name: str):
    last_error = None
    for selector in selectors:
        try:
            locator = context.locator(selector)
            item = first_visible(locator)
            if item:
                item.click(timeout=3000)
                item.fill(value, timeout=5000)
                try:
                    item.evaluate(
                        """el => {
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.blur();
                        }"""
                    )
                except Exception:
                    pass
                return selector
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"No encontré el campo {field_name}. Último error: {last_error}")


def click_button_by_caption(context, caption: str, exact: bool = True, timeout_ms: int = 1200) -> str:
    """Clic en botón Vaadin por caption. Evita confundir 'Consultar' con 'Volver a consultar'."""
    expected = norm(caption)
    last_error = None

    try:
        captions = context.locator("span.v-button-caption")
        for idx in range(captions.count()):
            item = captions.nth(idx)
            try:
                item.wait_for(state="visible", timeout=timeout_ms)
                text = norm(item.inner_text(timeout=timeout_ms))
                match = text == expected if exact else expected in text
                if not match:
                    continue

                button = item.locator(
                    "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' v-button ')][1]"
                )
                button.scroll_into_view_if_needed(timeout=5000)
                button.click(timeout=5000)
                return f"span.v-button-caption:{caption}"
            except Exception as exc:
                last_error = exc
    except Exception as exc:
        last_error = exc

    selectors = [
        f"text={caption}",
        f".v-button:has-text('{caption}')",
        f"button:has-text('{caption}')",
        "input[type='submit']",
    ]
    for selector in selectors:
        try:
            locator = context.locator(selector)
            item = first_visible(locator, timeout_ms=timeout_ms)
            if item:
                if exact:
                    try:
                        item_text = norm(item.inner_text(timeout=timeout_ms))
                        if item_text and item_text != expected:
                            continue
                    except Exception:
                        pass
                item.scroll_into_view_if_needed(timeout=5000)
                item.click(timeout=5000)
                return selector
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"No pude hacer clic en {caption}. Último error: {last_error}")


def click_login(context) -> str:
    try:
        return click_button_by_caption(context, "Ingresar >>", exact=True)
    except Exception:
        pass

    try:
        return click_button_by_caption(context, "Ingresar", exact=False)
    except Exception:
        pass

    try:
        pwd = context.locator("input[type='password']").first
        pwd.press("Enter", timeout=5000)
        return "password.Enter"
    except Exception as exc:
        raise RuntimeError(f"No pude hacer clic en Ingresar. Último error: {exc}")


def find_login_frame(page) -> Tuple[Any, str]:
    """Busca el contexto donde están usuario + password."""
    for context, label in all_contexts(page):
        try:
            password_count = context.locator("input[type='password']").count()
            text_count = context.locator(
                "input[type='text'], input.v-textfield:not([type='password']), input[maxlength='30']:not([type='password'])"
            ).count()
            if password_count >= 1 and text_count >= 1:
                return context, label
        except Exception:
            continue
    return page, "page:fallback"


def page_has_login_form(page) -> bool:
    try:
        return page.locator("input[type='password']").count() > 0
    except Exception:
        return False


def iter_browser_pages(page) -> List[Any]:
    """
    Devuelve todas las páginas abiertas en el contexto Playwright.
    Esto es clave para Diagnosticador/Vaadin: a veces el resultado queda
    en una pestaña/vista nueva del mismo contexto y el script seguía leyendo
    solo la página inicial.
    """
    pages: List[Any] = []

    try:
        for p in page.context.pages:
            if p not in pages:
                pages.append(p)
    except Exception:
        pass

    if page not in pages:
        pages.insert(0, page)

    return pages


def vaadin_text(page) -> str:
    """Lee texto visible de todas las páginas/frames del contexto."""
    textos: List[str] = []

    for p in iter_browser_pages(page):
        try:
            textos.append(p.locator("body").inner_text(timeout=1500))
        except Exception:
            pass

        try:
            for frame in p.frames:
                try:
                    textos.append(frame.locator("body").inner_text(timeout=800))
                except Exception:
                    continue
        except Exception:
            pass

    return norm(" ".join(t for t in textos if t))


def detect_login_status(page, initial_url: str) -> Tuple[bool, str, str]:
    """Heurística: la app Vaadin puede no cambiar de URL."""
    final_url = page.url
    text_norm = vaadin_text(page)

    error_patterns = [
        "usuario o contrasena",
        "contrasena incorrecta",
        "password incorrect",
        "login incorrect",
        "credenciales",
        "error de autentic",
        "authentication failed",
    ]
    success_patterns = [
        "cerrar sesion",
        "salir",
        "logout",
        "consulta",
        "diagnostico",
        "residencial",
        "cliente",
        "servicio",
        "ont",
        "gpon",
        "mac",
        "cuenta",
    ]

    if any(p in text_norm for p in error_patterns):
        return False, "LOGIN_RECHAZADO", "La página muestra un mensaje de credenciales/error."

    if final_url != initial_url and not page_has_login_form(page):
        return True, "LOGIN_OK", "La URL cambió y el formulario de login ya no está visible."

    if not page_has_login_form(page) and any(p in text_norm for p in success_patterns):
        return True, "LOGIN_OK", "El formulario desapareció y se detectaron textos internos de la aplicación."

    if final_url != initial_url:
        return True, "LOGIN_PROBABLE", "La URL cambió después de ingresar; valida la captura generada."

    return False, "LOGIN_NO_CONFIRMADO", "No pude confirmar el login automáticamente. Revisa captura y HTML de evidencia."


def wait_soft_network(page, timeout_ms: int = 8000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass


def wait_for_any_text(page, patterns: Iterable[str], timeout_ms: int = 20000) -> bool:
    deadline = time.perf_counter() + (timeout_ms / 1000)
    wanted = [norm(p) for p in patterns]

    while time.perf_counter() < deadline:
        try:
            body = vaadin_text(page)
            if any(p in body for p in wanted):
                return True
        except Exception:
            pass
        page.wait_for_timeout(600)
    return False


def fill_vaadin_field(field, value: str) -> None:
    """
    Vaadin a veces no detecta bien fill() si no hay eventos input/change/blur.
    """
    field.scroll_into_view_if_needed(timeout=5000)
    field.click(timeout=5000)
    field.fill("", timeout=5000)
    field.type(value, delay=60, timeout=15000)

    try:
        field.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.blur();
            }"""
        )
    except Exception:
        pass

    try:
        field.press("Tab", timeout=3000)
    except Exception:
        pass


def read_input_value(field) -> str:
    try:
        return clean_text(field.input_value(timeout=2000))
    except Exception:
        try:
            return clean_text(field.evaluate("el => el.value || ''"))
        except Exception:
            return ""


def mac_query_variants(value: str) -> List[str]:
    raw = clean_text(value).upper()
    hex_only = re.sub(r"[^0-9A-F]", "", raw)
    variants: List[str] = []

    if raw:
        variants.append(raw)
    if len(hex_only) == 12:
        variants.append(":".join(hex_only[i : i + 2] for i in range(0, 12, 2)))
        variants.append(hex_only)

    clean_variants: List[str] = []
    for item in variants:
        if item and item not in clean_variants:
            clean_variants.append(item)
    return clean_variants


def set_input_like_human(field, value: str, delay_ms: int = 35) -> str:
    """
    Escribe como usuario real. No hace TAB ni blur al final porque Vaadin
    puede limpiar el campo MAC/Cuenta al perder foco.
    """
    field.scroll_into_view_if_needed(timeout=5000)
    field.click(timeout=5000)

    try:
        field.press("Control+A", timeout=3000)
        field.press("Backspace", timeout=3000)
    except Exception:
        try:
            field.fill("", timeout=3000)
        except Exception:
            pass

    field.page.wait_for_timeout(250)
    field.type(value, delay=delay_ms, timeout=20000)

    try:
        field.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
            }"""
        )
    except Exception:
        pass

    field.page.wait_for_timeout(1000)
    return read_input_value(field)


def set_input_by_js(field, value: str) -> str:
    """Fallback usando setter nativo sin blur/TAB."""
    field.scroll_into_view_if_needed(timeout=5000)
    field.click(timeout=5000)
    field.evaluate(
        """(el, value) => {
            const proto = Object.getPrototypeOf(el);
            const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');

            if (descriptor && descriptor.set) {
                descriptor.set.call(el, '');
                el.dispatchEvent(new Event('input', { bubbles: true }));
                descriptor.set.call(el, value);
            } else {
                el.value = value;
            }

            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
        }""",
        value,
    )
    field.page.wait_for_timeout(1000)
    return read_input_value(field)


def fill_vaadin_query_field(field, value: str, tipo: str = "mac") -> str:
    """
    Llena el campo MAC/Cuenta sin TAB ni blur.
    Si Vaadin limpia el valor, reintenta y prueba formatos alternos de MAC.
    """
    tipo = norm(tipo)
    if tipo == "mac":
        variants = mac_query_variants(value)
    elif tipo == "cuenta":
        variants = [re.sub(r"\D", "", clean_text(value))]
    else:
        variants = [clean_text(value)]

    variants = [v for v in variants if v]
    if not variants:
        raise RuntimeError("No hay valor válido para escribir en el campo de consulta.")

    last_seen = ""
    for candidate in variants:
        seen = set_input_like_human(field, candidate, delay_ms=35)
        field.page.wait_for_timeout(300)
        seen_after = read_input_value(field)
        last_seen = seen_after or seen
        if seen_after:
            return seen_after

        seen = set_input_by_js(field, candidate)
        field.page.wait_for_timeout(300)
        seen_after = read_input_value(field)
        last_seen = seen_after or seen
        if seen_after:
            return seen_after

    raise RuntimeError(
        "El campo de consulta se limpió después de escribir. "
        f"Último valor visto: '{last_seen}'. Variantes probadas: {variants}"
    )


def find_query_field(page, tipo: str):
    max_len = "17" if tipo == "mac" else "8"
    selectors = [
        f"input[type='text'][maxlength='{max_len}'][style*='14em']",
        f"input.v-textfield[maxlength='{max_len}'][style*='14em']",
        f"input[type='text'][maxlength='{max_len}']",
        f"input.v-textfield[maxlength='{max_len}']",
        f"input[maxlength='{max_len}']",
    ]

    last_error = None
    for context, label in all_contexts(page):
        for selector in selectors:
            try:
                item = first_visible(context.locator(selector), timeout_ms=1200)
                if item:
                    return context, label, item, selector
            except Exception as exc:
                last_error = exc
    raise RuntimeError(f"No encontré campo de consulta para {tipo} maxlength={max_len}. Último error: {last_error}")


def query_field_is_visible(page, tipo: str) -> bool:
    try:
        _ctx, _label, field, _selector = find_query_field(page, tipo)
        return field.is_visible(timeout=800)
    except Exception:
        return False


def find_exact_vaadin_button(context, caption: str, timeout_ms: int = 700):
    """
    Busca botón Vaadin exacto. Evita tomar 'Volver a consultar'
    cuando necesitamos solamente 'Consultar'.
    """
    expected = norm(caption)

    # Primero, buscar el div.v-button directamente.
    for selector in ["div.v-button[role='button']", "div.v-button"]:
        try:
            buttons = context.locator(selector)
            for idx in range(buttons.count()):
                button = buttons.nth(idx)
                try:
                    button.wait_for(state="visible", timeout=timeout_ms)
                    text = norm(button.inner_text(timeout=timeout_ms))
                    if text == expected:
                        return button
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback buscando caption y subiendo al div.v-button.
    try:
        captions = context.locator("span.v-button-caption")
        for idx in range(captions.count()):
            cap = captions.nth(idx)
            try:
                cap.wait_for(state="visible", timeout=timeout_ms)
                text = norm(cap.inner_text(timeout=timeout_ms))
                if text != expected:
                    continue

                button = cap.locator(
                    "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' v-button ')][1]"
                )
                button.wait_for(state="visible", timeout=timeout_ms)
                return button
            except Exception:
                continue
    except Exception:
        pass

    return None


def click_locator_like_user(locator) -> None:
    """
    Clic robusto para Vaadin.
    Primero intenta clic normal; si Vaadin se pone raro, usa force y eventos de mouse.
    """
    locator.scroll_into_view_if_needed(timeout=5000)

    try:
        locator.click(timeout=7000)
        return
    except Exception:
        pass

    try:
        locator.click(timeout=7000, force=True)
        return
    except Exception:
        pass

    locator.evaluate(
        """el => {
            el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, view: window }));
            el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
            el.click();
        }"""
    )


def click_exact_consultar(page) -> str:
    errors: List[str] = []

    for context, label in all_contexts(page):
        try:
            button = find_exact_vaadin_button(context, "Consultar", timeout_ms=1000)
            if not button:
                continue

            click_locator_like_user(button)
            return f"{label}:Consultar"

        except Exception as exc:
            errors.append(f"{label}: {exc}")

    raise RuntimeError("No pude hacer clic exacto en Consultar. " + " | ".join(errors[-5:]))


def click_consultar_anywhere(page) -> str:
    return click_exact_consultar(page)


def has_exact_consultar_button(page) -> bool:
    for context, _label in all_contexts(page):
        try:
            button = find_exact_vaadin_button(context, "Consultar", timeout_ms=500)
            if button:
                return True
        except Exception:
            continue
    return False


def _html_has_diagnosticador_panels(html: str) -> bool:
    txt = norm(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True))
    return any(
        item in txt
        for item in [
            "informacion basica",
            "calidad tiempo real",
            "direccionamiento ip servicios",
            "diagnostico",
            "historico de niveles",
        ]
    )


def get_best_diagnosticador_page(page):
    """
    Escoge la página que parece tener el resultado final.
    Si Diagnosticador abre una pestaña/vista adicional, no nos quedamos
    leyendo la página inicial por error.
    """
    best_page = page
    best_score = -1

    for p in iter_browser_pages(page):
        try:
            html_parts = [p.content()]
            for frame in p.frames:
                try:
                    html_parts.append(frame.content())
                except Exception:
                    pass
            html = "\n".join(html_parts)
            score = diagnosticador_result_score(html)
            # desempate por longitud, pero el score manda
            score_weighted = (score * 1_000_000) + len(html)
            if score_weighted > best_score:
                best_score = score_weighted
                best_page = p
        except Exception:
            continue

    return best_page


def get_diagnosticador_html(page) -> str:
    """
    Obtiene el HTML real del Diagnosticador.
    Vaadin 6 renderiza todo en document.body.
    No tomamos simplemente el HTML más largo, porque a veces el más largo no es el DOM visible correcto.
    """
    candidates: List[str] = []

    # 1) Página principal visible
    try:
        body_html = page.evaluate("() => document.body ? document.body.outerHTML : document.documentElement.outerHTML")
        if body_html:
            candidates.append(body_html)
    except Exception:
        pass

    # 2) page.content normal
    try:
        candidates.append(page.content())
    except Exception:
        pass

    # 3) Frames
    for frame in page.frames:
        try:
            frame_html = frame.evaluate("() => document.body ? document.body.outerHTML : document.documentElement.outerHTML")
            if frame_html:
                candidates.append(frame_html)
        except Exception:
            try:
                candidates.append(frame.content())
            except Exception:
                pass

    if not candidates:
        return ""

    # Preferimos el HTML que tenga los paneles reales, no necesariamente el más largo.
    scored: List[Tuple[int, int, str]] = []

    for html in candidates:
        score = diagnosticador_result_score(html)
        scored.append((score, len(html), html))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2]

def save_evidence(page, prefix: str) -> Tuple[str, str]:
    """Compatibilidad del contrato: la API no persiste evidencias."""
    return "", ""


def perform_login(page, url: str, username: str, password: str, timeout_ms: int) -> Tuple[bool, str, str, Dict[str, str], str, str]:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    wait_soft_network(page, timeout_ms=8000)

    initial_url = page.url
    context, context_label = find_login_frame(page)

    user_selector = fill_by_selectors(
        context,
        [
            "input[type='text'][maxlength='30']",
            "input.v-textfield[type='text']",
            "input[maxlength='30']:not([type='password'])",
            "input.v-textfield:not([type='password'])",
            "input[type='text']",
        ],
        username,
        "usuario",
    )
    pwd_selector = fill_by_selectors(
        context,
        [
            "input[type='password'][maxlength='30']",
            "input.v-textfield[type='password']",
            "input[type='password']",
        ],
        password,
        "contraseña",
    )
    click_selector = click_login(context)

    wait_soft_network(page, timeout_ms=12000)
    page.wait_for_timeout(2500)
    final_url = page.url
    ok, estado, mensaje = detect_login_status(page, initial_url)
    selectors = {"user": user_selector, "password": pwd_selector, "login": click_selector, "context": context_label}
    return ok, estado, mensaje, selectors, initial_url, final_url


# ---------------------------------------------------------------------
# Extracción de HTML Vaadin Diagnosticador
# ---------------------------------------------------------------------
def has_exact_class(node, class_name: str) -> bool:
    """
    Valida clase exacta.
    Importante: 'v-panel' NO debe coincidir con 'v-panel-caption'.
    """
    if node is None:
        return False

    classes = node.get("class") or []

    if isinstance(classes, str):
        classes = classes.split()

    return class_name in classes


def find_panel(soup: BeautifulSoup, caption_pattern: str):
    """
    Ubica el panel Vaadin real por caption.

    Error corregido:
    No usar 'if "v-panel" in class_text', porque eso devuelve
    el div .v-panel-caption en vez del contenedor .v-panel.
    """
    regex = re.compile(caption_pattern, re.I)

    # Camino principal: caption -> subir hasta el div con clase exacta v-panel.
    for caption in soup.select(".v-panel-caption"):
        caption_text = clean_text(caption.get_text(" ", strip=True))

        if not regex.search(caption_text):
            continue

        parent = caption.parent

        while parent is not None:
            if getattr(parent, "name", None) and has_exact_class(parent, "v-panel"):
                return parent

            parent = parent.parent

    # Fallback: recorrer directamente paneles Vaadin.
    for panel in soup.select("div.v-panel"):
        cap = panel.select_one(".v-panel-caption")

        if not cap:
            continue

        caption_text = clean_text(cap.get_text(" ", strip=True))

        if regex.search(caption_text):
            return panel

    # Fallback final: buscar por texto y subir hasta v-panel exacto.
    for node in soup.find_all(True):
        node_text = clean_text(node.get_text(" ", strip=True))

        if not node_text:
            continue

        if not regex.search(node_text[:180]):
            continue

        parent = node.parent

        while parent is not None:
            if getattr(parent, "name", None) and has_exact_class(parent, "v-panel"):
                return parent

            parent = parent.parent

    return None

def normalize_key(key: str) -> str:
    key = norm(key).strip(" :")
    mapping = {
        "marca": "marca",
        "modelo": "modelo",
        "up time": "uptime",
        "uptime": "uptime",
        "firmware": "firmware",
        "firmware": "firmware",
        "serial": "serial",
        "mac": "mac",
        "olt": "olt",
        "nodo": "nodo",
        "puerto tarjeta": "puerto_tarjeta",
        "nombre tarjeta": "nombre_tarjeta",
        "puerto pon": "puerto_pon",
        "estado ante el acs": "estado_acs",
        "cuenta matriz": "cuenta_matriz",
        "cuenta": "cuenta_matriz",
        "edificio": "edificio",
        # PATCH_DIAGNOSTICADOR_DIRECCION_V1
        "direccion": "direccion",
        "direcci??n": "direccion",
        "direccion cliente": "direccion",
        "direcci??n cliente": "direccion",
        "direccion instalacion": "direccion",
        "direcci??n instalaci??n": "direccion",
    }
    return mapping.get(key, re.sub(r"[^a-z0-9]+", "_", key).strip("_"))


def extract_basic_info(soup: BeautifulSoup) -> Dict[str, str]:
    panel = find_panel(soup, r"Informaci[oó]n\s+B[aá]sica")
    if not panel:
        return {}

    data: Dict[str, str] = {}
    for bold in panel.find_all("b"):
        label_raw = clean_text(bold.get_text(" ", strip=True)).rstrip(":")
        key = normalize_key(label_raw)
        parent = bold.parent
        if not parent:
            continue

        parent_text = clean_text(parent.get_text(" ", strip=True))
        label_text = clean_text(bold.get_text(" ", strip=True))
        value = parent_text.replace(label_text, "", 1).strip(" :\t")
        if key and value:
            data[key] = value
    return data


def extract_vaadin_table(panel) -> List[Dict[str, str]]:
    if not panel:
        return []

    headers = [clean_text(x.get_text(" ", strip=True)) for x in panel.select(".v-table-header-cell .v-table-caption-container")]
    headers = [h for h in headers if h]

    rows: List[Dict[str, str]] = []
    for tr in panel.select(".v-table-table tr"):
        cells = [clean_text(x.get_text(" ", strip=True)) for x in tr.select(".v-table-cell-wrapper")]
        if not cells:
            cells = [clean_text(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
        if not cells:
            continue

        row: Dict[str, str] = {}
        for idx, cell in enumerate(cells):
            key = headers[idx] if idx < len(headers) and headers[idx] else f"col_{idx + 1}"
            row[key] = cell
        rows.append(row)
    return rows


def extract_calidad_tiempo_real(soup: BeautifulSoup) -> List[Dict[str, str]]:
    return extract_vaadin_table(find_panel(soup, r"Calidad\s+Tiempo\s+Real"))


def extract_vaadin_table_by_headers(
    soup: BeautifulSoup,
    required_headers: Iterable[str],
) -> List[Dict[str, str]]:
    """
    Busca una tabla Vaadin por encabezados, sin depender del panel.
    Esto corrige el caso de Direccionamiento Ip Servicios cuando el panel
    existe, pero el contenedor detectado por find_panel no alcanza a tomar
    correctamente la tabla interna.
    """
    required = {norm(h) for h in required_headers}

    for table_root in soup.select("div.v-table"):
        headers = [
            clean_text(x.get_text(" ", strip=True))
            for x in table_root.select(".v-table-header-cell .v-table-caption-container")
        ]
        headers = [h for h in headers if h]

        header_norms = {norm(h) for h in headers}

        if not required.issubset(header_norms):
            continue

        rows: List[Dict[str, str]] = []

        for tr in table_root.select(".v-table-table tr"):
            cells = [
                clean_text(x.get_text(" ", strip=True))
                for x in tr.select(".v-table-cell-wrapper")
            ]

            if not cells:
                cells = [
                    clean_text(x.get_text(" ", strip=True))
                    for x in tr.find_all(["td", "th"])
                ]

            if not cells:
                continue

            row: Dict[str, str] = {}

            for idx, cell in enumerate(cells):
                key = headers[idx] if idx < len(headers) else f"col_{idx + 1}"
                row[key] = cell

            # Evitar filas basura vacías
            if any(clean_text(v) for v in row.values()):
                rows.append(row)

        if rows:
            return rows

    return []


def extract_servicios_ip(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """
    Extrae la tabla Direccionamiento Ip Servicios.
    Primero intenta por panel; si no encuentra filas, busca globalmente
    por encabezados Servicio / Estado / Dirección Ip / DNS Server.
    """
    panel = find_panel(
        soup,
        r"Direccionamiento\s+Ip\s+Servicios|Direccionamiento\s+IP\s+Servicios",
    )

    rows = extract_vaadin_table(panel)

    if rows:
        return rows

    return extract_vaadin_table_by_headers(
        soup,
        ["Servicio", "Estado", "Dirección Ip", "DNS Server"],
    )

def extract_historico_niveles(soup: BeautifulSoup) -> Tuple[List[Dict[str, str]], List[str]]:
    panel = find_panel(soup, r"Hist[oó]rico\s+de\s+Niveles")
    rows = extract_vaadin_table(panel)
    images: List[str] = []
    if panel:
        images = [img.get("src", "") for img in panel.find_all("img") if img.get("src")]
    return rows, images


def extract_diagnostico(soup: BeautifulSoup) -> str:
    panel = find_panel(soup, r"Diagn[oó]stico")
    if not panel:
        return ""

    candidates: List[str] = []

    for label in panel.select(".v-label"):
        text = clean_text(label.get_text(" ", strip=True))
        if not text:
            continue

        text_norm = norm(text)

        if "consultando la informacion" in text_norm:
            continue

        if "volver a diagnosticar" in text_norm:
            continue

        if "incidente / rfc" in text_norm:
            continue

        if text_norm in {"diagnostico"}:
            continue

        # Diagnóstico real normalmente viene como texto largo.
        if len(text) >= 20:
            candidates.append(text)

    if candidates:
        return max(candidates, key=len)

    text = clean_text(panel.get_text(" ", strip=True))
    text_norm = norm(text)

    if "consultando la informacion" in text_norm:
        return ""

    text = re.sub(r"^Diagn[oó]stico\s*", "", text, flags=re.I)
    text = re.sub(r"Volver a Diagnosticar.*$", "", text, flags=re.I)
    text = re.sub(r"Incidente\s*/\s*RFC\s+del\s+Nodo.*$", "", text, flags=re.I)

    text = clean_text(text)

    if norm(text) in {"diagnostico", "consultando la informacion"}:
        return ""

    return text

def extract_diagnosticador_data_from_html(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    basica = extract_basic_info(soup)
    calidad = extract_calidad_tiempo_real(soup)
    servicios = extract_servicios_ip(soup)
    historico, historico_imagenes = extract_historico_niveles(soup)
    diagnostico = extract_diagnostico(soup)

    return {
        "informacion_basica": basica,
        "calidad_tiempo_real": calidad,
        "servicios_ip": servicios,
        "diagnostico": diagnostico,
        "historico_niveles": historico,
        "historico_imagenes": historico_imagenes,
    }


def get_first_row_value(rows: List[Dict[str, str]], col_candidates: Iterable[str], row_match: Optional[str] = None) -> str:
    wanted_cols = [norm(c) for c in col_candidates]
    wanted_row = norm(row_match) if row_match else None

    for row in rows:
        if wanted_row:
            joined = norm(" ".join(row.values()))
            first_val = norm(next(iter(row.values()), ""))
            if wanted_row not in joined and first_val != wanted_row:
                continue

        for key, val in row.items():
            if norm(key) in wanted_cols:
                return clean_text(val)
    return ""


def get_table_value_by_tipo(rows: List[Dict[str, str]], tipo_name: str) -> str:
    """Obtiene Valor para filas tipo TX/RX en Calidad Tiempo Real."""
    wanted = norm(tipo_name)
    for row in rows:
        values = list(row.values())
        if not values:
            continue

        tipo = get_first_row_value([row], ["Tipo", "col_1"]) or values[0]
        if norm(tipo) != wanted:
            continue

        valor = get_first_row_value([row], ["Valor", "col_2"])
        if not valor and len(values) >= 2:
            valor = values[1]
        return clean_text(valor)
    return ""


def diagnosticador_result_score(html: str) -> int:
    txt = norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    checks = [
        "informacion basica",
        "calidad tiempo real",
        "direccionamiento ip servicios",
        "diagnostico",
        "historico de niveles",
    ]
    return sum(1 for item in checks if item in txt)


def html_contains_full_diagnosticador_result(html: str) -> bool:
    txt = norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    tiene_basica = "informacion basica" in txt
    tiene_algo_tecnico = any(
        item in txt
        for item in [
            "calidad tiempo real",
            "direccionamiento ip servicios",
            "diagnostico",
            "historico de niveles",
        ]
    )
    return tiene_basica and tiene_algo_tecnico


def result_is_parseable(html: str) -> bool:
    datos = extract_diagnosticador_data_from_html(html)
    basica = bool(datos.get("informacion_basica"))
    calidad = bool(datos.get("calidad_tiempo_real"))
    servicios = bool(datos.get("servicios_ip"))
    diagnostico = bool(datos.get("diagnostico"))
    return basica and (calidad or servicios or diagnostico)


def datos_ready_for_template(datos: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    No devuelve OK apenas cargue Información Básica.
    Espera los bloques que usa la plantilla:
      - Básica mínima
      - TX/RX
      - Servicios IP
      - Diagnóstico
    """
    faltantes: List[str] = []

    basica = datos.get("informacion_basica") or {}
    calidad = datos.get("calidad_tiempo_real") or []
    servicios = datos.get("servicios_ip") or []
    diagnostico = clean_text(datos.get("diagnostico") or "")

    for campo in ["marca", "modelo", "serial", "mac", "olt", "nodo", "estado_acs"]:
        if not clean_text(basica.get(campo, "")):
            faltantes.append(f"basica.{campo}")

    tx = get_table_value_by_tipo(calidad, "TX")
    rx = get_table_value_by_tipo(calidad, "RX")

    if not tx:
        faltantes.append("calidad.TX")
    if not rx:
        faltantes.append("calidad.RX")

    if len(servicios) < 3:
        faltantes.append("servicios_ip")

    diagnostico_norm = norm(diagnostico)

    if (
        not diagnostico
        or "consultando la informacion" in diagnostico_norm
        or "incidente / rfc" in diagnostico_norm
    ):
        faltantes.append("diagnostico")

    return len(faltantes) == 0, faltantes


def nudge_vaadin_loading(page, iteration: int) -> None:
    """Pequeño scroll para forzar render/lazy load de paneles en Vaadin."""
    if iteration % 4 != 0:
        return

    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(400)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    try:
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(300)
        page.mouse.wheel(0, -900)
    except Exception:
        pass


def wait_after_first_consultar(page, consulta_tipo: str, timeout_ms: int = 70000) -> str:
    """
    Después del primer Consultar esperamos cambio de pantalla.
    No damos por listo el resultado solo por ver Información Básica.
    """
    page.wait_for_timeout(1000)
    deadline = time.perf_counter() + (timeout_ms / 1000)

    while time.perf_counter() < deadline:
        html = get_diagnosticador_html(page)
        datos = extract_diagnosticador_data_from_html(html)
        ready, _faltantes = datos_ready_for_template(datos)
        score = diagnosticador_result_score(html)

        if ready:
            return "TEMPLATE_READY"

        if score >= 2:
            return f"RESULTADO_SCORE_{score}"

        if not query_field_is_visible(page, consulta_tipo):
            return "PANTALLA_CAMBIO"

        txt = vaadin_text(page)
        if "no se encontro" in txt or "no se encontró" in txt:
            return "NO_ENCONTRADO"
        if "sin datos" in txt:
            return "SIN_DATOS"

        page.wait_for_timeout(1000)

    return "TIMEOUT_PRIMER_CONSULTAR"



# ATLAS_VECINOS_IMMEDIATE_AFTER_FIRST_CONSULTAR_V1
def wait_and_click_vecinos_priority(
    page,
    consulta_tipo: str,
    timeout_ms: int = 150000,
) -> dict:
    """
    Flujo orientado al DOM, no a tiempos fijos.

    Desde el PRIMER Consultar:
      - vigilar Vecinos inmediatamente;
      - si aparece, click inmediato;
      - segundo Consultar solamente si aparece;
      - seguir vigilando Vecinos;
      - no esperar networkidle;
      - no esperar carga completa de la pagina.

    timeout_ms es solo fail-safe.
    """

    deadline = (
        time.perf_counter()
        + timeout_ms / 1000
    )

    started = time.perf_counter()

    segundo_consultar_clicked = False
    informacion_incidente_seen = False
    pantalla_cambio_seen = False

    last_state = ""

    def emit(state: str) -> None:
        nonlocal last_state

        if state == last_state:
            return

        last_state = state

        print(
            "[VECINOS][IMMEDIATE] "
            f"t={time.perf_counter()-started:.2f}s "
            f"estado={state}",
            file=sys.stderr,
            flush=True,
        )

    while time.perf_counter() < deadline:

        # ======================================================================================
        # 1. VECINOS TIENE PRIORIDAD ABSOLUTA
        # ======================================================================================

        for context, label in iter_vecinos_contexts(
            page
        ):
            try:
                button = find_exact_vaadin_button(
                    context,
                    "Vecinos",
                    timeout_ms=250,
                )

                if button:
                    emit(
                        "VECINOS_VISIBLE"
                    )

                    click_locator_like_user(
                        button
                    )

                    elapsed = round(
                        time.perf_counter()
                        - started,
                        3,
                    )

                    print(
                        "[VECINOS][IMMEDIATE] "
                        f"CLICK_VECINOS "
                        f"t={elapsed}s "
                        f"contexto={label}",
                        file=sys.stderr,
                        flush=True,
                    )

                    return {
                        "estado":
                            "VECINOS_CLICKED",

                        "vecinos_clicked":
                            True,

                        "vecinos_source":
                            f"{label}:Vecinos",

                        "segundo_consultar_clicked":
                            segundo_consultar_clicked,

                        "informacion_incidente_seen":
                            informacion_incidente_seen,

                        "pantalla_cambio_seen":
                            pantalla_cambio_seen,

                        "elapsed_sec":
                            elapsed,
                    }

            except Exception:
                pass

        # ======================================================================================
        # 2. LEER ESTADO ACTUAL
        # ======================================================================================

        # ATLAS_DIAGNOSTICADOR_NO_DISPONIBLE_FAST_V1
        # El Diagnosticador publica un toast terminal cuando la cuenta/equipo
        # no tiene históricos recientes o la ONT no está disponible en ACS.
        # En ese escenario no tiene sentido esperar Vecinos hasta timeout.
        try:
            _atlas_no_disp_text = fold_vecinos_text(
                vaadin_text(page)
            )
        except Exception:
            _atlas_no_disp_text = ""

        _atlas_no_disp = (
            "no se puede consultar el cablemodem o la ont"
            in _atlas_no_disp_text
            and (
                "no tiene registros historicos"
                in _atlas_no_disp_text
                or (
                    "ont no se encuentra"
                    in _atlas_no_disp_text
                    and "acs" in _atlas_no_disp_text
                )
            )
        )

        if _atlas_no_disp:
            elapsed = round(
                time.perf_counter() - started,
                2,
            )

            print(
                "[VECINOS][IMMEDIATE] "
                "DIAGNOSTICADOR_NO_DISPONIBLE "
                f"t={elapsed}s",
                file=sys.stderr,
                flush=True,
            )

            return {
                "estado": "DIAGNOSTICADOR_NO_DISPONIBLE",
                "vecinos_clicked": False,
                "vecinos_source": "",
                "segundo_consultar_clicked": (
                    segundo_consultar_clicked
                ),
                "mensaje": (
                    "No se puede consultar el cablemodem o la ONT. "
                    "El cablemodem no tiene registros históricos en los últimos "
                    "siete(7) días o la ONT no se encuentra en disponible en el ACS."
                ),
            }

        try:
            text_now = fold_vecinos_text(
                vaadin_text(page)
            )
        except Exception:
            text_now = ""

        if (
            "informacion incidente"
            in text_now
        ):
            informacion_incidente_seen = True

            emit(
                "INFORMACION_INCIDENTE_VISIBLE"
            )

        try:
            query_visible = (
                query_field_is_visible(
                    page,
                    consulta_tipo,
                )
            )
        except Exception:
            query_visible = True

        if not query_visible:
            pantalla_cambio_seen = True

            if not informacion_incidente_seen:
                emit(
                    "PANTALLA_CAMBIO"
                )

        # ======================================================================================
        # 3. SEGUNDO CONSULTAR OPCIONAL
        # ======================================================================================

        if (
            not segundo_consultar_clicked
            and (
                informacion_incidente_seen
                or pantalla_cambio_seen
            )
        ):
            for context, label in all_contexts(
                page
            ):
                try:
                    button = find_exact_vaadin_button(
                        context,
                        "Consultar",
                        timeout_ms=250,
                    )

                    if not button:
                        continue

                    emit(
                        "SEGUNDO_CONSULTAR_VISIBLE"
                    )

                    click_locator_like_user(
                        button
                    )

                    segundo_consultar_clicked = True

                    elapsed = round(
                        time.perf_counter()
                        - started,
                        3,
                    )

                    print(
                        "[VECINOS][IMMEDIATE] "
                        "CLICK_SEGUNDO_CONSULTAR "
                        f"t={elapsed}s "
                        f"contexto={label}",
                        file=sys.stderr,
                        flush=True,
                    )

                    # No sleep.
                    # No networkidle.
                    # Próxima vuelta vuelve primero a Vecinos.
                    break

                except Exception:
                    pass

        # ======================================================================================
        # 4. ERRORES FUNCIONALES REALES
        # ======================================================================================

        if (
            "no se encontro"
            in text_now
            or "no se encontró"
            in text_now
        ):
            return {
                "estado":
                    "NO_ENCONTRADO",

                "vecinos_clicked":
                    False,

                "vecinos_source":
                    "",

                "segundo_consultar_clicked":
                    segundo_consultar_clicked,

                "informacion_incidente_seen":
                    informacion_incidente_seen,

                "pantalla_cambio_seen":
                    pantalla_cambio_seen,

                "elapsed_sec":
                    round(
                        time.perf_counter()
                        - started,
                        3,
                    ),
            }

        if "sin datos" in text_now:
            return {
                "estado":
                    "SIN_DATOS",

                "vecinos_clicked":
                    False,

                "vecinos_source":
                    "",

                "segundo_consultar_clicked":
                    segundo_consultar_clicked,

                "informacion_incidente_seen":
                    informacion_incidente_seen,

                "pantalla_cambio_seen":
                    pantalla_cambio_seen,

                "elapsed_sec":
                    round(
                        time.perf_counter()
                        - started,
                        3,
                    ),
            }

        # Solo yield para no quemar CPU.
        page.wait_for_timeout(
            250
        )

    return {
        "estado":
            "TIMEOUT_VECINOS_PRIORITY",

        "vecinos_clicked":
            False,

        "vecinos_source":
            "",

        "segundo_consultar_clicked":
            segundo_consultar_clicked,

        "informacion_incidente_seen":
            informacion_incidente_seen,

        "pantalla_cambio_seen":
            pantalla_cambio_seen,

        "elapsed_sec":
            round(
                time.perf_counter()
                - started,
                3,
            ),
    }


def wait_until_second_consultar_or_result(page, timeout_ms: int = 70000) -> str:
    """
    Espera la pantalla intermedia: puede aparecer el segundo Consultar
    o puede cargar directo el resultado completo.
    """
    deadline = time.perf_counter() + (timeout_ms / 1000)

    while time.perf_counter() < deadline:
        html = get_diagnosticador_html(page)
        datos = extract_diagnosticador_data_from_html(html)
        ready, _faltantes = datos_ready_for_template(datos)

        if ready:
            return "TEMPLATE_READY"

        for context, _label in all_contexts(page):
            try:
                button = find_exact_vaadin_button(context, "Consultar", timeout_ms=500)
                if button:
                    return "SEGUNDO_CONSULTAR_VISIBLE"
            except Exception:
                continue

        txt = vaadin_text(page)
        if "no se encontro" in txt or "no se encontró" in txt:
            return "NO_ENCONTRADO"
        if "sin datos" in txt:
            return "SIN_DATOS"

        page.wait_for_timeout(1000)

    return "TIMEOUT_SEGUNDO_CONSULTAR"


def wait_until_template_ready(page, timeout_ms: int = 180000) -> Tuple[bool, Dict[str, Any], List[str], int]:
    """
    Espera hasta que la plantilla esté completa.
    IMPORTANTE:
    En esta versión, si detectamos Información Básica + TX/RX + Servicios + Diagnóstico,
    salimos de inmediato. No seguimos esperando innecesariamente.
    """
    deadline = time.perf_counter() + (timeout_ms / 1000)

    best_datos: Dict[str, Any] = {}
    best_faltantes: List[str] = []
    best_score = 0
    iteration = 0

    while time.perf_counter() < deadline:
        iteration += 1

        html = get_diagnosticador_html(page)
        datos = extract_diagnosticador_data_from_html(html)
        ready, faltantes = datos_ready_for_template(datos)
        score = diagnosticador_result_score(html)

        if not best_datos or score > best_score or len(faltantes) < len(best_faltantes):
            best_datos = datos
            best_faltantes = faltantes
            best_score = score

        selectors_debug = {
            "score": score,
            "faltantes": faltantes,
            "basica": bool(datos.get("informacion_basica")),
            "calidad": len(datos.get("calidad_tiempo_real") or []),
            "servicios": len(datos.get("servicios_ip") or []),
            "diagnostico": bool(datos.get("diagnostico")),
        }

        print(
            "DEBUG_TEMPLATE_READY:",
            json.dumps(selectors_debug, ensure_ascii=False),
            file=sys.stderr,
        )

        # Si ya está listo, salir de una vez.
        if ready:
            return True, datos, faltantes, score

        txt = vaadin_text(page)

        if "no se encontro" in txt or "no se encontró" in txt or "sin datos" in txt:
            return False, datos, faltantes, score

        # Si visualmente ya aparece el diagnóstico y mínimo 3 paneles,
        # hacemos un último parseo y salimos para evitar quedar pegados

        nudge_vaadin_loading(page, iteration)
        page.wait_for_timeout(500)

    return False, best_datos, best_faltantes, best_score

def wait_for_consultar_or_result(page, timeout_ms: int = 20000) -> str:
    """
    Espera hasta que pase una de estas cosas:
    - aparece resultado completo listo para plantilla
    - aparece botón exacto Consultar para el siguiente paso
    - se agota el tiempo
    """
    deadline = time.perf_counter() + (timeout_ms / 1000)

    while time.perf_counter() < deadline:
        try:
            html = get_diagnosticador_html(page)
            datos = extract_diagnosticador_data_from_html(html)
            ready, _faltantes = datos_ready_for_template(datos)

            if ready:
                return "TEMPLATE_READY"

            if has_exact_consultar_button(page):
                return "CONSULTAR_VISIBLE"

            body = vaadin_text(page)
            if "no se encontro" in body or "no se encontró" in body or "sin datos" in body:
                return "SIN_DATOS"

        except Exception:
            pass

        page.wait_for_timeout(700)

    return "TIMEOUT"


# ---------------------------------------------------------------------
# Vecinos: apertura, espera y extraccion de tabla Vaadin
# ---------------------------------------------------------------------
def fold_vecinos_text(value: Any) -> str:
    value = clean_text(value).lower()

    replacements = {
        "\u00e1": "a",
        "\u00e9": "e",
        "\u00ed": "i",
        "\u00f3": "o",
        "\u00fa": "u",
        "\u00f1": "n",
        "\u00c3\u00a1": "a",
        "\u00c3\u00a9": "e",
        "\u00c3\u00ad": "i",
        "\u00c3\u00b3": "o",
        "\u00c3\u00ba": "u",
        "\u00c3\u00b1": "n",
    }

    for source, target in replacements.items():
        value = value.replace(source, target)

    return value


def vecino_header_key(value: Any) -> str:
    value = fold_vecinos_text(value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def iter_vecinos_contexts(page):
    for current_page in iter_browser_pages(page):
        for context, label in all_contexts(current_page):
            yield (
                context,
                f"{current_page.url}|{label}",
            )


def click_vecinos_anywhere(
    page,
    timeout_ms: int = 60000,
) -> str:
    deadline = time.perf_counter() + timeout_ms / 1000
    errors: List[str] = []

    while time.perf_counter() < deadline:
        for context, label in iter_vecinos_contexts(page):
            try:
                button = find_exact_vaadin_button(
                    context,
                    "Vecinos",
                    timeout_ms=800,
                )

                if not button:
                    continue

                click_locator_like_user(button)

                return f"{label}:Vecinos"

            except Exception as exc:
                errors.append(f"{label}: {exc}")

        page.wait_for_timeout(600)

    raise RuntimeError(
        "No aparecio el boton Vecinos despues de cargar "
        "el diagnostico. "
        + " | ".join(errors[-5:])
    )


def extract_vecinos_from_html(
    html: str,
) -> Dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")

    table_found = False
    vecinos: List[Dict[str, str]] = []

    for table_root in soup.select("div.v-table"):
        headers = [
            clean_text(element.get_text(" ", strip=True))
            for element in table_root.select(
                ".v-table-header .v-table-caption-container"
            )
        ]

        header_keys = [
            vecino_header_key(header)
            for header in headers
        ]

        required = {
            "mac",
            "c rr",
            "direccion",
        }

        if not required.issubset(set(header_keys)):
            continue

        table_found = True

        mac_index = header_keys.index("mac")
        cuenta_rr_index = header_keys.index("c rr")
        direccion_index = header_keys.index("direccion")

        for row_element in table_root.select(
            ".v-table-table tr"
        ):
            cells = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in row_element.select(
                    ".v-table-cell-wrapper"
                )
            ]

            required_index = max(
                mac_index,
                cuenta_rr_index,
                direccion_index,
            )

            if len(cells) <= required_index:
                continue

            mac = normalize_mac(cells[mac_index])
            cuenta_rr = cells[cuenta_rr_index]
            direccion = cells[direccion_index]

            if not (mac or cuenta_rr or direccion):
                continue

            vecinos.append(
                {
                    "mac": mac,
                    "cuenta_rr": cuenta_rr,
                    "direccion": direccion,
                }
            )

        break

    page_text = clean_text(
        soup.get_text(" ", strip=True)
    )

    folded_text = fold_vecinos_text(page_text)

    total_match = re.search(
        r"se\s+han\s+consultado\s+(\d+)\s+vecinos",
        folded_text,
        flags=re.I,
    )

    total_reportado = (
        int(total_match.group(1))
        if total_match
        else 0
    )

    aviso_match = re.search(
        r"mas\s+del\s+(\d+(?:[.,]\d+)?)\s*%\s+"
        r"de\s+los\s+vecinos\s+estan\s+desenganchados",
        folded_text,
        flags=re.I,
    )

    aviso = ""

    if aviso_match:
        porcentaje = aviso_match.group(1)

        aviso = (
            f"M\u00e1s del {porcentaje}% de los vecinos "
            f"est\u00e1n desenganchados"
        )

    unique: Dict[
        Tuple[str, str, str],
        Dict[str, str],
    ] = {}

    for vecino in vecinos:
        key = (
            vecino["mac"],
            vecino["cuenta_rr"],
            vecino["direccion"],
        )

        unique.setdefault(key, vecino)

    vecinos = list(unique.values())

    lines = [
        "MAC\t//\tC. RR\t//\tDirecci\u00f3n"
    ]

    lines.extend(
        (
            f"{row['mac']}\t//\t"
            f"{row['cuenta_rr']}\t//\t"
            f"{row['direccion']}"
        )
        for row in vecinos
    )

    return {
        "tabla_detectada": table_found,
        "total_reportado": total_reportado,
        "total_vecinos": (
            total_reportado or len(vecinos)
        ),
        "aviso_vecinos": aviso,
        "vecinos": vecinos,
        "mensaje_vecinos": "\n".join(lines),
    }


def read_vecinos_candidates(
    page,
) -> List[Tuple[Dict[str, Any], str]]:
    candidates: List[
        Tuple[Dict[str, Any], str]
    ] = []

    for context, label in iter_vecinos_contexts(page):
        try:
            html = context.evaluate(
                "() => document.body "
                "? document.body.outerHTML "
                ": document.documentElement.outerHTML"
            )

            parsed = extract_vecinos_from_html(html)

            if parsed["tabla_detectada"]:
                candidates.append(
                    (parsed, label)
                )

        except Exception:
            continue

    candidates.sort(
        key=lambda item: len(
            item[0].get("vecinos") or []
        ),
        reverse=True,
    )

    return candidates


def scroll_vecinos_tables(page) -> None:
    for context, _label in iter_vecinos_contexts(page):
        try:
            wrappers = context.locator(
                "div.v-table-body-wrapper"
            )

            for index in range(wrappers.count()):
                wrapper = wrappers.nth(index)

                wrapper.evaluate(
                    """element => {
                        const step = Math.max(
                            element.clientHeight * 0.80,
                            200
                        );

                        element.scrollTop = Math.min(
                            element.scrollHeight,
                            element.scrollTop + step
                        );
                    }"""
                )

        except Exception:
            continue


def build_vecinos_message(
    vecinos: List[Dict[str, str]],
) -> str:
    lines = [
        "MAC\t//\tC. RR\t//\tDirecci\u00f3n"
    ]

    lines.extend(
        (
            f"{row.get('mac', '')}\t//\t"
            f"{row.get('cuenta_rr', '')}\t//\t"
            f"{row.get('direccion', '')}"
        )
        for row in vecinos
    )

    return "\n".join(lines)


def _consultar_vecinos_lectura_inicial(
    page,
    timeout_ms: int = 120000,
) -> Dict[str, Any]:
    button_source = click_vecinos_anywhere(
        page,
        timeout_ms=60000,
    )

    wait_soft_network(
        page,
        timeout_ms=15000,
    )

    deadline = (
        time.perf_counter()
        + timeout_ms / 1000
    )

    collected: Dict[
        Tuple[str, str, str],
        Dict[str, str],
    ] = {}

    best_metadata: Dict[str, Any] = {}
    best_context = ""
    previous_count = -1
    stable_cycles = 0

    while time.perf_counter() < deadline:
        candidates = read_vecinos_candidates(page)

        if candidates:
            current, current_context = candidates[0]

            best_metadata = current
            best_context = current_context

            for row in current.get("vecinos") or []:
                key = (
                    row.get("mac", ""),
                    row.get("cuenta_rr", ""),
                    row.get("direccion", ""),
                )

                collected.setdefault(key, row)

            total_reportado = int(
                current.get("total_reportado") or 0
            )

            current_count = len(collected)

            if current_count == previous_count:
                stable_cycles += 1
            else:
                stable_cycles = 0
                previous_count = current_count

            carga_completa = (
                current_count > 0
                and (
                    (
                        total_reportado > 0
                        and current_count >= total_reportado
                    )
                    or stable_cycles >= 4
                )
            )

            if carga_completa:
                vecinos = list(collected.values())

                return {
                    "ok": True,
                    "estado": "VECINOS_OK",
                    "tabla_detectada": True,
                    "total_reportado": total_reportado,
                    "total_vecinos": (
                        total_reportado
                        or len(vecinos)
                    ),
                    "aviso_vecinos": current.get(
                        "aviso_vecinos",
                        "",
                    ),
                    "vecinos": vecinos,
                    "mensaje_vecinos": (
                        build_vecinos_message(vecinos)
                    ),
                    "boton": button_source,
                    "contexto": current_context,
                    "error": None,
                }

        scroll_vecinos_tables(page)
        page.wait_for_timeout(700)

    vecinos = list(collected.values())

    return {
        "ok": False,
        "estado": "VECINOS_TIMEOUT",
        "tabla_detectada": bool(
            best_metadata.get("tabla_detectada")
        ),
        "total_reportado": best_metadata.get(
            "total_reportado",
            0,
        ),
        "total_vecinos": len(vecinos),
        "aviso_vecinos": best_metadata.get(
            "aviso_vecinos",
            "",
        ),
        "vecinos": vecinos,
        "mensaje_vecinos": (
            build_vecinos_message(vecinos)
        ),
        "boton": button_source,
        "contexto": best_context,
        "error": (
            "La ventana Vecinos abrio, pero la tabla "
            "no termino de cargar completamente."
        ),
    }


def _extraer_vecinos_dom_actual(page):
    """
    Lee la tabla Vecinos del DOM actual sin hacer clic,
    sin recargar y sin lanzar otra consulta.
    """
    filas_detectadas = []

    paginas = []

    try:
        paginas.extend(page.context.pages)
    except Exception:
        pass

    if page not in paginas:
        paginas.insert(0, page)

    for pagina in paginas:
        contextos = [pagina]

        try:
            contextos.extend(
                frame
                for frame in pagina.frames
                if frame != pagina.main_frame
            )
        except Exception:
            pass

        for contexto in contextos:
            try:
                tablas = contexto.locator(
                    "div.v-table"
                )

                total_tablas = tablas.count()
            except Exception:
                continue

            for tabla_index in range(total_tablas):
                tabla = tablas.nth(tabla_index)

                try:
                    encabezados = tabla.locator(
                        ".v-table-header-cell "
                        ".v-table-caption-container"
                    ).all_inner_texts()
                except Exception:
                    encabezados = []

                encabezados = [
                    clean_text(valor)
                    for valor in encabezados
                ]

                encabezados_norm = [
                    norm(valor)
                    for valor in encabezados
                ]

                es_tabla_vecinos = (
                    any(
                        "mac" == valor
                        or valor.startswith("mac ")
                        for valor in encabezados_norm
                    )
                    and any(
                        "cuenta" in valor
                        or "c. rr" in valor
                        or "c rr" in valor
                        for valor in encabezados_norm
                    )
                    and any(
                        "direccion" in valor
                        for valor in encabezados_norm
                    )
                )

                if not es_tabla_vecinos:
                    continue

                try:
                    filas = tabla.locator(
                        ".v-table-table tr"
                    )
                    total_filas = filas.count()
                except Exception:
                    continue

                for fila_index in range(total_filas):
                    fila = filas.nth(fila_index)

                    try:
                        celdas = fila.locator(
                            ".v-table-cell-wrapper"
                        ).all_inner_texts()
                    except Exception:
                        celdas = []

                    celdas = [
                        clean_text(valor)
                        for valor in celdas
                    ]

                    if not any(celdas):
                        continue

                    registro = {}

                    for index, valor in enumerate(celdas):
                        encabezado = (
                            encabezados_norm[index]
                            if index < len(encabezados_norm)
                            else f"col_{index + 1}"
                        )

                        if (
                            encabezado == "mac"
                            or encabezado.startswith("mac ")
                        ):
                            registro["mac"] = valor

                        elif (
                            "cuenta" in encabezado
                            or "c. rr" in encabezado
                            or "c rr" in encabezado
                        ):
                            registro["cuenta_rr"] = valor

                        elif "direccion" in encabezado:
                            registro["direccion"] = valor

                    mac = clean_text(
                        registro.get("mac")
                    ).upper()

                    cuenta = re.sub(
                        r"\D",
                        "",
                        clean_text(
                            registro.get("cuenta_rr")
                        ),
                    )

                    direccion = clean_text(
                        registro.get("direccion")
                    )

                    mac_hex = re.sub(
                        r"[^0-9A-F]",
                        "",
                        mac,
                    )

                    if len(mac_hex) != 12:
                        continue

                    mac_formateada = ":".join(
                        mac_hex[index:index + 2]
                        for index in range(0, 12, 2)
                    )

                    filas_detectadas.append(
                        {
                            "mac": mac_formateada,
                            "cuenta_rr": cuenta,
                            "direccion": direccion,
                        }
                    )

    unicos = []
    vistos = set()

    for item in filas_detectadas:
        clave = (
            item.get("mac", ""),
            item.get("cuenta_rr", ""),
            item.get("direccion", ""),
        )

        if clave in vistos:
            continue

        vistos.add(clave)
        unicos.append(item)

    return unicos


def _diagnosticador_sigue_cargando_vecinos(page):
    """
    Detecta indicadores reales de carga de Vaadin.
    """
    paginas = []

    try:
        paginas.extend(page.context.pages)
    except Exception:
        pass

    if page not in paginas:
        paginas.insert(0, page)

    selectores_carga = (
        ".v-loading-indicator",
        ".v-loading-indicator-first",
        ".v-loading-indicator-second",
        ".v-loading-indicator-third",
        ".v-progressindicator",
        ".v-progressbar",
    )

    textos_carga = (
        "cargando",
        "consultando",
        "procesando",
        "obteniendo informacion",
        "espere por favor",
    )

    for pagina in paginas:
        contextos = [pagina]

        try:
            contextos.extend(
                frame
                for frame in pagina.frames
                if frame != pagina.main_frame
            )
        except Exception:
            pass

        for contexto in contextos:
            for selector in selectores_carga:
                try:
                    elementos = contexto.locator(selector)

                    for index in range(elementos.count()):
                        elemento = elementos.nth(index)

                        if elemento.is_visible(timeout=200):
                            return True
                except Exception:
                    continue

            try:
                texto = norm(
                    contexto.locator("body").inner_text(
                        timeout=600
                    )
                )

                if any(
                    indicador in texto
                    for indicador in textos_carga
                ):
                    return True
            except Exception:
                pass

    return False


# ATLAS_VECINOS_SELECT_MAX_V1_1
def seleccionar_maximo_vecinos(page):
    """
    Selecciona el máximo número de vecinos visible en el selector
    del modal Información Vecinos.

    Fail-closed:
    solo modifica un <select> si detecta al menos dos opciones
    pertenecientes al patrón esperado 30 / 60 / 90.
    """

    resultado = {
        "ok": False,
        "encontrado": False,
        "opciones": [],
        "valor_anterior": "",
        "valor_nuevo": "",
        "maximo": 0,
        "contexto": "",
        "error": "",
    }

    contexts = []

    try:
        pages = list(page.context.pages)
    except Exception:
        pages = [page]

    for current_page in pages:
        try:
            contexts.append(
                (
                    current_page,
                    f"{current_page.url}|page",
                )
            )
        except Exception:
            pass

        try:
            for frame in current_page.frames:
                if frame != current_page.main_frame:
                    contexts.append(
                        (
                            frame,
                            f"{current_page.url}|frame:{frame.url}",
                        )
                    )
        except Exception:
            pass

    if not contexts:
        contexts.append(
            (
                page,
                f"{getattr(page, 'url', '')}|page",
            )
        )

    seen = set()

    for context, label in contexts:
        identity = id(context)

        if identity in seen:
            continue

        seen.add(identity)

        try:
            selects = context.locator("select")
            select_count = selects.count()
        except Exception:
            continue

        for index in range(select_count):
            select = selects.nth(index)

            try:
                options = select.locator("option")
                option_count = options.count()
            except Exception:
                continue

            parsed = []

            for option_index in range(option_count):
                option = options.nth(option_index)

                try:
                    text = clean_text(
                        option.inner_text(timeout=500)
                    )

                    value = clean_text(
                        option.get_attribute("value")
                        or text
                    )
                except Exception:
                    continue

                numeric = None

                for candidate in (value, text):
                    candidate = clean_text(candidate)

                    if candidate.isdigit():
                        numeric = int(candidate)
                        break

                if numeric is None:
                    continue

                parsed.append(
                    {
                        "numeric": numeric,
                        "value": value,
                        "text": text,
                    }
                )

            numeric_values = sorted(
                {
                    item["numeric"]
                    for item in parsed
                }
            )

            expected_hits = {
                value
                for value in numeric_values
                if value in {30, 60, 90}
            }

            # No tocar selects ajenos.
            if len(expected_hits) < 2:
                continue

            resultado["encontrado"] = True
            resultado["opciones"] = numeric_values
            resultado["contexto"] = label

            target_numeric = max(expected_hits)

            resultado["maximo"] = target_numeric

            target = next(
                (
                    item
                    for item in parsed
                    if item["numeric"] == target_numeric
                ),
                None,
            )

            if not target:
                continue

            try:
                previous = clean_text(
                    select.input_value(timeout=700)
                )
            except Exception:
                previous = ""

            resultado["valor_anterior"] = previous

            selected = False

            target_value = clean_text(
                target.get("value")
            )

            target_text = clean_text(
                target.get("text")
            )

            if target_value:
                try:
                    select.select_option(
                        value=target_value,
                        timeout=2500,
                    )
                    selected = True
                except Exception:
                    pass

            if not selected and target_text:
                try:
                    select.select_option(
                        label=target_text,
                        timeout=2500,
                    )
                    selected = True
                except Exception:
                    pass

            if not selected:
                resultado["error"] = (
                    "Selector encontrado, pero no fue posible "
                    "seleccionar el máximo."
                )
                continue

            # Vaadin necesita un momento para refrescar la tabla.
            page.wait_for_timeout(1500)

            try:
                current = clean_text(
                    select.input_value(timeout=700)
                )
            except Exception:
                current = str(target_numeric)

            resultado["valor_nuevo"] = current
            resultado["ok"] = True

            print(
                "[VECINOS][SELECT_MAX] "
                f"contexto={label} "
                f"opciones={numeric_values} "
                f"antes={previous!r} "
                f"max={target_numeric} "
                f"despues={current!r}",
                file=sys.stderr,
                flush=True,
            )

            return resultado

    print(
        "[VECINOS][SELECT_MAX] "
        "No se encontro selector compatible 30/60/90.",
        file=sys.stderr,
        flush=True,
    )

    return resultado


# ATLAS_VECINOS_FILTROS_PRIMARIOS_V1
def _atlas_vecinos_buscar_checkbox(
    page,
    caption: str,
):
    """
    Busca un checkbox Vaadin por el texto de su LABEL.

    NO usa gwt-uid-* porque esos IDs son dinamicos.
    """

    expected = fold_vecinos_text(
        caption
    )

    for context, context_label in iter_vecinos_contexts(
        page
    ):
        try:
            labels = context.locator(
                "label"
            )

            for index in range(
                labels.count()
            ):
                label = labels.nth(
                    index
                )

                try:
                    if not label.is_visible(
                        timeout=200
                    ):
                        continue

                    text_value = fold_vecinos_text(
                        label.inner_text(
                            timeout=300
                        )
                    )

                except Exception:
                    continue

                if text_value != expected:
                    continue

                for_id = (
                    label.get_attribute(
                        "for"
                    )
                    or ""
                ).strip()

                checkbox = None

                if for_id:
                    try:
                        candidate = context.locator(
                            'input[id="'
                            + for_id
                            + '"]'
                        )

                        if candidate.count() > 0:
                            checkbox = candidate.first

                    except Exception:
                        checkbox = None

                if checkbox is None:
                    try:
                        candidate = label.locator(
                            "xpath=preceding-sibling::input"
                        )

                        if candidate.count() > 0:
                            checkbox = candidate.first

                    except Exception:
                        checkbox = None

                if checkbox is None:
                    try:
                        parent = label.locator(
                            "xpath=.."
                        )

                        candidate = parent.locator(
                            'input[type="checkbox"]'
                        )

                        if candidate.count() > 0:
                            checkbox = candidate.first

                    except Exception:
                        checkbox = None

                if checkbox is not None:
                    return (
                        checkbox,
                        label,
                        context_label,
                    )

        except Exception:
            continue

    return (
        None,
        None,
        "",
    )


def _atlas_vecinos_set_checkbox(
    checkbox,
    label,
    checked: bool,
) -> bool:

    if checkbox is None:
        return False

    try:
        current = checkbox.is_checked(
            timeout=500
        )
    except Exception:
        return False

    if current == checked:
        return True

    try:
        checkbox.click(
            timeout=2500
        )
    except Exception:

        if label is None:
            return False

        try:
            label.click(
                timeout=2500
            )
        except Exception:
            return False

    try:
        return (
            checkbox.is_checked(
                timeout=800
            )
            == checked
        )
    except Exception:
        return False


def _atlas_vecinos_filtrados_count(
    page,
) -> int:

    try:
        candidates = (
            read_vecinos_candidates(
                page
            )
        )

        if not candidates:
            return 0

        current, _context = candidates[0]

        return len(
            current.get(
                "vecinos"
            )
            or []
        )

    except Exception:
        return 0


def _atlas_vecinos_modal_visible(
    page,
) -> bool:

    for context, _label in iter_vecinos_contexts(
        page
    ):
        try:
            body = fold_vecinos_text(
                context.locator(
                    "body"
                ).inner_text(
                    timeout=350
                )
            )
        except Exception:
            continue

        if (
            "informacion vecinos"
            in body
            and "filtrar por"
            in body
        ):
            return True

    return False


def _atlas_vecinos_aplicar_filtros(
    page,
) -> dict:
    """
    PRIORIDAD:
      1. Solo desenganchados = ON
      2. RX/TX fuera de nivel = ON
      3. Si hay filas -> usar esa tabla.
      4. Si queda vacia -> apagar ambos y usar todos.

    El resultado filtrado es preferido.
    TODOS es exclusivamente fallback.
    """

    if not _atlas_vecinos_modal_visible(
        page
    ):
        return {
            "estado": "NO_MODAL",
            "filas": 0,
        }

    (
        desenganchados,
        label_desenganchados,
        ctx_desenganchados,
    ) = _atlas_vecinos_buscar_checkbox(
        page,
        "Solo desenganchados",
    )

    (
        niveles,
        label_niveles,
        ctx_niveles,
    ) = _atlas_vecinos_buscar_checkbox(
        page,
        "RX/TX fuera de nivel",
    )

    if (
        desenganchados is None
        or niveles is None
    ):
        return {
            "estado":
                "CONTROLES_NO_LISTOS",
            "filas": 0,
        }

    ok_desenganchados = (
        _atlas_vecinos_set_checkbox(
            desenganchados,
            label_desenganchados,
            True,
        )
    )

    ok_niveles = (
        _atlas_vecinos_set_checkbox(
            niveles,
            label_niveles,
            True,
        )
    )

    if not (
        ok_desenganchados
        and ok_niveles
    ):
        return {
            "estado":
                "CONTROLES_NO_LISTOS",
            "filas": 0,
        }

    print(
        "[VECINOS][FILTROS] "
        "Solo desenganchados=ON "
        "RX/TX fuera de nivel=ON",
        file=sys.stderr,
        flush=True,
    )

    # Vaadin necesita un instante para reconstruir la tabla.
    page.wait_for_timeout(
        1200
    )

    deadline = (
        time.perf_counter()
        + 5.0
    )

    previous = -1
    stable = 0
    count = 0

    while time.perf_counter() < deadline:

        count = (
            _atlas_vecinos_filtrados_count(
                page
            )
        )

        if count == previous:
            stable += 1
        else:
            previous = count
            stable = 0

        # Hay informacion filtrada y ya dejo de moverse.
        if (
            count > 0
            and stable >= 1
        ):
            print(
                "[VECINOS][FILTROS] "
                "MODO=FILTRADO "
                f"FILAS={count}",
                file=sys.stderr,
                flush=True,
            )

            return {
                "estado": "FILTRADO",
                "filas": count,
                "solo_desenganchados": True,
                "rx_tx_fuera_nivel": True,
            }

        page.wait_for_timeout(
            350
        )

    # ==============================================================================
    # FALLBACK:
    # con ambos filtros no hubo informacion.
    # Volvemos a todos los vecinos.
    # ==============================================================================

    _atlas_vecinos_set_checkbox(
        desenganchados,
        label_desenganchados,
        False,
    )

    _atlas_vecinos_set_checkbox(
        niveles,
        label_niveles,
        False,
    )

    print(
        "[VECINOS][FILTROS] "
        "SIN_FILAS_FILTRADAS -> "
        "FALLBACK_TODOS",
        file=sys.stderr,
        flush=True,
    )

    page.wait_for_timeout(
        1000
    )

    count_all = (
        _atlas_vecinos_filtrados_count(
            page
        )
    )

    print(
        "[VECINOS][FILTROS] "
        "MODO=FALLBACK_TODOS "
        f"FILAS_INICIALES={count_all}",
        file=sys.stderr,
        flush=True,
    )

    return {
        "estado": "FALLBACK_TODOS",
        "filas": count_all,
        "solo_desenganchados": False,
        "rx_tx_fuera_nivel": False,
    }


def consultar_vecinos(*args, **kwargs):
    # ATLAS_VECINOS_MODAL_REQUIRED_V2
    #
    # Direcciones:
    # 1) esperar boton Vecinos;
    # 2) clic una sola vez;
    # 3) validar ventana Informacion Vecinos;
    # 4) extraer MAC / C. RR / Direccion.
    #
    # TX/RX, Servicios IP y diagnostico completo NO son requisito.
    page = kwargs.get("page")

    if page is None:
        for candidate in args:
            if (
                hasattr(candidate, "wait_for_timeout")
                and hasattr(candidate, "context")
            ):
                page = candidate
                break

    if page is None:
        return {
            "ok": False,
            "estado": "VECINOS_PAGINA_NO_DISPONIBLE",
            "tabla_detectada": False,
            "modal_detectado": False,
            "boton_detectado": False,
            "total_reportado": 0,
            "total_vecinos": 0,
            "vecinos": [],
            "mensaje_vecinos": build_vecinos_message([]),
            "error": "No se recibio pagina Playwright.",
        }

    requested_timeout = kwargs.get("timeout_ms")

    try:
        requested_timeout = int(requested_timeout or 120000)
    except Exception:
        requested_timeout = 120000

    timeout_ms = max(
        15000,
        min(
            requested_timeout,
            int(
                os.getenv(
                    "DIAGNOSTICADOR_VECINOS_TIMEOUT_MS",
                    "120000",
                )
            ),
        ),
    )

    poll_ms = max(
        400,
        int(
            os.getenv(
                "DIAGNOSTICADOR_VECINOS_POLL_MS",
                "1000",
            )
        ),
    )

    started = time.perf_counter()
    deadline = started + (timeout_ms / 1000)

    # ATLAS_VECINOS_PRECLICKED_V1
    button_source = str(
        kwargs.get("button_source")
        or ""
    )

    button_clicked = bool(
        kwargs.get(
            "button_already_clicked",
            False,
        )
    )

    modal_detected = False
    table_detected = False
    last_error = ""
    best_total = 0
    best_aviso = ""
    best_context = ""

    # ATLAS_VECINOS_SELECT_MAX_PRECLICKED_V1
    # Si Vecinos ya fue abierto por --vecinos-priority, seleccionar
    # directamente el maximo 30/60/90 antes de aplicar los filtros.
    # No cambia filtros, extraccion, deduplicacion ni paginacion.
    if button_clicked:
        page.wait_for_timeout(700)
        seleccionar_maximo_vecinos(page)

    # ATLAS_VECINOS_FILTROS_PRIMARIOS_V1_STATE
    filtros_vecinos_estado = "PENDIENTE"
    filtros_controles_fallos = 0

    def snapshot_vecinos():
        nonlocal modal_detected
        nonlocal filtros_vecinos_estado
        nonlocal filtros_controles_fallos
        nonlocal table_detected
        nonlocal best_total
        nonlocal best_aviso
        nonlocal best_context

        # ATLAS_VECINOS_FILTROS_PRIMARIOS_V1_APPLY
        if filtros_vecinos_estado == "PENDIENTE":

            filtro = _atlas_vecinos_aplicar_filtros(
                page
            )

            filtro_estado = str(
                filtro.get("estado")
                or ""
            )

            if filtro_estado in (
                "FILTRADO",
                "FALLBACK_TODOS",
            ):
                filtros_vecinos_estado = (
                    filtro_estado
                )

            elif (
                filtro_estado
                == "CONTROLES_NO_LISTOS"
            ):
                filtros_controles_fallos += 1

                # Si el modal existe pero esta variante no publica
                # los filtros, no bloqueamos Vecinos indefinidamente.
                if filtros_controles_fallos >= 5:
                    filtros_vecinos_estado = (
                        "FALLBACK_SIN_CONTROLES"
                    )

                    print(
                        "[VECINOS][FILTROS] "
                        "MODO=FALLBACK_SIN_CONTROLES",
                        file=sys.stderr,
                        flush=True,
                    )

            # Si el modal ya esta abierto y aun estamos esperando
            # sus controles, NO devolvemos filas sin filtrar.
            if (
                filtro_estado
                == "CONTROLES_NO_LISTOS"
                and filtros_vecinos_estado
                == "PENDIENTE"
            ):
                return [], {}

        candidates = read_vecinos_candidates(page)

        if candidates:
            current, current_context = candidates[0]
            table_detected = bool(
                current.get("tabla_detectada")
            )
            best_total = int(
                current.get("total_reportado")
                or 0
            )
            best_aviso = str(
                current.get("aviso_vecinos")
                or ""
            )
            best_context = current_context

            rows = current.get("vecinos") or []

            if rows:
                modal_detected = True
                return rows, current

        for context, _label in iter_vecinos_contexts(page):
            try:
                body = fold_vecinos_text(
                    context.locator("body").inner_text(
                        timeout=700
                    )
                )
            except Exception:
                continue

            if (
                "informacion vecinos" in body
                and "mac" in body
                and "c. rr" in body
                and "direccion" in body
            ):
                modal_detected = True

        rows = _extraer_vecinos_dom_actual(page)

        if rows:
            table_detected = True
            modal_detected = True
            return rows, {}

        return [], {}

    # ATLAS_DIAGNOSTICADOR_NO_DISPONIBLE_FAST_V1_GUARD
    # Segunda guarda: si el toast sigue presente al entrar a consultar_vecinos,
    # devolver inmediatamente sin consumir el timeout del modal.
    try:
        _atlas_no_disp_text = fold_vecinos_text(
            vaadin_text(page)
        )
    except Exception:
        _atlas_no_disp_text = ""

    _atlas_no_disp = (
        "no se puede consultar el cablemodem o la ont"
        in _atlas_no_disp_text
        and (
            "no tiene registros historicos"
            in _atlas_no_disp_text
            or (
                "ont no se encuentra"
                in _atlas_no_disp_text
                and "acs" in _atlas_no_disp_text
            )
        )
    )

    if _atlas_no_disp:
        elapsed = round(
            time.perf_counter() - started,
            2,
        )

        return {
            "ok": False,
            "estado": "DIAGNOSTICADOR_NO_DISPONIBLE",
            "tabla_detectada": False,
            "modal_detectado": False,
            "boton_detectado": bool(button_clicked),
            "total_reportado": 0,
            "total_vecinos": 0,
            "aviso_vecinos": "",
            "vecinos": [],
            "mensaje_vecinos": build_vecinos_message([]),
            "espera_vecinos_seg": elapsed,
            "error": (
                "No se puede consultar el cablemodem o la ONT. "
                "El cablemodem no tiene registros históricos en los últimos "
                "siete(7) días o la ONT no se encuentra en disponible en el ACS."
            ),
        }

    # ATLAS_VECINOS_TABLA_COMPLETA_V3
    existing, existing_meta = snapshot_vecinos()

    collected_rows = {}
    stable_cycles = 0
    last_count = -1

    if existing:
        for row in existing:
            key = (
                str(row.get("mac") or "").strip().upper(),
                str(row.get("cuenta_rr") or "").strip(),
                str(row.get("direccion") or "").strip(),
            )
            collected_rows[key] = row

    while time.perf_counter() < deadline:
        if not button_clicked:
            try:
                remaining_ms = max(
                    800,
                    int(
                        (
                            deadline
                            - time.perf_counter()
                        )
                        * 1000
                    ),
                )

                button_source = click_vecinos_anywhere(
                    page,
                    timeout_ms=min(
                        3000,
                        remaining_ms,
                    ),
                )
                button_clicked = True

                print(
                    "[VECINOS][V2] Boton Vecinos clickeado: "
                    f"{button_source}",
                    file=sys.stderr,
                    flush=True,
                )

                page.wait_for_timeout(700)

                # ATLAS_VECINOS_SELECT_MAX_CALL_V1_1
                select_max = seleccionar_maximo_vecinos(page)

                if select_max.get("ok"):
                    # Esperar refresco de tabla despues de 30/60/90.
                    page.wait_for_timeout(1200)

            except Exception as exc:
                last_error = (
                    f"{type(exc).__name__}: {exc}"
                )

        rows, metadata = snapshot_vecinos()

        if rows:
            total_reportado = int(
                metadata.get("total_reportado")
                or best_total
                or 0
            )

            for row in rows:
                key = (
                    str(row.get("mac") or "").strip().upper(),
                    str(row.get("cuenta_rr") or "").strip(),
                    str(row.get("direccion") or "").strip(),
                )
                collected_rows[key] = row

            current_rows = list(collected_rows.values())
            current_count = len(current_rows)

            if current_count == last_count:
                stable_cycles += 1
            else:
                stable_cycles = 0
                last_count = current_count

            print(
                "[VECINOS][V3] progreso "
                f"extraido={current_count} "
                f"reportado={total_reportado or '?'} "
                f"stable={stable_cycles}",
                file=sys.stderr,
                flush=True,
            )

            # ATLAS_VECINOS_STABLE_TERMINAL_V1
            #
            # El total que reporta la UI es informativo.
            # Puede diferir de las filas realmente expuestas por el DOM.
            #
            # Una extracción con vecinos válidos se considera terminada si:
            #   1) alcanzó el total reportado, O
            #   2) la cantidad extraída permanece estable durante 3 ciclos.
            #
            # Esto evita esperar hasta timeout por filas que la interfaz
            # reporta pero nunca renderiza/exhibe al navegador.
            carga_completa = (
                current_count > 0
                and (
                    (
                        total_reportado > 0
                        and current_count >= total_reportado
                    )
                    or stable_cycles >= 3
                )
            )

            if carga_completa:
                print(
                    "[VECINOS][V3] Tabla completa: "
                    f"{current_count} fila(s).",
                    file=sys.stderr,
                    flush=True,
                )

                return {
                    "ok": True,
                    "estado": "VECINOS_OK",
                    "tabla_detectada": True,
                    "modal_detectado": True,
                    "boton_detectado": bool(button_clicked),
                    "boton": button_source,
                    "contexto": best_context,
                    "total_reportado": (
                        total_reportado
                        or current_count
                    ),
                    "total_vecinos": current_count,
                    "aviso_vecinos": (
                        metadata.get("aviso_vecinos")
                        or best_aviso
                        or ""
                    ),
                    "vecinos": current_rows,
                    "mensaje_vecinos": build_vecinos_message(
                        current_rows
                    ),
                    "espera_vecinos_seg": round(
                        time.perf_counter() - started,
                        2,
                    ),
                    "error": None,
                }
        if button_clicked:
            scroll_vecinos_tables(page)

        page.wait_for_timeout(poll_ms)

    elapsed = round(
        time.perf_counter() - started,
        2,
    )

    if not button_clicked:
        estado = "VECINOS_BOTON_NO_VISIBLE"
        error = (
            "No aparecio el boton Vecinos dentro del "
            f"tiempo permitido ({elapsed}s)."
        )
    elif not modal_detected:
        estado = "VECINOS_MODAL_NO_ABRIO"
        error = (
            "Se hizo clic en Vecinos, pero no se detecto "
            "la ventana Informacion Vecinos."
        )
    elif not table_detected:
        estado = "VECINOS_TABLA_NO_DETECTADA"
        error = (
            "La ventana Informacion Vecinos abrio, pero "
            "no se detecto la tabla MAC/C. RR/Direccion."
        )
    else:
        estado = "VECINOS_TABLA_SIN_FILAS"
        error = (
            "La tabla Vecinos se detecto, pero no publico filas."
        )

    if last_error:
        error = f"{error} Ultimo detalle: {last_error}"

    print(
        "[VECINOS][V2] Fallo controlado: "
        f"estado={estado} "
        f"boton={button_clicked} "
        f"modal={modal_detected} "
        f"tabla={table_detected}",
        file=sys.stderr,
        flush=True,
    )

    return {
        "ok": False,
        "estado": estado,
        "tabla_detectada": table_detected,
        "modal_detectado": modal_detected,
        "boton_detectado": button_clicked,
        "boton": button_source,
        "contexto": best_context,
        "total_reportado": best_total,
        "total_vecinos": 0,
        "aviso_vecinos": best_aviso,
        "vecinos": [],
        "mensaje_vecinos": build_vecinos_message([]),
        "espera_vecinos_seg": elapsed,
        "error": error,
    }


def build_diagnosticador_template(datos: Dict[str, Any]) -> str:
    b = datos.get("informacion_basica") or {}
    calidad = datos.get("calidad_tiempo_real") or []
    servicios = datos.get("servicios_ip") or []
    diagnostico = clean_text(datos.get("diagnostico") or "")
    historico = datos.get("historico_niveles") or []

    def gv(key: str) -> str:
        return clean_text(b.get(key) or "no data")

    lines: List[str] = []
    lines.append("Información Básica")
    lines.append(f"Marca: {gv('marca')}\tModelo: {gv('modelo')}")
    lines.append(f"Up time: {gv('uptime')}")
    lines.append(f"FirmWare: {gv('firmware')}")
    lines.append(f"Serial: {gv('serial')}\tMac: {gv('mac')}")
    lines.append(f"OLT: {gv('olt')}")
    lines.append(f"Nodo: {gv('nodo')}")
    lines.append(f"Puerto Tarjeta: {gv('puerto_tarjeta')}")
    lines.append(f"Nombre Tarjeta: {gv('nombre_tarjeta')}\tPuerto PON: {gv('puerto_pon')}")
    lines.append(f"Estado ante el ACS: {gv('estado_acs')}")
    lines.append(f"Cuenta Matriz: {gv('cuenta_matriz')}")
    if b.get("edificio"):
        lines.append(f"Edificio: {gv('edificio')}")
    lines.append("Opciones >>")

    lines.append("Calidad Tiempo Real")
    lines.append("Tipo")
    lines.append("Valor")
    for row in calidad:
        tipo = get_first_row_value([row], ["Tipo", "col_1"]) or next(iter(row.values()), "")
        valor = get_first_row_value([row], ["Valor", "col_2"])
        if not valor and len(row.values()) >= 2:
            valor = list(row.values())[1]
        if tipo:
            lines.append(tipo)
        if valor:
            lines.append(valor)

    lines.append("Direccionamiento Ip Servicios")
    lines.append("Servicio")
    lines.append("Estado")
    lines.append("Dirección Ip")
    lines.append("DNS Server")
    for row in servicios:
        servicio = get_first_row_value([row], ["Servicio", "col_1"]) or next(iter(row.values()), "")
        estado = get_first_row_value([row], ["Estado", "col_2"])
        ip = get_first_row_value([row], ["Dirección Ip", "Direccion Ip", "IP", "col_3"])
        dns = get_first_row_value([row], ["DNS Server", "DNS", "col_4"])
        if servicio:
            lines.append(servicio)
        if estado:
            lines.append(estado)
        if ip:
            lines.append(ip)
        if dns:
            lines.append(dns)

    if historico:
        lines.append("Histórico de Niveles")
        headers = list(historico[0].keys())
        lines.append("\t".join(headers))
        for row in historico:
            lines.append("\t".join(clean_text(row.get(h, "")) for h in headers))

    lines.append("Diagnostico")
    lines.append(diagnostico or "no data")

    mensaje_vecinos = (
        datos.get("mensaje_vecinos")
        or ""
    )

    if mensaje_vecinos:
        lines.append("")
        lines.append("Vecinos")
        lines.extend(
            str(mensaje_vecinos).splitlines()
        )

    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------
# Flujos principales
# ---------------------------------------------------------------------
def login_diagnosticador(
    url: str,
    username: str,
    password: str,
    headless: bool,
    timeout_ms: int,
    slow_mo_ms: int,
    hold_seconds: int,
) -> LoginResult:
    started = time.perf_counter()
    screenshot = ""
    html = ""
    initial_url = url
    final_url = url
    selectors: Dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        try:
            ok, estado, mensaje, selectors, initial_url, final_url = perform_login(page, url, username, password, timeout_ms)
            screenshot, html = save_evidence(page, "after_login")

            if hold_seconds > 0:
                page.wait_for_timeout(hold_seconds * 1000)

            return LoginResult(
                ok=ok,
                estado=estado,
                url_inicial=initial_url,
                url_final=final_url,
                screenshot=screenshot,
                html=html,
                mensaje=(
                    f"{mensaje} Selectores usados: user={selectors.get('user')}, "
                    f"password={selectors.get('password')}, login={selectors.get('login')}."
                ),
                error=None,
                login_context=selectors.get("context"),
                duracion_seg=round(time.perf_counter() - started, 2),
            )
        except Exception as exc:
            final_url = page.url if page else final_url
            try:
                screenshot, html = save_evidence(page, "error_login")
            except Exception:
                pass
            return LoginResult(
                ok=False,
                estado="ERROR_LOGIN",
                url_inicial=initial_url,
                url_final=final_url,
                screenshot=screenshot,
                html=html,
                mensaje="Falló la automatización de login. Revisa el error y la evidencia.",
                error=str(exc),
                login_context=selectors.get("context"),
                duracion_seg=round(time.perf_counter() - started, 2),
            )
        finally:
            browser.close()


def consultar_diagnosticador(
    url: str,
    username: str,
    password: str,
    consulta_tipo: str,
    consulta_valor: str,
    headless: bool,
    timeout_ms: int,
    slow_mo_ms: int,
    hold_seconds: int,
) -> ConsultaResult:
    started = time.perf_counter()
    screenshot = ""
    html_path = ""
    initial_url = url
    final_url = url
    selectors: Dict[str, Any] = {}
    consulta_tipo = norm(consulta_tipo)

    if consulta_tipo not in {"mac", "cuenta"}:
        return ConsultaResult(
            ok=False,
            estado="TIPO_CONSULTA_INVALIDO",
            url_inicial=url,
            url_final=url,
            screenshot="",
            html="",
            mensaje="El tipo de consulta debe ser 'mac' o 'cuenta'.",
            consulta_tipo=consulta_tipo,
            consulta_valor=consulta_valor,
        )

    consulta_valor = normalize_query_value(consulta_tipo, consulta_valor)
    if not consulta_valor:
        return ConsultaResult(
            ok=False,
            estado="VALOR_CONSULTA_FALTANTE",
            url_inicial=url,
            url_final=url,
            screenshot="",
            html="",
            mensaje="Ingresa el valor a consultar.",
            consulta_tipo=consulta_tipo,
            consulta_valor=consulta_valor,
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)
        debug_keep_open = False

        try:
            ok, estado_login, msg_login, login_selectors, initial_url, final_url = perform_login(
                page, url, username, password, timeout_ms
            )
            selectors.update({"login": login_selectors})

            if not ok:
                screenshot, html_path = save_evidence(page, "consulta_login_no_confirmado")
                return ConsultaResult(
                    ok=False,
                    estado=estado_login,
                    url_inicial=initial_url,
                    url_final=final_url,
                    screenshot=screenshot,
                    html=html_path,
                    mensaje=f"No se pudo confirmar login antes de consultar. {msg_login}",
                    login_context=login_selectors.get("context"),
                    duracion_seg=round(time.perf_counter() - started, 2),
                    consulta_tipo=consulta_tipo,
                    consulta_valor=consulta_valor,
                    selectores=selectors,
                )

            wait_for_any_text(page, ["Mac", "Cuenta", "Consultar"], timeout_ms=12000)

            query_context, query_context_label, query_field, query_selector = find_query_field(page, consulta_tipo)
            valor_escrito = fill_vaadin_query_field(query_field, consulta_valor, consulta_tipo)

            selectors["query"] = {
                "context": query_context_label,
                "field": query_selector,
                "value": consulta_valor,
                "valor_escrito": valor_escrito,
            }

            clicked: List[str] = []

            # 1) Primer Consultar: búsqueda por MAC/Cuenta.
            valor_antes_primer_click = read_input_value(query_field)
            selectors.setdefault("query_checks", []).append(
                {
                    "fase": "antes_primer_consultar",
                    "valor_antes_click": valor_antes_primer_click,
                }
            )

            if not valor_antes_primer_click:
                # El campo se limpió antes del click. Lo buscamos otra vez y reescribimos.
                query_context, query_context_label, query_field, query_selector = find_query_field(page, consulta_tipo)
                valor_escrito = fill_vaadin_query_field(query_field, consulta_valor, consulta_tipo)
                selectors["query"].update(
                    {
                        "context": query_context_label,
                        "field": query_selector,
                        "valor_reescrito_antes_click": valor_escrito,
                    }
                )

            selector_1 = click_consultar_anywhere(
                page
            )

            clicked.append(
                f"primer_consultar:{selector_1}"
            )

            # ATLAS_VECINOS_IMMEDIATE_AFTER_FIRST_CONSULTAR_V1
            vecinos_priority = (
                "--vecinos-priority"
                in sys.argv
            )

            vecinos_preclicked = False
            vecinos_preclicked_source = ""

            if vecinos_priority:

                # Desde este instante Vecinos tiene prioridad.
                priority_state = (
                    wait_and_click_vecinos_priority(
                        page,
                        consulta_tipo,
                        timeout_ms=150000,
                    )
                )

                selectors[
                    "vecinos_priority_state"
                ] = priority_state

                vecinos_preclicked = bool(
                    priority_state.get(
                        "vecinos_clicked"
                    )
                )

                vecinos_preclicked_source = str(
                    priority_state.get(
                        "vecinos_source"
                    )
                    or ""
                )

                if priority_state.get(
                    "segundo_consultar_clicked"
                ):
                    clicked.append(
                        "segundo_consultar:"
                        "AUTO_STATE_DRIVEN"
                    )

                estado_1 = str(
                    priority_state.get(
                        "estado"
                    )
                    or ""
                )

                selectors[
                    "estado_despues_primer_consultar"
                ] = estado_1

                html_intermedio = (
                    get_diagnosticador_html(
                        page
                    )
                )

                datos_intermedios = (
                    extract_diagnosticador_data_from_html(
                        html_intermedio
                    )
                )

                (
                    template_ready_intermedio,
                    faltantes_intermedios,
                ) = datos_ready_for_template(
                    datos_intermedios
                )

                selectors[
                    "faltantes_despues_primer_consultar"
                ] = faltantes_intermedios

            else:

                # Flujo histórico fuera de --vecinos-priority.
                wait_soft_network(
                    page,
                    timeout_ms=15000,
                )

                estado_1 = (
                    wait_after_first_consultar(
                        page,
                        consulta_tipo,
                        timeout_ms=70000,
                    )
                )

                selectors[
                    "estado_despues_primer_consultar"
                ] = estado_1

                html_intermedio = (
                    get_diagnosticador_html(
                        page
                    )
                )

                datos_intermedios = (
                    extract_diagnosticador_data_from_html(
                        html_intermedio
                    )
                )

                (
                    template_ready_intermedio,
                    faltantes_intermedios,
                ) = datos_ready_for_template(
                    datos_intermedios
                )

                selectors[
                    "faltantes_despues_primer_consultar"
                ] = faltantes_intermedios

                if not template_ready_intermedio:

                    estado_2 = (
                        wait_until_second_consultar_or_result(
                            page,
                            timeout_ms=70000,
                        )
                    )

                    selectors[
                        "estado_antes_segundo_consultar"
                    ] = estado_2

                    if (
                        estado_2
                        == "SEGUNDO_CONSULTAR_VISIBLE"
                    ):
                        selector_2 = (
                            click_consultar_anywhere(
                                page
                            )
                        )

                        clicked.append(
                            f"segundo_consultar:"
                            f"{selector_2}"
                        )

                        wait_soft_network(
                            page,
                            timeout_ms=20000,
                        )

                        page.wait_for_timeout(
                            1000
                        )

            selectors["consultar"] = clicked

            # 3) En modo vecinos-priority NO esperamos plantilla completa.
            if vecinos_priority:
                template_ready = template_ready_intermedio
                datos_esperados = datos_intermedios
                faltantes = faltantes_intermedios
                score = diagnosticador_result_score(
                    html_intermedio
                )
            else:
                template_ready, datos_esperados, faltantes, score = (
                    wait_until_template_ready(
                        page,
                        timeout_ms=180000,
                    )
                )

            final_url = page.url
            final_html = get_diagnosticador_html(page)

            # Parseo final obligatorio sobre el HTML visible actual.
            datos_finales = extract_diagnosticador_data_from_html(final_html)
            ready_final, faltantes_finales = datos_ready_for_template(datos_finales)
            score_final = diagnosticador_result_score(final_html)

            # Si el parseo final sale completo, usamos ese.
            # Si no, usamos el mejor capturado durante la espera.
            if ready_final:
                datos = datos_finales
                template_ready = True
                faltantes = []
                score = score_final
            elif template_ready and datos_esperados:
                datos = datos_esperados
            else:
                datos = datos_finales if datos_finales.get("informacion_basica") else datos_esperados

            # 4) Abrir Vecinos y extraer
            # MAC / C. RR / Direccion.
            try:
                # ATLAS_VECINOS_PRIORITY_TIMEOUT_V2
                # Direcciones no debe esperar 120 s por un modal
                # que puede no existir. Otros usos conservan 120 s.
                # ATLAS_VECINOS_PRIORITY_TIMEOUT_V3
                vecinos_timeout_ms = (
                    150000
                    if vecinos_priority
                    else 120000
                )

                vecinos_result = consultar_vecinos(
                    page,
                    timeout_ms=vecinos_timeout_ms,
                    button_already_clicked=(
                        vecinos_preclicked
                        if vecinos_priority
                        else False
                    ),
                    button_source=(
                        vecinos_preclicked_source
                        if vecinos_priority
                        else ""
                    ),
                )
            except Exception as exc:
                vecinos_result = {
                    "ok": False,
                    "estado": "ERROR_VECINOS",
                    "total_reportado": 0,
                    "total_vecinos": 0,
                    "aviso_vecinos": "",
                    "vecinos": [],
                    "mensaje_vecinos": (
                        "MAC\t//\tC. RR\t//\t"
                        "Direcci\u00f3n"
                    ),
                    "error": str(exc),
                }

            datos["total_vecinos"] = (
                vecinos_result.get(
                    "total_vecinos",
                    0,
                )
            )

            datos["aviso_vecinos"] = (
                vecinos_result.get(
                    "aviso_vecinos",
                    "",
                )
            )

            datos["vecinos"] = (
                vecinos_result.get(
                    "vecinos",
                    [],
                )
            )

            datos["mensaje_vecinos"] = (
                vecinos_result.get(
                    "mensaje_vecinos",
                    "",
                )
            )

            datos["estado_vecinos"] = (
                vecinos_result.get(
                    "estado",
                    "ERROR_VECINOS",
                )
            )

            selectors["vecinos"] = {
                "ok": vecinos_result.get(
                    "ok",
                    False,
                ),
                "estado": vecinos_result.get(
                    "estado",
                ),
                "boton": vecinos_result.get(
                    "boton",
                ),
                "contexto": vecinos_result.get(
                    "contexto",
                ),
                "total_reportado": (
                    vecinos_result.get(
                        "total_reportado",
                        0,
                    )
                ),
                "total_extraido": len(
                    vecinos_result.get(
                        "vecinos",
                    )
                    or []
                ),
                "error": vecinos_result.get(
                    "error",
                ),
            }

            plantilla = build_diagnosticador_template(datos)

            selectors["template_ready"] = template_ready
            selectors["faltantes_template"] = faltantes
            selectors["resultado_score"] = score
            selectors["resultado_score_final"] = score_final
            selectors["ready_final"] = ready_final
            selectors["faltantes_finales"] = faltantes_finales
            selectors["html_final_len"] = len(final_html)

            screenshot, html_path = save_evidence(page, f"consulta_{consulta_tipo}_{safe_prefix_value(consulta_valor)}")

            tiene_basica = bool(datos.get("informacion_basica"))
            tiene_resultado_parcial = bool(
                tiene_basica
                and (
                    datos.get("calidad_tiempo_real")
                    or datos.get("servicios_ip")
                    or datos.get("diagnostico")
                )
            )

            vecinos_ok = bool(
                (
                    selectors.get("vecinos")
                    or {}
                ).get("ok")
            )

            # ATLAS_VECINOS_SUCCESS_IS_TERMINAL_V1
            #
            # Para Direcciones (--vecinos-priority), una tabla de
            # Vecinos valida ES el resultado funcional buscado.
            #
            # No exigir Serial/OLT/ACS/Servicios IP para considerar
            # exitosa una consulta cuyo objetivo es obtener vecinos.
            vecinos_extraidos = int(
                (
                    selectors.get("vecinos")
                    or {}
                ).get("total_extraido")
                or 0
            )

            _atlas_estado_vecinos = str(
                (
                    selectors.get("vecinos")
                    or {}
                ).get("estado")
                or ""
            ).strip().upper()

            # ATLAS_DIAGNOSTICADOR_NO_DISPONIBLE_FAST_V1_RESPONSE
            if (
                _atlas_estado_vecinos
                == "DIAGNOSTICADOR_NO_DISPONIBLE"
            ):
                estado = "DIAGNOSTICADOR_NO_DISPONIBLE"
                mensaje = (
                    "No se puede consultar el cablemodem o la ONT. "
                    "El cablemodem no tiene registros históricos en los últimos "
                    "siete(7) días o la ONT no se encuentra en disponible en el ACS."
                )
                ok_final = False

            elif (
                vecinos_priority
                and vecinos_ok
                and vecinos_extraidos > 0
            ):
                estado = "CONSULTA_OK"
                mensaje = (
                    "Consulta OK: tabla de Vecinos "
                    f"recuperada con {vecinos_extraidos} fila(s)."
                )
                ok_final = True

            elif template_ready and vecinos_ok:
                estado = "CONSULTA_OK"
                mensaje = (
                    "Consulta OK: diagnostico y tabla "
                    "de vecinos completos."
                )
                ok_final = True
            elif template_ready:
                estado = "CONSULTA_PARCIAL"
                mensaje = (
                    "El diagnostico cargo completo, "
                    "pero fallo la tabla de Vecinos: "
                    + clean_text(
                        (
                            selectors.get("vecinos")
                            or {}
                        ).get("error")
                        or "sin detalle"
                    )
                )
                ok_final = False
            elif tiene_resultado_parcial or tiene_basica:
                estado = "CONSULTA_PARCIAL"
                mensaje = (
                    "La consulta cargó información parcial, pero faltan datos para completar la plantilla: "
                    + ", ".join(faltantes or selectors.get("faltantes_template", []))
                )
                ok_final = False
            else:
                estado = "CONSULTA_SIN_DATOS_PARSEABLES"
                mensaje = "La consulta terminó, pero no encontré los paneles esperados en el HTML. Revisa la captura/HTML de evidencia."
                ok_final = False

            if not ok_final and not headless and env_bool("DIAGNOSTICADOR_KEEP_OPEN_ON_ERROR", False):
                debug_keep_open = True

            if hold_seconds > 0:
                page.wait_for_timeout(hold_seconds * 1000)

            return ConsultaResult(
                ok=ok_final,
                estado=estado,
                url_inicial=initial_url,
                url_final=final_url,
                screenshot=screenshot,
                html=html_path,
                mensaje=mensaje,
                error=None,
                login_context=login_selectors.get("context"),
                duracion_seg=round(time.perf_counter() - started, 2),
                consulta_tipo=consulta_tipo,
                consulta_valor=consulta_valor,
                plantilla=plantilla,
                datos=datos,
                selectores=selectors,
            )

        except Exception as exc:
            final_url = page.url if page else final_url
            try:
                screenshot, html_path = save_evidence(page, f"error_consulta_{consulta_tipo}")
            except Exception:
                pass
            return ConsultaResult(
                ok=False,
                estado="ERROR_CONSULTA",
                url_inicial=initial_url,
                url_final=final_url,
                screenshot=screenshot,
                html=html_path,
                mensaje="Falló la consulta automática. Revisa el error y la evidencia.",
                error=str(exc),
                login_context=(selectors.get("login") or {}).get("context") if isinstance(selectors.get("login"), dict) else None,
                duracion_seg=round(time.perf_counter() - started, 2),
                consulta_tipo=consulta_tipo,
                consulta_valor=consulta_valor,
                selectores=selectors,
            )
        finally:
            if debug_keep_open and not headless:
                print(
                    "DEBUG: navegador abierto por error/consulta parcial. Ciérralo manualmente cuando termines de revisar.",
                    file=sys.stderr,
                )
                page.wait_for_timeout(10 * 60 * 1000)

            browser.close()


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def main() -> int:
    load_env_file(BASE_DIR / ".env")
    load_env_file(BASE_DIR / ".env.local")

    parser = argparse.ArgumentParser(description="Automatización Diagnosticador residencial")
    parser.add_argument("--url", default=os.getenv("DIAGNOSTICADOR_URL", DEFAULT_URL))
    parser.add_argument("--user", default=os.getenv("DIAGNOSTICADOR_USER"))
    parser.add_argument("--password", default=os.getenv("DIAGNOSTICADOR_PASSWORD"))
    parser.add_argument("--query-type", choices=["mac", "cuenta"], default=os.getenv("DIAGNOSTICADOR_QUERY_TYPE"))
    parser.add_argument("--query", default=os.getenv("DIAGNOSTICADOR_QUERY"), help="MAC o cuenta a consultar")
    parser.add_argument("--headless", action="store_true", default=env_bool("DIAGNOSTICADOR_HEADLESS", False))
    parser.add_argument("--headed", action="store_true", help="Forzar navegador visible")
    parser.add_argument("--timeout-ms", type=int, default=int(os.getenv("DIAGNOSTICADOR_TIMEOUT_MS", "180000")))
    parser.add_argument("--slow-mo", type=int, default=int(os.getenv("DIAGNOSTICADOR_SLOW_MO_MS", "120")))
    parser.add_argument("--hold-seconds", type=int, default=int(os.getenv("DIAGNOSTICADOR_HOLD_SECONDS", "8")))
    parser.add_argument("--json-output", action="store_true", help="Salida solo JSON para integraciones")
    # ATLAS_VECINOS_PRIORITY_V1
    parser.add_argument(
        "--vecinos-priority",
        action="store_true",
        help=("Prioriza Nodo/Vecinos y no espera TX/RX, servicios IP ni diagnostico completo."),
    )
    args = parser.parse_args()

    if args.headed:
        args.headless = False

    username = args.user or os.getenv("DIAG_REQUEST_USER")
    password = args.password or os.getenv("DIAG_REQUEST_PASSWORD")

    if not username and not args.json_output:
        username = input("Usuario Diagnosticador: ").strip()
    if not password and not args.json_output:
        password = getpass("Contraseña Diagnosticador: ")

    if not username or not password:
        result = {
            "ok": False,
            "estado": "CREDENCIALES_FALTANTES",
            "mensaje": "Configura DIAGNOSTICADOR_USER y DIAGNOSTICADOR_PASSWORD en .env o ingrésalas desde la interfaz local.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if args.query_type and args.query:
        result = consultar_diagnosticador(
            url=args.url,
            username=username,
            password=password,
            consulta_tipo=args.query_type,
            consulta_valor=args.query,
            headless=args.headless,
            timeout_ms=args.timeout_ms,
            slow_mo_ms=args.slow_mo,
            hold_seconds=args.hold_seconds,
        ).as_dict()
    else:
        result = login_diagnosticador(
            url=args.url,
            username=username,
            password=password,
            headless=args.headless,
            timeout_ms=args.timeout_ms,
            slow_mo_ms=args.slow_mo,
            hold_seconds=args.hold_seconds,
        ).as_dict()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
