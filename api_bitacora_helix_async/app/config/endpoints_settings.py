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