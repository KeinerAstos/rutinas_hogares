import os
from pathlib import Path


def _env_path(name: str, default: Path | str) -> Path:
    value = str(os.getenv(name, str(default))).strip()
    return Path(value).expanduser().resolve()


PROJECT_ROOT = Path(__file__).resolve().parents[2]

RUNTIME_ROOT = _env_path(
    "ATLAS_HOGARES_HOME",
    PROJECT_ROOT,
)

LOG_DIR = RUNTIME_ROOT / "logs"

INVENTORY_TOKEN_FILE = _env_path(
    "ATLAS_INVENTORY_TOKEN_FILE",
    Path(r"C:\xampp\atlas_inventario_red_data\api_token.txt"),
)