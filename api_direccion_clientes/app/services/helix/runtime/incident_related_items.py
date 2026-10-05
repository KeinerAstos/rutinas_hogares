# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Page, async_playwright

from app.services.helix.smartit_scraper import Settings, iniciar_sesion
from app.services.helix.runtime.helix_runner import (
    PROFILE_DIR,
    SEARCH_BUTTON_SELECTORS,
    SEARCH_INPUT_SELECTORS,
    find_related_tab_once,
    find_visible_across_frames,
    wait_no_loader,
)
from app.services.helix.runtime.wo_related_dryrun import _open_full_incident_view


_INC_RE = re.compile(r"^INC\d{12}$", re.IGNORECASE)
_WO_RE = re.compile(r"\bWO\d{13,14}\b", re.IGNORECASE)
_TA_RE = re.compile(r"\b(?:TA|TAS)\d{6,}\b", re.IGNORECASE)

LOG_DIR = Path(r"C:\xampp\htdocs\rutinas_hogares\api_direccion_clientes\logs\helix_relacionados")
LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class RelatedResult:
    inc: str
    titulo: str = ""
    wo_relacionadas: list[str] | None = None
    ta_relacionadas: list[str] | None = None
    estado: str = "RELACIONADOS_NO_CONFIRMADO"
    duracion_seg: float = 0.0
    frame_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["wo_relacionadas"] = self.wo_relacionadas or []
        data["ta_relacionadas"] = self.ta_relacionadas or []
        data["total_wo"] = len(data["wo_relacionadas"])
        data["total_ta"] = len(data["ta_relacionadas"])
        return data


def _logger(inc: str) -> logging.Logger:
    logger = logging.getLogger(f"helix_inc_related_{inc}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = logging.FileHandler(
        LOG_DIR / f"{inc}_{time.strftime('%Y%m%d_%H%M%S')}.log",
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    return logger


async def _buscar_incidente_unico(
    page: Page,
    inc: str,
    cfg: Settings,
    logger: logging.Logger,
) -> None:
    """
    Una sola busqueda logica por INC.

    Reutiliza exactamente los selectores de busqueda global de helix_runner:
    1. abre la lupa;
    2. localiza el input;
    3. escribe el INC una sola vez;
    4. espera el resultado exacto sin reenviar la busqueda.
    """

    _, button, button_selector = await find_visible_across_frames(
        page,
        SEARCH_BUTTON_SELECTORS,
        min(cfg.timeout_ms, 30000),
        logger,
        "lupa de busqueda global",
    )
    logger.info("INC: abriendo busqueda global con %s", button_selector)
    await button.click()

    _, search_input, input_selector = await find_visible_across_frames(
        page,
        SEARCH_INPUT_SELECTORS,
        min(cfg.timeout_ms, 30000),
        logger,
        "input de busqueda global",
    )

    logger.info("INC: escribiendo %s en %s", inc, input_selector)
    await search_input.fill("")
    await search_input.fill(inc)
    await search_input.press("Enter")

    deadline = time.monotonic() + 20.0
    show_all_used = False

    while time.monotonic() < deadline:
        for frame in list(page.frames):
            selectors = (
                'div.results-panel__item-layout[role="link"]',
                'div.results-panel__item-layout',
                '[role="link"]',
            )

            for selector in selectors:
                try:
                    cards = frame.locator(selector)
                    count = min(await cards.count(), 300)
                except Exception:
                    continue

                for i in range(count):
                    card = cards.nth(i)

                    try:
                        if not await card.is_visible():
                            continue
                    except Exception:
                        continue

                    try:
                        text = re.sub(
                            r"\s+",
                            " ",
                            await card.inner_text(timeout=1500),
                        ).strip().upper()
                    except Exception:
                        continue

                    ids = re.findall(r"\bINC\d{12}\b", text)
                    if inc not in ids:
                        continue

                    # Preferir el campo visible de ID cuando SmartIT lo expone.
                    exact_confirmed = False
                    try:
                        id_span = card.locator(
                            "div.search-item-layout__id span"
                        ).first
                        if await id_span.count():
                            visible_id = re.sub(
                                r"\s+",
                                "",
                                await id_span.inner_text(timeout=1200),
                            ).upper()
                            if visible_id == inc:
                                exact_confirmed = True
                    except Exception:
                        pass

                    if not exact_confirmed:
                        # Fallback: aceptar sólo si el INC solicitado aparece como
                        # identificador completo dentro de la tarjeta.
                        exact_confirmed = inc in ids

                    if not exact_confirmed:
                        continue

                    try:
                        await card.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass

                    logger.info("INC: resultado exacto encontrado: %s", inc)

                    pages_before = {
                        id(candidate)
                        for candidate in page.context.pages
                        if not candidate.is_closed()
                    }

                    await card.click(timeout=10000)
                    await page.wait_for_timeout(1200)

                    candidates = [
                        candidate
                        for candidate in page.context.pages
                        if not candidate.is_closed()
                    ]
                    new_pages = [
                        candidate
                        for candidate in candidates
                        if id(candidate) not in pages_before
                    ]

                    if new_pages:
                        page = new_pages[-1]
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        logger.info("INC: adoptada nueva pagina. URL=%s", page.url)
                    else:
                        logger.info("INC: detalle en pagina actual. URL=%s", page.url)

                    return page

        if not show_all_used:
            for frame in list(page.frames):
                try:
                    show_all = frame.locator('[ux-id="show-all-link"]').first
                    if await show_all.count() and await show_all.is_visible():
                        await show_all.click(timeout=2500)
                        show_all_used = True
                        logger.info("INC: se amplio la vista de resultados.")
                        break
                except Exception:
                    continue

        await page.wait_for_timeout(250)

    raise RuntimeError(f"INC_NO_ENCONTRADO_UNICO:{inc}")

async def _titulo_incidente(page: Page, inc: str) -> str:
    for frame in list(page.frames):
        for selector in (
            "h2#ar1000000000_data",
            '[id="ar1000000000_data"]',
            "h2",
        ):
            try:
                loc = frame.locator(selector).first
                if not await loc.count() or not await loc.is_visible():
                    continue
                text = re.sub(r"\s+", " ", await loc.inner_text()).strip()
                if text and text.upper() != inc:
                    return text
            except Exception:
                continue
    return ""


async def _find_related_across_pages(page: Page):
    candidates = [
        candidate
        for candidate in page.context.pages
        if not candidate.is_closed()
    ]

    ordered = [page] + [
        candidate
        for candidate in reversed(candidates)
        if id(candidate) != id(page)
    ]

    seen = set()
    for candidate in ordered:
        if id(candidate) in seen or candidate.is_closed():
            continue
        seen.add(id(candidate))

        try:
            found = await find_related_tab_once(candidate)
        except Exception:
            found = None

        if found:
            return candidate, found

    return page, None

async def _promover_ticketdisplay_preview_a_goto(
    page: Page,
    logger: logging.Logger,
):
    """
    SmartIT puede abrir el INC encontrado dentro de un iframe PWA:

      #/forms/onbmc-s/SHR:SV_TicketDisplay/SV_TicketDisplay?...&context=PREVIEW

    La vista operativa completa de ese mismo TicketDisplay se obtiene por:

      #/goto/SHR:SV_TicketDisplay/SV_TicketDisplay?...

    Este fallback solo navega el iframe; no modifica datos.
    """
    for candidate_page in [
        p for p in page.context.pages if not p.is_closed()
    ]:
        for frame in list(candidate_page.frames):
            frame_url = str(frame.url or "")

            if "SHR:SV_TicketDisplay/SV_TicketDisplay" not in frame_url:
                continue
            if "#/forms/onbmc-s/" not in frame_url:
                continue
            if "context=PREVIEW" not in frame_url:
                continue

            goto_url = frame_url.replace(
                "#/forms/onbmc-s/",
                "#/goto/",
                1,
            )

            logger.info(
                "INC: promoviendo TicketDisplay PREVIEW -> GOTO. source=%s target=%s",
                frame_url,
                goto_url,
            )

            try:
                await frame.goto(
                    goto_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception as exc:
                logger.warning(
                    "INC: frame.goto PWA termino con espera parcial: %s",
                    exc,
                )

            await candidate_page.wait_for_timeout(1000)

            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                try:
                    found = await find_related_tab_once(candidate_page)
                except Exception:
                    found = None

                if found:
                    logger.info(
                        "INC: TicketDisplay GOTO listo. FRAME_URL=%s",
                        frame.url,
                    )
                    return candidate_page, found

                await candidate_page.wait_for_timeout(300)

    return page, None

async def _localizar_related_frame_y_click_v25(
    page: Page,
    inc: str,
    logger: logging.Logger,
):
    """
    Port directo del flujo V2.5 que ya recuperó WO + TA:
    - esperar el frame PWA real;
    - hidratarlo;
    - scroll progresivo para lazy-load;
    - intentar click de Elementos relacionados;
    - si el click no aparece, continuar inspeccionando el PWA.
    """
    pwa_frame = None

    for _ in range(40):
        frames = list(page.frames)
        candidatos = [
            f
            for f in frames
            if "miasistencia360.claro.com.co/arsys/pwa" in (f.url or "").lower()
            and "login-light" not in (f.url or "").lower()
            and "fake-route" not in (f.url or "").lower()
        ]

        if candidatos:
            candidatos.sort(
                key=lambda f: (
                    "sv_ticketdisplay" not in (f.url or "").lower()
                    and "/goto/" not in (f.url or "").lower(),
                    len(f.url or ""),
                )
            )
            pwa_frame = candidatos[0]
            break

        await page.wait_for_timeout(300)

    if pwa_frame is None:
        for frame in page.frames:
            try:
                body = await frame.locator("body").inner_text(timeout=2500)
            except Exception:
                continue

            low = (body or "").lower()
            if (
                (inc and inc.lower() in low)
                or "elementos relacionados" in low
                or "related items" in low
            ):
                pwa_frame = frame
                break

    if pwa_frame is None:
        logger.warning("INC: FRAME_PWA_TICKET_NO_ENCONTRADO")
        return None, "NO_PWA"

    logger.info("INC: PWA seleccionado URL=%s", pwa_frame.url)

    # Esperar hidratación real.
    for _ in range(12):
        try:
            body = await pwa_frame.locator("body").inner_text(timeout=3500)
        except Exception:
            body = ""

        if body and (
            (inc and inc.lower() in body.lower())
            or len(body) > 1000
        ):
            break

        await page.wait_for_timeout(350)

    # Disparar lazy-load igual que V2.5.
    for _ in range(6):
        try:
            await pwa_frame.evaluate(
                """
                () => {
                  const els = [...document.querySelectorAll('*')];
                  for (const el of els) {
                    const s = getComputedStyle(el);
                    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') &&
                        el.scrollHeight > el.clientHeight + 30) {
                      el.scrollTop = Math.min(
                        el.scrollHeight,
                        el.scrollTop + Math.max(600, el.clientHeight)
                      );
                      el.dispatchEvent(new Event('scroll', {bubbles:true}));
                    }
                  }
                  window.scrollBy(0, Math.max(700, window.innerHeight * 0.85));
                }
                """
            )
        except Exception:
            pass

        await page.wait_for_timeout(350)

    frames_ordenados = [pwa_frame] + [
        f for f in page.frames if f is not pwa_frame
    ]

    for _ in range(5):
        candidatos = []

        for frame in frames_ordenados:
            for sel in (
                'text="Elementos relacionados"',
                'text=/Elementos\\s+relacionados/i',
                'text="Related items"',
                'text=/Related\\s+items/i',
                '[ux-id*="related" i]',
                '[class*="related" i]',
            ):
                try:
                    loc = frame.locator(sel)
                    count = min(await loc.count(), 40)

                    for i in range(count):
                        el = loc.nth(i)
                        try:
                            txt = re.sub(
                                r"\s+",
                                " ",
                                await el.inner_text(timeout=1200),
                            ).strip()
                        except Exception:
                            txt = ""

                        low = txt.lower()
                        if (
                            "elementos relacionados" in low
                            or "related items" in low
                        ):
                            candidatos.append((frame, el, txt))
                except Exception:
                    continue

        candidatos.sort(
            key=lambda t: (
                t[0] is not pwa_frame,
                "arsys/pwa" not in (t[0].url or "").lower(),
                len(t[2]),
            )
        )

        for frame, el, _ in candidatos:
            try:
                if await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await el.click(timeout=9000)
                    await page.wait_for_timeout(1300)
                    logger.info(
                        "INC: Elementos relacionados CLICK_OK frame=%s",
                        frame.url,
                    )
                    return frame, "CLICK_OK"
            except Exception:
                continue

        # Seguir activando lazy-load.
        try:
            await pwa_frame.evaluate(
                """
                () => {
                  const els = [...document.querySelectorAll('*')];
                  for (const el of els) {
                    const s = getComputedStyle(el);
                    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') &&
                        el.scrollHeight > el.clientHeight + 30) {
                      el.scrollTop = el.scrollHeight;
                      el.dispatchEvent(new Event('scroll', {bubbles:true}));
                    }
                  }
                  window.scrollTo(0, document.body.scrollHeight);
                }
                """
            )
        except Exception:
            pass

        await page.wait_for_timeout(500)

    logger.info(
        "INC: Elementos relacionados no clickeado; "
        "se continúa con FALLBACK_PWA_SCAN."
    )
    return pwa_frame, "FALLBACK_PWA_SCAN"


async def _colectar_ids_todos_frames(
    page: Page,
    all_wos: set[str],
    all_tas: set[str],
):
    """
    Une IDs visibles en TODOS los frames vivos.
    Sirve como red de seguridad cuando Helix virtualiza WO y TA
    en contenedores/frames diferentes.
    """
    totals: list[int] = []
    related_seen = False

    for candidate in list(page.frames):
        try:
            data = await candidate.evaluate(
                r"""
                () => {
                  const txt =
                    (document.body && document.body.innerText) || "";

                  const wos = [
                    ...new Set(
                      (txt.match(/\bWO\d{7,}\b/gi) || [])
                        .map(x => x.toUpperCase())
                    )
                  ];

                  const tas = [
                    ...new Set(
                      (txt.match(/\b(?:TA|TAS)\d{5,}\b/gi) || [])
                        .map(x => x.toUpperCase())
                    )
                  ];

                  const totals = [
                    ...txt.matchAll(
                      /\b(\d+)\s+fila(?:s)?\s+en\s+total\b/gi
                    )
                  ]
                    .map(m => parseInt(m[1],10))
                    .filter(Number.isFinite);

                  return {
                    wos,
                    tas,
                    totals,
                    related:
                      /Elementos relacionados/i.test(txt) ||
                      /Related items/i.test(txt)
                  };
                }
                """
            )
        except Exception:
            continue

        for wo in data.get("wos") or []:
            all_wos.add(str(wo).upper())

        for ta in data.get("tas") or []:
            all_tas.add(str(ta).upper())

        for value in data.get("totals") or []:
            try:
                totals.append(int(value))
            except Exception:
                pass

        if data.get("related"):
            related_seen = True

    return totals, related_seen

async def _extraer_related_v25(
    frame,
    page: Page,
    logger: logging.Logger,
):
    """
    Port fiel del extractor V2.5 certificado.
    Conserva:
    - selección del frame con mayor evidencia WO/TA;
    - scroller data-helix-wo-scroller;
    - lectura por filas virtualizadas + texto visible;
    - acumulación antes de cada movimiento;
    - scroll de 55% del viewport;
    - hasta 400 pasos.
    """
    all_wos = set()
    all_tas = set()
    filas_helix = None
    evid_related = False
    paginas_vistas = 0

    frames = [frame]
    try:
        for f in page.frames:
            if f not in frames:
                frames.append(f)
    except Exception:
        pass

    frame_obj = frame
    mejor_score = -1

    for f in frames:
        try:
            data = await f.evaluate(
                r"""
                () => {
                  const txt =
                    (document.body && document.body.innerText) || "";
                  const wos =
                    (txt.match(/\bWO\d{7,}\b/gi) || []);
                  const tas =
                    (txt.match(/\b(?:TA|TAS)\d{5,}\b/gi) || []);
                  const related =
                    /Elementos relacionados/i.test(txt) ||
                    /Related items/i.test(txt);
                  const rows =
                    document.querySelectorAll(
                      "tr,[role='row']"
                    ).length;

                  return {
                    woCount:
                      new Set(
                        wos.map(x => x.toUpperCase())
                      ).size,
                    taCount:
                      new Set(
                        tas.map(x => x.toUpperCase())
                      ).size,
                    related,
                    rows
                  };
                }
                """
            )

            score = (
                (
                    data.get("woCount", 0)
                    + data.get("taCount", 0)
                ) * 1000
                + (500 if data.get("related") else 0)
                + min(data.get("rows", 0), 300)
            )

            if score > mejor_score:
                mejor_score = score
                frame_obj = f
        except Exception:
            pass

    logger.info(
        "INC: V2.5 frame seleccionado SCORE=%s URL=%s",
        mejor_score,
        frame_obj.url,
    )

    contexto = await frame_obj.evaluate(
        r"""
        () => {
          const norm =
            s => (s || "").replace(/\s+/g, " ").trim();

          const all =
            [...document.querySelectorAll("body *")];
          const candidates = [];

          for (const el of all) {
            const s = getComputedStyle(el);

            const isScrollable =
              (
                s.overflowY === "auto"
                || s.overflowY === "scroll"
              )
              && el.scrollHeight > el.clientHeight + 20
              && el.clientHeight > 80;

            if (!isScrollable) continue;

            const txt =
              norm(el.innerText || el.textContent || "");

            const woCount =
              new Set(
                (txt.match(/\bWO\d{7,}\b/gi) || [])
                  .map(x => x.toUpperCase())
              ).size;

            const taCount =
              new Set(
                (txt.match(/\b(?:TA|TAS)\d{5,}\b/gi) || [])
                  .map(x => x.toUpperCase())
              ).size;

            const rowCount =
              el.querySelectorAll(
                "tr,[role='row'],[class*='row']"
              ).length;

            const hasTable =
              !!el.querySelector(
                "table,[role='grid'],[class*='grid'],[class*='table']"
              );

            const delta =
              el.scrollHeight - el.clientHeight;

            const score =
              ((woCount + taCount) > 0 ? 100000 : 0)
              + (woCount + taCount) * 5000
              + (rowCount > 0 ? 20000 : 0)
              + Math.min(rowCount, 500) * 50
              + (hasTable ? 10000 : 0)
              + Math.min(delta, 100000) / 10;

            candidates.push({
              el,
              score,
              woCount,
              taCount,
              rowCount,
              hasTable,
              scrollTop: el.scrollTop,
              scrollHeight: el.scrollHeight,
              clientHeight: el.clientHeight
            });
          }

          candidates.sort(
            (a,b) => b.score - a.score
          );

          const c = candidates[0] || null;

          if (c) {
            document.querySelectorAll(
              '[data-helix-wo-scroller="1"]'
            ).forEach(
              x => x.removeAttribute(
                "data-helix-wo-scroller"
              )
            );

            c.el.dataset.helixWoScroller = "1";
            c.el.scrollTop = 0;
            c.el.dispatchEvent(
              new Event("scroll", {bubbles:true})
            );
          }

          const bodyTxt =
            (document.body && document.body.innerText) || "";

          return {
            found: !!c,
            score: c ? c.score : 0,
            initialWO: c ? c.woCount : 0,
            initialTA: c ? c.taCount : 0,
            rowCount: c ? c.rowCount : 0,
            scrollHeight: c ? c.scrollHeight : 0,
            clientHeight: c ? c.clientHeight : 0,
            relatedTextVisible:
              /Elementos relacionados/i.test(bodyTxt)
              || /Related items/i.test(bodyTxt)
          };
        }
        """
    )

    logger.info(
        "INC: V2.5 SCROLLER found=%s score=%s initialWO=%s initialTA=%s rows=%s",
        contexto.get("found"),
        contexto.get("score"),
        contexto.get("initialWO"),
        contexto.get("initialTA"),
        contexto.get("rowCount"),
    )

    if contexto.get("relatedTextVisible"):
        evid_related = True

    if not contexto.get("found"):
        # Fallback multi-frame dentro del MISMO intento funcional.
        # No vuelve a buscar el INC ni abre una segunda consulta.
        frames_fallback = []

        try:
            for candidate in [frame_obj, *list(page.frames)]:
                if candidate not in frames_fallback:
                    frames_fallback.append(candidate)
        except Exception:
            frames_fallback = [frame_obj]

        fallback_totals = []
        fallback_frames_ok = 0

        for fallback_frame in frames_fallback:
            try:
                data = await fallback_frame.evaluate(
                    r"""
                    () => {
                      const body =
                        (document.body && document.body.innerText)
                        || "";

                      const attrs = [];

                      const nodes =
                        document.querySelectorAll(
                          "a,button,[href],[title],[aria-label],"
                          + "[data-id],[data-row-key],[data-value]"
                        );

                      const limit =
                        Math.min(nodes.length, 5000);

                      for (let i = 0; i < limit; i++) {
                        const el = nodes[i];

                        const values = [
                          el.innerText,
                          el.textContent,
                          el.getAttribute("href"),
                          el.getAttribute("title"),
                          el.getAttribute("aria-label"),
                          el.getAttribute("data-id"),
                          el.getAttribute("data-row-key"),
                          el.getAttribute("data-value")
                        ];

                        for (const value of values) {
                          if (
                            value
                            && /\b(?:WO|TA|TAS)\d{5,}\b/i.test(value)
                          ) {
                            attrs.push(value);
                          }
                        }
                      }

                      const txt =
                        body + "\n" + attrs.join("\n");

                      return {
                        wos: [
                          ...new Set(
                            (
                              txt.match(/\bWO\d{7,}\b/gi)
                              || []
                            ).map(x => x.toUpperCase())
                          )
                        ],
                        tas: [
                          ...new Set(
                            (
                              txt.match(
                                /\b(?:TA|TAS)\d{5,}\b/gi
                              )
                              || []
                            ).map(x => x.toUpperCase())
                          )
                        ],
                        totals: [
                          ...txt.matchAll(
                            /\b(\d+)\s+fila(?:s)?\s+en\s+total\b/gi
                          )
                        ].map(m => parseInt(m[1], 10))
                      };
                    }
                    """
                )

                fallback_frames_ok += 1

                for wo in data.get("wos") or []:
                    all_wos.add(wo)

                for ta in data.get("tas") or []:
                    all_tas.add(ta)

                fallback_totals.extend(
                    x
                    for x in (data.get("totals") or [])
                    if isinstance(x, int)
                )

            except Exception:
                continue

        if fallback_totals:
            filas_helix = max(fallback_totals)

        evid_related = (
            evid_related
            or bool(all_wos)
            or bool(all_tas)
        )

        logger.info(
            "INC: V2.5 FALLBACK_ALL_FRAMES frames=%s WO=%s TA=%s",
            fallback_frames_ok,
            len(all_wos),
            len(all_tas),
        )

        return (
            sorted(all_wos),
            sorted(all_tas),
            filas_helix,
            evid_related,
            frame_obj.url,
        )
    max_steps = 400
    last_top = -1
    stable_steps = 0
    paginas_vistas = 1

    for paso in range(max_steps):
        data = await frame_obj.evaluate(
            r"""
            () => {
              const sc =
                document.querySelector(
                  '[data-helix-wo-scroller="1"]'
                );

              if (!sc) return null;

              const norm =
                s => (s || "")
                  .replace(/\s+/g, " ")
                  .trim();

              let rows =
                [...sc.querySelectorAll(
                  "tr,[role='row']"
                )];

              if (!rows.length) {
                rows =
                  [...sc.querySelectorAll(
                    "[class*='row'],[class*='record'],[class*='item']"
                  )];
              }

              const rowTexts = [];
              const seen = new Set();

              for (const el of rows) {
                const t =
                  norm(
                    el.innerText
                    || el.textContent
                    || ""
                  );

                if (
                  !t
                  || t.length > 2500
                  || seen.has(t)
                ) {
                  continue;
                }

                if (
                  /\b(?:WO|TA|TAS)\d{5,}\b/i.test(t)
                ) {
                  seen.add(t);
                  rowTexts.push(t);
                }
              }

              const visibleText =
                norm(
                  sc.innerText
                  || sc.textContent
                  || ""
                );

              const wos = new Set();
              const tas = new Set();

              for (const t of rowTexts) {
                for (
                  const m
                  of t.match(/\bWO\d{7,}\b/gi) || []
                ) {
                  wos.add(m.toUpperCase());
                }

                for (
                  const m
                  of t.match(/\b(?:TA|TAS)\d{5,}\b/gi) || []
                ) {
                  tas.add(m.toUpperCase());
                }
              }

              for (
                const m
                of visibleText.match(/\bWO\d{7,}\b/gi) || []
              ) {
                wos.add(m.toUpperCase());
              }

              for (
                const m
                of visibleText.match(/\b(?:TA|TAS)\d{5,}\b/gi) || []
              ) {
                tas.add(m.toUpperCase());
              }

              const totals = [
                ...visibleText.matchAll(
                  /\b(\d+)\s+fila(?:s)?\s+en\s+total\b/gi
                )
              ]
                .map(m => parseInt(m[1],10))
                .filter(Number.isFinite);

              return {
                wos: [...wos],
                tas: [...tas],
                rowCount: rowTexts.length,
                totalRows:
                  totals.length
                    ? Math.max(...totals)
                    : null,
                top: sc.scrollTop,
                sh: sc.scrollHeight,
                ch: sc.clientHeight
              };
            }
            """
        )

        if not data:
            break

        before = len(all_wos)

        for wo in data.get("wos") or []:
            all_wos.add(wo.upper())

        for ta in data.get("tas") or []:
            all_tas.add(ta.upper())

        after = len(all_wos)

        if data.get("totalRows") is not None:
            val = int(data["totalRows"])
            filas_helix = (
                val
                if filas_helix is None
                else max(filas_helix, val)
            )

        top = int(data.get("top") or 0)
        sh = int(data.get("sh") or 0)
        ch = int(data.get("ch") or 0)
        max_top = max(0, sh - ch)

        if (
            paso == 0
            or paso % 10 == 0
            or top >= max_top - 2
        ):
            logger.info(
                "INC: [SCROLL_V25] paso=%s pos=%s/%s ROWS=%s WO=%s TA=%s",
                paso,
                top,
                max_top,
                data.get("rowCount", 0),
                len(all_wos),
                len(all_tas),
            )

        if after == before:
            stable_steps += 1
        else:
            stable_steps = 0

        if top >= max_top - 2:
            break

        if (
            top == last_top
            and stable_steps >= 3
        ):
            break

        last_top = top

        move = await frame_obj.evaluate(
            r"""
            () => {
              const sc =
                document.querySelector(
                  '[data-helix-wo-scroller="1"]'
                );

              if (!sc) {
                return {
                  moved:false,
                  top:0,
                  max:0
                };
              }

              const old = sc.scrollTop;
              const max =
                Math.max(
                  0,
                  sc.scrollHeight - sc.clientHeight
                );

              const step =
                Math.max(
                  100,
                  Math.floor(
                    sc.clientHeight * 0.55
                  )
                );

              const target =
                Math.min(max, old + step);

              sc.scrollTop = target;
              sc.dispatchEvent(
                new Event(
                  "scroll",
                  {bubbles:true}
                )
              );

              return {
                moved:
                  target > old + 1,
                top: target,
                max
              };
            }
            """
        )

        await frame_obj.wait_for_timeout(120)

        if not move.get("moved"):
            break

    evid_related = (
        evid_related
        or bool(all_wos)
        or bool(all_tas)
        or contexto.get("found", False)
    )

    logger.info(
        "INC: V2.5 FINAL WO=%s TA=%s TOTAL_REL=%s",
        len(all_wos),
        len(all_tas),
        filas_helix,
    )

    return (
        sorted(all_wos),
        sorted(all_tas),
        filas_helix,
        evid_related,
        frame_obj.url,
    )

async def _leer_relacionados_completos(
    page: Page,
    cfg: Settings,
    logger: logging.Logger,
    inc: str = "",
):
    related_frame, tab_modo = (
        await _localizar_related_frame_y_click_v25(
            page,
            inc,
            logger,
        )
    )

    if related_frame is None:
        logger.warning(
            "INC: relacionados no confirmados porque no existe frame PWA."
        )
        return page, [], [], False, ""

    (
        wos,
        tas,
        filas_helix,
        evid_related,
        frame_url,
    ) = await _extraer_related_v25(
        related_frame,
        page,
        logger,
    )

    logger.info(
        "INC: relacionados resultado "
        "TAB_MODO=%s TOTAL_RELACIONADOS=%s WO=%s TA=%s",
        tab_modo,
        filas_helix,
        len(wos),
        len(tas),
    )

    return (
        page,
        wos,
        tas,
        evid_related,
        str(frame_url or ""),
    )

async def consultar_relacionados_incidente_async(
    incident: str,
    *,
    headless: bool = True,
) -> dict[str, Any]:
    inc = str(incident or "").strip().upper()
    if not _INC_RE.fullmatch(inc):
        return {
            "ok": False,
            "codigo": "HELIX_INC_INVALIDO",
            "inc": inc,
            "titulo": "",
            "total_wo": 0,
            "wo_relacionadas": [],
            "total_ta": 0,
            "ta_relacionadas": [],
            "estado": "ERROR",
            "error": "El incidente debe tener formato INC seguido de 12 dígitos.",
        }

    started = time.monotonic()
    logger = _logger(inc)
    cfg = Settings.from_env()
    cfg = Settings(
        url=cfg.url,
        username=cfg.username,
        password=cfg.password,
        headless=bool(headless),
        timeout_ms=cfg.timeout_ms,
        slow_mo_ms=cfg.slow_mo_ms,
        keep_open_seconds=0,
        download_timeout_ms=cfg.download_timeout_ms,
        output_dir=cfg.output_dir,
        dashboard_csv=cfg.dashboard_csv,
    )

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=cfg.headless,
            slow_mo=cfg.slow_mo_ms,
            args=["--start-maximized", "--disable-notifications"],
        )
        try:
            pages = context.pages
            page = pages[0] if pages else await context.new_page()

            await iniciar_sesion(page, cfg, logger)
            page = await _buscar_incidente_unico(page, inc, cfg, logger)
            titulo = await _titulo_incidente(page, inc)
            page, wos, tas, confirmed, frame_url = await _leer_relacionados_completos(
                page, cfg, logger, inc
            )

            if wos or tas:
                estado = "OK"
            elif confirmed:
                estado = "SIN_RELACIONADOS"
            else:
                estado = "RELACIONADOS_NO_CONFIRMADO"

            result = RelatedResult(
                inc=inc,
                titulo=titulo,
                wo_relacionadas=wos,
                ta_relacionadas=tas,
                estado=estado,
                duracion_seg=round(time.monotonic() - started, 2),
                frame_url=frame_url,
            ).to_dict()
            result.update(
                {
                    "ok": estado in {"OK", "SIN_RELACIONADOS"},
                    "codigo": f"HELIX_INC_RELACIONADOS_{estado}",
                    "origen": "HELIX",
                }
            )
            return result
        finally:
            try:
                await context.close()
            except Exception:
                pass






