#!/usr/bin/env bash
# Demo UT5: saga coreografiada de compra por eventos.
#
# Muestra tres compras por el Gateway y cómo termina cada una:
#   A. activo válido con saldo        -> COMPLETED (OrderPlaced, AssetValidated, FundsDebited)
#   B. activo inactivo (TSLA)         -> FAILED    (OrderPlaced, OrderRejected)
#   C. saldo insuficiente             -> FAILED    (OrderPlaced, AssetValidated, FundsRejected)
# Para cada orden imprime la traza que guardó Audit y el saldo leído por SOAP.
# Al final reenvía un AssetValidated duplicado para mostrar que Wallet no
# debita dos veces (falacia 1: la red es confiable -> consumidor idempotente).
#
# Requiere Docker Compose, curl, Python 3 y el stack UT5 levantado.
set -euo pipefail

cd "$(dirname "$0")/.."

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
if [ -n "${COMPOSE_ENV_FILE:-}" ]; then
  compose_env_file=$COMPOSE_ENV_FILE
elif [ -f .env ]; then
  compose_env_file=.env
else
  compose_env_file=.env.example
fi

compose() {
  docker compose --env-file "$compose_env_file" "$@"
}

sql() {
  compose exec -T postgres_db sh -c 'psql -U "$POSTGRES_USER" -d "$1" -Atc "$2"' sh "$1" "$2"
}

if ! curl -fsS "$GATEWAY_URL/health" >/dev/null; then
  echo "El Gateway no responde. Levantá el stack con 'docker compose up --build -d --wait'." >&2
  exit 1
fi

token=$(curl -fsS -X POST "$GATEWAY_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"inversor1","password":"password123"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
token_scheme="Bea"
auth_header="Authorization: $token_scheme""rer $token"

balance() {
  curl -fsS -X POST "$GATEWAY_URL/api/v1/wallets/soap/wallet" \
    -H "$auth_header" -H "Content-Type: text/xml; charset=utf-8" \
    -d '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wal="http://investnow.com/wallet"><soapenv:Body><wal:GetWalletRequest><wal:user_id>1</wal:user_id></wal:GetWalletRequest></soapenv:Body></soapenv:Envelope>' |
    python3 -c '
import sys, xml.etree.ElementTree as ET
print(ET.fromstring(sys.stdin.read()).find(".//{http://investnow.com/wallet}balance").text)'
}

order_status() {
  curl -fsS "$GATEWAY_URL/api/v1/orders" -H "$auth_header" |
    python3 -c '
import json, sys
wanted = int(sys.argv[1])
print(next((o["status"] for o in json.load(sys.stdin) if o["order_id"] == wanted), "MISSING"))' "$1"
}

buy() {
  local title=$1 asset_id=$2 total=$3 expected=$4
  local response order_id status=PENDING
  echo
  echo "=== $title ==="
  echo "Saldo antes (SOAP): $(balance)"
  response=$(curl -fsS -X POST "$GATEWAY_URL/api/v1/orders" \
    -H "$auth_header" -H "Content-Type: application/json" \
    -d "{\"asset_id\":$asset_id,\"quantity\":1,\"total_price\":$total}")
  echo "POST /orders -> 202 $response"
  order_id=$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["order_id"])')

  for ((attempt=1; attempt<=60; attempt++)); do
    status=$(order_status "$order_id")
    [ "$status" != "PENDING" ] && break
    sleep 0.5
  done
  echo "GET /orders -> estado final: $status (esperado: $expected)"
  local reason
  reason=$(sql order_db "SELECT coalesce(error_message, '-') FROM orders WHERE order_id = $order_id")
  echo "Motivo: $reason"
  echo "Saldo después (SOAP): $(balance)"

  sleep 1
  echo "Traza en audit_db (correlation_id = $order_id):"
  sql audit_db "SELECT '  ' || to_char(occurred_at, 'HH24:MI:SS.MS') || '  ' || event_type
                FROM audit_events WHERE correlation_id = '$order_id' ORDER BY occurred_at"

  if [ "$status" != "$expected" ]; then
    echo "La orden $order_id terminó en $status y se esperaba $expected." >&2
    exit 1
  fi
  last_order_id=$order_id
}

buy "A. Compra válida (AAPL, 1.00)" 1 1.0 COMPLETED
completed_order_id=$last_order_id
buy "B. Activo inactivo (TSLA)" 3 1.0 FAILED
buy "C. Saldo insuficiente (50.000.000)" 1 50000000 FAILED

echo
echo "=== D. Reentrega duplicada de AssetValidated (orden $completed_order_id) ==="
before=$(balance)
event_json=$(sql audit_db "SELECT json_build_object('event_id', event_id, 'payload', payload)
                           FROM audit_events
                           WHERE correlation_id = '$completed_order_id'
                             AND event_type = 'AssetValidated'")
duplicate_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
compose exec -T catalog_service python - "$event_json" "$completed_order_id" "$duplicate_id" <<'PY'
import json, sys
from app.events import Event, contract, publish_event

original = json.loads(sys.argv[1])
for event_id in (original["event_id"], sys.argv[3]):
    publish_event(Event(
        event_type=contract.ASSET_VALIDATED,
        correlation_id=sys.argv[2],
        payload=original["payload"],
        event_id=event_id,
    ))
print("Publicado dos veces: mismo event_id y un event_id nuevo para la misma orden.")
PY
for ((attempt=1; attempt<=40; attempt++)); do
  processed=$(sql wallet_db "SELECT count(*) FROM processed_events WHERE event_id = '$duplicate_id'")
  [ "$processed" = "1" ] && break
  sleep 0.5
done
debits=$(sql wallet_db "SELECT count(*) FROM wallet_transactions WHERE transaction_id = 'order:$completed_order_id'")
after=$(balance)
echo "Débitos registrados para la orden: $debits | saldo antes: $before | saldo después: $after"
if [ "$debits" != "1" ] || [ "$before" != "$after" ]; then
  echo "Wallet debitó más de una vez la misma orden." >&2
  exit 1
fi
echo "Wallet procesó el duplicado sin volver a debitar (idempotencia por order_id)."
