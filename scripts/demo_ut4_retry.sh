#!/usr/bin/env bash
# Demo UT4: Retry con backoff exponencial en order_service.
# GET /assets (Catálogo) es idempotente, así que order_service reintenta
# ante fallas transitorias antes de fallar la orden (ver
# app/resilience/retry.py y app/routers/order_router.py). Se usa un
# endpoint de fault injection en catalog_service (POST
# /_debug/fail-next/{n}, solo para esta demo) para simular la falla de
# forma determinística, sin depender de los tiempos de reinicio de un
# contenedor real.
#
# Requiere el stack levantado y reconstruido con los cambios de UT4:
#   docker compose up --build -d
set -e

CATALOG=http://localhost:8002
ORDER=http://localhost:8003

echo "--- Verificando que el stack esté levantado ---"
if ! curl -s -o /dev/null -f "$CATALOG/health"; then
  echo "catalog_service no responde en $CATALOG/health."
  echo "Levantá (y reconstruí, si es la primera vez con los cambios de UT4) el stack:"
  echo "  docker compose up --build -d"
  exit 1
fi
if ! curl -s -o /dev/null -f "$ORDER/health"; then
  echo "order_service no responde en $ORDER/health."
  echo "Levantá (y reconstruí, si es la primera vez con los cambios de UT4) el stack:"
  echo "  docker compose up --build -d"
  exit 1
fi
echo "OK, ambos servicios responden."

# Pase lo que pase a partir de acá, dejar el contador de fallas simuladas en 0 al salir.
trap 'curl -s -X POST "$CATALOG/api/v1/_debug/fail-next/0" > /dev/null' EXIT

echo
echo "--- Caso 1: 2 fallas transitorias, dentro del límite de reintentos (MAX_ATTEMPTS=3) ---"
curl -s -X POST "$CATALOG/api/v1/_debug/fail-next/2"; echo
echo "Enviando la orden (debería completarse igual, reintentando 2 veces):"
curl -s -X POST "$ORDER/api/v1/orders" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "asset_id": 1, "quantity": 1, "total_price": 150.0}'; echo

echo
echo "Logs de order_service (se ven los 2 reintentos antes de la respuesta exitosa):"
docker compose logs order_service --tail 20 | grep -E "retry|POST /api/v1/orders" || true

echo
echo "--- Caso 2: más fallas que intentos disponibles (fail-next 5 > MAX_ATTEMPTS=3) ---"
curl -s -X POST "$CATALOG/api/v1/_debug/fail-next/5"; echo
echo "Enviando la orden (debería fallar tras agotar los 3 intentos):"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST "$ORDER/api/v1/orders" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "asset_id": 1, "quantity": 1, "total_price": 150.0}'
echo "El retry está acotado: no reintenta indefinidamente (3 intentos agotados, no 5)."

echo
echo "--- Restaurando saldo del usuario 1 (10000) tras el débito del Caso 1 ---"
docker compose exec -T postgres_db bash -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "UPDATE wallets SET balance = 10000.00 WHERE user_id = 1;"' \
  > /dev/null
echo "Listo."
