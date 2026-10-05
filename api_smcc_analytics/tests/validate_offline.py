from __future__ import annotations

from app.services.analytics_service import SmccAnalyticsService


def main() -> None:
    service = SmccAnalyticsService()

    health = service.health()

    print(f"SERVICE={health.get('service')}")
    print(f"VERSION={health.get('version')}")
    print(
        f"TIMEOUT_MINUTES="
        f"{health.get('session_timeout_minutes')}"
    )
    print(
        f"EVENTS_DIR_EXISTS="
        f"{health.get('events_dir_exists')}"
    )

    if health.get("ok") is not True:
        raise RuntimeError("Health offline no devolvio ok=True.")

    result = service.dashboard()

    print(f"DASHBOARD_OK={result.get('ok')}")
    print(
        f"EVENTS="
        f"{result['source']['events']}"
    )
    print(
        f"UNIQUE_CONVERSATIONS="
        f"{result['source']['unique_conversations']}"
    )
    print(
        f"DERIVED_SESSIONS="
        f"{result['source']['derived_sessions']}"
    )
    print(
        f"COMPLETED="
        f"{result['totals']['completed']}"
    )
    print(
        f"ERRORS="
        f"{result['totals']['errors']}"
    )
    print(
        f"IN_PROGRESS="
        f"{result['totals']['in_progress']}"
    )
    print(
        f"SUCCESS_RATE="
        f"{result['totals']['success_rate']}"
    )
    print(
        f"AVG_SECONDS="
        f"{result['duration']['avg_seconds']}"
    )
    print(
        f"MEDIAN_SECONDS="
        f"{result['duration']['median_seconds']}"
    )
    print(
        f"P90_SECONDS="
        f"{result['duration']['p90_seconds']}"
    )

    if result.get("ok") is not True:
        raise RuntimeError(
            "Dashboard offline no devolvio ok=True."
        )


if __name__ == "__main__":
    main()