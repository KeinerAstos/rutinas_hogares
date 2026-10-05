import os
import time
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = Path(r"C:\xampp\htdocs\CentralNOC\modules\Dashboard_Hogar\backend\data\pathtrak")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_REGION_CACHE = {}


PATHTRAK_USER = os.getenv("PATHTRAK_USER", "").strip()
PATHTRAK_PASSWORD = os.getenv("PATHTRAK_PASSWORD", "").strip()

PATHTRAK_CENTRO_URL = os.getenv("PATHTRAK_CENTRO_URL", "").strip()

PATHTRAK_REGIONALES_URL = os.getenv("PATHTRAK_REGIONALES_URL", "").strip()

PATHTRAK_TIMEOUT_MS = int(os.getenv("PATHTRAK_TIMEOUT_MS", "60000"))
PATHTRAK_HEADLESS = os.getenv("PATHTRAK_HEADLESS", "false").lower() == "true"

PATHTRAK_PROXY = os.getenv(
    "PATHTRAK_PROXY",
    "",
).strip()


def _normalizar_nodo(nodo):
    return str(nodo or "").strip().upper()


def _normalizar_region(region):
    value = str(region or "").strip().upper()

    if value in {"CENTRO", "REGIONALES"}:
        return value

    return ""


def _cargar_cache_regiones():
    return dict(_REGION_CACHE)


def _guardar_cache_regiones(cache):
    _REGION_CACHE.clear()
    _REGION_CACHE.update(cache)


def obtener_region_cache(nodo):
    nodo_normalizado = _normalizar_nodo(nodo)

    if not nodo_normalizado:
        return ""

    cache = _cargar_cache_regiones()
    return _normalizar_region(cache.get(nodo_normalizado))


def guardar_region_cache(nodo, region):
    nodo_normalizado = _normalizar_nodo(nodo)
    region_normalizada = _normalizar_region(region)

    if not nodo_normalizado or not region_normalizada:
        return False

    cache = _cargar_cache_regiones()
    region_anterior = cache.get(nodo_normalizado)

    cache[nodo_normalizado] = region_normalizada
    _guardar_cache_regiones(cache)

    print(f"[PATHTRAK] RegiÃ³n guardada: " f"{nodo_normalizado} -> {region_normalizada}")

    return region_anterior != region_normalizada


def ordenar_urls_por_region_cache(
    nodo,
    urls,
):
    region_cache = obtener_region_cache(nodo)

    if not region_cache:
        print(
            f"[PATHTRAK] Nodo {_normalizar_nodo(nodo)} "
            "sin regiÃ³n en cachÃ©. "
            "Se mantiene orden CENTRO -> REGIONALES."
        )
        return urls, ""

    ordenadas = sorted(
        urls,
        key=lambda item: (0 if item[0].upper() == region_cache else 1),
    )

    print(
        f"[PATHTRAK] RegiÃ³n preferida para " f"{_normalizar_nodo(nodo)}: {region_cache}"
    )

    return ordenadas, region_cache


def _stamp():
    # Microsegundos evitan colisiones entre jobs concurrentes del mismo nodo.
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _safe_name(texto):
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(texto))


def detectar_modal_rci_error(page):
    """
    Detecta modal de Spectrum:
      Error - No RCI found with the id specified.
    En este caso NO se debe fallar de inmediato: se debe aceptar y esperar.
    """
    try:
        body = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        body = ""

    return "no rci found with the id specified" in body or "no rci found" in body


def aceptar_modal_rci_y_esperar(page, espera_ms=9000):
    """
    Si aparece el modal No RCI found:
    - clic en Aceptar / OK
    - espera a que PathTrak termine de cargar la vista
    """
    if not detectar_modal_rci_error(page):
        return False

    print("[SPECTRUM] Modal 'No RCI found' detectado. Aceptando y esperando carga...")

    selectores = [
        "button:has-text('Aceptar')",
        "button:has-text('OK')",
        "button.swal2-confirm",
        ".swal2-confirm",
        "xpath=//button[contains(normalize-space(.), 'Aceptar')]",
        "xpath=//button[contains(normalize-space(.), 'OK')]",
        "xpath=//button[contains(normalize-space(.), 'Ok')]",
    ]

    clicked = False

    for selector in selectores:
        try:
            btn = page.locator(selector).first
            if btn.count() > 0 and btn.is_visible(timeout=1000):
                btn.click(timeout=4000, force=True)
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        try:
            page.keyboard.press("Enter")
            clicked = True
        except Exception:
            pass

    page.wait_for_timeout(espera_ms)

    # Espera adicional si queda algÃºn loader/cargando visible
    for _ in range(8):
        try:
            body = page.locator("body").inner_text(timeout=1000).lower()
        except Exception:
            body = ""

        if "cargando" not in body and "loading" not in body:
            break

        print("[SPECTRUM] Vista aÃºn cargando despuÃ©s del modal...")
        page.wait_for_timeout(2500)

    return True


def _debug_screenshot(page, nombre):
    path = SCREENSHOT_DIR / f"debug_{_safe_name(nombre)}_{_stamp()}.png"

    try:
        page.bring_to_front()
        page.wait_for_timeout(500)
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        pass

    return str(path)


def detectar_modal_rci_error(page):
    """
    Detecta el modal de Spectrum:
      Error - No RCI found with the id specified.
    Si aparece, NO se debe tomar como captura buena.
    """
    try:
        body = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        body = ""

    mensajes_error = [
        "no rci found with the id specified",
        "rci found",
        "no rci",
    ]

    return any(x in body for x in mensajes_error)


def cerrar_modal_error_si_aparece(page):
    try:
        if not detectar_modal_rci_error(page):
            return False

        print("[SPECTRUM] Modal RCI detectado. Cerrando modal...")

        selectores = [
            "button:has-text('Aceptar')",
            "button.swal2-confirm",
            ".swal2-confirm",
            "xpath=//button[contains(normalize-space(.), 'Aceptar')]",
            "xpath=//button[contains(normalize-space(.), 'OK')]",
        ]

        for selector in selectores:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=800):
                    btn.click(timeout=3000, force=True)
                    page.wait_for_timeout(800)
                    return True
            except Exception:
                continue

        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            return True
        except Exception:
            return True

    except Exception:
        return False


# PATHTRAK_SPECTRUM_CMTS_US_PORT_V4A
def es_url_spectrum(url):
    url = (url or "").lower()

    return (
        "/pathtrak/live/" in url
        or "#/app/spectrum" in url
        or "spectrum" in url
        or "hcu=" in url
        or "cmts_us_port" in url
    )


def login_pathtrak(page, url):
    print(f"[LOGIN] Abriendo: {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS)

    # Si ya estÃ¡ logueado, puede que aparezca directo el buscador.
    try:
        page.wait_for_selector("#txtSeachBox", timeout=8000)
        print("[LOGIN] SesiÃ³n reutilizada y validada.")
        return "reutilizada"
    except PlaywrightTimeoutError:
        pass

    page.wait_for_selector("#name", timeout=PATHTRAK_TIMEOUT_MS)
    page.fill("#name", PATHTRAK_USER)
    page.fill("#pass", PATHTRAK_PASSWORD)

    page.locator("button[type='submit']").click()

    try:
        page.wait_for_load_state("networkidle", timeout=PATHTRAK_TIMEOUT_MS)
    except Exception:
        pass

    page.wait_for_selector("#txtSeachBox", timeout=PATHTRAK_TIMEOUT_MS)
    print("[LOGIN] Login nuevo correcto.")
    return "nueva"


def aceptar_modal_si_aparece(page):
    """
    Cierra modal de PathTrak tipo SweetAlert:
    'La vista de mapa se deshabilitarÃ¡...'
    """

    page.wait_for_timeout(1000)

    selectores = [
        "button.swal2-confirm",
        ".swal2-confirm",
        "button.swal2-styled",
        "button:has-text('Aceptar')",
        "xpath=//button[contains(normalize-space(.), 'Aceptar')]",
        "xpath=//button[contains(@class, 'swal2-confirm')]",
    ]

    for _ in range(6):
        for selector in selectores:
            try:
                btn = page.locator(selector).first

                if btn.count() > 0 and btn.is_visible(timeout=800):
                    try:
                        btn.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass

                    btn.click(timeout=4000, force=True)
                    page.wait_for_timeout(1200)
                    print("[MODAL] Modal aceptado.")
                    return True

            except Exception:
                continue

        try:
            clicked = page.evaluate("""
                () => {
                    const btn =
                        document.querySelector('button.swal2-confirm') ||
                        document.querySelector('.swal2-confirm') ||
                        Array.from(document.querySelectorAll('button'))
                          .find(b => (b.innerText || '').trim().toLowerCase().includes('aceptar'));

                    if (!btn) return false;

                    btn.click();
                    return true;
                }
            """)

            if clicked:
                page.wait_for_timeout(1200)
                print("[MODAL] Modal aceptado por JS.")
                return True

        except Exception:
            pass

        try:
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
        except Exception:
            pass

        page.wait_for_timeout(700)

    print("[MODAL] No habÃ­a modal visible o no se pudo confirmar cierre.")
    return False


def esperar_nueva_pagina_o_actual(context, page, paginas_antes, timeout_ms=25000):
    """
    Para abrir la vista QoE del nodo.
    AquÃ­ sÃ­ se permite retornar la misma pÃ¡gina si la URL cambia a node/health.
    """

    inicio = time.time()

    while (time.time() - inicio) * 1000 < timeout_ms:
        if len(context.pages) > paginas_antes:
            nueva = context.pages[-1]
            nueva.bring_to_front()

            try:
                nueva.wait_for_load_state(
                    "domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS
                )
            except Exception:
                pass

            nueva.wait_for_timeout(1800)
            return nueva

        try:
            href = (page.url or "").lower()
            body = page.locator("body").inner_text(timeout=1000).lower()

            if (
                "node/health" in href
                or "resumen de nodo" in body
                or "tipos de problemas" in body
                or "lista de prioridad" in body
            ):
                return page
        except Exception:
            pass

        page.wait_for_timeout(500)

    return page


def buscar_nodo_y_abrir(context, page, nodo):
    """
    Abre el nodo usando el href real del dropdown.

    PathTrak muestra resultados asÃ­:
      <a target="_blank" href="/pathtrak/xpt/analysis/node/1136539">
        NODO 6601 (SEG A)
      </a>

    En vez de hacer click por coordenadas, tomamos el primer link /node/
    que contenga NODO <nodo> y abrimos esa URL directamente en una nueva pÃ¡gina.
    """
    nodo = str(nodo).strip()
    nodo_u = nodo.upper()

    search = page.locator("#txtSeachBox")
    search.wait_for(timeout=PATHTRAK_TIMEOUT_MS)

    search.click()
    search.fill("")
    page.wait_for_timeout(300)
    search.fill(nodo)

    page.wait_for_timeout(2500)

    dropdown_xpath = "/html/body/app-root/div/div/app-dashboard/div/div/div[1]/div/search/div/div/div"

    try:
        dropdown = page.locator(f"xpath={dropdown_xpath}")
        dropdown.wait_for(timeout=15000)

        texto_dropdown = dropdown.inner_text(timeout=5000).strip()
        texto_upper = texto_dropdown.upper()

        print("=" * 90)
        print("[BUSQUEDA] TEXTO DEL DROPDOWN")
        print("=" * 90)
        print(texto_dropdown)
        print("=" * 90)

        try:
            debug_search = (
                SCREENSHOT_DIR / f"debug_search_{_safe_name(nodo)}_{_stamp()}.png"
            )
            page.screenshot(path=str(debug_search), full_page=False)
            print(f"[BUSQUEDA] Screenshot bÃºsqueda: {debug_search}")
        except Exception as e:
            print(f"[BUSQUEDA] No pude guardar screenshot de bÃºsqueda: {e}")

        if "NO SE ENCONTRARON DATOS" in texto_upper:
            raise RuntimeError(f"No se encontraron datos para el nodo {nodo}.")

        if f"NODO {nodo_u}" not in texto_upper and nodo_u not in texto_upper:
            raise RuntimeError(
                f"La lista apareciÃ³, pero no contiene el nodo {nodo}. "
                f"Texto: {texto_dropdown}"
            )

        link_info = page.evaluate(
            """(nodo) => {
            const n = String(nodo || '').trim().toUpperCase();

            const anchors = Array.from(document.querySelectorAll(
                "ul.custome-autocomplete-ul a[href*='/pathtrak/xpt/analysis/node/'], " +
                "a[href*='/pathtrak/xpt/analysis/node/']"
            ));

            const rows = anchors.map((a, idx) => {
                const text = String(a.innerText || a.textContent || '').trim();
                return {
                    idx,
                    text,
                    textUpper: text.toUpperCase(),
                    href: a.href || a.getAttribute('href') || ''
                };
            });

            const exact = rows.find(r =>
                r.textUpper.includes(`NODO ${n}`) &&
                r.href.includes('/pathtrak/xpt/analysis/node/')
            );

            const fallback = rows.find(r =>
                r.textUpper.includes('NODO') &&
                r.textUpper.includes(n) &&
                r.href.includes('/pathtrak/xpt/analysis/node/')
            );

            return {
                selected: exact || fallback || null,
                rows: rows.slice(0, 8)
            };
        }""",
            nodo,
        )

        print(f"[BUSQUEDA] Links nodo detectados: {link_info}")

        selected = (link_info or {}).get("selected")

        if not selected or not selected.get("href"):
            raise RuntimeError(
                f"El dropdown contiene NODO {nodo}, pero no encontrÃ© href /xpt/analysis/node/. "
                f"Links detectados: {link_info}"
            )

        href = selected["href"]
        texto = selected.get("text", "")

        print(f"[BUSQUEDA] Abriendo primer link real de nodo: {texto} -> {href}")

        node_page = context.new_page()
        node_page.goto(href, wait_until="domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS)

        try:
            node_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        node_page.wait_for_timeout(2500)
        node_page.bring_to_front()

        print(f"[BUSQUEDA] PÃ¡gina real de nodo abierta: {node_page.url}")

        # Modal de licencia / InformaciÃ³n.
        try:
            aceptar_modal_si_aparece(node_page)
        except Exception as e:
            print(
                f"[BUSQUEDA] No pude cerrar modal inicial con aceptar_modal_si_aparece: {e}"
            )

        try:
            aceptar_cualquier_modal_pathtrak(node_page, motivo=f"nodo {nodo}")
        except Exception as e:
            print(
                f"[BUSQUEDA] No pude cerrar modal inicial con aceptar_cualquier_modal_pathtrak: {e}"
            )

        # PathTrak redirige el enlace /xpt/analysis/node/ hacia la vista
        # real /live/index.html#/app/ea?port=...
        #
        # No usamos locator("body").inner_text(timeout=1500), porque el DOM
        # de PathTrak es grande y ese mÃ©todo puede vencer aunque la pantalla
        # ya estÃ© completamente visible.
        deadline = time.monotonic() + 25
        intento = 0

        while time.monotonic() < deadline:
            intento += 1

            try:
                current_url = node_page.url or ""
            except Exception:
                current_url = ""

            try:
                estado = node_page.evaluate("""() => {
                    const texto = (
                        document.body?.innerText || ""
                    ).toLowerCase();

                    const selectores = [
                        ".ui-grid",
                        ".ui-grid-viewport",
                        ".highcharts-container",
                        "[class*='priority']",
                        "[class*='alarm']",
                        "[class*='node']"
                    ];

                    const selectorVisible = selectores.some(selector => {
                        const elementos = Array.from(
                            document.querySelectorAll(selector)
                        );

                        return elementos.some(elemento => {
                            const estilo = window.getComputedStyle(elemento);
                            const rect = elemento.getBoundingClientRect();

                            return (
                                estilo.display !== "none" &&
                                estilo.visibility !== "hidden" &&
                                rect.width > 0 &&
                                rect.height > 0
                            );
                        });
                    });

                    const textosVista = [
                        "estado del nodo",
                        "lista de prioridad",
                        "lista de alarmas",
                        "resumen de elementos",
                        "resumen de nodo",
                        "tipos de problemas",
                        "puntuaciÃ³n",
                        "afectado",
                        "enfatizado"
                    ];

                    return {
                        textoVista: textosVista.some(
                            valor => texto.includes(valor)
                        ),
                        selectorVisible,
                        cargando: (
                            texto.includes("cargando...") ||
                            texto.includes("loading") ||
                            texto.includes("por favor espere")
                        ),
                        textoInicial: texto.slice(0, 800)
                    };
                }""")
            except Exception as exc:
                estado = {
                    "textoVista": False,
                    "selectorVisible": False,
                    "cargando": False,
                    "error": str(exc),
                    "textoInicial": "",
                }

            url_live = (
                "/pathtrak/live/index.html" in current_url.lower()
                and "#/app/ea" in current_url.lower()
                and "port=" in current_url.lower()
            )

            vista_detectada = bool(
                estado.get("textoVista") or estado.get("selectorVisible")
            )

            print(
                f"[BUSQUEDA] ValidaciÃ³n vista intento={intento} | "
                f"url_live={url_live} | "
                f"vista={vista_detectada} | "
                f"cargando={estado.get('cargando', False)}"
            )

            # La URL live con port identifica inequÃ­vocamente la vista real
            # del nodo. No esperamos a que todos los paneles terminen.
            if url_live and vista_detectada:
                print(
                    "[BUSQUEDA] Vista real del nodo confirmada "
                    "por URL y contenido visible."
                )
                return node_page

            # PathTrak puede dejar un panel interno mostrando Cargando,
            # mientras el resto de la vista ya es totalmente utilizable.
            if url_live and intento >= 4:
                print(
                    "[BUSQUEDA] URL live del nodo confirmada. "
                    "ContinÃºo sin esperar todos los widgets."
                )
                return node_page

            # Revisamos modal solo ocasionalmente, no en cada iteraciÃ³n.
            if intento in {1, 4, 8}:
                try:
                    aceptar_modal_si_aparece(node_page)
                except Exception:
                    pass

            node_page.wait_for_timeout(1000)

        debug_node = None

        try:
            debug_node = (
                SCREENSHOT_DIR / f"debug_node_real_no_listo_"
                f"{_safe_name(nodo)}_{_stamp()}.png"
            )

            node_page.screenshot(
                path=str(debug_node),
                full_page=False,
            )

            print(f"[BUSQUEDA] Debug nodo real no listo: " f"{debug_node}")
        except Exception:
            pass

        current_url = node_page.url or ""

        if (
            "/pathtrak/live/index.html" in current_url.lower()
            and "#/app/ea" in current_url.lower()
            and "port=" in current_url.lower()
        ):
            print(
                "[BUSQUEDA] La URL live estÃ¡ abierta. " "ContinÃºo con la captura QoE."
            )
            return node_page

        raise RuntimeError(
            f"AbrÃ­ el nodo {nodo}, pero no apareciÃ³ la vista live. "
            f"URL: {current_url}. Debug: {debug_node}"
        )

    except Exception as e:
        raise RuntimeError(f"No pude seleccionar el nodo {nodo}: {e}")


def esperar_pagina_spectrum(context, timeout_ms=90000):
    """
    Para ONDAS.
    Solo acepta pÃ¡ginas cuya URL sea la real de spectrum/live.
    NO devuelve la pÃ¡gina QoE.
    """

    inicio = time.time()

    while (time.time() - inicio) * 1000 < timeout_ms:
        for p in reversed(list(context.pages)):
            try:
                url = p.url or ""

                if es_url_spectrum(url):
                    print(f"[SPECTRUM] PÃ¡gina real detectada: {url}")

                    p.bring_to_front()

                    try:
                        p.wait_for_load_state(
                            "domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS
                        )
                    except Exception:
                        pass

                    p.wait_for_timeout(2000)
                    return p

            except Exception:
                pass

        time.sleep(0.5)

    urls = []

    for p in context.pages:
        try:
            urls.append(p.url)
        except Exception:
            pass

    raise RuntimeError(
        "Se hizo click en ondas, pero no encontrÃ© la pestaÃ±a real de spectrum. "
        f"PÃ¡ginas abiertas: {urls}"
    )


def click_icono_spectrum_y_esperar(context, page, icon_locator, descripcion):
    """
    Hace click como usuario real sobre el centro del icono.
    Esta fue la forma mÃ¡s cercana a lo que haces manualmente.
    """

    print(f"[SPECTRUM] Intentando click real en: {descripcion}")

    page.bring_to_front()

    try:
        icon_locator.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass

    page.wait_for_timeout(800)

    box = icon_locator.bounding_box()

    if not box:
        raise RuntimeError(f"No pude obtener coordenadas del icono {descripcion}.")

    x = box["x"] + (box["width"] / 2)
    y = box["y"] + (box["height"] / 2)

    page.mouse.move(x, y)
    page.wait_for_timeout(300)

    # El popup puede abrir como pestaÃ±a nueva, pero a veces Playwright no lo captura.
    # Por eso hacemos click y luego buscamos cualquier pestaÃ±a que tenga /live/ o spectrum.
    try:
        with context.expect_page(timeout=12000) as nueva_info:
            page.mouse.click(x, y)

        nueva = nueva_info.value
        nueva.bring_to_front()

        try:
            nueva.wait_for_load_state("domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS)
        except Exception:
            pass

        nueva.wait_for_timeout(5000)

    except Exception:
        # Aunque no detecte popup, el click ya pudo abrir la pestaÃ±a.
        print("[SPECTRUM] No se capturÃ³ popup directo; buscarÃ© la pestaÃ±a por URL.")
        try:
            page.mouse.click(x, y)
        except Exception:
            pass

    return esperar_pagina_spectrum(context, timeout_ms=90000)


def abrir_analizador_espectro(context, page):
    """
    Abre la pestaÃ±a real de ondas/spectrum.

    NO usa fa-external-link.
    Usa Ãºnicamente:
    - MENU_Spectrum_Analyzer
    - MENU_CMTS_SPECTRUM_ANALYZER
    - i.fa-line-chart con title Analizador de espectro
    """

    page.bring_to_front()

    try:
        page.wait_for_load_state("domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS)
    except Exception:
        pass

    page.wait_for_timeout(3000)

    aceptar_modal_si_aparece(page)
    page.wait_for_timeout(2500)

    print("[SPECTRUM] Esperando carga de vista QoE del nodo...")

    try:
        page.wait_for_function(
            """() => {
                const text = document.body.innerText.toLowerCase();
                return text.includes('resumen de nodo') ||
                       text.includes('tipos de problemas') ||
                       text.includes('lista de prioridad') ||
                       text.includes('utilizaciÃ³n de datos');
            }""",
            timeout=PATHTRAK_TIMEOUT_MS,
        )
    except Exception:
        pass

    # Si ya existe una pestaÃ±a spectrum abierta, usarla.
    for p in reversed(list(context.pages)):
        try:
            if es_url_spectrum(p.url):
                print("[SPECTRUM] Ya habÃ­a una pestaÃ±a spectrum abierta.")
                p.bring_to_front()
                return p
        except Exception:
            pass

    print("[SPECTRUM] Buscando icono real de Analizador de espectro...")

    icon_selectors = [
        "i#MENU_Spectrum_Analyzer",
        "#MENU_Spectrum_Analyzer",
        "i#MENU_CMTS_SPECTRUM_ANALYZER",
        "#MENU_CMTS_SPECTRUM_ANALYZER",
        "i.fa-line-chart[title*='Analizador de espectro']",
        "xpath=//i[contains(@class,'fa-line-chart') and contains(@title,'Analizador de espectro')]",
    ]

    for intento in range(1, 12):
        print(f"[SPECTRUM] Intento de bÃºsqueda de icono #{intento}")

        for selector in icon_selectors:
            try:
                icon = page.locator(selector).first

                if icon.count() == 0:
                    continue

                try:
                    icon.wait_for(state="visible", timeout=3000)
                except Exception:
                    continue

                print(f"[SPECTRUM] Icono encontrado con selector: {selector}")

                try:
                    icon.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass

                page.wait_for_timeout(700)

                box = icon.bounding_box()

                if not box:
                    print(
                        "[SPECTRUM] El icono existe, pero no pude obtener coordenadas."
                    )
                    continue

                x = box["x"] + (box["width"] / 2)
                y = box["y"] + (box["height"] / 2)

                print(f"[SPECTRUM] Click fÃ­sico en coordenadas: x={x}, y={y}")

                page.mouse.move(x, y)
                page.wait_for_timeout(300)

                try:
                    with context.expect_page(timeout=12000) as nueva_info:
                        page.mouse.click(x, y)

                    nueva = nueva_info.value
                    nueva.bring_to_front()

                    try:
                        nueva.wait_for_load_state(
                            "domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS
                        )
                    except Exception:
                        pass

                    nueva.wait_for_timeout(5000)

                    return esperar_pagina_spectrum(context, timeout_ms=90000)

                except Exception:
                    print(
                        "[SPECTRUM] No se detectÃ³ popup directo; buscarÃ© pestaÃ±a por URL."
                    )

                    try:
                        page.mouse.click(x, y)
                    except Exception:
                        pass

                    return esperar_pagina_spectrum(context, timeout_ms=90000)

            except Exception as e:
                print(f"[SPECTRUM] FallÃ³ selector {selector}: {e}")
                continue

        page.wait_for_timeout(2500)

    # Fallback por menÃº de tres puntos, sin tocar external-link.
    print("[SPECTRUM] No encontrÃ© icono visible. Intentando por menÃº de tres puntos...")

    try:
        menu_selectors = [
            ".options-icon button.fa-ellipsis-v",
            "button.fa-ellipsis-v",
            "xpath=//span[contains(@class,'options-icon')]//button[contains(@class,'fa-ellipsis-v')]",
        ]

        menu_btn = None

        for selector in menu_selectors:
            try:
                loc = page.locator(selector).first

                if loc.count() > 0:
                    loc.wait_for(state="visible", timeout=5000)
                    menu_btn = loc
                    print(f"[SPECTRUM] MenÃº encontrado con selector: {selector}")
                    break
            except Exception:
                continue

        if menu_btn is not None:
            menu_btn.click(timeout=8000, force=True)
            page.wait_for_timeout(1500)

            opciones = [
                "button.dropdown-item:has-text('Analizador de espectro de')",
                "button.dropdown-item:has-text('Analizador de espectro')",
                "xpath=//button[contains(@class,'dropdown-item') and contains(normalize-space(.),'Analizador de espectro')]",
            ]

            for selector in opciones:
                try:
                    opcion = page.locator(selector).first

                    if opcion.count() == 0:
                        continue

                    opcion.wait_for(state="visible", timeout=5000)
                    print(f"[SPECTRUM] OpciÃ³n del menÃº encontrada: {selector}")

                    try:
                        with context.expect_page(timeout=12000) as nueva_info:
                            opcion.click(timeout=10000, force=True)

                        nueva = nueva_info.value
                        nueva.bring_to_front()

                        try:
                            nueva.wait_for_load_state(
                                "domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS
                            )
                        except Exception:
                            pass

                        nueva.wait_for_timeout(5000)

                    except Exception:
                        opcion.click(timeout=10000, force=True)

                    return esperar_pagina_spectrum(context, timeout_ms=90000)

                except Exception as e:
                    print(f"[SPECTRUM] FallÃ³ opciÃ³n {selector}: {e}")
                    continue

    except Exception as e:
        print(f"[SPECTRUM] FallÃ³ menÃº de tres puntos: {e}")

    raise RuntimeError(
        "No pude abrir el Analizador de espectro. "
        "No se encontrÃ³ el icono real ni la opciÃ³n del menÃº."
    )


def detectar_modal_pnm_sin_licencia(page):
    """
    Detecta el modal de PathTrak que indica que el nodo PNM
    seleccionado no tiene licencia para Spectrum.
    """
    try:
        body = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        body = ""

    textos = [
        "el nodo pnm seleccionado no tiene licencia",
        "nodo pnm seleccionado no tiene licencia",
        "no tiene licencia",
        "comunÃ­quese con su administrador",
        "comuniquese con su administrador",
    ]

    return any(texto in body for texto in textos)


def esperar_grafica_espectro(page):
    """
    Espera la grÃ¡fica real de ondas.

    Si aparece:
      No RCI found with the id specified.
    entonces NO hay grÃ¡fica de ondas para ese nodo/RCI.
    En ese caso se guarda debug y se falla rÃ¡pido, sin esperar 90 segundos.
    """

    page.bring_to_front()

    try:
        page.wait_for_load_state("domcontentloaded", timeout=PATHTRAK_TIMEOUT_MS)
    except Exception:
        pass

    print("[SPECTRUM] Esperando URL real de spectrum...")

    page.wait_for_function(
        """() => {
            const href = window.location.href.toLowerCase();
            return href.includes('/pathtrak/live/') ||
                   href.includes('#/app/spectrum') ||
                   href.includes('spectrum') ||
                   href.includes('hcu=') ||
                   href.includes('cmts_us_port');
        }""",
        timeout=PATHTRAK_TIMEOUT_MS,
    )

    print("[SPECTRUM] URL correcta. Validando grÃ¡fica o error RCI...")

    page.wait_for_timeout(2000)

    for i in range(90):
        # Caso importante: nodo PNM sin licencia.
        if detectar_modal_pnm_sin_licencia(page):
            debug_licencia = _debug_screenshot(page, "ondas_pnm_sin_licencia")

            print(
                "[SPECTRUM] Nodo PNM sin licencia detectado. "
                f"Debug: {debug_licencia}"
            )

            try:
                aceptar_cualquier_modal_pathtrak(page, motivo="pnm sin licencia")
            except Exception:
                pass

            raise RuntimeError(
                "PATHTRAK_PNM_SIN_LICENCIA: "
                "El nodo PNM seleccionado no tiene licencia. "
                "PathTrak no permite generar la grÃ¡fica de ondas "
                f"para este nodo. Debug: {debug_licencia}"
            )

        # Caso importante: No RCI => no habrÃ¡ grÃ¡fica de ondas.
        if detectar_modal_rci_error(page):
            debug_rci = _debug_screenshot(page, "ondas_no_rci_found")
            print(f"[SPECTRUM] No RCI found detectado. Debug: {debug_rci}")

            try:
                aceptar_modal_si_aparece(page)
            except Exception:
                pass

            try:
                aceptar_cualquier_modal_pathtrak(page, motivo="ondas no rci")
            except Exception:
                pass

            raise RuntimeError(
                "PathTrak abriÃ³ el Analizador de espectro, pero respondiÃ³: "
                "'No RCI found with the id specified'. "
                "Para este nodo no hay grÃ¡fica de ondas disponible en PathTrak. "
                f"Debug: {debug_rci}"
            )

        try:
            loading_count = page.locator(".highcharts-loading").count()
            loading_visible = False
            if loading_count > 0:
                loading_visible = page.locator(".highcharts-loading").first.is_visible(
                    timeout=500
                )
        except Exception:
            loading_visible = False

        grafica_visible = False

        for selector in [
            ".highcharts-container",
            ".highcharts-root",
            "svg",
            "canvas",
            "text=Analizador de espectro",
            "text=Principal",
            "text=dBmV",
            "text=Frecuencia",
        ]:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    grafica_visible = True
                    break
            except Exception:
                continue

        print(
            f"[SPECTRUM] Espera {i}s | "
            f"grafica={grafica_visible} | loading={loading_visible}"
        )

        if grafica_visible and not loading_visible:
            page.wait_for_timeout(3500)

            # Revalidar que no saliÃ³ RCI al final.
            if detectar_modal_rci_error(page):
                debug_rci = _debug_screenshot(page, "ondas_no_rci_found_final")
                raise RuntimeError(
                    "PathTrak mostrÃ³ No RCI found al final de la carga de ondas. "
                    f"Debug: {debug_rci}"
                )

            print("[SPECTRUM] GrÃ¡fica de ondas lista.")
            return True

        page.wait_for_timeout(1000)

    debug = _debug_screenshot(page, "ondas_grafica_no_cargo")
    raise RuntimeError(
        f"La grÃ¡fica de ondas no terminÃ³ de cargar. URL: {page.url}. Debug: {debug}"
    )


def aceptar_cualquier_modal_pathtrak(
    page,
    motivo="",
):
    """
    Cierra realmente avisos modales de PathTrak.

    Usa tres mecanismos:
      1. BotÃ³n SweetAlert.
      2. BotÃ³n visible con texto Aceptar.
      3. Clic JavaScript directo.

    Solo devuelve True cuando el modal desapareciÃ³.
    """
    try:
        page.bring_to_front()
    except Exception:
        pass

    detectado = False

    for intento in range(1, 8):
        try:
            informacion = page.evaluate(r"""() => {
                    const visible = el => {
                        if (!el) return false;

                        const style =
                            window.getComputedStyle(el);

                        const rect =
                            el.getBoundingClientRect();

                        return (
                            style.display !== "none" &&
                            style.visibility !== "hidden" &&
                            rect.width > 0 &&
                            rect.height > 0
                        );
                    };

                    const modales = Array.from(
                        document.querySelectorAll(
                            ".swal2-container, " +
                            ".swal2-popup, " +
                            ".modal, " +
                            "[role='dialog']"
                        )
                    ).filter(visible);

                    const botones = Array.from(
                        document.querySelectorAll("button")
                    ).filter(el => {
                        if (!visible(el)) return false;

                        const texto = String(
                            el.innerText ||
                            el.textContent ||
                            ""
                        )
                        .replace(/\\s+/g, " ")
                        .trim()
                        .toLowerCase();

                        return texto === "aceptar";
                    });

                    return {
                        modalVisible: modales.length > 0,
                        botonesAceptar: botones.length,
                        texto: String(
                            document.body?.innerText || ""
                        ).slice(0, 3000)
                    };
                }""")

        except Exception:
            informacion = {
                "modalVisible": False,
                "botonesAceptar": 0,
                "texto": "",
            }

        modal_visible = bool(informacion.get("modalVisible"))

        botones_aceptar = int(informacion.get("botonesAceptar") or 0)

        texto_pagina = str(informacion.get("texto") or "").lower()

        aviso_licencia = "licencia sus" in texto_pagina or "ha caducado" in texto_pagina

        if not modal_visible and not aviso_licencia:
            if detectado:
                print(f"[MODAL] Modal cerrado correctamente " f"{motivo}.")
                return True

            return False

        detectado = True

        print(
            f"[MODAL] Intento {intento} | "
            f"modal={modal_visible} | "
            f"botonesAceptar={botones_aceptar} | "
            f"motivo={motivo}"
        )

        # Ruta 1: selector SweetAlert.
        try:
            boton = page.locator(
                "button.swal2-confirm, "
                ".swal2-actions button, "
                "button:has-text('Aceptar')"
            )

            for indice in range(boton.count()):
                candidato = boton.nth(indice)

                if candidato.is_visible(timeout=500):
                    candidato.click(
                        force=True,
                        timeout=5000,
                    )

                    page.wait_for_timeout(1200)
                    break

        except Exception as exc:
            print(f"[MODAL] Clic Playwright no funcionÃ³: {exc}")

        # Ruta 2: clic JavaScript directo.
        try:
            click_js = page.evaluate("""() => {
                    const visible = el => {
                        if (!el) return false;

                        const style =
                            window.getComputedStyle(el);

                        const rect =
                            el.getBoundingClientRect();

                        return (
                            style.display !== "none" &&
                            style.visibility !== "hidden" &&
                            rect.width > 0 &&
                            rect.height > 0
                        );
                    };

                    const botones = Array.from(
                        document.querySelectorAll("button")
                    );

                    const aceptar = botones.find(el => {
                        const texto = String(
                            el.innerText ||
                            el.textContent ||
                            ""
                        )
                        .replace(/\\s+/g, " ")
                        .trim()
                        .toLowerCase();

                        return (
                            visible(el) &&
                            texto === "aceptar"
                        );
                    });

                    if (!aceptar) {
                        return false;
                    }

                    aceptar.dispatchEvent(
                        new MouseEvent(
                            "mousedown",
                            {
                                bubbles: true,
                                cancelable: true,
                                view: window
                            }
                        )
                    );

                    aceptar.dispatchEvent(
                        new MouseEvent(
                            "mouseup",
                            {
                                bubbles: true,
                                cancelable: true,
                                view: window
                            }
                        )
                    );

                    aceptar.click();

                    return true;
                }""")

            if click_js:
                print("[MODAL] Clic JavaScript ejecutado.")

        except Exception as exc:
            print(f"[MODAL] Clic JavaScript fallÃ³: {exc}")

        page.wait_for_timeout(1200)

    debug = _debug_screenshot(
        page,
        f"modal_no_cerrado_{motivo}",
    )

    raise RuntimeError(
        "PathTrak mostrÃ³ el aviso de licencia, pero el botÃ³n "
        f"Aceptar no logrÃ³ cerrarlo. Debug: {debug}"
    )


def detectar_tipo_vista_pathtrak(page):
    """
    Clasifica el contenido visible de la pÃ¡gina actual.

    Valores:
      - qoe
      - spectrum
      - desconocida
    """
    try:
        estado = page.evaluate("""() => {
            const texto = (
                document.body?.innerText || ""
            ).toLowerCase();

            const indicadoresQoe = [
                "resumen del nodo",
                "resumen de nodo",
                "tipos de problemas",
                "utilizaciÃ³n de datos",
                "utilizacion de datos",
                "lista de mÃ³dems",
                "lista de modems",
                "estado del nodo",
                "lista de prioridad"
            ];

            const indicadoresSpectrum = [
                "resumen de espectro",
                "ascendente",
                "difusiÃ³n",
                "difusion",
                "frecuencia (mhz)",
                "nivel (dbmv)",
                "id de alarma"
            ];

            return {
                qoe: indicadoresQoe.some(
                    valor => texto.includes(valor)
                ),
                spectrum: indicadoresSpectrum.some(
                    valor => texto.includes(valor)
                ),
                textoInicial: texto.slice(0, 1500),
                url: window.location.href
            };
        }""")

    except Exception as exc:
        return {
            "tipo": "desconocida",
            "error": str(exc),
            "url": page.url or "",
            "textoInicial": "",
        }

    if estado.get("spectrum"):
        tipo = "spectrum"
    elif estado.get("qoe"):
        tipo = "qoe"
    else:
        tipo = "desconocida"

    return {
        "tipo": tipo,
        "url": estado.get("url") or page.url or "",
        "textoInicial": estado.get("textoInicial", ""),
    }


def esperar_vista_qoe_estable(
    page,
    nodo,
    timeout_ms=60000,
):
    """
    Espera Ãºnicamente una vista QoE real.

    Una URL /live/index.html#/app/ea?port= no demuestra QoE:
    esa ruta puede corresponder a Espectro/EA.
    """
    print(f"[QOE] Validando contenido real para nodo {nodo}...")

    try:
        page.bring_to_front()
    except Exception:
        pass

    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=15000,
        )
    except Exception:
        pass

    deadline = time.monotonic() + max(10, timeout_ms / 1000)

    intento = 0
    ultimo_estado = {
        "tipo": "desconocida",
        "url": page.url or "",
    }

    while time.monotonic() < deadline:
        intento += 1

        ultimo_estado = detectar_tipo_vista_pathtrak(page)

        print(
            f"[QOE] intento={intento} | "
            f"tipo={ultimo_estado.get('tipo')} | "
            f"url={ultimo_estado.get('url')}"
        )

        if ultimo_estado.get("tipo") == "qoe":
            print("[QOE] Vista QoE real confirmada.")

            page.wait_for_timeout(1500)
            return True

        if ultimo_estado.get("tipo") == "spectrum":
            debug = None

            try:
                debug = _debug_screenshot(
                    page,
                    f"qoe_recibio_spectrum_{nodo}",
                )
            except Exception:
                pass

            raise RuntimeError(
                f"Se solicitÃ³ QoE para el nodo {nodo}, "
                "pero PathTrak abriÃ³ la vista de Espectro/EA. "
                f"URL: {ultimo_estado.get('url')}. "
                f"Debug: {debug}"
            )

        if intento in {1, 5}:
            try:
                aceptar_cualquier_modal_pathtrak(
                    page,
                    motivo=f"validando QoE nodo {nodo}",
                )
            except Exception:
                pass

        page.wait_for_timeout(1000)

    debug = None

    try:
        debug = _debug_screenshot(
            page,
            f"qoe_no_confirmado_{nodo}",
        )
    except Exception:
        pass

    raise NodoEncontradoPeroVistaNoCarga(
        f"El nodo {nodo} abriÃ³, pero no apareciÃ³ "
        f"una vista QoE vÃ¡lida dentro de {timeout_ms} ms. "
        f"Ãšltimo estado: {ultimo_estado}. "
        f"Debug: {debug}"
    )


def abrir_nodo_desde_dropdown_real(page, nodo, intento=""):
    """
    Selecciona la PRIMERA opciÃ³n real del dropdown:
      NODO 6601 (SEG A)

    No intenta recorrer candidatos ocultos. Primero usa teclado y luego click
    por coordenada sobre la primera fila visible.
    """
    print(
        f"[BUSQUEDA] Seleccionando primera opciÃ³n del dropdown para nodo {nodo} {intento}..."
    )

    nodo = str(nodo).strip()

    try:
        page.bring_to_front()
    except Exception:
        pass

    # 1) Ubicar input superior del buscador Nodo/MÃ³dem.
    box = page.evaluate("""() => {
        const visible = (el) => {
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 &&
                   st.display !== 'none' &&
                   st.visibility !== 'hidden';
        };

        const inputs = Array.from(document.querySelectorAll('input'))
            .filter(visible)
            .map((el) => {
                const r = el.getBoundingClientRect();
                return {
                    x: r.x,
                    y: r.y,
                    w: r.width,
                    h: r.height,
                    value: el.value || '',
                    placeholder: el.placeholder || ''
                };
            })
            .filter(x => x.y < 230 && x.w > 250)
            .sort((a, b) => b.w - a.w);

        return inputs[0] || null;
    }""")

    if not box:
        raise RuntimeError("No encontrÃ© el input superior del buscador Nodo/MÃ³dem.")

    input_x = box["x"] + 30
    input_y = box["y"] + box["h"] / 2

    print(
        f"[BUSQUEDA] Input buscador: x={round(input_x)} y={round(input_y)} w={round(box['w'])} h={round(box['h'])}"
    )

    # 2) Escribir nodo limpio.
    page.mouse.click(input_x, input_y)
    page.wait_for_timeout(600)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(300)
    page.keyboard.type(nodo, delay=40)
    page.wait_for_timeout(2500)

    try:
        debug = (
            SCREENSHOT_DIR
            / f"debug_dropdown_antes_click_primera_{_safe_name(nodo)}_{_stamp()}.png"
        )
        page.screenshot(path=str(debug), full_page=False)
        print(f"[BUSQUEDA] Screenshot dropdown antes de seleccionar: {debug}")
    except Exception:
        pass

    # 3) MÃ©todo preferido: primera opciÃ³n con teclado.
    # En este tipo de dropdown normalmente ArrowDown + Enter dispara el evento Angular correcto.
    print("[BUSQUEDA] Intento 1: ArrowDown + Enter sobre primera opciÃ³n.")
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    page.wait_for_timeout(9000)

    href = (page.url or "").lower()
    print(f"[BUSQUEDA] URL despuÃ©s de ArrowDown+Enter: {page.url}")

    if "#/dashboard" not in href:
        print("[BUSQUEDA] Primera opciÃ³n abriÃ³ la pÃ¡gina del nodo con teclado.")
        return page

    # 4) Reabrir dropdown si quedÃ³ en dashboard.
    print(
        "[BUSQUEDA] SiguiÃ³ en dashboard. Reabriendo dropdown para click en primera fila."
    )
    page.mouse.click(input_x, input_y)
    page.wait_for_timeout(500)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(300)
    page.keyboard.type(nodo, delay=35)
    page.wait_for_timeout(2500)

    # 5) Calcular primera fila visible debajo del input.
    # SegÃºn captura: header "Mostrando..." y debajo viene "NODO 6601 (SEG A)".
    # Click al inicio de la primera opciÃ³n, no al centro derecho.
    first_x = box["x"] + 35
    first_y_candidates = [
        box["y"] + box["h"] + 38,
        box["y"] + box["h"] + 48,
        box["y"] + box["h"] + 58,
        box["y"] + box["h"] + 68,
    ]

    for first_y in first_y_candidates:
        print(
            f"[BUSQUEDA] Intento click primera opciÃ³n: x={round(first_x)} y={round(first_y)}"
        )

        try:
            page.mouse.move(first_x, first_y)
            page.wait_for_timeout(300)
            page.mouse.down()
            page.wait_for_timeout(120)
            page.mouse.up()
            page.wait_for_timeout(9000)
        except Exception as e:
            print(f"[BUSQUEDA] FallÃ³ click coordenado primera opciÃ³n: {e}")

        href = (page.url or "").lower()
        print(f"[BUSQUEDA] URL despuÃ©s click primera opciÃ³n: {page.url}")

        if "#/dashboard" not in href:
            print(
                "[BUSQUEDA] Primera opciÃ³n abriÃ³ la pÃ¡gina del nodo con click coordenado."
            )
            return page

        # Si no abriÃ³, reabrimos para siguiente coordenada.
        try:
            page.mouse.click(input_x, input_y)
            page.wait_for_timeout(400)
            page.keyboard.press("Control+A")
            page.wait_for_timeout(200)
            page.keyboard.type(nodo, delay=25)
            page.wait_for_timeout(1800)
        except Exception:
            pass

    # 6) Ãšltimo intento: JS con elementFromPoint sobre primera fila.
    print("[BUSQUEDA] Intento final: elementFromPoint sobre primera opciÃ³n.")

    for first_y in first_y_candidates:
        clicked = page.evaluate(
            """({x, y}) => {
            function fire(el, type) {
                const ev = new MouseEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    clientX: x,
                    clientY: y
                });
                el.dispatchEvent(ev);
            }

            let el = document.elementFromPoint(x, y);
            if (!el) return null;

            let target = el;
            for (let i = 0; i < 8 && target; i++) {
                const txt = String(target.innerText || target.textContent || '').trim();
                const r = target.getBoundingClientRect();

                if (txt.includes('NODO') || txt.includes('Nodo')) {
                    fire(target, 'mouseover');
                    fire(target, 'mouseenter');
                    fire(target, 'mousedown');
                    fire(target, 'mouseup');
                    fire(target, 'click');

                    return {
                        tag: target.tagName,
                        text: txt.slice(0, 120),
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height)
                    };
                }

                target = target.parentElement;
            }

            fire(el, 'mousedown');
            fire(el, 'mouseup');
            fire(el, 'click');

            return {
                tag: el.tagName,
                text: String(el.innerText || el.textContent || '').trim().slice(0, 120)
            };
        }""",
            {"x": first_x, "y": first_y},
        )

        print(f"[BUSQUEDA] JS click result: {clicked}")
        page.wait_for_timeout(9000)

        href = (page.url or "").lower()
        print(f"[BUSQUEDA] URL despuÃ©s JS click primera opciÃ³n: {page.url}")

        if "#/dashboard" not in href:
            print("[BUSQUEDA] Primera opciÃ³n abriÃ³ la pÃ¡gina del nodo con JS.")
            return page

    try:
        debug = (
            SCREENSHOT_DIR
            / f"debug_primera_opcion_no_abre_{_safe_name(nodo)}_{_stamp()}.png"
        )
        page.screenshot(path=str(debug), full_page=False)
        print(f"[BUSQUEDA] Screenshot primera opciÃ³n no abriÃ³: {debug}")
    except Exception:
        debug = None

    raise RuntimeError(
        f"PathTrak mostrÃ³ la primera opciÃ³n NODO {nodo}, pero no abriÃ³ la pÃ¡gina del nodo. "
        f"URL actual: {page.url}. Debug: {debug}"
    )


def clasificar_vista_operativa_pathtrak(page):
    """
    Clasifica las tres pantallas operativas reales:

    - qoe_dashboard:
        /main/view.html#/dashboard
        Estado del nodo / Lista de prioridad / Lista de alarmas.

    - qoe_pnm:
        /pnm/view.html#/node/health
        Resumen de nodo / Tipos de problemas / UtilizaciÃ³n.

    - spectrum:
        /live/index.html#/app/ea?port=
        Resumen de espectro / Frecuencia / Nivel.
    """
    try:
        url = str(page.url or "")
    except Exception:
        url = ""

    try:
        estado = page.evaluate("""() => {
            const texto = String(
                document.body?.innerText || ""
            ).replace(/\\s+/g, " ").toLowerCase();

            return {
                texto,
                qoeDashboard: (
                    texto.includes("estado del nodo") &&
                    (
                        texto.includes("lista de prioridad") ||
                        texto.includes("lista de alarmas") ||
                        texto.includes("resumen de elementos")
                    )
                ),
                qoePnm: (
                    texto.includes("resumen de nodo") &&
                    (
                        texto.includes("tipos de problemas") ||
                        texto.includes("utilizaciÃ³n de datos") ||
                        texto.includes("utilizacion de datos") ||
                        texto.includes("grupo de mÃ³dems") ||
                        texto.includes("grupo de modems")
                    )
                ),
                spectrum: (
                    texto.includes("resumen de espectro") &&
                    (
                        texto.includes("frecuencia (mhz)") ||
                        texto.includes("nivel (dbmv)") ||
                        texto.includes("ascendente")
                    )
                ),
                cargando: (
                    texto.includes("cargando...") ||
                    texto.includes("loading") ||
                    texto.includes("por favor espere")
                )
            };
        }""")
    except Exception as exc:
        estado = {
            "texto": "",
            "qoeDashboard": False,
            "qoePnm": False,
            "spectrum": False,
            "cargando": False,
            "error": str(exc),
        }

    url_lower = url.lower()

    if estado.get("spectrum"):
        tipo = "spectrum"

    elif estado.get("qoePnm"):
        tipo = "qoe_pnm"

    elif estado.get("qoeDashboard"):
        tipo = "qoe_dashboard"

    elif "/pathtrak/pnm/view.html" in url_lower and "/node/health" in url_lower:
        tipo = "qoe_pnm_cargando"

    elif "/pathtrak/main/view.html" in url_lower and "#/dashboard" in url_lower:
        tipo = "dashboard_cargando"

    elif "/pathtrak/live/index.html" in url_lower and (
        ("#/app/ea" in url_lower and "port=" in url_lower)
        or (
            "#/app/spectrum" in url_lower
            and ("hcu=" in url_lower or "cmts_us_port=" in url_lower)
        )
    ):
        tipo = "spectrum_cargando"

    else:
        tipo = "desconocida"

    return {
        "tipo": tipo,
        "url": url,
        "cargando": bool(estado.get("cargando")),
        "texto_inicial": str(estado.get("texto") or "")[:1200],
        "error": estado.get("error"),
    }


def _paginas_vivas(context):
    paginas = []

    for pagina in list(context.pages):
        try:
            if not pagina.is_closed():
                paginas.append(pagina)
        except Exception:
            pass

    return paginas


def seleccionar_nodo_y_esperar_qoe(
    context,
    dashboard_page,
    nodo,
    timeout_ms=60000,
):
    """
    Selecciona el nodo en el dashboard y prueba su enlace real.

    Resultado adaptativo:
      - href redirige a PNM /node/health:
          se usa esa pÃ¡gina como QoE clÃ¡sico.

      - href redirige a LIVE /app/ea:
          esa pÃ¡gina corresponde a Spectrum.
          Se cierra y se conserva el dashboard como QoE.

    Esto evita clasificar MDN como dashboard sin abrir su QoE PNM.
    """
    nodo = str(nodo or "").strip().upper()

    if not nodo:
        raise RuntimeError("No se recibiÃ³ un nodo vÃ¡lido.")

    dashboard_page.bring_to_front()

    search = dashboard_page.locator("#txtSeachBox")

    search.wait_for(
        state="visible",
        timeout=20000,
    )

    search.click()
    search.fill("")
    dashboard_page.wait_for_timeout(300)
    search.fill(nodo)

    print(f"[BUSQUEDA-QOE] Buscando coincidencia exacta: " f"Nodo {nodo}")

    deadline_dropdown = time.monotonic() + 25
    exacta = None
    filas = []

    while time.monotonic() < deadline_dropdown:
        try:
            resultado = dashboard_page.evaluate(
                """(nodo) => {
                    const normalizar = valor =>
                        String(valor || "")
                            .replace(/\\s+/g, " ")
                            .trim()
                            .toUpperCase();

                    const buscado = normalizar(nodo);

                    const elementos = Array.from(
                        document.querySelectorAll(
                            "ul.custome-autocomplete-ul a, " +
                            "ul.custome-autocomplete-ul li, " +
                            ".custome-autocomplete-ul a, " +
                            ".custome-autocomplete-ul li"
                        )
                    );

                    const filas = elementos.map(
                        (elemento, indice) => {
                            const rect =
                                elemento.getBoundingClientRect();

                            const estilo =
                                window.getComputedStyle(elemento);

                            return {
                                indice,
                                texto: normalizar(
                                    elemento.innerText ||
                                    elemento.textContent
                                ),
                                href:
                                    elemento.href ||
                                    elemento.getAttribute("href") ||
                                    "",
                                tag:
                                    elemento.tagName || "",
                                visible: (
                                    rect.width > 0 &&
                                    rect.height > 0 &&
                                    estilo.display !== "none" &&
                                    estilo.visibility !== "hidden"
                                )
                            };
                        }
                    ).filter(fila => fila.visible);

                    const exacta = filas.find(fila =>
                        fila.texto === `NODO ${buscado}` ||
                        fila.texto.startsWith(
                            `NODO ${buscado} `
                        ) ||
                        fila.texto.startsWith(
                            `NODO ${buscado}(`
                        )
                    );

                    return {
                        exacta: exacta || null,
                        filas: filas.slice(0, 20),
                        sinDatos: String(
                            document.body?.innerText || ""
                        ).includes(
                            "No se encontraron datos"
                        )
                    };
                }""",
                nodo,
            )

            exacta = (resultado or {}).get("exacta")

            filas = (resultado or {}).get("filas") or []

            if exacta:
                break

            if (resultado or {}).get("sinDatos"):
                raise RuntimeError(f"No se encontraron datos para {nodo}.")

        except RuntimeError:
            raise

        except Exception:
            pass

        dashboard_page.wait_for_timeout(500)

    if not exacta:
        debug = _debug_screenshot(
            dashboard_page,
            f"sin_coincidencia_exacta_{nodo}",
        )

        raise RuntimeError(
            f"No apareciÃ³ la coincidencia exacta "
            f"Nodo {nodo}. Filas: {filas}. "
            f"Debug: {debug}"
        )

    href_nodo = str(exacta.get("href") or "").strip()

    print(
        f"[BUSQUEDA-QOE] Coincidencia encontrada: "
        f"{exacta.get('texto')} | "
        f"href={href_nodo}"
    )

    candidato = (
        dashboard_page.locator(
            "ul.custome-autocomplete-ul a, "
            "ul.custome-autocomplete-ul li, "
            ".custome-autocomplete-ul a, "
            ".custome-autocomplete-ul li"
        )
        .filter(has_text=f"Nodo {nodo}")
        .first
    )

    if candidato.count() == 0:
        debug = _debug_screenshot(
            dashboard_page,
            f"locator_nodo_no_encontrado_{nodo}",
        )

        raise RuntimeError(
            f"Se encontrÃ³ Nodo {nodo}, pero no fue posible "
            f"localizarlo para hacer clic. Debug: {debug}"
        )

    print(f"[BUSQUEDA-QOE] Seleccionando Nodo {nodo} " "en el dashboard.")

    candidato.scroll_into_view_if_needed()

    candidato.click(
        force=True,
        timeout=10000,
    )

    dashboard_page.wait_for_timeout(1800)

    # -------------------------------------------------------------
    # Probar el href real del nodo en una pÃ¡gina separada.
    # -------------------------------------------------------------

    probe_page = None
    probe_estado = {}

    if href_nodo:
        print(
            f"[BUSQUEDA-QOE] Abriendo href real para "
            f"clasificar el QoE de {nodo}: {href_nodo}"
        )

        probe_page = context.new_page()

        try:
            probe_page.goto(
                href_nodo,
                wait_until="domcontentloaded",
                timeout=35000,
            )
        except Exception as exc:
            print("[BUSQUEDA-QOE] goto del href terminÃ³ con " f"advertencia: {exc}")

        deadline_probe = time.monotonic() + 25

        while time.monotonic() < deadline_probe:
            try:
                probe_estado = clasificar_vista_operativa_pathtrak(probe_page)
            except Exception as exc:
                probe_estado = {
                    "tipo": "desconocida",
                    "url": probe_page.url or "",
                    "error": str(exc),
                }

            tipo_probe = probe_estado.get("tipo")

            print(
                f"[BUSQUEDA-QOE] Probe {nodo} | "
                f"tipo={tipo_probe} | "
                f"url={probe_estado.get('url')}"
            )

            if tipo_probe == "qoe_pnm":
                probe_page.bring_to_front()
                probe_page.wait_for_timeout(2500)

                print(
                    "[BUSQUEDA-QOE] QoE clÃ¡sico confirmado: "
                    f"qoe_pnm | {probe_estado.get('url')}"
                )

                return probe_page, probe_estado

            if tipo_probe in {
                "spectrum",
                "spectrum_cargando",
            }:
                print(
                    "[BUSQUEDA-QOE] El href del nodo abre "
                    "Spectrum. Se conservarÃ¡ el dashboard QoE."
                )

                try:
                    probe_page.close()
                except Exception:
                    pass

                probe_page = None
                break

            probe_page.wait_for_timeout(750)

    # -------------------------------------------------------------
    # Si el href no produjo PNM y abriÃ³ Spectrum, validar dashboard.
    # -------------------------------------------------------------

    dashboard_page.bring_to_front()

    deadline_dashboard = time.monotonic() + 35
    ultimo_dashboard = {}

    while time.monotonic() < deadline_dashboard:
        try:
            ultimo_dashboard = clasificar_vista_operativa_pathtrak(dashboard_page)

            dropdown_visible = dashboard_page.evaluate("""() => {
                    const elementos = Array.from(
                        document.querySelectorAll(
                            "ul.custome-autocomplete-ul, " +
                            ".custome-autocomplete-ul"
                        )
                    );

                    return elementos.some(elemento => {
                        const rect =
                            elemento.getBoundingClientRect();

                        const estilo =
                            window.getComputedStyle(elemento);

                        return (
                            rect.width > 0 &&
                            rect.height > 0 &&
                            estilo.display !== "none" &&
                            estilo.visibility !== "hidden"
                        );
                    });
                }""")

            valor_busqueda = dashboard_page.locator("#txtSeachBox").input_value()

            valor_busqueda_normalizado = str(valor_busqueda or "").strip().upper()

            nodo_seleccionado = (
                valor_busqueda_normalizado == nodo
                or valor_busqueda_normalizado.startswith(f"NODO {nodo} ")
                or valor_busqueda_normalizado.startswith(f"NODO {nodo}(")
            )

            print(
                f"[BUSQUEDA-QOE] Dashboard {nodo} | "
                f"tipo={ultimo_dashboard.get('tipo')} | "
                f"dropdown={dropdown_visible} | "
                f"input={valor_busqueda}"
            )

            if (
                ultimo_dashboard.get("tipo") == "qoe_dashboard"
                and not dropdown_visible
                and nodo_seleccionado
            ):
                dashboard_page.wait_for_timeout(2500)

                print(
                    "[BUSQUEDA-QOE] QoE dashboard confirmado: "
                    f"{nodo} | {ultimo_dashboard.get('url')}"
                )

                return (
                    dashboard_page,
                    ultimo_dashboard,
                )

        except Exception as exc:
            ultimo_dashboard = {
                "tipo": "desconocida",
                "error": str(exc),
            }

        dashboard_page.wait_for_timeout(750)

    if probe_page is not None and not probe_page.is_closed():
        try:
            probe_page.close()
        except Exception:
            pass

    debug = _debug_screenshot(
        dashboard_page,
        f"qoe_adaptativo_no_confirmado_{nodo}",
    )

    raise RuntimeError(
        f"No se confirmÃ³ QoE PNM ni QoE dashboard "
        f"para {nodo}. "
        f"Probe: {probe_estado}. "
        f"Dashboard: {ultimo_dashboard}. "
        f"Debug: {debug}"
    )


def esperar_qoe_operativo(
    page,
    nodo,
    timeout_ms=60000,
):
    """
    Confirma que QoE estÃ© realmente listo antes de capturar.

    No basta con reconocer la URL o los tÃ­tulos. Para dashboard:
      - el nodo debe estar seleccionado;
      - el desplegable debe estar cerrado;
      - no puede existir un indicador visible 'Cargando...';
      - debe existir contenido operativo en grÃ¡ficos o tablas.

    Para PNM:
      - debe aparecer Resumen de nodo;
      - no debe existir un cargador visible;
      - deben existir tarjetas, grÃ¡fica o filas de mÃ³dems.
    """
    nodo = str(nodo or "").strip().upper()

    deadline = time.monotonic() + max(20, timeout_ms / 1000)

    intento = 0
    ultimo_estado = {}

    while time.monotonic() < deadline:
        intento += 1

        clasificacion = clasificar_vista_operativa_pathtrak(page)

        try:
            carga = page.evaluate(
                """(nodo) => {
                    const visible = elemento => {
                        if (!elemento) return false;

                        const estilo =
                            window.getComputedStyle(elemento);

                        const rect =
                            elemento.getBoundingClientRect();

                        return (
                            estilo.display !== "none" &&
                            estilo.visibility !== "hidden" &&
                            estilo.opacity !== "0" &&
                            rect.width > 0 &&
                            rect.height > 0
                        );
                    };

                    const texto = elemento =>
                        String(
                            elemento?.innerText ||
                            elemento?.textContent ||
                            ""
                        )
                        .replace(/\\s+/g, " ")
                        .trim();

                    const todo = Array.from(
                        document.querySelectorAll("body *")
                    );

                    const cargadores = todo.filter(elemento => {
                        if (!visible(elemento)) {
                            return false;
                        }

                        const contenido = texto(elemento)
                            .toLowerCase();

                        const esTextoCarga = (
                            contenido === "cargando..." ||
                            contenido === "cargando" ||
                            contenido === "loading..." ||
                            contenido === "loading" ||
                            contenido === "por favor espere"
                        );

                        return esTextoCarga;
                    });

                    const filasDatos = Array.from(
                        document.querySelectorAll(
                            "tbody tr, " +
                            ".ui-grid-row, " +
                            ".ag-row, " +
                            "[role='row']"
                        )
                    ).filter(elemento => {
                        if (!visible(elemento)) {
                            return false;
                        }

                        const contenido = texto(elemento)
                            .toLowerCase();

                        return (
                            contenido.length > 10 &&
                            !contenido.includes("cargando")
                        );
                    });

                    const graficos = Array.from(
                        document.querySelectorAll(
                            "canvas, " +
                            "svg, " +
                            ".highcharts-container, " +
                            ".jqplot-target"
                        )
                    ).filter(elemento => {
                        if (!visible(elemento)) {
                            return false;
                        }

                        const rect =
                            elemento.getBoundingClientRect();

                        return (
                            rect.width >= 100 &&
                            rect.height >= 70
                        );
                    });

                    const buscador = document.querySelector(
                        "#txtSeachBox"
                    );

                    const valorBuscador = String(
                        buscador?.value || ""
                    )
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toUpperCase();

                    const nodoSeleccionado = (
                        valorBuscador === nodo ||
                        valorBuscador.startsWith(
                            `NODO ${nodo} `
                        ) ||
                        valorBuscador.startsWith(
                            `NODO ${nodo}(`
                        )
                    );

                    const dropdowns = Array.from(
                        document.querySelectorAll(
                            "ul.custome-autocomplete-ul, " +
                            ".custome-autocomplete-ul"
                        )
                    ).filter(visible);

                    const bodyText = String(
                        document.body?.innerText || ""
                    ).toLowerCase();

                    return {
                        cargadores: cargadores.length,
                        textosCarga: cargadores
                            .slice(0, 10)
                            .map(texto),
                        filasDatos: filasDatos.length,
                        graficos: graficos.length,
                        nodoSeleccionado,
                        dropdownVisible:
                            dropdowns.length > 0,
                        tieneResumenNodo:
                            bodyText.includes(
                                "resumen de nodo"
                            ),
                        tieneEstadoNodo:
                            bodyText.includes(
                                "estado del nodo"
                            ),
                        tieneListaAlarmas:
                            bodyText.includes(
                                "lista de alarmas"
                            ),
                        tieneTiposProblemas:
                            bodyText.includes(
                                "tipos de problemas"
                            )
                    };
                }""",
                nodo,
            )

        except Exception as exc:
            carga = {
                "cargadores": 999,
                "filasDatos": 0,
                "graficos": 0,
                "nodoSeleccionado": False,
                "dropdownVisible": True,
                "error": str(exc),
            }

        tipo = clasificacion.get("tipo")

        contenido_operativo = (
            carga.get("filasDatos", 0) > 0 or carga.get("graficos", 0) > 0
        )

        sin_cargadores = carga.get("cargadores", 0) == 0

        if tipo == "qoe_dashboard":
            listo = (
                carga.get("nodoSeleccionado")
                and not carga.get("dropdownVisible")
                and carga.get("tieneEstadoNodo")
                and carga.get("tieneListaAlarmas")
                and contenido_operativo
                and sin_cargadores
            )

        elif tipo == "qoe_pnm":
            listo = (
                carga.get("tieneResumenNodo")
                and carga.get("tieneTiposProblemas")
                and contenido_operativo
                and sin_cargadores
            )

        else:
            listo = False

        ultimo_estado = {
            "tipo": tipo,
            "url": clasificacion.get("url"),
            **carga,
            "listo": listo,
        }

        print(
            f"[QOE-LISTO] intento={intento} | "
            f"nodo={nodo} | "
            f"tipo={tipo} | "
            f"cargadores={carga.get('cargadores')} | "
            f"filas={carga.get('filasDatos')} | "
            f"graficos={carga.get('graficos')} | "
            f"seleccionado={carga.get('nodoSeleccionado')} | "
            f"dropdown={carga.get('dropdownVisible')} | "
            f"listo={listo}"
        )

        if listo:
            print(f"[QOE-LISTO] Vista completamente cargada " f"para nodo {nodo}.")

            # EstabilizaciÃ³n final para que terminen las animaciones.
            page.wait_for_timeout(1500)

            return {
                **clasificacion,
                "carga": carga,
            }

        page.wait_for_timeout(1000)

    debug = _debug_screenshot(
        page,
        f"qoe_incompleto_{nodo}",
    )

    raise RuntimeError(
        f"La vista QoE de {nodo} abriÃ³, pero no terminÃ³ "
        f"de cargar dentro de {timeout_ms} ms. "
        f"Ãšltimo estado: {ultimo_estado}. "
        f"Debug: {debug}"
    )


def abrir_spectrum_desde_qoe(
    context,
    qoe_page,
    nodo,
    timeout_ms=45000,
):
    """
    Abre ruido desde cualquiera de las dos vistas QoE usando
    el control exacto confirmado por el operador:
      #EXTERNAL_LINK_HCU_SPECTRUM_ANALYZER
    """
    qoe_page.bring_to_front()

    selector = "#EXTERNAL_LINK_HCU_SPECTRUM_ANALYZER"

    icono = qoe_page.locator(selector)

    try:
        icono.wait_for(
            state="visible",
            timeout=20000,
        )
    except Exception:
        debug = _debug_screenshot(
            qoe_page,
            f"sin_boton_spectrum_{nodo}",
        )

        raise RuntimeError(
            "No se encontrÃ³ el botÃ³n confirmado "
            f"{selector} para el nodo {nodo}. "
            f"Vista: "
            f"{clasificar_vista_operativa_pathtrak(qoe_page)}. "
            f"Debug: {debug}"
        )

    boton = icono.locator("xpath=ancestor::button[1]")

    if boton.count() == 0:
        boton = icono

    paginas_antes = {id(pagina) for pagina in _paginas_vivas(context)}

    print(f"[SPECTRUM] Pulsando {selector} para nodo {nodo}.")

    boton.click(
        force=True,
        timeout=10000,
    )

    deadline = time.monotonic() + max(20, timeout_ms / 1000)

    pagina_spectrum = None
    ultimo_estado = {}

    while time.monotonic() < deadline:
        paginas = _paginas_vivas(context)

        # Primero revisar pÃ¡ginas nuevas.
        ordenadas = sorted(
            paginas,
            key=lambda pagina: (0 if id(pagina) not in paginas_antes else 1),
        )

        for pagina in ordenadas:
            try:
                estado = clasificar_vista_operativa_pathtrak(pagina)

                if estado.get("tipo") in {
                    "spectrum",
                    "spectrum_cargando",
                }:
                    pagina_spectrum = pagina
                    ultimo_estado = estado
                    break

            except Exception:
                pass

        if pagina_spectrum is not None:
            try:
                pagina_spectrum.bring_to_front()
            except Exception:
                pass

            if ultimo_estado.get("tipo") == "spectrum":
                print(
                    "[SPECTRUM] Vista de espectro confirmada: "
                    f"{ultimo_estado.get('url')}"
                )

                return pagina_spectrum

        qoe_page.wait_for_timeout(750)

    debug_page = pagina_spectrum or (
        _paginas_vivas(context)[-1] if _paginas_vivas(context) else qoe_page
    )

    debug = _debug_screenshot(
        debug_page,
        f"spectrum_no_abriÃ³_{nodo}",
    )

    raise RuntimeError(
        f"El botÃ³n Spectrum fue pulsado para {nodo}, "
        "pero no apareciÃ³ la vista /live/#/app/ea. "
        f"Ãšltimo estado: {ultimo_estado}. "
        f"Debug: {debug}"
    )


def esperar_ondas_reales(
    page,
    nodo,
    timeout_ms=60000,
):
    """
    Espera no solo el panel, sino una grÃ¡fica con trazos dibujados.

    Acepta SVG/canvas/highcharts cuando:
      - hay varias rutas/series visibles; o
      - un canvas tiene tamaÃ±o operativo;
    y el texto contiene Resumen de espectro.
    """
    deadline = time.monotonic() + max(20, timeout_ms / 1000)

    ultimo_estado = {}

    while time.monotonic() < deadline:
        try:
            ultimo_estado = page.evaluate("""() => {
                const texto = String(
                    document.body?.innerText || ""
                ).replace(/\\s+/g, " ").toLowerCase();

                const visibles = elementos =>
                    Array.from(elementos).filter(elemento => {
                        const rect =
                            elemento.getBoundingClientRect();
                        const estilo =
                            window.getComputedStyle(elemento);

                        return (
                            rect.width > 20 &&
                            rect.height > 10 &&
                            estilo.display !== "none" &&
                            estilo.visibility !== "hidden"
                        );
                    });

                const paths = visibles(
                    document.querySelectorAll(
                        "svg path, " +
                        ".highcharts-series path, " +
                        ".highcharts-graph"
                    )
                );

                const canvases = visibles(
                    document.querySelectorAll("canvas")
                );

                const polylines = visibles(
                    document.querySelectorAll(
                        "svg polyline, svg line"
                    )
                );

                const tienePanel = (
                    texto.includes("resumen de espectro") &&
                    (
                        texto.includes("frecuencia (mhz)") ||
                        texto.includes("nivel (dbmv)")
                    )
                );

                const hayTrazos = (
                    paths.length >= 3 ||
                    polylines.length >= 3 ||
                    canvases.some(canvas => {
                        const rect =
                            canvas.getBoundingClientRect();

                        return (
                            rect.width >= 400 &&
                            rect.height >= 120
                        );
                    })
                );

                return {
                    tienePanel,
                    hayTrazos,
                    paths: paths.length,
                    polylines: polylines.length,
                    canvases: canvases.length,
                    cargando: (
                        texto.includes("cargando...") ||
                        texto.includes("loading")
                    ),
                    sinFilas:
                        texto.includes("no hay filas para mostrar")
                };
            }""")
        except Exception as exc:
            ultimo_estado = {
                "tienePanel": False,
                "hayTrazos": False,
                "error": str(exc),
            }

        print(
            f"[SPECTRUM] nodo={nodo} | "
            f"panel={ultimo_estado.get('tienePanel')} | "
            f"trazos={ultimo_estado.get('hayTrazos')} | "
            f"paths={ultimo_estado.get('paths', 0)} | "
            f"canvas={ultimo_estado.get('canvases', 0)}"
        )

        if ultimo_estado.get("tienePanel") and ultimo_estado.get("hayTrazos"):
            # PequeÃ±a estabilizaciÃ³n final de las ondas.
            page.wait_for_timeout(2000)
            return ultimo_estado

        page.wait_for_timeout(1000)

    debug = _debug_screenshot(
        page,
        f"ondas_no_cargaron_{nodo}",
    )

    raise RuntimeError(
        f"La vista Spectrum abriÃ³ para {nodo}, "
        "pero las ondas no terminaron de dibujarse. "
        f"Ãšltimo estado: {ultimo_estado}. "
        f"Debug: {debug}"
    )


# PATHTRAK_MULTI_RESULT_FALLBACK_V2
def _pathtrak_ruido_desde_resultado(resultado):
    if not isinstance(resultado, dict):
        return {}

    if str(resultado.get("tipo") or "").strip().lower() == "qoe_ruido":
        capturas = resultado.get("capturas") or {}
        ruido = capturas.get("ruido") or {}
        return ruido if isinstance(ruido, dict) else {}

    return resultado


def _pathtrak_debe_probar_segunda_opcion(resultado):
    # Solo activa el fallback cuando el primer resultado llego al
    # Analizador de espectro, mostro el error RCI y NO logro recuperar
    # una grafica util.
    ruido = _pathtrak_ruido_desde_resultado(resultado)

    if not ruido:
        return False

    tiene_error_rci = bool(ruido.get("error_screenshot"))
    recuperada = bool(ruido.get("grafica_recuperada"))

    return tiene_error_rci and not recuperada


def _pathtrak_rutas_evidencia(resultado):
    # Obtiene las rutas de screenshots que pertenecen a un resultado.
    # Se usa para limpiar la evidencia del primer intento solamente
    # despues de que el segundo intento produjo un resultado.
    rutas = set()

    def visitar(valor):
        if isinstance(valor, dict):
            for clave, contenido in valor.items():
                if clave in {"screenshot", "error_screenshot"}:
                    if isinstance(contenido, str) and contenido.strip():
                        rutas.add(contenido.strip())
                elif clave == "screenshots":
                    if isinstance(contenido, (list, tuple)):
                        for item in contenido:
                            if isinstance(item, str) and item.strip():
                                rutas.add(item.strip())
                elif isinstance(contenido, (dict, list, tuple)):
                    visitar(contenido)

        elif isinstance(valor, (list, tuple)):
            for item in valor:
                visitar(item)

    visitar(resultado)
    return rutas


def _pathtrak_eliminar_evidencias(resultado, nombre_region, nodo):
    for ruta in _pathtrak_rutas_evidencia(resultado):
        try:
            archivo = Path(ruta)
            if archivo.exists() and archivo.is_file():
                archivo.unlink()
                print(
                    f"[{nombre_region}] Fallback Nodo {nodo}: "
                    f"evidencia del primer resultado eliminada: {archivo}"
                )
        except Exception as exc:
            print(
                f"[{nombre_region}] Fallback Nodo {nodo}: "
                f"no pude eliminar evidencia anterior {ruta}: {exc}"
            )


def capturar_spectrum_en_url(
    playwright,
    url,
    nodo,
    nombre_region,
    tipo_captura="ondas",
):
    # Wrapper V2 para nodos con mas de una coincidencia.
    resultado_primero = _capturar_spectrum_en_url_intento(
        playwright=playwright,
        url=url,
        nodo=nodo,
        nombre_region=nombre_region,
        tipo_captura=tipo_captura,
        opcion_indice=0,
    )

    if not _pathtrak_debe_probar_segunda_opcion(resultado_primero):
        return resultado_primero

    print(
        f"[{nombre_region}] Nodo {nodo}: la primera opcion termino "
        "con error RCI/sin grafica. Se probara la segunda opcion."
    )

    # PATHTRAK_KEEP_QOE_OPTION1_SPECTRUM_OPTION2_V3
    tipo_normalizado = str(tipo_captura or "").strip().lower()

    # Si la solicitud es QoE + Ruido, la segunda opcion se usa SOLO
    # para Spectrum. Asi no se toma otra captura QoE de la opcion 2.
    tipo_segundo = (
        "ondas"
        if tipo_normalizado in {
            "qoe_ruido",
            "qoe+ruido",
            "qoe-y-ruido",
            "qoe_ondas",
            "ambas",
            "ambos",
        }
        else tipo_captura
    )

    try:
        resultado_segundo = _capturar_spectrum_en_url_intento(
            playwright=playwright,
            url=url,
            nodo=nodo,
            nombre_region=nombre_region,
            tipo_captura=tipo_segundo,
            opcion_indice=1,
        )
    except Exception as exc:
        print(
            f"[{nombre_region}] Nodo {nodo}: el intento sobre la "
            f"segunda opcion no pudo completarse: {exc}. "
            "Se conserva el resultado de la primera opcion."
        )
        return resultado_primero

    if (
        isinstance(resultado_segundo, dict)
        and resultado_segundo.get("_pathtrak_segunda_opcion_no_disponible")
    ):
        print(
            f"[{nombre_region}] Nodo {nodo}: no existe una segunda "
            "opcion real. Se conserva el primer resultado."
        )
        return resultado_primero

    # Para QoE + Ruido NO reemplazamos todo con la opcion 2.
    # Resultado final:
    #   foto 1 = QoE de la opcion 1
    #   foto 2 = Spectrum de la opcion 2
    if tipo_normalizado in {
        "qoe_ruido",
        "qoe+ruido",
        "qoe-y-ruido",
        "qoe_ondas",
        "ambas",
        "ambos",
    } and tipo_segundo == "ondas":
        capturas_primero = resultado_primero.get("capturas") or {}
        qoe_primero = (
            capturas_primero.get("qoe")
            if isinstance(capturas_primero, dict)
            else None
        )

        if isinstance(qoe_primero, dict) and qoe_primero.get("screenshot"):
            # PATHTRAK_PRUNE_TO_TWO_FINAL_SCREENSHOTS_V4
            # Guardamos TODAS las evidencias fisicas del segundo intento
            # antes de modificar su diccionario. Asi podremos borrar
            # el popup/error o la pantalla intermedia que no se muestre.
            rutas_segundo_originales = set(
                _pathtrak_rutas_evidencia(resultado_segundo)
            )

            ruido_segundo = _pathtrak_ruido_desde_resultado(resultado_segundo)
            ruido_segundo = (
                dict(ruido_segundo)
                if isinstance(ruido_segundo, dict)
                else {}
            )

            grafica_recuperada = bool(
                ruido_segundo.get("grafica_recuperada")
            )
            estado_ruido = str(
                ruido_segundo.get("estado") or ""
            ).strip().upper()
            error_screenshot = str(
                ruido_segundo.get("error_screenshot") or ""
            ).strip()
            screenshot_normal = str(
                ruido_segundo.get("screenshot") or ""
            ).strip()

            segunda_fallo = bool(
                error_screenshot
                and not grafica_recuperada
                and estado_ruido not in {"OK", "RECUPERADO"}
            )

            if segunda_fallo:
                # Si la opcion 2 tambien falla, mostrar SOLO su error.
                screenshot_ruido = error_screenshot or screenshot_normal
                ruido_segundo["screenshot"] = screenshot_ruido
                ruido_segundo["estado"] = (
                    ruido_segundo.get("estado") or "ERROR"
                )
                ruido_segundo["mensaje"] = (
                    "La primera opcion no presento grafica util y la "
                    "segunda opcion tambien mostro error. Se conserva "
                    "el QoE de la primera opcion y el error de la segunda."
                )
            else:
                # Si la opcion 2 carga, mostrar SOLO su grafica.
                screenshot_ruido = screenshot_normal
                ruido_segundo["estado"] = "OK"
                ruido_segundo["estado_label"] = (
                    "Spectrum cargado desde la segunda opcion"
                )
                ruido_segundo["mensaje"] = (
                    "Se conserva el QoE de la primera opcion y se usa "
                    "la grafica Spectrum de la segunda opcion."
                )

            # Evita que pathtrak_response agregue una tercera imagen
            # correspondiente al popup RCI de la opcion 2.
            ruido_segundo.pop("error_screenshot", None)

            qoe_path = str(
                qoe_primero.get("screenshot") or ""
            ).strip()

            # De TODO lo generado por opcion 1 y opcion 2,
            # conservar fisicamente SOLO:
            #   1) QoE de la opcion 1
            #   2) Spectrum final (o error final) de la opcion 2
            rutas_generadas = set(
                _pathtrak_rutas_evidencia(resultado_primero)
            )
            rutas_generadas.update(rutas_segundo_originales)

            conservar = set()

            if qoe_path:
                try:
                    conservar.add(
                        str(Path(qoe_path).resolve()).casefold()
                    )
                except Exception:
                    conservar.add(qoe_path.casefold())

            if screenshot_ruido:
                try:
                    conservar.add(
                        str(Path(screenshot_ruido).resolve()).casefold()
                    )
                except Exception:
                    conservar.add(screenshot_ruido.casefold())

            for ruta in rutas_generadas:
                ruta = str(ruta or "").strip()

                if not ruta:
                    continue

                try:
                    archivo = Path(ruta)
                    clave = str(archivo.resolve()).casefold()
                except Exception:
                    archivo = Path(ruta)
                    clave = ruta.casefold()

                if clave in conservar:
                    continue

                try:
                    if archivo.exists() and archivo.is_file():
                        archivo.unlink()
                        print(
                            f"[{nombre_region}] Nodo {nodo}: "
                            f"captura intermedia eliminada: {archivo}"
                        )
                except Exception as exc:
                    print(
                        f"[{nombre_region}] Nodo {nodo}: no pude "
                        f"eliminar captura intermedia {ruta}: {exc}"
                    )

            screenshots = [qoe_path]
            if screenshot_ruido:
                screenshots.append(screenshot_ruido)

            evidencias = [
                {
                    "tipo": "qoe",
                    "tipo_label": "QoE / Estado del nodo",
                    "screenshot": qoe_path,
                    "url": qoe_primero.get("url"),
                }
            ]

            if screenshot_ruido:
                evidencias.append(
                    {
                        "tipo": (
                            "ondas_error" if segunda_fallo else "ondas"
                        ),
                        "tipo_label": (
                            "Ruido / Error Spectrum"
                            if segunda_fallo
                            else "Ruido / Spectrum"
                        ),
                        "screenshot": screenshot_ruido,
                        "url": ruido_segundo.get("url"),
                    }
                )

            print(
                f"[{nombre_region}] Nodo {nodo}: resultado combinado. "
                "QoE=opcion 1 | Spectrum=opcion 2."
            )

            return {
                "ok": True,
                "tipo": "qoe_ruido",
                "region": nombre_region,
                "nodo": nodo,
                "estado": ("PARCIAL" if segunda_fallo else "OK"),
                "capturas": {
                    "qoe": qoe_primero,
                    "ruido": ruido_segundo,
                },
                "evidencias": evidencias,
                "screenshots": screenshots,
                "urls": {
                    "qoe": qoe_primero.get("url"),
                    "ruido": ruido_segundo.get("url"),
                },
                "mensaje": ruido_segundo.get("mensaje"),
                "fallback_segunda_opcion": True,
                "qoe_desde_opcion": 1,
                "spectrum_desde_opcion": 2,
            }

    # Para solicitudes que no son combinadas se mantiene el V2.
    _pathtrak_eliminar_evidencias(
        resultado_primero,
        nombre_region=nombre_region,
        nodo=nodo,
    )

    print(
        f"[{nombre_region}] Nodo {nodo}: se usara el resultado "
        "de la segunda opcion."
    )

    return resultado_segundo


def _capturar_spectrum_en_url_intento(
    playwright,
    url,
    nodo,
    nombre_region,
    tipo_captura="ondas",
    opcion_indice=0,
):
    """
    Flujo simple real de PathTrak.

    1. Login.
    2. Escribir nodo.
    3. Esperar lista.
    4. Hacer clic en el nodo exacto.
    5. Usar la pÃ¡gina que PathTrak abra.
    6. Esperar unos segundos.
    7. Capturar QoE.
    8. Para ruido, pulsar el botÃ³n confirmado de Spectrum.
    """
    nodo = str(nodo or "").strip().upper()

    tipo = str(tipo_captura or "ondas").strip().lower()

    if tipo in {
        "ambas",
        "ambos",
        "qoe+ruido",
        "qoe_ruido",
        "qoe-y-ruido",
        "qoe_ondas",
    }:
        tipo = "qoe_ruido"

    elif tipo == "ruido":
        tipo = "ondas"

    launch_options = {
        "headless": PATHTRAK_HEADLESS,
        "slow_mo": (250 if not PATHTRAK_HEADLESS else 0),
        "args": [
            "--ignore-certificate-errors",
            "--allow-insecure-localhost",
            "--disable-extensions",
            "--disable-features=Translate",
            "--start-maximized",
        ],
    }

    if PATHTRAK_PROXY:
        launch_options["proxy"] = {
            "server": PATHTRAK_PROXY,
        }

        print(f"[{nombre_region}] Proxy: " f"{PATHTRAK_PROXY}")

    else:
        launch_options["args"].extend(
            [
                "--no-proxy-server",
                "--proxy-bypass-list=*",
                "--disable-http2",
                "--disable-quic",
            ]
        )

    browser = playwright.chromium.launch(**launch_options)

    context_options = dict(
        ignore_https_errors=True,
        http_credentials={
            "username": PATHTRAK_USER,
            "password": PATHTRAK_PASSWORD,
        },
        viewport={
            "width": 1920,
            "height": 1080,
        },
        accept_downloads=True,
        locale="es-CO",
    )

    try:
        context = browser.new_context(**context_options)
        dashboard = context.new_page()
    except Exception:
        try:
            browser.close()
        finally:
            raise
    qoe_page = None
    spectrum_page = None
    qoe_capture = None

    try:
        print(f"[{nombre_region}] Login PathTrak.")

        estado_login = login_pathtrak(
            dashboard,
            url,
        )
        print(f"[{nombre_region}] Estado autenticaciÃ³n: {estado_login}.")

        dashboard.bring_to_front()

        input_nodo = dashboard.locator("#txtSeachBox")

        input_nodo.wait_for(
            state="visible",
            timeout=20000,
        )

        input_nodo.click()
        input_nodo.fill("")
        dashboard.wait_for_timeout(300)
        input_nodo.fill(nodo)

        print(f"[{nombre_region}] Esperando Nodo {nodo}.")

        deadline = time.monotonic() + 25
        opcion = None
        coincidencias_detectadas = []

        # Para evitar contar dos veces una misma fila (por ejemplo LI + A),
        # primero buscamos enlaces <a>, que son la opcion clickeable real.
        # Si PathTrak cambia y no hay enlaces, usamos <li> como fallback.
        selectores_resultado = [
            (
                "ul.custome-autocomplete-ul a, "
                ".custome-autocomplete-ul a"
            ),
            (
                "ul.custome-autocomplete-ul li, "
                ".custome-autocomplete-ul li"
            ),
        ]

        while time.monotonic() < deadline:
            coincidencias_detectadas = []
            candidatos = None
            selector_usado = None

            for selector_resultado in selectores_resultado:
                candidatos_prueba = dashboard.locator(selector_resultado)
                cantidad = candidatos_prueba.count()
                coincidencias_prueba = []

                for indice in range(cantidad):
                    candidato = candidatos_prueba.nth(indice)

                    try:
                        if not candidato.is_visible():
                            continue

                        texto = (
                            candidato.inner_text(timeout=1500)
                            .strip()
                            .upper()
                        )

                        coincide_nodo = (
                            texto == f"NODO {nodo}"
                            or texto.startswith(f"NODO {nodo} ")
                            or texto.startswith(f"NODO {nodo}(")
                        )

                        # PATHTRAK_CLUSTER_NODE_FALLBACK_V1_1
                        coincide_cluster = False

                        if texto.startswith("CLUSTER "):
                            miembros = (
                                texto[len("CLUSTER ") :]
                                .split("(")[0]
                                .split(",")
                            )
                            miembros = [
                                item.strip()
                                for item in miembros
                                if item.strip()
                            ]
                            coincide_cluster = (
                                str(nodo).strip().upper() in miembros
                            )

                        if coincide_nodo or coincide_cluster:
                            href = ""

                            try:
                                href = (
                                    candidato.get_attribute("href")
                                    or ""
                                )
                            except Exception:
                                pass

                            coincidencias_prueba.append(
                                {
                                    "indice_dom": indice,
                                    "texto": texto,
                                    "href": href,
                                }
                            )

                    except Exception:
                        continue

                if coincidencias_prueba:
                    candidatos = candidatos_prueba
                    selector_usado = selector_resultado
                    coincidencias_detectadas = coincidencias_prueba
                    break

            if coincidencias_detectadas:
                print(
                    f"[{nombre_region}] Nodo {nodo}: "
                    f"{len(coincidencias_detectadas)} opcion(es) "
                    f"coincidente(s) visibles. "
                    f"Se solicita opcion #{opcion_indice + 1}."
                )

                if opcion_indice < len(coincidencias_detectadas):
                    seleccion = coincidencias_detectadas[opcion_indice]
                    opcion = candidatos.nth(seleccion["indice_dom"])

                    print(
                        f"[{nombre_region}] Nodo {nodo}: seleccionando "
                        f"opcion #{opcion_indice + 1} | "
                        f"texto={seleccion.get('texto')} | "
                        f"href={seleccion.get('href')} | "
                        f"selector={selector_usado}"
                    )
                    break

            dashboard.wait_for_timeout(500)

        if opcion is None:
            if opcion_indice > 0:
                return {
                    "ok": False,
                    "_pathtrak_segunda_opcion_no_disponible": True,
                    "tipo": "fallback_sin_segunda_opcion",
                    "region": nombre_region,
                    "nodo": nodo,
                    "opcion_indice": opcion_indice,
                    "coincidencias_detectadas": coincidencias_detectadas,
                }

            debug = _debug_screenshot(
                dashboard,
                f"nodo_no_encontrado_{nodo}",
            )

            raise RuntimeError(
                f"No aparecio Nodo {nodo} "
                f"en la lista. Debug: {debug}"
            )

        print(
            f"[{nombre_region}] Clic real en Nodo {nodo} "
            f"(opcion #{opcion_indice + 1})."
        )

        paginas_antes = list(context.pages)

        opcion.scroll_into_view_if_needed()

        opcion.click(
            force=True,
            timeout=10000,
        )

        # Esperar a que PathTrak abra la vista correspondiente.
        deadline_pagina = time.monotonic() + 15
        qoe_page = dashboard

        while time.monotonic() < deadline_pagina:
            paginas = [pagina for pagina in context.pages if not pagina.is_closed()]

            nuevas = [pagina for pagina in paginas if pagina not in paginas_antes]

            if nuevas:
                qoe_page = nuevas[-1]
                break

            # Algunos nodos permanecen en el dashboard.
            valor = ""

            try:
                valor = input_nodo.input_value()
            except Exception:
                pass

            if str(valor or "").strip().upper().startswith(f"NODO {nodo}"):
                qoe_page = dashboard

            dashboard.wait_for_timeout(500)

        qoe_page.bring_to_front()

        print(f"[{nombre_region}] PÃ¡gina QoE: " f"{qoe_page.url}")

        # El operador confirmÃ³ que solo necesitamos dar
        # tiempo razonable a que la pÃ¡gina abra.
        qoe_page.wait_for_timeout(8000)

        if tipo in {
            "qoe",
            "estado",
            "nodo",
            "qoe_ruido",
        }:
            # En algunas vistas PNM aparece el aviso de licencia SUS.
            # Se debe cerrar antes de generar la captura QoE.
            try:
                modal_cerrado = aceptar_cualquier_modal_pathtrak(
                    qoe_page,
                    motivo=f"antes captura qoe {nodo}",
                )

                if modal_cerrado:
                    print(
                        f"[{nombre_region}] Aviso cerrado "
                        f"antes de capturar QoE de {nodo}."
                    )

                    qoe_page.wait_for_timeout(1200)

            except Exception as exc:
                print(
                    f"[{nombre_region}] No fue posible cerrar "
                    f"el aviso antes de QoE: {exc}"
                )

            qoe_out = SCREENSHOT_DIR / (
                f"qoe_{_safe_name(nombre_region)}_" f"{_safe_name(nodo)}_{_stamp()}.png"
            )

            print(f"[{nombre_region}] Inicio captura QoE nodo={nodo}.")
            qoe_page.bring_to_front()
            qoe_page.set_viewport_size(
                {
                    "width": 1920,
                    "height": 1080,
                }
            )

            qoe_page.screenshot(
                path=str(qoe_out),
                full_page=False,
            )

            # VerificaciÃ³n fÃ­sica de la evidencia QoE.
            #
            # Para qoe_ruido NO se permite continuar hacia Spectrum
            # si esta captura no existe realmente en disco.
            qoe_guardado = (
                qoe_out.exists() and qoe_out.is_file() and qoe_out.stat().st_size > 0
            )

            if not qoe_guardado:
                print(
                    f"[{nombre_region}] La captura QoE no apareciÃ³ "
                    f"en disco. Reintentando una vez: {qoe_out}"
                )

                qoe_page.bring_to_front()
                qoe_page.wait_for_timeout(800)

                qoe_page.screenshot(
                    path=str(qoe_out),
                    full_page=False,
                )

                qoe_guardado = (
                    qoe_out.exists()
                    and qoe_out.is_file()
                    and qoe_out.stat().st_size > 0
                )

            if not qoe_guardado:
                raise RuntimeError(
                    "PATHTRAK_QOE_SCREENSHOT_NO_GUARDADO: "
                    f"No se pudo guardar la evidencia QoE de "
                    f"{nodo} en {qoe_out}."
                )

            print(
                f"[{nombre_region}] Fin QoE; screenshot confirmado "
                f"en disco: {qoe_out} "
                f"({qoe_out.stat().st_size} bytes)"
            )

            qoe_capture = {
                "ok": True,
                "tipo": "qoe",
                "tipo_label": "QoE / Estado del nodo",
                "region": nombre_region,
                "nodo": nodo,
                "url": qoe_page.url,
                "screenshot": str(qoe_out),
            }

            if tipo in {
                "qoe",
                "estado",
                "nodo",
            }:
                return qoe_capture

        # Seguridad del flujo combinado:
        # qoe_ruido debe llegar a Spectrum Ãºnicamente despuÃ©s de
        # confirmar que la evidencia QoE existe realmente.
        if tipo == "qoe_ruido":
            if not qoe_capture:
                raise RuntimeError(
                    "PATHTRAK_QOE_RUIDO_SIN_QOE: "
                    "Se solicitÃ³ qoe_ruido, pero no existe "
                    "qoe_capture antes de abrir Spectrum."
                )

            qoe_path_confirmado = Path(str(qoe_capture.get("screenshot") or ""))

            if (
                not qoe_path_confirmado.exists()
                or not qoe_path_confirmado.is_file()
                or qoe_path_confirmado.stat().st_size <= 0
            ):
                raise RuntimeError(
                    "PATHTRAK_QOE_RUIDO_SIN_ARCHIVO_QOE: "
                    "La captura QoE no existe en disco antes "
                    f"de abrir Spectrum: {qoe_path_confirmado}"
                )

            print(
                f"[{nombre_region}] qoe_ruido: evidencia QoE "
                f"confirmada antes de Spectrum: "
                f"{qoe_path_confirmado}"
            )

        print(f"[{nombre_region}] Preparando pÃ¡gina QoE " "antes de abrir Spectrum.")

        # En la vista PNM puede aparecer el aviso de licencia SUS.
        # Ese modal bloquea todos los botones de la pÃ¡gina.
        for intento_modal in range(1, 6):
            cerrado = False

            try:
                cerrado = aceptar_cualquier_modal_pathtrak(
                    qoe_page,
                    motivo=f"antes spectrum {nodo}",
                )
            except Exception as exc:
                print(f"[{nombre_region}] Intento modal " f"{intento_modal}: {exc}")

            if cerrado:
                print(f"[{nombre_region}] Aviso PathTrak " "cerrado correctamente.")

                qoe_page.wait_for_timeout(1500)
                break

            # Fallback directo al botÃ³n SweetAlert confirmado.
            try:
                aceptar = qoe_page.locator("button.swal2-confirm.swal2-styled").last

                if aceptar.count() > 0 and aceptar.is_visible(timeout=1000):
                    print(
                        f"[{nombre_region}] Cerrando aviso "
                        "con selector SweetAlert directo."
                    )

                    aceptar.click(
                        force=True,
                        timeout=5000,
                    )

                    qoe_page.wait_for_timeout(1500)
                    break

            except Exception:
                pass

            qoe_page.wait_for_timeout(700)

        # Confirmar que el modal ya no siga bloqueando la pÃ¡gina.
        try:
            modal_visible = qoe_page.locator(".swal2-container").is_visible(
                timeout=1000
            )
        except Exception:
            modal_visible = False

        if modal_visible:
            debug = _debug_screenshot(
                qoe_page,
                f"modal_pnm_no_cerro_{nodo}",
            )

            raise RuntimeError(
                "El aviso de licencia SUS continÃºa abierto "
                f"y bloquea la pÃ¡gina. Debug: {debug}"
            )

        print(f"[{nombre_region}] Abriendo Spectrum.")

        # Abrir Spectrum directamente desde el DOM.
        #
        # Playwright locator() no estÃ¡ viendo el control de Angular,
        # aunque el botÃ³n sÃ­ aparece visualmente. Se pulsa mediante
        # JavaScript por id, title o texto del menÃº.

        qoe_page.bring_to_front()
        qoe_page.wait_for_timeout(3000)

        paginas_antes_spectrum = list(context.pages)

        click_spectrum = False
        metodo_spectrum = None
        deadline_click = time.monotonic() + 20

        while time.monotonic() < deadline_click:
            for frame in qoe_page.frames:
                try:
                    resultado_click = frame.evaluate("""() => {
                            const visible = elemento => {
                                if (!elemento) {
                                    return false;
                                }

                                const estilo =
                                    window.getComputedStyle(elemento);

                                const rect =
                                    elemento.getBoundingClientRect();

                                return (
                                    estilo.display !== "none" &&
                                    estilo.visibility !== "hidden" &&
                                    estilo.opacity !== "0" &&
                                    rect.width > 0 &&
                                    rect.height > 0
                                );
                            };

                            const disparar = elemento => {
                                if (!elemento) {
                                    return false;
                                }

                                elemento.scrollIntoView({
                                    block: "center",
                                    inline: "center"
                                });

                                const objetivo =
                                    elemento.closest("button") ||
                                    elemento;

                                objetivo.dispatchEvent(
                                    new PointerEvent(
                                        "pointerdown",
                                        {
                                            bubbles: true,
                                            cancelable: true
                                        }
                                    )
                                );

                                objetivo.dispatchEvent(
                                    new MouseEvent(
                                        "mousedown",
                                        {
                                            bubbles: true,
                                            cancelable: true,
                                            view: window
                                        }
                                    )
                                );

                                objetivo.dispatchEvent(
                                    new MouseEvent(
                                        "mouseup",
                                        {
                                            bubbles: true,
                                            cancelable: true,
                                            view: window
                                        }
                                    )
                                );

                                objetivo.click();

                                return true;
                            };

                            const directo = [
                                document.getElementById(
                                    "MENU_Spectrum_Analyzer"
                                ),
                                document.getElementById(
                                    "EXTERNAL_LINK_HCU_SPECTRUM_ANALYZER"
                                ),
                                document.querySelector(
                                    '[title="Analizador de espectro"]'
                                ),
                                document.querySelector(
                                    '[title*="Analizador de espectro"]'
                                )
                            ].find(elemento =>
                                elemento && visible(elemento)
                            );

                            if (directo) {
                                disparar(directo);

                                return {
                                    ok: true,
                                    metodo:
                                        directo.id ||
                                        directo.getAttribute("title") ||
                                        "title"
                                };
                            }

                            const botonesMenu = Array.from(
                                document.querySelectorAll(
                                    ".options-icon button, " +
                                    "button.fa-ellipsis-v, " +
                                    "button[data-bs-toggle='dropdown']"
                                )
                            ).filter(visible);

                            if (botonesMenu.length > 0) {
                                disparar(botonesMenu[0]);

                                return {
                                    ok: false,
                                    menuAbierto: true,
                                    metodo: "menu-tres-puntos"
                                };
                            }

                            const opcionTexto = Array.from(
                                document.querySelectorAll(
                                    "button, .dropdown-item, span"
                                )
                            ).find(elemento => {
                                if (!visible(elemento)) {
                                    return false;
                                }

                                const texto = String(
                                    elemento.innerText ||
                                    elemento.textContent ||
                                    ""
                                )
                                .replace(/\\s+/g, " ")
                                .trim()
                                .toLowerCase();

                                return (
                                    texto ===
                                    "analizador de espectro"
                                );
                            });

                            if (opcionTexto) {
                                disparar(opcionTexto);

                                return {
                                    ok: true,
                                    metodo:
                                        "texto-analizador-espectro"
                                };
                            }

                            return {
                                ok: false,
                                menuAbierto: false,
                                metodo: null
                            };
                        }""")

                    if resultado_click and resultado_click.get("ok"):
                        click_spectrum = True
                        metodo_spectrum = resultado_click.get("metodo")
                        break

                    if resultado_click and resultado_click.get("menuAbierto"):
                        frame.wait_for_timeout(700)

                        resultado_menu = frame.evaluate("""() => {
                                const visible = elemento => {
                                    if (!elemento) {
                                        return false;
                                    }

                                    const estilo =
                                        window.getComputedStyle(
                                            elemento
                                        );

                                    const rect =
                                        elemento.getBoundingClientRect();

                                    return (
                                        estilo.display !== "none" &&
                                        estilo.visibility !== "hidden" &&
                                        rect.width > 0 &&
                                        rect.height > 0
                                    );
                                };

                                const opcion = Array.from(
                                    document.querySelectorAll(
                                        ".dropdown-menu button, " +
                                        ".dropdown-item, button"
                                    )
                                ).find(elemento => {
                                    if (!visible(elemento)) {
                                        return false;
                                    }

                                    const texto = String(
                                        elemento.innerText ||
                                        elemento.textContent ||
                                        ""
                                    )
                                    .replace(/\\s+/g, " ")
                                    .trim()
                                    .toLowerCase();

                                    return texto ===
                                        "analizador de espectro";
                                });

                                if (!opcion) {
                                    return false;
                                }

                                const objetivo =
                                    opcion.closest("button") ||
                                    opcion;

                                objetivo.click();

                                return true;
                            }""")

                        if resultado_menu:
                            click_spectrum = True
                            metodo_spectrum = "menu:Analizador de espectro"
                            break

                except Exception as exc:
                    print(f"[{nombre_region}] Frame Spectrum: " f"{exc}")

            if click_spectrum:
                break

            qoe_page.wait_for_timeout(700)

        if not click_spectrum:
            debug = _debug_screenshot(
                qoe_page,
                f"click_js_spectrum_fallo_{nodo}",
            )

            raise RuntimeError(
                "No fue posible ejecutar el clic DOM sobre "
                "Analizador de espectro. "
                f"Debug: {debug}"
            )

        print(f"[{nombre_region}] Clic Spectrum ejecutado: " f"{metodo_spectrum}")

        # La tercera pÃ¡gina puede tardar unos segundos en aparecer.
        spectrum_page = None
        deadline_spectrum = time.monotonic() + 25

        while time.monotonic() < deadline_spectrum:
            paginas_actuales = [
                pagina for pagina in context.pages if not pagina.is_closed()
            ]

            nuevas = [
                pagina
                for pagina in paginas_actuales
                if pagina not in paginas_antes_spectrum
            ]

            for pagina in reversed(nuevas):
                try:
                    actual = str(pagina.url or "").lower()

                    if "/pathtrak/live/index.html" in actual:
                        spectrum_page = pagina
                        break

                except Exception:
                    continue

            if spectrum_page is not None:
                break

            qoe_page.wait_for_timeout(500)

        if spectrum_page is None:
            debug = _debug_screenshot(
                qoe_page,
                f"spectrum_tercera_pagina_no_abierta_{nodo}",
            )

            raise RuntimeError(
                "Se pulsÃ³ el analizador de espectro, "
                "pero PathTrak no abriÃ³ la tercera pÃ¡gina. "
                f"Debug: {debug}"
            )

        spectrum_page.bring_to_front()

        try:
            spectrum_page.wait_for_load_state(
                "domcontentloaded",
                timeout=30000,
            )
        except Exception:
            pass

        # ---------------------------------------------------------
        # Error/modal temporal al entrar al Analizador de espectro.
        # ---------------------------------------------------------
        #
        # PathTrak puede abrir correctamente el Analizador y mostrar
        # temporalmente un modal como:
        #
        #   "No RCI found with the id specified."
        #
        # No lo tratamos como fallo definitivo de inmediato:
        #   1. Pulsamos Aceptar.
        #   2. Esperamos como mÃ¡ximo 10 segundos.
        #   3. Si la grÃ¡fica termina de cargar, continuamos normal.
        #   4. Si no carga, guardamos igualmente una captura del
        #      estado actual (por ejemplo "Cargando...").
        spectrum_page.wait_for_timeout(2500)

        try:
            estado_error_spectrum = spectrum_page.evaluate(r"""() => {
                    const visible = elemento => {
                        if (!elemento) {
                            return false;
                        }

                        const estilo =
                            window.getComputedStyle(elemento);

                        const rect =
                            elemento.getBoundingClientRect();

                        return (
                            estilo.display !== "none" &&
                            estilo.visibility !== "hidden" &&
                            estilo.opacity !== "0" &&
                            rect.width > 0 &&
                            rect.height > 0
                        );
                    };

                    const texto = String(
                        document.body?.innerText || ""
                    )
                    .replace(/\s+/g, " ")
                    .trim()
                    .toLowerCase();

                    const modales = Array.from(
                        document.querySelectorAll(
                            ".swal2-container, " +
                            ".swal2-popup, " +
                            ".modal, " +
                            "[role='dialog']"
                        )
                    ).filter(visible);

                    const botonesAceptar = Array.from(
                        document.querySelectorAll("button")
                    ).filter(elemento => {
                        if (!visible(elemento)) {
                            return false;
                        }

                        const valor = String(
                            elemento.innerText ||
                            elemento.textContent ||
                            ""
                        )
                        .replace(/\s+/g, " ")
                        .trim()
                        .toLowerCase();

                        return (
                            valor === "aceptar" ||
                            valor === "ok"
                        );
                    });

                    return {
                        noRci: (
                            texto.includes(
                                "no rci found with the id specified"
                            )
                            || texto.includes(
                                "no rci found"
                            )
                        ),
                        modalVisible: modales.length > 0,
                        botonAceptarVisible:
                            botonesAceptar.length > 0,
                        texto: texto.slice(0, 2500)
                    };
                }""")
        except Exception:
            estado_error_spectrum = {
                "noRci": False,
                "modalVisible": False,
                "botonAceptarVisible": False,
                "texto": "",
            }

        error_temporal_spectrum = bool(
            estado_error_spectrum.get("noRci")
            or (
                estado_error_spectrum.get("modalVisible")
                and estado_error_spectrum.get("botonAceptarVisible")
            )
        )

        if error_temporal_spectrum:
            print(
                f"[{nombre_region}] Aviso/error temporal "
                f"detectado al abrir Spectrum para {nodo}."
            )

            # -----------------------------------------------------
            # EVIDENCIA 1: guardar el SweetAlert antes de cerrarlo.
            # -----------------------------------------------------
            error_out = SCREENSHOT_DIR / (
                f"ondas_{_safe_name(nombre_region)}_"
                f"{_safe_name(nodo)}_ERROR_RCI_"
                f"{_stamp()}.png"
            )

            try:
                spectrum_page.set_viewport_size(
                    {
                        "width": 1920,
                        "height": 1080,
                    }
                )
            except Exception:
                pass

            spectrum_page.screenshot(
                path=str(error_out),
                full_page=False,
            )

            print(
                f"[{nombre_region}] Evidencia del error RCI " f"guardada: {error_out}"
            )

            # -----------------------------------------------------
            # Cerrar especÃ­ficamente el SweetAlert real:
            #
            # <button class="swal2-confirm swal2-styled">
            #     Aceptar
            # </button>
            # -----------------------------------------------------
            modal_cerrado = False

            try:
                popup = spectrum_page.locator(".swal2-popup").first

                boton_aceptar = spectrum_page.locator("button.swal2-confirm").first

                if popup.count() > 0 and popup.is_visible(timeout=1500):
                    texto_popup = (
                        popup.inner_text(timeout=1500)
                        .replace("\n", " ")
                        .strip()
                        .lower()
                    )

                    if (
                        "no rci found with the id specified" in texto_popup
                        or "no rci found" in texto_popup
                    ):
                        boton_aceptar.wait_for(
                            state="visible",
                            timeout=3000,
                        )

                        boton_aceptar.click(
                            timeout=4000,
                            force=True,
                        )

                        try:
                            popup.wait_for(
                                state="hidden",
                                timeout=3000,
                            )
                        except Exception:
                            pass

                        modal_cerrado = True

                        print(
                            f"[{nombre_region}] SweetAlert "
                            f"No RCI aceptado para {nodo}."
                        )

            except Exception as exc:
                print(
                    f"[{nombre_region}] No pude cerrar "
                    f"SweetAlert RCI con selector exacto: "
                    f"{exc}"
                )

            # Fallback general si cambia ligeramente el modal.
            if not modal_cerrado:
                try:
                    modal_cerrado = aceptar_cualquier_modal_pathtrak(
                        spectrum_page,
                        motivo=(f"spectrum temporal nodo {nodo}"),
                    )
                except Exception as exc:
                    print(
                        f"[{nombre_region}] Fallback general "
                        f"de modal Spectrum fallÃ³: {exc}"
                    )

            # Ãšltimo fallback: Enter.
            if not modal_cerrado:
                try:
                    spectrum_page.keyboard.press("Enter")
                    spectrum_page.wait_for_timeout(500)

                    popup_visible_despues = False

                    try:
                        popup_visible_despues = spectrum_page.locator(
                            ".swal2-popup"
                        ).first.is_visible(timeout=700)
                    except Exception:
                        popup_visible_despues = False

                    modal_cerrado = not popup_visible_despues

                    print(
                        f"[{nombre_region}] Enter enviado "
                        f"como fallback. Cerrado="
                        f"{modal_cerrado}"
                    )
                except Exception:
                    pass

            # -----------------------------------------------------
            # Esperar EXACTAMENTE hasta 10 segundos para ver si
            # Spectrum consigue presentar datos Ãºtiles.
            #
            # IMPORTANTE:
            # Aunque la interfaz de Highcharts exista, NO la damos
            # por buena si muestra "Datos no disponibles" o sigue
            # mostrando un loader. Esto evita guardar la captura
            # vacÃ­a que el usuario indicÃ³ como innecesaria.
            # -----------------------------------------------------
            print(
                f"[{nombre_region}] Esperando hasta 10 segundos "
                f"despuÃ©s de Aceptar para {nodo}..."
            )

            deadline_recuperacion = time.monotonic() + 10
            grafica_util = False
            ultimo_estado_recuperacion = {}

            while time.monotonic() < deadline_recuperacion:
                try:
                    ultimo_estado_recuperacion = spectrum_page.evaluate(r"""() => {
                                const visible = elemento => {
                                    if (!elemento) {
                                        return false;
                                    }

                                    const estilo =
                                        window.getComputedStyle(
                                            elemento
                                        );

                                    const rect =
                                        elemento
                                        .getBoundingClientRect();

                                    return (
                                        estilo.display !== "none" &&
                                        estilo.visibility !== "hidden" &&
                                        estilo.opacity !== "0" &&
                                        rect.width > 0 &&
                                        rect.height > 0
                                    );
                                };

                                const texto = String(
                                    document.body?.innerText || ""
                                )
                                .replace(/\s+/g, " ")
                                .trim()
                                .toLowerCase();

                                const loaders = Array.from(
                                    document.querySelectorAll(
                                        ".highcharts-loading, " +
                                        ".spinner, " +
                                        ".loading, " +
                                        "[class*='loading'], " +
                                        "[class*='spinner']"
                                    )
                                ).filter(visible);

                                const series = Array.from(
                                    document.querySelectorAll(
                                        ".highcharts-series, " +
                                        ".highcharts-series-group " +
                                        "path.highcharts-graph, " +
                                        ".highcharts-series-group " +
                                        "path.highcharts-point, " +
                                        "canvas"
                                    )
                                ).filter(visible);

                                const modalVisible = Array.from(
                                    document.querySelectorAll(
                                        ".swal2-container, " +
                                        ".swal2-popup, " +
                                        ".modal, " +
                                        "[role='dialog']"
                                    )
                                ).some(visible);

                                const cargandoTexto = (
                                    texto.includes("cargando...") ||
                                    texto.includes("cargando") ||
                                    texto.includes("loading") ||
                                    texto.includes(
                                        "por favor espere"
                                    )
                                );

                                const sinDatos = (
                                    texto.includes(
                                        "datos no disponibles"
                                    ) ||
                                    texto.includes(
                                        "no data available"
                                    ) ||
                                    texto.includes(
                                        "sin datos"
                                    )
                                );

                                return {
                                    seriesVisible:
                                        series.length > 0,
                                    loadingVisible: (
                                        loaders.length > 0 ||
                                        cargandoTexto
                                    ),
                                    sinDatos,
                                    modalVisible,
                                    texto:
                                        texto.slice(0, 2500)
                                };
                            }""")

                except Exception as exc:
                    ultimo_estado_recuperacion = {
                        "seriesVisible": False,
                        "loadingVisible": True,
                        "sinDatos": False,
                        "modalVisible": False,
                        "error": str(exc),
                    }

                print(
                    f"[{nombre_region}] RecuperaciÃ³n "
                    f"Spectrum {nodo} | "
                    f"series="
                    f"{ultimo_estado_recuperacion.get('seriesVisible')} | "
                    f"loading="
                    f"{ultimo_estado_recuperacion.get('loadingVisible')} | "
                    f"sinDatos="
                    f"{ultimo_estado_recuperacion.get('sinDatos')} | "
                    f"modal="
                    f"{ultimo_estado_recuperacion.get('modalVisible')}"
                )

                grafica_util = bool(
                    ultimo_estado_recuperacion.get("seriesVisible")
                    and not ultimo_estado_recuperacion.get("loadingVisible")
                    and not ultimo_estado_recuperacion.get("sinDatos")
                    and not ultimo_estado_recuperacion.get("modalVisible")
                )

                if grafica_util:
                    break

                spectrum_page.wait_for_timeout(1000)

            # -----------------------------------------------------
            # EVIDENCIA 2:
            # despuÃ©s de los 10 s (o antes si aparece una grÃ¡fica
            # realmente Ãºtil), guardamos UNA sola captura final.
            #
            # Este return es deliberado: evita que el flujo siga mÃ¡s
            # abajo y cree una tercera captura Spectrum vacÃ­a.
            # -----------------------------------------------------
            if grafica_util:
                estado_final = "RECUPERADO"
                sufijo_final = "RECUPERADO"
                estado_label = "Spectrum recuperado despuÃ©s del aviso RCI"
                mensaje_final = (
                    "PathTrak mostrÃ³ el aviso No RCI, se pulsÃ³ "
                    "Aceptar y la grÃ¡fica logrÃ³ cargar dentro de "
                    "los 10 segundos."
                )
            else:
                estado_final = "CARGANDO_TIMEOUT"
                sufijo_final = "CARGANDO"
                estado_label = "Spectrum sin datos Ãºtiles despuÃ©s de 10 segundos"
                mensaje_final = (
                    "PathTrak mostrÃ³ el aviso No RCI. Se pulsÃ³ "
                    "Aceptar y se esperaron 10 segundos. La vista "
                    "no presentÃ³ datos Ãºtiles; se adjunta la "
                    "evidencia del estado posterior al aviso."
                )

            post_error_out = SCREENSHOT_DIR / (
                f"ondas_{_safe_name(nombre_region)}_"
                f"{_safe_name(nodo)}_{sufijo_final}_"
                f"{_stamp()}.png"
            )

            try:
                spectrum_page.set_viewport_size(
                    {
                        "width": 1920,
                        "height": 1080,
                    }
                )
            except Exception:
                pass

            spectrum_page.screenshot(
                path=str(post_error_out),
                full_page=False,
            )

            print(
                f"[{nombre_region}] Evidencia Spectrum posterior "
                f"al aviso guardada: {post_error_out}"
            )

            print(
                f"[{nombre_region}] No se generarÃ¡ captura "
                "Spectrum normal adicional para este caso."
            )

            ruido_capture = {
                "ok": True,
                "tipo": "ondas",
                "tipo_label": "Ruido / Spectrum",
                "region": nombre_region,
                "nodo": nodo,
                "estado": estado_final,
                "estado_label": estado_label,
                "mensaje": mensaje_final,
                "detalle": (
                    estado_error_spectrum.get("texto")
                    or "No RCI found with the id specified."
                ),
                "url": spectrum_page.url,
                # Captura principal posterior a los 10 segundos.
                "screenshot": str(post_error_out),
                # Evidencia adicional del popup original.
                "error_screenshot": str(error_out),
                "modal_aceptado": bool(modal_cerrado),
                "espera_reintento_seg": 10,
                "grafica_recuperada": bool(grafica_util),
                "estado_recuperacion": (ultimo_estado_recuperacion),
            }

            # Si la solicitud original fue qoe_ruido, la captura QoE
            # ya se generÃ³ antes de entrar a Spectrum. La devolvemos
            # junto con las DOS evidencias de Spectrum:
            #
            #   1. QoE del nodo.
            #   2. Popup/error No RCI.
            #   3. Estado del Spectrum despuÃ©s de esperar 10 s.
            #
            # El return aquÃ­ evita que mÃ¡s abajo se genere una cuarta
            # captura Spectrum normal/vacÃ­a.
            if tipo == "qoe_ruido":
                screenshots = []

                if qoe_capture and qoe_capture.get("screenshot"):
                    screenshots.append(qoe_capture.get("screenshot"))

                screenshots.append(str(error_out))
                screenshots.append(str(post_error_out))

                return {
                    "ok": True,
                    "tipo": "qoe_ruido",
                    "region": nombre_region,
                    "nodo": nodo,
                    "estado": ("OK" if grafica_util else "PARCIAL"),
                    # Se conserva el contrato qoe/ruido existente.
                    "capturas": {
                        "qoe": qoe_capture,
                        "ruido": ruido_capture,
                    },
                    # Lista explÃ­cita de las TRES evidencias.
                    # Esto facilita que cualquier capa superior pueda
                    # mostrarlas sin tener que inferir error_screenshot.
                    "evidencias": [
                        {
                            "tipo": "qoe",
                            "tipo_label": "QoE / Estado del nodo",
                            "screenshot": qoe_capture.get("screenshot"),
                            "url": qoe_capture.get("url"),
                        },
                        {
                            "tipo": "ondas_error_rci",
                            "tipo_label": ("Ruido / Error RCI"),
                            "screenshot": str(error_out),
                            "url": spectrum_page.url,
                        },
                        {
                            "tipo": "ondas_post_10s",
                            "tipo_label": ("Ruido / Estado despuÃ©s de 10 s"),
                            "screenshot": str(post_error_out),
                            "url": spectrum_page.url,
                        },
                    ],
                    # Orden garantizado:
                    # 1 QoE, 2 error, 3 estado posterior.
                    "screenshots": screenshots,
                    "urls": {
                        "qoe": qoe_capture.get("url"),
                        "ruido": spectrum_page.url,
                    },
                    "mensaje": ("QoE capturado correctamente. " + mensaje_final),
                }

            # Solicitud Ãºnicamente de ondas.
            return ruido_capture

        deadline_url = time.monotonic() + 25

        while time.monotonic() < deadline_url:
            actual = str(spectrum_page.url or "").lower()

            print(f"[{nombre_region}] Esperando Spectrum: " f"{actual}")

            es_spectrum = "/pathtrak/live/index.html" in actual and (
                ("#/app/ea" in actual and "port=" in actual)
                or (
                    "#/app/spectrum" in actual
                    and ("hcu=" in actual or "cmts_us_port=" in actual)
                )
            )

            if es_spectrum:
                break

            spectrum_page.wait_for_timeout(500)

        actual = str(spectrum_page.url or "").lower()

        es_spectrum = "/pathtrak/live/index.html" in actual and (
            ("#/app/ea" in actual and "port=" in actual)
            or (
                "#/app/spectrum" in actual
                and ("hcu=" in actual or "cmts_us_port=" in actual)
            )
        )

        if not es_spectrum:
            debug = _debug_screenshot(
                spectrum_page,
                f"spectrum_url_incorrecta_{nodo}",
            )

            raise RuntimeError(
                "La tercera pÃ¡gina abriÃ³, pero no llegÃ³ "
                "al Analizador de espectro. "
                f"URL: {spectrum_page.url}. "
                f"Debug: {debug}"
            )

        print(f"[{nombre_region}] Tercera pÃ¡gina Spectrum: " f"{spectrum_page.url}")

        # La grÃ¡fica se carga de forma asÃ­ncrona.
        # Esperamos un momento fijo antes de capturar.
        spectrum_page.wait_for_timeout(12000)

        ruido_out = SCREENSHOT_DIR / (
            f"ondas_{_safe_name(nombre_region)}_" f"{_safe_name(nodo)}_{_stamp()}.png"
        )

        print(f"[{nombre_region}] Inicio captura Spectrum nodo={nodo}.")
        spectrum_page.set_viewport_size(
            {
                "width": 1920,
                "height": 1080,
            }
        )

        spectrum_page.screenshot(
            path=str(ruido_out),
            full_page=False,
        )
        print(f"[{nombre_region}] Fin Spectrum; screenshot guardado: {ruido_out}")

        ruido_capture = {
            "ok": True,
            "tipo": "ondas",
            "tipo_label": "Ruido / Spectrum",
            "region": nombre_region,
            "nodo": nodo,
            "url": spectrum_page.url,
            "screenshot": str(ruido_out),
        }

        if tipo == "qoe_ruido":
            return {
                "ok": True,
                "tipo": "qoe_ruido",
                "region": nombre_region,
                "nodo": nodo,
                "capturas": {
                    "qoe": qoe_capture,
                    "ruido": ruido_capture,
                },
                "screenshots": [
                    qoe_capture.get("screenshot"),
                    ruido_capture.get("screenshot"),
                ],
                "urls": {
                    "qoe": qoe_capture.get("url"),
                    "ruido": ruido_capture.get("url"),
                },
            }

        return ruido_capture

    except Exception as exc:
        debug_page = spectrum_page or qoe_page or dashboard

        debug = _debug_screenshot(
            debug_page,
            f"{nombre_region}_{nodo}_{tipo}_error",
        )

        return {
            "ok": False,
            "tipo": tipo,
            "region": nombre_region,
            "nodo": nodo,
            "error": str(exc),
            "debug": debug,
        }

    finally:
        try:
            context.close()
        except Exception:
            pass

        try:
            browser.close()
        except Exception:
            pass


def capturar_spectrum(nodo, tipo_captura="ondas"):
    inicio_total = time.monotonic()

    nodo = _normalizar_nodo(nodo)

    tipo_normalizado = str(tipo_captura or "ondas").strip().lower()

    if tipo_normalizado in {
        "ambas",
        "ambos",
        "qoe+ruido",
        "qoe_ruido",
        "qoe-y-ruido",
        "qoe_ondas",
    }:
        tipo_normalizado = "qoe_ruido"

    elif tipo_normalizado == "ruido":
        tipo_normalizado = "ondas"

    if not PATHTRAK_USER or not PATHTRAK_PASSWORD:
        return {
            "ok": False,
            "tipo": tipo_normalizado,
            "nodo": nodo,
            "error": ("Faltan PATHTRAK_USER o " "PATHTRAK_PASSWORD en el .env"),
        }

    urls = [
        (
            "CENTRO",
            PATHTRAK_CENTRO_URL,
        ),
        (
            "REGIONALES",
            PATHTRAK_REGIONALES_URL,
        ),
    ]

    only_region = (
        os.getenv(
            "PATHTRAK_ONLY_REGION",
            "",
        )
        .strip()
        .upper()
    )

    region_preferida = ""

    if only_region:
        urls = [(nombre, url) for nombre, url in urls if nombre.upper() == only_region]

        if not urls:
            return {
                "ok": False,
                "tipo": tipo_normalizado,
                "nodo": nodo,
                "error": (
                    f"PATHTRAK_ONLY_REGION="
                    f"{only_region} no coincide "
                    "con CENTRO o REGIONALES."
                ),
            }

        region_preferida = only_region

        print("[PATHTRAK] RegiÃ³n forzada por ambiente: " f"{only_region}")

    else:
        urls, region_preferida = ordenar_urls_por_region_cache(
            nodo=nodo,
            urls=urls,
        )

    print(
        f"[PATHTRAK] Inicio captura nodo={nodo} "
        f"tipo={tipo_normalizado} "
        f"orden={[nombre for nombre, _ in urls]}"
    )

    errores = []
    metricas_regiones = []

    with sync_playwright() as playwright:
        for nombre_region, url in urls:
            inicio_region = time.monotonic()

            print(f"[PATHTRAK] Probando regiÃ³n " f"{nombre_region} para nodo {nodo}.")

            try:
                result = capturar_spectrum_en_url(
                    playwright=playwright,
                    url=url,
                    nodo=nodo,
                    nombre_region=nombre_region,
                    tipo_captura=tipo_normalizado,
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "tipo": tipo_normalizado,
                    "nodo": nodo,
                    "region": nombre_region,
                    "error": f"{type(exc).__name__}: {exc}",
                }

            duracion_region = round(
                time.monotonic() - inicio_region,
                2,
            )

            metrica_region = {
                "region": nombre_region,
                "duracion_segundos": duracion_region,
                "ok": bool(isinstance(result, dict) and result.get("ok")),
            }

            metricas_regiones.append(metrica_region)

            print(
                f"[PATHTRAK] Fin regiÃ³n "
                f"{nombre_region} | "
                f"ok={metrica_region['ok']} | "
                f"duraciÃ³n={duracion_region}s"
            )

            if not isinstance(result, dict):
                result = {
                    "ok": False,
                    "tipo": tipo_normalizado,
                    "nodo": nodo,
                    "region": nombre_region,
                    "error": (
                        "La funciÃ³n interna de PathTrak "
                        "no devolviÃ³ un resultado vÃ¡lido."
                    ),
                }

            if result.get("ok"):
                guardar_region_cache(
                    nodo=nodo,
                    region=nombre_region,
                )

                result["region_preferida"] = region_preferida or None

                result["metricas"] = {
                    "total_segundos": round(
                        time.monotonic() - inicio_total,
                        2,
                    ),
                    "regiones": metricas_regiones,
                    "cache_usada": bool(region_preferida),
                }

                print(
                    f"[PATHTRAK] Captura completada "
                    f"nodo={nodo} "
                    f"region={nombre_region} "
                    f"total="
                    f"{result['metricas']['total_segundos']}s"
                )

                return result

            error_region = {
                key: value for key, value in result.items() if key != "errores"
            }

            error_region["duracion_segundos"] = duracion_region

            errores.append(error_region)

            error_txt = str(result.get("error") or "").lower()

            nodo_encontrado_pero_no_cargo = any(
                marker in error_txt
                for marker in [
                    "fue encontrado y seleccionado",
                    "fue seleccionado",
                    "vista quedÃ³ en dashboard",
                    "dashboard/cargando",
                    "no terminÃ³ de cargar",
                    "nodoecontradopero",
                    "nodoencontradoperovistanocarga",
                ]
            )

            if nodo_encontrado_pero_no_cargo:
                print(
                    f"[PATHTRAK] {nombre_region} encontrÃ³ {nodo}, pero no cargÃ³ la vista; se probarÃ¡ la siguiente regiÃ³n."
                )

            # PATHTRAK_FALLBACK_REGIONALES_V3
            no_estaba_en_region = any(
                marker in error_txt
                for marker in [
                    "no se encontraron datos",
                    "no pude seleccionar el nodo",
                    "no contiene el nodo",
                    "no aparece como resultado tipo nodo",
                    "no apareciÃ³ nodo",
                    "no aparecio nodo",
                    "lista apareciÃ³, pero no contiene",
                    "probablemente solo hay mÃ³dems",
                    "probablemente solo hay modems",
                ]
            )

            if no_estaba_en_region:
                if region_preferida and nombre_region.upper() == region_preferida:
                    print(
                        f"[PATHTRAK] La regiÃ³n guardada "
                        f"{region_preferida} ya no contiene "
                        f"el nodo {nodo}. Se probarÃ¡ "
                        "la regiÃ³n alternativa."
                    )

                continue

            print(
                f"[PATHTRAK] {nombre_region} fallÃ³; se probarÃ¡ la siguiente regiÃ³n si estÃ¡ disponible."
            )

    return {
        "ok": False,
        "tipo": tipo_normalizado,
        "nodo": nodo,
        "error": ("No se pudo capturar en " "Centro ni Regionales."),
        "errores": errores,
        "metricas": {
            "total_segundos": round(
                time.monotonic() - inicio_total,
                2,
            ),
            "regiones": metricas_regiones,
            "cache_usada": bool(region_preferida),
        },
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("nodo", help="Nodo a consultar. Ej: SRL3B")
    parser.add_argument(
        "--tipo",
        default="ondas",
        choices=["ondas", "qoe", "estado", "nodo"],
        help="Tipo de captura: ondas o qoe",
    )

    args = parser.parse_args()

    resultado = capturar_spectrum(args.nodo, tipo_captura=args.tipo)
    print(resultado)

