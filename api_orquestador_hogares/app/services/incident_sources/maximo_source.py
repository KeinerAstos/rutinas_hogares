import os
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


MAXIMO_URL = os.getenv(
    "MAXIMO_URL",
    "",
).strip()

MAXIMO_USER = os.getenv("MAXIMO_USER", "").strip()
MAXIMO_PASSWORD = os.getenv("MAXIMO_PASSWORD", "").strip()

MAXIMO_HEADLESS = os.getenv("MAXIMO_HEADLESS", "0").strip() in ("1", "true", "TRUE", "SI", "YES")
MAXIMO_SLOWMO = int(os.getenv("MAXIMO_SLOWMO", "100") or "100")
MAXIMO_TIMEOUT = int(os.getenv("MAXIMO_TIMEOUT", "60000") or "60000")


ESTADOS_CERRADOS = {
    "CAN",
    "CLOSE",
    "CLOSED",
    "CERRADO",
    "CANCELADO",
    "RESOLVED",
    "RESUELTO",
}


def limpiar_texto(txt):
    if not txt:
        return ""

    return (
        str(txt)
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .strip()
    )


def normalizar(txt):
    return limpiar_texto(txt).upper()


def es_estado_valido_para_consulta(estado):
    estado = normalizar(estado)
    return estado and estado not in ESTADOS_CERRADOS


class MaximoSource:
    """
    Fuente de incidentes basada en scraping de Máximo.

    Esta clase NO hace SSH.
    Solo busca el INC en Máximo y devuelve nodos/OTs relacionados.

    El diagnóstico SSH lo sigue haciendo DiagnosticService.
    """

    def __init__(self):
        self.nombre = "MAXIMO"

    def buscar_incidente(self, tt_number):
        tt_number = normalizar(tt_number)

        if not MAXIMO_USER or not MAXIMO_PASSWORD:
            return {
                "ok": False,
                "fuente": self.nombre,
                "tt_number": tt_number,
                "error": "Faltan MAXIMO_USER o MAXIMO_PASSWORD en .env.",
                "registros": [],
                "nodos": [],
            }

        if not MAXIMO_URL:
            return {
                "ok": False,
                "fuente": self.nombre,
                "tt_number": tt_number,
                "error": "Falta MAXIMO_URL en .env.",
                "registros": [],
                "nodos": [],
            }

        browser = None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=MAXIMO_HEADLESS,
                    slow_mo=MAXIMO_SLOWMO,
                    args=["--start-maximized"],
                )

                page = browser.new_page(no_viewport=True)

                print(f"[MAXIMO] Abriendo Máximo...")
                page.goto(MAXIMO_URL, wait_until="domcontentloaded", timeout=MAXIMO_TIMEOUT)

                self._login(page)
                self._volver_a_incidencias_claro(page)

                print(f"[MAXIMO] Buscando incidente: {tt_number}")
                self._buscar_incidente_en_maximo(page, tt_number)

                detalle_incidente = self._extraer_detalle_incidente(page, tt_number)

                self._ir_registros_relacionados(page)

                tickets = self._extraer_tickets_relacionados(page)
                ordenes = self._extraer_ordenes_relacionadas(page)
                alarmas = self._extraer_alarmas_relacionadas(page)

                browser.close()
                browser = None

                nodos = self._extraer_nodos_desde_ordenes(ordenes)

                return {
                    "ok": True,
                    "fuente": self.nombre,
                    "tt_number": tt_number,
                    "detalle_incidente": detalle_incidente,
                    "tickets_relacionados": tickets,
                    "ordenes_relacionadas": ordenes,
                    "alarmas_relacionadas": alarmas,
                    "registros": ordenes,
                    "nodos": nodos,
                    "total_registros": len(ordenes),
                    "total_tickets_relacionados": len(tickets),
                    "total_ordenes_relacionadas": len(ordenes),
                    "total_alarmas_relacionadas": len(alarmas),
                }

        except Exception as e:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

            return {
                "ok": False,
                "fuente": self.nombre,
                "tt_number": tt_number,
                "error": f"Error consultando Máximo: {e}",
                "registros": [],
                "nodos": [],
            }

    def _login(self, page):
        print("[MAXIMO] Ingresando usuario...")
        page.locator("#j_username").wait_for(state="visible", timeout=MAXIMO_TIMEOUT)
        page.fill("#j_username", MAXIMO_USER)

        print("[MAXIMO] Ingresando contraseña...")
        page.locator("#j_password").wait_for(state="visible", timeout=MAXIMO_TIMEOUT)
        page.fill("#j_password", MAXIMO_PASSWORD)

        print("[MAXIMO] Enviando login...")
        page.press("#j_password", "Enter")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=MAXIMO_TIMEOUT)
        except PlaywrightTimeoutError:
            pass

        time.sleep(5)

    def _esperar_y_click(self, page, selector, nombre, timeout=None):
        timeout = timeout or MAXIMO_TIMEOUT

        print(f"[MAXIMO] Esperando botón: {nombre}")
        page.locator(selector).wait_for(state="visible", timeout=timeout)
        page.locator(selector).click()
        print(f"[MAXIMO] Click OK: {nombre}")

    def _esperar_y_fill(self, page, selector, texto, nombre, timeout=None):
        timeout = timeout or MAXIMO_TIMEOUT

        print(f"[MAXIMO] Esperando campo: {nombre}")
        page.locator(selector).wait_for(state="visible", timeout=timeout)
        page.locator(selector).click()
        page.locator(selector).fill("")
        page.locator(selector).fill(texto)
        print(f"[MAXIMO] Texto escrito en {nombre}: {texto}")

    def _volver_a_incidencias_claro(self, page):
        print("[MAXIMO] Entrando a Incidencias Claro...")

        self._esperar_y_click(
            page,
            "#titlebar-tb_homeButton",
            "Centro de inicio",
            timeout=MAXIMO_TIMEOUT,
        )

        time.sleep(3)

        self._esperar_y_click(
            page,
            "#FavoriteApp_INCIDENT",
            "Incidencias Claro",
            timeout=MAXIMO_TIMEOUT,
        )

        time.sleep(5)

    def _buscar_incidente_en_maximo(self, page, tt_number):
        self._esperar_y_fill(
            page,
            "#quicksearch",
            tt_number,
            "Buscar Incidencia",
            timeout=MAXIMO_TIMEOUT,
        )

        time.sleep(1)

        self._esperar_y_click(
            page,
            "#quicksearchQSImage",
            "Botón buscar incidencia",
            timeout=MAXIMO_TIMEOUT,
        )

        print(f"[MAXIMO] Búsqueda enviada para: {tt_number}")
        time.sleep(8)

    def _ir_registros_relacionados(self, page):
        print("[MAXIMO] Entrando a Registros relacionados...")

        tab = page.locator("a[role='tab']", has_text="Registros relacionados")
        tab.wait_for(state="visible", timeout=MAXIMO_TIMEOUT)
        tab.click()

        time.sleep(4)

    def _extraer_ordenes_relacionadas(self, page):
        """
        Lee SOLO la segunda tabla: Órdenes de trabajo relacionadas.
        No lee la tabla superior de Tickets relacionados.
        """

        print("[MAXIMO] Leyendo tabla de Órdenes de trabajo relacionadas...")

        titulo = page.locator("text=Órdenes de trabajo relacionadas").last
        titulo.wait_for(state="visible", timeout=MAXIMO_TIMEOUT)

        tabla = titulo.locator(
            "xpath=following::table[contains(@id, '_tbod-tbd')][1]"
        )

        tabla.wait_for(state="visible", timeout=MAXIMO_TIMEOUT)

        filas = tabla.locator("tr[id*='_tbod_tdrow-tr[R:']")
        total = filas.count()

        print(f"[MAXIMO] Filas detectadas en Órdenes relacionadas: {total}")

        ordenes = []
        vistos = set()

        for i in range(total):
            fila = filas.nth(i)
            fila_id = fila.get_attribute("id")

            if not fila_id or "[R:" not in fila_id:
                continue

            row_num = fila_id.split("[R:")[1].replace("]", "")

            def valor_columna(col):
                celda = tabla.locator(f"[id*='_tdrow_[C:{col}]-c[R:{row_num}]']")

                if celda.count() == 0:
                    return ""

                celda = celda.first
                input_col = celda.locator("input")

                if input_col.count() > 0:
                    return limpiar_texto(input_col.first.get_attribute("value"))

                try:
                    return limpiar_texto(celda.inner_text(timeout=5000))
                except Exception:
                    return ""

            orden = {
                "orden_trabajo": valor_columna(1),
                "descripcion": valor_columna(2),
                "clase": valor_columna(3),
                "tipo_trabajo": valor_columna(4),
                "ubicacion": valor_columna(5),
                "articulo_configuracion": valor_columna(6),
                "fecha_creacion": valor_columna(7),
                "relacionado_por": valor_columna(8),
                "fecha_relacion": valor_columna(9),
                "resolutor": valor_columna(10),
                "estado": valor_columna(11),
            }

            clave = (
                orden["orden_trabajo"],
                orden["descripcion"],
                orden["clase"],
                orden["tipo_trabajo"],
                orden["ubicacion"],
                orden["articulo_configuracion"],
            )

            if clave in vistos:
                continue

            vistos.add(clave)

            if orden["orden_trabajo"]:
                ordenes.append(orden)

        return ordenes

    def _valor_por_label(self, page, label_text):
        """
        Extrae el valor de un campo de Máximo buscando por el texto del label.
        Funciona aunque los IDs dinámicos cambien.
        """
        try:
            return page.evaluate(
                """
                (labelText) => {
                    const norm = (s) => (s || '')
                        .replace(/\\s+/g, ' ')
                        .replace(':', '')
                        .trim()
                        .toUpperCase();

                    const wanted = norm(labelText);
                    const labels = Array.from(document.querySelectorAll('label'));

                    for (const lb of labels) {
                        if (norm(lb.innerText) !== wanted) continue;

                        const forId = lb.getAttribute('for');

                        if (forId) {
                            const el = document.getElementById(forId);
                            if (el) {
                                if ('value' in el && el.value !== undefined) {
                                    return (el.value || '').trim();
                                }
                                return (el.innerText || el.textContent || '').trim();
                            }
                        }

                        const tr = lb.closest('tr');
                        if (tr) {
                            const inputs = Array.from(
                                tr.querySelectorAll('input, textarea')
                            ).filter(e => {
                                const type = (e.getAttribute('type') || '').toLowerCase();
                                return type !== 'hidden' && type !== 'button' && type !== 'submit';
                            });

                            for (const input of inputs) {
                                if ((input.value || '').trim()) {
                                    return input.value.trim();
                                }
                            }

                            let text = (tr.innerText || '').replace(lb.innerText, '').trim();
                            text = text.replace(/\\s+/g, ' ');
                            return text;
                        }
                    }

                    return '';
                }
                """,
                label_text,
            )
        except Exception:
            return ""

    def _extraer_detalle_incidente(self, page, tt_number):
        """
        Extrae datos principales de la pestaña Incidencia.
        """
        print("[MAXIMO] Extrayendo detalle principal del incidente...")

        detalle = {
            "incidente": tt_number,
            "resumen": self._valor_por_label(page, "Resumen"),
            "detalles": self._valor_por_label(page, "Detalles"),
            "propietario": self._valor_por_label(page, "Propietario"),
            "turno_propietario": self._valor_por_label(page, "Turno propietario"),
            "grupo_propietario": self._valor_por_label(page, "Grupo propietario"),
            "creado_por": self._valor_por_label(page, "Creado por"),
            "prioridad_interna": self._valor_por_label(page, "Prioridad interna"),
            "ruta_clasificacion": self._valor_por_label(page, "Ruta de clasificación"),
            "descripcion_clase": self._valor_por_label(page, "Descripción de clase"),
            "ubicacion": self._valor_por_label(page, "Ubicación"),
            "proveedor_ticket_externo": self._valor_por_label(page, "Proveedor ticket externo"),
            "ticket_externo": self._valor_por_label(page, "N° Ticket externo"),
            "descripcion_tecnica": self._valor_por_label(page, "Descripción tecnica"),
            "flujo_creacion": self._valor_por_label(page, "Flujo de creación"),
            "incidente_creado_por_alarma": self._valor_por_label(page, "¿Incidente creado por alarma?"),
            "impacto": self._valor_por_label(page, "Impacto"),
            "urgencia": self._valor_por_label(page, "Urgencia"),
            "prioridad": self._valor_por_label(page, "Prioridad"),
            "articulo_configuracion_principal": self._valor_por_label(page, "Artículo de configuración"),
            "nombre_articulo_configuracion": self._valor_por_label(page, "Nombre del artículo de configuración"),
            "descripcion_ci": self._valor_por_label(page, "Descripción"),
            "clasificacion_ci": self._valor_por_label(page, "Clasificación"),
            "ubicacion_ci": self._valor_por_label(page, "Ubicación"),
            "fecha_inicio_falla": self._valor_por_label(page, "Fecha inicio falla"),
            "fecha_fin_falla": self._valor_por_label(page, "Fecha fin falla"),
            "fecha_creacion": self._valor_por_label(page, "Fecha de creación"),
            "fecha_notificacion": self._valor_por_label(page, "Fecha de notificación"),
            "fecha_inicio": self._valor_por_label(page, "Fecha de inicio"),
            "fecha_resolucion": self._valor_por_label(page, "Fecha resolución"),
            "finalizacion_prevista": self._valor_por_label(page, "Finalización prevista"),
        }

        return detalle

    def _valor_columna_tabla(self, tabla, row_num, col):
        celda = tabla.locator(f"[id*='_tdrow_[C:{col}]-c[R:{row_num}]']")

        if celda.count() == 0:
            return ""

        celda = celda.first
        input_col = celda.locator("input")

        if input_col.count() > 0:
            return limpiar_texto(input_col.first.get_attribute("value"))

        try:
            return limpiar_texto(celda.inner_text(timeout=5000))
        except Exception:
            return ""

    def _extraer_tickets_relacionados(self, page):
        print("[MAXIMO] Leyendo tabla de Tickets relacionados...")

        try:
            titulo = page.locator("text=Tickets relacionados").last
            titulo.wait_for(state="visible", timeout=MAXIMO_TIMEOUT)

            tabla = titulo.locator(
                "xpath=following::table[contains(@id, '_tbod-tbd')][1]"
            )

            tabla.wait_for(state="visible", timeout=MAXIMO_TIMEOUT)

            filas = tabla.locator("tr[id*='_tbod_tdrow-tr[R:']")
            total = filas.count()

            tickets = []
            vistos = set()

            for i in range(total):
                fila = filas.nth(i)
                fila_id = fila.get_attribute("id")

                if not fila_id or "[R:" not in fila_id:
                    continue

                row_num = fila_id.split("[R:")[1].replace("]", "")

                ticket = {
                    "clave": self._valor_columna_tabla(tabla, row_num, 1),
                    "descripcion": self._valor_columna_tabla(tabla, row_num, 2),
                    "clase": self._valor_columna_tabla(tabla, row_num, 3),
                    "estado": self._valor_columna_tabla(tabla, row_num, 4),
                    "relacion": self._valor_columna_tabla(tabla, row_num, 5),
                }

                clave = (
                    ticket["clave"],
                    ticket["descripcion"],
                    ticket["clase"],
                    ticket["estado"],
                    ticket["relacion"],
                )

                if clave in vistos:
                    continue

                vistos.add(clave)

                if ticket["clave"] or ticket["descripcion"]:
                    tickets.append(ticket)

            return tickets

        except Exception as e:
            print(f"[MAXIMO] No pude leer Tickets relacionados: {e}")
            return []

    def _extraer_alarmas_relacionadas(self, page):
        print("[MAXIMO] Leyendo tabla de Alarmas relacionadas...")

        try:
            titulo = page.locator("text=Alarmas Relacionadas").last
            titulo.wait_for(state="visible", timeout=MAXIMO_TIMEOUT)

            tabla = titulo.locator(
                "xpath=following::table[contains(@id, '_tbod-tbd')][1]"
            )

            tabla.wait_for(state="visible", timeout=MAXIMO_TIMEOUT)

            filas = tabla.locator("tr[id*='_tbod_tdrow-tr[R:']")
            total = filas.count()

            alarmas = []
            vistos = set()

            for i in range(total):
                fila = filas.nth(i)
                fila_id = fila.get_attribute("id")

                if not fila_id or "[R:" not in fila_id:
                    continue

                row_num = fila_id.split("[R:")[1].replace("]", "")

                alarma = {
                    "id_alarma": self._valor_columna_tabla(tabla, row_num, 1),
                    "consecutivo": self._valor_columna_tabla(tabla, row_num, 2),
                    "nombre_alarma": self._valor_columna_tabla(tabla, row_num, 3),
                    "estacion_base": self._valor_columna_tabla(tabla, row_num, 4),
                    "sector_estacion_base": self._valor_columna_tabla(tabla, row_num, 5),
                    "elemento_red": self._valor_columna_tabla(tabla, row_num, 6),
                    "dn": self._valor_columna_tabla(tabla, row_num, 7),
                    "severidad": self._valor_columna_tabla(tabla, row_num, 8),
                    "id_global": self._valor_columna_tabla(tabla, row_num, 9),
                    "alarma_activa": self._valor_columna_tabla(tabla, row_num, 10),
                    "fecha_inicio_alarma": self._valor_columna_tabla(tabla, row_num, 11),
                    "fecha_fin_alarma": self._valor_columna_tabla(tabla, row_num, 12),
                    "indisponibilidad_hr": self._valor_columna_tabla(tabla, row_num, 13),
                    "relacionado_por": self._valor_columna_tabla(tabla, row_num, 14),
                    "cancelado_por": self._valor_columna_tabla(tabla, row_num, 15),
                    "tecnologia": self._valor_columna_tabla(tabla, row_num, 16),
                }

                clave = (
                    alarma["nombre_alarma"],
                    alarma["elemento_red"],
                    alarma["fecha_inicio_alarma"],
                    alarma["fecha_fin_alarma"],
                )

                if clave in vistos:
                    continue

                vistos.add(clave)

                if alarma["nombre_alarma"] or alarma["elemento_red"]:
                    alarmas.append(alarma)

            return alarmas

        except Exception as e:
            print(f"[MAXIMO] No pude leer Alarmas relacionadas: {e}")
            return []
    
    def _extraer_nodos_desde_ordenes(self, ordenes):
        nodos = []
        vistos = set()

        for orden in ordenes:
            estado = orden.get("estado", "")
            nodo = normalizar(orden.get("articulo_configuracion", ""))

            if not nodo:
                continue

            # Para diagnosticar, usamos solo órdenes abiertas/activas.
            if not es_estado_valido_para_consulta(estado):
                continue

            if nodo not in vistos:
                vistos.add(nodo)
                nodos.append(nodo)

        return nodos