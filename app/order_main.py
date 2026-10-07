# app/order_main.py

"""Arranque del Servicio de Órdenes: sanity check -> config -> FastAPI -> Uvicorn."""

import sys
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.aspects.logging_aspect import add_logging_aspect
from app.config.loader import build_config, load_raw_config
from app.config.validator import validate
from app.routers import order_router  # Router específico de gestión de órdenes
from app.routers.health_router import build_health_router

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

raw_config = load_raw_config(CONFIG_PATH)
errors = validate(raw_config)
if errors:
    print("Configuración inválida en Order Service, el servicio no va a arrancar:")
    for error in errors:
        print(f"  - {error}")
    sys.exit(1)

config = build_config(raw_config)

app = FastAPI(title="InvestNow - Order Service")
app.state.config = config

# Aplicar aspecto transversal de logging
add_logging_aspect(app, enabled=config.features.request_logging_enabled)

# Registrar rutas de procesamiento de órdenes
app.include_router(order_router.router)


def check_dependencies() -> dict:
    """Health Endpoint Monitoring (UT4): Order depende de Catalog y de
    Wallet (ver diagrama de componentes de UT3), así que además del
    liveness propio expone un chequeo profundo del estado de cada una.
    No usa retry a propósito: para una señal de salud interesa el estado
    inmediato, no uno enmascarado por reintentos."""
    results = {}
    for name, url in (("catalog", order_router.CATALOG_URL), ("wallet", order_router.WALLET_URL)):
        try:
            response = httpx.get(f"{url}/health", timeout=2.0)
            results[name] = {"status": "ok" if response.status_code == 200 else "down"}
        except httpx.RequestError as exc:
            results[name] = {"status": "down", "detail": str(exc)}
    return results


app.include_router(build_health_router("order", dependency_check=check_dependencies))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)