from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

BACKEND = Path(__file__).resolve().parents[3]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env", override=False)
except Exception:
    pass

from app.integrations.acs_tr069.acs_tr069_client import (
    DEFAULT_ACS_URL,
    clean_text,
    env_bool,
    login_acs,
    search_device,
    recover_acs_search_page,
    save_evidence,
)
from urllib.parse import urljoin

def _safe_attempt(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "search_by": clean_text(item.get("search_by")),
        "no_data_found": bool(item.get("no_data_found")),
        "no_data_source": clean_text(item.get("no_data_source")),
        "wait_elapsed_sec": item.get("wait_elapsed_sec"),
        "fresh_ajax_response": bool(item.get("fresh_ajax_response")),
        "hfDeviceWasFound": clean_text(item.get("hfDeviceWasFound")),

        # FTTH_ACS_MAC_DIAGNOSTICADOR_V1
        "device_found": bool(item.get("device_found")),
        "device_serial": clean_text(item.get("device_serial")),
        "device_mac": clean_text(item.get("device_mac")),
    }

def _read_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise RuntimeError("STDIN_JSON_REQUERIDO")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("STDIN_JSON_DEBE_SER_OBJETO")
    return data

def _normalize_serials(raw_values: Any) -> list[str]:
    if not isinstance(raw_values, list):
        raise RuntimeError("SERIALS_DEBE_SER_LISTA")
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = clean_text(raw).upper()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

# FTTH_ACS_BATCH_RECOVERY_V1
def resolve_batch(serials: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    username = clean_text(os.getenv("ACS_TR069_USER") or os.getenv("ACS_USER"))
    password = clean_text(os.getenv("ACS_TR069_PASSWORD") or os.getenv("ACS_PASSWORD"))
    url = clean_text(os.getenv("ACS_TR069_URL")) or DEFAULT_ACS_URL
    # Paridad exacta con la ruta individual de Maximo, que siempre pasa --headless.
    headless = True
    slow_mo_ms = int(os.getenv("ACS_TR069_SLOW_MO_MS", "120"))
    timeout_ms = int(os.getenv("ACS_TR069_TIMEOUT_MS", "180000"))

    if not username or not password:
        return {
            "ok": False,
            "estado": "ACS_CREDENCIALES_INVALIDAS",
            "cuenta": "",
            "selected_index": 0,
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    if not serials:
        return {
            "ok": False,
            "estado": "ACS_BATCH_SIN_CANDIDATOS",
            "cuenta": "",
            "selected_index": 0,
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "duracion_seg": round(time.perf_counter() - started, 2),
        }

    resultados: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        try:
            login_acs(page, url, username, password, timeout_ms)

            # ACS_BATCH_EVIDENCE_V1
            try:
                save_evidence(
                    page,
                    "acs_batch_post_login",
                )
            except Exception:
                pass

        except Exception as exc:
            try:
                browser.close()
            except Exception:
                pass
            return {
                "ok": False,
                "estado": "ACS_LOGIN_ERROR",
                "cuenta": "",
                "selected_index": 0,
                "resultados": [],
                "browser_launches": 1,
                "login_count": 1,
                "session_reused": True,
                "error_tipo": type(exc).__name__,
                "duracion_seg": round(time.perf_counter() - started, 2),
            }

        try:
            for index, value in enumerate(serials, start=1):
                candidate_started = time.perf_counter()
                try:
                    # ACS_DEVICE_FOUND_REAL_MAC_V1
                    # ACS_TRACE_SERIAL_MAC_V1
                    print(
                        f"[ACS-TRACE] SERIAL_SEARCH_START value={value}",
                        file=sys.stderr,
                        flush=True,
                    )

                    serial_attempt = search_device(
                        page,
                        value,
                        "serial",
                    )

                    print(
                        "[ACS-TRACE] SERIAL_SEARCH_RESULT "
                        f"account={clean_text(serial_attempt.get('account'))!r} "
                        f"device_found={serial_attempt.get('device_found')} "
                        f"device_serial={serial_attempt.get('device_serial')!r} "
                        f"device_mac={serial_attempt.get('device_mac')!r} "
                        f"no_data={serial_attempt.get('no_data_found')} "
                        f"no_data_source={serial_attempt.get('no_data_source')!r} "
                        f"hfDeviceWasFound={serial_attempt.get('hfDeviceWasFound')!r} "
                        f"fresh_ajax={serial_attempt.get('fresh_ajax_response')!r} "
                        f"fresh_ajax_source={serial_attempt.get('fresh_ajax_source')!r} "
                        f"confirmations={serial_attempt.get('no_data_confirmations')!r} "
                        f"wait={serial_attempt.get('wait_elapsed_sec')!r} "
                        f"context={serial_attempt.get('device_context')!r}",
                        file=sys.stderr,
                        flush=True,
                    )

                    attempts = [
                        serial_attempt
                    ]

                    account = clean_text(
                        serial_attempt.get("account")
                    )

                    # Si el serial encontr? el equipo pero no hay mycust04,
                    # usar la MAC REAL visible en Device information.
                    if (
                        not account
                        and serial_attempt.get("device_found")
                    ):
                        real_mac = clean_text(
                            serial_attempt.get("device_mac")
                        )

                        if real_mac:

                            # ACS_FORCE_SEARCH_FRAME_V1
                            print(
                                f"[ACS-TRACE] REAL_MAC={real_mac}",
                                file=sys.stderr,
                                flush=True,
                            )

                            print(
                                "[ACS-TRACE] RECOVERY_SEARCH_START",
                                file=sys.stderr,
                                flush=True,
                            )

                            recovery = _force_acs_search_frame(
                                page,
                                url,
                                timeout_ms,
                            )

                            print(
                                f"[ACS-TRACE] RECOVERY_SEARCH_RESULT={recovery}",
                                file=sys.stderr,
                                flush=True,
                            )

                            if not recovery.get("ok"):
                                raise RuntimeError(
                                    "No fue posible regresar ACS a "
                                    "CPEs/Search.aspx antes de consultar "
                                    f"la MAC real {real_mac}. "
                                    f"Detalle={recovery}"
                                )

                            print(
                                f"[ACS-TRACE] MAC_SEARCH_START value={real_mac}",
                                file=sys.stderr,
                                flush=True,
                            )

                            mac_attempt = search_device(
                                page,
                                real_mac,
                                "mac",
                            )

                            print(
                                "[ACS-TRACE] MAC_SEARCH_RESULT "
                                f"account={clean_text(mac_attempt.get('account'))!r} "
                                f"device_found={mac_attempt.get('device_found')} "
                                f"device_serial={mac_attempt.get('device_serial')!r} "
                                f"device_mac={mac_attempt.get('device_mac')!r} "
                                f"no_data={mac_attempt.get('no_data_found')}",
                                file=sys.stderr,
                                flush=True,
                            )

                            attempts.append(
                                mac_attempt
                            )

                            account = clean_text(
                                mac_attempt.get("account")
                            )

                    # Compatibilidad hist?rica:
                    # solo usar el MISMO identificador como MAC
                    # cuando Serial realmente devuelve No data found.
                    elif (
                        not account
                        and serial_attempt.get("no_data_found")
                    ):
                        mac_attempt = search_device(
                            page,
                            value,
                            "mac",
                        )

                        attempts.append(
                            mac_attempt
                        )

                        account = clean_text(
                            mac_attempt.get("account")
                        )

                    safe_attempts = [_safe_attempt(attempt) for attempt in attempts]

                    if account:
                        resultados.append({
                            "index": index,
                            "ok": True,
                            "estado": "ACS_CUENTA_OK",
                            "cuenta": account,
                            "search_attempts": safe_attempts,
                            "duracion_seg": round(time.perf_counter() - candidate_started, 2),
                        })
                        return {
                            "ok": True,
                            "estado": "ACS_BATCH_CUENTA_OK",
                            "cuenta": account,
                            "selected_index": index,
                            "resultados": resultados,
                            "browser_launches": 1,
                            "login_count": 1,
                            "session_reused": True,
                            "duracion_seg": round(time.perf_counter() - started, 2),
                        }

                    resultados.append({
                        "index": index,
                        "ok": False,
                        "estado": "ACS_SIN_RESULTADOS",
                        "cuenta": "",
                        "search_attempts": safe_attempts,
                        "duracion_seg": round(time.perf_counter() - candidate_started, 2),
                    })

                    # ACS_BATCH_NEXT_CANDIDATE_RECOVERY_V1
                    #
                    # search_device() puede terminar correctamente en DeviceInfo.aspx
                    # aunque no exista mycust04. Si hay otro serial pendiente,
                    # debemos regresar al formulario Search antes del siguiente ciclo.
                    if index < len(serials):
                        print(
                            "[ACS-TRACE] NEXT_CANDIDATE_RECOVERY_START "
                            f"from_index={index} next_index={index + 1}",
                            file=sys.stderr,
                            flush=True,
                        )

                        recovery = _force_acs_search_frame(
                            page,
                            url,
                            timeout_ms,
                        )

                        print(
                            f"[ACS-TRACE] NEXT_CANDIDATE_RECOVERY_RESULT={recovery}",
                            file=sys.stderr,
                            flush=True,
                        )

                        if not recovery.get("ok"):
                            recovery = recover_acs_search_page(
                                page,
                                url,
                                username,
                                password,
                                timeout_ms,
                            )

                            print(
                                "[ACS-TRACE] NEXT_CANDIDATE_RECOVERY_FALLBACK="
                                f"{recovery}",
                                file=sys.stderr,
                                flush=True,
                            )

                        if not recovery.get("ok"):
                            return {
                                "ok": False,
                                "estado": "ACS_BATCH_RECOVERY_ERROR",
                                "cuenta": "",
                                "selected_index": 0,
                                "resultados": resultados,
                                "browser_launches": 1,
                                "login_count": (
                                    2
                                    if recovery.get("login_performed")
                                    else 1
                                ),
                                "session_reused": True,
                                "error_tipo": "ACS_SEARCH_RECOVERY_ERROR",
                                "error": str(recovery.get("error") or ""),
                                "duracion_seg": round(
                                    time.perf_counter() - started,
                                    2,
                                ),
                            }

                except Exception as exc:

                    # ACS_BATCH_EVIDENCE_V1
                    try:
                        save_evidence(
                            page,
                            f"acs_batch_candidate_error_{value}",
                        )
                    except Exception:
                        pass

                    # FTTH_ACS_BATCH_CANDIDATE_CONTINUE_V1
                    # Un fallo de un identificador no debe abortar todo
                    # el lote si navegador y pagina siguen utilizables.
                    resultados.append({
                        "index": index,
                        "ok": False,
                        "estado": "ACS_CANDIDATE_ERROR",
                        "cuenta": "",
                        "search_attempts": [],
                        "error_tipo": type(exc).__name__,
                        "error": str(exc),
                        "duracion_seg": round(
                            time.perf_counter() - candidate_started,
                            2,
                        ),
                    })

                    browser_dead = False

                    try:
                        browser_dead = (
                            page.is_closed()
                            or not browser.is_connected()
                        )
                    except Exception:
                        browser_dead = True

                    if browser_dead:
                        return {
                            "ok": False,
                            "estado": "ACS_BATCH_ERROR",
                            "cuenta": "",
                            "selected_index": 0,
                            "resultados": resultados,
                            "browser_launches": 1,
                            "login_count": 1,
                            "session_reused": True,
                            "error_tipo": type(exc).__name__,
                            "error": str(exc),
                            "duracion_seg": round(
                                time.perf_counter() - started,
                                2,
                            ),
                        }

                    recovery = recover_acs_search_page(
                        page,
                        url,
                        username,
                        password,
                        timeout_ms,
                    )

                    resultados[-1]["recovery"] = recovery

                    if not recovery.get("ok"):
                        return {
                            "ok": False,
                            "estado": "ACS_BATCH_RECOVERY_ERROR",
                            "cuenta": "",
                            "selected_index": 0,
                            "resultados": resultados,
                            "browser_launches": 1,
                            "login_count": (
                                2
                                if recovery.get("login_performed")
                                else 1
                            ),
                            "session_reused": True,
                            "error_tipo": "ACS_SEARCH_RECOVERY_ERROR",
                            "error": str(recovery.get("error") or ""),
                            "duracion_seg": round(
                                time.perf_counter() - started,
                                2,
                            ),
                        }

                    continue
            return {
                "ok": False,
                "estado": "ACS_BATCH_SIN_RESULTADOS",
                "cuenta": "",
                "selected_index": 0,
                "resultados": resultados,
                "browser_launches": 1,
                "login_count": 1,
                "session_reused": True,
                "duracion_seg": round(time.perf_counter() - started, 2),
            }
        finally:
            browser.close()

def login_test() -> dict[str, Any]:
    """
    Prueba aislada del único login ACS.
    No consulta seriales y no ejecuta IPPing.
    Replica el modo individual: headless + viewport 1440x900.
    """
    started = time.perf_counter()

    username = clean_text(
        os.getenv("ACS_TR069_USER")
        or os.getenv("ACS_USER")
    )
    password = clean_text(
        os.getenv("ACS_TR069_PASSWORD")
        or os.getenv("ACS_PASSWORD")
    )
    url = clean_text(
        os.getenv("ACS_TR069_URL")
    ) or DEFAULT_ACS_URL

    slow_mo_ms = int(
        os.getenv("ACS_TR069_SLOW_MO_MS", "120")
    )
    timeout_ms = int(
        os.getenv("ACS_TR069_TIMEOUT_MS", "180000")
    )

    if not username or not password:
        return {
            "ok": False,
            "estado": "ACS_CREDENCIALES_INVALIDAS",
            "browser_launches": 0,
            "login_count": 0,
            "headless": True,
            "duracion_seg": round(
                time.perf_counter() - started,
                2,
            ),
        }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            slow_mo=slow_mo_ms,
        )
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        try:
            login_acs(
                page,
                url,
                username,
                password,
                timeout_ms,
            )
            return {
                "ok": True,
                "estado": "LOGIN_TEST_OK",
                "browser_launches": 1,
                "login_count": 1,
                "headless": True,
                "viewport": "1440x900",
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }
        except Exception as exc:
            return {
                "ok": False,
                "estado": "LOGIN_TEST_ERROR",
                "browser_launches": 1,
                "login_count": 1,
                "headless": True,
                "error_tipo": type(exc).__name__,
                "duracion_seg": round(
                    time.perf_counter() - started,
                    2,
                ),
            }
        finally:
            browser.close()

def self_test(serials: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "estado": "SELF_TEST_OK",
        "candidate_count": len(serials),
        "acs_user_present": bool(os.getenv("ACS_TR069_USER") or os.getenv("ACS_USER")),
        "acs_password_present": bool(os.getenv("ACS_TR069_PASSWORD") or os.getenv("ACS_PASSWORD")),
        "browser_launches": 0,
        "login_count": 0,
        "headless_forced": True,
        "viewport": "1440x900",
    }


# ACS_FORCE_SEARCH_FRAME_V1_START
def _force_acs_search_frame(
    page,
    base_url: str,
    timeout_ms: int,
) -> dict:

    search_url = urljoin(
        base_url,
        "CPEs/Search.aspx",
    )

    errors = []

    # ------------------------------------------------------------------
    # 1. Navegar directamente el frame CPE activo
    # ------------------------------------------------------------------

    for frame in page.frames:

        frame_url = str(
            frame.url or ""
        )

        upper_url = frame_url.upper()

        if (
            "/CSR/CPES/" not in upper_url
            and "/CPES/" not in upper_url
        ):
            continue

        try:
            frame.goto(
                search_url,
                wait_until="domcontentloaded",
                timeout=min(
                    int(timeout_ms),
                    30000,
                ),
            )

            frame.wait_for_selector(
                "#ddlSearchOption",
                state="visible",
                timeout=15000,
            )

            return {
                "ok": True,
                "metodo": "FRAME_GOTO",
                "frame_url": str(
                    frame.url or ""
                ),
                "search_url": search_url,
            }

        except Exception as exc:
            errors.append(
                "FRAME_GOTO:"
                + type(exc).__name__
                + ":"
                + str(exc)
            )

    # ------------------------------------------------------------------
    # 2. Fallback con funcion nativa de Friendly
    # ------------------------------------------------------------------

    try:

        executed = page.evaluate(
            """() => {
                if (
                    typeof window.SetDescktopFrm === 'function'
                ) {
                    window.SetDescktopFrm(
                        'CPEs/Search.aspx',
                        ''
                    );
                    return true;
                }

                return false;
            }"""
        )

        if executed:

            page.wait_for_timeout(
                1200
            )

            for frame in page.frames:

                try:
                    locator = frame.locator(
                        "#ddlSearchOption"
                    )

                    if (
                        locator.count()
                        and locator.first.is_visible(
                            timeout=1000
                        )
                    ):
                        return {
                            "ok": True,
                            "metodo": "SetDescktopFrm",
                            "frame_url": str(
                                frame.url or ""
                            ),
                            "search_url": search_url,
                        }

                except Exception:
                    continue

    except Exception as exc:
        errors.append(
            "SETDESKTOP:"
            + type(exc).__name__
            + ":"
            + str(exc)
        )

    return {
        "ok": False,
        "metodo": "",
        "search_url": search_url,
        "frames": [
            str(
                frame.url or ""
            )
            for frame in page.frames
        ],
        "errores": errors[-5:],
    }
# ACS_FORCE_SEARCH_FRAME_V1_END


def main() -> int:
    parser = argparse.ArgumentParser(description="ATLAS ACS account batch runner")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--login-test", action="store_true")
    args = parser.parse_args()
    try:
        request = _read_request()
        serials = _normalize_serials(request.get("serials"))
        if args.login_test:
            result = login_test()
        elif args.self_test:
            result = self_test(serials)
        else:
            result = resolve_batch(serials)
    except Exception as exc:
        result = {
            "ok": False,
            "estado": "ACS_BATCH_INPUT_ERROR",
            "cuenta": "",
            "selected_index": 0,
            "resultados": [],
            "browser_launches": 0,
            "login_count": 0,
            "session_reused": False,
            "error_tipo": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
