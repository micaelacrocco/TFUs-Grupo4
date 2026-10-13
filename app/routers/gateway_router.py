# app/routers/gateway_router.py
from fastapi import APIRouter, Depends, HTTPException, Response, status
import httpx
from app.security.gatekeeper import gatekeeper_verify
import os

router = APIRouter(prefix="/api/v1", tags=["API Gateway Perimetral"])

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order_service:8000")

@router.post("/orders")
async def gateway_create_order(order_payload: dict, auth_context: dict = Depends(gatekeeper_verify)):
    """
    Gateway Offloading:
    1. El Gatekeeper ya validó el JWT y extrajo el usuario.
    2. El Gateway reenvía la petición 
       inyectando cabeceras de contexto seguro (X-User-Id) al microservicio interno.
    """
    user_id = str(auth_context["user_id"])
    
    headers = {
        "X-User-Id": user_id,
        "X-Forwarded-Proto": "https" # Offloading de terminación TLS/Proxy
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{ORDER_SERVICE_URL}/api/v1/orders",
                json=order_payload,
                headers=headers,
                timeout=5.0
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )
        
        except httpx.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="El servicio de órdenes no se encuentra disponible temporalmente"
            )