#!/usr/bin/env bash
# Demo: Patrón Gateway Offloading y Propagación de Contexto Seguro
set -e

GATEWAY_URL="http://localhost:8000"

echo "--- 1. Autenticándose para obtener credenciales ---"
LOGIN_RESPONSE=$(curl -s -X POST "$GATEWAY_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "inversor1", "password": "password123"}')

TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))")

if [ -z "$TOKEN" ]; then
  echo "Error al autenticar."
  exit 1
fi

echo "--- 2. Creando una orden a través del Gateway (Offloading + Gatekeeper) ---"
curl -s -X POST "$GATEWAY_URL/api/v1/orders" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"asset_id": 2, "quantity": 5, "total_price": 500.0}'
echo
echo "Orden procesada por el microservicio interno gracias a la inyección de la cabecera X-User-Id."

echo
echo "--- 3. Consultando el historial de órdenes del usuario autenticado ---"
# El token le dice al Gateway quién es el usuario (user_id: 1)
curl -s -X GET "$GATEWAY_URL/api/v1/orders/1" \
  -H "Authorization: Bearer $TOKEN"
echo
echo "Consulta realizada correctamente a través del proxy perimetral."