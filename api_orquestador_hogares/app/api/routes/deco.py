
# ATLAS_HELIX_WO_SUMMARY_EXTERNAL_8023_V1
from app.services.helix.incident_related_batch_http_client import consultar_resumen_ot as consultar_resumen_ot_helix
# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.services.deco.bootstrap import (
    PATHTRAK_SCREENSHOT_DIR,
    health as deco_runtime_health,
)
from app.services.deco.execution import ejecutar_chat
from app.services.maximo.config import settings as maximo_settings
from app.services.helix.service import (
    helix_downloads_dir,
    helix_health,
)
from app.services.helix.incident_related_batch_http_client import (
    iniciar_job as iniciar_job_relacionados_helix,
    obtener_job as obtener_job_relacionados_helix,
    consultar_incidente as consultar_relacionados_incidente_externo,
)
router = APIRouter(tags=["DECO"])
class HelixRelatedBatchRequest(BaseModel):
    incidentes: list[str]
    workers: int = 7
    area: str = "front"
class DecoChatRequest(BaseModel):
    mensaje: str
    conversation_id: Optional[str] = None
@router.get("/api/deco/health")
def deco_health():
    result = deco_runtime_health()
    result["helix"] = helix_health()
    return result
@router.post("/api/deco/chat")
def deco_chat(req: DecoChatRequest):
    try:
        return ejecutar_chat(
            req.mensaje,
            conversation_id=req.conversation_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )
@router.get("/api/deco/screenshots/pathtrak/{filename}")
def deco_pathtrak_screenshot(filename: str):
    # PATHTRAK_SCREENSHOT_PROXY_8024_V1
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import urlopen
    from fastapi.responses import Response
    from app.config.endpoints_settings import (
        get_pathtrak_captures_api_timeout,
        get_pathtrak_captures_api_url,
    )
    safe_name = Path(filename).name
    if (
        not safe_name
        or safe_name != filename
        or not safe_name.lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp")
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="Nombre de captura no valido",
        )
    url = (
        get_pathtrak_captures_api_url()
        + "/api/pathtrak/screenshots/"
        + quote(safe_name)
    )
    try:
        with urlopen(
            url,
            timeout=get_pathtrak_captures_api_timeout(),
        ) as upstream:
            body = upstream.read()
            content_type = (
                upstream.headers.get_content_type()
                or "image/png"
            )
    except HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(
                status_code=404,
                detail="Captura no encontrada",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"PathTrak respondio HTTP {exc.code}",
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail="API PathTrak no disponible",
        ) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-ATLAS-PathTrak-Source": "8024",
        },
    )
@router.get("/api/deco/maximo/files/{filename}")
def deco_maximo_file(filename: str):
    safe_name = Path(filename).name
    base = maximo_settings.downloads_dir.resolve()
    path = (base / safe_name).resolve()
    if base not in path.parents:
        raise HTTPException(status_code=403, detail="Ruta no permitida")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Evidencia no encontrada")
    return FileResponse(path, filename=path.name)
# PATCH_HELIX_RESUMEN_OT_ATLAS_V1
@router.get("/api/deco/helix/ot/{ot}/resumen")
def deco_helix_ot_resumen(ot: str):
    return consultar_resumen_ot_helix(ot)
# HELIX_INC_RELATED_ITEMS_ATLAS_V1
@router.post("/api/deco/helix/incidentes/relacionados/jobs")
def deco_helix_relacionados_batch_start(req: HelixRelatedBatchRequest):
    return iniciar_job_relacionados_helix(
        req.incidentes,
        workers=req.workers,
        area=req.area,
    )
@router.get("/api/deco/helix/incidentes/relacionados/jobs/{job_id}")
def deco_helix_relacionados_batch_status(job_id: str):
    return obtener_job_relacionados_helix(job_id)
# ATLAS_HELIX_RELACIONADOS_INDIVIDUAL_EXTERNAL_8023_V1
@router.get("/api/deco/helix/incidente/{inc}/relacionados")
def deco_helix_inc_relacionados(inc: str):
    return consultar_relacionados_incidente_externo(inc)
@router.get("/api/deco/helix/files/{ot}/{incident}/{filename}")
def deco_helix_file(ot: str, incident: str, filename: str):
    safe_ot = Path(ot).name
    safe_incident = Path(incident).name
    safe_name = Path(filename).name
    base = helix_downloads_dir.resolve()
    path = (base / safe_ot / safe_incident / safe_name).resolve()
    if base not in path.parents:
        raise HTTPException(status_code=403, detail="Ruta no permitida")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo Helix no encontrado")
    return FileResponse(path, filename=path.name)
# === DECO JOBS COMPAT HELIX V6 ===
import asyncio as _deco_asyncio
import inspect as _deco_inspect
from typing import Optional as _DecoOptional
from fastapi import HTTPException as _DecoHTTPException
from fastapi import Query as _DecoQuery
from pydantic import BaseModel as _DecoBaseModel
from pydantic import Field as _DecoField
from app.services.deco.jobs import (
    crear_job as _deco_create_job,
    health as _deco_orchestrator_health,
    listar_jobs as _deco_list_jobs,
    obtener_job as _deco_get_job,
)
class DecoCompatJobRequest(_DecoBaseModel):
    mensaje: str = _DecoField(min_length=1)
    conversation_id: _DecoOptional[str] = None
    user_id: _DecoOptional[str] = None
def _deco_legacy_execute(
    message: str,
    conversation_id: _DecoOptional[str],
):
    request_data = {
        "mensaje": message,
    }
    model_fields = getattr(
        DecoChatRequest,
        "model_fields",
        getattr(DecoChatRequest, "__fields__", {}),
    )
    if "conversation_id" in model_fields:
        request_data["conversation_id"] = conversation_id
    request = DecoChatRequest(**request_data)
    result = deco_chat(request)
    if _deco_inspect.isawaitable(result):
        return _deco_asyncio.run(result)
    return result
@router.get("/api/deco/orchestrator/health")
def deco_orchestrator_health():
    return _deco_orchestrator_health()
@router.post("/api/deco/jobs")
def create_deco_job_compat(req: DecoCompatJobRequest):
    try:
        job = _deco_create_job(
            message=req.mensaje,
            conversation_id=req.conversation_id,
            user_id=req.user_id,
            ejecutor_heredado=_deco_legacy_execute,
        )
        return {
            "ok": True,
            "tipo_respuesta": "deco_job",
            "job": job,
        }
    except Exception as exc:
        raise _DecoHTTPException(
            status_code=500,
            detail=str(exc),
        )
@router.get("/api/deco/jobs")
def list_deco_jobs_compat(
    limit: int = _DecoQuery(default=50, ge=1, le=200),
    conversation_id: _DecoOptional[str] = None,
):
    return _deco_list_jobs(
        limit=limit,
        conversation_id=conversation_id,
    )
@router.get("/api/deco/jobs/{job_id}")
def get_deco_job_compat(job_id: str):
    job = _deco_get_job(job_id)
    if not job:
        raise _DecoHTTPException(
            status_code=404,
            detail="Trabajo DECO no encontrado",
        )
    return {
        "ok": True,
        "job": job,
    }
# === FIN DECO JOBS COMPAT HELIX V6 ===
# ENDPOINTS_HFC_RAPIDOS_V1
@router.get("/api/deco/hfc/estado/{nodo}")
def deco_hfc_estado_nodo_rapido(nodo: str):
    from app.clients.hfc import estado_nodo
    result = estado_nodo(nodo)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
@router.get("/api/deco/capabilities/hfc")
def deco_capabilities_hfc():
    from app.services.deco.registry import catalogo_hfc_rapido
    return catalogo_hfc_rapido()
# ENDPOINT_HFC_MODEMS_NODO_V1
@router.get("/api/deco/hfc/modems/{nodo}")
def hfc_modems_nodo(nodo: str):
    from app.clients.hfc import modems_nodo
    result = modems_nodo(nodo)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
# ENDPOINT_HFC_ESTADO_CMTS_V1
@router.get("/api/deco/hfc/cmts/estado/{cmts}")
def hfc_estado_cmts(cmts: str):
    from app.clients.hfc import estado_cmts
    result = estado_cmts(cmts)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
# ENDPOINT_HFC_NODOS_CMTS_V1
@router.get("/api/deco/hfc/cmts/nodos/{cmts}")
def hfc_nodos_cmts(cmts: str):
    from app.clients.hfc import nodos_cmts
    result = nodos_cmts(cmts)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
# ENDPOINTS_HFC_CATALOGO_EXPANDIDO_V1
@router.get("/api/deco/hfc/buscar/nodo/{nodo}")
def hfc_buscar_nodo(nodo: str):
    from app.clients.hfc import buscar_nodo
    result = buscar_nodo(nodo)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
@router.get("/api/deco/hfc/cmts/afectados/{cmts}")
def hfc_afectados_cmts(cmts: str):
    from app.clients.hfc import afectados_cmts
    result = afectados_cmts(cmts)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
@router.get("/api/deco/hfc/zona/criticos/{zona}")
def hfc_nodos_criticos_zona(zona: str):
    from app.clients.hfc import nodos_criticos_zona
    result = nodos_criticos_zona(zona)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
@router.get("/api/deco/hfc/buscar/cmts/{texto}")
def hfc_buscar_cmts(texto: str):
    from app.clients.hfc import buscar_cmts
    result = buscar_cmts(texto)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
# ENDPOINT_HFC_HISTORIAL_NODO_V1
@router.get("/api/deco/hfc/nodo/historial/{nodo}")
def hfc_historial_nodo(nodo: str):
    from app.clients.hfc import historial_nodo
    result = historial_nodo(nodo)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
# FTTH_TRONCAL_DIRECCIONES_ENDPOINT_V1
from app.services.ftth.ftth_troncal_direcciones_service import (
    consultar_direcciones_troncal_ftth,
)
@router.get("/api/deco/ftth/troncal/{wo}/direcciones")
def deco_ftth_troncal_direcciones(wo: str):
    try:
        return consultar_direcciones_troncal_ftth(wo)
    except Exception as exc:
        error_type = type(exc).__name__
        error_text = str(exc)
        combined = f"{error_type}: {error_text}".lower()
        ssh_unavailable = any(
            token in combined
            for token in (
                "timeout",
                "timed out",
                "ssh",
                "opening channel",
                "connection refused",
                "no route to host",
            )
        )
        return {
            "ok": False,
            "tipo_respuesta": "direccion_clientes_ftth_troncal",
            "codigo": (
                "OLT_ACCESO_NO_DISPONIBLE"
                if ssh_unavailable
                else "FTTH_TRONCAL_EXEC_ERROR"
            ),
            "wo": wo,
            "etapa": "ACCESO_OLT" if ssh_unavailable else "EJECUCION_FTTH_TRONCAL",
            "error_tipo": error_type,
            "error": error_text,
        }
# VM_FTTH_RUNTIME_CONSOLIDADO_START
from app.services.ftth.vm_ftth_runtime import (
    diagnosticar_vm_ftth as _vm_diag_runtime,
)
# VM_FTTH_RUNTIME_CONSOLIDADO_END
# VM_FTTH_DIRECT_V2_START
from fastapi import Body as _VmBody, HTTPException as _VmHTTPException
from app.services.ftth.vm_ftth_direct_service import (
    VmFtthError as _VmFtthError,
    health_vm_ftth as _vm_health,
    listar_olts_vm_ftth as _vm_olts,
    descubrir_puertos_vm_ftth as _vm_discover,
    diagnosticar_vm_ftth as _vm_diag,
)
@router.get("/api/deco/ftth/vm/health")
def deco_vm_ftth_health():
    return _vm_health()
@router.get("/api/deco/ftth/vm/olts")
def deco_vm_ftth_olts():
    try:
        return _vm_olts()
    except _VmFtthError as exc:
        raise _VmHTTPException(status_code=400, detail=str(exc))
@router.post("/api/deco/ftth/vm/descubrir")
def deco_vm_ftth_descubrir(payload: dict = _VmBody(...)):
    try:
        return _vm_discover(payload)
    except _VmFtthError as exc:
        raise _VmHTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _VmHTTPException(status_code=502, detail=f"VM_FTTH_DISCOVERY_ERROR:{type(exc).__name__}:{exc}")
@router.post("/api/deco/ftth/vm/diagnostico")
def deco_vm_ftth_diagnostico(payload: dict = _VmBody(...)):
    try:
        return _vm_diag_runtime(payload)
    except _VmFtthError as exc:
        raise _VmHTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _VmHTTPException(status_code=502, detail=f"VM_FTTH_DIAG_ERROR:{type(exc).__name__}:{exc}")
# VM_FTTH_DIRECT_V2_END
# VM_FTTH_PREWARM_V2_7_START
from app.services.ftth.vm_ftth_direct_service import (
    precalentar_vm_ftth as _vm_prewarm_v7,
    estado_cache_vm_ftth as _vm_cache_status_v7,
)
@router.post("/api/deco/ftth/vm/prewarm")
def deco_vm_ftth_prewarm_v7(payload: dict = _VmBody(...)):
    try:
        return _vm_prewarm_v7(payload)
    except _VmFtthError as exc:
        raise _VmHTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _VmHTTPException(
            status_code=502,
            detail=f"VM_FTTH_PREWARM_ERROR:{type(exc).__name__}:{exc}"
        )
@router.get("/api/deco/ftth/vm/cache")
def deco_vm_ftth_cache_v7():
    return _vm_cache_status_v7()
# VM_FTTH_PREWARM_V2_7_END
@router.get("/api/deco/confirmaciones/imagen")
def deco_confirmaciones_imagen():
    # CONFIRMACIONES_IMAGE_PROXY_8026_V1
    from urllib.error import HTTPError, URLError
    from urllib.request import urlopen
    from fastapi.responses import Response
    from app.config.endpoints_settings import (
        get_confirmaciones_api_timeout,
        get_confirmaciones_api_url,
    )
    url = (
        get_confirmaciones_api_url()
        + "/api/confirmaciones/imagen"
    )
    try:
        with urlopen(
            url,
            timeout=get_confirmaciones_api_timeout(),
        ) as upstream:
            body = upstream.read()
            content_type = (
                upstream.headers.get_content_type()
                or "image/jpeg"
            )
    except HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(
                status_code=404,
                detail="Imagen de confirmacion no encontrada",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"Confirmaciones respondio HTTP {exc.code}",
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail="API de confirmaciones no disponible",
        ) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-ATLAS-Confirmaciones-Source": "8026",
        },
    )
