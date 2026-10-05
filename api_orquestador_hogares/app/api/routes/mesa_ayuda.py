# -*- coding: utf-8 -*-
"""API del simulador Mesa de Ayuda dentro del namespace DECO."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.mesa_ayuda.catalogo import opciones_publicas
from app.services.mesa_ayuda.conversation_service import conversar, health
from app.services.mesa_ayuda.smcc_bridge_service import (
    health_smcc_bridge,
    procesar_mensaje_smcc,
)
from app.services.mesa_ayuda.helix_note_publish_service import publicar_nota_helix_smcc

# SMCC_HELIX_NOTE_TRACKING_V1_1
from app.services.mesa_ayuda.smcc_tracking_schema import SmccTrackingEventIn
from app.services.mesa_ayuda.smcc_tracking_service import SmccTrackingService
router = APIRouter(
    prefix="/api/deco/mesa-ayuda",
    tags=["DECO - Mesa de Ayuda"],
)


class MesaAyudaChatRequest(BaseModel):
    mensaje: str = Field(default="", max_length=500)
    conversation_id: str = Field(min_length=1, max_length=200)
    user_id: str | None = Field(default=None, max_length=100)
    canal: str = Field(default="ATLAS", min_length=1, max_length=40)
    # ATLAS_MESA_HELIX_CREDENTIALS_REQUEST_V2
    helix_username: str = Field(default="", max_length=200)
    helix_password: str = Field(default="", max_length=500)


@router.get("/health")
def mesa_ayuda_health():
    return health()


@router.get("/catalogo")
def mesa_ayuda_catalogo():
    return {
        "ok": True,
        "opciones": opciones_publicas(),
        "nota": (
            "ERROR_ESCALAMIENTO no se expone como solicitud pública; "
            "queda reservado como clasificación interna."
        ),
    }


@router.post("/chat")
def mesa_ayuda_chat(req: MesaAyudaChatRequest):
    try:
        return conversar(
            mensaje=req.mensaje,
            conversation_id=req.conversation_id,
            user_id=req.user_id,
            canal=req.canal,
            helix_username=req.helix_username,
            helix_password=req.helix_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        )

# SMCC_ATLAS_BRIDGE_ROUTE_FASE1_V1
class SmccBridgeRequest(BaseModel):
    case_id: str = Field(min_length=6, max_length=30)
    message_id: str = Field(min_length=1, max_length=160)
    origin: str = Field(min_length=1, max_length=30)
    text: str = Field(default="", max_length=4000)
    timestamp: str = Field(default="", max_length=100)
    channel: str = Field(default="WHATSAPP", max_length=40)
    agent_id: str = Field(default="", max_length=100)
    source_url: str = Field(default="", max_length=1000)
    dom_id: str = Field(default="", max_length=200)
    # HELIX_NOTE_AUTO_FINAL_ROUTE_V1
    action: str = Field(default="PROCESS_CLIENT", max_length=40)
    note_text: str = Field(default="", max_length=120000)
    note_hash: str = Field(default="", max_length=128)
    note_context: dict = Field(default_factory=dict)




    # HELIX_SESSION_AUTH_V13_F1_2
    # Credenciales efímeras para operaciones Helix autorizadas de SMCC.
    helix_auth: dict = Field(default_factory=dict)
    helix_auth_mode: str = Field(default="", max_length=40)
@router.get("/smcc/health")
def smcc_bridge_health():
    return health_smcc_bridge()


# SMCC_HELIX_NOTE_TRACKING_V1_1
def _track_helix_note_result(
    req: SmccBridgeRequest,
    result: dict,
) -> None:
    """
    Auditoría fail-open de la publicación de nota.

    Nunca persiste password.
    El tracking nunca debe impedir una publicación Helix.
    """
    try:
        data = result if isinstance(result, dict) else {}

        context = (
            req.note_context
            if isinstance(req.note_context, dict)
            else {}
        )

        production_write = (
            data.get("production_write") is True
        )

        ok = data.get("ok") is True

        if ok and production_write:
            note_status = "PUBLISHED"
        elif production_write:
            note_status = "COMMIT_UNVERIFIED"
        else:
            note_status = "ERROR_PRECOMMIT"

        wo = str(
            data.get("wo")
            or context.get("wo")
            or ""
        ).strip().upper()

        operation = str(
            context.get("tipo_nombre")
            or context.get("tipo")
            or ""
        ).strip()

        request_mode = str(
            getattr(
                req,
                "helix_auth_mode",
                "",
            )
            or ""
        ).strip().upper()

        request_user = str(
            getattr(
                req,
                "helix_user",
                "",
            )
            or ""
        ).strip()

        request_auth = getattr(
            req,
            "helix_auth",
            None,
        )

        if (
            not request_user
            and isinstance(request_auth, dict)
        ):
            request_user = str(
                request_auth.get("username")
                or ""
            ).strip()

        helix_auth_mode = str(
            data.get("helix_auth_mode")
            or request_mode
            or ""
        ).strip().upper()

        helix_user = str(
            data.get("helix_user")
            or request_user
            or ""
        ).strip()

        incidente = str(
            data.get("incidente")
            or ""
        ).strip().upper()

        codigo = str(
            data.get("codigo")
            or ""
        ).strip()

        SmccTrackingService().register_event(
            SmccTrackingEventIn(
                conversation_id=(
                    f"smcc:{str(req.case_id).strip()}"
                ),
                event="HELIX_NOTE_RESULT",
                wo=wo or None,
                operation=operation or None,
                status="ACTIVE",
                result=note_status,
                error_code=(
                    codigo
                    if note_status != "PUBLISHED"
                    else None
                ),
                metadata={
                    "source": "SMCC_HELIX_NOTE_BACKEND",
                    "helix_auth_mode": helix_auth_mode,
                    "helix_user": helix_user,
                    "note_status": note_status,
                    "incidente": incidente,
                    "production_write": production_write,
                    "codigo": codigo,
                },
            )
        )

    except Exception:
        # Fail-open.
        return


@router.post("/smcc/bridge")
def smcc_bridge(req: SmccBridgeRequest):
    try:
        action = str(req.action or "PROCESS_CLIENT").strip().upper()

        if action == "HELIX_NOTE_PUBLISH":
            note_result = publicar_nota_helix_smcc(
                case_id=req.case_id,
                note_text=req.note_text,
                note_hash=req.note_hash,
                note_context=req.note_context,
                agent_id=req.agent_id,

                helix_auth=req.helix_auth,
                helix_auth_mode=req.helix_auth_mode,
            )

            _track_helix_note_result(
                req,
                note_result,
            )

            return note_result
        if action not in {"", "PROCESS_CLIENT"}:
            raise HTTPException(
                status_code=400,
                detail=f"Acción SMCC no soportada: {action}",
            )

        result = procesar_mensaje_smcc(
            case_id=req.case_id,
            message_id=req.message_id,
            origin=req.origin,
            text=req.text,
            timestamp=req.timestamp,
            channel=req.channel,
            agent_id=req.agent_id,
            source_url=req.source_url,
            dom_id=req.dom_id,
            # ATLAS_SMCC_PROCESS_CLIENT_AUTH_FORWARD_V1
            helix_auth=req.helix_auth,
            helix_auth_mode=req.helix_auth_mode,
        )

        if not result.get("ok"):
            raise HTTPException(
                status_code=422,
                detail=result,
            )

        return result

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        )