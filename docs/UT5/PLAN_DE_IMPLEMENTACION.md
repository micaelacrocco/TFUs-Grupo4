# Plan de implementación — UT5 Parte 2

> Guía para dividir el trabajo de la Parte 2 en tres líneas paralelas. El
> diseño (eventos, colas, patrones, pruebas) está en
> [`ENTREGA.md`](ENTREGA.md). Este documento solo dice **quién hace qué y
> en qué orden**.

La división es **por servicio**: cada línea es dueña de sus servicios de
punta a punta (código, Dockerfile y pruebas). Así, dos líneas nunca
modifican los mismos archivos. La única dependencia compartida es la
librería de eventos (`app/events/`), que se entrega primero (Paso 0).

---

## Paso 0 — Contrato compartido ✅ resuelto

Todo lo de esta sección está implementado en `app/events/`. **Cualquier
cambio al contrato se acuerda entre todos** y se hace solo en
`app/events/contract.py`.

### Broker

- Exchange **`investnow.events`**, de tipo `topic` y durable.
- Cada consumidor tiene tres colas durables:
  - `<cola>`: la principal.
  - `<cola>.retry`: espera de reintento. Cuando vence el TTL de 5 s, el
    mensaje vuelve a la principal.
  - `<cola>.dlq`: mensajes descartados después de 5 intentos o con error
    permanente.
- `rabbitmq_init` crea los exchanges, las colas y los permisos una vez
  que RabbitMQ está disponible. Cada servicio usa su propia credencial
  mediante `EVENTS_RABBITMQ_URL`.
- Los consumidores envían los reintentos y los mensajes descartados por
  `investnow.delivery`. Cada servicio solo puede escribir en sus propios
  destinos de reintento y DLQ.

### Eventos y routing keys

| Evento | Routing key | Publica | Payload |
|---|---|---|---|
| `OrderPlaced` | `order.placed` | Order | `order_id`, `user_id`, `asset_id`, `quantity`, `total_price` |
| `AssetValidated` | `order.asset_validated` | Catalog | `order_id`, `user_id`, `asset_id`, `quantity`, `total_price` |
| `OrderRejected` | `order.rejected` | Catalog | `order_id`, `user_id`, `reason` |
| `FundsDebited` | `wallet.funds_debited` | Wallet | `order_id`, `user_id`, `amount`, `new_balance` |
| `FundsRejected` | `wallet.funds_rejected` | Wallet | `order_id`, `user_id`, `reason` |

`AssetValidated` repite los datos de la orden para que Wallet no tenga que
consultar a Order.

### Colas y suscripciones

| Cola | Servicio | Bindings |
|---|---|---|
| `order.events` | Order | `order.rejected`, `wallet.funds_debited`, `wallet.funds_rejected` |
| `catalog.events` | Catalog | `order.placed` |
| `wallet.events` | Wallet | `order.asset_validated` |
| `audit.events` | Audit | `order.*`, `wallet.*` |

### Sobre del evento (JSON)

```json
{
  "event_id": "b3f1…",           // UUID; clave de deduplicación
  "event_type": "OrderPlaced",
  "occurred_at": "2026-11-04T22:30:00+00:00",
  "correlation_id": "42",        // order_id, para seguir la saga
  "payload": { "order_id": 42, "user_id": 1, "...": "..." }
}
```

`Event.new(...)` valida que el payload tenga exactamente los campos del
contrato. Si no los tiene, falla al publicar y no en el consumidor.

### API de la librería

```python
from app.events import (
    Event, contract,
    save_to_outbox, ensure_outbox_schema, start_outbox_relay,
    mark_processed, ensure_inbox_schema,
    start_consumer, PermanentError,
)

# Productor (dentro de la transacción de negocio):
event = Event.new(contract.ORDER_PLACED, correlation_id=order_id, payload={...})
save_to_outbox(cursor, event)          # misma transacción que el INSERT de la orden

# Al arrancar el servicio:
ensure_outbox_schema(DATABASE_URL)     # tabla outbox
start_outbox_relay(DATABASE_URL)       # hilo que publica lo pendiente

# Consumidor:
def handle(event: Event) -> None:      # si lanza excepción → retry; PermanentError → DLQ
    ...
start_consumer(contract.QUEUE_WALLET, handle)   # hilo con reconexión

# Deduplicación (dentro de la transacción del handler):
if not mark_processed(cursor, "wallet", event.event_id):
    return                              # ya procesado → no repetir efecto
```

### Reglas de uso

1. **Nunca publicar directo desde un endpoint**: siempre `save_to_outbox`
   en la misma transacción que el cambio de negocio. La excepción es
   Catalog, que no tiene base y publica con `publish_event` desde el
   handler (si se repite, el evento duplicado lo deduplica el consumidor).
2. **El handler es idempotente**: usa `mark_processed` o una clave de
   negocio única (Wallet: `transaction_id = "order:{order_id}"`).
3. **Un rechazo de negocio no es un error**: si el activo está inactivo o
   el saldo no alcanza, el handler publica el evento de rechazo y termina
   bien. Las excepciones son solo para fallas técnicas.
4. Ante datos inválidos que nunca van a funcionar, lanzar
   `PermanentError`: el mensaje va directo a la DLQ, sin reintentos.

---

## Línea 1 — Plataforma: infraestructura, Catalog y Audit

**Dueña de:** `app/events/`, `docker-compose.yaml`, `docker/postgres/init.sql`,
Catalog, Audit y `README.md`.

| # | Tarea |
|---|---|
| 1.1 | ✅ Librería `app/events/` (Paso 0) |
| 1.2 | ✅ `order_db`, `wallet_db` y `audit_db` en `init.sql`. Cada servicio tiene su propio `DATABASE_URL`. |
| 1.3 | Compose ya deja RabbitMQ y Postgres en la red interna, configura usuarios de RabbitMQ por servicio e incluye `audit_service`. Queda retirar `order_worker` al integrar las líneas 2 y 3. **Esta línea coordina los cambios de Compose.** |
| 1.4 | ✅ Catálogo consume `OrderPlaced` y publica `AssetValidated` u `OrderRejected`. |
| 1.5 | ✅ Audit consume `audit.events` y guarda cada evento en `audit_db`. |
| 1.6 | ✅ El `README.md` explica cómo levantar UT5 y probar Catálogo y Audit. |

## Línea 2 — Order Service y pruebas de disponibilidad

**Dueña de:** Order Service y `scripts/demo_ut5_*`.

| # | Tarea |
|---|---|
| 2.1 | **Línea base UT4**: correr las pruebas P1 a P3 (`ENTREGA.md` 5.3) sobre el commit `7373fdf`. No depende de nada: se puede hacer desde el día 1 |
| 2.2 | `POST /orders`: guardar la orden y `OrderPlaced` en el outbox en una misma transacción. Levantar el relay |
| 2.3 | Consumidor de `order.events` que actualiza el estado de la orden (deduplicando con `mark_processed`) |
| 2.4 | Eliminar `order_worker`, `order_processing.py` y `order_messaging.py` |
| 2.5 | `scripts/demo_ut5_disponibilidad.sh` con las pruebas P1 a P4 |

## Línea 3 — Wallet Service y SOAP

**Dueña de:** Wallet Service y el cliente SOAP del Gateway.

| # | Tarea |
|---|---|
| 3.1 | Migrar `GET /wallets` a SOAP: mensajes XML y contrato **WSDL**. No depende de la librería: se puede hacer desde el día 1 |
| 3.2 | El Gateway consume el servicio SOAP y lo expone al cliente |
| 3.3 | Script de demo SOAP: obtener el WSDL, hacer una llamada directa y otra a través del Gateway |
| 3.4 | Consumidor de `wallet.events`: debita de forma idempotente por `order_id` y guarda `FundsDebited` o `FundsRejected` en el outbox, en la misma transacción. Levantar el relay |

---

## Integración y cierre (todo el grupo)

1. **Integración:** compra de punta a punta (2.3 de `ENTREGA.md`) con
   `docker compose up --build` desde cero.
2. **Pruebas:** correr `demo_ut5_disponibilidad.sh` y compararlo con la
   línea base de 2.1.
3. **Resultados:** volcar la comparación en `ENTREGA.md`. Si la mejora no
   se verifica, explicar por qué.
4. **Uso de IA:** cada integrante aporta sus prompts. Entre todos se
   escribe el análisis crítico (una página como máximo).
5. **Ensayo:** cada integrante tiene que poder explicar las tres líneas.

## Correspondencia con la letra

| Requisito de la Parte 2 | Línea |
|---|---|
| 1. Implementación distribuida | 1.4, 1.5, 2.2, 2.3, 3.4 |
| 2. Migración de REST a SOAP con WSDL, consumido por otro componente | 3.1–3.3 |
| 3. Patrón para una falacia (Outbox, Retry + DLQ, idempotencia) | 1.1 (librería), usada en 2.2, 2.3 y 3.4 |
| 4. Validación del atributo de calidad, comparada con la arquitectura anterior | 2.1, 2.5 + integración |
| 5. Despliegue con Docker | 1.2, 1.3 + integración |
| Uso de IA | Integración |
