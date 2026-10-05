import os
from pathlib import Path


def _env_path(name: str, default: Path | str) -> Path:
    value = str(os.getenv(name, str(default))).strip()
    return Path(value).expanduser().resolve()


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = _env_path("ATLAS_HOGARES_HOME", PROJECT_ROOT)

INPUT_DIR = RUNTIME_ROOT / "input"
DATA_DIR = RUNTIME_ROOT / "data"
LOG_DIR = RUNTIME_ROOT / "logs"
SCRIPTS_DIR = RUNTIME_ROOT / "app" / "tools"
STORAGE_DIR = RUNTIME_ROOT / "storage"

# Runtime temporal del backend.
TEMP_DIR = STORAGE_DIR / "temp"
TEMP_SCREENSHOTS_DIR = TEMP_DIR / "screenshots"

PATHTRAK_TEMP_SCREENSHOT_DIR = TEMP_SCREENSHOTS_DIR / "pathtrak"
HELIX_TEMP_SCREENSHOT_DIR = TEMP_SCREENSHOTS_DIR / "helix"
HELIX_RN_TEMP_SCREENSHOT_DIR = TEMP_SCREENSHOTS_DIR / "helix_redes_neutras"


DISPOSITIVOS_INPUT = INPUT_DIR / "dispositivos" / "vips.json"
DISPOSITIVOS_DATA_DIR = DATA_DIR / "dispositivos"

CENTRALNOC_ROOT = _env_path(
    "ATLAS_CENTRALNOC_ROOT",
    Path(r"C:\xampp\htdocs\CentralNOC"),
)

DASHBOARD_HOGAR_ROOT = _env_path(
    "ATLAS_DASHBOARD_HOGAR_ROOT",
    CENTRALNOC_ROOT / "modules" / "Dashboard_Hogar",
)

DASHBOARD_HOGAR_DATA_DIR = DASHBOARD_HOGAR_ROOT / "data"

PHP_EXECUTABLE = _env_path(
    "ATLAS_PHP_EXECUTABLE",
    Path(r"C:\xampp\php\php.exe"),
)


OPERATION_BRIDGE = _env_path(
    "ATLAS_OPERATION_BRIDGE",
    SCRIPTS_DIR / "operation_bridge.php",
)

INVENTORY_TOKEN_FILE = _env_path(
    "ATLAS_INVENTORY_TOKEN_FILE",
    Path(r"C:\xampp\atlas_inventario_red_data\api_token.txt"),
)


# HFC Analytics - claves SSH hacia el puente Linux.
# Se mantienen aquí porque son rutas locales del servidor Windows.
HFC_ANALYTICS_SSH_KEY_PRIMARY = _env_path(
    "ATLAS_HFC_ANALYTICS_SSH_KEY",
    Path(r"C:\Users\NTTSERVER\.ssh\noc_cable_atlas_ed25519"),
)

HFC_ANALYTICS_SSH_KEY_FALLBACK = _env_path(
    "ATLAS_HFC_ANALYTICS_SSH_KEY_FALLBACK",
    Path(r"C:\Users\NTTSERVER.ssh\noc_cable_atlas_ed25519"),
)

HFC_ANALYTICS_SSH_KEYS = (
    HFC_ANALYTICS_SSH_KEY_PRIMARY,
    HFC_ANALYTICS_SSH_KEY_FALLBACK,
)

def ensure_runtime_dirs() -> None:
    for path in (
        INPUT_DIR,
        DATA_DIR,
        LOG_DIR,
        SCRIPTS_DIR,
        STORAGE_DIR,
        TEMP_DIR,
        TEMP_SCREENSHOTS_DIR,
        PATHTRAK_TEMP_SCREENSHOT_DIR,
        HELIX_TEMP_SCREENSHOT_DIR,
        HELIX_RN_TEMP_SCREENSHOT_DIR,
        DISPOSITIVOS_INPUT.parent,
        DISPOSITIVOS_DATA_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)




