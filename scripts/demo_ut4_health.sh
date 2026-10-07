#!/usr/bin/env bash
# Demo UT4: Health Endpoint Monitoring.
# Cada servicio expone GET /health (liveness; en wallet_service además
# valida la conexión a Postgres). order_service, que depende de Catálogo y
# de Wallet, expone además GET /health/dependencies, que agrega el estado
# de cada una. Docker Compose usa estos mismos endpoints como `healthcheck`
# (ver docker-compose.yaml) para no levantar order_service/api hasta que
# sus dependencias estén realmente sanas, no solo "iniciadas".
#
# Requiere el stack levantado y reconstruido con los cambios de UT4:
#   docker compose up --build -d
set -e

fail_hint="(sin respuesta -- ¿está el stack levantado y reconstruido? 'docker compose up --build -d')"

echo "--- Verificando que el stack esté levantado ---"
if ! curl -s -o /dev/null -f "http://localhost:8000/health"; then
  echo "api no responde en http://localhost:8000/health."
  echo "Levantá (y reconstruí, si es la primera vez con los cambios de UT4) el stack:"
  echo "  docker compose up --build -d"
  exit 1
fi
echo "OK, el stack responde."
echo

echo "--- Estado de salud de los 4 servicios ---"
for entry in "api:8000" "catalog:8002" "wallet:8001" "order:8003"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  echo "[$name] GET /health"
  curl -s "http://localhost:${port}/health" || echo "$fail_hint"
  echo
done

echo
echo "--- Estado agregado de las dependencias de order_service (Catálogo + Wallet) ---"
curl -s http://localhost:8003/health/dependencies || echo "$fail_hint"
echo

echo
echo "--- Matando wallet_service para simular una caída ---"
docker compose kill wallet_service
sleep 1

echo "GET /health directo a wallet_service (debería fallar: conexión rechazada):"
curl -s http://localhost:8001/health || echo "(falló como se esperaba, el contenedor está caído)"

echo
echo "GET /health/dependencies en order_service (debería reportar wallet caído, HTTP 503):"
curl -s -w "\nHTTP %{http_code}\n" http://localhost:8003/health/dependencies || echo "$fail_hint"

echo
echo "--- docker compose ps (columna STATUS de wallet_service: exited/unhealthy) ---"
docker compose ps

echo
echo "--- Levantando wallet_service de nuevo ---"
docker compose up -d wallet_service

echo "Esperando a que el healthcheck lo marque como healthy (máx. 30s)..."
status="unknown"
for i in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q wallet_service)" 2>/dev/null || echo "unknown")
  [ "$status" = "healthy" ] && break
  sleep 1
done
echo "wallet_service: $status"

echo
echo "GET /health/dependencies en order_service (debería volver a estar OK):"
curl -s http://localhost:8003/health/dependencies || echo "$fail_hint"
echo
