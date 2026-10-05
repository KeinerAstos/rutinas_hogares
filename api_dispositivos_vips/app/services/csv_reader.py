import csv
from pathlib import Path
from typing import Any


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []

    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel

        return [
            {str(key).strip(): value for key, value in row.items() if key is not None}
            for row in csv.DictReader(handle, dialect=dialect)
        ]
