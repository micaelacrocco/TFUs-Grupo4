#!/usr/bin/env bash
# Demo UT4: Retry con backoff exponencial en el worker de órdenes.
# GET /assets (Catálogo) es idempotente, así que order_worker reintenta
# ante fallas transitorias antes de completar o fallar la orden (ver
# app/resilience/retry.py y app/order_worker.py). Se usa un
# endpoint de fault injection en catalog_service (POST
# /_debug/fail-next/{n}, solo para esta demo) para simular la falla de
# forma determinística, sin depender de los tiempos de reinicio de un
# contenedor real.
#
# Requiere el stack levantado y reconstruido con los cambios de UT4:
#   docker compose up --build -d
set -e

GATEWAY_URL="http://localhost:8000"

echo "--- Verificando que Catalog y Orders estén levantados ---"
if ! curl -s -o /dev/null -f "$GATEWAY_URL/api/v1/catalog/health"; then
  echo "catalog_service no responde en $GATEWAY_URL/api/v1/catalog/health."
  echo "Levantá (y reconstruí, si es la primera vez con los cambios de UT4) el stack:"
  echo "  docker compose up --build -d"
  exit 1
fi
if ! curl -s -o /dev/null -f "$GATEWAY_URL/api/v1/orders/health/dependencies"; then
  echo "order_service no responde en $GATEWAY_URL/api/v1/orders/health/dependencies."
  echo "Levantá (y reconstruí, si es la primera vez con los cambios de UT4) el stack:"
  echo "  docker compose up --build -d"
  exit 1
fi
echo "OK, ambos servicios responden."

# Pase lo que pase a partir de acá, dejar el contador de fallas simuladas en 0 al salir.
trap 'docker compose exec -T catalog_service python3 -c "import urllib.request; req = urllib.request.Request(\"http://localhost:8000/api/v1/_debug/fail-next/0\", data=b\"\"); urllib.request.urlopen(req)" > /dev/null 2>&1' EXIT

echo "--- Obteniendo token JWT mediante el Login simulado ---"
LOGIN_RESPONSE=$(curl -s -X POST "$GATEWAY_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "inversor1", "password": "password123"}')
  
TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))")

if [ -z "$TOKEN" ]; then
  echo "❌ Error al autenticar. Respuesta: $LOGIN_RESPONSE"
  exit 1
fi
echo "✅ Token JWT obtenido con éxito. $TOKEN"

submit_order() {
  local response body code
  response=$(curl -sS -w $'\n%{http_code}' -X POST "$GATEWAY_URL/api/v1/orders" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d '{"asset_id": 1, "quantity": 1, "total_price": 150.0}')
  code=${response##*$'\n'}
  body=${response%$'\n'*}
  if [ "$code" != "202" ]; then
    echo "La API esperaba responder 202; respondió HTTP $code: $body" >&2
    return 1
  fi
  printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["order_id"])'
}

wait_for_order_status() {
  local order_id=$1 expected=$2 current
  for _ in $(seq 1 40); do
    current=$(curl -sS "$GATEWAY_URL/api/v1/orders" \
    -H "Authorization: Bearer $TOKEN" | python3 -c \
      'import json,sys; order_id=int(sys.argv[1]); orders=json.load(sys.stdin); print(next((o["status"] for o in orders if int(o["order_id"]) == order_id), "UNKNOWN"))' \
      "$order_id")
    if [ "$current" = "$expected" ]; then
      echo "Orden $order_id: $current"
      return 0
    fi
    if [ "$current" = "FAILED" ] || [ "$current" = "COMPLETED" ]; then
      echo "Orden $order_id terminó en $current; se esperaba $expected." >&2
      return 1
    fi
    sleep 1
  done
  echo "Timeout esperando $expected para la orden $order_id (estado: $current)." >&2
  return 1
}

echo
echo "--- Caso 1: 2 fallas transitorias, dentro del límite de reintentos (MAX_ATTEMPTS=3) ---"
docker compose exec -T catalog_service python3 -c "import urllib.request; req = urllib.request.Request('http://localhost:8000/api/v1/_debug/fail-next/2', data=b\"\"); urllib.request.urlopen(req)" > /dev/null
echo "Encolando la orden (respuesta 202; el worker reintenta 2 veces):"
order_id=$(submit_order)
echo "Aceptada con order_id=$order_id"
wait_for_order_status "$order_id" COMPLETED

echo
echo "Logs de order_worker (incluyen los reintentos):"
docker compose logs order_worker --tail 30 | grep -E "retry|Order .*failed" || true

# se resetea el contador para simular el caso 2 sorteando Cache
docker compose exec -T catalog_service python3 -c "import urllib.request; req = urllib.request.Request('http://localhost:8000/api/v1/_debug/fail-next/5', data=b''); urllib.request.urlopen(req)" > /dev/null

echo
echo "--- Caso 2: más fallas que intentos disponibles (fail-next 5 > MAX_ATTEMPTS=3) ---"
docker compose exec -T catalog_service python3 -c "import urllib.request; req = urllib.request.Request('http://localhost:8000/api/v1/_debug/fail-next/5')" > /dev/null
echo "Encolando la orden (la API acepta y el worker la marca FAILED tras 3 intentos):"
order_id=$(submit_order)
echo "Aceptada con order_id=$order_id"
wait_for_order_status "$order_id" FAILED
echo "El retry está acotado: 3 intentos, no 5."

echo
echo "--- Restaurando saldo del usuario 1 (10000) tras el débito del Caso 1 ---"
docker compose exec -T postgres_db bash -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "UPDATE wallets SET balance = 10000.00 WHERE user_id = 1;"' \
  > /dev/null
echo "Listo."
