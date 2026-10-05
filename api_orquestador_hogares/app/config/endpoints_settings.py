from __future__ import annotations

import os


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()

    if not value:
        raise RuntimeError(
            f"Falta configurar {name} en .env."
        )

    return value


def get_smartit_url() -> str:
    return _required("SMARTIT_URL")


def get_smartit_app_base_url() -> str:
    url = get_smartit_url()

    if "#/" in url:
        return url.split("#/", 1)[0].rstrip("/") + "/#/"

    return url.rstrip("/") + "/#/"


# ATLAS_REDES_NEUTRAS_EXTERNAL_API_V1
def get_redes_neutras_api_url() -> str:
    return _required("REDES_NEUTRAS_API_URL").rstrip("/")


def get_redes_neutras_api_timeout() -> float:
    raw = _required("REDES_NEUTRAS_API_TIMEOUT")

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "REDES_NEUTRAS_API_TIMEOUT debe ser numerico."
        ) from exc

    if value <= 0:
        raise RuntimeError(
            "REDES_NEUTRAS_API_TIMEOUT debe ser mayor que cero."
        )

    return value


# ATLAS_HELIX_RELACIONADOS_EXTERNAL_API_V2
def get_helix_relacionados_api_url() -> str:
    return _required(
        "HELIX_RELACIONADOS_API_URL"
    ).rstrip("/")


def get_helix_relacionados_api_timeout() -> float:
    raw = _required(
        "HELIX_RELACIONADOS_API_TIMEOUT"
    )

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "HELIX_RELACIONADOS_API_TIMEOUT debe ser numerico."
        ) from exc

    if value <= 0:
        raise RuntimeError(
            "HELIX_RELACIONADOS_API_TIMEOUT debe ser mayor que cero."
        )

    return value


# ATLAS_DIRECCIONES_EXTERNAL_API_V1
def get_direcciones_api_url() -> str:
    return _required(
        "DIRECCIONES_API_URL"
    ).rstrip("/")


def get_direcciones_api_timeout() -> float:
    raw = _required(
        "DIRECCIONES_API_TIMEOUT"
    )

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "DIRECCIONES_API_TIMEOUT debe ser numerico."
        ) from exc

    if value <= 0:
        raise RuntimeError(
            "DIRECCIONES_API_TIMEOUT debe ser mayor que cero."
        )

    return value

# ATLAS_PATHTRAK_EXTERNAL_API_V1
def get_pathtrak_captures_api_url() -> str:
    return _required(
        "PATHTRAK_CAPTURES_API_URL"
    ).rstrip("/")


def get_pathtrak_captures_api_timeout() -> float:
    raw = _required(
        "PATHTRAK_CAPTURES_API_TIMEOUT"
    )

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "PATHTRAK_CAPTURES_API_TIMEOUT debe ser numerico."
        ) from exc

    if value <= 0:
        raise RuntimeError(
            "PATHTRAK_CAPTURES_API_TIMEOUT debe ser mayor que cero."
        )

    return value

# ATLAS_BITACORA_HELIX_EXTERNAL_API_V1
def get_bitacora_helix_api_url() -> str:
    return _required("BITACORA_HELIX_API_URL").rstrip("/")


def get_bitacora_helix_api_timeout() -> float:
    raw = _required("BITACORA_HELIX_API_TIMEOUT")

    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "BITACORA_HELIX_API_TIMEOUT debe ser numerico."
        ) from exc

    if value <= 0:
        raise RuntimeError(
            "BITACORA_HELIX_API_TIMEOUT debe ser mayor que cero."
        )

    return value

# ATLAS_CONFIRMACIONES_EXTERNAL_API_V1
def get_confirmaciones_api_url() -> str:
    return _required(
        "CONFIRMACIONES_API_URL"
    ).rstrip("/")


def get_confirmaciones_api_timeout() -> float:
    raw = _required(
        "CONFIRMACIONES_API_TIMEOUT"
    )

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "CONFIRMACIONES_API_TIMEOUT debe ser numerico."
        ) from exc

    if value <= 0:
        raise RuntimeError(
            "CONFIRMACIONES_API_TIMEOUT debe ser mayor que cero."
        )

    return value

# ATLAS_OT_RELACIONADA_API_8027_V1
def get_ot_relacionada_api_url() -> str:
    return _required("OT_RELACIONADA_API_URL").rstrip("/")


def get_ot_relacionada_api_timeout() -> float:
    value = os.getenv("OT_RELACIONADA_API_TIMEOUT", "420")
    try:
        return float(value)
    except Exception:
        return 420.0

# ATLAS_DISPOSITIVOS_EXTERNAL_API_V1
def get_dispositivos_api_url() -> str:
    return _required(
        "DISPOSITIVOS_API_URL"
    ).rstrip("/")


def get_dispositivos_api_timeout() -> float:
    raw = _required(
        "DISPOSITIVOS_API_TIMEOUT"
    )

    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "DISPOSITIVOS_API_TIMEOUT debe ser numerico."
        ) from exc

    if value <= 0:
        raise RuntimeError(
            "DISPOSITIVOS_API_TIMEOUT debe ser mayor que cero."
        )

    return value

# ATLAS_HFC_8030_CLIENT_V2
def get_hfc_api_base_url() -> str:
    return os.getenv(
        "ATLAS_HFC_API_BASE_URL",
        "http://127.0.0.1:8030",
    ).rstrip("/")


def get_hfc_api_timeout() -> int:
    return int(
        os.getenv(
            "ATLAS_HFC_API_TIMEOUT",
            "60",
        )
    )

