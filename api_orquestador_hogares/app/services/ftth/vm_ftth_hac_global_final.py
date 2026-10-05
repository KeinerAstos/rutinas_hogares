from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

RX_ATT_DBM = -27.0
OPER_PCT = 85.0
MAX_AGE_DAYS = 30
MAX_WORKERS = 4
CACHE_TTL = 300.0
WORKER_STAGGER_S = 0.65
SUMMARY_IDLE_TIMEOUT = 1.5
SUMMARY_IDLE_FALLBACK = 2.0

DESC_CACHE_TTL = 86400.0
DESC_CACHE_PATH = os.environ.get(
    "VM_FTTH_HAC_DESC_CACHE",
    r"C:\xampp\htdocs\rutinas_hogares\api_orquestador_hogares\data\vm_ftth\hac_desc_cache.json",
)
_DESC_CACHE_LOCK = threading.RLock()

BOARD_CACHE_TTL = 21600.0
BOARD_CACHE_PATH = os.environ.get(
    "VM_FTTH_HAC_BOARD_CACHE",
    r"C:\xampp\htdocs\rutinas_hogares\api_orquestador_hogares\data\vm_ftth\hac_board_cache.json",
)
_BOARD_CACHE_LOCK = threading.RLock()

_PROMPT_RE = re.compile(
    r"^[A-Za-z0-9_.:\-]+(?:\([^)]+\))?[>#]\s*$"
)

_HEADER_RE = re.compile(
    r"In port\s+(\d+/\d+/\d+),\s+"
    r"the total of ONTs are:\s*(\d+),\s+online:\s*(\d+)",
    re.I,
)

_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _last_nonempty(text):
    for line in reversed(str(text or "").splitlines()):
        value = line.strip()
        if value:
            return value
    return ""


def _execute_complete(session, command, timeout, idle_timeout):
    raw = session.execute(
        command,
        timeout=timeout,
        idle_timeout=idle_timeout,
    )

    text = str(raw or "")
    last = _last_nonempty(text)

    if not _PROMPT_RE.match(last):
        raise RuntimeError(
            "NO_FINAL_PROMPT:"
            + command
            + ":"
            + last[:180]
        )

    return text


def _prepare(session):
    # config is not required for these display commands.
    for command in (
        "enable",
        "scroll",
        "undo smart",
    ):
        try:
            session.execute(
                command,
                timeout=8,
                idle_timeout=0.7,
            )
        except Exception:
            pass


def _split_blocks(text):
    lines = str(text or "").splitlines()
    headers = []

    for index, line in enumerate(lines):
        match = _HEADER_RE.search(line)
        if match:
            headers.append(
                (
                    index,
                    match.group(1),
                    int(match.group(2)),
                    int(match.group(3)),
                )
            )

    blocks = {}

    for pos, item in enumerate(headers):
        start, port, total, online = item

        end = (
            headers[pos + 1][0]
            if pos + 1 < len(headers)
            else len(lines)
        )

        blocks[port] = {
            "header_total": total,
            "header_online": online,
            "text": "\n".join(lines[start:end]),
        }

    return blocks



def _load_desc_cache():
    path = DESC_CACHE_PATH

    with _DESC_CACHE_LOCK:
        try:
            if not os.path.exists(path):
                return {}

            with open(
                path,
                "r",
                encoding="utf-8",
            ) as handle:
                data = json.load(handle)

            if not isinstance(data, dict):
                return {}

            return data

        except Exception:
            return {}


def _save_desc_cache(data):
    path = DESC_CACHE_PATH
    folder = os.path.dirname(path)

    with _DESC_CACHE_LOCK:
        try:
            os.makedirs(
                folder,
                exist_ok=True,
            )

            temp = path + ".tmp"

            with open(
                temp,
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    data,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )

            os.replace(
                temp,
                path,
            )

        except Exception:
            pass


def _desc_cache_get(ip, slot):
    cache = _load_desc_cache()
    key = f"{ip}|{slot}"
    item = cache.get(key)

    if not isinstance(item, dict):
        return None

    ts = float(
        item.get("ts")
        or 0.0
    )

    age = time.time() - ts

    if age < 0 or age > DESC_CACHE_TTL:
        return None

    raw = str(
        item.get("raw")
        or ""
    )

    if not raw.strip():
        return None

    return {
        "raw": raw,
        "age_s": round(age, 2),
    }


def _desc_cache_set(ip, slot, raw):
    if not str(raw or "").strip():
        return

    cache = _load_desc_cache()
    key = f"{ip}|{slot}"

    cache[key] = {
        "ts": time.time(),
        "raw": str(raw),
    }

    _save_desc_cache(
        cache
    )



def _load_board_cache():
    with _BOARD_CACHE_LOCK:
        try:
            if not os.path.exists(BOARD_CACHE_PATH):
                return {}

            with open(
                BOARD_CACHE_PATH,
                "r",
                encoding="utf-8",
            ) as handle:
                data = json.load(handle)

            return data if isinstance(data, dict) else {}

        except Exception:
            return {}


def _save_board_cache(data):
    with _BOARD_CACHE_LOCK:
        try:
            folder = os.path.dirname(
                BOARD_CACHE_PATH
            )

            os.makedirs(
                folder,
                exist_ok=True,
            )

            temp = BOARD_CACHE_PATH + ".tmp"

            with open(
                temp,
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    data,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )

            os.replace(
                temp,
                BOARD_CACHE_PATH,
            )

        except Exception:
            pass


def _board_cache_get(ip):
    cache = _load_board_cache()
    item = cache.get(str(ip))

    if not isinstance(item, dict):
        return None

    ts = float(
        item.get("ts")
        or 0.0
    )

    age = time.time() - ts

    if age < 0 or age > BOARD_CACHE_TTL:
        return None

    slots = item.get("slots")

    if (
        not isinstance(slots, list)
        or not slots
    ):
        return None

    parsed = []

    for item_row in slots:
        if (
            not isinstance(item_row, list)
            or len(item_row) != 2
        ):
            return None

        parsed.append(
            (
                int(item_row[0]),
                str(item_row[1]),
            )
        )

    return {
        "slots": parsed,
        "age_s": round(age, 2),
    }


def _board_cache_set(ip, slots):
    if not slots:
        return

    cache = _load_board_cache()

    cache[str(ip)] = {
        "ts": time.time(),
        "slots": [
            [int(slot), str(board)]
            for slot, board
            in slots
        ],
    }

    _save_board_cache(
        cache
    )


def _pct(num, den):
    if den <= 0:
        return 0.0
    return round((num * 100.0) / den, 2)


def _evaluate(port, description, parsed, now):
    cutoff = now - timedelta(days=MAX_AGE_DAYS)

    registered = len(parsed)

    registered_online = sum(
        1
        for row in parsed
        if row.get("state") == "online"
    )

    registered_offline = sum(
        1
        for row in parsed
        if row.get("state") == "offline"
    )

    selected = [
        row
        for row in parsed
        if (
            row.get("state") == "online"
            and row.get("rx_dbm") is not None
            and row.get("last_change") is not None
            and row["last_change"] >= cutoff
        )
    ]

    attenuated_rows = [
        row
        for row in selected
        if row["rx_dbm"] <= RX_ATT_DBM
    ]

    valid = len(selected)
    attenuated = len(attenuated_rows)
    good = max(0, valid - attenuated)

    pct_oper = _pct(good, valid)
    pct_att = _pct(attenuated, valid)

    if registered > 0 and registered_online == 0:
        status = "CAIDA"
        observation = "TRK Caida"
    elif valid <= 0:
        status = "OTRO"
        observation = "SIN DATOS VALIDOS"
    elif pct_oper > OPER_PCT:
        status = "OPERATIVA"
        observation = "TRK Operativa"
    else:
        status = "ATENUADA"
        observation = "TRK Atenuada"

    rx_values = [
        row["rx_dbm"]
        for row in selected
        if row.get("rx_dbm") is not None
    ]

    avg_power = (
        round(sum(rx_values) / len(rx_values), 3)
        if rx_values
        else None
    )

    return {
        "port": port,
        "pon": port,
        "description": description,
        "trunk": description,
        "id_port": description,
        "onus": valid,
        "working": valid,
        "online": valid,
        "registered_onus": registered,
        "registered_online": registered_online,
        "registered_offline": registered_offline,
        "offline": 0,
        "dyinggasp": 0,
        "los": 0,
        "attenuated": attenuated,
        "power_attenuated": attenuated,
        "power_samples": len(rx_values),
        "avg_power": avg_power,
        "avg_rx_dbm": avg_power,
        "pct_operativa": pct_oper,
        "pct_atenuada": pct_att,
        "status": status,
        "estado": status,
        "observation": observation,
        "trk_status": observation,
        "rx_threshold_dbm": RX_ATT_DBM,
        "max_last_change_days": MAX_AGE_DAYS,
    }


def _open_session(legacy, ip, user, password):
    session = legacy.SSHJumpSession(
        ip,
        user,
        password,
        connect_timeout=12,
    )
    session.__enter__()
    _prepare(session)
    return session


def _close_session(session):
    if session is None:
        return

    try:
        session.__exit__(None, None, None)
    except Exception:
        pass


def _process_slot(session, slot, board, legacy, now, ip, summary_idle_timeout):
    started = time.monotonic()

    summary_raw = _execute_complete(
        session,
        f"display ont info summary 0/{slot}",
        150,
        summary_idle_timeout,
    )

    desc_cached = _desc_cache_get(
        ip,
        slot,
    )

    desc_cache_hit = (
        desc_cached is not None
    )

    desc_cache_age_s = (
        desc_cached["age_s"]
        if desc_cached
        else None
    )

    if desc_cached:
        desc_raw = desc_cached["raw"]
        desc_seconds = 0.0
    else:
        desc_started = time.monotonic()

        desc_raw = _execute_complete(
            session,
            f"display port desc 0/{slot}",
            25,
            1.5,
        )

        desc_seconds = round(
            time.monotonic()
            - desc_started,
            3,
        )

        _desc_cache_set(
            ip,
            slot,
            desc_raw,
        )

    blocks = _split_blocks(summary_raw)

    if not blocks:
        raise RuntimeError(
            "SLOT_SUMMARY_NO_PORT_BLOCKS"
        )

    rows = []

    header_total = 0
    header_online = 0
    parsed_state_rows = 0
    parsed_power_rows = 0
    description_rows = 0

    for port in sorted(
        blocks,
        key=lambda value: tuple(
            int(item)
            for item in value.split("/")
        ),
    ):
        block = blocks[port]

        parsed = legacy._h119_parse_summary(
            block["text"]
        )

        state_rows = sum(
            1
            for row in parsed
            if row.get("state") in ("online", "offline")
        )

        power_rows = sum(
            1
            for row in parsed
            if row.get("rx_dbm") is not None
        )

        if state_rows != int(block["header_total"]):
            raise RuntimeError(
                "STATE_ROWS_MISMATCH:"
                + port
                + f":header={block['header_total']}"
                + f":parsed={state_rows}"
            )

        if power_rows != int(block["header_online"]):
            raise RuntimeError(
                "POWER_ROWS_MISMATCH:"
                + port
                + f":header_online={block['header_online']}"
                + f":parsed={power_rows}"
            )

        description = legacy._h119_parse_desc(
            desc_raw,
            port,
        )

        if description:
            description_rows += 1

        row = _evaluate(
            port,
            description,
            parsed,
            now,
        )

        row["slot"] = slot
        row["board"] = board
        row["header_total"] = block["header_total"]
        row["header_online"] = block["header_online"]
        row["parsed_state_rows"] = state_rows
        row["parsed_power_rows"] = power_rows

        rows.append(row)

        header_total += int(block["header_total"])
        header_online += int(block["header_online"])
        parsed_state_rows += state_rows
        parsed_power_rows += power_rows

    if description_rows < max(1, len(rows) - 1):
        raise RuntimeError(
            "DESCRIPTION_ROWS_INCOMPLETE:"
            + f"ports={len(rows)}:"
            + f"desc={description_rows}"
        )

    return {
        "slot": slot,
        "board": board,
        "seconds": round(
            time.monotonic() - started,
            2,
        ),
        "rows": rows,
        "integrity": {
            "ports": len(rows),
            "header_total_onus": header_total,
            "header_online_onus": header_online,
            "parsed_state_rows": parsed_state_rows,
            "parsed_power_rows": parsed_power_rows,
            "description_rows": description_rows,
            "desc_cache_hit": desc_cache_hit,
            "desc_cache_age_s": desc_cache_age_s,
            "desc_seconds": desc_seconds,
            "complete": (
                parsed_state_rows == header_total
                and parsed_power_rows == header_online
                and description_rows >= max(1, len(rows) - 1)
            ),
        },
    }


def _worker(
    worker_id,
    chunk,
    legacy,
    ip,
    user,
    password,
    now,
):
    if worker_id > 1:
        time.sleep(
            (worker_id - 1)
            * WORKER_STAGGER_S
        )

    rows = []
    errors = []
    meta = []

    session = None

    try:
        session = _open_session(
            legacy,
            ip,
            user,
            password,
        )

        for slot, board in chunk:
            item = None
            last_error = None

            for attempt in (1, 2):
                try:
                    if session is None:
                        session = _open_session(
                            legacy,
                            ip,
                            user,
                            password,
                        )

                    attempt_idle = (
                        SUMMARY_IDLE_TIMEOUT
                        if attempt == 1
                        else SUMMARY_IDLE_FALLBACK
                    )

                    item = _process_slot(
                        session,
                        slot,
                        board,
                        legacy,
                        now,
                        ip,
                        attempt_idle,
                    )

                    item["attempt"] = attempt
                    item["summary_idle_used"] = attempt_idle
                    break

                except Exception as exc:
                    last_error = (
                        f"{type(exc).__name__}:{exc}"
                    )

                    _close_session(session)
                    session = None

                    if attempt == 1:
                        time.sleep(1.5)

            if item is None:
                errors.append({
                    "worker": worker_id,
                    "slot": slot,
                    "board": board,
                    "error": last_error,
                })

                meta.append({
                    "worker": worker_id,
                    "slot": slot,
                    "board": board,
                    "ok": False,
                    "attempt": 2,
                    "seconds": None,
                    "rows": 0,
                    "integrity": None,
                })

                continue

            rows.extend(item["rows"])

            meta.append({
                "worker": worker_id,
                "slot": slot,
                "board": board,
                "ok": True,
                "attempt": item["attempt"],
                "seconds": item["seconds"],
                "rows": len(item["rows"]),
                "integrity": item["integrity"],
            })

    finally:
        _close_session(session)

    return {
        "worker": worker_id,
        "rows": rows,
        "errors": errors,
        "meta": meta,
        "slots_assigned": len(chunk),
    }


def _totals(rows):
    return {
        "ports": len(rows),
        "onus": sum(int(r.get("onus") or 0) for r in rows),
        "working": sum(int(r.get("working") or 0) for r in rows),
        "online": sum(int(r.get("online") or 0) for r in rows),
        "offline": 0,
        "dyinggasp": 0,
        "los": 0,
        "attenuated": sum(int(r.get("attenuated") or 0) for r in rows),
        "operativa": sum(1 for r in rows if r.get("status") == "OPERATIVA"),
        "atenuada": sum(1 for r in rows if r.get("status") == "ATENUADA"),
        "caida": sum(1 for r in rows if r.get("status") == "CAIDA"),
        "other": sum(1 for r in rows if r.get("status") == "OTRO"),
    }


def _cache_get(ip, mode):
    key = (str(ip), str(mode))
    now = time.monotonic()

    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None

        age = now - item["ts"]

        if age > CACHE_TTL:
            _CACHE.pop(key, None)
            return None

        result = copy.deepcopy(item["result"])

    result["cache_hit"] = True
    result["cache_age_s"] = round(age, 2)
    result["elapsed_s"] = 0.0
    return result


def _cache_set(ip, mode, result):
    with _CACHE_LOCK:
        _CACHE[(str(ip), str(mode))] = {
            "ts": time.monotonic(),
            "result": copy.deepcopy(result),
        }


def run_huawei_global(payload, legacy):
    olt = legacy._resolve_olt(payload)
    mode = str(payload.get("mode") or "all").strip().lower()

    cached = _cache_get(olt["ip"], mode)
    if cached is not None:
        return cached

    started = time.monotonic()
    now = datetime.now()

    user, password = legacy._credentials("HUAWEI")

    board_cached = _board_cache_get(
        olt["ip"]
    )

    board_cache_hit = (
        board_cached is not None
    )

    board_cache_age_s = (
        board_cached["age_s"]
        if board_cached
        else None
    )

    board_discovery_seconds = 0.0

    if board_cached:
        slots = board_cached["slots"]
    else:
        discovery_started = time.monotonic()

        with legacy.SSHJumpSession(
            olt["ip"],
            user,
            password,
            connect_timeout=12,
        ) as discovery_session:
            _prepare(discovery_session)

            board_raw = _execute_complete(
                discovery_session,
                "display board 0",
                25,
                1.5,
            )

        board_discovery_seconds = round(
            time.monotonic()
            - discovery_started,
            3,
        )

        slots = legacy._vm_huawei_service_slots_v43(
            board_raw
        )

        if slots:
            _board_cache_set(
                olt["ip"],
                slots,
            )

    if not slots:
        raise RuntimeError(
            "HUAWEI_GLOBAL_NO_SERVICE_SLOTS"
        )

    worker_count = min(
        MAX_WORKERS,
        len(slots),
    )

    chunks = [[] for _ in range(worker_count)]

    for index, item in enumerate(slots):
        chunks[index % worker_count].append(item)

    rows = []
    errors = []
    slot_meta = []
    worker_meta = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=worker_count
    ) as pool:
        futures = [
            pool.submit(
                _worker,
                index + 1,
                chunk,
                legacy,
                olt["ip"],
                user,
                password,
                now,
            )
            for index, chunk
            in enumerate(chunks)
            if chunk
        ]

        for future in concurrent.futures.as_completed(futures):
            item = future.result()

            rows.extend(item["rows"])
            errors.extend(item["errors"])
            slot_meta.extend(item["meta"])

            worker_meta.append({
                "worker": item["worker"],
                "slots_assigned": item["slots_assigned"],
                "rows": len(item["rows"]),
                "errors": len(item["errors"]),
            })

    rows.sort(
        key=lambda row: tuple(
            int(value)
            for value in row["port"].split("/")
        )
    )

    slot_meta.sort(
        key=lambda item: int(item.get("slot") or 0)
    )

    worker_meta.sort(
        key=lambda item: int(item["worker"])
    )

    if mode == "active":
        rows = [
            row
            for row in rows
            if int(row.get("working") or 0) > 0
        ]

    integrity_slots = [
        item
        for item in slot_meta
        if item.get("ok")
        and isinstance(item.get("integrity"), dict)
    ]

    header_total_onus = sum(
        int(item["integrity"].get("header_total_onus") or 0)
        for item in integrity_slots
    )

    header_online_onus = sum(
        int(item["integrity"].get("header_online_onus") or 0)
        for item in integrity_slots
    )

    parsed_state_rows = sum(
        int(item["integrity"].get("parsed_state_rows") or 0)
        for item in integrity_slots
    )

    parsed_power_rows = sum(
        int(item["integrity"].get("parsed_power_rows") or 0)
        for item in integrity_slots
    )

    description_rows = sum(
        int(item["integrity"].get("description_rows") or 0)
        for item in integrity_slots
    )

    desc_cache_hits = sum(
        1
        for item in integrity_slots
        if item["integrity"].get("desc_cache_hit")
    )

    desc_cache_misses = (
        len(integrity_slots)
        - desc_cache_hits
    )

    desc_command_seconds = round(
        sum(
            float(
                item["integrity"].get("desc_seconds")
                or 0.0
            )
            for item in integrity_slots
        ),
        3,
    )

    integrity_complete = (
        len(integrity_slots) == len(slots)
        and all(
            bool(item["integrity"].get("complete"))
            for item in integrity_slots
        )
        and parsed_state_rows == header_total_onus
        and parsed_power_rows == header_online_onus
    )

    complete = (
        not errors
        and len(slot_meta) == len(slots)
        and all(item.get("ok") for item in slot_meta)
        and integrity_complete
    )

    result = {
        "ok": complete,
        "fast": True,
        "complete": complete,
        "partial": not complete,
        "cache_hit": False,
        "cache_age_s": 0.0,
        "strategy":
            "huawei_global_adaptive_idle_final",
        "aggregate": True,
        "olt": olt["olt"],
        "ip": olt["ip"],
        "vendor": "HUAWEI",
        "mode": mode,
        "elapsed_s": round(
            time.monotonic() - started,
            2,
        ),
        "workers": worker_count,
        "persistent_sessions": worker_count,
        "discovered_slots": len(slots),
        "completed_slots": sum(
            1 for item in slot_meta
            if item.get("ok")
        ),
        "remaining_slots": sum(
            1 for item in slot_meta
            if not item.get("ok")
        ),
        "discovered_ports": len(rows),
        "completed_ports": len(rows),
        "integrity_complete": integrity_complete,
        "header_total_onus": header_total_onus,
        "header_online_onus": header_online_onus,
        "parsed_state_rows": parsed_state_rows,
        "parsed_power_rows": parsed_power_rows,
        "description_rows": description_rows,
        "desc_cache_hits": desc_cache_hits,
        "desc_cache_misses": desc_cache_misses,
        "desc_command_seconds": desc_command_seconds,
        "desc_cache_ttl_s": DESC_CACHE_TTL,
        "desc_cache_path": DESC_CACHE_PATH,
        "board_cache_hit": board_cache_hit,
        "board_cache_age_s": board_cache_age_s,
        "board_discovery_seconds": board_discovery_seconds,
        "board_cache_ttl_s": BOARD_CACHE_TTL,
        "board_cache_path": BOARD_CACHE_PATH,
        "totals": _totals(rows),
        "results": rows,
        "errors": errors,
        "slot_meta": slot_meta,
        "worker_meta": worker_meta,
        "parameters": {
            "vendor_rule": "HAC=HUAWEI",
            "architecture":
                "3_persistent_sessions_integrity_guard",
            "slot_summary_command":
                "display ont info summary 0/<slot>",
            "summary_idle_timeout_s":
                SUMMARY_IDLE_TIMEOUT,
            "summary_idle_fallback_s":
                SUMMARY_IDLE_FALLBACK,
            "slot_desc_command":
                "display port desc 0/<slot>",
            "client_filter":
                "online+rx+last_change<=30d",
            "rx_attenuated_dbm":
                RX_ATT_DBM,
            "trk_operativa_pct_gt":
                OPER_PCT,
            "cache_ttl_s":
                CACHE_TTL,
            "desc_cache_ttl_s":
                DESC_CACHE_TTL,
            "desc_cache_path":
                DESC_CACHE_PATH,
            "board_cache_ttl_s":
                BOARD_CACHE_TTL,
            "board_cache_path":
                BOARD_CACHE_PATH,
            "integrity_rule":
                "state_rows=header_total;power_rows=header_online",
        },
    }

    if complete:
        _cache_set(
            olt["ip"],
            mode,
            result,
        )

    return result
