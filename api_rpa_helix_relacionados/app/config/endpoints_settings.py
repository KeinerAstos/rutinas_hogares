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
