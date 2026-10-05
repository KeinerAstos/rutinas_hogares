from __future__ import annotations

import re
import unicodedata
from typing import Any


SERVICE_VERSION = "GES_DIRECCIONES_NOTAS_V2"


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.upper()


def _parse_account_line(line: str) -> dict[str, str] | None:
    """
    Formato observado:
        40732485    CR 70 66B-11 508

    Se exige una cuenta numérica de 6 a 12 dígitos al inicio.
    Todo lo restante se conserva como dirección.
    """
    match = re.match(
        r"^\s*(\d{6,12})\s+(.+?)\s*$",
        line or "",
    )

    if not match:
        return None

    cuenta = _clean(match.group(1))
    direccion = _clean(match.group(2))

    if not cuenta or not direccion:
        return None

    return {
        "cuenta": cuenta,
        "direccion": direccion,
    }


def extraer_cuentas_afectadas_desde_texto(
    texto: str,
) -> list[dict[str, str]]:
    """
    Busca el bloque CUENTAS AFECTADAS dentro de una nota.

    El bloque termina cuando aparece:
    - una línea vacía después de haber encontrado cuentas,
    - un encabezado conocido posterior,
    - o el final de la nota.
    """
    raw = str(texto or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    resultados: list[dict[str, str]] = []
    dentro_bloque = False

    stop_headers = (
        "SE ADJUNTAN",
        "TELEMETRI",
        "OBSERVACION",
        "OPCIONES",
        "CALIDAD TIEMPO REAL",
        "SE ENVIA",
        "SE ENVÍA",
        "DIAGNOSTICADOR",
    )

    for line in lines:
        normalized = _normalize(line).strip()

        if not dentro_bloque:
            if re.search(r"\bCUENTAS?\s+AFECTADAS?\b", normalized):
                dentro_bloque = True
            continue

        # Permitir espacios en blanco antes del primer dato.
        if not normalized:
            if resultados:
                break
            continue

        if resultados and any(
            normalized.startswith(header)
            for header in stop_headers
        ):
            break

        parsed = _parse_account_line(line)

        if parsed:
            resultados.append(parsed)
            continue

        # Si ya veníamos recolectando cuentas y aparece texto que
        # claramente no pertenece a una fila, cerramos el bloque.
        if resultados:
            break

    # GES_DIRECCIONES_PIPE_TABLE_V2
    # Fallback para notas GES con formato:
    #
    #   C. RR|MAC|DIRECCION|NODO
    #   10958056|f4:95:...|CL 111 5-98 202|4A0033
    #
    # El formato historico CUENTAS AFECTADAS conserva prioridad.
    # Este fallback se usa solo si no se obtuvieron filas antes.
    if not resultados:
        header_index = None

        for index, line in enumerate(lines):
            normalized_header = (
                _normalize(line)
                .replace(" ", "")
                .replace(".", "")
            )

            if "CRR|MAC|DIRECCION|NODO" in normalized_header:
                header_index = index
                break

        if header_index is not None:
            for line in lines[header_index + 1:]:
                raw_line = str(line or "").strip()

                if not raw_line:
                    if resultados:
                        break
                    continue

                if "|" not in raw_line:
                    if resultados:
                        break
                    continue

                columns = [
                    _clean(part)
                    for part in raw_line.split("|")
                ]

                if len(columns) < 4:
                    continue

                cuenta = columns[0]
                direccion = columns[2]

                if not re.fullmatch(r"\d{6,12}", cuenta):
                    continue

                # Ejemplo:
                # 93627814|CUENTA PRINCIPAL||4A0033
                # Sin direccion no se publica como cliente.
                if not direccion:
                    continue

                resultados.append(
                    {
                        "cuenta": cuenta,
                        "direccion": direccion,
                    }
                )

    # Algunas notas registran una o varias cuentas sin dirección.
    # Solo se acepta la etiqueta CUENTA(S), nunca Cuenta Matriz, TEL o TAS.
    cuentas_etiquetadas: list[str] = []
    for line in lines:
        match = re.match(r"^\s*CUENTAS?\s*:\s*(.*)$", line, re.IGNORECASE)
        if not match:
            continue
        # Aceptar listas separadas por coma, punto y coma o espacios.
        value = match.group(1).split("OBSERVACIONES:", 1)[0]
        cuentas_etiquetadas.extend(
            re.findall(r"(?<!\d)\d{6,12}(?!\d)", value)
        )

    cuentas_con_direccion = {item["cuenta"] for item in resultados}
    for cuenta in cuentas_etiquetadas:
        if cuenta not in cuentas_con_direccion:
            resultados.append({"cuenta": cuenta, "direccion": ""})
            cuentas_con_direccion.add(cuenta)

    # Dedupe por cuenta; prevalece la fila que trae dirección.
    final: list[dict[str, str]] = []
    positions: dict[str, int] = {}

    for item in resultados:
        cuenta = item["cuenta"]
        if cuenta in positions:
            if item["direccion"] and not final[positions[cuenta]]["direccion"]:
                final[positions[cuenta]] = item
        else:
            positions[cuenta] = len(final)
            final.append(item)

    return final


def extraer_cuentas_afectadas_desde_notas(
    notas: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    notas = notas if isinstance(notas, list) else []

    clientes: list[dict[str, Any]] = []
    notas_con_cuentas = 0
    positions: dict[str, int] = {}

    for note in notas:
        if not isinstance(note, dict):
            continue

        indice = int(note.get("indice") or 0)
        texto = str(note.get("texto") or "")

        encontrados = extraer_cuentas_afectadas_desde_texto(texto)

        if encontrados:
            notas_con_cuentas += 1

        for item in encontrados:
            cuenta = item["cuenta"]
            if cuenta in positions:
                if item["direccion"] and not clientes[positions[cuenta]]["direccion"]:
                    clientes[positions[cuenta]]["direccion"] = item["direccion"]
                    clientes[positions[cuenta]]["nota_indice"] = indice
                continue
            positions[cuenta] = len(clientes)

            clientes.append(
                {
                    "cuenta": item["cuenta"],

                    # GES_ALIAS_CUENTA_SERIAL_V1_2
                    # Alias de presentacion para ATLAS.
                    # El valor real sigue siendo una cuenta.
                    "serial": item["cuenta"],

                    "direccion": item["direccion"],
                    "fuente": "NOTA_HELIX",
                    "nota_indice": indice,
                }
            )

    if clientes:
        codigo = "GES_DIRECCIONES_DESDE_NOTAS"
        respuesta = (
            f"Se encontraron {len(clientes)} cuenta(s) afectada(s) "
            "en las notas de Helix."
        )
        ok = True
    elif notas:
        codigo = "GES_SIN_CUENTAS_EN_NOTAS"
        respuesta = (
            "Se revisaron las notas de Helix, pero no se encontró "
            "un bloque de CUENTAS AFECTADAS con direcciones."
        )
        ok = False
    else:
        codigo = "GES_SIN_NOTAS"
        respuesta = (
            "Helix no entregó notas de Actividad/WorkLog para esta gestión GES."
        )
        ok = False

    return {
        "ok": ok,
        "codigo": codigo,
        "tipo_respuesta": "ges_direcciones_notas",
        "fuente": "NOTAS_HELIX",
        "notas_revisadas": len(notas),
        "notas_con_cuentas": notas_con_cuentas,
        "clientes_encontrados": len(clientes),
        "clientes": clientes,
        "respuesta": respuesta,
        "version": SERVICE_VERSION,
    }
