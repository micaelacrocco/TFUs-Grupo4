import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.aspects.logging_aspect import add_logging_aspect
from app.catalog_events import validate_asset
from app.config.loader import build_config, load_raw_config
from app.config.validator import validate
from app.events import contract, start_consumer
from app.routers import catalog_router
from app.routers.health_router import build_health_router

CONFIG_PATH = Path(__file__).parent / "config" / "config.yaml"

raw_config = load_raw_config(CONFIG_PATH)
errors = validate(raw_config)
if errors:
    sys.exit(1)

config = build_config(raw_config)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("EVENTS_RABBITMQ_URL") or os.getenv("RABBITMQ_URL"):
        start_consumer(contract.QUEUE_CATALOG, validate_asset)
    yield


app = FastAPI(title="InvestNow - Catalog Service", lifespan=lifespan)
app.state.config = config

add_logging_aspect(app, enabled=config.features.request_logging_enabled)
app.include_router(catalog_router.router)

# Health Endpoint Monitoring (UT4): sin dependencias propias, chequeo shallow.
app.include_router(build_health_router("catalog"))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
