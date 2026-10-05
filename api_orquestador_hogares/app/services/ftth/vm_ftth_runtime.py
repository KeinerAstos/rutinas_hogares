from __future__ import annotations

from app.services.ftth import vm_ftth_direct_service as legacy
from app.services.ftth.vm_ftth_hac_global_final import (
    run_huawei_global,
)


def _truthy(value):
    if value is True:
        return True
    return str(value or "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _vendor(payload, olt):
    name = str(
        olt.get("olt")
        or payload.get("olt")
        or ""
    ).strip().upper()

    # Regla autoritativa.
    if name.startswith("HAC-"):
        return "HUAWEI"
    if name.startswith("ZAC-"):
        return "ZTE"

    if "MA5800" in name:
        return "HUAWEI"

    if (
        "C600" in name
        or "C650" in name
        or "ZTC" in name
    ):
        return "ZTE"

    value = str(
        olt.get("vendor")
        or payload.get("vendor")
        or ""
    ).strip().upper()

    return (
        value
        if value in ("HUAWEI", "ZTE")
        else "AUTO"
    )


def diagnosticar_vm_ftth(payload):
    olt = legacy._resolve_olt(payload)
    vendor = _vendor(payload, olt)

    if vendor not in ("HUAWEI", "ZTE"):
        raise legacy.VmFtthError(
            "FABRICANTE_NO_IDENTIFICADO"
        )

    mode = str(
        payload.get("mode")
        or "specific"
    ).strip().lower()

    fast = _truthy(
        payload.get("fast", True)
    )

    normalized = dict(payload)
    normalized["vendor"] = vendor

    # El frontend actual manda fast=true tambien en specific.
    # Se fuerza false para que V11.8/V11.9 entren siempre.
    if mode == "specific":
        normalized["fast"] = False
        return legacy.diagnosticar_vm_ftth(
            normalized
        )

    olt_name = str(
        olt.get("olt")
        or ""
    ).upper()

    # Caso Huawei FLSF ya validado: conservar V11.7.
    if (
        vendor == "HUAWEI"
        and "CUMBRE-M5-MA5800" in olt_name
    ):
        normalized["fast"] = fast
        return legacy.diagnosticar_vm_ftth(
            normalized
        )

    # HAC global: motor unico por slot.
    if (
        vendor == "HUAWEI"
        and fast
        and mode in ("all", "active")
    ):
        return run_huawei_global(
            normalized,
            legacy,
        )

    # ZAC y casos restantes: mantener comportamiento validado.
    normalized["fast"] = fast
    return legacy.diagnosticar_vm_ftth(
        normalized
    )
