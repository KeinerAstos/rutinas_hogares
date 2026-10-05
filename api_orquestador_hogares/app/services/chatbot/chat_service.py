from app.services.chatbot.intent_parser import parsear_mensaje
from app.services.chatbot.response_builder import (
    construir_respuesta_diagnostico,
    construir_respuesta_incidente,
    respuesta_ayuda,
    respuesta_no_entendi,
)
from app.services.incident_sources.incident_resolver import IncidentResolver
from diagnostic_engine.core.inventory import InventoryService
from diagnostic_engine.core.diagnostic_service import DiagnosticService


class ChatNOCService:
    def __init__(self, macro_file=None):
        self.inventory = InventoryService(macro_file=macro_file)
        self.diagnostic_service = DiagnosticService(inventory=self.inventory)
        self.incident_resolver = IncidentResolver()
    
    def cargar_contexto(self):
        self.diagnostic_service.cargar_contexto()

    def diagnosticar_nodo(self, nodo):
        diagnostico = self.diagnostic_service.diagnosticar_nodo(
            nodo=nodo,
            imprimir=False,
        )

        return construir_respuesta_diagnostico(diagnostico)
    def diagnosticar_incidente(self, tt_number):
        resultado_inc = self.incident_resolver.resolver_incidente(tt_number)

        if not resultado_inc.get("ok"):
            return (
                f"No encontré el incidente {tt_number} en las fuentes disponibles.\n"
                f"Detalle: {resultado_inc.get('error', '')}\n\n"
                f"Por ahora las fuentes activas son CSV locales. "
                f"Después conectamos Máximo/API/Playwright sin cambiar el chatbot."
            )

        nodos = resultado_inc.get("nodos") or []

        nodos_originales = resultado_inc.get("nodos") or []

        if not nodos_originales:
            registros = resultado_inc.get("registros") or []

            if registros:
                lineas = []
                lineas.append(f"Encontré el incidente {tt_number} en {resultado_inc.get('fuente', '')}, pero no hay nodos abiertos para diagnosticar.")
                lineas.append("")
                lineas.append("Órdenes encontradas:")

                for r in registros[:10]:
                    lineas.append(
                        f"- OT: {r.get('orden_trabajo', '')} | "
                        f"Estado: {r.get('estado', '')} | "
                        f"Nodo/CI: {r.get('articulo_configuracion', '')}"
                    )

                if len(registros) > 10:
                    lineas.append(f"... y {len(registros) - 10} órdenes más.")

                return "\n".join(lineas)

            return (
                f"Encontré el incidente {tt_number}, pero no pude detectar nodos asociados.\n"
                f"Fuente: {resultado_inc.get('fuente', '')}"
            )
        
        nodos_validos = []
        nodos_descartados = []

        for nodo in nodos_originales:
            if self.inventory.buscar_nodo(nodo):
                nodos_validos.append(nodo)
            else:
                nodos_descartados.append(nodo)

        nodos = nodos_validos

        if not nodos:
            return (
                f"Encontré el incidente {tt_number}, pero ninguno de los posibles nodos existe en la macro.\n"
                f"Detectados originalmente: {', '.join(nodos_originales)}\n"
                f"Fuente: {resultado_inc.get('fuente', '')}"
            )

        diagnosticos = []

        print(f"Incidente {tt_number} encontrado. Nodos válidos: {', '.join(nodos)}")

        if nodos_descartados:
            print(f"Nodos descartados/no válidos: {', '.join(nodos_descartados)}")

        for i, nodo in enumerate(nodos, start=1):
            print(f"[{i}/{len(nodos)}] Diagnosticando nodo {nodo} del incidente {tt_number}...")

            diagnostico = self.diagnostic_service.diagnosticar_nodo(
                nodo=nodo,
                imprimir=False,
            )

            diagnosticos.append(diagnostico)

        return construir_respuesta_incidente(
            resultado_incidente=resultado_inc,
            diagnosticos=diagnosticos,
        )
    def consultar_cmts(self, cmts_o_ip):
        info = self.diagnostic_service.consultar_cmts(cmts_o_ip)

        if not info.get("ok"):
            return info.get("error", "No se pudo consultar el CMTS.")

        cmts_name = info.get("cmts")
        ip = info.get("ip")
        marca = info.get("marca")
        driver = info.get("driver")
        nodos = info.get("nodos") or []
        total_nodos = info.get("total_nodos", len(nodos))

        lineas = []
        lineas.append("=" * 72)
        lineas.append("RESUMEN CMTS")
        lineas.append("=" * 72)
        lineas.append(f"CMTS       : {cmts_name}")
        lineas.append(f"IP         : {ip}")
        lineas.append(f"Marca      : {marca}")
        lineas.append(f"Driver     : {driver}")
        lineas.append(f"Nodos macro: {total_nodos}")
        lineas.append("-" * 72)

        if nodos:
            muestra = nodos[:30]
            lineas.append("Primeros nodos:")
            lineas.append(", ".join(muestra))

            if len(nodos) > 30:
                lineas.append(f"... y {len(nodos) - 30} nodos más.")
        else:
            lineas.append("No encontré nodos asociados en la macro.")

        lineas.append("=" * 72)

        return "\n".join(lineas)

    def responder(self, mensaje):
        intent = parsear_mensaje(mensaje)
        tipo = intent.get("intent")

        if tipo == "VACIO":
            return ""

        if tipo == "SALIR":
            return "__SALIR__"

        if tipo == "AYUDA":
            return respuesta_ayuda()

        if tipo == "CONSULTAR_INCIDENTE":
            return self.diagnosticar_incidente(intent.get("tt_number"))

        if tipo == "CONSULTAR_NODO":
            return self.diagnosticar_nodo(intent.get("nodo"))

        if tipo == "CONSULTAR_CMTS":
            return self.consultar_cmts(intent.get("cmts_o_ip"))

        return respuesta_no_entendi()