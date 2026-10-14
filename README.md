# InvestNow — TFU (Grupo 4)

Trabajo Final incremental a lo largo de las Unidades Temáticas de la
materia. Cada unidad agrega una entrega sobre la anterior; este README es
un índice del estado actual del repositorio y de dónde encontrar la
documentación de cada entrega.

---

## Entregas

| Unidad | Tema                                            | Documentación                                                  |
|--------|--------------------------------------------------|-----------------------------------------------------------------|
| UT1    | Contexto de negocio, historias de usuario y requerimientos arquitectónicamente significativos | [`docs/UT1/ENTREGA.md`](docs/UT1/ENTREGA.md) |
| UT2    | Tácticas de arquitectura (config externa, aspectos, sanity checking) | [`docs/UT2/ENTREGA.md`](docs/UT2/ENTREGA.md) |
| UT3    | Descomposición en microservicios (Catálogo, Wallet, Órdenes) | [`docs/UT3/ENTREGA.md`](docs/UT3/ENTREGA.md) · [`docs/UT3/PRUEBAS_ENDPOINTS.md`](docs/UT3/PRUEBAS_ENDPOINTS.md) |
| UT4 | Patrones de Arquitectura: Disponibilidad (Health Endpoint Monitoring, Retry) y Seguridad (Gatekeeper, Gateway Offloading) | [`docs/UT4/ENTREGA.md`](docs/UT4/ENTREGA.md) |

UT1 es el trabajo de análisis previo (sin código asociado); UT2, UT3 y UT4 son
las entregas con implementación, incrementales entre sí.

Cada documento de entrega es autocontenido: explica el alcance, las
decisiones tomadas y cómo demostrarlas para esa unidad puntual. El código
es incremental (lo de UT2 y UT3 sigue funcionando dentro del sistema de UT4), así
que para entender una decisión de diseño conviene leer el documento de la
unidad correspondiente en vez de inferirlo del diff de código.

---

## Estructura del proyecto

```
app/
  main.py               # servicio "api" (UT2): valida config -> arma la app -> levanta el servidor
  catalog_main.py       # servicio de Catálogo (UT3)
  wallet_main.py        # servicio de Wallet (UT3), persiste en Postgres
  order_main.py         # servicio de Órdenes (UT3)
  config/               # config.yaml compartido + carga y validación (UT2)
  aspects/              # aspecto de logging, aislado del resto de la app (UT2)
  routers/              # endpoints de cada servicio
  security/             # módulo del Patrón Gatekeeper y validadores JWT (UT4)
docker/                 # un Dockerfile por servicio (api, catalog, wallet, order) + init.sql de Postgres
docker-compose.yaml     # levanta los 4 servicios + Postgres
docker-compose.scaling.yml  # proyecto de Compose separado para escalar catalog_service (ver demo_ut3_scaling.sh)
scripts/                # demos de cada RNF (UT2), de cada concepto de UT3 y patrones de UT4
docs/
  UT1/                  # documentación de la entrega de UT1
  UT2/                  # documentación de la entrega de UT2
  UT3/                  # documentación de la entrega de UT3
  UT4/                  # documentación de la entrega de UT4
```

---

## Puesta en marcha (stack completo)

```bash
cp .env.example .env   # una sola vez
docker compose up --build
```

Esto levanta:

| Servicio          | Puerto | Origen |
|-------------------|--------|--------|
| `api`             | 8000   | UT2 (demo de tácticas de arquitectura) UT4 Gateway|
| `wallet_service`  | aislado   | UT3 (saldo persistido en Postgres) UT4 Gatekeeper |
| `catalog_service` | aislado   | UT3, UT4 Gatekeeper|
| `order_service`   | aislado   | UT3, UT4 Gatekeeper|
| `postgres_db`     | 5432   | UT3 |

Para las demos puntuales de cada RNF de UT2 ver la sección de scripts en
[`docs/UT2/ENTREGA.md`](docs/UT2/ENTREGA.md#6-scripts); para probar los
endpoints de UT3 ver [`docs/UT3/PRUEBAS_ENDPOINTS.md`](docs/UT3/PRUEBAS_ENDPOINTS.md);
para las demos de ACID, servicios sin estado y escalabilidad horizontal de
UT3 ver la sección 7 de [`docs/UT3/ENTREGA.md`](docs/UT3/ENTREGA.md).
Para las pruebas de Health Checks y Retry de UT4 ver docs/UT4/ENTREGA.md. Para las demos de Gatekeeper y Gateway Offloading  ver docs/UT4/SECURITY.md.
