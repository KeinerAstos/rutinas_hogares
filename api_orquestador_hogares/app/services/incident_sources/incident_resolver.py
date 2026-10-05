from app.services.incident_sources.csv_source import CSVIncidentSource

try:
    from app.services.incident_sources.maximo_source import MaximoSource
except Exception:
    MaximoSource = None


class IncidentResolver:
    """
    Resolvedor central de incidentes.

    Orden:
    1. Máximo por scraping
    2. CSV local como respaldo

    Si Máximo encuentra contexto pero no trae nodos abiertos,
    ese contexto se conserva y se adjunta al resultado del CSV.
    """

    def __init__(self, sources=None):
        if sources is not None:
            self.sources = sources
            return

        self.sources = []

        if MaximoSource is not None:
            self.sources.append(MaximoSource())

        self.sources.append(CSVIncidentSource())

    def resolver_incidente(self, tt_number):
        errores = []
        mejor_resultado_sin_nodos = None
        contexto_maximo = None

        for source in self.sources:
            nombre = getattr(source, "nombre", source.__class__.__name__)

            print(f"[INCIDENT_RESOLVER] Buscando {tt_number} en fuente: {nombre}")

            resultado = source.buscar_incidente(tt_number)

            if resultado.get("ok"):
                fuente = resultado.get("fuente", nombre)
                nodos = resultado.get("nodos") or []

                if fuente == "MAXIMO":
                    contexto_maximo = resultado

                if nodos:
                    print(
                        f"[INCIDENT_RESOLVER] Fuente {nombre} respondió OK con nodos: "
                        f"{', '.join(nodos)}"
                    )

                    if contexto_maximo and fuente != "MAXIMO":
                        print("[INCIDENT_RESOLVER] Adjuntando contexto de MAXIMO al resultado final.")
                        resultado["contexto_maximo"] = contexto_maximo
                        resultado["fuente_diagnostico"] = fuente
                        resultado["fuente_contexto"] = "MAXIMO"

                    return resultado

                print(
                    f"[INCIDENT_RESOLVER] Fuente {nombre} encontró el incidente, "
                    f"pero no trajo nodos abiertos. Intentando siguiente fuente..."
                )

                if mejor_resultado_sin_nodos is None:
                    mejor_resultado_sin_nodos = resultado

                continue

            errores.append({
                "fuente": resultado.get("fuente", nombre),
                "error": resultado.get("error", ""),
            })

            print(
                f"[INCIDENT_RESOLVER] Fuente {nombre} no respondió OK: "
                f"{resultado.get('error', '')}"
            )

        if mejor_resultado_sin_nodos is not None:
            mejor_resultado_sin_nodos["errores_fuentes"] = errores
            return mejor_resultado_sin_nodos

        return {
            "ok": False,
            "tt_number": str(tt_number).strip().upper(),
            "fuente": "NINGUNA",
            "error": "No encontré el incidente en las fuentes disponibles.",
            "errores_fuentes": errores,
            "registros": [],
            "nodos": [],
        }