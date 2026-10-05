# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from app.services.helix.smartit_scraper import Settings
from app.config.endpoints_settings import get_smartit_url

URL_HELIX = get_smartit_url()

INC_RE = re.compile(r"^INC\d{12}$", re.I)
TAS_RE = re.compile(r"^TAS\d{12}$", re.I)

WO_RE = re.compile(r"\bWO\d{7,}\b", re.I)
TA_RE = re.compile(r"\b(?:TA|TAS)\d{5,}\b", re.I)

MAX_WORKERS = 7

ROOT = Path(r"C:\xampp\htdocs\rutinas_hogares\api_rpa_helix_relacionados\data\batch")
JOBS_DIR = ROOT / "jobs"

# Namespace limpio y exclusivo del motor V2.5 exacto.
# Los perfiles se reutilizan entre jobs, como el V2.5 original.
PROFILES = ROOT / "profiles_v25_exact"

LOG_DIR = Path(
    r"C:\xampp\htdocs\rutinas_hogares\api_direccion_clientes\logs\helix_relacionados_batch"
)

DIAG_ROOT = Path(
    r"C:\xampp\htdocs\rutinas_hogares\api_rpa_helix_relacionados\runtime\diagnosticos\helix_relacionados_v25_exact_runtime"
)

# La consola de pruebas mantiene sus archivos dentro de la entrega.
if os.environ.get("HELIX_TEST_DIR"):
    ROOT = Path(os.environ["HELIX_TEST_DIR"])
    JOBS_DIR = ROOT / "jobs"
    PROFILES = ROOT / "profiles"
    LOG_DIR = ROOT / "logs"
    DIAG_ROOT = ROOT / "diagnosticos"

for p in (ROOT, JOBS_DIR, PROFILES, LOG_DIR, DIAG_ROOT):
    p.mkdir(parents=True, exist_ok=True)

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_JOB_IDS: dict[str, str | None] = {
    "front": None,
    "back": None,
}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _logger(job_id: str, worker: int) -> logging.Logger:
    name = f"helix_v25_exact_{job_id}_w{worker:02d}"
    log = logging.getLogger(name)

    if log.handlers:
        return log

    log.setLevel(logging.INFO)
    log.propagate = False

    fh = logging.FileHandler(
        LOG_DIR / f"{job_id}_W{worker:02d}_V25_EXACT.log",
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    return log


def _write_job(job_id: str) -> None:
    with _LOCK:
        payload = dict(_JOBS[job_id])
        payload.pop("_incidentes", None)

    target = JOBS_DIR / f"{job_id}.json"
    tmp = target.with_suffix(".json.tmp")

    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "area": job.get("area", "front"),
        "estado": job["estado"],
        "total": job["total"],
        "procesados": job["procesados"],
        "pendientes": max(0, job["total"] - job["procesados"]),
        "workers": job["workers"],
        "creado": job["creado"],
        "iniciado": job.get("iniciado", ""),
        "finalizado": job.get("finalizado", ""),
        "duracion_seg": job.get("duracion_seg", 0.0),
        "resultados": list(job.get("resultados", [])),
        "error": job.get("error", ""),
        "pausado": bool(job.get("pause_requested", False)),
        "detener_solicitado": bool(job.get("stop_requested", False)),
    }


def iniciar_job(
    incidentes: list[str],
    workers: int = MAX_WORKERS,
    area: str = "front",
) -> dict[str, Any]:
    """
    Acepta tanto INC como TAS.

    El nombre del parametro `incidentes` se conserva
    para no romper deco.py ni el front actual.

    `area` separa la ejecución de Front Office y Back Office,
    permitiendo un job activo independiente por cada área.
    """
    area = str(area or "front").strip().lower()

    if area not in {"front", "back"}:
        area = "front"

    tickets = []
    seen = set()

    for raw in incidentes:
        ticket = str(raw or "").strip().upper()

        if not (INC_RE.fullmatch(ticket) or TAS_RE.fullmatch(ticket)):
            continue

        if ticket in seen:
            continue

        seen.add(ticket)
        tickets.append(ticket)

    if not tickets:
        return {
            "ok": False,
            "codigo": "HELIX_BATCH_SIN_TICKETS_VALIDOS",
            "respuesta": "No se recibieron INC o TAS validos.",
        }

    worker_count = max(
        1,
        min(
            int(workers or MAX_WORKERS),
            MAX_WORKERS,
            len(tickets),
        ),
    )

    with _LOCK:
        active_job_id = _ACTIVE_JOB_IDS.get(area)

        if active_job_id:
            active = _JOBS.get(active_job_id)

            if active and active.get("estado") in {"PENDIENTE", "PROCESANDO"}:
                return {
                    "ok": False,
                    "codigo": "HELIX_BATCH_OCUPADO",
                    "respuesta": (
                        f"Ya existe un analisis masivo en ejecucion para {area}."
                    ),
                    "job_id": active_job_id,
                    "area": area,
                }

        job_id = uuid.uuid4().hex[:16]
        _ACTIVE_JOB_IDS[area] = job_id

        _JOBS[job_id] = {
            "job_id": job_id,
            "area": area,
            "estado": "PENDIENTE",
            "total": len(tickets),
            "procesados": 0,
            "workers": worker_count,
            "creado": _now(),
            "iniciado": "",
            "finalizado": "",
            "duracion_seg": 0.0,
            "resultados": [],
            "error": "",
            "pause_requested": False,
            "stop_requested": False,
            "control_actualizado": "",
            # Se mantiene este nombre por compatibilidad interna.
            "_incidentes": tickets,
        }

        _write_job(job_id)

    th = threading.Thread(
        target=_thread_main,
        args=(job_id,),
        daemon=True,
        name=f"helix-v25-exact-{job_id}",
    )

    th.start()

    with _LOCK:
        return {
            "ok": True,
            "codigo": "HELIX_BATCH_INICIADO",
            **_public(_JOBS[job_id]),
        }


def obtener_job(job_id: str) -> dict[str, Any]:
    """
    Devuelve el estado público de un job.

    Primero intenta leer el job que sigue en memoria. Si el proceso fue
    reiniciado o el job ya no está en _JOBS, intenta recuperarlo desde
    el JSON persistido en JOBS_DIR.
    """
    key = str(job_id or "").strip()

    if not key:
        return {
            "ok": False,
            "codigo": "HELIX_BATCH_JOB_ID_INVALIDO",
            "job_id": "",
        }

    with _LOCK:
        job = _JOBS.get(key)

        if job is not None:
            return {
                "ok": True,
                **_public(job),
            }

    path = JOBS_DIR / f"{key}.json"

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))

            if isinstance(data, dict):
                return {
                    "ok": True,
                    **_public(data),
                }

        except Exception:
            pass

    return {
        "ok": False,
        "codigo": "HELIX_BATCH_NO_ENCONTRADO",
        "job_id": key,
    }


async def login_helix(
    page,
    usuario: str,
    password: str,
    worker: int,
    log: logging.Logger,
):
    await page.goto(
        URL_HELIX,
        wait_until="domcontentloaded",
        timeout=90000,
    )
    await page.wait_for_timeout(1200)

    try:
        button = page.locator("#header-search_button")
        if await button.count() and await button.first.is_visible():
            log.info("SESION_EXISTENTE_OK")
            return
    except Exception:
        pass

    pass_box = None

    for sel in [
        "#login_user_password",
        'input[name="password"]',
        'input[placeholder="Password"]',
        'input[type="password"]:visible',
    ]:
        loc = page.locator(sel)
        try:
            for i in range(await loc.count()):
                x = loc.nth(i)
                if await x.is_visible():
                    xid = (await x.get_attribute("id") or "").lower()
                    ph = (await x.get_attribute("placeholder") or "").lower()
                    if "change" in xid or "new password" in ph:
                        continue
                    pass_box = x
                    break
        except Exception:
            pass
        if pass_box is not None:
            break

    if pass_box is None:
        raise RuntimeError("No encontre Password real de login.")

    user_box = None

    for sel in [
        "#login_user_name",
        'input[name*="user" i]',
        'input[id*="user" i]',
        'input[placeholder*="usuario" i]',
        'input[placeholder*="user" i]',
        'input[type="email"]',
        'input[type="text"]:visible',
    ]:
        loc = page.locator(sel)
        try:
            for i in range(await loc.count()):
                x = loc.nth(i)
                if not await x.is_visible():
                    continue
                xid = (await x.get_attribute("id") or "").lower()
                if "change" in xid or "dummy" in xid:
                    continue
                user_box = x
                break
        except Exception:
            pass
        if user_box is not None:
            break

    if user_box is None:
        raise RuntimeError("No encontre usuario de login.")

    await user_box.fill(usuario)
    await pass_box.fill(password)

    clicked = False
    patron = re.compile(
        r"ingresar|iniciar sesi[oó]n|login|sign in|entrar",
        re.I,
    )

    botones = page.locator('button, input[type="submit"]')

    for i in range(await botones.count()):
        b = botones.nth(i)
        try:
            if not await b.is_visible():
                continue

            txt = " ".join(
                [
                    (await b.inner_text() or "").strip(),
                    (await b.get_attribute("value") or "").strip(),
                    (await b.get_attribute("aria-label") or "").strip(),
                ]
            )

            if patron.search(txt):
                await b.click()
                clicked = True
                break
        except Exception:
            pass

    if not clicked:
        await pass_box.press("Enter")

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for frame in list(page.frames):
            try:
                texto = (
                    await frame.locator("body").inner_text(timeout=1000)
                ).casefold()
            except Exception:
                continue
            if any(
                mensaje in texto
                for mensaje in (
                    "incorrect username or password",
                    "invalid username or password",
                    "usuario o contraseña incorrect",
                    "usuario o contrasena incorrect",
                )
            ):
                raise RuntimeError(
                    "HELIX_CREDENCIALES_INVALIDAS: Helix rechazo el usuario o la clave. "
                    "Revise SMARTIT_USER y SMARTIT_PASSWORD en codigo_backend/.env."
                )
        if await page.locator("#header-search_button").first.is_visible():
            log.info("LOGIN_OK")
            return
        await page.wait_for_timeout(400)
    raise RuntimeError(
        "HELIX_LOGIN_NO_CONFIRMADO: No aparecio la busqueda despues de iniciar sesion."
    )


async def preparar_caja_busqueda(page):
    caja = page.locator("#globalSearchBox")

    try:
        if await caja.count() and await caja.first.is_visible():
            return caja.first
    except Exception:
        pass

    boton = page.locator("#header-search_button")
    await boton.wait_for(
        state="visible",
        timeout=30000,
    )
    await boton.click(timeout=20000)

    await caja.wait_for(
        state="visible",
        timeout=20000,
    )

    return caja.first


async def buscar_ticket(page, inc):
    """
    Busqueda comprobada tomada del flujo HELIX_INC_TITULO_SEGUNDO_PLANO_V2.

    Una sola busqueda funcional por INC:
      - abre Global Search
      - escribe INC y Enter una vez
      - localiza div.results-panel__item-layout[role="link"]
      - valida div.search-item-layout__id span EXACTO
      - hace click solo en la tarjeta exacta
      - espera 5 segundos para que PREVIEW termine de montar
    """
    esperado = str(inc or "").strip().upper()

    if not esperado:
        raise RuntimeError("INC_VACIO")

    # Limpiar evidencia anterior.
    page._atlas_opened_inc = ""
    page._atlas_search_card_text = ""

    caja = page.locator("#globalSearchBox")

    try:
        if not (await caja.count() and await caja.first.is_visible()):
            boton = page.locator("#header-search_button")
            await boton.wait_for(
                state="visible",
                timeout=20000,
            )
            await boton.click(timeout=20000)
            await caja.wait_for(
                state="visible",
                timeout=20000,
            )
    except Exception:
        boton = page.locator("#header-search_button")
        await boton.wait_for(
            state="visible",
            timeout=20000,
        )
        await boton.click(timeout=20000)
        await caja.wait_for(
            state="visible",
            timeout=20000,
        )

    caja = caja.first
    await caja.fill("")
    await caja.fill(esperado)
    await caja.press("Enter")

    print(
        f"[SEARCH55] INC={esperado} ENTER=1",
        flush=True,
    )

    panel = page.locator("div.search__results-panel")

    try:
        await panel.wait_for(
            state="visible",
            timeout=30000,
        )
    except Exception:
        pass

    async def encontrar_visible():
        candidatos = page.locator('div.results-panel__item-layout[role="link"]')

        total = await candidatos.count()

        for i in range(total):
            item = candidatos.nth(i)
            id_span = item.locator("div.search-item-layout__id span")

            if not await id_span.count():
                continue

            try:
                ticket_id = clean(await id_span.first.inner_text(timeout=1500)).upper()
            except Exception:
                continue

            if ticket_id != esperado:
                continue

            try:
                card_text = clean(await item.inner_text(timeout=2500))
            except Exception:
                card_text = ""

            print(
                f"[SEARCH55] INC={esperado} "
                f"CARD_EXACT=SI INDEX={i} "
                f"REAL_ID={ticket_id!r}",
                flush=True,
            )

            page._atlas_search_card_text = card_text

            try:
                await item.scroll_into_view_if_needed()
            except Exception:
                pass

            await item.click(timeout=20000)

            page._atlas_opened_inc = esperado

            print(
                f"[SEARCH55] INC={esperado} " f"CARD_CLICK=OK WAIT_AFTER=5S",
                flush=True,
            )

            await page.wait_for_timeout(5000)

            return True

        return False

    if await encontrar_visible():
        return

    mostrar_todo = page.locator('[ux-id="show-all-link"]')

    if await mostrar_todo.count():
        try:
            if await mostrar_todo.first.is_visible():
                await mostrar_todo.first.click(timeout=15000)
                print(
                    f"[SEARCH55] INC={esperado} " f"SHOW_ALL=CLICK",
                    flush=True,
                )
                await page.wait_for_timeout(2000)
        except Exception:
            pass

    viewport = page.locator("div.results-panel__items-viewport")

    # Esto no repite la busqueda ni Enter;
    # solo pagina/scroll dentro del resultado ya cargado.
    for _ in range(30):
        if await encontrar_visible():
            return

        try:
            if await viewport.count():
                await viewport.first.evaluate("(el)=>{el.scrollTop=el.scrollHeight;}")
            else:
                await page.mouse.wheel(0, 2400)
        except Exception:
            await page.mouse.wheel(0, 2400)

        await page.wait_for_timeout(1000)

    raise RuntimeError(f"INC_NO_ENCONTRADO_UNICO:{esperado}")


async def buscar_incidente(page, inc):
    return await buscar_ticket(page, inc)


async def validar_inc_abierto(page, inc):
    """
    Valida que el incidente solicitado sea realmente el abierto.

    Soporta dos escenarios:
      1. Preview antiguo: targetForm=Incident + botón
         "Ver incidencia completa".
      2. Flujo nuevo: la pagina ya está directamente en /incidentPV/
         y el frame tiene targetForm=Incident.

    La identidad principal sigue siendo _atlas_opened_inc.
    """

    esperado = str(inc or "").strip().upper()

    abierto = (
        str(
            getattr(
                page,
                "_atlas_opened_inc",
                "",
            )
            or ""
        )
        .strip()
        .upper()
    )

    # ---------------------------------------------------------
    # 1. IDENTIDAD DEL INC
    # ---------------------------------------------------------

    if abierto != esperado:
        print(
            f"[IDENTITY55] INC={esperado} " f"FAIL=CARD_PROOF OPENED={abierto!r}",
            flush=True,
        )

        return (
            False,
            "INC_ABIERTO_NO_CONFIRMADO",
            abierto,
        )

    # ---------------------------------------------------------
    # 2. ESPERAR EVIDENCIA REAL DE VISTA INCIDENT
    # ---------------------------------------------------------

    deadline = time.perf_counter() + 30.0

    while time.perf_counter() < deadline:

        # Revisamos todas las paginas por si Helix abre otra.
        for candidate_page in list(page.context.pages):

            try:
                page_url = candidate_page.url or ""
            except Exception:
                page_url = ""

            # La página principal ya puede estar directamente
            # en la vista completa del incidente.
            page_incidentpv = "/incidentPV/" in page_url

            for frame in candidate_page.frames:

                try:
                    frame_url = frame.url or ""
                except Exception:
                    frame_url = ""

                frame_incident = "targetForm=Incident" in frame_url

                # -------------------------------------------------
                # CASO NUEVO:
                # Ya estamos en incidentPV y existe el frame Incident.
                # -------------------------------------------------

                if page_incidentpv and frame_incident:

                    print(
                        f"[IDENTITY55] INC={esperado} "
                        f"PASS=INCIDENTPV_PLUS_TARGETFORM "
                        f"PAGE={page_url!r} "
                        f"FRAME={frame_url!r}",
                        flush=True,
                    )

                    candidate_page._atlas_opened_inc = esperado

                    return (
                        True,
                        "INC_EXACTO_INCIDENTPV",
                        esperado,
                    )

                # -------------------------------------------------
                # CASO TAMBIEN VALIDO:
                # Aunque la URL principal todavía no haya cambiado,
                # el frame ya es claramente un Incident.
                # -------------------------------------------------

                if frame_incident:
                    try:
                        body = frame.locator("body")

                        if await body.count():
                            texto = await body.inner_text(timeout=1500)

                            texto = clean(texto).upper()

                            # El INC solicitado aparece dentro del
                            # formulario Incident.
                            if esperado in texto:

                                print(
                                    f"[IDENTITY55] INC={esperado} "
                                    f"PASS=TARGETFORM_PLUS_INC_VISIBLE "
                                    f"FRAME={frame_url!r}",
                                    flush=True,
                                )

                                candidate_page._atlas_opened_inc = esperado

                                return (
                                    True,
                                    "INC_EXACTO_FRAME_INCIDENT",
                                    esperado,
                                )

                    except Exception:
                        pass

        await page.wait_for_timeout(250)

    # ---------------------------------------------------------
    # 3. DIAGNOSTICO FINAL
    # ---------------------------------------------------------

    urls = []

    for candidate_page in list(page.context.pages):

        try:
            purl = candidate_page.url or ""
        except Exception:
            purl = ""

        for frame in candidate_page.frames:

            try:
                furl = frame.url or ""
            except Exception:
                furl = ""

            urls.append(
                {
                    "page": purl,
                    "frame": furl,
                }
            )

    print(
        f"[IDENTITY55] INC={esperado} "
        f"FAIL=INCIDENT_VIEW_NO_CONFIRMADA "
        f"OPENED={abierto!r} "
        f"URLS={urls!r}",
        flush=True,
    )

    return (
        False,
        "INC_ABIERTO_NO_CONFIRMADO",
        abierto,
    )

    selectors = [
        "button#304428981",
        'button[testid="ar304428981"]',
        'button[automationid="1094960774"]',
        'button:has-text("Ver incidencia completa")',
    ]

    deadline = time.perf_counter() + 30.0

    while time.perf_counter() < deadline:
        for frame in page.frames:
            u = frame.url or ""

            if "targetForm=Incident" not in u:
                continue

            for sel in selectors:
                try:
                    loc = frame.locator(sel)

                    for i in range(
                        min(
                            await loc.count(),
                            10,
                        )
                    ):
                        el = loc.nth(i)

                        if not await el.is_visible():
                            continue

                        print(
                            f"[IDENTITY55] INC={esperado} "
                            f"PASS=CARD_EXACT_PLUS_FULL_BUTTON "
                            f"FRAME={u!r}",
                            flush=True,
                        )

                        return (
                            True,
                            "INC_EXACTO_CARD_REAL",
                            esperado,
                        )
                except Exception:
                    continue

        await page.wait_for_timeout(250)

    print(
        f"[IDENTITY55] INC={esperado} " f"FAIL=FULL_BUTTON_NO_VISIBLE",
        flush=True,
    )

    return (
        False,
        "INC_ABIERTO_NO_CONFIRMADO",
        abierto,
    )


async def extraer_titulo(page):
    for _ in range(20):
        for frame in page.frames:
            try:
                h2 = frame.locator("h2#ar1000000000_data")

                if await h2.count() and await h2.first.is_visible():
                    txt = clean(await h2.first.inner_text())
                    if txt:
                        return txt
            except Exception:
                pass

        await page.wait_for_timeout(300)

    return ""


async def _atlas_capture_tasks_fallback(page, esperado):
    esperado = str(esperado or "").strip().upper()

    task_selectors = [
        'button[data-testid="adapt-tabs-dropdown-1_tab_0"]',
        'button[role="tab"]:has-text("Tasks")',
        'button[role="tab"]:has-text("Tareas")',
        '[role="tab"]:has-text("Tasks")',
        '[role="tab"]:has-text("Tareas")',
    ]

    async def find_task():
        for frame in page.frames:
            if "targetForm=Incident" not in (frame.url or ""):
                continue

            for sel in task_selectors:
                try:
                    loc = frame.locator(sel)
                    for i in range(min(await loc.count(), 20)):
                        el = loc.nth(i)
                        try:
                            if await el.is_visible():
                                txt = (await el.inner_text()).strip()
                                if (
                                    sel
                                    == 'button[data-testid="adapt-tabs-dropdown-1_tab_0"]'
                                    or re.search(r"\b(?:Tasks|Tareas)\b", txt, re.I)
                                ):
                                    return frame, el, sel, txt
                        except Exception:
                            continue
                except Exception:
                    continue
        return None

    task = None
    deadline = time.perf_counter() + 20.0

    while time.perf_counter() < deadline:
        task = await find_task()
        if task:
            break
        await page.wait_for_timeout(250)

    if not task:
        print(f"[TASKS5564] INC={esperado} TAB_TASKS=NO", flush=True)

        probe_js = r"""
        () => {
          const norm=s => (s || '').replace(/\s+/g,' ').trim();

          const visible=el => {
            if(!el) return false;
            const st=getComputedStyle(el);
            if(st.display==='none' || st.visibility==='hidden') return false;
            const r=el.getBoundingClientRect();
            return r.width>0 && r.height>0;
          };

          const selector=[
            'button',
            'a',
            '[role="tab"]',
            '[role="button"]',
            '[data-testid]',
            '[testid]',
            '[class*="tab"]',
            '[class*="Tab"]'
          ].join(',');

          const out=[];

          for(const el of document.querySelectorAll(selector)){
            if(!visible(el)) continue;

            const text=norm(el.innerText || el.textContent || '');
            const aria=norm(el.getAttribute('aria-label') || '');
            const title=norm(el.getAttribute('title') || '');
            const testid=norm(
              el.getAttribute('data-testid') ||
              el.getAttribute('testid') ||
              ''
            );

            const hay=[text,aria,title,testid].join(' ');

            if(!/\b(tarea|tareas|task|tasks)\b/i.test(hay)){
              continue;
            }

            out.push({
              tag:el.tagName,
              text,
              aria,
              title,
              role:el.getAttribute('role') || '',
              id:el.id || '',
              testid,
              cls:typeof el.className==='string' ? el.className : '',
              disabled:!!el.disabled,
              outer:(el.outerHTML || '').slice(0,900)
            });
          }

          return out.slice(0,100);
        }
        """

        print(
            f"[TASKS556PROBE] INC={esperado} FRAMES_TOTAL={len(page.frames)}",
            flush=True,
        )

        for idx, probe_frame in enumerate(page.frames):
            try:
                frame_url = probe_frame.url or ""
            except Exception:
                frame_url = ""

            print(
                f"[TASKS556PROBE] FRAME_INDEX={idx} URL={frame_url!r}",
                flush=True,
            )

            try:
                hits = await asyncio.wait_for(
                    probe_frame.evaluate(probe_js),
                    timeout=3.0,
                )
            except Exception as exc:
                print(
                    f"[TASKS556PROBE] FRAME_INDEX={idx} EVAL_ERROR={type(exc).__name__}:{exc}",
                    flush=True,
                )
                continue

            print(
                f"[TASKS556PROBE] FRAME_INDEX={idx} HITS={len(hits or [])}",
                flush=True,
            )

            for hidx, hit in enumerate(hits or []):
                print(
                    "[TASKS556PROBE] "
                    f"FRAME_INDEX={idx} HIT={hidx} "
                    f"TAG={hit.get('tag')!r} "
                    f"TEXT={hit.get('text')!r} "
                    f"ARIA={hit.get('aria')!r} "
                    f"TITLE={hit.get('title')!r} "
                    f"ROLE={hit.get('role')!r} "
                    f"ID={hit.get('id')!r} "
                    f"TESTID={hit.get('testid')!r} "
                    f"CLASS={hit.get('cls')!r} "
                    f"DISABLED={hit.get('disabled')!r} "
                    f"OUTER={hit.get('outer')!r}",
                    flush=True,
                )

        return None

    frame, el, sel, txt = task
    print(
        f"[TASKS5564] INC={esperado} TAB_TASKS=SI SEL={sel!r} TEXT={txt!r}", flush=True
    )

    try:
        await el.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        await el.click(timeout=12000)
    except Exception:
        try:
            await el.click(force=True, timeout=12000)
        except Exception:
            return None

    print(f"[TASKS5564] INC={esperado} TAB_CLICK=OK", flush=True)
    await page.wait_for_timeout(2000)

    capture_js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();
      const visible=el => {
        if(!el) return false;
        const s=getComputedStyle(el);
        if(s.display==='none' || s.visibility==='hidden') return false;
        const r=el.getBoundingClientRect();
        return r.width>0 && r.height>0;
      };

      const statuses=[
        ['Pendiente',/\bPendiente\b/i],
        ['Pending',/\bPending\b/i],
        ['Asignado',/\bAsignado\b/i],
        ['Assigned',/\bAssigned\b/i],
        ['Cerrado',/\bCerrado\b/i],
        ['Closed',/\bClosed\b/i],
        ['Cancelado',/\bCancelad[oa]\b/i],
        ['Cancelled',/\bCancelled\b/i],
        ['Completado',/\bCompletad[oa]\b/i],
        ['Completed',/\bCompleted\b/i],
        ['Resuelto',/\bResuelt[oa]\b/i],
        ['Resolved',/\bResolved\b/i],
        ['En progreso',/\bEn progreso\b/i],
        ['In Progress',/\bIn Progress\b/i],
        ['Abierto',/\bAbiert[oa]\b/i],
        ['Open',/\bOpen\b/i]
      ];

      const stateFrom=text => {
        for(const [name,re] of statuses){
          if(re.test(text)) return name;
        }
        return '';
      };

      const found=new Map();
      const nodes=[
        ...document.querySelectorAll(
          "tbody tr,[role='row'],[class*='row'],[class*='record'],[class*='item']"
        )
      ];

      for(const node of nodes){
        if(!visible(node)) continue;
        const text=norm(node.innerText || node.textContent || '');
        if(!text || text.length>3000) continue;

        const ids=[
          ...new Set(
            (text.match(/\b(?:TAS|TA)\d{5,}\b/gi) || [])
              .map(x=>x.toUpperCase())
          )
        ];

        for(const id of ids){
          const cells=[...node.querySelectorAll('td,[role="cell"]')]
            .filter(visible)
            .map(x=>norm(x.innerText || x.textContent || ''));

          let estado='';
          for(const c of cells){
            estado=stateFrom(c);
            if(estado) break;
          }
          if(!estado) estado=stateFrom(text);

          found.set(id,{
            id,
            tipo_relacion:'',
            titulo:'',
            estado,
            usuario_asignado:'',
            grupo_asignado:'',
            crear_fecha:'',
            tipo_ticket:'Tarea'
          });
        }
      }

      if(!found.size){
        for(const node of document.querySelectorAll('body *')){
          if(!visible(node)) continue;
          const text=norm(node.innerText || node.textContent || '');
          if(!text || text.length>500) continue;

          const ids=[
            ...new Set(
              (text.match(/\b(?:TAS|TA)\d{5,}\b/gi) || [])
                .map(x=>x.toUpperCase())
            )
          ];

          for(const id of ids){
            if(!found.has(id)){
              found.set(id,{
                id,
                tipo_relacion:'',
                titulo:'',
                estado:stateFrom(text),
                usuario_asignado:'',
                grupo_asignado:'',
                crear_fecha:'',
                tipo_ticket:'Tarea'
              });
            }
          }
        }
      }

      return [...found.values()];
    }
    """

    rows = []
    deadline = time.perf_counter() + 15.0

    while time.perf_counter() < deadline and not rows:
        frames = [frame] if frame in page.frames else []

        for f in page.frames:
            if f not in frames and "targetForm=Incident" in (f.url or ""):
                frames.append(f)

        for f in frames:
            try:
                data = await asyncio.wait_for(f.evaluate(capture_js), timeout=2.0)
            except Exception:
                continue

            if data:
                rows = data
                frame = f
                break

        if not rows:
            await page.wait_for_timeout(300)

    if not rows:
        print(f"[TASKS5564] INC={esperado} TASK_ROWS=0", flush=True)
        return None

    unique = {}
    for row in rows:
        rid = str(row.get("id") or "").upper()
        if not re.match(r"^(?:TAS|TA)\d{5,}$", rid):
            continue
        unique[rid] = row

    rows = list(unique.values())

    if not rows:
        return None

    page._atlas_related_payload = {
        "inc": esperado,
        "total": len(rows),
        "headers": [
            "ID",
            "Tipo de relación",
            "Título",
            "Estado",
            "Usuario asignado",
            "Grupo asignado",
            "Crear fecha",
            "Tipo Ticket",
        ],
        "rows": rows,
        "frame_url": frame.url or "",
        "source": "TASKS_TAB_V556",
    }

    print(
        f"[TASKS5564] INC={esperado} CAPTURE=OK "
        f"TA={[r.get('id') for r in rows]!r} "
        f"STATES={[(r.get('id'),r.get('estado')) for r in rows]!r}",
        flush=True,
    )

    return frame, "TASKS_TAB_CAPTURED_V556"


async def localizar_related_frame_y_click(page, inc=""):
    """
    Flujo compatible con dos escenarios:

    1. Flujo viejo:
       preview Incident -> Ver incidencia completa -> Elementos relacionados.

    2. Flujo nuevo:
       ya estamos directamente en /incidentPV/
       -> Elementos relacionados.

    Después captura la tabla y hace scroll de WO/TAS.
    """

    esperado = str(inc or "").strip().upper()
    page._atlas_related_payload = None

    full_selectors = [
        "button#304428981",
        'button[testid="ar304428981"]',
        'button[automationid="1094960774"]',
        'button:has-text("Ver incidencia completa")',
    ]

    related_selectors = [
        'button[data-testid="adapt-tabs-dropdown-1_tab_2"]',
        'button[role="tab"][data-testid="adapt-tabs-dropdown-1_tab_2"]',
        'button[role="tab"][data-testid$="_tab_2"]',
        'button[role="tab"]:has-text("Elementos relacionados")',
        '[role="tab"]:has-text("Elementos relacionados")',
    ]

    async def find_visible(selectors):
        for candidate_page in list(page.context.pages):
            if candidate_page.is_closed():
                continue

            page_url = candidate_page.url or ""

            for frame in candidate_page.frames:
                u = frame.url or ""

                if "targetForm=Incident" not in u and "incidentPV" not in page_url:
                    continue

                for sel in selectors:
                    try:
                        loc = frame.locator(sel)

                        for i in range(min(await loc.count(), 20)):
                            el = loc.nth(i)

                            try:
                                if await el.is_visible():
                                    return (
                                        candidate_page,
                                        frame,
                                        el,
                                        sel,
                                    )
                            except Exception:
                                continue

                    except Exception:
                        continue

        return None

    # ---------------------------------------------------------
    # 1. DETECTAR SI YA ESTAMOS EN INCIDENTPV
    # ---------------------------------------------------------

    ya_en_incidentpv = False

    for candidate_page in list(page.context.pages):
        try:
            page_url = candidate_page.url or ""
        except Exception:
            page_url = ""

        if "/incidentPV/" not in page_url:
            continue

        for frame in candidate_page.frames:
            try:
                frame_url = frame.url or ""
            except Exception:
                frame_url = ""

            if "targetForm=Incident" in frame_url:
                ya_en_incidentpv = True

                print(
                    f"[RELATED554] INC={esperado} "
                    f"YA_EN_INCIDENTPV=SI "
                    f"PAGE={page_url!r} "
                    f"FRAME={frame_url!r}",
                    flush=True,
                )

                break

        if ya_en_incidentpv:
            break

    # ---------------------------------------------------------
    # 2. SOLO SI NO ESTAMOS EN INCIDENTPV:
    #    BUSCAR "VER INCIDENCIA COMPLETA"
    # ---------------------------------------------------------

    if not ya_en_incidentpv:

        full = None
        deadline = time.perf_counter() + 30.0

        while time.perf_counter() < deadline:
            full = await find_visible(full_selectors)

            if full:
                break

            await page.wait_for_timeout(250)

        if not full:
            raise RuntimeError(f"VER_INCIDENCIA_COMPLETA_NO_ENCONTRADO:" f"{esperado}")

        (
            full_page,
            full_frame,
            full_el,
            full_sel,
        ) = full

        print(
            f"[RELATED554] INC={esperado} " f"FULL_FOUND=SI " f"SEL={full_sel!r}",
            flush=True,
        )

        try:
            await full_el.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass

        try:
            await full_el.click(timeout=15000)

        except Exception:
            await full_el.click(
                force=True,
                timeout=15000,
            )

        print(
            f"[RELATED554] INC={esperado} " f"FULL_CLICK=OK " f"WAIT_AFTER=5S",
            flush=True,
        )

        await page.wait_for_timeout(5000)

    else:
        print(
            f"[RELATED554] INC={esperado} "
            f"FULL_CLICK=SKIP "
            f"RAZON=YA_EN_INCIDENTPV",
            flush=True,
        )

    # ---------------------------------------------------------
    # 3. ELEMENTOS RELACIONADOS
    # ---------------------------------------------------------

    related = None
    deadline = time.perf_counter() + 40.0

    while time.perf_counter() < deadline:

        related = await find_visible(related_selectors)

        if related:
            break

        await page.wait_for_timeout(250)

    if not related:

        fallback = await _atlas_capture_tasks_fallback(
            page,
            esperado,
        )

        if fallback:
            return fallback

        raise RuntimeError(f"ELEMENTOS_RELACIONADOS_NO_ENCONTRADO:" f"{esperado}")

    (
        related_page,
        related_frame,
        related_el,
        related_sel,
    ) = related

    frame_url = related_frame.url or ""

    m = re.search(
        r"[?&]targetId=([^&#]+)",
        frame_url,
        re.I,
    )

    target_id = m.group(1) if m else ""

    print(
        f"[RELATED554] INC={esperado} "
        f"TAB_FOUND=SI "
        f"TARGET_ID={target_id!r} "
        f"SEL={related_sel!r}",
        flush=True,
    )

    try:
        await related_el.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        await related_el.click(timeout=15000)

    except Exception:
        await related_el.click(
            force=True,
            timeout=15000,
        )

    print(
        f"[RELATED554] INC={esperado} " f"TAB_CLICK=OK " f"WAIT_AFTER=3S",
        flush=True,
    )

    await page.wait_for_timeout(3000)

    # ---------------------------------------------------------
    # 4. CAPTURA TABLA
    # ---------------------------------------------------------

    table_js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();

      const tables=[...document.querySelectorAll('adapt-table')];

      const table=
        document.querySelector('adapt-table#ar304424281')
        || document.querySelector('#ar304424281')
        || tables.find(t => {
          const txt=norm(t.innerText || t.textContent || '');

          return (
            txt.includes('Tipo de relación')
            && txt.includes('Estado')
            && txt.includes('Crear fecha')
            && txt.includes('Tipo Ticket')
          );
        });

      if(!table) return null;

      const headers=[
        ...table.querySelectorAll('thead th')
      ].map(
        x => norm(
          x.innerText ||
          x.textContent ||
          ''
        )
      );

      const rows=[];

      for(
        const tr of table.querySelectorAll(
          'tbody tr'
        )
      ){
        const cells=[
          ...tr.querySelectorAll('td')
        ].map(
          td => norm(
            td.innerText ||
            td.textContent ||
            ''
          )
        );

        if(!cells.length) continue;

        const id=(
          cells[0] ||
          ''
        ).toUpperCase();

        if(
          !/^(?:INC|WO|TAS|TA)\d{5,}$/.test(id)
        ){
          continue;
        }

        rows.push({
          id,
          tipo_relacion:
            cells[1] || '',
          titulo:
            cells[2] || '',
          estado:
            (
              cells[3]
              ||
              cells.find(
                c =>
                  /^(Assigned|Asignado|Pending|Pendiente|In Progress|En curso|Work In Progress|Completed|Completado|Closed|Cerrado|Resolved|Resuelto|Cancelled|Canceled|Cancelado|Rechazado|Rejected)$/i
                    .test(c)
              )
              ||
              ''
            ),
          usuario_asignado:
            cells[4] || '',
          grupo_asignado:
            cells[5] || '',
          crear_fecha:
            cells[6] || '',
          tipo_ticket:
            cells[7] || ''
        });
      }

      if(!rows.length) {
        return null;
      }

      const txt=norm(
        table.innerText ||
        table.textContent ||
        ''
      );

      const mt=txt.match(
        /(\d+)\s+filas?\s+en\s+total/i
      );

      return {
        total:
          mt
            ? Number(mt[1])
            : rows.length,

        headers,
        rows
      };
    }
    """

    deadline = time.perf_counter() + 25.0
    captured_frame = None
    captured_data = None

    while time.perf_counter() < deadline:

        candidatos = []

        try:
            if related_frame in page.frames:
                candidatos.append(related_frame)
        except Exception:
            pass

        for frame in page.frames:

            if frame in candidatos:
                continue

            u = frame.url or ""

            if "targetForm=Incident" not in u:
                continue

            if target_id and f"targetId={target_id}" not in u:
                continue

            candidatos.append(frame)

        for frame in candidatos:

            try:
                data = await asyncio.wait_for(
                    frame.evaluate(table_js),
                    timeout=2.0,
                )
            except Exception:
                continue

            if data and data.get("rows"):
                captured_frame = frame
                captured_data = data
                break

        if captured_data:
            break

        await page.wait_for_timeout(350)

    # ---------------------------------------------------------
    # 5. FALLBACK SI NO HAY TABLA
    # ---------------------------------------------------------

    if not captured_data or captured_frame is None:

        fallback = await _atlas_capture_tasks_fallback(
            page,
            esperado,
        )

        if fallback:
            return fallback

        empty_frame_v443 = await _atlas_prepare_empty_related_if_confirmed_v443(
            page,
            esperado,
        )

        if empty_frame_v443 is not None:
            return (
                empty_frame_v443,
                "EMPTY_RELATED_AND_TASKS_CONFIRMED_V443",
            )

        raise RuntimeError(f"TABLA_RELACIONADOS_TIMEOUT_25S:" f"{esperado}")

    # ---------------------------------------------------------
    # 6. DATOS VISIBLES
    # ---------------------------------------------------------

    visible_rows = list(captured_data.get("rows") or [])

    all_ids = {str(r.get("id") or "").upper() for r in visible_rows}

    all_wos = {x for x in all_ids if x.startswith("WO")}

    all_tas = {x for x in all_ids if (x.startswith("TAS") or x.startswith("TA"))}

    print(
        f"[RELATED554] INC={esperado} "
        f"TABLE_VISIBLE=OK "
        f"ROWS={len(visible_rows)} "
        f"WO={len(all_wos)} "
        f"TA={len(all_tas)}",
        flush=True,
    )

    # ---------------------------------------------------------
    # 7. DETECTAR SCROLLER
    # ---------------------------------------------------------

    detect_js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();

      const all=[
        ...document.querySelectorAll(
          'body *'
        )
      ];

      const candidates=[];

      for(const el of all){

        const s=getComputedStyle(el);

        const scrollable=(
          (
            s.overflowY==='auto'
            ||
            s.overflowY==='scroll'
          )
          &&
          el.scrollHeight >
            el.clientHeight + 20
          &&
          el.clientHeight > 80
        );

        if(!scrollable) continue;

        const txt=norm(
          el.innerText ||
          el.textContent ||
          ''
        );

        const woCount=new Set(
          (
            txt.match(
              /\bWO\d{7,}\b/gi
            )
            ||
            []
          ).map(
            x=>x.toUpperCase()
          )
        ).size;

        const taCount=new Set(
          (
            txt.match(
              /\b(?:TA|TAS)\d{5,}\b/gi
            )
            ||
            []
          ).map(
            x=>x.toUpperCase()
          )
        ).size;

        const rowCount=
          el.querySelectorAll(
            "tr,[role='row'],[class*='row']"
          ).length;

        const hasTable=
          !!el.querySelector(
            "table,[role='grid'],[class*='grid'],[class*='table'],adapt-table"
          );

        const delta=
          el.scrollHeight -
          el.clientHeight;

        const score=
          (
            (woCount+taCount)>0
              ? 100000
              : 0
          )
          +
          (woCount+taCount)*5000
          +
          (
            rowCount>0
              ? 20000
              : 0
          )
          +
          Math.min(
            rowCount,
            500
          )*50
          +
          (
            hasTable
              ? 10000
              : 0
          )
          +
          Math.min(
            delta,
            100000
          )/10;

        candidates.push({
          el,
          score,
          woCount,
          taCount,
          rowCount,
          sh:el.scrollHeight,
          ch:el.clientHeight
        });
      }

      candidates.sort(
        (a,b)=>b.score-a.score
      );

      const c=
        candidates[0] ||
        null;

      if(!c) return null;

      c.el.dataset.helixWoScroller='1';

      c.el.scrollTop=0;

      c.el.dispatchEvent(
        new Event(
          'scroll',
          {bubbles:true}
        )
      );

      return {
        score:c.score,
        woCount:c.woCount,
        taCount:c.taCount,
        rowCount:c.rowCount,
        sh:c.sh,
        ch:c.ch
      };
    }
    """

    try:
        contexto = await asyncio.wait_for(
            captured_frame.evaluate(detect_js),
            timeout=2.0,
        )

    except Exception:
        contexto = None

    detail_by_id = {
        str(r.get("id") or "").upper(): dict(r)
        for r in visible_rows
        if str(r.get("id") or "").upper()
    }

    if contexto:

        print(
            f"[RELATED554] INC={esperado} "
            f"SCROLLER=OK "
            f"SH={contexto.get('sh')} "
            f"CH={contexto.get('ch')}",
            flush=True,
        )

        read_scroll_js = r"""
        () => {
          const sc=document.querySelector(
            '[data-helix-wo-scroller="1"]'
          );

          if(!sc) return null;

          const norm=s => (s || '').replace(/\s+/g,' ').trim();

          let rows=[
            ...sc.querySelectorAll(
              "tr,[role='row']"
            )
          ];

          if(!rows.length){
            rows=[
              ...sc.querySelectorAll(
                "[class*='row'],[class*='record'],[class*='item']"
              )
            ];
          }

          const texts=[];
          const seen=new Set();
          const details=[];

          for(const el of rows){

            const t=norm(
              el.innerText ||
              el.textContent ||
              ''
            );

            if(
              !t
              ||
              t.length>2500
              ||
              seen.has(t)
            ){
              continue;
            }

            if(
              /\b(?:WO|TA|TAS)\d{5,}\b/i.test(t)
            ){
              seen.add(t);
              texts.push(t);
            }

            const cells=[
              ...el.querySelectorAll('td')
            ].map(
              td =>
                norm(
                  td.innerText ||
                  td.textContent ||
                  ''
                )
            );

            if(cells.length){

              const id=(
                cells[0] ||
                ''
              ).toUpperCase();

              if(
                /^(?:WO|TAS|TA)\d{5,}$/.test(id)
              ){
                details.push({
                  id,
                  tipo_relacion:
                    cells[1] || '',
                  titulo:
                    cells[2] || '',
                  estado:
                    cells[3] || '',
                  usuario_asignado:
                    cells[4] || '',
                  grupo_asignado:
                    cells[5] || '',
                  crear_fecha:
                    cells[6] || '',
                  tipo_ticket:
                    cells[7] || ''
                });
              }
            }
          }

          const visible=norm(
            sc.innerText ||
            sc.textContent ||
            ''
          );

          const combined=
            texts.join(' ')
            +
            ' '
            +
            visible;

          const wos=[
            ...new Set(
              (
                combined.match(
                  /\bWO\d{7,}\b/gi
                )
                ||
                []
              ).map(
                x=>x.toUpperCase()
              )
            )
          ];

          const tas=[
            ...new Set(
              (
                combined.match(
                  /\b(?:TA|TAS)\d{5,}\b/gi
                )
                ||
                []
              ).map(
                x=>x.toUpperCase()
              )
            )
          ];

          return {
            wos,
            tas,
            details,
            top:sc.scrollTop,
            sh:sc.scrollHeight,
            ch:sc.clientHeight
          };
        }
        """

        move_js = r"""
        () => {
          const sc=document.querySelector(
            '[data-helix-wo-scroller="1"]'
          );

          if(!sc){
            return {
              moved:false,
              top:0,
              max:0
            };
          }

          const old=sc.scrollTop;

          const max=Math.max(
            0,
            sc.scrollHeight -
            sc.clientHeight
          );

          const step=Math.max(
            100,
            Math.floor(
              sc.clientHeight*0.55
            )
          );

          const target=Math.min(
            max,
            old+step
          );

          sc.scrollTop=target;

          sc.dispatchEvent(
            new Event(
              'scroll',
              {bubbles:true}
            )
          );

          return {
            moved:
              target>old+1,
            top:target,
            max
          };
        }
        """

        scroll_deadline = time.perf_counter() + 15.0

        last_top = -1
        stable = 0
        paso = 0

        last_count = len(all_wos) + len(all_tas)

        while time.perf_counter() < scroll_deadline and paso < 120:

            paso += 1

            try:
                d = await asyncio.wait_for(
                    captured_frame.evaluate(read_scroll_js),
                    timeout=2.0,
                )

            except Exception:
                break

            if not d:
                break

            for x in d.get("wos") or []:
                all_wos.add(str(x).upper())

            for x in d.get("tas") or []:
                all_tas.add(str(x).upper())

            for detail in d.get("details") or []:

                rid = str(detail.get("id") or "").upper()

                if not rid:
                    continue

                prev = detail_by_id.get(rid) or {}

                merged = dict(prev)

                for key in (
                    "id",
                    "tipo_relacion",
                    "titulo",
                    "estado",
                    "usuario_asignado",
                    "grupo_asignado",
                    "crear_fecha",
                    "tipo_ticket",
                ):

                    value = detail.get(key)

                    if value not in (
                        None,
                        "",
                    ):
                        merged[key] = value

                merged["id"] = rid

                detail_by_id[rid] = merged

            count = len(all_wos) + len(all_tas)

            if count == last_count:
                stable += 1
            else:
                stable = 0

            last_count = count

            top = int(d.get("top") or 0)

            sh = int(d.get("sh") or 0)

            ch = int(d.get("ch") or 0)

            max_top = max(0, sh - ch)

            if paso == 1 or paso % 10 == 0 or top >= max_top - 2:
                print(
                    f"[RELATED554] "
                    f"INC={esperado} "
                    f"SCROLL "
                    f"paso={paso} "
                    f"pos={top}/{max_top} "
                    f"WO={len(all_wos)} "
                    f"TA={len(all_tas)}",
                    flush=True,
                )

            if top >= max_top - 2:
                break

            if top == last_top and stable >= 3:
                break

            last_top = top

            try:
                move = await asyncio.wait_for(
                    captured_frame.evaluate(move_js),
                    timeout=2.0,
                )

            except Exception:
                break

            if not move.get("moved"):
                break

            await page.wait_for_timeout(120)

    # ---------------------------------------------------------
    # 8. RECONSTRUIR DETALLE FINAL
    # ---------------------------------------------------------

    rebuilt = []
    used = set()

    for row in visible_rows:

        rid = str(row.get("id") or "").upper()

        if rid and rid in detail_by_id:

            merged = dict(row)

            for key, value in detail_by_id[rid].items():

                if value not in (
                    None,
                    "",
                ):
                    merged[key] = value

            rebuilt.append(merged)
            used.add(rid)

        else:
            rebuilt.append(row)

    for rid in sorted(all_wos | all_tas):

        if rid in used:
            continue

        d = detail_by_id.get(rid) or {
            "id": rid,
            "tipo_relacion": "",
            "titulo": "",
            "estado": "",
            "usuario_asignado": "",
            "grupo_asignado": "",
            "crear_fecha": "",
            "tipo_ticket": "",
        }

        d["id"] = rid

        rebuilt.append(d)
        used.add(rid)

    visible_rows = rebuilt

    page._atlas_related_payload = {
        "inc": esperado,
        "total": max(
            int(captured_data.get("total") or 0),
            len(visible_rows),
        ),
        "headers": captured_data.get("headers") or [],
        "rows": visible_rows,
        "frame_url": captured_frame.url or "",
    }

    print(
        f"[RELATED554] INC={esperado} "
        f"FINAL_CAPTURE=OK "
        f"ROWS={len(visible_rows)} "
        f"WO={sorted(all_wos)!r} "
        f"TA={sorted(all_tas)!r}",
        flush=True,
    )

    return (
        captured_frame,
        "FULL_RELATED_TABLE_SCROLL_CAPTURED",
    )


async def extraer_related(
    page,
    inc,
    frame,
    diag_dir: Path,
):
    """
    V5.5.2
    Consume el snapshot atómico capturado por RELATED552.
    No vuelve a tocar el DOM de SmartIT.
    """
    esperado = str(inc or "").strip().upper()

    payload = getattr(
        page,
        "_atlas_related_payload",
        None,
    )

    if not isinstance(payload, dict):
        print(
            f"[RELATED552] EXTRACT=NO_PAYLOAD INC={esperado}",
            flush=True,
        )
        return (
            [],
            [],
            None,
            False,
            1,
            [],
        )

    payload_inc = str(payload.get("inc") or "").strip().upper()

    if payload_inc != esperado:
        print(
            f"[RELATED552] EXTRACT=PAYLOAD_MISMATCH "
            f"INC={esperado} PAYLOAD_INC={payload_inc!r}",
            flush=True,
        )
        return (
            [],
            [],
            None,
            False,
            1,
            [],
        )

    detalles = []

    for raw in payload.get("rows") or []:
        rid = str(raw.get("id") or "").strip().upper()

        if not rid:
            continue

        if rid.startswith("WO"):
            tipo = "WO"
        elif rid.startswith("TAS") or rid.startswith("TA"):
            tipo = "TA"
        elif rid.startswith("INC"):
            tipo = "INC"
        else:
            tipo = "OTRO"

        detalles.append(
            {
                "id": rid,
                "tipo": tipo,
                "tipo_relacion": clean(raw.get("tipo_relacion")),
                "titulo": clean(raw.get("titulo")),
                "estado": clean(raw.get("estado")),
                "usuario_asignado": clean(raw.get("usuario_asignado")),
                "grupo_asignado": clean(raw.get("grupo_asignado")),
                "crear_fecha": clean(raw.get("crear_fecha")),
                "tipo_ticket": clean(raw.get("tipo_ticket")),
            }
        )

    if not detalles:
        if payload.get("empty_confirmed"):
            print(
                f"[RELATED443] EXTRACT=EMPTY_CONFIRMED_V443 INC={esperado}",
                flush=True,
            )
            return (
                [],
                [],
                payload.get("total") if payload.get("total") is not None else 0,
                True,
                1,
                [],
            )

        print(
            f"[RELATED552] EXTRACT=PAYLOAD_EMPTY INC={esperado}",
            flush=True,
        )
        return (
            [],
            [],
            payload.get("total"),
            False,
            1,
            [],
        )

    wos = sorted({r["id"] for r in detalles if r["tipo"] == "WO"})

    tas = sorted({r["id"] for r in detalles if r["tipo"] == "TA"})

    print(
        f"[RELATED552] EXTRACT=OK INC={esperado} "
        f"TOTAL={payload.get('total')} "
        f"ROWS={len(detalles)} WO={wos!r} TA={tas!r}",
        flush=True,
    )

    try:
        (diag_dir / "related_table_v552.json").write_text(
            json.dumps(
                {
                    "inc": esperado,
                    "total": payload.get("total"),
                    "headers": payload.get("headers") or [],
                    "detalles": detalles,
                    "frame_url": payload.get("frame_url") or "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    return (
        wos,
        tas,
        payload.get("total"),
        True,
        1,
        detalles,
    )


# ATLAS_ALARMAS_NETCOOL_V557_BEGIN
async def _atlas_extract_incident_state(page, esperado):
    import asyncio
    import re

    esperado = str(esperado or "").strip().upper()

    js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();

      const visible=el => {
        if(!el) return false;
        const st=getComputedStyle(el);
        if(st.display==='none' || st.visibility==='hidden') return false;
        const r=el.getBoundingClientRect();
        return r.width>0 && r.height>0;
      };

      const txt=norm(document.body ? document.body.innerText : '');

      const patterns=[
        /\bEstado\s+(.+?)\s+(?:Incidencia grave|Nota de resoluci[oó]n|Cambio de estado|SERVICIOS AFECTADOS)\b/i,
        /\bStatus\s+(.+?)\s+(?:Major incident|Resolution note|Status reason|Affected services)\b/i
      ];

      for(const re of patterns){
        const m=txt.match(re);
        if(m && m[1]){
          return norm(m[1]).slice(0,80);
        }
      }

      const labels=[...document.querySelectorAll('[id$="_label"],[testid$="_label"],label,div,span')]
        .filter(visible)
        .filter(el => /^(Estado|Status)$/i.test(norm(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label') || '')))
        .slice(0,20);

      for(const label of labels){
        let box=label.parentElement;
        for(let level=0; box && level<5; level++, box=box.parentElement){
          const text=norm(box.innerText || box.textContent || '');
          if(!text || text.length>220) continue;

          const m=text.match(/^(?:Estado|Status)\s+(.+)$/i);
          if(m && m[1]){
            const value=norm(m[1]).replace(/^(Estado|Status)\s+/i,'');
            if(value && !/^(Estado|Status)$/i.test(value)) return value.slice(0,80);
          }
        }

        const sibling=label.nextElementSibling;
        if(sibling && visible(sibling)){
          const value=norm(sibling.innerText || sibling.textContent || '');
          if(value) return value.slice(0,80);
        }
      }

      return '';
    }
    """

    for frame in page.frames:
        url = frame.url or ""

        if "targetForm=Incident" not in url and "incidentPV" not in url:
            continue

        try:
            value = await asyncio.wait_for(frame.evaluate(js), timeout=2.5)
        except Exception:
            continue

        value = str(value or "").strip()

        if value:
            print(
                f"[INCSTATE557] INC={esperado} ESTADO={value!r}",
                flush=True,
            )
            return value

    print(f"[INCSTATE557] INC={esperado} ESTADO_NO_DETECTADO", flush=True)
    return ""


async def _atlas_capture_alarmas(page, esperado, diag_dir=None):
    import asyncio
    import json
    import re
    import time

    esperado = str(esperado or "").strip().upper()

    def _clean_py(value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    result = {
        "consultado": False,
        "total": None,
        "activas": None,
        "canceladas": None,
        "capturadas": 0,
        "captura_completa": False,
        "detalle": [],
        "activas_detalle": [],
        "source": "",
        "error": "",
    }

    button_selectors = [
        'button[testid="ar536871144"]',
        'button[name="ar536871144"]',
        'button:has-text("Alarmas")',
        '[role="button"]:has-text("Alarmas")',
    ]

    async def find_alarm_button():
        for candidate_page in list(page.context.pages):
            if candidate_page.is_closed():
                continue

            for frame in candidate_page.frames:
                frame_url = frame.url or ""
                page_url = candidate_page.url or ""

                if (
                    "targetForm=Incident" not in frame_url
                    and "incidentPV" not in page_url
                    and "SHR:SV_TicketDisplay" not in frame_url
                ):
                    continue

                for sel in button_selectors:
                    try:
                        loc = frame.locator(sel)
                        total = min(await loc.count(), 20)

                        for i in range(total):
                            el = loc.nth(i)

                            try:
                                if not await el.is_visible():
                                    continue
                            except Exception:
                                continue

                            try:
                                txt = _clean_py(await el.inner_text(timeout=1200))
                            except Exception:
                                txt = ""

                            if sel in (
                                'button[testid="ar536871144"]',
                                'button[name="ar536871144"]',
                            ) or re.search(r"\bAlarmas\b", txt, re.I):
                                return candidate_page, frame, el, sel, txt
                    except Exception:
                        continue

        return None

    found = None
    deadline = time.perf_counter() + 25.0

    while time.perf_counter() < deadline:
        found = await find_alarm_button()

        if found:
            break

        await page.wait_for_timeout(300)

    if not found:
        result["error"] = "BOTON_ALARMAS_NO_ENCONTRADO"
        print(f"[ALARMAS557] INC={esperado} BOTON=NO", flush=True)
        return result

    before_pages = list(page.context.pages)
    source_page, frame, button, selector, text = found

    print(
        f"[ALARMAS557] INC={esperado} BOTON=SI SEL={selector!r} TEXT={text!r}",
        flush=True,
    )

    try:
        await button.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        await button.click(timeout=15000)
    except Exception:
        try:
            await button.click(force=True, timeout=15000)
        except Exception as exc:
            result["error"] = f"CLICK_ALARMAS_FAIL:{type(exc).__name__}:{exc}"
            print(f"[ALARMAS557] INC={esperado} CLICK=FAIL {exc}", flush=True)
            return result

    print(f"[ALARMAS557] INC={esperado} CLICK=OK", flush=True)

    alarm_page = None
    deadline = time.perf_counter() + 35.0

    while time.perf_counter() < deadline and alarm_page is None:
        for candidate_page in list(page.context.pages):
            if candidate_page.is_closed():
                continue

            try:
                purl = candidate_page.url or ""
                ptitle = await candidate_page.title()
            except Exception:
                purl = ""
                ptitle = ""

            if (
                "INT:NetCool_Alarmas" in purl
                or "NetCool_Alarmas" in ptitle
                or "Alarmas(GET)" in ptitle
            ):
                alarm_page = candidate_page
                break

            for f in candidate_page.frames:
                furl = f.url or ""

                if "INT:NetCool_Alarmas" in furl:
                    alarm_page = candidate_page
                    break

            if alarm_page is not None:
                break

        if alarm_page is None:
            await page.wait_for_timeout(500)

    if alarm_page is None:
        result["error"] = "PAGINA_ALARMAS_NO_ABRIO"
        print(f"[ALARMAS557] INC={esperado} PAGINA=NO", flush=True)
        return result

    print(
        f"[ALARMAS557] INC={esperado} PAGINA=SI URL={(alarm_page.url or '')[:220]!r}",
        flush=True,
    )

    capture_js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();

      const visible=el => {
        if(!el) return false;
        const st=getComputedStyle(el);
        if(st.display==='none' || st.visibility==='hidden') return false;
        const r=el.getBoundingClientRect();
        return r.width>0 && r.height>0;
      };

      const bodyText=norm(document.body ? document.body.innerText : '');
      const totalMatch =
        bodyText.match(/(\d+)\s+filas?\s+en\s+total/i) ||
        bodyText.match(/Registros\s+desde\s+\d+\s+hasta\s+\d+\s+de\s+(\d+)/i) ||
        bodyText.match(/records?\s+from\s+\d+\s+to\s+\d+\s+of\s+(\d+)/i);

      const table =
        document.querySelector('adapt-table#ar536870937') ||
        document.querySelector('#ar536870937') ||
        [...document.querySelectorAll('adapt-table,table')].find(t => {
          const text=norm(t.innerText || t.textContent || '');
          return text.includes('NOMB_ALARMA')
            && text.includes('CONSEC_NBR')
            && text.includes('CANCELADOPOR');
        });

      if(!table){
        return {
          found:false,
          total:totalMatch ? Number(totalMatch[1]) : null,
          rows:[]
        };
      }

      const headers=[...table.querySelectorAll('thead th')]
        .map(x => norm(x.innerText || x.textContent || ''));

      const keys=[
        'dn',
        'node',
        'creada',
        'insertada',
        'estado',
        'nomb_alarma',
        'num_alarma',
        'fecha_hora_clareo',
        'tecnologia',
        'cancelado_por',
        'relacionado_por',
        'sector_eb',
        'serverserial',
        'consec_nbr'
      ];

      const rows=[];

      for(const tr of table.querySelectorAll('tbody tr')){
        if(!visible(tr)) continue;

        const tds=[...tr.querySelectorAll('td')];

        if(tds.length<5) continue;

        const values=tds.map(td => norm(td.innerText || td.textContent || ''));

        if(!values.some(Boolean)) continue;

        const row={};

        for(let i=0;i<keys.length;i++){
          row[keys[i]]=values[i] || '';
        }

        const colorNode=tds.find(td => {
          const value=norm(td.innerText || td.textContent || '');
          return value;
        }) || tds[0];

        row.color=colorNode ? getComputedStyle(colorNode).color : '';

        const estado=String(row.estado || '').trim();
        const canceladoPor=String(row.cancelado_por || '').trim();
        const color=String(row.color || '').toLowerCase();

        if(estado === '1' || /255,\s*0,\s*0/.test(color)){
          row.activa=true;
        }else if(estado === '0' || canceladoPor || /0,\s*0,\s*255/.test(color)){
          row.activa=false;
        }else{
          row.activa=!canceladoPor;
        }

        row.raw=values;
        rows.push(row);
      }

      return {
        found:true,
        total:totalMatch ? Number(totalMatch[1]) : null,
        headers,
        rows
      };
    }
    """

    scroll_js = r"""
    () => {
      const candidates=[];
      const add=el => {
        if(!el || candidates.includes(el)) return;
        candidates.push(el);
      };

      add(document.scrollingElement);
      add(document.documentElement);
      add(document.body);

      for(const el of document.querySelectorAll(
        '.ui-table-container,.ui-table-wrapper,.at-table-scroll-wrapper,' +
        '[class*="scroll"],[class*="Scroll"],[style*="overflow"]'
      )){
        add(el);
      }

      let moved=false;

      for(const el of candidates){
        try{
          const max=el.scrollHeight - el.clientHeight;
          if(max <= 5) continue;

          const before=el.scrollTop || 0;
          const step=Math.max(500, Math.floor((el.clientHeight || 800)*0.85));
          el.scrollTop=Math.min(max, before + step);

          if((el.scrollTop || 0) !== before){
            moved=true;
          }
        }catch(e){}
      }

      window.scrollBy(0,900);

      return {moved};
    }
    """

    next_js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();

      const visible=el => {
        if(!el) return false;
        const st=getComputedStyle(el);
        if(st.display==='none' || st.visibility==='hidden') return false;
        const r=el.getBoundingClientRect();
        return r.width>0 && r.height>0;
      };

      const btns=[...document.querySelectorAll('button,a,[role="button"]')]
        .filter(visible)
        .filter(el => {
          const text=norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
          return /^(Siguiente|Next)$/i.test(text) || /\b(Siguiente|Next)\b/i.test(text);
        })
        .filter(el => !el.disabled && el.getAttribute('aria-disabled') !== 'true');

      const btn=btns[0];

      if(!btn) return false;

      btn.click();

      return true;
    }
    """

    merged = {}
    total_rows = None
    table_seen = False
    stagnant = 0
    last_count = 0

    for vuelta in range(90):
        page_frames = list(alarm_page.frames)

        for f in page_frames:
            try:
                data = await asyncio.wait_for(f.evaluate(capture_js), timeout=3.0)
            except Exception:
                continue

            if not data:
                continue

            if data.get("found"):
                table_seen = True

            if data.get("total") is not None:
                try:
                    total_rows = max(
                        int(total_rows or 0),
                        int(data.get("total") or 0),
                    )
                except Exception:
                    pass

            for row in data.get("rows") or []:
                key = (
                    str(row.get("consec_nbr") or "").strip()
                    or str(row.get("serverserial") or "").strip()
                    or "|".join(str(x).strip() for x in row.get("raw") or [])
                )

                if not key:
                    continue

                merged[key] = row

        current_count = len(merged)

        if total_rows and current_count >= total_rows:
            break

        if current_count == last_count:
            stagnant += 1
        else:
            stagnant = 0
            last_count = current_count

        moved_any = False

        for f in page_frames:
            try:
                moved = await asyncio.wait_for(f.evaluate(scroll_js), timeout=1.5)
                if moved and moved.get("moved"):
                    moved_any = True
            except Exception:
                continue

        try:
            await alarm_page.mouse.wheel(0, 900)
        except Exception:
            pass

        if stagnant >= 5:
            clicked_next = False

            for f in page_frames:
                try:
                    clicked_next = bool(
                        await asyncio.wait_for(
                            f.evaluate(next_js),
                            timeout=1.5,
                        )
                    )
                except Exception:
                    clicked_next = False

                if clicked_next:
                    print(
                        f"[ALARMAS557] INC={esperado} NEXT_CLICK=OK",
                        flush=True,
                    )
                    stagnant = 0
                    await alarm_page.wait_for_timeout(1200)
                    break

            if not clicked_next and not moved_any:
                break

        await alarm_page.wait_for_timeout(220)

    rows = list(merged.values())
    active = [r for r in rows if bool(r.get("activa"))]
    cancelled = [r for r in rows if not bool(r.get("activa"))]

    complete = total_rows is None or total_rows <= 0 or len(rows) >= int(total_rows)

    result = {
        "consultado": bool(table_seen),
        "total": int(total_rows) if total_rows else len(rows),
        "activas": len(active),
        "canceladas": len(cancelled),
        "capturadas": len(rows),
        "captura_completa": bool(complete),
        "detalle": rows,
        "activas_detalle": active,
        "source": "INT_NETCOOL_ALARMAS_V557",
        "error": "" if table_seen else "TABLA_ALARMAS_NO_ENCONTRADA",
    }

    if table_seen and not complete:
        result["error"] = (
            f"CAPTURA_INCOMPLETA:"
            f"total={result['total']}:capturadas={result['capturadas']}"
        )

    print(
        f"[ALARMAS557] INC={esperado} "
        f"TOTAL={result['total']} "
        f"CAPTURADAS={result['capturadas']} "
        f"ACTIVAS={result['activas']} "
        f"CANCELADAS={result['canceladas']} "
        f"COMPLETA={result['captura_completa']} "
        f"ERROR={result['error']!r}",
        flush=True,
    )

    try:
        if diag_dir is not None:
            target = diag_dir / "alarmas_netcool_v557.json"
            target.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception:
        pass

    try:
        if (
            alarm_page is not page
            and alarm_page not in before_pages
            and not alarm_page.is_closed()
        ):
            await alarm_page.close()
    except Exception:
        pass

    return result


# ATLAS_ALARMAS_NETCOOL_V557_END


# ATLAS_HELIX_V443_EMPTY_RELATED_FECHA_BEGIN
async def _atlas_extract_incident_created_at_v443(page, esperado):
    import asyncio

    esperado = str(esperado or "").strip().upper()

    js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();

      const visible=el => {
        if(!el) return false;
        const st=getComputedStyle(el);
        if(st.display==='none' || st.visibility==='hidden') return false;
        const r=el.getBoundingClientRect();
        return r.width>0 && r.height>0;
      };

      const dateRe='([0-3]?\\d\\/[01]?\\d\\/20\\d{2}\\s+[0-2]?\\d:[0-5]\\d(?::[0-5]\\d)?(?:\\s*[AP]\\.?M\\.?)?|20\\d{2}-[01]\\d-[0-3]\\d(?:[ T][0-2]\\d:[0-5]\\d(?::[0-5]\\d)?)?)';

      const labelRe=new RegExp(
        '(?:Fecha\\\\s+(?:de\\\\s+)?creaci[oó]n|Fecha\\\\s+apertura|Fecha\\\\s+notificaci[oó]n|Fecha\\\\s+registro|Creation\\\\s+Date|Created\\\\s+Date|Submit\\\\s+Date|Reported\\\\s+Date|Opened\\\\s+Date)\\\\s*[:\\\\-]?\\\\s*' + dateRe,
        'i'
      );

      const bodyText=norm(document.body ? document.body.innerText : '');
      const direct=bodyText.match(labelRe);
      if(direct && direct[1]) return norm(direct[1]).slice(0,80);

      const labels=[...document.querySelectorAll('[id$="_label"],[testid$="_label"],label,div,span')]
        .filter(visible)
        .filter(el => {
          const t=norm(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label') || '');
          return /^(Fecha\s+(de\s+)?creaci[oó]n|Fecha\s+apertura|Fecha\s+notificaci[oó]n|Fecha\s+registro|Creation\s+Date|Created\s+Date|Submit\s+Date|Reported\s+Date|Opened\s+Date)$/i.test(t);
        })
        .slice(0,80);

      for(const label of labels){
        let box=label.parentElement;

        for(let level=0; box && level<8; level++, box=box.parentElement){
          const text=norm(box.innerText || box.textContent || '');

          if(!text || text.length>500) continue;

          const m=text.match(new RegExp(dateRe,'i'));
          if(m && m[1]) return norm(m[1]).slice(0,80);
        }

        const sibs=[
          label.nextElementSibling,
          label.parentElement && label.parentElement.nextElementSibling
        ];

        for(const s of sibs){
          if(!s || !visible(s)) continue;
          const t=norm(s.innerText || s.textContent || '');
          const m=t.match(new RegExp(dateRe,'i'));
          if(m && m[1]) return norm(m[1]).slice(0,80);
        }
      }

      return '';
    }
    """

    for frame in page.frames:
        url = frame.url or ""

        if (
            "targetForm=Incident" not in url
            and "incidentPV" not in url
            and "SHR:SV_TicketDisplay" not in url
        ):
            continue

        try:
            value = await asyncio.wait_for(frame.evaluate(js), timeout=2.5)
        except Exception:
            continue

        value = str(value or "").strip()

        if value:
            print(
                f"[INCDATE443] INC={esperado} FECHA_CREACION={value!r}",
                flush=True,
            )
            return value

    print(f"[INCDATE443] INC={esperado} FECHA_CREACION_NO_DETECTADA", flush=True)
    return ""


async def _atlas_prepare_empty_related_if_confirmed_v443(page, esperado):
    import asyncio

    esperado = str(esperado or "").strip().upper()

    js = r"""
    () => {
      const norm=s => (s || '').replace(/\s+/g,' ').trim();
      const txt=norm(document.body ? document.body.innerText : '');

      const relatedZero=/\b(Related\s+items|Elementos\s+relacionados)\s*0\b/i.test(txt);
      const relatedVisible=/\b(Related\s+items|Elementos\s+relacionados)\b/i.test(txt);
      const tasksZero=/\b(Tasks|Tareas)\s*0\b/i.test(txt);
      const tasksVisible=/\b(Tasks|Tareas)\b/i.test(txt);
      const hasRelatedId=/\b(?:WO\d{7,}|TAS?0*\d{5,})\b/i.test(txt);

      return {
        relatedZero,
        relatedVisible,
        tasksZero,
        tasksVisible,
        hasRelatedId,
        text:txt.slice(0,2000)
      };
    }
    """

    best = None

    for frame in page.frames:
        try:
            url = frame.url or ""

            if (
                "targetForm=Incident" not in url
                and "SHR:SV_TicketDisplay" not in url
                and "incidentPV" not in url
            ):
                continue

            info = await asyncio.wait_for(frame.evaluate(js), timeout=2.0)

            if not info:
                continue

            best = info

            confirmed = (
                bool(info.get("tasksZero"))
                and not bool(info.get("hasRelatedId"))
                and (
                    bool(info.get("relatedZero"))
                    or bool(info.get("relatedVisible"))
                    or bool(info.get("tasksVisible"))
                )
            )

            if confirmed:
                page._atlas_related_payload = {
                    "inc": esperado,
                    "total": 0,
                    "headers": [],
                    "rows": [],
                    "frame_url": frame.url or "",
                    "empty_confirmed": True,
                    "empty_reason": "RELATED_ITEMS_0_TASKS_0",
                }

                print(
                    f"[RELATED443] INC={esperado} EMPTY_CONFIRMED "
                    f"relatedZero={info.get('relatedZero')} "
                    f"tasksZero={info.get('tasksZero')} "
                    f"hasRelatedId={info.get('hasRelatedId')}",
                    flush=True,
                )

                return frame
        except Exception:
            continue

    print(
        f"[RELATED443] INC={esperado} EMPTY_NOT_CONFIRMED INFO={best!r}",
        flush=True,
    )
    return None


# ATLAS_HELIX_V443_EMPTY_RELATED_FECHA_END

# ATLAS_HELIX_V46_ENRICH_RELATED_STATES_BEGIN


async def _atlas_enrich_related_states_v45(
    page,
    esperado,
    detalles,
    wo_ids=None,
    ta_ids=None,
):
    import asyncio
    import re

    esperado = str(esperado or "").strip().upper()

    states = [
        "Work In Progress",
        "Trabajo en curso",
        "In Progress",
        "En curso",
        "Assigned",
        "Asignado",
        "Pending",
        "Pendiente",
        "Completed",
        "Completado",
        "Closed",
        "Cerrado",
        "Resolved",
        "Resuelto",
        "Cancelled",
        "Canceled",
        "Cancelado",
        "Rejected",
        "Rechazado",
        "Acknowledged",
        "Reconocido",
        "Scheduled",
        "Programado",
        "Waiting",
        "En espera",
        "Open",
        "Abierto",
        "New",
        "Nuevo",
    ]

    # =========================================================
    # HELPERS PYTHON
    # =========================================================

    def arr(value):
        if value is None:
            return []

        if isinstance(
            value,
            (list, tuple, set),
        ):
            return list(value)

        return [value]

    def clean(value):
        return re.sub(
            r"\s+",
            " ",
            str(value or ""),
        ).strip()

    def upper(value):
        return clean(value).upper()

    def find_state(text):
        txt = clean(text)

        if not txt:
            return ""

        # Primero coincidencia exacta.
        for st in sorted(
            states,
            key=len,
            reverse=True,
        ):
            if txt.casefold() == st.casefold():
                return txt

        # Después dentro de un texto.
        for st in sorted(
            states,
            key=len,
            reverse=True,
        ):
            if re.search(
                r"\b" + re.escape(st) + r"\b",
                txt,
                re.I,
            ):
                return st

        return ""

    def get_id(item):
        if isinstance(item, dict):

            for key in (
                "id",
                "ID",
                "ticket",
                "numero",
                "number",
            ):
                value = clean(item.get(key))

                if value:
                    return value

            blob = " ".join(clean(v) for v in item.values())

        else:
            blob = clean(item)

        match = re.search(
            r"\b(?:WO\d{7,}|TAS?0*\d{5,})\b",
            blob,
            re.I,
        )

        return match.group(0) if match else ""

    def current_state(item):
        if not isinstance(item, dict):
            return ""

        # Primero campos explícitos.
        for key in (
            "estado",
            "Estado",
            "status",
            "Status",
            "estado_ticket",
            "state",
            "State",
        ):
            state = find_state(item.get(key))

            if state:
                return state

        # Fallback.
        for key in (
            "raw",
            "cells",
            "row",
            "values",
            "text",
        ):
            value = item.get(key)

            if isinstance(
                value,
                (list, tuple),
            ):
                for cell in value:
                    state = find_state(cell)

                    if state:
                        return state

            else:
                state = find_state(value)

                if state:
                    return state

        return ""

    # =========================================================
    # NORMALIZAR DETALLES
    # =========================================================

    fixed = []
    seen = set()

    for raw in arr(detalles):

        if isinstance(raw, dict):
            item = dict(raw)

        else:
            sid = get_id(raw)

            if not sid:
                continue

            item = {
                "id": sid,
                "raw": [clean(raw)],
                "estado": "",
            }

        sid = get_id(item)
        sid_upper = upper(sid)

        if not sid_upper:
            continue

        if sid_upper in seen:
            continue

        fixed.append(item)
        seen.add(sid_upper)

    # Asegurar que estén todos los WO/TAS.
    for sid in arr(wo_ids) + arr(ta_ids):
        sid = clean(sid)
        sid_upper = upper(sid)

        if not sid_upper:
            continue

        if sid_upper in seen:
            continue

        fixed.append(
            {
                "id": sid,
                "tipo": ("TA" if sid_upper.startswith("TA") else "WO"),
                "estado": "",
                "raw": [],
            }
        )

        seen.add(sid_upper)

    ids = [get_id(item) for item in fixed if get_id(item)]

    if not ids:
        return fixed

    tas_ids = [sid for sid in ids if upper(sid).startswith("TA")]

    # =========================================================
    # ESTADOS CON PRIORIDAD
    # =========================================================

    states_by_id = {}

    # Mayor número = fuente más confiable.
    source_priority = {
        "detalle": 10,
        "payload": 20,
        "dom_relacionados": 30,
        # La pestaña Tasks es autoridad para TAS.
        "dom_tasks": 100,
    }

    def feed(
        sid,
        state,
        source,
    ):
        sid = clean(sid)
        state = find_state(state)

        if not sid or not state:
            return

        key = upper(sid)

        new_priority = source_priority.get(
            source,
            0,
        )

        previous = states_by_id.get(key)

        if previous is not None:
            previous_priority = previous[2]

            if new_priority < previous_priority:
                return

        states_by_id[key] = (
            state,
            source,
            new_priority,
        )

    # =========================================================
    # 1. ESTADO EXISTENTE DEL DETALLE
    # =========================================================

    for item in fixed:
        sid = get_id(item)
        state = current_state(item)

        if sid and state:
            feed(
                sid,
                state,
                "detalle",
            )

    # =========================================================
    # 2. PAYLOAD DE RELACIONADOS
    # =========================================================

    payload = getattr(
        page,
        "_atlas_related_payload",
        None,
    )

    if isinstance(payload, dict):

        rows = arr(payload.get("rows") or payload.get("detalles") or [])

        for row in rows:

            if isinstance(row, dict):

                sid = get_id(row)

                if not sid:
                    continue

                # IMPORTANTE:
                # leer primero el campo estado de ESA fila.
                state = ""

                for key in (
                    "estado",
                    "Estado",
                    "status",
                    "Status",
                    "state",
                    "State",
                ):
                    state = find_state(row.get(key))

                    if state:
                        break

                if not state:

                    for value in row.values():

                        for candidate in arr(value):

                            state = find_state(candidate)

                            if state:
                                break

                        if state:
                            break

                if state:
                    feed(
                        sid,
                        state,
                        "payload",
                    )

    # =========================================================
    # JS:
    # BUSCAR UNA FILA REAL QUE CONTENGA EL ID EXACTO
    # =========================================================

    js = r"""
    (ids) => {

      const norm = s =>
        (s || '')
          .replace(/\s+/g, ' ')
          .trim();

      const up = s =>
        norm(s).toUpperCase();

      const states = [
        'Work In Progress',
        'Trabajo en curso',
        'In Progress',
        'En curso',
        'Assigned',
        'Asignado',
        'Pending',
        'Pendiente',
        'Completed',
        'Completado',
        'Closed',
        'Cerrado',
        'Resolved',
        'Resuelto',
        'Cancelled',
        'Canceled',
        'Cancelado',
        'Rejected',
        'Rechazado',
        'Acknowledged',
        'Reconocido',
        'Scheduled',
        'Programado',
        'Waiting',
        'En espera',
        'Open',
        'Abierto',
        'New',
        'Nuevo'
      ];

      const esc = s =>
        s.replace(
          /[.*+?^${}()|[\]\\]/g,
          '\\$&'
        );

      const visible = el => {
        if (!el) return false;

        const style =
          getComputedStyle(el);

        if (
          style.display === 'none'
          ||
          style.visibility === 'hidden'
        ) {
          return false;
        }

        const rect =
          el.getBoundingClientRect();

        return (
          rect.width > 0
          &&
          rect.height > 0
        );
      };


      const findState = text => {

        const value = norm(text);

        if (!value) {
          return '';
        }

        // Exacto primero.
        for (
          const state of [...states]
            .sort(
              (a,b) => b.length-a.length
            )
        ) {

          if (
            value.toLowerCase()
            ===
            state.toLowerCase()
          ) {
            return value;
          }
        }

        for (
          const state of [...states]
            .sort(
              (a,b) => b.length-a.length
            )
        ) {

          if (
            new RegExp(
              '\\b'
              + esc(state)
              + '\\b',
              'i'
            ).test(value)
          ) {
            return state;
          }
        }

        return '';
      };


         const getCells = row => {

        let cells = [
          ...row.querySelectorAll(
            'td,'
            + '[role="cell"],'
            + '[role="gridcell"],'
            + '.adapt-table-cell,'
            + '[data-testid*="cell"]'
          )
        ]
          .filter(visible)
          .map(el =>
            norm(
              el.innerText
              ||
              el.textContent
              ||
              el.getAttribute('title')
              ||
              el.getAttribute('aria-label')
              ||
              ''
            )
          )
          .filter(Boolean);

        if (!cells.length) {

          cells = (
            row.innerText
            ||
            row.textContent
            ||
            ''
          )
            .split(/\n+/)
            .map(norm)
            .filter(Boolean);
        }

        return cells;

      };


      // ==============================================
      // NUEVO: LEER COLUMNA EXACTA ESTADO / STATUS
      // ==============================================

      const getStatusFromExactColumn = row => {

        const table = row.closest(
          'table, adapt-table, [role="grid"]'
        );

        if (!table) {
          return '';
        }

        const headers = [
          ...table.querySelectorAll(
            'thead th, [role="columnheader"]'
          )
        ]
          .map(el =>
            norm(
              el.innerText
              ||
              el.textContent
              ||
              el.getAttribute('aria-label')
              ||
              ''
            )
          );


        let statusIndex = -1;

        for (
          let i = 0;
          i < headers.length;
          i++
        ) {

          const header =
            headers[i]
              .toLowerCase()
              .trim();

          if (
            header === 'estado'
            ||
            header === 'status'
            ||
            header === 'estado de tarea'
            ||
            header === 'task status'
          ) {
            statusIndex = i;
            break;
          }
        }


        if (statusIndex < 0) {
          return '';
        }


        const cells = [
          ...row.querySelectorAll(
            'td,'
            + '[role="cell"],'
            + '[role="gridcell"],'
            + '.adapt-table-cell'
          )
        ]
          .filter(visible)
          .map(el =>
            norm(
              el.innerText
              ||
              el.textContent
              ||
              el.getAttribute('title')
              ||
              el.getAttribute('aria-label')
              ||
              ''
            )
          );


        if (
          statusIndex >= cells.length
        ) {
          return '';
        }


        return findState(
          cells[statusIndex]
        );
      };



      const rows = [
        ...document.querySelectorAll(
          'tbody tr,'
          + 'tr[role="row"],'
          + '[role="row"],'
          + '[data-testid*="row"]'
        )
      ].filter(visible);


      const out = {};


      for (const rawId of ids) {

        const id = up(rawId);

        if (!id) continue;

        /*
         * Solo aceptamos una fila cuyo contenido
         * tenga el ID solicitado.
         *
         * Ya no buscamos DIV gigantes ni contenedores
         * que puedan contener estados de otras TAS.
         */
        const matchingRows =
          rows.filter(row => {

            const text = up(
              row.innerText
              ||
              row.textContent
              ||
              ''
            );

            return text.includes(id);
          });


        for (const row of matchingRows) {

  const cells =
    getCells(row);

  /*
   * PRIMERA OPCIÓN:
   * leer exclusivamente la columna
   * Estado / Status de esta TAS.
   */
  let state =
    getStatusFromExactColumn(row);


  /*
   * FALLBACK:
   * solamente si Helix no expone
   * correctamente los encabezados.
   */
  if (!state) {

    const candidates = [];

    for (const cell of cells) {

      if (
        up(cell).includes(id)
      ) {
        continue;
      }

      const candidate =
        findState(cell);

      if (candidate) {
        candidates.push(candidate);
      }
    }


    /*
     * Si aparecen Pending y New en la misma fila,
     * Pending representa el estado operativo de
     * la tarea y debe ganar sobre New.
     */
    const priority = [
      'Pending',
      'Pendiente',
      'Closed',
      'Cerrado',
      'Completed',
      'Completado',
      'Resolved',
      'Resuelto',
      'Work In Progress',
      'Trabajo en curso',
      'In Progress',
      'En curso',
      'Assigned',
      'Asignado',
      'Cancelled',
      'Canceled',
      'Cancelado',
      'Open',
      'Abierto',
      'New',
      'Nuevo'
    ];


    for (const wanted of priority) {

      const found =
        candidates.find(
          candidate =>
            candidate
              .toLowerCase()
              ===
            wanted.toLowerCase()
        );

      if (found) {
        state = found;
        break;
      }
    }
  }


  if (!state) {
    continue;
  }
  
          out[rawId] = {
            estado: state,
            cells: cells,
            rowText: norm(
              row.innerText
              ||
              row.textContent
              ||
              ''
            ).slice(0, 900)
          };

          break;
        }
      }


      return out;
    }
    """

    # =========================================================
    # ESCANEAR DOM
    # =========================================================

    async def scan_dom(
        ids_to_scan,
        source,
    ):
        out = {}

        if not ids_to_scan:
            return out

        for frame in page.frames:

            try:
                url = frame.url or ""

                if (
                    "targetForm=Incident" not in url
                    and "SHR:SV_TicketDisplay" not in url
                    and "incidentPV" not in url
                ):
                    continue

                data = await asyncio.wait_for(
                    frame.evaluate(
                        js,
                        ids_to_scan,
                    ),
                    timeout=3.5,
                )

                if not isinstance(
                    data,
                    dict,
                ):
                    continue

                for sid, info in data.items():

                    if not isinstance(
                        info,
                        dict,
                    ):
                        continue

                    state = info.get("estado")

                    if not state:
                        continue

                    out[upper(sid)] = (
                        state,
                        source,
                    )

                    print(
                        f"[STATE46] "
                        f"INC={esperado} "
                        f"ID={sid} "
                        f"ESTADO={state!r} "
                        f"SOURCE={source} "
                        f"CELLS={info.get('cells')!r}",
                        flush=True,
                    )

            except Exception:
                continue

        return out

    # =========================================================
    # 3. RELACIONADOS
    # =========================================================

    relacionados_scan = await scan_dom(
        ids,
        "dom_relacionados",
    )

    for key, (
        state,
        source,
    ) in relacionados_scan.items():

        feed(
            key,
            state,
            source,
        )

    # =========================================================
    # 4. PARA TODAS LAS TAS:
    #    ABRIR TASKS SIEMPRE
    #
    # IMPORTANTE:
    # aunque ya tengan In Progress, Assigned, etc.
    # =========================================================

    if tas_ids:

        clicked = False

        for frame in page.frames:

            try:
                url = frame.url or ""

                if (
                    "targetForm=Incident" not in url
                    and "SHR:SV_TicketDisplay" not in url
                    and "incidentPV" not in url
                ):
                    continue

                selectors = [
                    "button[data-testid='adapt-tabs-dropdown-1_tab_0']",
                    "button[role='tab']:has-text('Tasks')",
                    "button[role='tab']:has-text('Tareas')",
                    "[role='tab']:has-text('Tasks')",
                    "[role='tab']:has-text('Tareas')",
                ]

                for selector in selectors:

                    loc = frame.locator(selector)

                    total = await loc.count()

                    for i in range(total):

                        element = loc.nth(i)

                        try:
                            if not await element.is_visible():
                                continue

                        except Exception:
                            continue

                        try:
                            await element.click(timeout=5000)

                        except Exception:

                            try:
                                await element.click(
                                    force=True,
                                    timeout=5000,
                                )

                            except Exception:
                                continue

                        print(
                            f"[STATE46] "
                            f"INC={esperado} "
                            f"TASKS_CLICK=OK "
                            f"SEL={selector!r}",
                            flush=True,
                        )

                        clicked = True

                        await page.wait_for_timeout(2500)

                        break

                    if clicked:
                        break

                if clicked:
                    break

            except Exception:
                continue

        # =====================================================
        # ESCANEAR TODAS LAS TAS EN LA PESTAÑA TASKS.
        #
        # Este estado tiene prioridad 100 y reemplaza
        # In Progress antiguo encontrado antes.
        # =====================================================

        if clicked:

            tasks_scan = await scan_dom(
                tas_ids,
                "dom_tasks",
            )

            for key, (
                state,
                source,
            ) in tasks_scan.items():

                feed(
                    key,
                    state,
                    source,
                )

    # =========================================================
    # 5. APLICAR ESTADO FINAL
    #
    # IMPORTANTE:
    # ya NO hacemos:
    #
    #   if current_state(item):
    #       continue
    #
    # porque eso impedía reemplazar un estado viejo.
    # =========================================================

    updated = 0

    for item in fixed:

        sid = get_id(item)

        if not sid:
            continue

        found = states_by_id.get(upper(sid))

        if not found:

            print(
                f"[STATE46] " f"INC={esperado} " f"ID={sid} " f"ESTADO_NO_DETECTADO",
                flush=True,
            )

            continue

        state = found[0]
        source = found[1]

        estado_anterior = clean(item.get("estado"))

        # Reemplazar aunque antes tuviera otro estado.
        item["estado"] = state

        item["estado_source"] = source

        if estado_anterior.casefold() != state.casefold():
            updated += 1

        print(
            f"[STATE46] "
            f"INC={esperado} "
            f"ID={sid} "
            f"ANTES={estado_anterior!r} "
            f"FINAL={state!r} "
            f"SOURCE={source}",
            flush=True,
        )

    print(
        f"[STATE46] " f"INC={esperado} " f"UPDATED={updated} " f"TOTAL={len(fixed)}",
        flush=True,
    )

    return fixed


# ATLAS_HELIX_V46_ENRICH_RELATED_STATES_END


async def procesar_inc(
    page,
    inc: str,
    worker: int,
    search_lock: asyncio.Lock,
    *,
    ya_abierto: bool = False,
):
    # ATLAS_V443_FECHA_INIT
    fecha_creacion_incidente = ""
    inicio = time.perf_counter()

    diag_dir = (
        DIAG_ROOT / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"_W{worker:02d}_{inc}"
    )
    diag_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # SmartIT/Helix Global Search es el punto sensible.
    # Solo la busqueda/apertura del INC se ejecuta de uno en uno.
    # Titulo y Elementos relacionados vuelven a ejecutarse en paralelo.
    if not ya_abierto:
        async with search_lock:
            await buscar_incidente(page, inc)
            await page.wait_for_timeout(650)

    inc_ok, inc_validation, inc_detectado = await validar_inc_abierto(
        page,
        inc,
    )

    if not inc_ok:
        raise RuntimeError(
            f"{inc_validation}:" f"SOLICITADO={inc}:" f"DETECTADO={inc_detectado}"
        )

    titulo = await extraer_titulo(page)

    estado_incidente = await _atlas_extract_incident_state(
        page,
        inc,
    )

    fecha_creacion_incidente = await _atlas_extract_incident_created_at_v443(
        page,
        inc,
    )

    related_frame, tab_modo = await localizar_related_frame_y_click(
        page,
        inc,
    )

    (
        wos,
        tas,
        filas_helix,
        evid_related,
        paginas_vistas,
        relacionados_detalle,
    ) = await extraer_related(
        page,
        inc,
        related_frame,
        diag_dir,
    )

    relacionados_detalle = await _atlas_enrich_related_states_v45(
        page,
        inc,
        relacionados_detalle,
        wos,
        tas,
    )

    alarmas = await _atlas_capture_alarmas(
        page,
        inc,
        diag_dir,
    )

    if wos or tas:
        estado = "OK"
    elif evid_related:
        estado = "SIN_RELACIONADOS"
    else:
        estado = "RELACIONADOS_NO_CONFIRMADO"

    duracion = time.perf_counter() - inicio

    return {
        "inc": inc,
        "titulo": titulo,
        "estado": estado,
        "estado_incidente": estado_incidente,
        "fecha_creacion_incidente": fecha_creacion_incidente,
        "alarmas": alarmas,
        "alarmas_total": alarmas.get("total"),
        "alarmas_activas_total": alarmas.get("activas"),
        "alarmas_canceladas_total": alarmas.get("canceladas"),
        "alarmas_activas_detalle": alarmas.get("activas_detalle", []),
        "alarmas_detalle": alarmas.get("detalle", []),
        "total_wo": len(wos),
        "wo_relacionadas": wos,
        "total_ta": len(tas),
        "ta_relacionadas": tas,
        "relacionados_detalle": relacionados_detalle,
        "duracion_seg": round(duracion, 2),
        "worker": worker,
        "frame_url": (
            (related_frame.url or "")[:500]
            + f" | TAB_MODO={tab_modo}"
            + f" | PAGINAS={paginas_vistas}"
            + ("" if filas_helix is None else f" | TOTAL_REL={filas_helix}")
        ),
        "error": "",
    }


def _append_result(
    job_id: str,
    result: dict[str, Any],
):
    with _LOCK:
        job = _JOBS[job_id]
        job["resultados"].append(result)
        job["procesados"] = len(job["resultados"])
        _write_job(job_id)


def _control_job(
    job_id: str,
    action: str,
) -> dict[str, Any]:
    key = str(job_id or "").strip()

    if not key:
        return {
            "ok": False,
            "codigo": "HELIX_BATCH_JOB_ID_INVALIDO",
            "job_id": "",
            "respuesta": "Job invalido.",
        }

    with _LOCK:
        job = _JOBS.get(key)

        if job is None:
            return {
                "ok": False,
                "codigo": "HELIX_BATCH_NO_ACTIVO",
                "job_id": key,
                "respuesta": (
                    "El job no esta activo en memoria. "
                    "Si 8023 fue reiniciado, inicie una nueva carga."
                ),
            }

        estado = str(job.get("estado") or "").strip().upper()

        if estado in {"COMPLETADO", "ERROR", "DETENIDO", "INTERRUMPIDO"}:
            return {
                "ok": False,
                "codigo": "HELIX_BATCH_TERMINAL",
                "job_id": key,
                "estado": estado,
                "respuesta": "El job ya finalizo.",
            }

        if action == "pause":
            job["pause_requested"] = True
            job["estado"] = "PAUSADO"
            job["control_actualizado"] = _now()
            respuesta = "Pausa solicitada. Los workers se detienen antes del siguiente ticket."

        elif action == "resume":
            job["pause_requested"] = False
            if not bool(job.get("stop_requested", False)):
                job["estado"] = "PROCESANDO"
            job["control_actualizado"] = _now()
            respuesta = "Barrido reanudado."

        elif action == "stop":
            job["stop_requested"] = True
            job["pause_requested"] = False
            job["estado"] = "DETENIENDO"
            job["control_actualizado"] = _now()
            respuesta = "Detencion solicitada. Se conservaran los resultados ya obtenidos."

        else:
            return {
                "ok": False,
                "codigo": "HELIX_BATCH_ACCION_INVALIDA",
                "job_id": key,
                "respuesta": "Accion de control invalida.",
            }

        _write_job(key)
        public = _public(job)

    return {
        "ok": True,
        "codigo": "HELIX_BATCH_CONTROL_OK",
        "accion": action,
        "respuesta": respuesta,
        **public,
    }


def pausar_job(job_id: str) -> dict[str, Any]:
    return _control_job(job_id, "pause")


def reanudar_job(job_id: str) -> dict[str, Any]:
    return _control_job(job_id, "resume")


def detener_job(job_id: str) -> dict[str, Any]:
    return _control_job(job_id, "stop")


async def _wait_job_resumable(job_id: str) -> bool:
    while True:
        with _LOCK:
            job = _JOBS.get(job_id)

            if job is None:
                return False

            if bool(job.get("stop_requested", False)):
                return False

            paused = bool(job.get("pause_requested", False))

            if not paused:
                if str(job.get("estado") or "").strip().upper() == "PAUSADO":
                    job["estado"] = "PROCESANDO"
                    _write_job(job_id)
                return True

        await asyncio.sleep(1.0)


async def worker_loop(
    p,
    job_id: str,
    worker: int,
    queue: asyncio.Queue,
    username: str,
    password: str,
    search_lock: asyncio.Lock,
    area: str,
):
    log = _logger(job_id, worker)

    # Cada área usa perfiles de Chromium independientes para evitar
    # que Front y Back intenten abrir el mismo user_data_dir.
    profile = PROFILES / area / f"worker_{worker:02d}"
    profile.mkdir(
        parents=True,
        exist_ok=True,
    )

    context = None

    try:
        # Pequeño stagger para no golpear login al mismo tiempo.
        await asyncio.sleep(
            max(
                0.0,
                (worker - 1) * 2.0,
            )
        )

        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=True,
            viewport={
                "width": 1600,
                "height": 1000,
            },
        )

        page = context.pages[0] if context.pages else await context.new_page()

        await login_helix(
            page,
            username,
            password,
            worker,
            log,
        )

        while True:
            if not await _wait_job_resumable(job_id):
                break

            try:
                ticket = queue.get_nowait()

            except asyncio.QueueEmpty:
                break

            if not await _wait_job_resumable(job_id):
                queue.put_nowait(ticket)
                break

            started = time.perf_counter()

            try:
                # ==========================================
                # CONSULTA DIRECTA POR INC
                # ==========================================
                if INC_RE.fullmatch(ticket):

                    log.info(
                        "INC_START %s",
                        ticket,
                    )
                    # ATLAS_HELIX_RETRY_INC_V1
                    # Segundo intento controlado para INC directos.
                    #
                    # Reintenta:
                    #   - excepcion recuperable
                    #   - RELACIONADOS_NO_CONFIRMADO
                    #
                    # No reintenta:
                    #   - SIN_RELACIONADOS confirmado
                    #   - OK
                    #   - HELIX_CREDENCIALES_INVALIDAS

                    result = None
                    retry_reason = ""
                    first_attempt_error = ""

                    for attempt in (1, 2):

                        try:

                            log.info(
                                "INC_ATTEMPT %s ATTEMPT=%s/2",
                                ticket,
                                attempt,
                            )

                            print(
                                f"[RETRY_INC_V1] "
                                f"INC={ticket} "
                                f"ATTEMPT={attempt}/2 "
                                f"START",
                                flush=True,
                            )

                            result = await procesar_inc(
                                page,
                                ticket,
                                worker,
                                search_lock,
                            )

                            result["attempts"] = attempt
                            result["retry_used"] = attempt > 1
                            result["retry_reason"] = retry_reason

                            estado_attempt = str(
                                result.get("estado") or ""
                            ).strip().upper()

                            # OK y SIN_RELACIONADOS son finales.
                            if estado_attempt != "RELACIONADOS_NO_CONFIRMADO":

                                result["retry_recovered"] = attempt > 1

                                if first_attempt_error:
                                    result["first_attempt_error"] = (
                                        first_attempt_error
                                    )

                                print(
                                    f"[RETRY_INC_V1] "
                                    f"INC={ticket} "
                                    f"ATTEMPT={attempt}/2 "
                                    f"RESULT={estado_attempt or 'OK'} "
                                    f"FINAL=SI",
                                    flush=True,
                                )

                                break

                            # Primera consulta no pudo confirmar.
                            retry_reason = "RELACIONADOS_NO_CONFIRMADO"

                            if attempt == 1:

                                log.warning(
                                    "INC_RETRY %s ATTEMPT=1/2 REASON=%s",
                                    ticket,
                                    retry_reason,
                                )

                                print(
                                    f"[RETRY_INC_V1] "
                                    f"INC={ticket} "
                                    f"ATTEMPT=1/2 "
                                    f"RESULT=RELACIONADOS_NO_CONFIRMADO "
                                    f"RETRY=SI WAIT=2S",
                                    flush=True,
                                )

                                await page.wait_for_timeout(2000)
                                continue

                            # Segundo intento tambien no confirmado.
                            result["retry_recovered"] = False

                            print(
                                f"[RETRY_INC_V1] "
                                f"INC={ticket} "
                                f"ATTEMPT=2/2 "
                                f"RESULT=RELACIONADOS_NO_CONFIRMADO "
                                f"FINAL=SI",
                                flush=True,
                            )

                            break

                        except Exception as attempt_exc:

                            attempt_error = (
                                f"{type(attempt_exc).__name__}:"
                                f"{attempt_exc}"
                            )

                            attempt_error_upper = attempt_error.upper()

                            # Credenciales invalidas no son recuperables.
                            if (
                                "HELIX_CREDENCIALES_INVALIDAS"
                                in attempt_error_upper
                            ):

                                log.error(
                                    "INC_NO_RETRY %s ATTEMPT=%s/2 "
                                    "REASON=CREDENTIALS_INVALID",
                                    ticket,
                                    attempt,
                                )

                                raise

                            # Segundo intento tambien fallo.
                            if attempt >= 2:

                                log.error(
                                    "INC_RETRY_EXHAUSTED %s "
                                    "ATTEMPT=2/2 ERROR=%s",
                                    ticket,
                                    attempt_error,
                                )

                                print(
                                    f"[RETRY_INC_V1] "
                                    f"INC={ticket} "
                                    f"ATTEMPT=2/2 "
                                    f"ERROR={attempt_error!r} "
                                    f"FINAL=ERROR",
                                    flush=True,
                                )

                                raise

                            # Primer error: reintentar una sola vez.
                            first_attempt_error = attempt_error
                            retry_reason = "EXCEPTION"

                            log.warning(
                                "INC_RETRY %s ATTEMPT=1/2 "
                                "REASON=EXCEPTION ERROR=%s",
                                ticket,
                                attempt_error,
                            )

                            print(
                                f"[RETRY_INC_V1] "
                                f"INC={ticket} "
                                f"ATTEMPT=1/2 "
                                f"ERROR={attempt_error!r} "
                                f"RETRY=SI WAIT=2S",
                                flush=True,
                            )

                            await page.wait_for_timeout(2000)

                    # ATLAS_HELIX_RETRY_INC_V1_END

                    result["ticket_consultado"] = ticket
                    result["tipo_consulta"] = "INC"
                    result["tas_origen"] = ""

                # ==========================================
                # CONSULTA POR TAS
                # ==========================================
                elif TAS_RE.fullmatch(ticket):

                    log.info(
                        "TAS_START %s",
                        ticket,
                    )

                    print(
                        f"[BATCH_TAS] " f"WORKER={worker} " f"TAS={ticket} " f"START",
                        flush=True,
                    )

                    # Import local para evitar import circular:
                    # task_related_service importa este motor.
                    from app.services.helix.task_related_service import (
                        leer_tarea_y_abrir_incidente,
                    )

                    datos_tarea = {
                        "tas": ticket,
                    }

                    # La búsqueda Global Search debe mantenerse
                    # protegida por el mismo lock del motor.
                    async with search_lock:

                        # Crear una página limpia.
                        nueva_page = await context.new_page()

                        # Cerrar páginas del ticket anterior.
                        for extra in list(context.pages):
                            if extra is nueva_page:
                                continue

                            try:
                                await extra.close()
                            except Exception:
                                pass

                        page = nueva_page

                        await page.goto(
                            URL_HELIX,
                            wait_until="domcontentloaded",
                            timeout=90000,
                        )

                        await page.wait_for_timeout(1200)

                        incident_page = await leer_tarea_y_abrir_incidente(
                            page,
                            ticket,
                            datos_tarea,
                        )

                    inc_padre = str(datos_tarea.get("inc") or "").strip().upper()

                    if not INC_RE.fullmatch(inc_padre):
                        raise RuntimeError(
                            f"TAS_INC_PADRE_INVALIDO:"
                            f"TAS={ticket}:"
                            f"INC={inc_padre}"
                        )

                    print(
                        f"[BATCH_TAS] "
                        f"TAS={ticket} "
                        f"INC_PADRE={inc_padre} "
                        f"ABIERTO=SI",
                        flush=True,
                    )

                    # El INC ya quedó abierto por
                    # leer_tarea_y_abrir_incidente().
                    result = await procesar_inc(
                        incident_page,
                        inc_padre,
                        worker,
                        search_lock,
                        ya_abierto=True,
                    )

                    # Mantener INC como identificador principal
                    # para no romper el front actual.
                    result["inc"] = inc_padre

                    # Información de origen para posteriormente
                    # mostrar TAS -> INC en el front.
                    result["ticket_consultado"] = ticket
                    result["tipo_consulta"] = "TAS"
                    result["tas_origen"] = ticket

                    result["titulo_tarea"] = str(datos_tarea.get("titulo") or "")

                    result["estado_tarea"] = str(datos_tarea.get("estado") or "")

                    log.info(
                        "TAS_END %s INC=%s " "ESTADO=%s WO=%s TA=%s",
                        ticket,
                        inc_padre,
                        result.get("estado"),
                        result.get("total_wo"),
                        result.get("total_ta"),
                    )

                else:
                    raise RuntimeError(f"TICKET_NO_SOPORTADO:{ticket}")

                log.info(
                    "TICKET_END %s " "ESTADO=%s WO=%s TA=%s",
                    ticket,
                    result.get("estado"),
                    result.get("total_wo"),
                    result.get("total_ta"),
                )

            except Exception as exc:
                log.exception(
                    "TICKET_ERROR %s",
                    ticket,
                )

                result = {
                    "inc": (ticket if INC_RE.fullmatch(ticket) else ""),
                    "ticket_consultado": ticket,
                    "tipo_consulta": ("TAS" if TAS_RE.fullmatch(ticket) else "INC"),
                    "tas_origen": (ticket if TAS_RE.fullmatch(ticket) else ""),
                    "titulo": "",
                    "titulo_tarea": "",
                    "estado_tarea": "",
                    "estado": "ERROR",
                    "estado_incidente": "",
                    "fecha_creacion_incidente": "",
                    "alarmas": {
                        "consultado": False,
                        "total": None,
                        "activas": None,
                        "canceladas": None,
                        "capturadas": 0,
                        "captura_completa": False,
                        "detalle": [],
                        "activas_detalle": [],
                        "source": "",
                        "error": ("NO_CONSULTADO_POR_ERROR_TICKET"),
                    },
                    "alarmas_total": None,
                    "alarmas_activas_total": None,
                    "alarmas_canceladas_total": None,
                    "alarmas_activas_detalle": [],
                    "alarmas_detalle": [],
                    "total_wo": 0,
                    "wo_relacionadas": [],
                    "total_ta": 0,
                    "ta_relacionadas": [],
                    "relacionados_detalle": [],
                    "duracion_seg": round(
                        time.perf_counter() - started,
                        2,
                    ),
                    "worker": worker,
                    "frame_url": "",
                    "error": (f"{type(exc).__name__}: {exc}"),
                }

            _append_result(
                job_id,
                result,
            )

            queue.task_done()

    except Exception as exc:
        log.exception(
            "WORKER_FATAL %s",
            exc,
        )

    finally:
        if context is not None:
            try:
                await context.close()

            except Exception:
                pass


async def _run_async(job_id: str):
    started = time.perf_counter()

    try:
        with _LOCK:
            job = _JOBS[job_id]
            incidents = list(job["_incidentes"])
            worker_count = int(job["workers"])
            area = str(job.get("area", "front")).strip().lower()

            if area not in {"front", "back"}:
                area = "front"

            job["area"] = area
            job["estado"] = "PROCESANDO"
            job["iniciado"] = _now()
            _write_job(job_id)

        cfg = Settings.from_env()

        username = str(cfg.username or "").strip()

        password = str(cfg.password or "")

        if not username or not password:
            raise RuntimeError("CREDENCIALES_HELIX_NO_DISPONIBLES")

        queue: asyncio.Queue = asyncio.Queue()

        # Lock por job: solo protege Global Search.
        # La extraccion de relacionados sigue concurrente.
        search_lock = asyncio.Lock()

        for inc in incidents:
            queue.put_nowait(inc)

        async with async_playwright() as p:
            tasks = [
                asyncio.create_task(
                    worker_loop(
                        p,
                        job_id,
                        worker,
                        queue,
                        username,
                        password,
                        search_lock,
                        area,
                    )
                )
                for worker in range(
                    1,
                    worker_count + 1,
                )
            ]

            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

        with _LOCK:
            job = _JOBS[job_id]
            stopped = bool(job.get("stop_requested", False))

        if stopped:
            while True:
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

        else:
            while True:
                try:
                    inc = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                _append_result(
                    job_id,
                    {
                        "inc": inc,
                        "titulo": "",
                        "estado": "ERROR",
                        "estado_incidente": "",
                        "alarmas": {
                            "consultado": False,
                            "total": None,
                            "activas": None,
                            "canceladas": None,
                            "capturadas": 0,
                            "captura_completa": False,
                            "detalle": [],
                            "activas_detalle": [],
                            "source": "",
                            "error": "NO_CONSULTADO_POR_ERROR_INC",
                        },
                        "alarmas_total": None,
                        "alarmas_activas_total": None,
                        "alarmas_canceladas_total": None,
                        "alarmas_activas_detalle": [],
                        "alarmas_detalle": [],
                        "total_wo": 0,
                        "wo_relacionadas": [],
                        "total_ta": 0,
                        "ta_relacionadas": [],
                        "relacionados_detalle": [],
                        "duracion_seg": 0.0,
                        "worker": 0,
                        "frame_url": "",
                        "error": "WORKER_NO_DISPONIBLE",
                    },
                )

        with _LOCK:
            job = _JOBS[job_id]
            job["estado"] = "DETENIDO" if stopped else "COMPLETADO"
            job["pause_requested"] = False
            job["finalizado"] = _now()
            job["duracion_seg"] = round(
                time.perf_counter() - started,
                2,
            )
            _write_job(job_id)

    except Exception as exc:
        with _LOCK:
            job = _JOBS[job_id]
            job["estado"] = "ERROR"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["finalizado"] = _now()
            job["duracion_seg"] = round(
                time.perf_counter() - started,
                2,
            )
            _write_job(job_id)

    finally:
        with _LOCK:
            job = _JOBS.get(job_id, {})
            area = str(job.get("area", "front")).strip().lower()

            if area not in {"front", "back"}:
                area = "front"

            if _ACTIVE_JOB_IDS.get(area) == job_id:
                _ACTIVE_JOB_IDS[area] = None


def _thread_main(job_id: str):
    asyncio.run(_run_async(job_id))


# ATLAS_HELIX_V443_BACKEND_APPLIED

# ATLAS_HELIX_V45_BACKEND_APPLIED

