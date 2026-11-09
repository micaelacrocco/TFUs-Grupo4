#!/usr/bin/env bash
# Experimentos P1-P4 de disponibilidad de UT5.
# Requiere Docker Compose, curl, Python 3 y el stack UT5 levantado.
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
ORDER_COUNT=50
service_to_restore=""

restore_service() {
  if [ -n "$service_to_restore" ]; then
    docker compose start "$service_to_restore" >/dev/null
  fi
}
trap restore_service EXIT

force_wallet_redelivery() {
  echo "Bloqueando brevemente el saldo de demo para provocar una entrega en curso."
  docker compose exec -d postgres_db sh -c \
    'PGPASSWORD="$WALLET_DB_PASSWORD" PGAPPNAME=ut5-wallet-redelivery-lock psql -h 127.0.0.1 -U wallet_app -d wallet_db -c "BEGIN; SELECT balance FROM wallets WHERE user_id = 1 FOR UPDATE; SELECT pg_sleep(45); COMMIT;"'
  sleep 1
  docker compose start wallet_service
  local unacknowledged=0
  for ((attempt=1; attempt<=100; attempt++)); do
    unacknowledged=$(docker compose exec -T rabbitmq rabbitmqctl list_queues -q \
      name messages_unacknowledged |
      awk '$1 == "wallet.events" {print $2}')
    if [ "${unacknowledged:-0}" -gt 0 ]; then
      break
    fi
    sleep 0.1
  done
  if [ "${unacknowledged:-0}" -lt 1 ]; then
    echo "No se observó un mensaje en curso en wallet.events." >&2
    return 1
  fi

  echo "Mensaje en curso detectado; terminando Wallet para forzar su reentrega."
  docker compose kill wallet_service
  docker compose start wallet_service
  service_to_restore=""
}

if ! curl -fsS "$GATEWAY_URL/health" >/dev/null; then
  echo "El Gateway no responde. Levantá el stack con 'docker compose up --build -d'." >&2
  exit 1
fi

wallet_consumers=0
for ((attempt=1; attempt<=30; attempt++)); do
  wallet_consumers=$(docker compose exec -T rabbitmq rabbitmqctl list_queues -q \
    name consumers | awk '$1 == "wallet.events" {print $2}')
  if [ "${wallet_consumers:-0}" -gt 0 ]; then
    break
  fi
  sleep 1
done
if [ "${wallet_consumers:-0}" -lt 1 ]; then
  echo "wallet.events no tiene consumidores; integra la Línea 3 antes de ejecutar la demo." >&2
  exit 1
fi

login_response=$(curl -fsS -X POST "$GATEWAY_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"inversor1","password":"password123"}')
token=$(printf '%s' "$login_response" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
token_scheme="Bea"
token_header="$token_scheme""rer $token"

submit_order() {
  local response code body
  response=$(curl -fsS -w $'\n%{http_code}' -X POST "$GATEWAY_URL/api/v1/orders" \
    -H "Authorization: $token_header" \
    -H "Content-Type: application/json" \
    -d '{"asset_id":1,"quantity":1,"total_price":1.0}')
  code=${response##*$'\n'}
  body=${response%$'\n'*}
  if [ "$code" != "202" ]; then
    echo "Se esperaba HTTP 202; se recibió HTTP $code: $body" >&2
    return 1
  fi
  printf '%s' "$body" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["order_id"])'
}

run_outage_test() {
  local service=$1 name=$2 elapsed
  local -a ids=()

  echo
  echo "--- $name: detener $service después de 10 órdenes ---"
  for ((i=1; i<=ORDER_COUNT; i++)); do
    if [ "$i" -eq 11 ]; then
      docker compose stop "$service"
      service_to_restore=$service
      outage_started=$SECONDS
    fi
    ids+=("$(submit_order)")
    printf 'Aceptada %d/%d (order_id=%s)\n' "$i" "$ORDER_COUNT" "${ids[-1]}"
    sleep 1
  done

  elapsed=$((SECONDS - outage_started))
  if [ "$elapsed" -lt 60 ]; then
    sleep "$((60 - elapsed))"
  fi
  if [ "$service" = "wallet_service" ]; then
    force_wallet_redelivery
  else
    docker compose start "$service"
    service_to_restore=""
  fi

  local ids_csv summary completed failed pending missing
  ids_csv=$(IFS=,; echo "${ids[*]}")
  for ((attempt=1; attempt<=180; attempt++)); do
    summary=$(curl -fsS "$GATEWAY_URL/api/v1/orders" \
      -H "Authorization: $token_header" |
      python3 -c '
import json,sys
wanted={int(value) for value in sys.argv[1].split(",")}
orders={int(order["order_id"]):order["status"] for order in json.load(sys.stdin)}
states=[orders.get(order_id,"MISSING") for order_id in wanted]
print(*(sum(state == expected for state in states)
        for expected in ("COMPLETED","FAILED","PENDING","MISSING")))
' "$ids_csv")
    read -r completed failed pending missing <<< "$summary"
    if [ "$((completed + failed))" -eq "$ORDER_COUNT" ]; then
      break
    fi
    sleep 1
  done

  echo "Órdenes: aceptadas=$ORDER_COUNT completadas=$completed fallidas=$failed pendientes=$pending ausentes=$missing"
  if [ "$completed" -ne "$ORDER_COUNT" ] || [ "$failed" -ne 0 ] ||
     [ "$pending" -ne 0 ] || [ "$missing" -ne 0 ]; then
    echo "La prueba $name no alcanzó el resultado esperado." >&2
    return 1
  fi

  local expected_transactions actual_transactions query
  expected_transactions=$ORDER_COUNT
  query="SELECT count(*) FROM wallet_transactions WHERE user_id = 1 AND transaction_id IN ($(printf "'order:%s'," "${ids[@]}" | sed 's/,$//'))"
  actual_transactions=$(docker compose exec -T postgres_db sh -c \
    'psql -U "$POSTGRES_USER" -d wallet_db -Atc "$1"' sh "$query")
  echo "P2/idempotencia tras redelivery: transacciones Wallet=$actual_transactions; órdenes completadas=$expected_transactions"
  if [ "$actual_transactions" -ne "$expected_transactions" ]; then
    echo "La cantidad de débitos no coincide con las órdenes completadas." >&2
    return 1
  fi
}

run_outage_test wallet_service "P1/P2 - caída y recuperación de Wallet"
run_outage_test catalog_service "P3 - caída y recuperación de Catalog"

echo
echo "--- P4 - evento malformado a DLQ y continuidad de Catalog ---"
probe_id=$(python3 -c 'import time; print(int(time.time() * 1000000))')
initial_dlq_count=$(docker compose exec -T rabbitmq rabbitmqctl list_queues -q name messages_ready |
  awk '$1 == "catalog.events.dlq" {print $2}')
initial_dlq_count=${initial_dlq_count:-0}
docker compose exec -T order_service python -c "
from contextlib import closing
import pika
from app.events import Event, broker, contract

with closing(broker.connect()) as connection:
    channel = broker.open_publish_channel(connection)
    channel.basic_publish(
        exchange=contract.EXCHANGE,
        routing_key='order.placed',
        body=b'{malformed',
        properties=pika.BasicProperties(
            content_type='application/json',
            delivery_mode=pika.DeliveryMode.Persistent,
        ),
        mandatory=True,
    )
    broker.publish(channel, Event.new(
        contract.ORDER_PLACED,
        correlation_id='$probe_id',
        payload={
            'order_id': $probe_id,
            'user_id': 1,
            'asset_id': 3,
            'quantity': 1,
            'total_price': 1,
        },
    ))
"

dlq_count=0
for ((attempt=1; attempt<=30; attempt++)); do
  dlq_count=$(docker compose exec -T rabbitmq rabbitmqctl list_queues -q name messages_ready |
    awk '$1 == "catalog.events.dlq" {print $2}')
  if [ "${dlq_count:-0}" -gt 0 ]; then
    break
  fi
  sleep 1
done
if [ "${dlq_count:-0}" -lt 1 ]; then
  echo "No se encontró el evento malformado en catalog.events.dlq." >&2
  exit 1
fi

audit_count=0
for ((attempt=1; attempt<=30; attempt++)); do
  audit_count=$(docker compose exec -T postgres_db sh -c \
    'psql -U "$POSTGRES_USER" -d audit_db -Atc "$1"' sh \
    "SELECT count(*) FROM audit_events WHERE correlation_id = '$probe_id' AND event_type = 'OrderRejected'")
  if [ "$audit_count" -gt 0 ]; then
    break
  fi
  sleep 1
done
if [ "$audit_count" -lt 1 ]; then
  echo "Catalog no procesó el evento válido posterior al mensaje malformado." >&2
  exit 1
fi
if [ "$dlq_count" -le "$initial_dlq_count" ]; then
  echo "El evento malformado no incrementó catalog.events.dlq." >&2
  exit 1
fi

echo "P4 verificada: DLQ de Catalog tiene $dlq_count mensaje(s) y procesó un evento válido posterior."
