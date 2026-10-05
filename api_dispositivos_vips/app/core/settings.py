import os
from dataclasses import dataclass
from app.config.hfc_endpoints_settings import get_hfc_analytics_settings

from dotenv import load_dotenv

from app.core.paths import PROJECT_ROOT


load_dotenv(PROJECT_ROOT / ".env")


def _as_int(name: str, default: int, minimum: int = 1, maximum: int = 128) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


_HFC_ANALYTICS = get_hfc_analytics_settings()


@dataclass(frozen=True)
class Settings:
    app_name: str = "ATLAS Hogares API"
    app_version: str = "1.0.0"
    environment: str = os.getenv("ATLAS_HOGARES_ENV", "development")
    max_parallel_jobs: int = _as_int("ATLAS_MAX_PARALLEL_JOBS", 4, maximum=16)


    # Mesa de Ayuda - tracking de conversaciones MySQL
    chatbot_db_host: str = os.getenv(
        "ATLAS_CHATBOT_DB_HOST",
        "localhost",
    )
    chatbot_db_port: int = _as_int(
        "ATLAS_CHATBOT_DB_PORT",
        3306,
        maximum=65535,
    )
    chatbot_db_name: str = os.getenv(
        "ATLAS_CHATBOT_DB_NAME",
        "front_office_ntt",
    )
    chatbot_db_user: str = os.getenv(
        "ATLAS_CHATBOT_DB_USER",
        "root",
    )
    chatbot_db_password: str = os.getenv(
        "ATLAS_CHATBOT_DB_PASSWORD",
        "",
    )
    chatbot_db_table: str = os.getenv(
        "ATLAS_CHATBOT_DB_TABLE",
        "mesa_ayuda_consultas_chatbot_test",
    )

    # HFC Analytics - puente Linux
    hfc_analytics_ssh_host: str = _HFC_ANALYTICS.host
    hfc_analytics_ssh_port: int = _HFC_ANALYTICS.port
    hfc_analytics_ssh_user: str = _HFC_ANALYTICS.user
    hfc_analytics_remote_db: str = _HFC_ANALYTICS.remote_db
    hfc_analytics_connect_timeout: int = _HFC_ANALYTICS.connect_timeout
    hfc_analytics_command_timeout: int = _HFC_ANALYTICS.command_timeout


settings = Settings()

