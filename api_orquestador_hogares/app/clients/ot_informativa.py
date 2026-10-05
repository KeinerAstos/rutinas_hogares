from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.endpoints_settings import get_ot_relacionada_api_url

_PATHS = {
    "FIBRA": "/api/ot/informacion/fibra",
    "COAXIAL": "/api/ot/informacion/coaxial",
}


def consultar_informacion_ot(tipo: str) -> dict:
    key = str(tipo or "").strip().upper()
    if key not in _PATHS:
        return {"ok": False, "codigo": "OT_INFORMATIVA_TIPO_INVALIDO"}

    url = get_ot_relacionada_api_url() + _PATHS[key]
    try:
        with urlopen(
            Request(url, headers={"Accept": "application/json"}),
            timeout=8,
        ) as reply:
            payload = json.load(reply)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return {
            "ok": False,
            "codigo": "OT_INFORMATIVA_API_NO_DISPONIBLE",
            "error": f"{type(exc).__name__}: {exc}",
        }
    if (
        not isinstance(payload, dict)
        or not payload.get("ok")
        or payload.get("tipo") != key
        or not isinstance(payload.get("imagen_url"), str)
        or not payload.get("imagen_url", "").startswith("/api/ot/informacion/")
    ):
        return {
            "ok": False,
            "codigo": "OT_INFORMATIVA_RESPUESTA_INVALIDA",
        }
    return payload
