# app/order_main.py

"""Arranque del Servicio de Órdenes: sanity check -> config -> FastAPI -> Uvicorn."""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI
import pika
import psycopg2

from app.aspects.logging_aspect import add_logging_aspect
from app.config.loader import build_config, load_raw_config
from app.config.validator import validate
from app.order_messaging import open_orders_channel
from app.order_store import initialize_order_schema
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

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    initialize_order_schema()
    yield


app = FastAPI(title="InvestNow - Order Service", lifespan=lifespan)
app.state.config = config

# Aplicar aspecto transversal de logging
add_logging_aspect(app, enabled=config.features.request_logging_enabled)

# Registrar rutas de procesamiento de órdenes
app.include_router(order_router.router)


def check_dependencies() -> dict:
    """Health Endpoint Monitoring (UT4): Order depende de Catalog y de
    Wallet, PostgreSQL y RabbitMQ. Expone el estado inmediato de cada una;
    no usa retry para no enmascarar una dependencia degradada."""
    results = {}
    for name, url in (
        ("catalog", os.getenv("CATALOG_SERVICE_URL", "http://catalog_service:8000")),
        ("wallet", os.getenv("WALLET_SERVICE_URL", "http://wallet_service:8000")),
    ):
        try:
            response = httpx.get(f"{url}/health", timeout=2.0)
            results[name] = {"status": "ok" if response.status_code == 200 else "down"}
        except httpx.RequestError as exc:
            results[name] = {"status": "down", "detail": str(exc)}
    try:
        connection, _ = open_orders_channel()
        connection.close()
        results["rabbitmq"] = {"status": "ok"}
    except (pika.exceptions.AMQPError, OSError, ValueError) as exc:
        results["rabbitmq"] = {"status": "down", "detail": str(exc)}
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        results["database"] = {
            "status": "down",
            "detail": "DATABASE_URL is not configured",
        }
    else:
        try:
            connection = psycopg2.connect(database_url)
            connection.close()
            results["database"] = {"status": "ok"}
        except (psycopg2.Error, OSError, ValueError) as exc:
            results["database"] = {"status": "down", "detail": str(exc)}
    return results


app.include_router(build_health_router("order", dependency_check=check_dependencies))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)