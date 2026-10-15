# app/routers/gateway_router.py
from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
import httpx
from app.security.gatekeeper import gatekeeper_verify
import os

router = APIRouter(prefix="/api/v1", tags=["API Gateway Perimetral"])

# ==========================================
# CONFIGURACIÓN DE RUTAS POR PERÍMETRO
# ==========================================
PUBLIC_SERVICE_ROUTES = {
    "catalog": os.getenv("CATALOG_SERVICE_URL", "http://catalog_service:8000")
}

PRIVATE_SERVICE_ROUTES = {
    "orders": os.getenv("ORDER_SERVICE_URL", "http://order_service:8000"),
    "wallets": os.getenv("WALLET_SERVICE_URL", "http://wallet_service:8000")
}

ALL_SERVICES = PUBLIC_SERVICE_ROUTES | PRIVATE_SERVICE_ROUTES


# ==========================================
# 1. HEALTH CHECKS Y RUTAS PUBLICAS (SIN GATEKEEPER)
# ==========================================

@router.get("/{service_name}/health")
async def gateway_service_health(service_name: str):
    """
    Permite consultar el liveness check 
    de cualquier microservicio interno sin autenticación.
    """
    if service_name not in ALL_SERVICES:
        raise HTTPException(status_code=404, detail="Servicio no registrado en el Gateway")

    target_url = f"{ALL_SERVICES[service_name]}/health"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(target_url, timeout=3.0)
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )
        except httpx.RequestError:
            return Response(
                content='{"status": "down"}',
                status_code=503,
                media_type="application/json"
            )


@router.get("/orders/health/dependencies")
async def gateway_orders_dependencies():
    """
    Consulta específica para el readiness profundo de dependencias de órdenes.
    """
    order_base_url = ALL_SERVICES.get("orders", "http://order_service:8000")
    target_url = f"{order_base_url}/health/dependencies"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(target_url, timeout=3.0)
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )
        except httpx.RequestError:
            return Response(
                content='{"status": "degraded"}',
                status_code=503,
                media_type="application/json"
            )
@router.api_route("/catalog{path:path}", methods=["GET"])
async def public_catalog_proxy(path: str, request: Request):
    """
    Proxy público para el catálogo. Permite consultas sin requerir token JWT
    ni inyectar cabeceras de usuario.
    """
    target_base_url = PUBLIC_SERVICE_ROUTES["catalog"]
    target_url = f"{target_base_url}/api/v1/catalog{path}"

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=target_url,
                content=await request.body(),
                headers=headers,
                params=request.query_params,
                timeout=10.0
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type")
            )
        except httpx.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="El servicio de catálogo no se encuentra disponible temporalmente."
            )   

# ==========================================
# 2. GATEWAY PROTEGIDO
# ==========================================

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def gateway_proxy(
    path: str,
    request: Request,
    auth_context: dict = Depends(gatekeeper_verify)
):
    """
    Gateway Offloading:
    1. El Gatekeeper ya validó el JWT y extrajo el usuario.
    2. Se analiza la ruta para dirigir la petición. 
    3. Se inyecta cabecera de contexto seguro (X-User-Id) y se renvía la req
    """

    path_segments = path.strip("/").split("/")
    service_key = path_segments[0] if path_segments else ""

    if service_key not in PRIVATE_SERVICE_ROUTES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ruta no encontrada o servicio no registrado en el Gateway para '{service_key}'"
        )

    target_base_url = PRIVATE_SERVICE_ROUTES[service_key]
    target_url = f"{target_base_url}/api/v1/{path}"

    # Preparar los headers heredados de la petición original
    headers = dict(request.headers)
    
    # Remover headers de red perimetral que deben recalcularse o no propagarse tal cual
    headers.pop("host", None)
    headers.pop("content-length", None)
    
    # Inyectar contexto seguro validado por el Gatekeeper
    user_id = str(auth_context.get("user_id", ""))
    roles_list = auth_context.get("roles", [])
    user_roles = ",".join(roles_list)

    headers.update({
        "X-User-Id": user_id,
        "X-User-Roles": user_roles,
        "X-Forwarded-Proto": "https"
    })

    # Capturar payload y parámetros de consulta
    body = await request.body()
    query_params = request.query_params

    # Reenviar la petición mediante HTTPX de forma asíncrona
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=target_url,
                content=body,
                headers=headers,
                params=query_params,
                timeout=10.0
            )
            
            # Limpiar headers de transferencia del servidor interno
            excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
            response_headers = {
                k: v for k, v in response.headers.items() 
                if k.lower() not in excluded_headers
            }

            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type")
            )

        except httpx.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"El microservicio interno '{service_key}' no se encuentra disponible temporalmente."
            )