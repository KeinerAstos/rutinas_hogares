"""Nucleo NOC y contexto operativo extraido de deco_service."""

from __future__ import annotations

_EXTRACTED_NAMES = {
    "get_chat_service",
    "extraer_valor_respuesta",
    "ejecutar_motor_noc_directo",
    "es_solicitud_todos_nodos",
    "extraer_cmts_de_mensaje",
    "recordar_cmts_desde_respuesta",
    "listar_todos_nodos_cmts",
}

def configure(namespace):
    for key, value in namespace.items():
        if key.startswith("__"):
            continue
        if key in _EXTRACTED_NAMES:
            continue
        if key == "configure":
            continue
        globals()[key] = value

def get_chat_service():
    global _chat_service, _chat_error

    if _chat_service is not None:
        return _chat_service

    try:
        from app.services.chatbot.chat_service import ChatNOCService

        svc = ChatNOCService()

        if hasattr(svc, "cargar_contexto") and callable(svc.cargar_contexto):
            try:
                svc.cargar_contexto()
                print("[DECO] Contexto ChatNOCService cargado.")
            except Exception as e:
                print(f"[DECO] No se pudo cargar contexto explícito: {e}")

        _chat_service = svc
        _chat_error = None
        return _chat_service

    except Exception:
        _chat_error = traceback.format_exc()
        _chat_service = None
        return None

def extraer_valor_respuesta(texto: str, campo: str) -> str:
    if not isinstance(texto, str):
        return ""

    campo_upper = campo.upper()

    for linea in texto.splitlines():
        linea_limpia = linea.strip()

        if not linea_limpia or ":" not in linea_limpia:
            continue

        izquierda, derecha = linea_limpia.split(":", 1)

        if izquierda.strip().upper() == campo_upper:
            return derecha.strip()

    # Regex de respaldo por si llegan espacios raros
    m = re.search(rf"(?im)^\s*{re.escape(campo)}\s*:\s*(.+?)\s*$", texto)
    return m.group(1).strip() if m else ""

def ejecutar_motor_noc_directo(mensaje: str) -> Dict[str, Any]:
    svc = get_chat_service()

    if svc is None:
        return {
            "ok": False,
            "respuesta": "No se pudo cargar ChatNOCService.",
            "error": _chat_error,
        }

    for nombre in [
        "responder",
        "procesar",
        "procesar_mensaje",
        "handle_message",
        "chat",
        "preguntar",
        "ejecutar",
    ]:
        metodo = getattr(svc, nombre, None)

        if callable(metodo):
            try:
                resultado = metodo(mensaje)

                if isinstance(resultado, dict):
                    return {
                        "ok": True,
                        "respuesta": json.dumps(resultado, ensure_ascii=False, indent=2),
                        "metodo": nombre,
                    }

                return {
                    "ok": True,
                    "respuesta": str(resultado),
                    "metodo": nombre,
                }

            except Exception:
                return {
                    "ok": False,
                    "respuesta": "El motor cargó, pero falló procesando el mensaje.",
                    "metodo": nombre,
                    "error": traceback.format_exc(),
                }

    return {
        "ok": False,
        "respuesta": "No encontré método válido en ChatNOCService.",
    }

def es_solicitud_todos_nodos(mensaje: str) -> bool:
    msg = normalizar_txt(mensaje)
    frases = [
        "VER TODOS LOS NODOS",
        "MOSTRAR TODOS LOS NODOS",
        "LISTAR TODOS LOS NODOS",
        "TODOS LOS NODOS",
        "NODOS DEL CMTS",
        "LISTA NODOS",
        "LISTAR NODOS",
    ]
    return any(f in msg for f in frases)

def extraer_cmts_de_mensaje(mensaje: str) -> Optional[str]:
    """
    Permite frases como:
    - ver todos los nodos del cmts JAMU-JAMU-H-01-CS100G
    - nodos del cmts ARME-GALA-H-02-CS100G
    """

    txt = normalizar_txt(mensaje)
    for patron in [r"\bDEL\s+CMTS\s+([A-Z0-9_.-]+)", r"\bCMTS\s+([A-Z0-9_.-]+)"]:
        m = re.search(patron, txt)
        if m:
            return m.group(1).strip()
    return None

def recordar_cmts_desde_respuesta(respuesta: Any) -> None:
    """Guarda último CMTS/IP consultado para frases como 'ver todos los nodos'."""

    if isinstance(respuesta, dict):
        texto = json.dumps(respuesta, ensure_ascii=False)
    else:
        texto = str(respuesta or "")

    cmts = extraer_valor_respuesta(texto, "CMTS")
    ip = extraer_valor_respuesta(texto, "IP")

    # Fallback para JSON generado por response_builder si llega como string JSON.
    if not cmts:
        try:
            obj = json.loads(texto)
            if isinstance(obj, dict):
                cmts = obj.get("cmts") or obj.get("CMTS") or ""
                ip = obj.get("ip") or obj.get("IP") or ip
        except Exception:
            pass

    if cmts:
        _SESSION["ultimo_cmts"] = str(cmts).strip().upper()
    if ip:
        _SESSION["ultimo_ip"] = str(ip).strip()

def listar_todos_nodos_cmts(cmts_consulta: str) -> Dict[str, Any]:
    """Lista todos los nodos de un CMTS usando el inventario/macro."""

    cmts_consulta = str(cmts_consulta or "").strip().upper()

    try:
        from diagnostic_engine.core.inventory import InventoryService

        inv = InventoryService()
        inv.cargar()
        nodos = inv.buscar_nodos_por_cmts(cmts_consulta)
        nodos_ordenados = sorted(set(str(x).strip().upper() for x in nodos if str(x).strip()))
        info_cmts = inv.buscar_cmts(cmts_consulta) or {}
        ip = info_cmts.get("ip") or _SESSION.get("ultimo_ip")
        vendor = info_cmts.get("marca") or ""

        return {
            "ok": True,
            "tipo_respuesta": "cmts_nodes_list",
            "respuesta": {
                "cmts": cmts_consulta,
                "total": len(nodos_ordenados),
                "nodos": nodos_ordenados,
                "ip": ip,
                "vendor": vendor,
            },
            "metodo": "listar_todos_nodos_cmts",
        }

    except Exception:
        return {
            "ok": False,
            "respuesta": "Error listando nodos del CMTS.",
            "error": traceback.format_exc(),
        }
