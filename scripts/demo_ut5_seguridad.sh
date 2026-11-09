#!/usr/bin/env bash
# Demo UT5: falacia 3 (la red es segura). Verifica las decisiones de
# ENTREGA.md §4.3:
#   1. Solo el Gateway publica un puerto al host (broker y bases no).
#   2. El Gatekeeper rechaza pedidos sin JWT válido.
#   3. El cliente no puede suplantar el contexto (X-User-Id / X-User-Roles).
#   4. El SOAP de Wallet no deja leer la billetera de otro usuario.
#   5. Credenciales por servicio en RabbitMQ: Catalog no puede publicar un
#      FundsDebited forjado; solo Wallet tiene ese permiso.
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

failures=0
check() {
  local description=$1 expected=$2 actual=$3
  if [ "$expected" = "$actual" ]; then
    printf '  OK    %s (%s)\n' "$description" "$actual"
  else
    printf '  FALLA %s: esperado %s, obtenido %s\n' "$description" "$expected" "$actual"
    failures=$((failures + 1))
  fi
}

if ! curl -fsS "$GATEWAY_URL/health" >/dev/null; then
  echo "El Gateway no responde. Levantá el stack con 'docker compose up --build -d --wait'." >&2
  exit 1
fi

login() {
  curl -fsS -X POST "$GATEWAY_URL/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$1\",\"password\":\"$2\"}" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}
token_scheme="Bea"
investor_auth="Authorization: $token_scheme""rer $(login inversor1 password123)"

code() {
  curl -s -o /dev/null -w '%{http_code}' "$@"
}

echo "--- 1. Puertos publicados al host ---"
exposed=$(compose ps --format json | python3 -c '
import json, sys
text = sys.stdin.read().strip()
rows = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines() if l]
print(",".join(sorted({r["Service"] for r in rows for p in (r.get("Publishers") or []) if p.get("PublishedPort")})))')
check "servicios con puerto publicado" "api" "$exposed"

echo "--- 2. Gatekeeper ---"
check "GET /orders sin token" 401 "$(code "$GATEWAY_URL/api/v1/orders")"
check "GET /orders con token inválido" 401 \
  "$(code "$GATEWAY_URL/api/v1/orders" -H "Authorization: $token_scheme""rer invalido")"
check "GET /catalog es público" 200 "$(code "$GATEWAY_URL/api/v1/catalog")"

echo "--- 3. Suplantación de headers de contexto ---"
spoofed_owners=$(curl -fsS "$GATEWAY_URL/api/v1/orders" -H "$investor_auth" -H "X-User-Id: 2" |
  python3 -c 'import json,sys; print(",".join(sorted({str(o["user_id"]) for o in json.load(sys.stdin)})) or "sin-ordenes-ajenas")')
case "$spoofed_owners" in
  1|sin-ordenes-ajenas) spoof_result=ignorado ;;
  *) spoof_result="ve-ordenes-de:$spoofed_owners" ;;
esac
check "X-User-Id: 2 enviado por inversor1" ignorado "$spoof_result"
check "X-User-Roles: admin enviado por inversor1" 403 \
  "$(code "$GATEWAY_URL/api/v1/orders/admin/2" -H "$investor_auth" -H "X-User-Roles: admin")"

echo "--- 4. SOAP: billetera de otro usuario ---"
check "inversor1 consulta GetWallet(user_id=2)" 403 \
  "$(code -X POST "$GATEWAY_URL/api/v1/wallets/soap/wallet" -H "$investor_auth" \
      -H "Content-Type: text/xml" \
      -d '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wal="http://investnow.com/wallet"><soapenv:Body><wal:GetWalletRequest><wal:user_id>2</wal:user_id></wal:GetWalletRequest></soapenv:Body></soapenv:Envelope>')"

echo "--- 5. Permisos por servicio en RabbitMQ ---"
forge() {
  compose exec -T "$1" python - <<'PY'
from app.events import Event, contract, publish_event
try:
    publish_event(Event.new(
        contract.FUNDS_DEBITED, correlation_id=0,
        payload={"order_id": 0, "user_id": 1, "amount": 1.0, "new_balance": 1.0},
    ))
    print("PUBLICADO")
except Exception as error:
    print("RECHAZADO" if "ACCESS_REFUSED" in str(error) else f"ERROR {error}")
PY
}
check "Catalog publica FundsDebited forjado" RECHAZADO "$(forge catalog_service)"
check "Order publica FundsDebited forjado" RECHAZADO "$(forge order_service)"

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures verificación(es) fallaron." >&2
  exit 1
fi
echo "Todas las verificaciones de la falacia 3 pasaron."
