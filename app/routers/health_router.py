"""Health Endpoint Monitoring (UT4): expone el estado de cada servicio para
que un orquestador (Docker Compose) o un monitor externo pueda detectar
degradación antes de que falle una request de negocio real.

Se distinguen dos niveles, siguiendo la idea central del patrón:

- `GET /health` (liveness): el proceso está arriba y puede responder. Si el
  servicio tiene una dependencia propia e imprescindible para cumplir su
  responsabilidad (ej. Wallet necesita Postgres), este chequeo también la
  valida -- no tiene sentido reportarse "sano" si no puede hacer lo único
  que hace.
- `GET /health/dependencies` (readiness profundo, opcional): solo para
  servicios que a su vez dependen de OTROS servicios de la plataforma (ej.
  Order depende de Catalog y de Wallet). Agrega el estado de cada
  dependencia directa consultando el `/health` de cada una.

Aislado en su propio módulo y reutilizado por los cuatro `*_main.py`, con el
mismo criterio de separación de responsabilidades que ya usa el proyecto
para `app/aspects/` (UT2).
"""

from typing import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

LivenessCheck = Callable[[], tuple[bool, str]]
DependencyCheck = Callable[[], dict]


def build_health_router(
    service_name: str,
    *,
    liveness_check: LivenessCheck | None = None,
    dependency_check: DependencyCheck | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health():
        if liveness_check is None:
            return {"status": "ok", "service": service_name}

        ok, detail = liveness_check()
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ok" if ok else "down",
                "service": service_name,
                "detail": detail,
            },
        )

    if dependency_check is not None:

        @router.get("/health/dependencies")
        def health_dependencies():
            dependencies = dependency_check()
            overall_ok = all(dep["status"] == "ok" for dep in dependencies.values())
            return JSONResponse(
                status_code=200 if overall_ok else 503,
                content={
                    "status": "ok" if overall_ok else "degraded",
                    "service": service_name,
                    "dependencies": dependencies,
                },
            )

    return router
