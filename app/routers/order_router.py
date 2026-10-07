import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx

from app.resilience.retry import call_with_retry

router = APIRouter(prefix="/api/v1", tags=["Order Service"])

# URLs internas obtenidas de las variables de entorno configuradas en Docker Compose
CATALOG_URL = os.getenv("CATALOG_SERVICE_URL", "http://catalog_service:8000")
WALLET_URL = os.getenv("WALLET_SERVICE_URL", "http://wallet_service:8000")

orders_db = []

class OrderRequest(BaseModel):
    user_id: int
    asset_id: int
    quantity: int
    total_price: float

@router.post("/orders")
def create_order(order: OrderRequest):
    """I-ORD-01: Procesamiento de orden consumiendo Catálogo y Wallet."""
    
    # 1. CONSUMO DE INTERFAZ EXTERNA: Verificar si el activo existe y está activo en el Servicio de Catálogo
    # Retry (UT4): GET es idempotente, así que ante una falla transitoria
    # (conexión, timeout, 502/503/504) reintenta con backoff antes de
    # rendirse.
    try:
        response = call_with_retry("GET", f"{CATALOG_URL}/api/v1/assets", timeout=5.0)
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Catalog Service unreachable")
        
        assets = response.json()
        asset = next((a for a in assets if a["id"] == order.asset_id), None)
        
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found in catalog")
        if asset["status"] != "active":
            raise HTTPException(status_code=400, detail="Asset is inactive. Trading blocked.")
            
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Failed to connect to Catalog Service")

    # 2. CONSUMO DE INTERFAZ INTERNA: Debitar saldo en el Servicio de Wallet (Transacción ACID)
    # Retry (UT4): el débito NO es idempotente, así que solo reintenta si la
    # conexión nunca se estableció (`retry_on_connect_only=True`). Ante un
    # timeout de lectura no reintenta: el servidor pudo haber aplicado el
    # débito antes de que se perdiera la respuesta, y reintentarlo a ciegas
    # arriesgaría debitar dos veces (ver ASR-05, UT1).
    try:
        tx_payload = {
            "user_id": order.user_id,
            "amount": -order.total_price  # Restar del saldo
        }
        tx_response = call_with_retry(
            "POST",
            f"{WALLET_URL}/api/v1/wallets/transact",
            json=tx_payload,
            timeout=5.0,
            retry_on_connect_only=True,
        )

        if tx_response.status_code != 200:
            error_detail = tx_response.json().get("detail", "Wallet transaction failed")
            raise HTTPException(status_code=400, detail=error_detail)
            
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Failed to connect to Wallet Service")

    # 3. Registrar la orden de forma exitosa
    new_order = {
        "order_id": len(orders_db) + 1,
        "user_id": order.user_id,
        "asset_id": order.asset_id,
        "quantity": order.quantity,
        "total_price": order.total_price,
        "status": "executed"
    }
    orders_db.append(new_order)
    
    return {
        "message": "Order processed and executed successfully",
        "order": new_order
    }

@router.get("/orders/{user_id}")
def get_user_orders(user_id: int):
    """I-ORD-01: Consulta de historial de órdenes por usuario."""
    user_orders = [o for o in orders_db if o["user_id"] == user_id]
    return user_orders