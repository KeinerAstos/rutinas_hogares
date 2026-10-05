from __future__ import annotations

import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .config import settings
from .models import RelatedRecord, ValidationResult


class MaximoClient:
    HOME_BUTTON = "#titlebar-tb_homeButton"
    WOTRACK_LINK = "#FavoriteApp_WOTRACK"
    QUICKSEARCH_INPUT = "#quicksearch"
    QUICKSEARCH_BUTTON = "#quicksearchQSImage"
    CLASSIFICATION_SELECTOR = "#mc8f7970f-tb"
    RELATED_TAB = "#m4326cf1d-tab_anchor"
    RELATED_TABLE = "#m9f413804_tbod-tbd"

    def __init__(self):
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=settings.headless,
            slow_mo=settings.slow_mo,
            args=["--start-maximized"],
        )
        self.context = self.browser.new_context(no_viewport=True, accept_downloads=True)
        self.context.set_default_timeout(settings.timeout_ms)
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self._pw:
            self._pw.stop()

    def log(self, message):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

    def save_debug(self, name):
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            self.page.screenshot(path=str(settings.debug_dir / f"{safe}_{stamp}.png"), full_page=True)
        except Exception:
            pass
        try:
            (settings.debug_dir / f"{safe}_{stamp}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception:
            pass

    def login_maximo(self):
        if not settings.maximo_url:
            raise RuntimeError("Falta MAXIMO_URL en el archivo .env")

        self.log("Abriendo Maximo...")
        self.page.goto(settings.maximo_url, wait_until="domcontentloaded", timeout=settings.timeout_ms)
        self.page.wait_for_timeout(2500)

        if self.page.locator("#j_username").count() > 0:
            self.log("Formulario de login detectado.")
            if not settings.maximo_user or not settings.maximo_password:
                raise RuntimeError("Faltan MAXIMO_USER o MAXIMO_PASSWORD en el archivo .env")

            self.page.locator("#j_username").wait_for(state="visible", timeout=settings.timeout_ms)
            self.page.fill("#j_username", settings.maximo_user)
            self.page.locator("#j_password").wait_for(state="visible", timeout=settings.timeout_ms)
            self.page.fill("#j_password", settings.maximo_password)

            self.log("Enviando login...")
            self.page.press("#j_password", "Enter")
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=settings.timeout_ms)
            except PlaywrightTimeoutError:
                pass
            self.page.wait_for_timeout(7000)
        else:
            self.log("No apareció formulario de login; puede existir una sesión activa.")

        body = ""
        try:
            body = self.page.locator("body").inner_text(timeout=10000)
        except Exception:
            pass

        if (
            "Bienvenido" in body
            or "Centro de inicio" in body
            or self.page.locator(self.HOME_BUTTON).count() > 0
        ):
            self.log("Login / sesión OK.")
            return

        self.save_debug("login_no_confirmado")
        raise RuntimeError("No pude confirmar el login en Maximo.")


    def return_to_work_orders(self):
        self.log("Regresando a Seguimiento de órdenes de trabajo...")

        dialog = self.page.locator(
            'table[summary="Ver adjuntos"]'
        ).first

        try:
            if dialog.count() > 0 and dialog.is_visible():
                self.close_attachments_dialog()
        except Exception:
            pass

        return_link = self.page.locator(
            'a[href*="returntoapp"][href*="APPTARGET"]'
        ).first

        try:
            if return_link.count() > 0 and return_link.is_visible():
                return_link.click(
                    timeout=15000,
                    force=True,
                )
                self.page.wait_for_timeout(1800)
        except Exception as error:
            self.log(
                f"No se pudo usar el enlace de regreso: {error}"
            )

        self.log("Reiniciando WOTRACK para la siguiente OT...")

        opened = False
        try:
            opened = self.page.evaluate(
                """
                () => {
                    if (typeof sendEvent === 'function') {
                        sendEvent('changeapp', 'startcntr', 'WOTRACK', 3);
                        return true;
                    }
                    return false;
                }
                """
            )
        except Exception:
            opened = False

        quicksearch = self.page.locator(
            self.QUICKSEARCH_INPUT
        ).first

        if not opened:
            try:
                if not (
                    quicksearch.count() > 0
                    and quicksearch.is_visible()
                ):
                    raise RuntimeError(
                        "No pude reiniciar WOTRACK."
                    )
            except Exception:
                raise RuntimeError(
                    "No pude reiniciar WOTRACK."
                )

        quicksearch.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        self.page.wait_for_timeout(1800)

        quicksearch.click(
            timeout=10000,
            force=True,
        )
        quicksearch.fill("")

        self.page.wait_for_timeout(500)

        self.log(
            "WOTRACK limpio y listo para la siguiente OT."
        )

    def go_to_work_orders(self):
        quicksearch = self.page.locator(
            self.QUICKSEARCH_INPUT
        ).first

        try:
            if quicksearch.count() > 0 and quicksearch.is_visible():
                self.log("WOTRACK ya está abierto.")
                return
        except Exception:
            pass

        return_link = self.page.locator(
            'a[href*="returntoapp"][href*="APPTARGET"]'
        ).first

        try:
            if return_link.count() > 0 and return_link.is_visible():
                self.return_to_work_orders()
                return
        except Exception:
            pass

        self.log("Entrando al Centro de inicio...")

        home_button = self.page.locator(
            self.HOME_BUTTON
        ).first

        try:
            if home_button.count() > 0 and home_button.is_visible():
                home_button.click(
                    timeout=settings.timeout_ms,
                    force=True,
                )
                self.page.wait_for_timeout(3000)
        except Exception:
            pass

        self.log(
            "Abriendo Seguimiento de órdenes de trabajo..."
        )

        work_orders_link = self.page.locator(
            self.WOTRACK_LINK
        ).first

        if work_orders_link.count() > 0:
            work_orders_link.wait_for(
                state="visible",
                timeout=settings.timeout_ms,
            )
            work_orders_link.click(
                timeout=settings.timeout_ms,
                force=True,
            )
        else:
            opened = self.page.evaluate(
                """
                () => {
                    if (typeof sendEvent === 'function') {
                        sendEvent('changeapp', 'startcntr', 'WOTRACK', 3);
                        return true;
                    }
                    return false;
                }
                """
            )

            if not opened:
                raise RuntimeError(
                    "No pude abrir WOTRACK."
                )

        quicksearch.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        self.page.wait_for_timeout(1200)
        self.log("WOTRACK listo.")

    def search_ot(self, ot):
        ot = str(ot or "").strip().upper()

        if not ot:
            raise RuntimeError("La OT recibida está vacía.")

        self.log(f"Buscando OT: {ot}")

        search_input = self.page.locator(
            self.QUICKSEARCH_INPUT
        ).first

        search_button = self.page.locator(
            self.QUICKSEARCH_BUTTON
        ).first

        search_input.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        search_button.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        search_input.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        search_input.fill("")
        self.page.wait_for_timeout(600)

        search_input.fill(ot)

        written_value = (
            search_input.input_value()
            .strip()
            .upper()
        )

        if written_value != ot:
            raise RuntimeError(
                f"No pude escribir correctamente la OT {ot}. "
                f"Valor detectado: {written_value}"
            )

        self.log(
            f"OT escrita correctamente: {written_value}"
        )

        # Maximo usa async=setvalue. Sacar el foco permite
        # que el servidor reciba el valor antes de buscar.
        search_input.press("Tab")

        self.log(
            "Esperando que Maximo registre la OT..."
        )

        self.page.wait_for_timeout(1500)

        current_value = (
            search_input.input_value()
            .strip()
            .upper()
        )

        if current_value != ot:
            search_input.click(
                timeout=settings.timeout_ms,
                force=True,
            )
            search_input.fill("")
            self.page.wait_for_timeout(500)
            search_input.fill(ot)
            search_input.press("Tab")
            self.page.wait_for_timeout(1500)

            current_value = (
                search_input.input_value()
                .strip()
                .upper()
            )

        if current_value != ot:
            raise RuntimeError(
                f"Maximo no conservó la OT {ot} "
                "antes de ejecutar la búsqueda."
            )

        self.log(
            "OT confirmada. Ejecutando búsqueda desde la lupa..."
        )

        search_button.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        self.log("Clic realizado en la lupa.")

        self.page.wait_for_timeout(2500)

        try:
            # No usamos bodyText porque el quicksearch también contiene
            # la OT y generaría un falso positivo.
            self.page.wait_for_function(
                """
                (expectedOt) => {
                    const fields = Array.from(
                        document.querySelectorAll(
                            'input[type="text"], textarea'
                        )
                    );

                    const detailField = fields.some((field) => {
                        if (field.id === 'quicksearch') {
                            return false;
                        }

                        const value = (
                            field.value || ''
                        ).trim().toUpperCase();

                        return value === expectedOt;
                    });

                    const classification = document.querySelector(
                        '#mc8f7970f-tb'
                    );

                    const relatedTab = document.querySelector(
                        '#m4326cf1d-tab_anchor'
                    );

                    return Boolean(
                        detailField
                        && classification
                        && relatedTab
                    );
                }
                """,
                arg=ot,
                timeout=settings.timeout_ms,
            )

        except Exception:
            self.save_debug(
                f"{ot}_sin_detalle_confirmado"
            )

            raise RuntimeError(
                f"No pude confirmar el detalle de la OT {ot}. "
                "Maximo no procesó la nueva búsqueda o la OT no existe."
            )

        self.log(f"Detalle de OT confirmado: {ot}")

    def get_classification(self):
        field = self.page.locator(self.CLASSIFICATION_SELECTOR)
        field.wait_for(state="attached", timeout=settings.timeout_ms)
        return field.input_value().strip()

    @staticmethod
    def classification_allowed(value):
        text = (value or "").upper()
        return "SERVICIOS FIJOS" in text or "REDES NEUTRAS" in text

    # PATCH_MAXIMO_IMPACTO_NOTAS_SERIALES_V3

    @staticmethod
    def extract_serial_candidates(sources):
        """
        Extrae identificadores v?lidos para consultar en ACS.

        Prioridades:
        1. Seriales asociados expl?citamente a una etiqueta.
        2. MAC asociada expl?citamente a una etiqueta.
        3. Seriales GPON con formato fabricante + 8 hexadecimales.

        No acepta cadenas hexadecimales sueltas de 12 caracteres,
        porque pueden corresponder a IDs internos presentes en Notas.
        """
        import re

        attempts = []
        seen = set()

        def add_attempt(search_by, raw_value, source):
            kind = str(search_by or "").strip().lower()
            value = str(raw_value or "").strip().upper()

            value = value.strip(
                " \t\r\n,;|()[]{}<>\"'"
            )

            if kind == "mac":
                value = re.sub(
                    r"[^0-9A-F]",
                    "",
                    value,
                )

                if not re.fullmatch(
                    r"[0-9A-F]{12}",
                    value,
                ):
                    return

            elif kind == "serial":
                value = re.sub(
                    r"[^A-Z0-9]",
                    "",
                    value,
                )

                if not re.fullmatch(
                    r"[A-Z0-9]{8,32}",
                    value,
                ):
                    return

            else:
                return

            key = (kind, value)

            if key in seen:
                return

            seen.add(key)

            attempts.append(
                {
                    "search_by": kind,
                    "value": value,
                    "source": source,
                }
            )

        serial_label_re = re.compile(
            r"""
            \b
            (?:
                SERIAL
                (?:\s+(?:ONT|ONU|EQUIPO))?
                |
                S/N
                |
                SN
            )
            \b
            \s*
            (?:[:=#-]\s*)?
            ([A-Z0-9][A-Z0-9._:-]{7,31})
            """,
            re.I | re.X,
        )

        mac_label_re = re.compile(
            r"""
            \bMAC
            (?:\s+ADDRESS)?
            \b
            \s*
            (?:[:=#-]\s*)?
            (
                (?:[0-9A-F]{2}[:-]?){5}
                [0-9A-F]{2}
            )
            """,
            re.I | re.X,
        )

        # Formato habitual de serial ?ptico:
        # cuatro letras de fabricante + ocho hexadecimales.
        #
        # Ejemplos:
        # SCOMA0B20EE5
        # SKYWB86D18E8
        # HWTCA60092B7
        # ZTEGCE817177
        # SDMC691CED07
        gpon_serial_re = re.compile(
            r"(?<![A-Z0-9])"
            r"([A-Z]{4}[0-9A-F]{8})"
            r"(?![A-Z0-9])",
            re.I,
        )

        for source_text in sources or []:
            current_text = str(source_text or "")

            # Valores expresamente identificados como serial.
            for match in serial_label_re.finditer(current_text):
                add_attempt(
                    "serial",
                    match.group(1),
                    "SERIAL_ETIQUETADO",
                )

            # MAC solamente cuando est? expresamente identificada.
            for match in mac_label_re.finditer(current_text):
                add_attempt(
                    "mac",
                    match.group(1),
                    "MAC_ETIQUETADA",
                )

            # Seriales ?pticos sin etiqueta, habituales en Impacto.
            for match in gpon_serial_re.finditer(current_text):
                add_attempt(
                    "serial",
                    match.group(1),
                    "GPON_SERIAL",
                )

        return attempts

    def extract_serials_from_open_incident_editor(
        self,
        incident_key,
    ):
        """
        Lee la nota principal cargada al abrir el incidente.

        El contenido se encuentra en #dijitEditorBody dentro
        de un iframe. Se revisa antes de Impacto y Notas.
        """
        self.log(
            "Leyendo nota principal del incidente antes de Impacto..."
        )

        sources = []

        # El iframe puede tardar algunos instantes en aparecer.
        for attempt in range(8):
            sources = []

            for frame in self.page.frames:
                try:
                    editors = frame.locator("#dijitEditorBody")

                    for index in range(editors.count()):
                        editor = editors.nth(index)

                        if not editor.is_visible():
                            continue

                        editor_text = editor.inner_text(
                            timeout=3000
                        ).strip()

                        if editor_text:
                            sources.append(editor_text)
                except Exception:
                    continue

            if sources:
                break

            if attempt < 7:
                self.page.wait_for_timeout(500)

        if not sources:
            self.log(
                "No apareci? contenido en #dijitEditorBody. "
                "Se continuar? con Impacto."
            )
            return []

        serial_re = re.compile(
            r"(?im)^\s*"
            r"(?:SERIAL|S/?N|ONT(?:\s+ID)?)"
            r"\s*[:#=\-]\s*"
            r"([A-Z0-9][A-Z0-9._/\-]{5,39})"
        )

        olt_mac_re = re.compile(
            r"(?im)^\s*OLT\s*[:#=\-]\s*"
            r"((?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2})"
        )

        serials = []
        seen = set()

        def add_candidate(raw_value, is_mac=False):
            value = str(raw_value or "").strip().upper()
            value = value.strip(".,;:()[]{}<>\"'")

            if is_mac:
                value = re.sub(r"[:-]", "", value)

            # Impide aceptar cuentas, casos y tel?fonos.
            if not any(char.isalpha() for char in value):
                return

            if not any(char.isdigit() for char in value):
                return

            if not 6 <= len(value) <= 40:
                return

            if value not in seen:
                seen.add(value)
                serials.append(value)

        for source in sources:
            for match in serial_re.finditer(source):
                add_candidate(match.group(1))

            # OLT/MAC se utiliza como respaldo y se normaliza.
            for match in olt_mac_re.finditer(source):
                add_candidate(match.group(1), is_mac=True)

        if serials:
            self.log(
                "Seriales encontrados en la nota principal "
                f"del incidente {incident_key}: "
                + ", ".join(serials)
            )
            return serials

        self.log(
            "La nota principal fue le?da, pero no contiene "
            "seriales v?lidos. Se continuar? con Impacto."
        )
        return []


    def extract_serials_from_incident_notes(self, incident_key):
        """
        Revisa todas las notas del incidente y obtiene candidatos a serial.

        No descarga adjuntos y no modifica el flujo de evidencias.
        """
        self.log(
            "Impacto no mostro seriales. "
            "Buscando seriales en todas las notas del incidente..."
        )

        self.open_notes_tab()

        notes_table = self.page.locator(
            'table[summary="Notas incidente"]'
        ).first

        notes_table.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        rows = notes_table.locator(
            'tr[id*="_tbod_tdrow-tr[R:"]'
        )

        total_notes = rows.count()

        self.log(
            f"Notas incidente: {total_notes} nota(s) para revisar."
        )

        sources = []

        # Maximo conserva el texto completo en el value de la columna C:6.
        # inner_text() no incluye el contenido de los input.
        try:
            note_values = notes_table.locator(
                'input[id*="_tdrow_"][id*="[C:6]"], '
                'textarea[id*="_tdrow_"][id*="[C:6]"]'
            ).evaluate_all(
                """
                elements => elements
                    .map(element => (
                        element.value
                        || element.getAttribute('value')
                        || element.textContent
                        || ''
                    ).trim())
                    .filter(Boolean)
                """
            )

            sources.extend(note_values)

            self.log(
                "Notas incidente: "
                f"{len(note_values)} contenido(s) completos leidos "
                "desde la columna de texto."
            )
        except Exception as error:
            self.log(
                "No se pudieron leer los valores completos "
                f"de Notas incidente: {error}"
            )

        for note_index in range(total_notes):
            notes_table = self.page.locator(
                'table[summary="Notas incidente"]'
            ).first

            rows = notes_table.locator(
                'tr[id*="_tbod_tdrow-tr[R:"]'
            )

            if note_index >= rows.count():
                continue

            row = rows.nth(note_index)

            try:
                metadata = self.extract_note_metadata(
                    row,
                    note_index,
                )

                if metadata.get("summary"):
                    sources.append(metadata["summary"])
            except Exception:
                pass

            toggle = row.locator(
                'a[title="Ver detalles"], '
                'img[title="Ver detalles"]'
            ).first

            detail_opened = False

            try:
                if toggle.count() > 0 and toggle.is_visible():
                    toggle.click(
                        timeout=settings.timeout_ms,
                        force=True,
                    )
                    self.page.wait_for_timeout(700)
                    detail_opened = True
            except Exception as error:
                self.log(
                    f"Nota {note_index + 1}: "
                    f"no se pudo abrir el detalle: {error}"
                )

            try:
                notes_table = self.page.locator(
                    'table[summary="Notas incidente"]'
                ).first

                current_rows = notes_table.locator(
                    'tr[id*="_tbod_tdrow-tr[R:"]'
                )

                if note_index < current_rows.count():
                    current_row = current_rows.nth(note_index)

                    row_text = current_row.inner_text(
                        timeout=5000
                    ).strip()

                    if row_text:
                        sources.append(row_text)

                    values = current_row.locator(
                        "input, textarea"
                    ).evaluate_all(
                        """
                        elements => elements
                            .map(element => (
                                element.value
                                || element.getAttribute('title')
                                || element.textContent
                                || ''
                            ))
                            .filter(Boolean)
                        """
                    )

                    sources.extend(values)
            except Exception as error:
                self.log(
                    f"Nota {note_index + 1}: "
                    f"no se pudo leer todo el contenido: {error}"
                )

            if detail_opened:
                self._close_note_detail(
                    note_index,
                    "Notas incidente",
                )

        serials = self.extract_serial_candidates(sources)

        if serials:
            self.log(
                "Intentos ACS encontrados en Notas incidente: "
                + ", ".join(
                    f"{item['search_by']}={item['value']}"
                    for item in serials
                )
            )
        else:
            self.log(
                "No se encontraron seriales en las notas "
                f"del incidente {incident_key}."
            )
            self.save_debug(
                f"{incident_key}_notas_sin_seriales"
            )

        return serials

    def extract_impact_serials(self):
        """
        Busca seriales primero en Impacto y, si no aparecen,
        revisa todas las notas del incidente.
        """
        self.open_related()
        related_records = self.extract_related()

        incident = next(
            (
                record
                for record in related_records
                if str(
                    getattr(record, "key", "") or ""
                ).upper().startswith("INC")
                or str(
                    getattr(record, "record_class", "") or ""
                ).upper() == "INCIDENT"
            ),
            None,
        )

        if incident is None:
            self.save_debug(
                "incidente_relacionado_no_encontrado"
            )
            raise RuntimeError(
                "La OT no tiene un incidente relacionado disponible."
            )

        incident_key = str(
            getattr(incident, "key", "") or ""
        ).strip().upper()

        self.open_related_incident(incident_key)

        self.log("Abriendo pestana Impacto...")

        impact = None

        selectors = [
            'a[role="tab"]',
            'a',
            'span',
            'button',
        ]

        for selector in selectors:
            candidates = self.page.locator(selector).filter(
                has_text=re.compile(
                    r"^\s*Impacto\s*$",
                    re.I,
                )
            )

            for index in range(candidates.count()):
                candidate = candidates.nth(index)

                try:
                    if candidate.is_visible():
                        impact = candidate
                        break
                except Exception:
                    continue

            if impact is not None:
                break

        if impact is None:
            self.save_debug("impacto_no_encontrado")
            raise RuntimeError(
                "No se encontro la pestana Impacto "
                "en el incidente relacionado."
            )

        impact.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        self.page.wait_for_timeout(1800)

        sources = []

        try:
            visible_rows = self.page.locator("tr:visible")

            for row_index in range(visible_rows.count()):
                row = visible_rows.nth(row_index)

                try:
                    row_text = row.inner_text(
                        timeout=2000
                    ).strip()

                    if row_text:
                        sources.append(row_text)
                except Exception:
                    continue
        except Exception:
            pass

        try:
            values = self.page.locator(
                'input:visible, textarea:visible'
            ).evaluate_all(
                """
                elements => elements
                    .map(element => (
                        element.value
                        || element.getAttribute('title')
                        || ''
                    ))
                    .filter(Boolean)
                """
            )

            sources.extend(values)
        except Exception:
            pass

        impact_serials = self.extract_serial_candidates(
            sources
        )

        if impact_serials:
            self.log(
                "Impacto revisado. Intentos ACS encontrados: "
                + ", ".join(
                    f"{item['search_by']}={item['value']}"
                    for item in impact_serials
                )
            )
            return impact_serials

        self.save_debug(
            f"{incident_key}_impacto_sin_seriales"
        )

        return self.extract_serials_from_incident_notes(
            incident_key
        )


    def open_related(self):
        self.log("Abriendo Registros relacionados...")
        if self.page.locator(self.RELATED_TAB).count() > 0:
            self.page.locator(self.RELATED_TAB).click(force=True)
        else:
            tab = self.page.locator('a[role="tab"]', has_text="Registros relacionados").first
            tab.wait_for(state="visible", timeout=settings.timeout_ms)
            tab.click(force=True)
        self.page.locator(self.RELATED_TABLE).wait_for(state="visible", timeout=settings.timeout_ms)
        self.page.wait_for_timeout(900)

    def extract_related(self):
        rows = self.page.locator(self.RELATED_TABLE).locator('tr[id*="_tbod_tdrow-tr[R:"]')
        records = []
        for i in range(rows.count()):
            row = rows.nth(i)

            def input_value(col):
                loc = row.locator(f'input[id*="_tdrow_[C:{col}]_txt-tb"]')
                return loc.first.input_value().strip() if loc.count() else ""

            def span_text(col):
                loc = row.locator(f'td[id*="_tdrow_[C:{col}]-c"] span')
                return loc.first.inner_text().strip() if loc.count() else ""

            key = input_value(1)
            if not key:
                continue
            records.append(RelatedRecord(
                key=key,
                description=input_value(2),
                record_class=input_value(3),
                created_at=span_text(4),
                status=span_text(5),
                relationship=span_text(6),
            ))
        return records

    
    
    def open_related_incident(self, incident_key):
        incident_key = str(incident_key or "").strip().upper()

        if not incident_key:
            raise RuntimeError(
                "El incidente relacionado está vacío."
            )

        self.log(
            f"Abriendo menú del incidente: {incident_key}"
        )

        incident_input = self.page.locator(
            f'input[value="{incident_key}"]'
        ).first

        incident_input.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        row = incident_input.locator(
            "xpath=ancestor::tr[1]"
        )

        menu_button = row.locator(
            'img[title="Menú Detalles"]'
        ).first

        menu_button.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        menu_button.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        self.log(
            "Clic realizado en Menú Detalles."
        )

        self.page.wait_for_timeout(800)

        # Buscar únicamente una opción visible cuyo texto sea exactamente "Ir a".
        go_to_option = None

        candidates = self.page.get_by_text(
            "Ir a",
            exact=True,
        )

        for index in range(candidates.count()):
            candidate = candidates.nth(index)

            try:
                if candidate.is_visible():
                    go_to_option = candidate
                    break
            except Exception:
                continue

        if go_to_option is None:
            self.save_debug(
                f"{incident_key}_sin_ir_a"
            )

            raise RuntimeError(
                "El menú abrió, pero no encontré "
                "la opción visible 'Ir a'."
            )

        self.log(
            "Opción 'Ir a' encontrada."
        )

        # El submenú solo aparece cuando el mouse permanece sobre "Ir a".
        go_to_option.hover(
            timeout=settings.timeout_ms,
            force=True,
        )

        self.log(
            "Hover realizado sobre 'Ir a'."
        )

        self.page.wait_for_timeout(1200)

        # Buscar la opción visible del submenú.
        incident_option = None

        incident_candidates = self.page.get_by_text(
            "Incidencias Claro",
            exact=True,
        )

        for index in range(incident_candidates.count()):
            candidate = incident_candidates.nth(index)

            try:
                if candidate.is_visible():
                    incident_option = candidate
                    break
            except Exception:
                continue

        if incident_option is None:
            self.save_debug(
                f"{incident_key}_sin_incidencias_claro"
            )

            raise RuntimeError(
                "Se hizo hover sobre 'Ir a', pero no apareció "
                "la opción 'Incidencias Claro'."
            )

        self.log(
            "Opción 'Incidencias Claro' encontrada."
        )

        incident_option.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        self.log(
            "Clic realizado en 'Incidencias Claro'."
        )

        self.log(
            "Esperando que Máximo termine de abrir el incidente..."
        )

        try:
            self.page.wait_for_function(
                """
                (expectedIncident) => {
                    const expected = String(
                        expectedIncident || ''
                    ).trim().toUpperCase();

                    const values = Array.from(
                        document.querySelectorAll(
                            'input, textarea'
                        )
                    ).map((element) => String(
                        element.value || ''
                    ).trim().toUpperCase());

                    const incidentInField = values.includes(
                        expected
                    );

                    const bodyText = String(
                        document.body?.innerText || ''
                    ).toUpperCase();

                    const incidentInBody = bodyText.includes(
                        expected
                    );

                    const incidentNotesTable =
                        document.querySelector(
                            'table[summary="Notas incidente"]'
                        );

                    const notesTab = Array.from(
                        document.querySelectorAll(
                            'a[role="tab"], a'
                        )
                    ).some((element) => {
                        const title = String(
                            element.getAttribute('title') || ''
                        ).trim().toUpperCase();

                        const text = String(
                            element.textContent || ''
                        ).trim().toUpperCase();

                        return (
                            title === 'NOTAS'
                            || text === 'NOTAS'
                        );
                    });

                    return Boolean(
                        incidentInField
                        || incidentInBody
                        || incidentNotesTable
                        || notesTab
                    );
                }
                """,
                arg=incident_key,
                timeout=settings.timeout_ms,
            )

        except Exception as error:
            self.save_debug(
                f"{incident_key}_apertura_no_confirmada"
            )

            raise RuntimeError(
                f"Máximo recibió el clic para abrir "
                f"{incident_key}, pero la pantalla del "
                "incidente no terminó de cargar dentro del "
                f"tiempo permitido. Detalle: {error}"
            )

        self.page.wait_for_timeout(1500)

        self.log(
            f"Incidente abierto correctamente: {incident_key}"
        )
    
    def open_notes_tab(self):
        self.log("Abriendo pestaña Notas...")

        notes_tab = self.page.locator(
            'a[role="tab"][title="Notas"]'
        ).first

        if notes_tab.count() == 0:
            notes_tab = self.page.get_by_text(
                "Notas",
                exact=True,
            ).first

        notes_tab.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        notes_tab.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        notes_table = self.page.locator(
            'table[summary="Notas incidente"]'
        ).first

        notes_table.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        self.page.wait_for_timeout(1500)

        self.log("Pestaña Notas abierta correctamente.")
    

    def open_ot_tas_notes_tab(self):
        """
        Abre la pestaña secundaria Notas OT / TAS.
        Solo se consulta cuando Notas incidente no tiene documentos.
        """
        self.log(
            "Sin evidencia en Notas incidente. "
            "Abriendo Notas OT / TAS..."
        )

        notes_tab = self.page.locator(
            "#m3af4f7f5-tab_anchor"
        ).first

        if notes_tab.count() == 0:
            notes_tab = self.page.locator(
                'a[role="tab"][title="Notas OT / TAS"]'
            ).first

        if notes_tab.count() == 0:
            notes_tab = self.page.get_by_text(
                "Notas OT / TAS",
                exact=True,
            ).first

        notes_tab.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        notes_tab.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        notes_table = self.page.locator(
            'table[summary="Notas OT / TAS"]'
        ).first

        notes_table.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        self.page.wait_for_timeout(1000)

        self.log(
            "Pestaña Notas OT / TAS abierta correctamente."
        )

    def close_attachments_dialog(self):
        self.log("Cerrando ventana de adjuntos...")

        dialog_table = self.page.locator(
            'table[summary="Ver adjuntos"]'
        ).first

        accept_button = self.page.locator(
            'button[title="Aceptar"]:visible'
        ).last

        if accept_button.count() == 0:
            accept_button = self.page.get_by_role(
                "button",
                name="Aceptar",
                exact=True,
            ).last

        if accept_button.count() == 0:
            self.save_debug("modal_adjuntos_sin_aceptar")
            raise RuntimeError(
                "No encontré el botón Aceptar "
                "de la ventana de adjuntos."
            )

        accept_button.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        accept_button.click(
            timeout=settings.timeout_ms,
            force=True,
        )

        try:
            dialog_table.wait_for(
                state="hidden",
                timeout=15000,
            )
        except Exception:
            self.save_debug("modal_adjuntos_no_cerro")
            raise RuntimeError(
                "Se hizo clic en Aceptar, pero "
                "la ventana de adjuntos continuó abierta."
            )

        self.page.wait_for_timeout(700)
        self.log(
            "Ventana de adjuntos cerrada correctamente."
        )


    @staticmethod
    def parse_maximo_datetime(value="", dojovalue=""):
        """
        Convierte fechas de Maximo a datetime para ordenar las notas.
        """
        value = str(value or "").strip()

        for fmt in (
            "%d/%m/%y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%y %H:%M",
            "%d/%m/%Y %H:%M",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue

        try:
            milliseconds = int(str(dojovalue or "").strip())
            if milliseconds > 0:
                return datetime.fromtimestamp(milliseconds / 1000)
        except (TypeError, ValueError, OSError):
            pass

        return datetime.min

    def extract_note_metadata(self, row, note_index):
        """
        Lee fecha y resumen directamente desde la fila de la nota.
        """
        note_summary = ""
        note_date_text = ""
        note_dojovalue = ""

        try:
            summary_input = row.locator(
                'input[id*="_tdrow_[C:6]_txt-tb"]'
            ).first

            if summary_input.count() > 0:
                note_summary = summary_input.input_value().strip()
        except Exception:
            pass

        try:
            date_input = row.locator(
                'input[id*="_tdrow_[C:4]_txt-tb"]'
            ).first

            if date_input.count() > 0:
                note_date_text = (
                    date_input.get_attribute("title")
                    or date_input.input_value()
                    or ""
                ).strip()

                note_dojovalue = (
                    date_input.get_attribute("dojovalue")
                    or ""
                ).strip()
        except Exception:
            pass

        return {
            "note_index": note_index,
            "note_number": note_index + 1,
            "summary": note_summary,
            "created_at": note_date_text,
            "datetime": self.parse_maximo_datetime(
                note_date_text,
                note_dojovalue,
            ),
        }

    def unique_download_path(self, filename):
        """
        Evita sobreescribir archivos de ejecuciones anteriores.
        """
        destination = settings.downloads_dir / filename

        if not destination.exists():
            return destination

        stem = destination.stem
        suffix = destination.suffix
        counter = 2

        while True:
            candidate = destination.with_name(
                f"{stem}_{counter}{suffix}"
            )

            if not candidate.exists():
                return candidate

            counter += 1

    def download_open_attachments(
        self,
        incident_key,
        note_index,
        note_created_at="",
        note_summary="",
    ):
        self.log(
            f"Revisando adjuntos de la nota {note_index + 1}..."
        )

        attachments_table = self.page.locator(
            'table[summary="Ver adjuntos"]'
        ).first

        try:
            attachments_table.wait_for(
                state="visible",
                timeout=15000,
            )
        except Exception:
            self.save_debug(
                f"{incident_key}_nota_{note_index + 1}_sin_modal_adjuntos"
            )

            raise RuntimeError(
                "Se hizo clic en Adjuntos, pero no apareció "
                "la ventana 'Ver adjuntos'."
            )

        attachment_rows = attachments_table.locator(
            'tr[id*="_tbod_tdrow-tr[R:"]'
        )

        total_rows = attachment_rows.count()

        if total_rows == 0:
            self.log(
                f"Nota {note_index + 1}: no contiene archivos adjuntos."
            )

            self.close_attachments_dialog()
            return []

        self.log(
            f"Nota {note_index + 1}: "
            f"{total_rows} archivo(s) encontrado(s)."
        )

        downloaded = []

        for attachment_index in range(total_rows):
            attachments_table = self.page.locator(
                'table[summary="Ver adjuntos"]'
            ).first

            attachment_rows = attachments_table.locator(
                'tr[id*="_tbod_tdrow-tr[R:"]'
            )

            if attachment_index >= attachment_rows.count():
                break

            row = attachment_rows.nth(attachment_index)

            document_link = row.locator(
                'td[id*="_tdrow_[C:0]-c"] a'
            ).first

            filename_cell = row.locator(
                'td[id*="_tdrow_[C:1]-c"] span'
            ).first

            folder_cell = row.locator(
                'td[id*="_tdrow_[C:2]-c"] span'
            ).first

            created_cell = row.locator(
                'td[id*="_tdrow_[C:4]-c"] span'
            ).first

            application_cell = row.locator(
                'td[id*="_tdrow_[C:6]-c"] span'
            ).first

            document_number = ""
            filename = ""
            folder = ""
            created_at = ""
            application = ""

            try:
                document_number = document_link.inner_text().strip()
            except Exception:
                pass

            try:
                filename = (
                    filename_cell.get_attribute("title")
                    or filename_cell.inner_text()
                    or ""
                ).strip()
            except Exception:
                pass

            try:
                folder = (
                    folder_cell.get_attribute("title")
                    or folder_cell.inner_text()
                    or ""
                ).strip()
            except Exception:
                pass

            try:
                created_at = (
                    created_cell.get_attribute("title")
                    or created_cell.inner_text()
                    or ""
                ).strip()
            except Exception:
                pass

            try:
                application = (
                    application_cell.get_attribute("title")
                    or application_cell.inner_text()
                    or ""
                ).strip()
            except Exception:
                pass

            if not filename:
                filename = (
                    f"{incident_key}_nota_{note_index + 1}_"
                    f"adjunto_{attachment_index + 1}"
                )

            safe_filename = re.sub(
                r'[<>:"/\\|?*]+',
                "_",
                filename,
            ).strip()

            if not safe_filename:
                safe_filename = (
                    f"{incident_key}_adjunto_"
                    f"{attachment_index + 1}"
                )

            destination = self.unique_download_path(
                safe_filename
            )

            self.log(
                f"Descargando archivo vigente: {safe_filename}"
            )

            try:
                with self.page.expect_download(
                    timeout=settings.timeout_ms
                ) as download_info:
                    document_link.click(
                        timeout=settings.timeout_ms,
                        force=True,
                    )

                browser_download = download_info.value
                browser_download.save_as(str(destination))

            except Exception as download_error:
                self.log(
                    f"No se pudo descargar {safe_filename}: "
                    f"{download_error}"
                )

                self.save_debug(
                    f"{incident_key}_error_descarga_"
                    f"{note_index + 1}_{attachment_index + 1}"
                )
                continue

            downloaded.append(
                {
                    "note_index": note_index + 1,
                    "note_created_at": note_created_at,
                    "note_summary": note_summary,
                    "document": document_number,
                    "filename": destination.name,
                    "original_filename": safe_filename,
                    "folder": folder,
                    "created_at": created_at,
                    "application": application,
                    "saved_path": str(destination.resolve()),
                }
            )

            self.log(
                f"Archivo guardado: {destination}"
            )

        self.close_attachments_dialog()
        return downloaded

    def _process_notes_source(
        self,
        incident_key,
        table_summary,
        source_name,
        open_tab_callback,
    ):
        """
        Revisa una tabla de notas desde la más reciente hacia la más antigua
        y descarga únicamente la evidencia de la nota más nueva que tenga
        documentos.
        """
        open_tab_callback()

        notes_table = self.page.locator(
            f'table[summary="{table_summary}"]'
        ).first

        notes_table.wait_for(
            state="visible",
            timeout=settings.timeout_ms,
        )

        initial_rows = notes_table.locator(
            'tr[id*="_tbod_tdrow-tr[R:"]'
        )

        total_notes = initial_rows.count()

        self.log(
            f"{source_name}: {total_notes} nota(s) encontrada(s)."
        )

        notes_metadata = []

        for note_index in range(total_notes):
            metadata = self.extract_note_metadata(
                initial_rows.nth(note_index),
                note_index,
            )
            metadata["source"] = source_name
            notes_metadata.append(metadata)

        notes_metadata.sort(
            key=lambda item: (
                item["datetime"],
                -item["note_index"],
            ),
            reverse=True,
        )

        downloaded_files = []
        selected_note = None
        notes_checked = 0

        for metadata in notes_metadata:
            note_index = metadata["note_index"]
            note_number = metadata["note_number"]
            note_summary = metadata["summary"]
            note_created_at = metadata["created_at"]

            notes_checked += 1

            self.log("=" * 60)
            self.log(
                f"{source_name} | Procesando nota "
                f"{note_number}/{total_notes} "
                f"| Fecha: {note_created_at or 'SIN FECHA'}"
            )

            if note_summary:
                preview = re.sub(
                    r"\s+",
                    " ",
                    note_summary,
                )[:180]

                self.log(
                    f"Resumen nota: {preview}"
                )

            notes_table = self.page.locator(
                f'table[summary="{table_summary}"]'
            ).first

            rows = notes_table.locator(
                'tr[id*="_tbod_tdrow-tr[R:"]'
            )

            if note_index >= rows.count():
                continue

            row = rows.nth(note_index)

            toggle = row.locator(
                'a[title="Ver detalles"], '
                'img[title="Ver detalles"]'
            ).first

            if toggle.count() > 0:
                toggle.click(
                    timeout=settings.timeout_ms,
                    force=True,
                )

                self.page.wait_for_timeout(1000)

                self.log(
                    f"Detalle de nota {note_number} abierto."
                )

            attachment_button = None

            attachment_candidates = self.page.get_by_text(
                "Adjuntos",
                exact=True,
            )

            for candidate_index in range(
                attachment_candidates.count()
            ):
                candidate = attachment_candidates.nth(
                    candidate_index
                )

                try:
                    if candidate.is_visible():
                        attachment_button = candidate
                        break
                except Exception:
                    continue

            if attachment_button is None:
                self.log(
                    f"{source_name} - Nota {note_number}: "
                    "no encontré el botón Adjuntos."
                )

                self._close_note_detail(
                    note_index,
                    table_summary,
                )
                continue

            attachment_button.click(
                timeout=settings.timeout_ms,
                force=True,
            )

            self.page.wait_for_timeout(1000)

            note_downloads = self.download_open_attachments(
                incident_key=incident_key,
                note_index=note_index,
                note_created_at=note_created_at,
                note_summary=note_summary,
            )

            self._close_note_detail(
                note_index,
                table_summary,
            )

            if note_downloads:
                for attachment in note_downloads:
                    attachment["note_source"] = source_name

                downloaded_files.extend(note_downloads)
                selected_note = metadata

                self.log(
                    f"Evidencia vigente encontrada en {source_name}: "
                    f"nota {note_number} "
                    f"| Fecha: {note_created_at or 'SIN FECHA'}"
                )
                break

        return {
            "notes_checked": notes_checked,
            "notes_with_attachments": 1 if selected_note else 0,
            "attachments": downloaded_files,
            "selected_note": (
                {
                    "note_index": selected_note["note_number"],
                    "created_at": selected_note["created_at"],
                    "summary": selected_note["summary"],
                    "source": source_name,
                }
                if selected_note
                else None
            ),
        }

    def process_incident_notes(self, incident_key):
        """
        1. Revisa Notas incidente.
        2. Si encuentra evidencia, termina.
        3. Solo si no encuentra evidencia, revisa Notas OT / TAS.
        """
        primary_result = self._process_notes_source(
            incident_key=incident_key,
            table_summary="Notas incidente",
            source_name="Notas incidente",
            open_tab_callback=self.open_notes_tab,
        )

        if primary_result["attachments"]:
            self.log("=" * 60)
            self.log(
                "Se encontró evidencia en Notas incidente. "
                "No se revisará Notas OT / TAS."
            )
            return primary_result

        self.log("=" * 60)
        self.log(
            "Notas incidente no tiene documentos. "
            "Buscando ahora en Notas OT / TAS."
        )

        fallback_result = self._process_notes_source(
            incident_key=incident_key,
            table_summary="Notas OT / TAS",
            source_name="Notas OT / TAS",
            open_tab_callback=self.open_ot_tas_notes_tab,
        )

        total_checked = (
            primary_result["notes_checked"]
            + fallback_result["notes_checked"]
        )

        fallback_result["notes_checked"] = total_checked

        if fallback_result["attachments"]:
            self.log(
                f"Revisión terminada: {total_checked} nota(s) "
                "revisada(s) entre ambas tablas; "
                f"{len(fallback_result['attachments'])} archivo(s) "
                "descargado(s) desde Notas OT / TAS."
            )
            return fallback_result

        self.log(
            f"Revisión terminada: {total_checked} nota(s) "
            "revisada(s) entre ambas tablas, sin adjuntos."
        )

        return {
            "notes_checked": total_checked,
            "notes_with_attachments": 0,
            "attachments": [],
            "selected_note": None,
        }

    def _close_note_detail(
        self,
        note_index,
        table_summary="Notas incidente",
    ):
        try:
            notes_table = self.page.locator(
                f'table[summary="{table_summary}"]'
            ).first

            rows = notes_table.locator(
                'tr[id*="_tbod_tdrow-tr[R:"]'
            )

            if note_index >= rows.count():
                return

            row = rows.nth(note_index)

            close_toggle = row.locator(
                'a[title="Cerrar detalles"], '
                'img[title="Cerrar detalles"]'
            ).first

            if close_toggle.count() > 0:
                close_toggle.click(
                    timeout=10000,
                    force=True,
                )
                self.page.wait_for_timeout(700)

        except Exception:
            pass

    def validate(self, ot, login=True):
        try:
            if login:
                self.login_maximo()

            self.go_to_work_orders()
            self.search_ot(ot)
            classification = self.get_classification()
            self.log(f"Clasificación encontrada: {classification}")

            if not self.classification_allowed(classification):
                return ValidationResult(
                    ok=False,
                    ot=ot,
                    classification=classification,
                    code="SERVICE_NOT_ALLOWED",
                    response_text="No se puede realizar la validación porque la OT corresponde a otro tipo de servicio.",
                )

            self.open_related()
            related = self.extract_related()
            incidents = [
                r for r in related
                if r.record_class.upper() == "INCIDENT" or r.key.upper().startswith("INC")
            ]

            if len(incidents) != 1:
                self.save_debug(f"{ot}_anomalia_incidentes")
                return ValidationResult(
                    ok=False,
                    ot=ot,
                    classification=classification,
                    incidents=incidents,
                    code="RELATED_INCIDENT_ANOMALY",
                    response_text=f"Anomalía: se encontraron {len(incidents)} incidentes relacionados y se esperaba exactamente uno.",
                )

            incident = incidents[0]

            self.open_related_incident(
                incident.key
            )

            notes_result = self.process_incident_notes(
                incident.key
            )

            attachments = notes_result["attachments"]
            notes_checked = notes_result["notes_checked"]
            notes_with_attachments = notes_result[
                "notes_with_attachments"
            ]

            self.save_debug(
                f"{ot}_{incident.key}_notas_revisadas"
            )

            if attachments:
                response_text = (
                    f"Se revisaron {notes_checked} nota(s) "
                    f"y se descargaron {len(attachments)} archivo(s)."
                )

                result_code = "EVIDENCE_DOWNLOADED"

            else:
                response_text = (
                    f"Se revisaron {notes_checked} nota(s), "
                    "pero no se encontraron archivos adjuntos."
                )

                result_code = "NO_EVIDENCE_FOUND"

            result = ValidationResult(
                ok=True,
                ot=ot,
                classification=classification,
                incidents=incidents,
                attachments=attachments,
                notes_checked=notes_checked,
                notes_with_attachments=notes_with_attachments,
                code=result_code,
                response_text=response_text,
            )

            try:
                self.return_to_work_orders()
            except Exception as return_error:
                self.log(
                    "La OT terminó correctamente, pero no se pudo "
                    "regresar a WOTRACK: "
                    f"{return_error}"
                )

            return result

        except Exception as exc:
            try:
                self.save_debug(f"{ot}_error")
            except Exception:
                pass
            return ValidationResult(
                ok=False,
                ot=ot,
                code="MAXIMO_AUTOMATION_ERROR",
                error=str(exc),
                response_text="No fue posible completar la validación en Maximo.",
            )
