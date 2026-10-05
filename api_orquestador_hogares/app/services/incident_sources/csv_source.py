import csv
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]

DEFAULT_CSV_FILES = [
    BASE_DIR / "inc_mayores_6h.csv",
    BASE_DIR / "inc_test_vendor.csv",
]


def limpiar(txt):
    if txt is None:
        return ""
    return str(txt).strip()


def normalizar(txt):
    return limpiar(txt).upper()


def partir_nodos_desde_texto(txt):
    """
    Extrae posibles nodos desde campos como NodeAlias, Node, Summary, AlertKey.
    No inventa nodos, solo divide por separadores comunes.
    """
    txt = limpiar(txt)

    if not txt:
        return []

    partes = re.split(r"[,\s;/|]+", txt)
    nodos = []

    for p in partes:
        p = limpiar(p).upper()

        if not p:
            continue

        # Filtra palabras basura muy comunes
        if p in {
            "NULL",
            "N/A",
            "NA",
            "ERROR",
            "GESTOR",
            "POLLER",
            "MARCACION",
            "SLOT",
            "RX",
            "CREADO",
            "POR",
            "DE",
            "CM",
            "LAS",
            "A",
            "EL",
            "LA",
            "LOS",
            "DEL",
            "OPTICO",
        }:
            continue

        # Debe parecer identificador de nodo
        if re.fullmatch(r"[A-Z0-9_-]{2,20}", p):
            nodos.append(p)

    # Quitar duplicados preservando orden
    salida = []
    vistos = set()

    for n in nodos:
        if n not in vistos:
            vistos.add(n)
            salida.append(n)

    return salida


class CSVIncidentSource:
    """
    Fuente de incidentes basada en CSV.

    Hoy lee archivos locales:
    - inc_mayores_6h.csv
    - inc_test_vendor.csv

    Mañana esta fuente puede convivir con:
    - Maximo API
    - Netcool
    - Playwright
    - base intermedia
    """

    def __init__(self, csv_files=None):
        self.csv_files = csv_files or DEFAULT_CSV_FILES

    def buscar_incidente(self, tt_number):
        tt_number = normalizar(tt_number)

        resultados = []

        for csv_file in self.csv_files:
            csv_file = Path(csv_file)

            if not csv_file.exists():
                continue

            resultados.extend(
                self._buscar_en_archivo(
                    csv_file=csv_file,
                    tt_number=tt_number,
                )
            )

        if not resultados:
            return {
                "ok": False,
                "fuente": "CSV",
                "tt_number": tt_number,
                "error": f"No encontré el incidente {tt_number} en los CSV disponibles.",
                "registros": [],
                "nodos": [],
            }

        nodos = []
        vistos = set()

        for r in resultados:
            for n in r.get("nodos_detectados", []):
                if n not in vistos:
                    vistos.add(n)
                    nodos.append(n)

        return {
            "ok": True,
            "fuente": "CSV",
            "tt_number": tt_number,
            "registros": resultados,
            "nodos": nodos,
            "total_registros": len(resultados),
        }

    def _buscar_en_archivo(self, csv_file, tt_number):
        encontrados = []

        with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
            muestra = f.read(4096)
            f.seek(0)

            delimiter = ";"
            if muestra.count(",") > muestra.count(";"):
                delimiter = ","

            reader = csv.DictReader(f, delimiter=delimiter)

            for row in reader:
                tt = (
                    row.get("TTNumber")
                    or row.get("TTNUMBER")
                    or row.get("INC")
                    or row.get("INCIDENTE")
                    or ""
                )

                tt_helix = (
                    row.get("TTNumber_Helix")
                    or row.get("TTNUMBER_HELIX")
                    or row.get("HELIX")
                    or ""
                )

                if normalizar(tt) != tt_number and normalizar(tt_helix) != tt_number:
                    continue

                nodos = self._extraer_nodos_row(row)

                encontrados.append({
                    "archivo": str(csv_file),
                    "tt_number": limpiar(tt),
                    "tt_number_helix": limpiar(tt_helix),
                    "severity": limpiar(row.get("Severity")),
                    "first_occurrence": limpiar(row.get("FirstOccurrence")),
                    "summary": limpiar(row.get("Summary")),
                    "node_alias": limpiar(row.get("NodeAlias")),
                    "node": limpiar(row.get("Node")),
                    "alert_key": limpiar(row.get("AlertKey")),
                    "alert_group": limpiar(row.get("AlertGroup")),
                    "municipio": limpiar(row.get("MUNICIPIO")),
                    "ubicacion": limpiar(row.get("Ubicacion")),
                    "horas_abierto": limpiar(row.get("Horas_Abierto")),
                    "nodos_detectados": nodos,
                    "row": row,
                })

        return encontrados

    def _extraer_nodos_row(self, row):
        """
        Extrae nodos del incidente.

        Regla segura:
        1. NodeAlias
        2. Node

        No usamos Summary ni AlertKey por defecto porque traen fechas,
        códigos, textos operativos y palabras que pueden parecer nodos.
        """
        campos_prioritarios = [
            row.get("NodeAlias"),
            row.get("Node"),
        ]

        nodos = []
        vistos = set()

        for campo in campos_prioritarios:
            for n in partir_nodos_desde_texto(campo):
                if n not in vistos:
                    vistos.add(n)
                    nodos.append(n)

        return nodos