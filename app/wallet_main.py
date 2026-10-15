# app/wallet_main.py

"""Arranque del Servicio de Wallet: sanity check -> config -> FastAPI (ACID) -> Uvicorn."""

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI

from app.aspects.logging_aspect import add_logging_aspect
from app.config.loader import build_config, load_raw_config
from app.config.validator import validate
from app.routers import wallet_router  # Router específico de cuentas y saldos
from app.routers.health_router import build_health_router

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

raw_config = load_raw_config(CONFIG_PATH)
errors = validate(raw_config)
if errors:
    print("Configuración inválida en Wallet Service, el servicio no va a arrancar:")
    for error in errors:
        print(f"  - {error}")
    sys.exit(1)

config = build_config(raw_config)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    wallet_router.initialize_wallet_schema()
    yield


app = FastAPI(title="InvestNow - Wallet Service (ACID Model)", lifespan=lifespan)
app.state.config = config

# Aplicar aspecto transversal de logging
add_logging_aspect(app, enabled=config.features.request_logging_enabled)

# Registrar rutas financieras de la wallet
app.include_router(wallet_router.router)


def check_database() -> tuple[bool, str]:
    """Health Endpoint Monitoring (UT4): la única dependencia de Wallet es
    Postgres, así que su liveness no tiene sentido sin validarla -- no está
    "sano" un proceso que no puede cumplir su única responsabilidad."""
    try:
        conn = wallet_router.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
        return True, "database reachable"
    except Exception as exc:  # cualquier falla de conexión/consulta cuenta como "down"
        return False, f"database unreachable: {exc}"


app.include_router(build_health_router("wallet", liveness_check=check_database))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)