# InvestNow (Grupo 4)

InvestNow es una API que simula la compra de activos. El cliente consulta el
Catálogo, envía una orden y puede ver su estado y el saldo de su billetera.
El proyecto crece con cada unidad de la materia.

## Entregas

| Unidad | Tema | Documentación |
|---|---|---|
| UT1 | Contexto de negocio, historias de usuario y requisitos de arquitectura | [Entrega](docs/UT1/ENTREGA.md) |
| UT2 | Configuración externa, logging y validación al inicio | [Entrega](docs/UT2/ENTREGA.md) |
| UT3 | Separación en microservicios | [Entrega](docs/UT3/ENTREGA.md) y [pruebas de los endpoints](docs/UT3/PRUEBAS_ENDPOINTS.md) |
| UT4 | Patrones de disponibilidad, rendimiento y seguridad | [Entrega](docs/UT4/ENTREGA.md) |
| UT5 | Arquitectura basada en eventos | [Entrega](docs/UT5/ENTREGA.md) y [plan de implementación](docs/UT5/PLAN_DE_IMPLEMENTACION.md) |

## Cómo levantar UT5

Con Docker y Docker Compose disponibles, corré estos comandos desde la raíz
del repo. El primero crea `.env` con los valores de la demo si todavía no
existe.

```bash
test -f .env || cp .env.example .env
docker compose up --build -d --wait
```

El Gateway queda en [localhost:8000](http://localhost:8000). Podés comprobar
que responde con `curl http://localhost:8000/health`. Es el único servicio
que publica un puerto en tu máquina.

| Servicio | Qué hace |
|---|---|
| `api` | Recibe las peticiones, autentica al usuario y las envía al servicio correspondiente. |
| `order_service` | Recibe los pedidos de compra y expone su estado. |
| `catalog_service` | Expone el catálogo y responde a eventos de órdenes con una validación o un rechazo. Usa Redis como caché para las consultas. |
| `wallet_service` | Maneja el saldo y los movimientos de cada billetera. |
| `audit_service` | Guarda una copia de los eventos para poder seguir cada orden. |
| `postgres_db` | Aloja tres bases separadas para Órdenes, Wallet y Audit. |
| `redis` | Guarda en caché las consultas del catálogo. |
| `rabbitmq` | Distribuye los eventos y guarda los mensajes pendientes en las colas. |
| `rabbitmq_init` | Crea los exchanges, las colas y los permisos al levantar el stack. Termina una vez que los configura. |
| `order_worker` | Sigue completando las compras que entran por la API hasta que se integren las tandas 2 y 3. |

UT5 crea sus propios volúmenes para Postgres y RabbitMQ. Si ya tenías una
demo de UT4, sus datos quedan en los volúmenes anteriores. Para comparar
con UT4, el plan toma el commit `7373fdf` como punto de partida.

## Cómo probar Catálogo y Audit

Catálogo escucha `OrderPlaced`. Si el activo está habilitado, publica
`AssetValidated`. Si está inactivo o no existe, publica `OrderRejected`.
Audit guarda tanto el evento de entrada como el resultado y registra una
sola fila por ID de evento.

Con el stack levantado, publicá una orden de ejemplo para un activo
inexistente. El comando manda el evento directo a RabbitMQ para probar esta
parte del flujo sin crear una compra por la API.

```bash
ORDER_ID=$(date +%s)
docker compose exec -T order_service python - "$ORDER_ID" <<'PY'
import sys

from app.events import Event, contract, publish_event

order_id = int(sys.argv[1])
event = Event.new(
    contract.ORDER_PLACED,
    correlation_id=order_id,
    payload={
        "order_id": order_id,
        "user_id": 1,
        "asset_id": 999999,
        "quantity": 1,
        "total_price": 20,
    },
)
publish_event(event)
PY
```

Desde la misma terminal, buscá los eventos de esa orden en `audit_db`.

```bash
docker compose exec -T postgres_db sh -c \
  'psql -U "$POSTGRES_USER" -d audit_db -c "$1"' audit \
  "SELECT event_type, payload
   FROM audit_events
   WHERE correlation_id = '$ORDER_ID'
   ORDER BY occurred_at"
```

Deberías ver `OrderPlaced` y `OrderRejected`. El rechazo incluye el motivo
`Asset not found in catalog`. Como el procesamiento ocurre en segundo plano,
si la consulta todavía no muestra las dos filas, esperá un momento y
ejecutala de nuevo.

## Dónde está el código

Los servicios están en `app/`. Los módulos `catalog_events.py` y
`audit_main.py` contienen la validación y el registro de eventos.
En `app/events/` están el contrato compartido y el código de publicación,
consumo y reintentos.

En `docker/` están los Dockerfiles y la inicialización de
Postgres. Los scripts de demostración de las unidades anteriores están
en `scripts/` y las instrucciones para ejecutarlos están en sus respectivas
entregas.
