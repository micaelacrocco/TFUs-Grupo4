import socket

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1", tags=["Catalog Service"])

# Base de datos simulada en memoria para la demo. Al vivir en el proceso, cada
# réplica de este servicio tiene su propia copia: el catálogo se considera
# "de solo lectura" a los fines de la demo de escalabilidad horizontal
# (ver scripts/demo_ut3_scaling.sh); habilitar/deshabilitar un activo
# (I-CAT-02) solo afecta a la réplica que atendió esa request.
assets_db = {
    1: {"id": 1, "name": "Apple Inc.", "symbol": "AAPL", "status": "active"},
    2: {"id": 2, "name": "S&P 500 ETF", "symbol": "SPY", "status": "active"},
    3: {"id": 3, "name": "Tesla Inc.", "symbol": "TSLA", "status": "inactive"}
}

# Contador en memoria usado solo por el endpoint de fault injection de abajo
# (demo del patrón Retry, UT4): cuántas próximas llamadas a GET /assets
# deben simular una falla transitoria antes de volver a responder normal.
_fail_next_n = 0

@router.get("/assets")
def get_assets():
    """I-CAT-01: Consulta pública del catálogo de activos.

    Incluye `served_by` (hostname del contenedor) para poder evidenciar,
    al escalar horizontalmente este servicio, que distintas réplicas
    responden indistintamente a la misma solicitud.
    """
    global _fail_next_n
    if _fail_next_n > 0:
        _fail_next_n -= 1
        raise HTTPException(status_code=503, detail="Simulated transient failure (debug)")

    served_by = socket.gethostname()
    return [{**asset, "served_by": served_by} for asset in assets_db.values()]

@router.post("/_debug/fail-next/{n}")
def set_fail_next(n: int):
    """Solo para demostrar el patrón Retry (UT4, scripts/demo_ut4_retry.sh):
    fuerza que las próximas `n` llamadas a `GET /assets` devuelvan 503,
    simulando una falla transitoria real de forma determinística, sin
    depender de los tiempos de reinicio de un contenedor real."""
    global _fail_next_n
    _fail_next_n = n
    return {"fail_next": _fail_next_n}

@router.patch("/assets/{asset_id}/status")
def update_asset_status(asset_id: int, status: str):
    """I-CAT-02: Interfaz de administración para habilitar/deshabilitar activos."""
    if asset_id not in assets_db:
        raise HTTPException(status_code=404, detail="Asset not found")
    if status not in ["active", "inactive"]:
        raise HTTPException(status_code=400, detail="Invalid status value")
    
    assets_db[asset_id]["status"] = status
    return {"message": "Status updated successfully", "asset": assets_db[asset_id]}