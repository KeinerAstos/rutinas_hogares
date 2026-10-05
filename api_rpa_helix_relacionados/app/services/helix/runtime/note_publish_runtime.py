from __future__ import annotations

import logging
import re
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from app.services.helix.runtime.helix_runner import (
    PROFILE_DIR,
    click_global_search,
    click_incident_in_table,
    normalize_text,
    open_related_tab_and_read,
    open_work_order,
)

from app.services.helix.smartit_scraper import (
    Settings,
    iniciar_sesion,
)


# ATLAS_HELIX_NOTE_PRECOMMIT_RUNTIME_8023_V1

INC_RE = re.compile(r"^INC\d+$", re.I)

SERVICE_ROOT = Path(__file__).resolve().parents[4]

RUN_ROOT = (
    SERVICE_ROOT
    / "runtime"
    / "diagnostics"
    / "helix_note_publish"
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        _clean(value),
    ).upper()


def _logger(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger(
        "helix_note_precommit_"
        + run_dir.name
    )

    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    run_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        run_dir / "precommit.log",
        encoding="utf-8",
    )

    stream_handler = logging.StreamHandler()

    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger


async def _body_text_content(frame) -> str:
    try:
        body = frame.locator("body")
        if not await body.count():
            return ""

        return str(
            await body.text_content(
                timeout=2500
            )
            or ""
        )
    except Exception:
        return ""


async def _find_incident_frame(
    page,
    incident_id: str,
    logger=None,
):
    # ATLAS_HELIX_VISIBLE_INCIDENT_FRAME_8023_V1

    wanted = _norm(incident_id)

    matches = []

    for index, frame in enumerate(list(page.frames)):

        try:
            url = str(frame.url or "")
        except Exception:
            url = ""

        if "TARGETFORM=INCIDENT" not in url.upper():
            continue

        body_text = await _body_text_content(frame)

        if not wanted or wanted not in _norm(body_text):
            continue

        editor_visible = False
        note_type_visible = False
        public_visible = False
        publication_visible = False

        try:
            editor = frame.locator(
                '[id="304247080"]'
            )

            for i in range(await editor.count()):
                try:
                    if await editor.nth(i).is_visible(
                        timeout=1000
                    ):
                        editor_visible = True
                        break
                except Exception:
                    pass
        except Exception:
            pass

        try:
            note_type = frame.locator(
                '[id="rx-select-217"]'
            )

            for i in range(await note_type.count()):
                try:
                    if await note_type.nth(i).is_visible(
                        timeout=1000
                    ):
                        note_type_visible = True
                        break
                except Exception:
                    pass
        except Exception:
            pass

        try:
            public = frame.locator(
                '[id="rx-checkbox-219"]'
            )

            for i in range(await public.count()):
                try:
                    if await public.nth(i).is_visible(
                        timeout=1000
                    ):
                        public_visible = True
                        break
                except Exception:
                    pass
        except Exception:
            pass

        try:
            publication = frame.locator(
                '[id="304268430"]'
            )

            for i in range(await publication.count()):
                try:
                    if await publication.nth(i).is_visible(
                        timeout=1000
                    ):
                        publication_visible = True
                        break
                except Exception:
                    pass
        except Exception:
            pass

        score = (
            (8 if editor_visible else 0)
            + (4 if note_type_visible else 0)
            + (2 if public_visible else 0)
            + (1 if publication_visible else 0)
        )

        info = {
            "frame": frame,
            "url": url,
            "index": index,
            "score": score,
            "editor_visible": editor_visible,
            "note_type_visible": note_type_visible,
            "public_visible": public_visible,
            "publication_visible": publication_visible,
        }

        matches.append(info)

        if logger is not None:
            logger.info(
                "INC_FRAME_CANDIDATE "
                "index=%s score=%s editor=%s "
                "note_type=%s public=%s publication=%s url=%s",
                index,
                score,
                editor_visible,
                note_type_visible,
                public_visible,
                publication_visible,
                url[:300],
            )

    if not matches:
        return None

    matches.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    best = matches[0]

    if logger is not None:
        logger.info(
            "INC_FRAME_SELECTED "
            "index=%s score=%s editor=%s "
            "note_type=%s public=%s publication=%s",
            best["index"],
            best["score"],
            best["editor_visible"],
            best["note_type_visible"],
            best["public_visible"],
            best["publication_visible"],
        )

    # Exigimos al menos editor visible.
    if not best["editor_visible"]:
        return None

    return best["frame"], best["url"]


async def _element_info(locator):
    if not await locator.count():
        return None

    loc = locator.first

    try:
        visible = await loc.is_visible()
    except Exception:
        visible = False

    try:
        disabled = await loc.is_disabled()
    except Exception:
        disabled = None

    data = await loc.evaluate(
        """el => ({
          tag: el.tagName || '',
          id: el.id || '',
          name: el.getAttribute('name') || '',
          text: (el.innerText || el.textContent || '').trim(),
          value: ('value' in el ? String(el.value || '') : ''),
          checked: ('checked' in el ? !!el.checked : null)
        })"""
    )

    data["visible"] = visible
    data["disabled"] = disabled

    return data


def _clone_profile(
    source_profile: Path,
    clone_profile: Path,
    *,
    session_credentials: bool,
) -> None:

    blocked = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "GrShaderCache",
        "ShaderCache",
        "Crashpad",
    }

    if session_credentials:
        blocked.update({
            "Cookies",
            "Cookies-journal",
            "Local Storage",
            "Session Storage",
            "Sessions",
            "IndexedDB",
            "WebStorage",
            "Service Worker",
        })

    def ignore(_path, names):
        return [
            name
            for name in names
            if (
                name in blocked
                or name.startswith("Singleton")
            )
        ]

    shutil.copytree(
        source_profile,
        clone_profile,
        dirs_exist_ok=True,
        ignore=ignore,
    )


async def precommit_note_publish(
    valid: dict[str, Any],
    auth_context: dict[str, Any],
    *,
    headless: bool = True,
) -> dict[str, Any]:

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    run_dir = (
        RUN_ROOT
        / f"{stamp}_{valid['case_id']}"
    )

    run_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger = _logger(run_dir)

    result: dict[str, Any] = {
        "ok": False,
        "codigo": "HELIX_NOTE_PRECOMMIT_INICIADO",
        "incident": "",
        "status_value": "",
        "note_type": "",
        "public_checked": None,
        "editor_empty": None,
        "publication_visible": False,
        "publication_disabled": None,
        "production_write": False,
        "publication_clicked": False,
        "safe_to_retry": True,
        "run_dir": str(run_dir),
        "error": "",
    }

    source_profile = Path(
        str(PROFILE_DIR)
    )

    if not source_profile.exists():
        raise RuntimeError(
            "No existe perfil Helix base: "
            + str(source_profile)
        )

    clone_profile = (
        run_dir
        / "perfil_writer"
    )

    session_credentials = (
        auth_context.get("mode")
        == "SESSION_CREDENTIALS"
    )

    _clone_profile(
        source_profile,
        clone_profile,
        session_credentials=session_credentials,
    )

    base_cfg = Settings.from_env()
    cfg = base_cfg

    if session_credentials:
        cfg = replace(
            base_cfg,
            username=auth_context["username"],
            password=auth_context["password"],
        )

    explicit_inc = _clean(
        valid.get("explicit_inc")
    ).upper()

    logger.info(
        "PRECOMMIT_BEGIN | wo=%s | explicit_inc=%s | auth_mode=%s",
        valid.get("wo", ""),
        explicit_inc,
        auth_context.get("mode", ""),
    )

    async with async_playwright() as playwright:

        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(clone_profile),
            headless=headless,
            slow_mo=cfg.slow_mo_ms,
            accept_downloads=False,
            viewport={
                "width": 1600,
                "height": 950,
            },
            args=[
                "--start-maximized",
                "--disable-notifications",
            ],
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
            # --------------------------------------------------------------
            # 1. Login
            # --------------------------------------------------------------

            await iniciar_sesion(
                page,
                cfg,
                logger,
            )

            # --------------------------------------------------------------
            # 2. Buscar y abrir WO
            # --------------------------------------------------------------

            await click_global_search(
                page,
                cfg,
                logger,
                valid["wo"],
            )

            page = await open_work_order(
                page,
                cfg,
                logger,
                valid["wo"],
            )

            # --------------------------------------------------------------
            # 3. Relacionados
            # --------------------------------------------------------------

            (
                _frame,
                related_table,
                _headers,
                related_items,
            ) = await open_related_tab_and_read(
                page,
                cfg,
                logger,
            )

            ids: list[str] = []

            for item in related_items or []:
                incident_id = _clean(
                    getattr(item, "id", "")
                ).upper()

                if (
                    INC_RE.fullmatch(incident_id)
                    and incident_id not in ids
                ):
                    ids.append(incident_id)

            logger.info(
                "RELATED_INC_IDS=%s",
                ",".join(ids),
            )

            if explicit_inc:

                if explicit_inc not in ids:
                    result["codigo"] = (
                        "HELIX_NOTE_INC_EXPLICITO_NO_EN_TABLA"
                    )
                    result["error"] = (
                        "INC explícito no encontrado en relacionados: "
                        + explicit_inc
                    )
                    return result

                incident_id = explicit_inc

            else:

                if len(ids) != 1:
                    result["codigo"] = (
                        "HELIX_NOTE_INC_AMBIGUO"
                    )
                    result["error"] = (
                        "Se requiere exactamente 1 INC relacionado; "
                        f"se detectaron {len(ids)}."
                    )
                    return result

                incident_id = ids[0]

            result["incident"] = incident_id

            # --------------------------------------------------------------
            # 4. Abrir INC
            # --------------------------------------------------------------

            await click_incident_in_table(
                related_table,
                incident_id,
                page,
                cfg,
                logger,
            )

            # --------------------------------------------------------------
            # 5. Encontrar frame real INC
            # --------------------------------------------------------------

            hit = await _find_incident_frame(
                page,
                incident_id,
                logger=logger,
            )

            if hit is None:
                result["codigo"] = (
                    "HELIX_NOTE_INC_FRAME_NO_ENCONTRADO"
                )
                result["error"] = (
                    "No fue posible localizar el frame "
                    "del incidente abierto."
                )
                return result

            frame, _frame_url = hit

            # --------------------------------------------------------------
            # 6. Estado
            # --------------------------------------------------------------

            status = await _element_info(
                frame.locator(
                    '[id="ar7_data"]'
                )
            )

            if status is not None:
                result["status_value"] = _clean(
                    status.get("text")
                )

            # --------------------------------------------------------------
            # 7. Editor
            # --------------------------------------------------------------

            editor = frame.locator(
                '[id="304247080"]'
            ).first

            editor_info = await _element_info(
                editor
            )

            if editor_info is None:
                result["codigo"] = (
                    "HELIX_NOTE_EDITOR_NO_ENCONTRADO"
                )
                result["error"] = (
                    "Editor WorkLog no encontrado."
                )
                return result

            editor_value = _clean(
                editor_info.get("value")
            )

            result["editor_empty"] = (
                editor_value == ""
            )

            if not result["editor_empty"]:
                result["codigo"] = (
                    "HELIX_NOTE_EDITOR_NO_VACIO"
                )
                result["error"] = (
                    "El editor WorkLog ya contiene texto."
                )
                return result

            # ATLAS_HELIX_NOTE_PRECOMMIT_EDITOR_FOCUS_PARITY_V1
            #
            # Paridad exacta con el publicador 8011 funcional:
            # el WorkLog recibe foco antes de que SmartIT termine
            # de activar Tipo de nota / Publico / Publicacion.
            #
            # focus() NO escribe contenido.
            try:
                await editor.focus(
                    timeout=5000
                )
            except Exception as exc:
                result["codigo"] = (
                    "HELIX_NOTE_EDITOR_FOCUS_ERROR"
                )
                result["error"] = (
                    f"No fue posible enfocar WorkLog: "
                    f"{type(exc).__name__}: {exc}"
                )
                return result

            await page.wait_for_timeout(450)

            logger.info(
                "EDITOR_FOCUS_PARITY=OK"
            )

            # --------------------------------------------------------------
            # 8. Tipo de nota
            # --------------------------------------------------------------
            # ATLAS_HELIX_NOTE_TYPE_READONLY_PARITY_8011_V1

            note_type_locator = frame.locator(
                '[id="rx-select-217"]'
            )

            note_type_count = await note_type_locator.count()

            logger.info(
                "NOTE_TYPE_COUNT_AFTER_FOCUS=%s",
                note_type_count,
            )

            note_type = await _element_info(
                note_type_locator
            )

            result["note_type"] = _clean(
                (note_type or {}).get("text")
            )

            note_type_visible = (
                bool(
                    (note_type or {}).get(
                        "visible"
                    )
                )
            )

            logger.info(
                "NOTE_TYPE_AFTER_FOCUS text=%s visible=%s",
                result["note_type"],
                note_type_visible,
            )

            allowed_note_types = {
                _norm("Información general"),
                _norm("General Information"),
            }

            if (
                _norm(result["note_type"])
                not in allowed_note_types
            ):
                result["codigo"] = (
                    "HELIX_NOTE_TIPO_INESPERADO"
                )
                result["error"] = (
                    "Tipo de nota inesperado despues de "
                    "enfocar WorkLog: "
                    + (
                        result["note_type"]
                        or "VACIO"
                    )
                )
                return result

            # --------------------------------------------------------------
            # 9. Público desmarcado
            # --------------------------------------------------------------

            public = await _element_info(
                frame.locator(
                    '[id="rx-checkbox-219"]'
                )
            )

            if public is None:
                result["codigo"] = (
                    "HELIX_NOTE_PUBLICO_NO_ENCONTRADO"
                )
                result["error"] = (
                    "Checkbox Público no encontrado."
                )
                return result

            result["public_checked"] = (
                public.get("checked")
            )

            if result["public_checked"] is not False:
                result["codigo"] = (
                    "HELIX_NOTE_PUBLICO_NO_ESTA_DESMARCADO"
                )
                result["error"] = (
                    "El checkbox Público no está desmarcado."
                )
                return result

            # --------------------------------------------------------------
            # 10. Botón Publicar
            # --------------------------------------------------------------

            publication = frame.locator(
                '[id="304268430"]'
            ).first

            publication_info = await _element_info(
                publication
            )

            if publication_info is None:
                result["codigo"] = (
                    "HELIX_NOTE_PUBLICACION_NO_ENCONTRADA"
                )
                result["error"] = (
                    "Botón Publicar no encontrado."
                )
                return result

            result["publication_visible"] = bool(
                publication_info.get("visible")
            )

            result["publication_disabled"] = (
                publication_info.get("disabled")
            )

            if (
                not result["publication_visible"]
                or result["publication_disabled"]
                is not False
            ):
                result["codigo"] = (
                    "HELIX_NOTE_PUBLICACION_NO_LISTA"
                )
                result["error"] = (
                    "Botón Publicar no está listo."
                )
                return result

            # ==============================================================
            # BARRERA DURA FASE 2B
            # ==============================================================
            #
            # NO:
            #   - editor.fill(...)
            #   - publication.click()
            #   - escritura en WorkLog
            #
            # ==============================================================

            result["ok"] = True
            result["codigo"] = (
                "HELIX_NOTE_PRECOMMIT_OK"
            )
            result["production_write"] = False
            result["publication_clicked"] = False
            result["safe_to_retry"] = True
            result["error"] = ""

            logger.info(
                "PRECOMMIT_OK | inc=%s | "
                "editor_empty=%s | "
                "note_type=%s | "
                "public_checked=%s | "
                "publication_visible=%s | "
                "publication_disabled=%s",
                incident_id,
                result["editor_empty"],
                result["note_type"],
                result["public_checked"],
                result["publication_visible"],
                result["publication_disabled"],
            )

            return result

        finally:
            await context.close()


def runtime_capabilities() -> dict[str, Any]:
    return {
        "profile_dir": str(PROFILE_DIR),
        "profile_exists": PROFILE_DIR.exists(),
        "settings_available": callable(Settings),
        "login_available": callable(iniciar_sesion),
        "global_search_available": callable(click_global_search),
        "open_work_order_available": callable(open_work_order),
        "related_reader_available": callable(open_related_tab_and_read),
        "incident_click_available": callable(click_incident_in_table),
        "normalize_available": callable(normalize_text),
    }
