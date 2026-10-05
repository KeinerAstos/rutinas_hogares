from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.mesa_ayuda.helix_note_service import SOP_POR_TIPO


SERVICE_VERSION = "HELIX_NOTE_AUTO_FINAL_V1_SESSION_AUTH_V13_F1_2"
WRITE_LOCK = threading.RLock()

PROGRAM_DATA = Path(r"C:\xampp\htdocs\rutinas_hogares\api_orquestador_hogares")
STATE_DIR = PROGRAM_DATA / "data" / "helix_note_publish"
STATE_FILE = STATE_DIR / "idempotency.json"
RUN_ROOT = PROGRAM_DATA / "diagnosticos" / "helix_note_publish"

RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "helix"
    / "runtime"
    / "helix_runner.py"
)

WO_RE = re.compile(r"^WO\d{13,14}$", re.I)
INC_RE = re.compile(r"^INC\d+$", re.I)
GRID_NEEDLE = "grid/GetTableEntryList/HPD:SV_Union_WorkInfo_SocialEvents"
WORKLOG_NEEDLE = "SetEntryList/HPD:WorkLog"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value)).upper()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _state_default() -> dict[str, Any]:
    return {"version": 1, "records": {}}


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _state_default()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _state_default()
    if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
        return _state_default()
    return data


def _save_state(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(STATE_FILE)


def _idem_key(case_id: str, solicitud_id: str, wo: str, tipo_codigo: str) -> str:
    raw = f"{case_id}|{solicitud_id}|{wo}|{tipo_codigo}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_request(
    *,
    case_id: str,
    note_text: str,
    note_hash: str,
    note_context: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    case = _clean(case_id)
    text = str(note_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    supplied_hash = _clean(note_hash).lower()
    context = note_context if isinstance(note_context, dict) else {}

    tipo = _norm(context.get("tipo_codigo"))
    wo = _clean(context.get("wo")).upper()
    explicit_inc = _clean(context.get("incidente_relacionado")).upper()
    solicitud_id = _clean(context.get("solicitud_id"))
    expected_sop = _clean(SOP_POR_TIPO.get(tipo, ""))

    if not case:
        return False, {"codigo": "HELIX_NOTE_CASE_VACIO", "error": "case_id vacío."}
    if not solicitud_id:
        return False, {"codigo": "HELIX_NOTE_SOLICITUD_ID_VACIA", "error": "solicitud_id vacía."}
    if not expected_sop:
        return False, {"codigo": "HELIX_NOTE_TIPO_SIN_SOP", "error": "El tipo no tiene SOP habilitado."}
    if context.get("habilitado") is not True:
        return False, {"codigo": "HELIX_NOTE_DESHABILITADA", "error": "La nota está deshabilitada por backend."}
    if context.get("terminal_nota") is not True:
        return False, {"codigo": "HELIX_NOTE_NO_TERMINAL", "error": "La gestión todavía no está en estado terminal."}
    if context.get("publicar_automaticamente") is not True:
        return False, {"codigo": "HELIX_NOTE_AUTO_OFF", "error": "Publicación automática deshabilitada."}
    if context.get("escritura_helix") is not True:
        return False, {"codigo": "HELIX_NOTE_WRITE_OFF", "error": "Escritura Helix deshabilitada."}
    if _norm(context.get("destino")) != "INC_RELACIONADO":
        return False, {"codigo": "HELIX_NOTE_DESTINO_INVALIDO", "error": "El destino no es INC_RELACIONADO."}
    if not WO_RE.fullmatch(wo):
        return False, {"codigo": "HELIX_NOTE_WO_INVALIDA", "error": "WO inválida."}
    if explicit_inc and not INC_RE.fullmatch(explicit_inc):
        return False, {"codigo": "HELIX_NOTE_INC_INVALIDO", "error": "INC explícito inválido."}
    if not text or len(text) > 120000:
        return False, {"codigo": "HELIX_NOTE_TEXTO_INVALIDO", "error": "Nota vacía o demasiado grande."}
    if text == expected_sop:
        return False, {"codigo": "HELIX_NOTE_SIN_CONVERSACION", "error": "La nota no contiene conversación."}
    if not text.startswith(expected_sop + "\n\n"):
        return False, {"codigo": "HELIX_NOTE_SOP_MISMATCH", "error": "El encabezado SOP no coincide con el backend."}

    calculated = _sha256_text(text)
    if supplied_hash and supplied_hash != calculated:
        return False, {"codigo": "HELIX_NOTE_HASH_MISMATCH", "error": "SHA-256 de la nota no coincide."}

    return True, {
        "case_id": case,
        "note_text": text,
        "note_hash": calculated,
        "tipo_codigo": tipo,
        "sop_titulo": expected_sop,
        "wo": wo,
        "explicit_inc": explicit_inc,
        "solicitud_id": solicitud_id,
    }


def _load_runner_module():
    if not RUNNER_PATH.exists():
        raise RuntimeError(f"No existe runtime Helix: {RUNNER_PATH}")

    name = f"atlas_helix_note_writer_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar helix_runner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def _body_inner_text(frame) -> str:
    try:
        body = frame.locator("body")
        if not await body.count():
            return ""
        return str(await body.inner_text(timeout=2500))
    except Exception:
        return ""


async def _body_text_content(frame) -> str:
    try:
        body = frame.locator("body")
        if not await body.count():
            return ""
        return str(await body.text_content(timeout=2500) or "")
    except Exception:
        return ""


async def _find_incident_frame(page, incident_id: str):
    wanted = _norm(incident_id)
    for frame in list(page.frames):
        try:
            url = str(frame.url or "")
        except Exception:
            url = ""
        if "TARGETFORM=INCIDENT" not in url.upper():
            continue
        text = await _body_text_content(frame)
        if wanted and wanted in _norm(text):
            return frame, url
    return None


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


def _parse_record_total(text: str) -> int | None:
    match = re.search(
        r"Registros\s+desde\s+\d+\s+hasta\s+\d+\s+de\s+(\d+)",
        text,
        re.I,
    )
    return int(match.group(1)) if match else None


# HELIX_SESSION_AUTH_V13_F1_2
def _session_auth_context(
    helix_auth: dict[str, Any] | None,
    helix_auth_mode: str,
) -> dict[str, Any]:
    mode = _norm(helix_auth_mode)

    if mode not in {"", "TEST_FALLBACK", "SESSION_CREDENTIALS"}:
        raise ValueError("HELIX_AUTH_MODE_INVALIDO")

    if mode != "SESSION_CREDENTIALS":
        return {
            "mode": mode or "TEST_FALLBACK",
            "username": "",
            "password": "",
        }

    auth = helix_auth if isinstance(helix_auth, dict) else {}

    username = _clean(auth.get("username"))
    password = str(auth.get("password") or "")
    scope = _norm(auth.get("scope"))
    persistence = _norm(auth.get("persistence"))

    if not username or not password:
        raise ValueError("HELIX_AUTH_CREDENCIALES_VACIAS")

    if len(username) > 200 or len(password) > 500:
        raise ValueError("HELIX_AUTH_LONGITUD_INVALIDA")

    if any(ch in username for ch in ("\r", "\n", "\t", "\x00")):
        raise ValueError("HELIX_AUTH_USUARIO_INVALIDO")

    if "\x00" in password:
        raise ValueError("HELIX_AUTH_PASSWORD_INVALIDO")

    if scope != "NOTE_PUBLISH_ONLY":
        raise ValueError("HELIX_AUTH_SCOPE_INVALIDO")

    if persistence != "NONE":
        raise ValueError("HELIX_AUTH_PERSISTENCE_INVALIDA")

    return {
        "mode": "SESSION_CREDENTIALS",
        "username": username,
        "password": password,
    }



# HELIX_NOTE_RUNNER_ERROR_PROPAGATION_V13_F1_8
def _apply_runner_precommit_result(
    result: dict[str, Any],
    runner_payload: dict[str, Any] | None,
    *,
    rc: int,
    explicit_inc: str,
) -> dict[str, Any]:
    """
    Conserva el error real del runtime Helix.

    No permite que un error de login/búsqueda sea reemplazado
    genéricamente por HELIX_NOTE_INC_NO_RESUELTO.
    """
    payload = (
        runner_payload
        if isinstance(runner_payload, dict)
        else {}
    )

    runner_code = _clean(
        payload.get("codigo")
    )

    runner_error = _clean(
        payload.get("error")
    )

    combined = (
        runner_code
        + " "
        + runner_error
    ).upper()

    # El INC explícito ya fue validado por _validate_request.
    # Aunque todavía no se haya abierto visualmente, no debe perderse
    # del resultado diagnóstico.
    if (
        explicit_inc
        and not _clean(result.get("incident"))
    ):
        result["incident"] = explicit_inc

    if result.get("production_write"):
        return result

    if "HELIX_CREDENCIALES_INVALIDAS" in combined:
        result["codigo"] = (
            "HELIX_NOTE_CREDENCIALES_INVALIDAS"
        )
        result["error"] = (
            runner_error
            or
            "SmartIT rechazó de forma persistente "
            "las credenciales durante el login."
        )
        result["safe_to_retry"] = True
        return result

    if "HELIX_LOGIN_NO_CONFIRMADO" in combined:
        result["codigo"] = (
            "HELIX_NOTE_LOGIN_NO_CONFIRMADO"
        )
        result["error"] = (
            runner_error
            or
            "El runtime no pudo confirmar el login."
        )
        result["safe_to_retry"] = True
        return result

    if runner_code == "HELIX_OT_NO_ENCONTRADA":
        result["codigo"] = (
            "HELIX_NOTE_WO_NO_ENCONTRADA"
        )
        result["error"] = (
            runner_error
            or
            "Helix no encontró la WO."
        )
        result["safe_to_retry"] = True
        return result

    if rc != 0:
        result["codigo"] = (
            "HELIX_NOTE_RUNTIME_PRECOMMIT_FALLIDO"
        )
        result["error"] = (
            runner_error
            or
            (
                "Runtime Helix terminó con "
                f"rc={rc} antes de publicar la nota."
            )
        )
        result["safe_to_retry"] = True
        return result

    if not _clean(result.get("incident")):
        result["codigo"] = (
            "HELIX_NOTE_INC_NO_RESUELTO"
        )
        result["error"] = (
            runner_error
            or
            "No se resolvió un INC relacionado de forma segura."
        )

    return result


async def _publish_with_runner(
    valid: dict[str, Any],
    run_dir: Path,
    auth_context: dict[str, Any],
) -> dict[str, Any]:
    runner = _load_runner_module()

    result: dict[str, Any] = {
        "ok": False,
        "codigo": "HELIX_NOTE_NO_EJECUTADA",
        "incident": "",
        "status_value": "",
        "note_type": "",
        "public_checked": None,
        "record_total_before": None,
        "record_total_after": None,
        "worklog_statuses": [],
        "grid_statuses": [],
        "publication_clicked": False,
        "production_write": False,
        "editor_cleared": False,
        "verification_method": "",
        "safe_to_retry": True,
        "error": "",
    }

    source_profile = Path(str(runner.PROFILE_DIR))
    if not source_profile.exists():
        raise RuntimeError(f"No existe perfil Helix base: {source_profile}")

    clone_profile = run_dir / "perfil_writer"

    def ignore(_path, names):
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

        # HELIX_SESSION_AUTH_V13_F1_2
        # La autenticacion del perfil base NO puede heredarse
        # cuando la nota se publica con la cuenta del agente.
        if auth_context.get("mode") == "SESSION_CREDENTIALS":
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

        return [
            name
            for name in names
            if name in blocked or name.startswith("Singleton")
        ]

    try:
        shutil.copytree(
            source_profile,
            clone_profile,
            dirs_exist_ok=True,
            ignore=ignore,
        )
    except Exception as exc:
        raise RuntimeError(f"No se pudo clonar el perfil Helix: {type(exc).__name__}: {exc}") from exc

    runner.PROFILE_DIR = clone_profile

    original_click = runner.click_incident_in_table
    selected_incident = {"value": ""}
    explicit_inc = valid.get("explicit_inc", "")

    async def guarded_click(table, incident_id, page, cfg, logger):
        ids: list[str] = []
        links = table.locator("a")
        count = min(await links.count(), 250)
        for index in range(count):
            try:
                text = _clean(await links.nth(index).inner_text(timeout=700)).upper()
            except Exception:
                continue
            if INC_RE.fullmatch(text) and text not in ids:
                ids.append(text)

        candidate = _clean(incident_id).upper()

        if explicit_inc:
            if candidate != explicit_inc:
                raise RuntimeError(
                    f"HELIX_NOTE_INC_EXPLICITO_MISMATCH: candidato={candidate} esperado={explicit_inc}"
                )
            if ids and explicit_inc not in ids:
                raise RuntimeError(
                    f"HELIX_NOTE_INC_EXPLICITO_NO_EN_TABLA: {explicit_inc}"
                )
        else:
            if len(ids) != 1:
                result["codigo"] = "HELIX_NOTE_INC_AMBIGUO"
                result["error"] = (
                    "Se requieren exactamente 1 INC relacionado y se detectaron "
                    f"{len(ids)}. No se publicó ninguna nota."
                )
                raise RuntimeError(result["error"])
            if candidate != ids[0]:
                result["codigo"] = "HELIX_NOTE_INC_SELECCION_INESPERADA"
                result["error"] = (
                    f"candidato={candidate} unico={ids[0]}. No se publicó ninguna nota."
                )
                raise RuntimeError(result["error"])

        selected_incident["value"] = candidate
        result["incident"] = candidate
        return await original_click(table, candidate, page, cfg, logger)

    runner.click_incident_in_table = guarded_click

    # HELIX_NOTE_RUNTIME_TRACE_V13_F1_6
    trace_file = run_dir / "runtime_trace_v13_f1_6.log"

    def trace(event: str, **data):
        try:
            safe = {
                key: value
                for key, value in data.items()
                if key not in {"password", "helix_auth"}
            }

            with trace_file.open(
                "a",
                encoding="utf-8",
            ) as fh:
                payload = " | ".join(
                    f"{key}={value}"
                    for key, value in safe.items()
                )
                fh.write(
                    event
                    + (" | " + payload if payload else "")
                    + "\n"
                )
        except Exception:
            pass

    trace(
        "RUNNER_PREPARED",
        wo=valid.get("wo", ""),
        explicit_inc=explicit_inc,
        auth_mode=auth_context.get("mode", ""),
    )

    original_related_reader = runner.open_related_tab_and_read

    async def traced_related_reader(*args, **kwargs):
        trace("RELATED_BEGIN")
        try:
            value = await original_related_reader(*args, **kwargs)
            try:
                items = value[3] or []
                ids = [
                    str(getattr(item, "id", "") or "")
                    for item in items
                ]
            except Exception:
                ids = []
            trace(
                "RELATED_OK",
                count=len(ids),
                ids=",".join(ids),
            )
            return value
        except Exception as exc:
            trace(
                "RELATED_ERROR",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    runner.open_related_tab_and_read = traced_related_reader

    original_guarded_click = guarded_click

    # HELIX_NOTE_PUBLISH_AFTER_INC_V13_F1_9
    async def traced_guarded_click(
        table,
        incident_id,
        page,
        cfg,
        logger,
    ):
        trace(
            "GUARDED_CLICK_BEGIN",
            incident_id=incident_id,
            explicit_inc=explicit_inc,
        )

        try:
            value = await original_guarded_click(
                table,
                incident_id,
                page,
                cfg,
                logger,
            )
        except Exception as exc:
            trace(
                "GUARDED_CLICK_ERROR",
                incident_id=incident_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        trace(
            "GUARDED_CLICK_OK",
            incident_id=incident_id,
        )

        # El INC ya fue validado y abierto.
        # Este es el punto correcto para escribir la nota.
        trace(
            "NOTE_PUBLISH_CALLBACK_BEGIN",
            incident_id=incident_id,
        )

        try:
            await publish_callback(
                page,
                cfg,
                logger,
            )
        except Exception as exc:
            trace(
                "NOTE_PUBLISH_CALLBACK_ERROR",
                incident_id=incident_id,
                error_type=type(exc).__name__,
                error=str(exc),
                production_write=result.get(
                    "production_write",
                    False,
                ),
                codigo=result.get(
                    "codigo",
                    "",
                ),
            )
            raise

        trace(
            "NOTE_PUBLISH_CALLBACK_END",
            incident_id=incident_id,
            production_write=result.get(
                "production_write",
                False,
            ),
            codigo=result.get(
                "codigo",
                "",
            ),
            verification_method=result.get(
                "verification_method",
                "",
            ),
        )

        return value

    runner.click_incident_in_table = traced_guarded_click

    async def publish_callback(page, cfg, logger):
        incident_id = selected_incident["value"]
        if not incident_id:
            return [], 0

        hit = await _find_incident_frame(page, incident_id)
        if hit is None:
            return [], 0

        frame, _frame_url = hit

        status = await _element_info(frame.locator('[id="ar7_data"]'))
        if status is not None:
            result["status_value"] = _clean(status.get("text"))

        current_text = await _body_inner_text(frame)
        result["record_total_before"] = _parse_record_total(current_text)

        editor = frame.locator('[id="304247080"]').first
        editor_info = await _element_info(editor)
        if editor_info is None:
            raise RuntimeError("HELIX_NOTE_EDITOR_NO_ENCONTRADO")
        if _clean(editor_info.get("value")):
            raise RuntimeError("HELIX_NOTE_EDITOR_NO_VACIO")

        await editor.focus()
        await page.wait_for_timeout(450)

        # HELIX_NOTE_TYPE_BILINGUAL_V13_F1_10
        note_type = await _element_info(
            frame.locator('[id="rx-select-217"]')
        )

        result["note_type"] = _clean(
            (note_type or {}).get("text")
        )

        note_type_norm = _norm(
            result["note_type"]
        )

        allowed_note_types = {
            _norm("Información general"),
            _norm("General Information"),
        }

        if note_type_norm not in allowed_note_types:
            raise RuntimeError(
                f"HELIX_NOTE_TIPO_INESPERADO: "
                f"{result['note_type'] or 'VACIO'}"
            )

        public = await _element_info(frame.locator('[id="rx-checkbox-219"]'))
        if public is None:
            raise RuntimeError("HELIX_NOTE_PUBLICO_NO_ENCONTRADO")
        result["public_checked"] = public.get("checked")
        if result["public_checked"] is not False:
            raise RuntimeError("HELIX_NOTE_PUBLICO_NO_ESTA_DESMARCADO")

        publication = frame.locator('[id="304268430"]').first
        pub_info = await _element_info(publication)
        if not pub_info or not pub_info.get("visible") or pub_info.get("disabled") is not False:
            raise RuntimeError("HELIX_NOTE_PUBLICACION_NO_LISTA")

        pending_tasks: set[asyncio.Task] = set()

        async def capture_response(response):
            try:
                url = str(response.url or "")
                if WORKLOG_NEEDLE in url:
                    result["worklog_statuses"].append(int(response.status))
                if GRID_NEEDLE in url:
                    result["grid_statuses"].append(int(response.status))
            except Exception:
                pass

        def on_response(response):
            task = asyncio.create_task(capture_response(response))
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)

        page.on("response", on_response)

        await editor.fill(valid["note_text"])
        actual = await editor.input_value()
        if actual != valid["note_text"]:
            await editor.fill("")
            raise RuntimeError("HELIX_NOTE_EDITOR_MISMATCH")

        pub_info = await _element_info(publication)
        if not pub_info or not pub_info.get("visible") or pub_info.get("disabled") is not False:
            await editor.fill("")
            raise RuntimeError("HELIX_NOTE_PUBLICACION_CAMBIO_ANTES_COMMIT")

        await publication.click()
        result["publication_clicked"] = True
        result["production_write"] = True
        result["safe_to_retry"] = False

        await page.wait_for_timeout(2800)
        if pending_tasks:
            await asyncio.gather(*list(pending_tasks), return_exceptions=True)

        try:
            result["editor_cleared"] = (await editor.input_value()) == ""
        except Exception:
            result["editor_cleared"] = True

        after_text = await _body_inner_text(frame)
        result["record_total_after"] = _parse_record_total(after_text)

        worklog_ok = 200 in result["worklog_statuses"]
        grid_ok = 200 in result["grid_statuses"]
        count_ok = (
            result["record_total_before"] is not None
            and result["record_total_after"] is not None
            and result["record_total_after"] >= result["record_total_before"] + 1
        )

        verified = bool(
            worklog_ok
            and result["editor_cleared"]
            and (count_ok or grid_ok)
        )

        if verified:
            result["ok"] = True
            result["codigo"] = "HELIX_NOTE_PUBLICADA_VERIFICADA"
            result["verification_method"] = (
                "WORKLOG_200+EDITOR_CLEAR+COUNT_INCREMENT"
                if count_ok
                else "WORKLOG_200+EDITOR_CLEAR+GRID_200"
            )
        else:
            result["codigo"] = "HELIX_NOTE_COMMIT_NO_VERIFICADO"
            result["error"] = (
                "Publicación recibió click, pero no se obtuvo evidencia suficiente para "
                "certificar el WorkLog. No se permite reintento automático."
            )

        return [], 0

    # F1.9:
    # read_activity_attachments conserva su comportamiento original.
    # La nota se publica únicamente después de abrir el INC.

    async def no_download(*args, **kwargs):
        if len(args) >= 4:
            return args[3]
        return None

    runner.download_attachment = no_download

    parser = runner.build_parser()
    opts = {
        action.dest: action.option_strings[0]
        for action in parser._actions
        if action.option_strings
    }

    if "ot" not in opts or "headless" not in opts:
        raise RuntimeError("Runtime Helix no expone --ot/--headless esperados.")

    # HELIX_NOTE_FORCE_INCIDENT_ARG_V13_F1_5
    # La publicación debe llegar al INC aunque la WO tenga documentación.
    if "forzar_incidente" not in opts:
        raise RuntimeError(
            "Runtime Helix no expone --forzar-incidente."
        )

    argv = [
        opts["ot"],
        valid["wo"],
        opts["headless"],
        opts["forzar_incidente"],
    ]
    if explicit_inc:
        if "incidente" not in opts:
            raise RuntimeError("Runtime Helix no expone --incidente.")
        argv += [opts["incidente"], explicit_inc]

    parsed = parser.parse_args(argv)

    # HELIX_SESSION_AUTH_PROPAGATION_V13_F1_7
    setattr(
        parsed,
        "_atlas_session_auth",
        {
            "username": auth_context.get("username", ""),
            "password": auth_context.get("password", ""),
        },
    )

    original_settings_class = runner.Settings

    try:
        if auth_context.get("mode") == "SESSION_CREDENTIALS":
            base_cfg = original_settings_class.from_env()

            session_cfg = replace(
                base_cfg,
                username=auth_context["username"],
                password=auth_context["password"],
            )

            class _AtlasSessionSettings:
                @classmethod
                def from_env(cls):
                    return session_cfg

            runner.Settings = _AtlasSessionSettings

        trace(
            "MAIN_ASYNC_BEGIN",
            ot=valid.get("wo", ""),
            explicit_inc=explicit_inc,
            parser_incidente=getattr(parsed, "incidente", ""),
            parser_forzar_incidente=getattr(
                parsed,
                "forzar_incidente",
                False,
            ),
        )

        try:
            rc = await runner.main_async(parsed)

            runner_payload = getattr(
                parsed,
                "_atlas_last_payload",
                {},
            )

            trace(
                "MAIN_ASYNC_END",
                rc=rc,
                selected_incident=selected_incident.get("value", ""),
                production_write=result.get("production_write", False),
                codigo=result.get("codigo", ""),
                error=result.get("error", ""),
                runner_codigo=_clean(
                    (runner_payload or {}).get("codigo")
                ),
                runner_error=_clean(
                    (runner_payload or {}).get("error")
                ),
            )
        except Exception as exc:
            trace(
                "MAIN_ASYNC_EXCEPTION",
                error_type=type(exc).__name__,
                error=str(exc),
                selected_incident=selected_incident.get("value", ""),
            )
            raise

    finally:
        runner.Settings = original_settings_class
    if result["production_write"]:
        return result

    return _apply_runner_precommit_result(
        result,
        runner_payload,
        rc=rc,
        explicit_inc=explicit_inc,
    )


def publicar_nota_helix_smcc(
    *,
    case_id: str,
    note_text: str,
    note_hash: str,
    note_context: dict[str, Any],
    agent_id: str = "",
    helix_auth: dict[str, Any] | None = None,
    helix_auth_mode: str = "",
) -> dict[str, Any]:
    del agent_id  # La autoría visible corresponde a la cuenta autenticada en Helix.

    base = {
        "ok": False,
        "codigo": "HELIX_NOTE_ERROR",
        "service_version": SERVICE_VERSION,
        "case_id": _clean(case_id),
        "wo": _clean((note_context or {}).get("wo")).upper(),
        "incidente": "",
        "production_write": False,
        "safe_to_retry": True,
        "duplicate": False,
        "error": "",
    }

    try:
        auth_context = _session_auth_context(
            helix_auth,
            helix_auth_mode,
        )
    except ValueError as exc:
        base["codigo"] = "HELIX_NOTE_AUTH_INVALID"
        base["error"] = str(exc)
        base["safe_to_retry"] = True
        return base

    base["helix_auth_mode"] = auth_context["mode"]
    base["helix_user"] = auth_context["username"]

    valid_ok, validated = _validate_request(
        case_id=case_id,
        note_text=note_text,
        note_hash=note_hash,
        note_context=note_context,
    )

    if not valid_ok:
        base.update(validated)
        return base

    base["wo"] = validated["wo"]
    base["note_hash"] = validated["note_hash"]
    base["solicitud_id"] = validated["solicitud_id"]
    base["note_length"] = len(validated["note_text"])

    with WRITE_LOCK:
        key = _idem_key(
            validated["case_id"],
            validated["solicitud_id"],
            validated["wo"],
            validated["tipo_codigo"],
        )

        state = _load_state()
        previous = state["records"].get(key)

        if isinstance(previous, dict):
            status = _clean(previous.get("status")).upper()
            if status == "VERIFIED":
                base.update({
                    "ok": True,
                    "codigo": "HELIX_NOTE_DUPLICADA_YA_VERIFICADA",
                    "duplicate": True,
                    "incidente": _clean(previous.get("incidente")),
                    "production_write": False,
                    "safe_to_retry": False,
                    "verification_method": _clean(previous.get("verification_method")),
                })
                return base
            if status == "COMMIT_UNVERIFIED":
                base.update({
                    "codigo": "HELIX_NOTE_DUPLICATE_BLOCK_COMMIT_INCIERTO",
                    "duplicate": True,
                    "incidente": _clean(previous.get("incidente")),
                    "production_write": True,
                    "safe_to_retry": False,
                    "error": "Existe un commit previo no verificado; se bloquea un segundo intento para evitar duplicados.",
                })
                return base

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        run_dir = RUN_ROOT / f"{stamp}_{validated['case_id']}"
        run_dir.mkdir(parents=True, exist_ok=True)

        try:
            result = asyncio.run(_publish_with_runner(validated, run_dir, auth_context))
        except Exception as exc:
            base.update({
                "codigo": "HELIX_NOTE_PRECOMMIT_EXCEPTION",
                "error": f"{type(exc).__name__}: {exc}",
                "safe_to_retry": True,
            })
            return base

        base.update({
            "ok": bool(result.get("ok")),
            "codigo": _clean(result.get("codigo")) or "HELIX_NOTE_ERROR",
            "incidente": _clean(result.get("incident")),
            "production_write": bool(result.get("production_write")),
            "safe_to_retry": bool(result.get("safe_to_retry")),
            "verification_method": _clean(result.get("verification_method")),
            "worklog_statuses": result.get("worklog_statuses") or [],
            "grid_statuses": result.get("grid_statuses") or [],
            "record_total_before": result.get("record_total_before"),
            "record_total_after": result.get("record_total_after"),
            "error": _clean(result.get("error")),
        })

        if result.get("ok"):
            state["records"][key] = {
                "status": "VERIFIED",
                "case_id": validated["case_id"],
                "wo": validated["wo"],
                "solicitud_id": validated["solicitud_id"],
                "incidente": base["incidente"],
                "note_hash": validated["note_hash"],
                "note_length": len(validated["note_text"]),
                "verification_method": base["verification_method"],
                "created_at": _now(),
            }
            _save_state(state)
        elif result.get("production_write"):
            state["records"][key] = {
                "status": "COMMIT_UNVERIFIED",
                "case_id": validated["case_id"],
                "wo": validated["wo"],
                "solicitud_id": validated["solicitud_id"],
                "incidente": base["incidente"],
                "note_hash": validated["note_hash"],
                "note_length": len(validated["note_text"]),
                "created_at": _now(),
            }
            _save_state(state)

        return base