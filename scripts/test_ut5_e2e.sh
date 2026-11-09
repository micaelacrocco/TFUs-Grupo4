#!/usr/bin/env bash
# Corre los tests de UT5: unitarios (dentro del contenedor de Order, que ya
# tiene las dependencias) y end-to-end (desde el host contra el stack).
#
# Requiere Docker Compose, Python 3 y el stack UT5 levantado:
#   docker compose up --build -d --wait --remove-orphans
#
# Opciones por variable de entorno:
#   E2E_SKIP_SLOW=1     omite la prueba de caída de Wallet (~15 s).
#   COMPOSE_ENV_FILE=…  archivo de variables para docker compose.
#   GATEWAY_URL=…       URL del Gateway (default http://localhost:8000).
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
export COMPOSE_ENV_FILE=$compose_env_file GATEWAY_URL

if ! curl -fsS "$GATEWAY_URL/health" >/dev/null; then
  echo "El Gateway no responde. Levantá el stack con 'docker compose up --build -d --wait'." >&2
  exit 1
fi

echo "=== Tests unitarios (dentro de order_service) ==="
docker compose --env-file "$compose_env_file" run --rm --no-deps -T \
  -v "$PWD/tests:/app/tests:ro" order_service \
  python -m unittest discover -s tests -v

echo
echo "=== Tests end-to-end (host -> Gateway -> servicios) ==="
python3 -m unittest discover -s tests/e2e -t tests/e2e -v
