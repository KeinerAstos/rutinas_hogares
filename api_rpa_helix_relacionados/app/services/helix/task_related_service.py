"""Lectura de tareas usando la seleccion exacta del backend existente."""

import asyncio
import json
import re
import time

from app.services.helix import incident_related_batch_service as motor

LEER_TAREA_JS = r"""() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();

  const visible = el => !!(
    el &&
    el.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden'
  );

  const text = el => norm(
    el && (el.value || el.innerText || el.textContent)
  );

  const labels = [
    ...document.querySelectorAll(
      'label,[id$="_label"],[aria-label],dt,div,span'
    )
  ].filter(visible);

  function field(names) {
    for (const label of labels) {
      const name = norm(
        label.getAttribute('aria-label') ||
        label.textContent
      )
        .replace(/[:*]$/, '')
        .trim();

      if (!names.includes(name.toLowerCase())) {
        continue;
      }

      if (label.matches('input,textarea,select')) {
        return text(label);
      }

      const target = document.getElementById(
        label.getAttribute('for')
      );

      if (visible(target) && text(target)) {
        return text(target);
      }

      if (label.id.endsWith('_label')) {
        const target = document.getElementById(
          label.id.replace(/_label$/, '_data')
        );

        if (visible(target) && text(target)) {
          return text(target);
        }
      }

      const sibling = label.nextElementSibling;

      if (visible(sibling) && text(sibling)) {
        return text(sibling);
      }

      const box = label.parentElement;

      if (box) {
        const input = [
          ...box.querySelectorAll(
            'input,textarea,select,[role="combobox"]'
          )
        ].find(visible);

        if (input && text(input)) {
          return text(input);
        }
      }
    }

    return '';
  }

  document
    .querySelectorAll('[data-helix-parent-inc]')
    .forEach(
      el => el.removeAttribute('data-helix-parent-inc')
    );

  const candidates = [];

  for (
    const link of document.querySelectorAll(
      'button,a,[role="link"]'
    )
  ) {
    if (!visible(link)) {
      continue;
    }

    const ids = [
      ...new Set(
        (
          `${text(link)} ${
            link.getAttribute('aria-label') || ''
          }`.match(/\bINC\d{12}\b/gi) || []
        ).map(x => x.toUpperCase())
      )
    ];

    if (ids.length !== 1) {
      continue;
    }

    let contextual = false;

    for (
      let node = link.parentElement, i = 0;
      node && node !== document.body && i < 4;
      node = node.parentElement, i++
    ) {
      if (
        text(node).length < 1200 &&
        /(?:Esta es una tarea para|This is a task (?:for|of))/i
          .test(text(node))
      ) {
        contextual = true;
        break;
      }
    }

    if (
      contextual ||
      link.id === 'ar10000006_data'
    ) {
      candidates.push({
        link,
        inc: ids[0],
        contextual
      });
    }
  }

  const selected = candidates.some(c => c.contextual)
    ? candidates.filter(c => c.contextual)
    : candidates;

  const parents = [];

  for (const c of selected) {
    c.link.setAttribute(
      'data-helix-parent-inc',
      c.inc
    );

    if (!parents.some(p => p.inc === c.inc)) {
      parents.push({
        inc: c.inc
      });
    }
  }

  const title = document.querySelector('#ar8_data');

  const statusText = norm(
    document.body &&
    document.body.innerText
  ).match(
    /\b(?:Status|Estado)\s+(.{1,80}?)\s+(?:Status reason|Motivo del estado|Razon del estado|Razón del estado)\b/i
  );

  const headerStatus = norm(
    document.body &&
    document.body.innerText
  ).match(
    /\b(?:Status|Estado)\s+(Work In Progress|Trabajo en curso|In Progress|En curso|Assigned|Asignado|Pending|Pendiente|Completed|Completado|Closed|Cerrado|Resolved|Resuelto|Cancelled|Canceled|Cancelado|Open|Abierto|New|Nuevo|Staged|Preparado)\b/i
  );

  return {
    titulo: visible(title)
      ? text(title)
      : '',

    estado:
      field([
        'estado',
        'status',
        'estado de tarea',
        'task status'
      ]) ||
      (headerStatus
        ? headerStatus[1]
        : '') ||
      (statusText
        ? norm(statusText[1])
        : ''),

    parents
  };
}"""


async def abrir_tarea_completa(page, tas):
    await motor.buscar_ticket(page, tas)

    patron = re.compile(r"^(?:Ver tarea completa|View full task)$", re.I)

    deadline = time.monotonic() + 30

    pagina_click = None

    while time.monotonic() < deadline:
        for candidate in list(page.context.pages):
            for frame in candidate.frames:
                for role in ("button", "link"):
                    elements = frame.get_by_role(role, name=patron)

                    for i in range(await elements.count()):
                        element = elements.nth(i)

                        if not await element.is_visible():
                            continue

                        print(
                            f"[TASK_FULL] CLICK TAS={tas} "
                            f"PAGE={candidate.url} "
                            f"FRAME={frame.url}",
                            flush=True,
                        )

                        await element.click(timeout=15000)

                        pagina_click = candidate
                        break

                    if pagina_click:
                        break

                if pagina_click:
                    break

            if pagina_click:
                break

        if pagina_click:
            break

        await asyncio.sleep(0.3)

    if not pagina_click:
        raise RuntimeError(f"VER_TAREA_COMPLETA_NO_ENCONTRADO:{tas}")

    # -----------------------------------------------------
    # IMPORTANTE:
    # El click anterior no significa que SmartIT ya terminó
    # de abrir la vista completa de la tarea.
    # Esperamos explícitamente /taskPV/
    # -----------------------------------------------------

    print(
        f"[TASK_FULL] ESPERANDO_TASKPV TAS={tas}",
        flush=True,
    )

    deadline_task = time.monotonic() + 40

    while time.monotonic() < deadline_task:

        for candidate in list(page.context.pages):

            url = candidate.url or ""

            if "/taskPV/" in url:
                print(
                    f"[TASK_FULL] TASKPV_OK TAS={tas} " f"URL={url}",
                    flush=True,
                )

                # Dar tiempo al formulario PWA/Angular
                # para terminar de montar sus controles.
                await asyncio.sleep(2)

                return candidate

        await asyncio.sleep(0.25)

    urls = [candidate.url for candidate in page.context.pages]

    raise RuntimeError(f"TASKPV_NO_ABIERTO:{tas}:" f"{urls}")


async def leer_tarea_y_abrir_incidente(page, tas, datos):
    page = await abrir_tarea_completa(page, tas)

    deadline = time.monotonic() + 35
    observados = set()

    while time.monotonic() < deadline:
        matches = []

        for candidate in list(page.context.pages):
            for frame in candidate.frames:
                try:
                    payload = await frame.evaluate(LEER_TAREA_JS)

                except Exception:
                    continue

                for campo in ("titulo", "estado", "parents"):
                    if payload.get(campo):
                        observados.add(campo)

                if payload.get("parents"):
                    matches.append((candidate, frame, payload))

        ids = {
            parent["inc"] for _, _, payload in matches for parent in payload["parents"]
        }

        if len(ids) > 1:
            raise RuntimeError(f"INCIDENTE_PADRE_AMBIGUO:{tas}")

        for source_page, frame, payload in matches:

            if not payload.get("titulo") or not payload.get("estado"):
                continue

            parent = payload["parents"][0]
            inc = parent["inc"]

            datos.update(
                titulo=payload["titulo"],
                estado=payload["estado"],
                inc=inc,
            )

            locator = frame.locator(f'[data-helix-parent-inc="{inc}"]:visible').first

            if not await locator.count():
                continue

            print(
                f"[TASK_INC] " f"TAS={tas} " f"INC={inc} " f"FRAME={frame.url}",
                flush=True,
            )

            try:
                await locator.scroll_into_view_if_needed()

            except Exception:
                pass

            # -------------------------------------------------
            # DIAGNOSTICO DEL ELEMENTO INC
            # -------------------------------------------------

            try:
                info = await locator.evaluate("""el => ({
                        tag: el.tagName || '',
                        id: el.id || '',
                        href: el.getAttribute('href') || '',
                        role: el.getAttribute('role') || '',
                        cls:
                            typeof el.className === 'string'
                                ? el.className
                                : '',
                        aria:
                            el.getAttribute('aria-label') || '',
                        text:
                            (
                                el.innerText ||
                                el.textContent ||
                                ''
                            ).trim(),
                        html:
                            (
                                el.outerHTML ||
                                ''
                            ).slice(0, 3000)
                    })""")

                print(
                    f"[TASK_INC] "
                    f"ELEMENTO INC={inc} "
                    f"TAG={info.get('tag')} "
                    f"ID={info.get('id')} "
                    f"HREF={info.get('href')} "
                    f"ROLE={info.get('role')} "
                    f"ARIA={info.get('aria')}",
                    flush=True,
                )

                print(
                    f"[TASK_INC] " f"HTML={info.get('html')}",
                    flush=True,
                )

            except Exception as exc:
                print(
                    f"[TASK_INC] " f"ERROR_INSPECCION INC={inc}: " f"{exc}",
                    flush=True,
                )

            # -------------------------------------------------
            # ESTADO ANTES DEL CLICK
            # -------------------------------------------------

            pages_antes = set(page.context.pages)

            url_antes = source_page.url

            print(
                f"[TASK_INC] "
                f"ANTES_CLICK "
                f"PAGES={len(page.context.pages)} "
                f"URL={url_antes}",
                flush=True,
            )

            # -------------------------------------------------
            # CLICK REAL POR COORDENADAS
            # -------------------------------------------------

            click_hecho = False

            try:
                box = await locator.bounding_box()

                print(
                    f"[TASK_INC] " f"BOX INC={inc}: {box}",
                    flush=True,
                )

                if box:
                    x = box["x"] + box["width"] / 2

                    y = box["y"] + box["height"] / 2

                    await source_page.mouse.click(x, y)

                    click_hecho = True

                    print(
                        f"[TASK_INC] "
                        f"MOUSE_CLICK_OK "
                        f"INC={inc} "
                        f"X={x:.1f} "
                        f"Y={y:.1f}",
                        flush=True,
                    )

            except Exception as exc:
                print(
                    f"[TASK_INC] " f"MOUSE_CLICK_ERROR " f"INC={inc}: {exc}",
                    flush=True,
                )

            # -------------------------------------------------
            # SI EL CLICK FISICO NO SE PUDO HACER,
            # PROBAR PLAYWRIGHT
            # -------------------------------------------------

            if not click_hecho:
                try:
                    await locator.click(timeout=15000)

                    click_hecho = True

                    print(
                        f"[TASK_INC] " f"PLAYWRIGHT_CLICK_OK " f"INC={inc}",
                        flush=True,
                    )

                except Exception as exc:
                    print(
                        f"[TASK_INC] " f"PLAYWRIGHT_CLICK_ERROR " f"INC={inc}: {exc}",
                        flush=True,
                    )

            # -------------------------------------------------
            # ULTIMO INTENTO: EVENTOS JS
            # -------------------------------------------------

            if not click_hecho:
                try:
                    await locator.evaluate("""el => {
                            const target =
                                el.closest(
                                    'a,button,[role="link"]'
                                ) ||
                                el.querySelector(
                                    'a,button,[role="link"]'
                                ) ||
                                el;

                            target.scrollIntoView({
                                block: 'center',
                                inline: 'center'
                            });

                            target.dispatchEvent(
                                new MouseEvent(
                                    'mousedown',
                                    {
                                        bubbles: true,
                                        cancelable: true,
                                        view: window
                                    }
                                )
                            );

                            target.dispatchEvent(
                                new MouseEvent(
                                    'mouseup',
                                    {
                                        bubbles: true,
                                        cancelable: true,
                                        view: window
                                    }
                                )
                            );

                            target.dispatchEvent(
                                new MouseEvent(
                                    'click',
                                    {
                                        bubbles: true,
                                        cancelable: true,
                                        view: window
                                    }
                                )
                            );
                        }""")

                    click_hecho = True

                    print(
                        f"[TASK_INC] " f"JS_CLICK_OK " f"INC={inc}",
                        flush=True,
                    )

                except Exception as exc:
                    print(
                        f"[TASK_INC] " f"JS_CLICK_ERROR " f"INC={inc}: {exc}",
                        flush=True,
                    )

            if not click_hecho:
                raise RuntimeError(f"INC_CLICK_NO_EJECUTADO:{inc}")

            await asyncio.sleep(3)

            print(
                f"[TASK_INC] "
                f"POST_CLICK "
                f"INC={inc} "
                f"PAGES={len(page.context.pages)} "
                f"URL_ANTES={url_antes} "
                f"URL_DESPUES={source_page.url}",
                flush=True,
            )

            # -------------------------------------------------
            # ESPERAR QUE HELIX ABRA EL INCIDENTE
            # -------------------------------------------------

            until = time.monotonic() + 40

            while time.monotonic() < until:
                pages_actuales = list(page.context.pages)

                for target in pages_actuales:
                    for incident_frame in target.frames:

                        try:
                            frame_url = incident_frame.url or ""

                            body = incident_frame.locator("body")

                            if not await body.count():
                                continue

                            texto = await body.inner_text(timeout=1500)

                            texto_norm = re.sub(
                                r"\s+",
                                " ",
                                texto or "",
                            ).strip()

                            inc_visible = inc.upper() in texto_norm.upper()

                            url_incidente = bool(
                                re.search(
                                    r"(targetForm=Incident|Incident|incidents?)",
                                    frame_url,
                                    re.I,
                                )
                            )

                            señales = incident_frame.locator("""
                                    button[testid="ar304428981"],
                                    button:has-text("Ver incidencia completa"),
                                    button:has-text("View full incident"),
                                    [aria-label*="Incident"],
                                    [aria-label*="Incidencia"]
                                    """)

                            tiene_señal = False

                            try:
                                for i in range(await señales.count()):
                                    if await señales.nth(i).is_visible():
                                        tiene_señal = True
                                        break

                            except Exception:
                                pass

                            # ---------------------------------
                            # CASO 1
                            # URL de incidente + INC visible
                            # ---------------------------------

                            if url_incidente and inc_visible:
                                target._atlas_opened_inc = inc

                                print(
                                    f"[TASK_INC] "
                                    f"ABIERTO "
                                    f"INC={inc} "
                                    f"URL={frame_url}",
                                    flush=True,
                                )

                                return target

                            # ---------------------------------
                            # CASO 2
                            # INC visible + controles
                            # de vista de incidente
                            # ---------------------------------

                            if inc_visible and tiene_señal:
                                target._atlas_opened_inc = inc

                                print(
                                    f"[TASK_INC] "
                                    f"ABIERTO "
                                    f"INC={inc} "
                                    f"POR_CONTENIDO "
                                    f"URL={frame_url}",
                                    flush=True,
                                )

                                return target

                        except Exception:
                            continue

                # ---------------------------------------------
                # NUEVAS PAGINAS
                # ---------------------------------------------

                nuevas = [p for p in page.context.pages if p not in pages_antes]

                for nueva in nuevas:
                    try:
                        await nueva.wait_for_load_state(
                            "domcontentloaded",
                            timeout=3000,
                        )

                    except Exception:
                        pass

                await asyncio.sleep(0.35)

            # -------------------------------------------------
            # DIAGNOSTICO SI EL INCIDENTE NO ABRIO
            # -------------------------------------------------

            diagnostico = []

            for target in list(page.context.pages):
                for incident_frame in target.frames:

                    try:
                        body_text = await incident_frame.locator("body").inner_text(
                            timeout=1500
                        )

                        diagnostico.append(
                            {
                                "page_url": target.url,
                                "frame_url": incident_frame.url,
                                "inc_buscado": inc,
                                "contiene_inc": inc.upper() in body_text.upper(),
                                "text": body_text[:3000],
                            }
                        )

                    except Exception as exc:
                        diagnostico.append(
                            {
                                "page_url": target.url,
                                "frame_url": incident_frame.url,
                                "error": str(exc),
                            }
                        )

            archivo_diag = motor.DIAG_ROOT / f"{tas}_inc_enlace.json"

            archivo_diag.write_text(
                json.dumps(
                    diagnostico,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            print(
                f"[TASK_INC] " f"DIAGNOSTICO={archivo_diag}",
                flush=True,
            )

            try:
                await source_page.screenshot(
                    path=str(motor.DIAG_ROOT / f"{tas}_{inc}_click_fallido.png"),
                    full_page=True,
                )

            except Exception:
                pass

            raise RuntimeError(f"INC_ENLACE_NO_ABIERTO:{inc}")

        await asyncio.sleep(0.4)

    faltantes = ", ".join(
        campo for campo in ("titulo", "estado", "parents") if campo not in observados
    )

    raise RuntimeError(
        f"TAREA_DATOS_NO_CONFIRMADOS:{tas}: "
        f"Campos sin confirmar: "
        f"{faltantes or 'datos en una misma vista'}."
    )
