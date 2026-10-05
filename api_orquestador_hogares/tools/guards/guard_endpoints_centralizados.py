from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

ROOTS = [
    BACKEND / "app",
    BACKEND / "chatbot",
    BACKEND / "incident_sources",
    BACKEND / "pathtrak",
]

EXTENSIONS = {
    ".py",
    ".php",
    ".js",
    ".ts",
    ".ps1",
}

EXCLUDED_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    "_deploy_backups",
    "backup",
    "backups",
    ".git",
    "tests",
    "test",
    "node_modules",
    "logs",
    "screenshots",
    "diagnosticos",
    "perfil_navegador",
}

EXCLUDED_PREFIXES = (
    "app/config/",
    "app/data/",
)

URL_RE = re.compile(
    r"https?://[^\s'\"<>)]+",
    re.I,
)

IP_RE = re.compile(
    r"(?<![\d.])"
    r"(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?![\d.])"
)

ENV_ENDPOINT_FALLBACK_RE = re.compile(
    r"(?:os\.getenv|os\.environ\.get)"
    r"\(\s*['\"]([A-Z0-9_]+)['\"]"
    r"\s*,\s*['\"]([^'\"]+)['\"]",
    re.I,
)

ENV_OR_FALLBACK_RE = re.compile(
    r"os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]\s*\)"
    r"\s*or\s*['\"]([^'\"]+)['\"]",
    re.I,
)

ENDPOINT_KEY_RE = re.compile(
    r"(?:_URL|_BASE_URL|_ENDPOINT|_HOST|_PORT|_SERVER|_ADDRESS)$",
    re.I,
)

DOCUMENTATION_IP_PREFIXES = (
    "192.0.2.",
    "198.51.100.",
    "203.0.113.",
)


def rel(path: Path) -> str:
    return str(
        path.relative_to(BACKEND)
    ).replace("\\", "/")


def excluded(path: Path) -> bool:
    parts = {
        p.lower()
        for p in path.parts
    }

    if parts & {
        p.lower()
        for p in EXCLUDED_DIRS
    }:
        return True

    r = rel(path).lower()

    return any(
        r.startswith(prefix)
        for prefix in EXCLUDED_PREFIXES
    )


def is_comment_only(line: str) -> bool:
    stripped = line.strip()

    return (
        stripped.startswith("#")
        or stripped.startswith("//")
        or stripped.startswith("*")
    )


def allowed_ip(line: str, ip: str) -> bool:
    low = line.lower()

    if is_comment_only(line):
        return True

    if ip.startswith(DOCUMENTATION_IP_PREFIXES):
        return True

    if ip == "127.0.0.1" and "src_addr" in low:
        return True

    if ip == "0.0.0.0":
        return True

    if "firmware" in low:
        return True

    return False


violations = []
files_scanned = 0

for root in ROOTS:
    if not root.exists():
        continue

    for path in root.rglob("*"):
        if (
            not path.is_file()
            or path.suffix.lower() not in EXTENSIONS
            or excluded(path)
        ):
            continue

        files_scanned += 1
        file_rel = rel(path)

        try:
            lines = path.read_text(
                encoding="utf-8-sig",
                errors="replace",
            ).splitlines()
        except Exception:
            continue

        for lineno, line in enumerate(
            lines,
            start=1,
        ):
            if is_comment_only(line):
                continue

            for match in URL_RE.finditer(line):
                url = match.group(0).rstrip(
                    ".,;])}"
                )

                violations.append({
                    "file": file_rel,
                    "line": lineno,
                    "kind": "URL",
                    "value": url,
                })

            for match in IP_RE.finditer(line):
                ip = match.group(0)

                if any(
                    ip in m.group(0)
                    for m in URL_RE.finditer(line)
                ):
                    continue

                if allowed_ip(line, ip):
                    continue

                violations.append({
                    "file": file_rel,
                    "line": lineno,
                    "kind": "IP",
                    "value": ip,
                })

            for regex in (
                ENV_ENDPOINT_FALLBACK_RE,
                ENV_OR_FALLBACK_RE,
            ):
                for match in regex.finditer(line):
                    key = match.group(1).upper()
                    value = match.group(2)

                    if not ENDPOINT_KEY_RE.search(key):
                        continue

                    is_endpoint_value = bool(
                        URL_RE.search(value)
                        or IP_RE.fullmatch(value)
                        or (
                            key.endswith("_PORT")
                            and value.isdigit()
                        )
                    )

                    if not is_endpoint_value:
                        continue

                    violations.append({
                        "file": file_rel,
                        "line": lineno,
                        "kind": "ENV_ENDPOINT_FALLBACK",
                        "value": f"{key}={value}",
                    })


dedup = {}

for item in violations:
    key = (
        item["file"],
        item["line"],
        item["kind"],
        item["value"],
    )
    dedup[key] = item

violations = sorted(
    dedup.values(),
    key=lambda x: (
        x["file"],
        x["line"],
        x["kind"],
        x["value"],
    ),
)

print("=" * 118)
print("ATLAS - GUARD ENDPOINTS CENTRALIZADOS")
print("=" * 118)
print(f"BACKEND={BACKEND}")
print(f"FILES_SCANNED={files_scanned}")
print(f"VIOLATIONS={len(violations)}")

if violations:
    print()
    print("ENDPOINT_HARDCODE_VIOLATIONS")

    for item in violations:
        print(
            f"{item['file']}:{item['line']} "
            f"[{item['kind']}] {item['value']}"
        )

    print()
    print("GUARD_STATUS=FAIL")
    print("CENTRALIZATION_POLICY=VIOLATED")
    sys.exit(1)

print("GUARD_STATUS=PASS")
print("CENTRALIZATION_POLICY=COMPLIANT")
print("ENDPOINTS_HARDCODEADOS_REALES=0")
sys.exit(0)