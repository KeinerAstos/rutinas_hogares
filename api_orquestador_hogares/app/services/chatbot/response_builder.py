def obtener(d, *keys, default=""):
    actual = d
    for k in keys:
        if not isinstance(actual, dict):
            return default
        actual = actual.get(k)
        if actual is None:
            return default
    return actual


def _valor(d, k):
    v = d.get(k, "")
    if v is None:
        return ""
    return str(v).strip()


def construir_respuesta_diagnostico(diagnostico):
    vendor = diagnostico.get("vendor", "")
    driver = diagnostico.get("driver_usado", "")
    cmts = diagnostico.get("cmts", "")
    ip = diagnostico.get("ip", "")
    nodo = diagnostico.get("nodo", "")

    estado = diagnostico.get("estado_final", "")
    severidad = diagnostico.get("severidad_diagnostico", "")

    resumen = diagnostico.get("resumen_modems") or {}
    metricas = diagnostico.get("metricas") or {}

    total = resumen.get("total", 0)
    online = resumen.get("online", resumen.get("registered", 0))
    registered = resumen.get("registered", online)
    offline = resumen.get("offline", 0)
    active = resumen.get("active", 0)
    init = resumen.get("init", 0)

    pct_online = metricas.get("pct_online", 0)
    pct_offline = metricas.get("pct_offline", 0)

    if online == 0 and registered > 0:
        online = registered

    if online == 0 and total > 0 and pct_online > 0:
        online = round((total * pct_online) / 100)
        registered = online

    mds = diagnostico.get("mds_encontrados") or diagnostico.get("mds_configurados") or []
    upstreams = diagnostico.get("upstreams_encontrados") or diagnostico.get("upstreams") or []
    cable_mac = diagnostico.get("cable_mac", "")

    fuente_mapeo = diagnostico.get("fuente_mapeo", "")
    diagnostico_operativo = diagnostico.get("diagnostico_operativo", "")

    decision = "NO_ESCALAR_AUTOMATICO"

    if estado in (
        "AFECTACION_MASIVA_PROBABLE",
        "DEGRADACION_PROBABLE",
        "REVISAR",
        "ERROR_AUTOMATIZACION",
    ):
        decision = "REVISAR / POSIBLE ESCALAMIENTO"
    elif estado in (
        "REVISAR_DATOS_NODO_CMTS",
        "SIN_SERVICE_GROUP",
        "SIN_FIBER_NODE",
    ):
        decision = "REVISAR_DATOS_NODO_CMTS"

    lineas = []
    lineas.append("=" * 72)
    lineas.append("RESPUESTA NOC")
    lineas.append("=" * 72)
    lineas.append(f"Nodo        : {nodo}")
    lineas.append(f"CMTS        : {cmts}")
    lineas.append(f"IP          : {ip}")
    lineas.append(f"Vendor      : {vendor}")
    lineas.append(f"Driver      : {driver}")
    lineas.append("-" * 72)
    lineas.append(f"Estado      : {estado}")
    lineas.append(f"Severidad   : {severidad}")
    lineas.append(f"Decisión    : {decision}")
    lineas.append("-" * 72)
    lineas.append(f"Total       : {total}")
    lineas.append(f"Online      : {online}")
    lineas.append(f"Registered  : {registered}")
    lineas.append(f"Active      : {active}")
    lineas.append(f"Offline     : {offline}")
    lineas.append(f"Init        : {init}")
    lineas.append(f"% Online    : {pct_online}")
    lineas.append(f"% Offline   : {pct_offline}")

    if mds:
        lineas.append(f"Md          : {', '.join(mds)}")

    if upstreams:
        lineas.append(f"Upstreams   : {', '.join(upstreams)}")

    if cable_mac:
        lineas.append(f"Cable MAC   : {cable_mac}")

    if fuente_mapeo:
        lineas.append(f"Mapeo       : {fuente_mapeo}")

    lineas.append("-" * 72)

    if diagnostico_operativo:
        lineas.append("Diagnóstico :")
        lineas.append(diagnostico_operativo)
    else:
        lineas.append("Diagnóstico : Sin texto operativo disponible.")

    lineas.append("=" * 72)

    return "\n".join(lineas)


def respuesta_ayuda():
    return """
Comandos disponibles:

  estado nodo 0621
  validar nodo MEC1
  revisar nodo PQF1
  nodo COT1

  estado cmts BOGO-FONT-H-09-COS
  consultar cmts <IP_CMTS>

  validar incidente INC73677956
  consultar inc INC73677956
  INC73677956

  ayuda
  salir

Ejemplos:

  estado nodo 0621
  estado nodo PQF1
  estado nodo 39A13B
  estado nodo COT1
  validar incidente INC73677956
""".strip()


def respuesta_no_entendi():
    return """
No entendí la consulta.

Prueba con:

  estado nodo 0621
  validar nodo PQF1
  estado cmts BOGO-FONT-H-09-COS
  revisar el inc INC73677956
  ayuda
""".strip()


def decision_por_estado(estado):
    estado = str(estado or "").strip().upper()

    if estado in ("OK", "OK_SERVICE_GROUP_SIN_MODEMS"):
        return "NO_ESCALAR_AUTOMATICO"

    if estado in (
        "AFECTACION_MASIVA_PROBABLE",
        "DEGRADACION_PROBABLE",
        "ERROR_AUTOMATIZACION",
    ):
        return "REVISAR / POSIBLE ESCALAMIENTO"

    if estado in (
        "REVISAR",
        "REVISAR_DATOS_NODO_CMTS",
        "SIN_SERVICE_GROUP",
        "SIN_FIBER_NODE",
        "PENDIENTE_CLASIFICAR",
    ):
        return "REVISAR_DATOS_NODO_CMTS"

    if estado.startswith("NO_APLICA"):
        return "NO_APLICA"

    return "REVISAR"


def construir_respuesta_incidente(resultado_incidente, diagnosticos):
    tt = resultado_incidente.get("tt_number", "")
    fuente = resultado_incidente.get("fuente", "")
    nodos = resultado_incidente.get("nodos") or []

    # DEBUG TEMPORAL.
    # Cuando confirmes que ya aparece el bloque de Máximo, puedes borrar este print.
    print(
        f"[DEBUG_RESPONSE] fuente={fuente} "
        f"contexto_maximo={bool(resultado_incidente.get('contexto_maximo'))} "
        f"fuente_contexto={resultado_incidente.get('fuente_contexto', '')}"
    )

    lineas = []
    lineas.append("=" * 72)
    lineas.append("RESPUESTA INCIDENTE NOC")
    lineas.append("=" * 72)
    lineas.append(f"Incidente     : {tt}")
    lineas.append(f"Fuente        : {fuente}")
    lineas.append(f"Nodos detect. : {len(nodos)}")
    lineas.append("-" * 72)

    if not nodos:
        lineas.append("No se detectaron nodos asociados al incidente.")

        bloque_maximo = construir_bloque_contexto_maximo(resultado_incidente)
        if bloque_maximo:
            lineas.extend(bloque_maximo)

        lineas.append("=" * 72)
        return "\n".join(lineas)

    conteo_decisiones = {}
    filas = []

    for d in diagnosticos:
        nodo = d.get("nodo", "")
        cmts = d.get("cmts", "")
        vendor = d.get("vendor") or d.get("driver_usado", "")
        estado = d.get("estado_final", "")
        severidad = d.get("severidad_diagnostico", "")

        resumen = d.get("resumen_modems") or {}
        metricas = d.get("metricas") or {}

        total = resumen.get("total", 0)
        online = resumen.get("online", resumen.get("registered", 0))
        registered = resumen.get("registered", 0)
        offline = resumen.get("offline", 0)
        pct_online = metricas.get("pct_online", 0)

        if online == 0 and registered > 0:
            online = registered

        if online == 0 and total > 0 and pct_online > 0:
            online = round((total * pct_online) / 100)

        decision = decision_por_estado(estado)
        conteo_decisiones[decision] = conteo_decisiones.get(decision, 0) + 1

        filas.append({
            "nodo": nodo,
            "cmts": cmts,
            "vendor": vendor,
            "estado": estado,
            "severidad": severidad,
            "total": total,
            "online": online,
            "offline": offline,
            "pct_online": pct_online,
            "decision": decision,
        })

    # Decisión consolidada
    if any(x["decision"] == "REVISAR / POSIBLE ESCALAMIENTO" for x in filas):
        decision_final = "REVISAR / POSIBLE ESCALAMIENTO"
    elif any(x["decision"] == "REVISAR_DATOS_NODO_CMTS" for x in filas):
        if any(x["decision"] == "NO_ESCALAR_AUTOMATICO" for x in filas):
            decision_final = "REVISAR_PARCIALMENTE"
        else:
            decision_final = "REVISAR_DATOS_NODO_CMTS"
    elif all(x["decision"] == "NO_ESCALAR_AUTOMATICO" for x in filas):
        decision_final = "NO_ESCALAR_AUTOMATICO"
    else:
        decision_final = "REVISAR"

    lineas.append(f"Decisión INC  : {decision_final}")
    lineas.append("-" * 72)

    for f in filas:
        lineas.append(
            f"{f['nodo']} | {f['vendor']} | {f['estado']} | "
            f"Online {f['online']}/{f['total']} ({f['pct_online']}%) | "
            f"Offline {f['offline']} | {f['decision']}"
        )

    # AQUÍ ESTABA FALTANDO ESTA LLAMADA.
    # Esto permite que aparezca el contexto de Máximo aunque el diagnóstico venga del CSV.
    bloque_maximo = construir_bloque_contexto_maximo(resultado_incidente)
    if bloque_maximo:
        lineas.extend(bloque_maximo)

    lineas.append("-" * 72)
    lineas.append("Resumen decisiones:")

    for decision, cantidad in conteo_decisiones.items():
        lineas.append(f"  {decision}: {cantidad}")

    lineas.append("=" * 72)

    return "\n".join(lineas)


def construir_bloque_contexto_maximo(resultado_incidente):
    contexto_maximo = resultado_incidente.get("contexto_maximo") or resultado_incidente

    if contexto_maximo.get("fuente") != "MAXIMO":
        return []

    detalle = contexto_maximo.get("detalle_incidente") or {}
    tickets = contexto_maximo.get("tickets_relacionados") or []
    ordenes = (
        contexto_maximo.get("ordenes_relacionadas")
        or contexto_maximo.get("registros")
        or []
    )
    alarmas = contexto_maximo.get("alarmas_relacionadas") or []

    if not detalle and not tickets and not ordenes and not alarmas:
        return []

    lineas = []
    lineas.append("-" * 72)
    lineas.append("Contexto Máximo:")

    fuente_diag = resultado_incidente.get("fuente_diagnostico")
    fuente_contexto = resultado_incidente.get("fuente_contexto")

    if fuente_diag and fuente_contexto:
        lineas.append(
            f"Nota             : Diagnóstico tomado de {fuente_diag}; "
            f"contexto operativo tomado de {fuente_contexto}."
        )

    resumen = _valor(detalle, "resumen")
    detalles = _valor(detalle, "detalles")
    grupo = _valor(detalle, "grupo_propietario")
    creado_por = _valor(detalle, "creado_por")
    flujo = _valor(detalle, "flujo_creacion")
    impacto = _valor(detalle, "impacto")
    urgencia = _valor(detalle, "urgencia")
    prioridad = _valor(detalle, "prioridad")
    ci_principal = _valor(detalle, "articulo_configuracion_principal")
    fecha_creacion = _valor(detalle, "fecha_creacion")
    fecha_inicio = _valor(detalle, "fecha_inicio")
    fecha_resolucion = _valor(detalle, "fecha_resolucion")

    if resumen:
        lineas.append(f"Resumen          : {resumen}")

    if grupo or creado_por or flujo:
        lineas.append(f"Grupo/creado/flujo: {grupo} | {creado_por} | {flujo}")

    if impacto or urgencia or prioridad:
        lineas.append(f"Impacto/Urg/Prior: {impacto} / {urgencia} / {prioridad}")

    if ci_principal:
        lineas.append(f"CI principal     : {ci_principal}")

    if fecha_creacion or fecha_inicio or fecha_resolucion:
        lineas.append(
            f"Fechas           : Creación={fecha_creacion} | "
            f"Inicio={fecha_inicio} | Resolución={fecha_resolucion}"
        )

    if detalles:
        detalles_corto = detalles
        if len(detalles_corto) > 350:
            detalles_corto = detalles_corto[:350] + "..."
        lineas.append(f"Detalles         : {detalles_corto}")

    lineas.append(
        f"Relacionados     : Tickets={len(tickets)} | "
        f"OTs={len(ordenes)} | Alarmas={len(alarmas)}"
    )

    if ordenes:
        lineas.append("")
        lineas.append("Órdenes relacionadas en Máximo:")

        for i, o in enumerate(ordenes[:5], start=1):
            lineas.append(
                f"{i}. OT {o.get('orden_trabajo', '')} | "
                f"Estado: {o.get('estado', '')} | "
                f"Tipo: {o.get('tipo_trabajo', '')} | "
                f"Nodo/CI: {o.get('articulo_configuracion', '')}"
            )

            if o.get("ubicacion"):
                lineas.append(f"   Ubicación: {o.get('ubicacion', '')}")

            if o.get("resolutor"):
                lineas.append(f"   Resolutor: {o.get('resolutor', '')}")

            if o.get("fecha_creacion"):
                lineas.append(f"   Fecha creación: {o.get('fecha_creacion', '')}")

            if o.get("descripcion"):
                desc = o.get("descripcion", "")
                if len(desc) > 250:
                    desc = desc[:250] + "..."
                lineas.append(f"   Desc     : {desc}")

        if len(ordenes) > 5:
            lineas.append(f"... y {len(ordenes) - 5} órdenes más.")

    if alarmas:
        lineas.append("")
        lineas.append("Alarmas relacionadas en Máximo:")

        for i, a in enumerate(alarmas[:5], start=1):
            lineas.append(
                f"{i}. {a.get('nombre_alarma', '')} | "
                f"Elemento: {a.get('elemento_red', '')} | "
                f"Tec: {a.get('tecnologia', '')} | "
                f"Activa: {a.get('alarma_activa', '')}"
            )

            lineas.append(
                f"   Inicio: {a.get('fecha_inicio_alarma', '')} | "
                f"Fin: {a.get('fecha_fin_alarma', '')} | "
                f"Cancelado por: {a.get('cancelado_por', '')}"
            )

        if len(alarmas) > 5:
            lineas.append(f"... y {len(alarmas) - 5} alarmas más.")

    if tickets:
        lineas.append("")
        lineas.append("Tickets relacionados en Máximo:")

        for i, t in enumerate(tickets[:5], start=1):
            lineas.append(
                f"{i}. {t.get('clave', '')} | "
                f"{t.get('clase', '')} | "
                f"{t.get('estado', '')} | "
                f"{t.get('relacion', '')} | "
                f"{t.get('descripcion', '')}"
            )

        if len(tickets) > 5:
            lineas.append(f"... y {len(tickets) - 5} tickets más.")

    return lineas


def construir_respuesta_maximo_sin_nodos(resultado_incidente):
    tt = resultado_incidente.get("tt_number", "")
    fuente = resultado_incidente.get("fuente", "")
    registros = resultado_incidente.get("registros") or []

    estados_cerrados = {
        "CAN",
        "CLOSE",
        "CLOSED",
        "CERRADO",
        "CANCELADO",
        "RESOLVED",
        "RESUELTO",
    }

    ordenes_abiertas = []
    ordenes_cerradas = []
    nodos_ci = []

    for r in registros:
        estado = str(r.get("estado", "") or "").strip().upper()
        nodo_ci = str(r.get("articulo_configuracion", "") or "").strip().upper()

        if nodo_ci and nodo_ci not in nodos_ci:
            nodos_ci.append(nodo_ci)

        if estado in estados_cerrados:
            ordenes_cerradas.append(r)
        else:
            ordenes_abiertas.append(r)

    lineas = []
    lineas.append("=" * 72)
    lineas.append("INCIDENTE EN MÁXIMO")
    lineas.append("=" * 72)
    lineas.append(f"Incidente        : {tt}")
    lineas.append(f"Fuente           : {fuente}")
    lineas.append(f"Órdenes total    : {len(registros)}")
    lineas.append(f"Órdenes abiertas : {len(ordenes_abiertas)}")
    lineas.append(f"Órdenes cerradas : {len(ordenes_cerradas)}")
    lineas.append(f"Nodos/CI detect. : {len(nodos_ci)}")

    if nodos_ci:
        lineas.append(f"Nodos/CI         : {', '.join(nodos_ci)}")

    lineas.append("-" * 72)

    if not ordenes_abiertas:
        lineas.append("No hay nodos abiertos para diagnosticar automáticamente.")
    else:
        lineas.append("Hay órdenes abiertas, pero no se detectaron nodos válidos para diagnóstico.")

    bloque_maximo = construir_bloque_contexto_maximo(resultado_incidente)
    if bloque_maximo:
        lineas.extend(bloque_maximo)

    lineas.append("-" * 72)
    lineas.append("Órdenes relacionadas:")

    if not registros:
        lineas.append("No se encontraron órdenes relacionadas.")
    else:
        for i, r in enumerate(registros, start=1):
            ot = r.get("orden_trabajo", "")
            estado = r.get("estado", "")
            nodo_ci = r.get("articulo_configuracion", "")
            tipo = r.get("tipo_trabajo", "")
            clase = r.get("clase", "")
            ubicacion = r.get("ubicacion", "")
            resolutor = r.get("resolutor", "")
            fecha_creacion = r.get("fecha_creacion", "")
            relacionado_por = r.get("relacionado_por", "")
            fecha_relacion = r.get("fecha_relacion", "")
            descripcion = r.get("descripcion", "")

            lineas.append("")
            lineas.append(f"{i}. OT: {ot} | Estado: {estado} | Nodo/CI: {nodo_ci}")

            if tipo:
                lineas.append(f"   Tipo trabajo   : {tipo}")
            if clase:
                lineas.append(f"   Clase          : {clase}")
            if ubicacion:
                lineas.append(f"   Ubicación      : {ubicacion}")
            if resolutor:
                lineas.append(f"   Resolutor      : {resolutor}")
            if fecha_creacion:
                lineas.append(f"   Fecha creación : {fecha_creacion}")
            if relacionado_por:
                lineas.append(f"   Relacionado por: {relacionado_por}")
            if fecha_relacion:
                lineas.append(f"   Fecha relación : {fecha_relacion}")
            if descripcion:
                lineas.append(f"   Descripción    : {descripcion}")

    lineas.append("-" * 72)
    lineas.append("Acción sugerida:")

    if registros and not ordenes_abiertas:
        lineas.append(
            "No ejecutar diagnóstico automático porque las órdenes relacionadas "
            "están cerradas, canceladas o resueltas."
        )
    elif ordenes_abiertas:
        lineas.append(
            "Revisar manualmente las órdenes abiertas porque no se logró extraer "
            "un nodo válido para diagnóstico automático."
        )
    else:
        lineas.append(
            "Revisar el incidente en Máximo porque no se encontraron órdenes relacionadas."
        )

    lineas.append("=" * 72)

    return "\n".join(lineas)