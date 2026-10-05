# -*- coding: utf-8 -*-
"""Sesion viva optimizada para preparar y confirmar una OT relacionada en SmartIT.

Mantiene una sola sesion Playwright activa y separa la preparacion de la confirmacion.
Los helpers de dry-run, commit, journal y OFSC se consolidan aqui para reducir
fragmentacion sin cambiar el flujo funcional.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Frame, Locator, Page, Playwright, async_playwright

from helix_runner import (
    PROFILE_DIR,
    click_global_search,
    click_incident_in_table,
    find_related_tab_once,
    open_related_tab_and_read,
    open_work_order,
    wait_no_loader,
)
from smartit_scraper import Settings, iniciar_sesion, locator_visible


def get_smartit_url() -> str:
    value = str(os.getenv("SMARTIT_URL") or "").strip()
    if not value:
        raise RuntimeError("Falta configurar SMARTIT_URL en .env.")
    return value


def get_smartit_app_base_url() -> str:
    url = get_smartit_url()
    if "#/" in url:
        return url.split("#/", 1)[0].rstrip("/") + "/#/"
    return url.rstrip("/") + "/#/"


# ATLAS_OT_COMMIT_DURABLE_JOURNAL_V2
# PREPARED = lease temporal. Los estados posteriores a intencion de Save
# permanecen bloqueantes hasta reconciliacion explicita.

_journal_journal_dir = Path(
    os.getenv(
        "ATLAS_OT_COMMIT_JOURNAL_DIR",
        r"C:\ProgramData\CentralNOC\Dashboard_Hogar\data\ot_commit_journal",
    )
)

_journal_schema_version = 2

_journal_permanent_block_states = {
    "SAVE_CLICK_INTENT",
    "SAVE_CLICKED",
    "SAVE_CONFIRMED",
    "UNCERTAIN",
}

_journal_retryable_states = {"FAILED_SAFE"}
_journal_valid_states = _journal_permanent_block_states | _journal_retryable_states | {"PREPARED"}

_journal_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

_journal_PREPARED_LEASE_SEC = max(
    60,
    int(os.getenv("ATLAS_OT_COMMIT_PREPARED_LEASE_SECONDS", "900")),
)
_journal_LOCK_WAIT_SEC = 5.0
_journal_LOCK_STALE_SEC = 900.0


def _journal_utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _journal_utc_now() -> str:
    return _journal_utc_now_dt().isoformat(timespec="seconds")


def _journal_norm(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def journal_operation_key(source_wo: Any, tipo: Any, expected_incident: Any) -> str:
    canonical = "|".join(
        (
            _journal_norm(source_wo),
            _journal_norm(tipo),
            _journal_norm(expected_incident),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _journal_validate_key(operation_hash: str) -> str:
    key = str(operation_hash or "").strip().lower()
    if not _journal_HASH_RE.fullmatch(key):
        raise ValueError("OT_COMMIT_OPERATION_HASH_INVALID")
    return key


def _journal_record_path(operation_hash: str) -> Path:
    return _journal_journal_dir / f"{_journal_validate_key(operation_hash)}.json"


def _journal_lock_path(operation_hash: str) -> Path:
    return _journal_journal_dir / f"{_journal_validate_key(operation_hash)}.lock"


def _journal_ensure_dir() -> None:
    _journal_journal_dir.mkdir(parents=True, exist_ok=True)


@contextmanager
def _journal_operation_lock(operation_hash: str):
    _journal_ensure_dir()
    lock_path = _journal_lock_path(operation_hash)
    deadline = time.monotonic() + _journal_LOCK_WAIT_SEC
    fd = None

    while fd is None:
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(
                fd,
                f"pid={os.getpid()} created_at={_journal_utc_now()}\n".encode(
                    "ascii",
                    errors="replace",
                ),
            )
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > _journal_LOCK_STALE_SEC:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue

            if time.monotonic() >= deadline:
                raise TimeoutError("OT_COMMIT_JOURNAL_LOCK_TIMEOUT")

            time.sleep(0.05)

    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass


def _journal_corrupt_record(operation_hash: str) -> dict[str, Any]:
    return {
        "schema_version": _journal_schema_version,
        "operation_hash": _journal_validate_key(operation_hash),
        "state": "UNCERTAIN",
        "reason_code": "JOURNAL_CORRUPT_OR_UNREADABLE",
        "attempt": 0,
        "created_at": "",
        "updated_at": _journal_utc_now(),
    }


def _journal_read_unlocked(operation_hash: str) -> dict[str, Any] | None:
    path = _journal_record_path(operation_hash)

    if not path.exists():
        return None

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8",
                errors="strict",
            )
        )
    except Exception:
        return _journal_corrupt_record(operation_hash)

    if not isinstance(data, dict):
        return _journal_corrupt_record(operation_hash)

    state = str(data.get("state") or "").strip().upper()
    if state not in _journal_valid_states:
        return _journal_corrupt_record(operation_hash)

    return data


def _journal_read_state(operation_hash: str) -> dict[str, Any] | None:
    key = _journal_validate_key(operation_hash)

    with _journal_operation_lock(key):
        data = _journal_read_unlocked(key)
        return dict(data) if data else None


def _journal_atomic_write_unlocked(
    operation_hash: str,
    payload: dict[str, Any],
) -> None:
    key = _journal_validate_key(operation_hash)
    _journal_ensure_dir()
    target = _journal_record_path(key)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{key}.",
        suffix=".tmp",
        dir=str(_journal_journal_dir),
        text=True,
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as fh:
            json.dump(
                payload,
                fh,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            fh.flush()
            os.fsync(fh.fileno())

        os.replace(tmp_name, target)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


def _journal_parse_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()

    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _journal_prepared_is_fresh(record: dict[str, Any]) -> bool:
    lease = _journal_parse_utc(record.get("lease_expires_at"))

    if lease is None:
        # Registro PREPARED antiguo/incompleto: fail-safe solo durante
        # un margen basado en updated_at; si tampoco es legible se trata
        # como expirado porque nunca alcanzo intencion de Save.
        updated = _journal_parse_utc(record.get("updated_at"))
        if updated is None:
            return False
        lease = updated + timedelta(seconds=_journal_PREPARED_LEASE_SEC)

    return lease > _journal_utc_now_dt()


def journal_reserve(operation_hash: str) -> dict[str, Any]:
    key = _journal_validate_key(operation_hash)

    with _journal_operation_lock(key):
        current = _journal_read_unlocked(key)

        if current:
            state = str(current.get("state") or "").upper()

            if state in _journal_permanent_block_states:
                return {
                    "ok": False,
                    "blocked": True,
                    "operation_hash": key,
                    "state": state,
                    "attempt": int(current.get("attempt") or 0),
                    "reason": "DURABLE_BLOCK_STATE",
                }

            if state == "PREPARED" and _journal_prepared_is_fresh(current):
                return {
                    "ok": False,
                    "blocked": True,
                    "operation_hash": key,
                    "state": "PREPARED",
                    "attempt": int(current.get("attempt") or 0),
                    "reason": "PREPARED_LEASE_ACTIVE",
                }

            attempt = int(current.get("attempt") or 0) + 1
            created_at = current.get("created_at") or _journal_utc_now()
        else:
            attempt = 1
            created_at = _journal_utc_now()

        now = _journal_utc_now_dt()
        record = {
            "schema_version": _journal_schema_version,
            "operation_hash": key,
            "state": "PREPARED",
            "reason_code": "",
            "attempt": attempt,
            "created_at": created_at,
            "updated_at": now.isoformat(timespec="seconds"),
            "lease_expires_at": (
                now + timedelta(seconds=_journal_PREPARED_LEASE_SEC)
            ).isoformat(timespec="seconds"),
        }

        _journal_atomic_write_unlocked(key, record)

        return {
            "ok": True,
            "blocked": False,
            "operation_hash": key,
            "state": "PREPARED",
            "attempt": attempt,
            "lease_expires_at": record["lease_expires_at"],
        }


def _journal_mark(
    operation_hash: str,
    state: str,
    reason_code: str = "",
) -> dict[str, Any]:
    key = _journal_validate_key(operation_hash)
    state_norm = _journal_norm(state)

    if state_norm not in _journal_valid_states:
        raise ValueError("OT_COMMIT_JOURNAL_STATE_INVALID")

    with _journal_operation_lock(key):
        current = _journal_read_unlocked(key) or {
            "schema_version": _journal_schema_version,
            "operation_hash": key,
            "attempt": 1,
            "created_at": _journal_utc_now(),
        }

        current["schema_version"] = _journal_schema_version
        current["operation_hash"] = key
        current["state"] = state_norm
        current["reason_code"] = _journal_norm(reason_code)[:120]
        current["attempt"] = max(
            1,
            int(current.get("attempt") or 1),
        )
        current["updated_at"] = _journal_utc_now()
        current.setdefault("created_at", _journal_utc_now())

        # Una vez se llega a intencion de Save, el lease PREPARED deja de
        # tener semantica. El estado durable decide el bloqueo.
        if state_norm != "PREPARED":
            current.pop("lease_expires_at", None)

        _journal_atomic_write_unlocked(key, current)
        return dict(current)


def journal_mark_save_intent(operation_hash: str) -> dict[str, Any]:
    return _journal_mark(operation_hash, "SAVE_CLICK_INTENT")


def journal_mark_save_clicked(operation_hash: str) -> dict[str, Any]:
    return _journal_mark(operation_hash, "SAVE_CLICKED")


def journal_mark_save_confirmed(operation_hash: str) -> dict[str, Any]:
    return _journal_mark(operation_hash, "SAVE_CONFIRMED")


def journal_mark_uncertain(
    operation_hash: str,
    reason_code: str = "",
) -> dict[str, Any]:
    return _journal_mark(
        operation_hash,
        "UNCERTAIN",
        reason_code=reason_code,
    )


def journal_mark_failed_safe(
    operation_hash: str,
    reason_code: str = "",
) -> dict[str, Any]:
    return _journal_mark(
        operation_hash,
        "FAILED_SAFE",
        reason_code=reason_code,
    )


# HELIX_WO_RELATED_DRYRUN_V1
# Seguridad: este modulo NO contiene ninguna funcion ni selector operativo
# que haga click sobre "Guardar". Su frontera termina despues de seleccionar
# Descripcion Tipo y leer la WO provisional.

RUNTIME_ROOT = Path(
    r"C:\ProgramData\CentralNOC\Dashboard_Hogar\diagnosticos\helix_wo_related_dryrun"
)

CREATE_RELATED_SELECTORS = [
    'button[data-testid="menu-item-test-id-4"]',
    '[data-testid="menu-item-test-id-4"]',

    'button:has-text("Crear relacionado")',
    '[role="menuitem"]:has-text("Crear relacionado")',
    'button[role="menuitem"]:has-text("Crear relacionado")',
    'a:has-text("Crear relacionado")',
    '[role="button"]:has-text("Crear relacionado")',

    'button:has-text("Create related")',
    '[role="menuitem"]:has-text("Create related")',
    'button[role="menuitem"]:has-text("Create related")',
    'a:has-text("Create related")',
    '[role="button"]:has-text("Create related")',
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
    '[data-testid="304423861"]',
    '[testid="304423861"]',

    'button:has-text("Orden de trabajo")',
    '[role="button"]:has-text("Orden de trabajo")',
    '[role="menuitem"]:has-text("Orden de trabajo")',

    'button:has-text("Work order")',
    '[role="button"]:has-text("Work order")',
    '[role="menuitem"]:has-text("Work order")',
]

CREATE_VIEW_SELECTORS = [
    'h2#ar304421251',

    'h2[aria-label="Crear orden trabajo"]',
    'h2:has-text("Crear orden trabajo")',
    'text="Crear orden trabajo"',

    'h2[aria-label="Create work order"]',
    'h2:has-text("Create work order")',
    'text="Create work order"',
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

    # ATLAS_FAST_INCIDENTPV_FIRST_V1
    # Si el PREVIEW ya expone targetForm=Incident + targetId,
    # promovemos directamente a incidentPV sin esperar 30 segundos.
    fast_target_id = ""
    fast_source_frame_url = ""

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

            fast_target_id = match.group(1).strip()
            fast_source_frame_url = frame_url
            break

        if fast_target_id:
            break

    if fast_target_id:
        fast_direct_url = (
            get_smartit_app_base_url()
            + f"incidentPV/{fast_target_id}"
        )

        logger.info(
            "INC: FAST incidentPV. targetId=%s source=%s",
            fast_target_id,
            fast_source_frame_url,
        )

        try:
            await page.goto(
                fast_direct_url,
                wait_until="domcontentloaded",
                timeout=min(cfg.timeout_ms, 20000),
            )
        except Exception as exc:
            logger.warning(
                "INC: FAST incidentPV termino con espera parcial: %s",
                exc,
            )

        await page.wait_for_timeout(800)

        logger.info(
            "INC: FAST incidentPV completado. " 
            "Se delega Elementos relacionados al siguiente paso."
        )

        return page

    logger.info(
        "INC: targetId no disponible. " 
        "Se mantiene ruta Ver incidencia completa."
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

    # ATLAS_FAST_AFTER_FULL_INCIDENT_V1
    # La siguiente etapa ya busca y pulsa Elementos relacionados.
    # Evitamos repetir aqui una espera de hasta 35 segundos.
    await full_page.wait_for_timeout(700)

    logger.info(
        "INC: vista completa abierta. " 
        "Se delega inmediatamente Elementos relacionados al siguiente paso."
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
    except Exception as exc:
        logger.warning(
            "Elementos relacionados no acepto click normal. No se fuerza. error=%s",
            exc,
        )
        raise RuntimeError(
            "No fue posible abrir Elementos relacionados mediante click normal."
        ) from exc

    await page.wait_for_timeout(900)



# ATLAS_DEBUG_CREATE_RELATED_DOM_V1
async def _debug_create_related_dom(
    page: Page,
    logger: logging.Logger,
) -> None:
    """
    Diagnóstico READ ONLY.
    Inspecciona controles alrededor de 'Crear relacionado'.
    NO hace click y NO modifica la interfaz.
    """

    logger.info("DOM_DEBUG_CREATE_RELATED_START")

    selectors = [
        "button",
        '[role="button"]',
        '[role="menuitem"]',
        "a",
        '[data-testid]',
        '[testid]',
    ]

    seen = set()
    total = 0

    for candidate_page in [
        p for p in page.context.pages if not p.is_closed()
    ]:
        for frame_index, frame in enumerate(list(candidate_page.frames)):

            try:
                frame_url = str(frame.url or "")
            except Exception:
                frame_url = ""

            for selector in selectors:
                try:
                    locators = frame.locator(selector)
                    count = min(await locators.count(), 400)
                except Exception:
                    continue

                for index in range(count):
                    locator = locators.nth(index)

                    try:
                        text = re.sub(
                            r"\s+",
                            " ",
                            str(
                                await locator.inner_text(timeout=500)
                                or ""
                            ),
                        ).strip()
                    except Exception:
                        text = ""

                    attrs = {}

                    for attr in (
                        "data-testid",
                        "testid",
                        "role",
                        "aria-label",
                        "title",
                        "id",
                        "class",
                    ):
                        try:
                            attrs[attr] = str(
                                await locator.get_attribute(attr)
                                or ""
                            ).strip()
                        except Exception:
                            attrs[attr] = ""

                    haystack = " ".join(
                        [
                            text,
                            attrs.get("data-testid", ""),
                            attrs.get("testid", ""),
                            attrs.get("aria-label", ""),
                            attrs.get("title", ""),
                            attrs.get("id", ""),
                            attrs.get("class", ""),
                        ]
                    ).lower()

                    # Nos interesan controles de relacionados/crear
                    # y específicamente el test-id ya conocido.
                    if (
                        "relacion" not in haystack
                        and "related" not in haystack
                        and "crear" not in haystack
                        and "create" not in haystack
                        and "menu-item-test-id-4" not in haystack
                    ):
                        continue

                    try:
                        visible = await locator.is_visible()
                    except Exception:
                        visible = False

                    try:
                        enabled = await locator.is_enabled()
                    except Exception:
                        enabled = False

                    try:
                        tag = await locator.evaluate(
                            "el => el.tagName"
                        )
                    except Exception:
                        tag = ""

                    try:
                        parent_info = await locator.evaluate(
                            """el => {
                                const p = el.parentElement;
                                if (!p) return {};
                                const s = getComputedStyle(p);
                                return {
                                    tag: p.tagName || "",
                                    display: s.display || "",
                                    visibility: s.visibility || "",
                                    hidden: !!p.hidden,
                                    ariaHidden:
                                        p.getAttribute("aria-hidden") || "",
                                    className:
                                        typeof p.className === "string"
                                            ? p.className
                                            : ""
                                };
                            }"""
                        )
                    except Exception:
                        parent_info = {}

                    key = (
                        frame_url,
                        tag,
                        text,
                        attrs.get("data-testid", ""),
                        attrs.get("role", ""),
                    )

                    if key in seen:
                        continue

                    seen.add(key)
                    total += 1

                    logger.info(
                        "DOM_CREATE_CANDIDATE #%s "
                        "frame=%s "
                        "tag=%r visible=%s enabled=%s "
                        "text=%r "
                        "data-testid=%r testid=%r role=%r "
                        "aria-label=%r title=%r id=%r "
                        "parent=%r",
                        total,
                        frame_url,
                        tag,
                        visible,
                        enabled,
                        text[:300],
                        attrs.get("data-testid", ""),
                        attrs.get("testid", ""),
                        attrs.get("role", ""),
                        attrs.get("aria-label", ""),
                        attrs.get("title", ""),
                        attrs.get("id", ""),
                        parent_info,
                    )

    logger.info(
        "DOM_DEBUG_CREATE_RELATED_END total=%s",
        total,
    )



# ATLAS_DEBUG_CREATE_RELATED_STRUCTURE_V1
async def _debug_create_related_structure(
    page: Page,
    logger: logging.Logger,
) -> None:
    """
    READ ONLY.
    Captura la estructura alrededor del menu Crear relacionado.
    NO hace click.
    """

    for candidate_page in [
        p for p in page.context.pages if not p.is_closed()
    ]:
        for frame in list(candidate_page.frames):

            try:
                menus = frame.locator(
                    'adapt-menu#adapt-menu-3, '
                    'adapt-menu[testid="304423831"]'
                )

                count = await menus.count()
            except Exception:
                continue

            for index in range(count):

                menu = menus.nth(index)

                try:
                    data = await menu.evaluate(
                        """el => {
                            const compact = (node) => {
                                if (!node) return null;

                                const attrs = {};
                                for (const a of node.attributes || []) {
                                    attrs[a.name] = a.value;
                                }

                                return {
                                    tag: node.tagName || "",
                                    text: (node.innerText || "")
                                        .replace(/\\s+/g, " ")
                                        .trim()
                                        .slice(0, 500),
                                    attrs,
                                    html: (node.outerHTML || "")
                                        .replace(/\\s+/g, " ")
                                        .slice(0, 2000)
                                };
                            };

                            const parent = el.parentElement;

                            return {
                                self: compact(el),
                                parent: compact(parent),
                                grandparent: compact(
                                    parent ? parent.parentElement : null
                                ),
                                previousSibling: compact(
                                    el.previousElementSibling
                                ),
                                nextSibling: compact(
                                    el.nextElementSibling
                                ),
                                parentChildren: parent
                                    ? Array.from(parent.children)
                                        .slice(0, 20)
                                        .map(compact)
                                    : []
                            };
                        }"""
                    )

                    logger.info(
                        "CREATE_RELATED_STRUCTURE=%s",
                        json.dumps(
                            data,
                            ensure_ascii=False,
                        ),
                    )

                except Exception as exc:
                    logger.warning(
                        "CREATE_RELATED_STRUCTURE_ERROR=%s",
                        exc,
                    )



# ATLAS_CREATE_RELATED_TRIGGER_V2
async def _open_create_related_menu(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
) -> Page:
    """
    Abre EXCLUSIVAMENTE el menu 'Crear relacionado'.

    No crea la OT y nunca pulsa Guardar. Se evita depender de un unico
    data-testid porque SmartIT puede renderizar el mismo control con ids
    distintos segun la vista/version.
    """

    selectors = list(dict.fromkeys(
        CREATE_RELATED_SELECTORS
        + [
            '[aria-label*="Crear relacionado" i]',
            '[title*="Crear relacionado" i]',
            'adapt-menu button',
        ]
    ))

    deadline = time.monotonic() + min(cfg.timeout_ms, 35000) / 1000

    while time.monotonic() < deadline:
        for candidate_page in [
            p for p in page.context.pages if not p.is_closed()
        ]:
            for frame in list(candidate_page.frames):
                for selector in selectors:
                    try:
                        locators = frame.locator(selector)
                        count = min(await locators.count(), 40)
                    except Exception:
                        continue

                    for index in range(count):
                        button = locators.nth(index)

                        try:
                            attached = await button.evaluate(
                                "el => !!el && el.isConnected"
                            )
                        except Exception:
                            attached = False
                        if not attached:
                            continue

                        # Algunos controles no exponen innerText, por eso se
                        # consideran tambien aria-label y title.
                        try:
                            text = re.sub(
                                r"\s+",
                                " ",
                                str(await button.inner_text(timeout=700) or ""),
                            ).strip()
                        except Exception:
                            text = ""

                        try:
                            aria_label = str(
                                await button.get_attribute("aria-label") or ""
                            ).strip()
                        except Exception:
                            aria_label = ""

                        try:
                            title = str(
                                await button.get_attribute("title") or ""
                            ).strip()
                        except Exception:
                            title = ""

                        identity = " ".join(
                            part for part in (text, aria_label, title) if part
                        )

                        identity_norm = _norm(identity)

                        if (
                            "CREAR RELACIONADO" not in identity_norm
                            and "CREATE RELATED" not in identity_norm
                        ):
                            continue

                        try:
                            visible = await button.is_visible()
                        except Exception:
                            visible = False
                        if not visible:
                            logger.info(
                                "CREATE_RELATED_TRIGGER candidato oculto omitido. "
                                "selector=%s frame=%s",
                                selector,
                                frame.url,
                            )
                            continue

                        try:
                            enabled = await button.is_enabled()
                        except Exception:
                            enabled = True
                        if not enabled:
                            logger.info(
                                "CREATE_RELATED_TRIGGER candidato deshabilitado omitido. "
                                "selector=%s frame=%s",
                                selector,
                                frame.url,
                            )
                            continue

                        try:
                            expanded_before = (
                                await button.get_attribute("aria-expanded")
                            )
                        except Exception:
                            expanded_before = ""

                        logger.info(
                            "CREATE_RELATED_TRIGGER encontrado. selector=%s "
                            "text=%r aria-label=%r visible=%s enabled=%s "
                            "aria-expanded-before=%s frame=%s",
                            selector,
                            text,
                            aria_label,
                            visible,
                            enabled,
                            expanded_before,
                            frame.url,
                        )

                        try:
                            await button.scroll_into_view_if_needed(timeout=4000)
                        except Exception:
                            pass

                        try:
                            await button.click(timeout=8000)
                        except Exception as exc:
                            logger.warning(
                                "CREATE_RELATED_TRIGGER click normal fallo. "
                                "selector=%s error=%s",
                                selector,
                                exc,
                            )
                            continue

                        await candidate_page.wait_for_timeout(700)

                        try:
                            expanded_after = (
                                await button.get_attribute("aria-expanded")
                            )
                        except Exception:
                            expanded_after = ""

                        logger.info(
                            "CREATE_RELATED_TRIGGER activado. selector=%s "
                            "aria-expanded-after=%s",
                            selector,
                            expanded_after,
                        )
                        return candidate_page

        await page.wait_for_timeout(250)

    logger.warning(
        "CREATE_RELATED_TRIGGER no pudo activarse. "
        "Capturando diagnostico DOM antes de abortar."
    )

    try:
        await _debug_create_related_dom(page, logger)
    except Exception as exc:
        logger.warning("DEBUG_CREATE_RELATED_DOM_ERROR=%s", exc)

    try:
        await _debug_create_related_structure(page, logger)
    except Exception as exc:
        logger.warning("DEBUG_CREATE_RELATED_STRUCTURE_ERROR=%s", exc)

    try:
        shot_dir = RUNTIME_ROOT
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot_path = shot_dir / (
            "crear_relacionado_fallo_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".png"
        )

        captured = False
        for candidate_page in [
            p for p in page.context.pages if not p.is_closed()
        ]:
            await candidate_page.screenshot(
                path=str(shot_path),
                full_page=True,
            )
            logger.info("CREATE_RELATED_FAILURE_SCREENSHOT=%s", shot_path)
            captured = True
            break

        if not captured:
            logger.warning(
                "CREATE_RELATED_FAILURE_SCREENSHOT=SIN_PAGINA_ABIERTA"
            )
    except Exception as exc:
        logger.warning("CREATE_RELATED_FAILURE_SCREENSHOT_ERROR=%s", exc)

    raise RuntimeError(
        "No se pudo activar el trigger real Crear relacionado."
    )



# ATLAS_INC_STATUS_GATE_V1
async def _read_incident_status(
    page: Page,
    logger: logging.Logger,
) -> str:
    """
    Lee el Estado operativo del INC abierto.

    READ ONLY.
    No hace click.
    No modifica SmartIT.
    """

    known_states = [
        "In Progress",
        "En progreso",
        "En curso",
        "Assigned",
        "Asignado",
        "Pending",
        "Pendiente",
        "Resolved",
        "Resuelto",
        "Closed",
        "Cerrado",
        "Cancelled",
        "Canceled",
        "Cancelado",
    ]

    for candidate_page in [
        p for p in page.context.pages if not p.is_closed()
    ]:
        for frame in list(candidate_page.frames):

            # -------------------------------------------------------------
            # Estrategia 1:
            # localizar texto exacto "Estado" / "Status" y buscar el
            # contenedor más pequeño que también contenga un estado conocido.
            # -------------------------------------------------------------
            for label_text in ("Estado", "Status"):

                try:
                    labels = frame.get_by_text(
                        label_text,
                        exact=True,
                    )

                    count = min(await labels.count(), 20)
                except Exception:
                    continue

                for index in range(count):

                    label = labels.nth(index)

                    try:
                        if not await label.is_visible():
                            continue
                    except Exception:
                        continue

                    try:
                        result = await label.evaluate(
                            """(el, states) => {
                                const norm = value =>
                                    (value || "")
                                    .replace(/\\s+/g, " ")
                                    .trim();

                                let node = el;

                                for (let level = 0; level < 7 && node; level++) {

                                    const text = norm(node.innerText);

                                    for (const state of states) {
                                        const re = new RegExp(
                                            "(?:Estado|Status)\\\\s*" +
                                            state.replace(
                                                /[.*+?^${}()|[\\]\\\\]/g,
                                                "\\\\$&"
                                            ),
                                            "i"
                                        );

                                        if (re.test(text)) {
                                            return {
                                                state,
                                                text: text.slice(0, 500),
                                                level
                                            };
                                        }
                                    }

                                    node = node.parentElement;
                                }

                                return null;
                            }""",
                            known_states,
                        )
                    except Exception:
                        result = None

                    if isinstance(result, dict):
                        state = str(result.get("state") or "").strip()

                        if state:
                            logger.info(
                                "INC_STATUS detectado=%s contexto=%r",
                                state,
                                str(result.get("text") or "")[:300],
                            )
                            return state

            # -------------------------------------------------------------
            # Estrategia 2:
            # controles/labels que directamente expongan el valor.
            # -------------------------------------------------------------
            for state in known_states:

                try:
                    locator = frame.get_by_text(
                        state,
                        exact=True,
                    )

                    count = min(await locator.count(), 20)
                except Exception:
                    continue

                for index in range(count):

                    item = locator.nth(index)

                    try:
                        if not await item.is_visible():
                            continue
                    except Exception:
                        continue

                    try:
                        context = await item.evaluate(
                            """el => {
                                let n = el;
                                for (let i = 0; i < 5 && n; i++) {
                                    const t = (
                                        n.innerText || ""
                                    )
                                    .replace(/\\s+/g, " ")
                                    .trim();

                                    if (
                                        /(?:Estado|Status)/i.test(t)
                                    ) {
                                        return t.slice(0, 500);
                                    }

                                    n = n.parentElement;
                                }
                                return "";
                            }"""
                        )
                    except Exception:
                        context = ""

                    if context:

                        logger.info(
                            "INC_STATUS detectado=%s contexto=%r",
                            state,
                            context[:300],
                        )

                        return state

    logger.warning(
        "INC_STATUS no pudo determinarse de forma confiable."
    )
    return ""


def _incident_status_blocks_create_related(status: str) -> bool:
    normalized = _norm(status)

    return normalized in {
        "CERRADO",
        "CLOSED",
        "CANCELADO",
        "CANCELLED",
        "CANCELED",
    }


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




SAVE_SELECTORS = [
    'button:has-text("Guardar")',
    'button:has-text("Save")',
]

async def _first_visible(
    page: Page,
    selectors: list[str],
    timeout_ms: int,
):
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        for candidate_page in [
            p for p in page.context.pages
            if not p.is_closed()
        ]:
            for frame in list(candidate_page.frames):
                for selector in selectors:
                    try:
                        loc = frame.locator(selector).first
                        if await locator_visible(loc):
                            return candidate_page, frame, loc, selector
                    except Exception:
                        pass

        await page.wait_for_timeout(200)

    return None


async def _read_wo(page: Page, timeout_ms: int) -> str:
    found = await _first_visible(
        page,
        DRAFT_WO_SELECTORS,
        timeout_ms,
    )

    if not found:
        return ""

    _, _, loc, _ = found
    values = []

    try:
        values.append(await loc.inner_text())
    except Exception:
        pass

    for attr in ("value", "title", "aria-label"):
        try:
            values.append(await loc.get_attribute(attr))
        except Exception:
            pass

    for value in values:
        match = re.search(
            r"WO\d{13,14}",
            str(value or ""),
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(0).upper()

    return ""


async def _verify_saved_work_order(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    work_order: str,
) -> bool:
    if not _WO_RE.fullmatch(work_order):
        return False

    await page.wait_for_timeout(1800)

    home = get_smartit_url()

    try:
        await page.goto(
            home,
            wait_until="domcontentloaded",
            timeout=45000,
        )
    except Exception as exc:
        logger.warning(
            "Verificación: navegación a consola terminó parcial: %s",
            exc,
        )

    await page.wait_for_timeout(800)

    try:
        await click_global_search(
            page,
            cfg,
            logger,
            work_order,
        )

        await open_work_order(
            page,
            cfg,
            logger,
            work_order,
        )

        logger.info(
            "Verificación OK: WO creada encontrada y abierta: %s",
            work_order,
        )
        return True

    except Exception as exc:
        logger.exception(
            "No se pudo verificar la WO %s tras Guardar: %s",
            work_order,
            exc,
        )
        return False




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

        result["error"] = (
            "Boton Integracion OFsC no disponible."
        )

        result["duracion_seg"] = round(
            time.monotonic() - started,
            2,
        )

        logger.info(
            "Ruta OFsC no disponible; se usara fallback."
        )

        return result

    _, button, selector = found_button

    result["disponible"] = True

    logger.info(
        "Abriendo Integracion OFsC con %s",
        selector,
    )

    try:

        try:
            await button.scroll_into_view_if_needed(
                timeout=3000
            )
        except Exception:
            pass

        try:
            await button.click(
                timeout=7000
            )

        except Exception:
            await button.click(
                force=True,
                timeout=7000,
            )

        found_panel = await _first_visible_across_frames(
            page,
            OFSC_PANEL_SELECTORS,
            10000,
        )

        if not found_panel:

            result["error"] = (
                "El panel WOI:WorkOrder no aparecio."
            )

            return result

        _, panel, panel_selector = found_panel

        logger.info(
            "Panel OFsC detectado con %s",
            panel_selector,
        )

        for field, selector_field in OFSC_FIELDS.items():

            try:

                field_locator = panel.locator(
                    selector_field
                ).first

                if await field_locator.count():

                    result[field] = await _input_value(
                        field_locator
                    )

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

        result["error"] = (
            f"{type(exc).__name__}: {exc}"
        )

        logger.warning(
            "Fallo ruta rapida OFsC: %s",
            result["error"],
        )

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

                    await close.click(
                        timeout=5000
                    )

                    logger.info(
                        "Panel OFsC cerrado."
                    )

        except Exception as exc:

            logger.warning(
                "No se pudo cerrar panel OFsC: %s",
                exc,
            )

        result["duracion_seg"] = round(
            time.monotonic() - started,
            2,
        )



# Compatibilidad interna: el manager historico referenciaba dry.* y commit_rt.*.
dry = sys.modules[__name__]
commit_rt = sys.modules[__name__]

_WO_RE = re.compile(r"^WO\d{13,14}$", re.IGNORECASE)
_ALLOWED_TYPES = {"FIBRA", "COAX"}
SESSION_TTL_SECONDS = max(
    30,
    int(os.getenv("ATLAS_OT_CONFIRM_TTL_SECONDS", "120")),
)

LIVE_ROOT = Path(
    r"C:\ProgramData\CentralNOC\Dashboard_Hogar"
    r"\diagnosticos\helix_wo_related_live"
)


# ATLAS_OT_USER_AUDIT_SANITIZE_V1
AUDIT_ROOT = Path(
    r"C:\ProgramData\CentralNOC\Dashboard_Hogar\data\helix_ot_fibra_coaxial"
)

AUDIT_FILE = (
    AUDIT_ROOT
    / "ot_user_audit.jsonl"
)


def _audit_user_event(
    *,
    event: str,
    username: str,
    wo: str,
    tipo: str,
    incident: str = "",
    provisional: str = "",
    created_wo: str = "",
    result_code: str = "",
) -> None:
    """
    Auditoria operacional OT.

    Nunca recibe ni persiste password.
    """
    try:
        AUDIT_ROOT.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "timestamp": datetime.now().isoformat(
                timespec="seconds"
            ),
            "event": str(event or "").strip(),
            "authenticated_user": str(
                username or ""
            ).strip(),
            "wo": str(wo or "").strip().upper(),
            "tipo": str(tipo or "").strip().upper(),
            "incident": str(
                incident or ""
            ).strip().upper(),
            "provisional": str(
                provisional or ""
            ).strip().upper(),
            "created_wo": str(
                created_wo or ""
            ).strip().upper(),
            "result_code": str(
                result_code or ""
            ).strip(),
        }

        with AUDIT_FILE.open(
            "a",
            encoding="utf-8",
        ) as fh:
            fh.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    except Exception:
        # La auditoria nunca debe bloquear la operacion.
        pass


@dataclass
class LiveSession:
    session_id: str
    authenticated_user: str
    wo: str
    tipo: str
    incident: str
    provisional: str
    description_type: str
    created_monotonic: float
    expires_monotonic: float
    playwright: Playwright
    context: BrowserContext
    page: Page
    cfg: Settings
    logger: logging.Logger
    run_dir: Path
    status: str = "READY_TO_CONFIRM"
    expiry_task: asyncio.Task | None = None

    def seconds_left(self) -> int:
        return max(0, int(self.expires_monotonic - time.monotonic()))


class LiveOtSessionManager:
    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._active: LiveSession | None = None

    @staticmethod
    def _norm_type(value: str) -> str:
        tipo = str(value or "").strip().upper()
        if tipo == "COAXIAL":
            tipo = "COAX"
        return tipo

    @staticmethod
    def _settings_with_credentials(
        username: str,
        password: str,
    ) -> Settings:
        cfg = Settings.from_env()
        user = str(username or "").strip()
        pwd = str(password or "")

        if bool(user) != bool(pwd):
            raise ValueError("CREDENCIALES_HELIX_INCOMPLETAS")

        if user and pwd:
            cfg = replace(cfg, username=user, password=pwd)
        return cfg

    @staticmethod
    def _logger(run_dir: Path) -> logging.Logger:
        run_dir.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"helix_wo_related_live_{run_dir.name}")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        fh = logging.FileHandler(run_dir / "live_session.log", encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        return logger

    @staticmethod
    def _base_failure(
        wo: str,
        tipo: str,
        codigo: str,
        error: str,
        *,
        incident: str = "",
        provisional: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "tipo_respuesta": "helix_ot_relacionada_dryrun",
            "codigo": codigo,
            "modo": "DRY_RUN",
            "safe_mode": True,
            "allow_save_click": False,
            "reportar_como_creada": False,
            "wo_origen": wo,
            "tipo": tipo,
            "incidente_relacionado": incident,
            "wo_provisional": provisional,
            "descripcion_tipo_seleccionada": "",
            "guardar_bloqueado": False,
            "session_id": "",
            "expires_in_seconds": 0,
            "error": error,
        }

    @staticmethod
    def _commit_failure(
        wo: str,
        tipo: str,
        incident: str,
        provisional: str,
        codigo: str,
        error: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "codigo": codigo,
            "modo": "COMMIT_CONFIRMADO",
            "wo_origen": wo,
            "tipo": tipo,
            "incidente_relacionado": incident,
            "wo_provisional": provisional,
            "wo_creada": "",
            "descripcion_tipo_seleccionada": "",
            "save_clicked": False,
            "save_confirmed": False,
            "error": error,
        }

    async def _detach_active(self, expected: LiveSession | None = None) -> LiveSession | None:
        async with self._guard:
            current = self._active
            if current is None:
                return None
            if expected is not None and current is not expected:
                return None
            self._active = None
            return current

    async def _close_resources(self, session: LiveSession) -> None:
        current_task = asyncio.current_task()
        task = session.expiry_task
        if task and task is not current_task and not task.done():
            task.cancel()

        try:
            if not session.context.pages or all(p.is_closed() for p in session.context.pages):
                pass
            await session.context.close()
        except Exception as exc:
            session.logger.warning("Cierre BrowserContext parcial: %s", exc)

        try:
            await session.playwright.stop()
        except Exception as exc:
            session.logger.warning("Cierre Playwright parcial: %s", exc)

    async def _expire_after_ttl(self, session: LiveSession) -> None:
        try:
            await asyncio.sleep(SESSION_TTL_SECONDS)
            async with self._guard:
                if self._active is not session:
                    return
                if session.status != "READY_TO_CONFIRM":
                    return
                session.status = "EXPIRED"
                self._active = None

            session.logger.warning(
                "Sesion expirada tras %s segundos. WO=%s INC=%s provisional=%s",
                SESSION_TTL_SECONDS,
                session.wo,
                session.incident,
                session.provisional,
            )
            await self._close_resources(session)
        except asyncio.CancelledError:
            return

    def snapshot(self) -> dict[str, Any]:
        session = self._active
        if not session:
            return {
                "active": False,
                "status": "IDLE",
                "expires_in_seconds": 0,
            }
        return {
            "active": True,
            "status": session.status,
            "wo": session.wo,
            "tipo": session.tipo,
            "incidente_relacionado": session.incident,
            "wo_provisional": session.provisional,
            "authenticated_user": session.authenticated_user,
            "expires_in_seconds": session.seconds_left(),
        }

    async def prepare(
        self,
        wo: str,
        tipo: str,
        *,
        helix_username: str = "",
        helix_password: str = "",
    ) -> dict[str, Any]:
        wo = str(wo or "").strip().upper()
        tipo = self._norm_type(tipo)

        if not _WO_RE.fullmatch(wo):
            return self._base_failure(wo, tipo, "DRYRUN_WO_INVALIDA", "WO invalida.")
        if tipo not in _ALLOWED_TYPES:
            return self._base_failure(
                wo, tipo, "DRYRUN_TIPO_INVALIDO", "Tipo permitido: FIBRA o COAX."
            )

        # Una sola automatizacion Helix viva a la vez.
        async with self._guard:
            active = self._active
            if active and active.status in {"PREPARING", "READY_TO_CONFIRM", "COMMITTING"}:
                return self._base_failure(
                    wo,
                    tipo,
                    "DRYRUN_HELIX_OCUPADO",
                    (
                        "Hay una OT esperando confirmacion o siendo procesada. "
                        f"Estado={active.status}; vence_en={active.seconds_left()}s."
                    ),
                )

        try:
            cfg = self._settings_with_credentials(helix_username, helix_password)
        except ValueError:
            return self._base_failure(
                wo,
                tipo,
                "DRYRUN_CREDENCIALES_INCOMPLETAS",
                "Usuario y contrasena Helix deben enviarse juntos.",
            )
        except Exception as exc:
            return self._base_failure(
                wo,
                tipo,
                "DRYRUN_CONFIG_ERROR",
                f"{type(exc).__name__}: {exc}",
            )

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = LIVE_ROOT / f"{stamp}_{wo}_{tipo}"
        logger = self._logger(run_dir)

        playwright: Playwright | None = None
        context: BrowserContext | None = None
        session: LiveSession | None = None
        payload = self._base_failure(wo, tipo, "DRYRUN_INICIADO", "")
        payload["output_dir"] = str(run_dir)
        payload["descripcion_tipo_query"] = dry.TYPE_CONFIG[tipo]["query"]

        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            playwright = await async_playwright().start()
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=bool(
                    str(os.getenv("ATLAS_OT_DRYRUN_HEADLESS", os.getenv("HELIX_CHAT_HEADLESS", "false")))
                    .strip()
                    .lower()
                    in {"1", "true", "yes", "si", "sí", "on"}
                ),
                slow_mo=cfg.slow_mo_ms,
                accept_downloads=False,
                viewport={"width": 1600, "height": 950},
                args=["--start-maximized", "--disable-notifications"],
            )
            context.set_default_timeout(cfg.timeout_ms)
            context.set_default_navigation_timeout(cfg.timeout_ms)
            page = context.pages[0] if context.pages else await context.new_page()

            logger.info(
                "LIVE PREPARE iniciado. WO=%s tipo=%s",
                wo,
                tipo,
            )

            authenticated_user = str(
                getattr(
                    cfg,
                    "username",
                    "",
                )
                or ""
            ).strip()

            await iniciar_sesion(
                page,
                cfg,
                logger,
            )

            # ATLAS_OT_USER_AUDIT_SANITIZE_V1
            #
            # Desde este punto la autenticacion ya termino.
            # La password no es necesaria durante el TTL ni el commit.
            # Se reemplaza por cadena vacia antes de persistir cfg
            # dentro de LiveSession.
            cfg = replace(
                cfg,
                password="",
            )

            logger.info(
                "HELIX_AUTH_OK user=%s password_retained=false",
                authenticated_user,
            )

            await click_global_search(
                page,
                cfg,
                logger,
                wo,
            )
            page = await open_work_order(page, cfg, logger, wo)

            canonical_incident = ""
            try:
                ofsc_hint = await intentar_integracion_ofsc(page, logger)
                canonical_incident = str(
                    ofsc_hint.get("incidente_relacionado") or ""
                ).strip().upper()
            except Exception as ofsc_exc:
                logger.warning("LIVE PREPARE sin hint OFSC: %s", ofsc_exc)

            _, related_table, _, related_items = await open_related_tab_and_read(
                page, cfg, logger
            )
            if not related_items:
                raise RuntimeError("La WO origen no tiene incidentes relacionados.")

            incident = None
            incident_source = ""
            if canonical_incident:
                incident = next(
                    (
                        item
                        for item in related_items
                        if str(item.id or "").strip().upper() == canonical_incident
                    ),
                    None,
                )
                if incident is None:
                    related_ids = [
                        str(item.id or "").strip().upper()
                        for item in related_items
                        if str(item.id or "").strip()
                    ]
                    raise RuntimeError(
                        "INC_CANONICO_OFSC_NO_PRESENTE_EN_RELACIONADOS: "
                        f"canonical={canonical_incident} related={related_ids}"
                    )
                incident_source = "OFSC"

            if incident is None:
                incident = next(
                    (
                        item
                        for item in related_items
                        if dry._norm(item.tipo_relacion) == "CREADO POR"
                    ),
                    None,
                )
                if incident is not None:
                    incident_source = "RELACIONADOS_CREADO_POR"

            if incident is None:
                incident = related_items[0]
                incident_source = "RELACIONADOS_PRIMERO"

            payload["incidente_relacionado"] = incident.id
            payload["incidente_fuente"] = incident_source

            await click_incident_in_table(related_table, incident.id, page, cfg, logger)

            incident_status = await dry._read_incident_status(page, logger)
            payload["estado_incidente"] = incident_status
            if dry._incident_status_blocks_create_related(incident_status):
                raise RuntimeError(
                    f"INCIDENTE_NO_HABILITADO_PARA_CREAR_OT:{incident.id}:{incident_status}"
                )

            page = await dry._open_full_incident_view(page, cfg, logger)
            await dry._click_related_tab_on_incident(page, cfg, logger)
            page = await dry._open_create_related_menu(page, cfg, logger)

            found_wo_menu = await dry._first_visible_across_context(
                page,
                dry.WORK_ORDER_MENU_SELECTORS,
                min(cfg.timeout_ms, 20000),
            )
            if not found_wo_menu:
                raise RuntimeError("No se encontro Orden de trabajo en Crear relacionado.")
            page, _, wo_menu, _ = found_wo_menu
            await wo_menu.click(timeout=10000)

            found_form = await dry._first_visible_across_context(
                page,
                dry.CREATE_VIEW_SELECTORS,
                min(cfg.timeout_ms, 45000),
            )
            if not found_form:
                raise RuntimeError("No aparecio la vista Crear orden trabajo.")
            page, _, _, _ = found_form

            blocked_initial = await dry._disable_save_buttons(page, logger)
            payload["guardar_bloqueado"] = blocked_initial > 0

            found_draft = await dry._first_visible_across_context(
                page,
                dry.DRAFT_WO_SELECTORS,
                min(cfg.timeout_ms, 15000),
            )
            if not found_draft:
                raise RuntimeError("No se pudo leer WO provisional.")
            page, _, draft_locator, _ = found_draft
            draft_text = (await dry._read_text(draft_locator)).upper()
            match = re.search(r"WO\d{13,14}", draft_text)
            if not match:
                raise RuntimeError("WO provisional invalida o vacia.")
            provisional = match.group(0).upper()
            payload["wo_provisional"] = provisional

            type_result = await dry._select_description_type(page, tipo, cfg, logger)
            page = type_result["page"]
            selected_type = str(type_result["option"] or "").strip()
            payload["descripcion_tipo_seleccionada"] = selected_type
            payload["guardar_bloqueado"] = bool(
                payload["guardar_bloqueado"]
                or type_result["save_buttons_blocked"] > 0
            )

            final_blocked = await dry._disable_save_buttons(page, logger)
            payload["guardar_bloqueado"] = bool(
                payload["guardar_bloqueado"] or final_blocked > 0
            )
            if not payload["guardar_bloqueado"]:
                raise RuntimeError("BARRERA_GUARDAR_NO_CONFIRMADA")

            screenshot_path = run_dir / "final_antes_de_guardar.png"
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
                payload["screenshot"] = str(screenshot_path)
            except Exception as screenshot_exc:
                payload["screenshot_error"] = str(screenshot_exc)

            session_id = secrets.token_urlsafe(24)
            now = time.monotonic()
            session = LiveSession(
                session_id=session_id,
                authenticated_user=authenticated_user,
                wo=wo,
                tipo=tipo,
                incident=str(incident.id or "").strip().upper(),
                provisional=provisional,
                description_type=selected_type,
                created_monotonic=now,
                expires_monotonic=now + SESSION_TTL_SECONDS,
                playwright=playwright,
                context=context,
                page=page,
                cfg=cfg,
                logger=logger,
                run_dir=run_dir,
            )

            async with self._guard:
                if self._active is not None:
                    raise RuntimeError("OT_LIVE_SESSION_RACE_ACTIVE")
                self._active = session

            session.expiry_task = asyncio.create_task(self._expire_after_ttl(session))

            payload.update({
                "ok": True,
                "codigo": "DRYRUN_LISTO_ANTES_DE_GUARDAR",
                "modo": "DRY_RUN",
                "safe_mode": True,
                "allow_save_click": False,
                "reportar_como_creada": False,
                "session_id": session_id,
                "authenticated_user": authenticated_user,
                "session_status": "READY_TO_CONFIRM",
                "expires_in_seconds": SESSION_TTL_SECONDS,
                "error": "",
            })
            _audit_user_event(
                event="PREPARED",
                username=authenticated_user,
                wo=wo,
                tipo=tipo,
                incident=session.incident,
                provisional=provisional,
                result_code="DRYRUN_OK",
            )

            logger.warning(
                "LIVE READY. session=%s WO=%s INC=%s provisional=%s TTL=%ss",
                session_id,
                wo,
                session.incident,
                provisional,
                SESSION_TTL_SECONDS,
            )
            return payload

        except Exception as exc:
            text = str(exc)
            if text.startswith("INCIDENTE_NO_HABILITADO_PARA_CREAR_OT:"):
                parts = text.split(":", 2)
                incident_id = parts[1] if len(parts) > 1 else ""
                status = parts[2] if len(parts) > 2 else ""
                payload["codigo"] = "INCIDENTE_NO_HABILITADO_PARA_CREAR_OT"
                payload["incidente_relacionado"] = incident_id
                payload["estado_incidente"] = status
                payload["error"] = (
                    f"El incidente {incident_id} tiene estado {status}. "
                    "SmartIT no permite Crear relacionado."
                )
            else:
                payload["codigo"] = "DRYRUN_FALLIDO"
                payload["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("LIVE PREPARE fallido: %s", exc)

            if session is not None:
                await self._detach_active(session)
                await self._close_resources(session)
            else:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                if playwright is not None:
                    try:
                        await playwright.stop()
                    except Exception:
                        pass
            return payload

    async def _enable_save_buttons(self, page: Page, logger: logging.Logger) -> int:
        enabled = 0
        for candidate_page in [p for p in page.context.pages if not p.is_closed()]:
            for frame in list(candidate_page.frames):
                for selector in dry.SAVE_BUTTON_SELECTORS:
                    try:
                        locators = frame.locator(selector)
                        count = min(await locators.count(), 20)
                    except Exception:
                        continue
                    for index in range(count):
                        button = locators.nth(index)
                        try:
                            changed = await button.evaluate(
                                """el => {
                                    if (el.dataset.atlasDryrunBlocked !== 'true') {
                                        return false;
                                    }
                                    el.removeAttribute('disabled');
                                    el.removeAttribute('aria-disabled');
                                    el.style.pointerEvents = '';
                                    delete el.dataset.atlasDryrunBlocked;
                                    return true;
                                }"""
                            )
                            if changed:
                                enabled += 1
                        except Exception:
                            continue
        logger.warning("Barrera Guardar retirada en %s elemento(s).", enabled)
        return enabled

    async def commit(
        self,
        wo: str,
        tipo: str,
        *,
        expected_incident: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        wo = str(wo or "").strip().upper()
        tipo = self._norm_type(tipo)
        expected_inc = str(expected_incident or "").strip().upper()
        requested_session = str(session_id or "").strip()

        async with self._guard:
            session = self._active
            if session is None:
                return self._commit_failure(
                    wo,
                    tipo,
                    expected_inc,
                    "",
                    "OT_SESSION_EXPIRED_OR_NOT_FOUND",
                    "La sesion de confirmacion no existe o ya expiro. Debe repetir el proceso.",
                )

            if session.status != "READY_TO_CONFIRM":
                return self._commit_failure(
                    wo,
                    tipo,
                    expected_inc or session.incident,
                    session.provisional,
                    "OT_SESSION_NOT_READY",
                    f"La sesion esta en estado {session.status}.",
                )

            if time.monotonic() >= session.expires_monotonic:
                session.status = "EXPIRED"
                self._active = None
                expired = session
            else:
                expired = None

            if expired is None:
                if requested_session and requested_session != session.session_id:
                    return self._commit_failure(
                        wo,
                        tipo,
                        expected_inc or session.incident,
                        session.provisional,
                        "OT_SESSION_ID_MISMATCH",
                        "La confirmacion no corresponde a la sesion activa.",
                    )
                if wo != session.wo or tipo != session.tipo:
                    return self._commit_failure(
                        wo,
                        tipo,
                        expected_inc or session.incident,
                        session.provisional,
                        "OT_SESSION_REQUEST_MISMATCH",
                        "WO/tipo no corresponden a la sesion preparada.",
                    )
                if expected_inc and expected_inc != session.incident:
                    return self._commit_failure(
                        wo,
                        tipo,
                        expected_inc,
                        session.provisional,
                        "OT_SESSION_INC_MISMATCH",
                        "El INC confirmado no corresponde a la sesion preparada.",
                    )

                session.status = "COMMITTING"
                if session.expiry_task and not session.expiry_task.done():
                    session.expiry_task.cancel()

        if expired is not None:
            await self._close_resources(expired)
            return self._commit_failure(
                wo,
                tipo,
                expected_inc or expired.incident,
                expired.provisional,
                "OT_SESSION_EXPIRED",
                "Pasaron mas de 120 segundos. El navegador fue cerrado; repita el proceso.",
            )

        # Desde aqui la confirmacion fue aceptada dentro del TTL.
        operation_hash = journal_operation_key(
            session.wo,
            session.tipo,
            session.incident,
        )
        result = self._commit_failure(
            session.wo,
            session.tipo,
            session.incident,
            session.provisional,
            "COMMIT_FALLIDO",
            "",
        )
        result["descripcion_tipo_seleccionada"] = session.description_type
        result["session_id"] = session.session_id
        result["authenticated_user"] = session.authenticated_user
        save_intent = False

        try:
            reservation = journal_reserve(operation_hash)
            if not reservation.get("ok"):
                result["codigo"] = "COMMIT_DUPLICATE_BLOCKED"
                result["error"] = (
                    "Existe una operacion previa bloqueante para esta WO/tipo/INC. "
                    "No se ejecutara Guardar nuevamente."
                )
                return result

            page = session.page
            if page.is_closed():
                journal_mark_failed_safe(operation_hash, "LIVE_PAGE_CLOSED_BEFORE_SAVE")
                result["codigo"] = "OT_SESSION_PAGE_CLOSED"
                result["error"] = "El navegador de confirmacion ya no esta disponible."
                return result

            current_provisional = await commit_rt._read_wo(
                page,
                min(session.cfg.timeout_ms, 5000),
            )
            if current_provisional != session.provisional:
                journal_mark_failed_safe(operation_hash, "PROVISIONAL_CHANGED_BEFORE_SAVE")
                result["codigo"] = "OT_PROVISIONAL_CHANGED"
                result["error"] = (
                    "La WO provisional cambio antes de Guardar. "
                    f"Inicial={session.provisional} Actual={current_provisional}"
                )
                return result

            enabled = await self._enable_save_buttons(page, session.logger)
            if enabled <= 0:
                journal_mark_failed_safe(operation_hash, "SAVE_BARRIER_NOT_RELEASED")
                result["codigo"] = "OT_SAVE_BARRIER_NOT_RELEASED"
                result["error"] = "No fue posible retirar la barrera de Guardar."
                return result

            found_save = await commit_rt._first_visible(
                page,
                commit_rt.SAVE_SELECTORS,
                min(session.cfg.timeout_ms, 12000),
            )
            if not found_save:
                journal_mark_failed_safe(operation_hash, "SAVE_BUTTON_NOT_FOUND")
                result["codigo"] = "OT_SAVE_BUTTON_NOT_FOUND"
                result["error"] = "No se encontro el boton Guardar en la sesion preparada."
                return result

            _, _, save_btn, save_selector = found_save
            confirm_provisional = await commit_rt._read_wo(
                page,
                min(session.cfg.timeout_ms, 5000),
            )
            if confirm_provisional != session.provisional:
                journal_mark_failed_safe(operation_hash, "PROVISIONAL_CHANGED_AFTER_UNLOCK")
                result["codigo"] = "OT_PROVISIONAL_CHANGED"
                result["error"] = "La WO provisional cambio al retirar la barrera de Guardar."
                return result

            session.logger.warning(
                "CLICK GUARDAR AUTORIZADO. session=%s WO=%s INC=%s provisional=%s tipo=%s selector=%s",
                session.session_id,
                session.wo,
                session.incident,
                session.provisional,
                session.tipo,
                save_selector,
            )

            journal_mark_save_intent(operation_hash)
            save_intent = True
            await save_btn.click(timeout=12000)
            result["save_clicked"] = True
            journal_mark_save_clicked(operation_hash)

            await wait_no_loader(
                page,
                min(session.cfg.timeout_ms, 45000),
                session.logger,
            )
            await page.wait_for_timeout(1200)

            final_wo = await commit_rt._read_wo(
                page,
                min(session.cfg.timeout_ms, 8000),
            ) or session.provisional
            result["wo_creada"] = final_wo

            verified = await commit_rt._verify_saved_work_order(
                page,
                session.cfg,
                session.logger,
                final_wo,
            )
            result["save_confirmed"] = bool(verified)

            if verified:
                journal_mark_save_confirmed(operation_hash)
                result["ok"] = True
                result["codigo"] = "OT_CREADA_VERIFICADA"

                _audit_user_event(
                    event="COMMIT_CONFIRMED",
                    username=session.authenticated_user,
                    wo=session.wo,
                    tipo=session.tipo,
                    incident=session.incident,
                    provisional=session.provisional,
                    created_wo=final_wo,
                    result_code="OT_CREADA_VERIFICADA",
                )
                result["error"] = ""
                session.status = "CREATED"
            else:
                journal_mark_uncertain(operation_hash, "SAVE_CLICKED_NOT_VERIFIED")
                result["codigo"] = "OT_GUARDAR_EJECUTADO_NO_VERIFICADO"
                result["error"] = (
                    "Guardar fue ejecutado, pero ATLAS no pudo verificar la WO despues. "
                    "NO reintentar automaticamente."
                )
                session.status = "UNCERTAIN"

            return result

        except Exception as exc:
            if save_intent or result.get("save_clicked"):
                journal_mark_uncertain(operation_hash, "LIVE_EXCEPTION_AFTER_SAVE_INTENT")
            else:
                try:
                    journal_mark_failed_safe(operation_hash, "LIVE_EXCEPTION_BEFORE_SAVE")
                except Exception:
                    pass
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["codigo"] = (
                "OT_GUARDAR_RESULTADO_INCIERTO"
                if save_intent
                else "COMMIT_SERVICE_ERROR"
            )
            session.logger.exception("LIVE COMMIT fallido: %s", exc)
            return result

        finally:
            await self._detach_active(session)
            await self._close_resources(session)

    async def cancel(
        self,
        *,
        wo: str = "",
        tipo: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        requested_wo = str(wo or "").strip().upper()
        requested_type = self._norm_type(tipo) if tipo else ""
        requested_id = str(session_id or "").strip()

        async with self._guard:
            session = self._active
            if not session:
                return {
                    "ok": True,
                    "codigo": "OT_SESSION_NO_ACTIVA",
                    "cancelled": False,
                    "message": "No hay una sesion OT activa.",
                }
            if session.status == "COMMITTING":
                return {
                    "ok": False,
                    "codigo": "OT_SESSION_COMMITTING",
                    "cancelled": False,
                    "message": "La OT ya esta en proceso de Guardar y no puede cancelarse.",
                }
            if requested_id and requested_id != session.session_id:
                return {
                    "ok": False,
                    "codigo": "OT_SESSION_ID_MISMATCH",
                    "cancelled": False,
                    "message": "La sesion indicada no corresponde a la sesion activa.",
                }
            if requested_wo and requested_wo != session.wo:
                return {
                    "ok": False,
                    "codigo": "OT_SESSION_REQUEST_MISMATCH",
                    "cancelled": False,
                    "message": "La WO indicada no corresponde a la sesion activa.",
                }
            if requested_type and requested_type != session.tipo:
                return {
                    "ok": False,
                    "codigo": "OT_SESSION_REQUEST_MISMATCH",
                    "cancelled": False,
                    "message": "El tipo indicado no corresponde a la sesion activa.",
                }
            session.status = "CANCELLED"
            self._active = None

        await self._close_resources(session)
        return {
            "ok": True,
            "codigo": "OT_SESSION_CANCELLED",
            "cancelled": True,
            "wo_origen": session.wo,
            "tipo": session.tipo,
            "incidente_relacionado": session.incident,
            "wo_provisional": session.provisional,
        }

    async def shutdown(self) -> None:
        session = await self._detach_active()
        if session:
            session.status = "SHUTDOWN"
            await self._close_resources(session)


live_ot_session_manager = LiveOtSessionManager()

