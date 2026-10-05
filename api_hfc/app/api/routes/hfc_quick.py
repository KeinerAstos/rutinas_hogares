from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/deco/hfc", tags=["HFC Quick"])

def _ok_or_404(result):
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result

@router.get("/estado/{nodo}")
def estado_nodo(nodo: str):
    from app.services.hfc_quick_service import estado_nodo as service
    return _ok_or_404(service(nodo))

@router.get("/modems/{nodo}")
def modems_nodo(nodo: str):
    from app.services.hfc_quick_service import modems_nodo as service
    return _ok_or_404(service(nodo))

@router.get("/cmts/estado/{cmts}")
def estado_cmts(cmts: str):
    from app.services.hfc_quick_service import estado_cmts as service
    return _ok_or_404(service(cmts))

@router.get("/cmts/nodos/{cmts}")
def nodos_cmts(cmts: str):
    from app.services.hfc_quick_service import nodos_cmts as service
    return _ok_or_404(service(cmts))

@router.get("/buscar/nodo/{nodo}")
def buscar_nodo(nodo: str):
    from app.services.hfc_quick_service import buscar_nodo as service
    return _ok_or_404(service(nodo))

@router.get("/cmts/afectados/{cmts}")
def afectados_cmts(cmts: str):
    from app.services.hfc_quick_service import afectados_cmts as service
    return _ok_or_404(service(cmts))

@router.get("/zona/criticos/{zona}")
def nodos_criticos_zona(zona: str):
    from app.services.hfc_quick_service import nodos_criticos_zona as service
    return _ok_or_404(service(zona))

@router.get("/buscar/cmts/{texto}")
def buscar_cmts(texto: str):
    from app.services.hfc_quick_service import buscar_cmts as service
    return _ok_or_404(service(texto))

@router.get("/nodo/historial/{nodo}")
def historial_nodo(nodo: str):
    from app.services.hfc_quick_service import historial_nodo as service
    return _ok_or_404(service(nodo))