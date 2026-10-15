import httpx

from app.order_models import OrderRequest
from app.resilience.retry import call_with_retry


class OrderProcessingError(Exception):
    pass


def process_order(
    order_id: int,
    user_id: int,
    order: OrderRequest,
    catalog_url: str,
    wallet_url: str,
) -> None:
    try:
        ## Desacoplar llamado a catalogo --> Crear Catalog Client
        catalog_response = call_with_retry(
            "GET",
            f"{catalog_url}/api/v1/catalog",
            timeout=5.0,
        )
    except httpx.RequestError as exc:
        raise OrderProcessingError("Failed to connect to Catalog Service") from exc

    if catalog_response.status_code != 200:
        raise OrderProcessingError("Catalog Service unreachable")

    assets = catalog_response.json()
    asset = next((item for item in assets if item["id"] == order.asset_id), None)
    if asset is None:
        raise OrderProcessingError("Asset not found in catalog")
    if asset["status"] != "active":
        raise OrderProcessingError("Asset is inactive. Trading blocked.")

    try:
        ## Hay que sacar esto de acá, order processing tiene que ser agnóstico a la petición -> Crear Wallet Client
        wallet_response = call_with_retry(
            "POST",
            f"{wallet_url}/api/v1/wallets/transact",
            headers={"X-User-Id": str(user_id)},
            json={
                "user_id": user_id,
                "amount": -order.total_price,
                "transaction_id": f"order:{order_id}",
            },
            timeout=5.0,
            retry_on_connect_only=True,
        )
    except httpx.RequestError as exc:
        raise OrderProcessingError("Failed to connect to Wallet Service") from exc

    if wallet_response.status_code != 200:
        try:
            error_detail = wallet_response.json().get(
                "detail", "Wallet transaction failed"
            )
        except ValueError:
            error_detail = "Wallet transaction failed"
        raise OrderProcessingError(str(error_detail))
