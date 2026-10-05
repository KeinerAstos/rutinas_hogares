from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException, Response

from app.config.smcc_analytics_settings import (
    get_smcc_analytics_settings,
)


def obtener_dashboard(
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Cliente HTTP de api_smcc_analytics (8028).

    Mantiene temporalmente el contrato HTTP heredado del router
    para garantizar paridad funcional durante la centralización.
    """

    settings = get_smcc_analytics_settings()

    if not settings.base_url:
        raise HTTPException(
            status_code=503,
            detail="SMCC_ANALYTICS_BASE_URL no configurado.",
        )

    query: dict[str, str] = {}

    if date_from:
        query["date_from"] = date_from

    if date_to:
        query["date_to"] = date_to

    target = (
        f"{settings.base_url}"
        f"/api/v1/smcc/analytics/dashboard"
    )

    if query:
        target = f"{target}?{urlencode(query)}"

    request = Request(
        target,
        method="GET",
        headers={
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(
            request,
            timeout=settings.timeout_seconds,
        ) as upstream:
            body = upstream.read()
            status = int(upstream.status)
            content_type = (
                upstream.headers.get(
                    "Content-Type",
                    "application/json",
                )
            )

    except HTTPError as exc:
        body = exc.read()

        return Response(
            content=body,
            status_code=int(exc.code),
            media_type="application/json",
        )

    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "No fue posible consultar "
                "api_smcc_analytics."
            ),
        ) from exc

    try:
        return json.loads(
            body.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return Response(
            content=body,
            status_code=status,
            media_type=content_type,
        )