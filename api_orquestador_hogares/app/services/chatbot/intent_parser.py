import re


def limpiar_texto(txt):
    if txt is None:
        return ""
    return str(txt).strip()


def normalizar(txt):
    return limpiar_texto(txt).upper()


def parsear_mensaje(mensaje):
    """
    Parser simple por reglas.
    No usa IA. Esto evita respuestas inventadas.

    Intenciones soportadas:
    - AYUDA
    - SALIR
    - CONSULTAR_INCIDENTE
    - CONSULTAR_NODO
    - CONSULTAR_CMTS
    - DESCONOCIDA
    """
    msg_original = limpiar_texto(mensaje)
    msg = normalizar(msg_original)

    if not msg:
        return {
            "intent": "VACIO",
            "mensaje_original": msg_original,
        }

    if msg in ("SALIR", "EXIT", "QUIT", "CHAO", "CERRAR"):
        return {
            "intent": "SALIR",
            "mensaje_original": msg_original,
        }

    if msg in ("AYUDA", "HELP", "COMANDOS", "?"):
        return {
            "intent": "AYUDA",
            "mensaje_original": msg_original,
        }

    # ==========================================================
    # INCIDENTES
    # ==========================================================

    # validar incidente INC73677956
    # consultar inc INC73677956
    # revisar caso INC73677956
    # cómo está el incidente INC73677956
    m = re.search(
        r"\b(?:VALIDAR|REVISA|REVISAR|CONSULTA|CONSULTAR|DIAGNOSTICA|DIAGNOSTICAR|ESTADO|MIRA|VER|BUSCA|BUSCAR|COMO ESTA|CÓMO ESTÁ|QUE PASA CON|QUÉ PASA CON)\s+"
        r"(?:EL\s+|LA\s+)?"
        r"(?:INCIDENTE|INC|TT|CASO)\s+"
        r"([A-Z]{2,10}\d{4,})\b",
        msg,
        re.IGNORECASE,
    )
    if m:
        return {
            "intent": "CONSULTAR_INCIDENTE",
            "tt_number": m.group(1).strip().upper(),
            "mensaje_original": msg_original,
        }

    # Si aparece un INC en cualquier parte del mensaje
    m = re.search(r"\b([A-Z]{2,10}\d{4,})\b", msg, re.IGNORECASE)
    if m and m.group(1).upper().startswith("INC"):
        return {
            "intent": "CONSULTAR_INCIDENTE",
            "tt_number": m.group(1).strip().upper(),
            "mensaje_original": msg_original,
        }

    # Si solo escribe INC73677956
    m = re.fullmatch(r"([A-Z]{2,10}\d{4,})", msg, re.IGNORECASE)
    if m:
        return {
            "intent": "CONSULTAR_INCIDENTE",
            "tt_number": m.group(1).strip().upper(),
            "mensaje_original": msg_original,
        }

    # ==========================================================
    # CMTS / IP
    # ==========================================================

    # estado cmts BOGO-FONT-H-09-COS
    # consultar cmts 172.31.254.188
    # qué sabes del cmts BOGO-FONT-H-09-COS
    m = re.search(
        r"\b(?:ESTADO|VALIDAR|REVISA|REVISAR|CONSULTA|CONSULTAR|RESUMEN|MIRA|VER|BUSCA|BUSCAR|QUE SABES DE|QUÉ SABES DE|COMO ESTA|CÓMO ESTÁ)?\s*"
        r"(?:EL\s+|LA\s+)?"
        r"CMTS\s+([A-Z0-9_.:-]+)\b",
        msg,
        re.IGNORECASE,
    )
    if m:
        return {
            "intent": "CONSULTAR_CMTS",
            "cmts_o_ip": m.group(1).strip(),
            "mensaje_original": msg_original,
        }

    # consulta la ip 172.31.254.188
    # estado ip 172.31.254.188
    m = re.search(
        r"\b(?:IP)\s+(\d{1,3}(?:\.\d{1,3}){3})\b",
        msg,
        re.IGNORECASE,
    )
    if m:
        return {
            "intent": "CONSULTAR_CMTS",
            "cmts_o_ip": m.group(1).strip(),
            "mensaje_original": msg_original,
        }

    # Si solo escribe una IP
    m = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3})", msg)
    if m:
        return {
            "intent": "CONSULTAR_CMTS",
            "cmts_o_ip": m.group(1).strip(),
            "mensaje_original": msg_original,
        }

    # ==========================================================
    # NODOS
    # ==========================================================

    # estado nodo 0621
    # validar nodo MEC1
    # cómo está el nodo PQF1
    # mira el nodo COT1
    m = re.search(
        r"\b(?:ESTADO|VALIDAR|REVISA|REVISAR|CONSULTA|CONSULTAR|DIAGNOSTICA|DIAGNOSTICAR|MIRA|VER|BUSCA|BUSCAR|COMO ESTA|CÓMO ESTÁ|QUE PASA CON|QUÉ PASA CON)\s+"
        r"(?:EL\s+|LA\s+)?"
        r"NODO\s+([A-Z0-9_-]+)\b",
        msg,
        re.IGNORECASE,
    )
    if m:
        return {
            "intent": "CONSULTAR_NODO",
            "nodo": m.group(1).strip(),
            "mensaje_original": msg_original,
        }

    # nodo 0621
    m = re.search(r"\bNODO\s+([A-Z0-9_-]+)\b", msg, re.IGNORECASE)
    if m:
        return {
            "intent": "CONSULTAR_NODO",
            "nodo": m.group(1).strip(),
            "mensaje_original": msg_original,
        }

    # Si el usuario solo escribe algo que parece nodo: 0621, MEC1, PQF1
    # OJO: INC e IP ya fueron capturados arriba.
    if re.fullmatch(r"[A-Z0-9_-]{2,15}", msg):
        return {
            "intent": "CONSULTAR_NODO",
            "nodo": msg,
            "mensaje_original": msg_original,
        }

    return {
        "intent": "DESCONOCIDA",
        "mensaje_original": msg_original,
    }