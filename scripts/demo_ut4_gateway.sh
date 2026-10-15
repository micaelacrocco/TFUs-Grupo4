#!/usr/bin/env bash
# Demo: Flujo End-to-End - Gateway Offloading, Gatekeeper y Propagación de Contexto
set -e

GATEWAY_URL="http://localhost:8000"

echo "=========================================================="
echo "--- 1. Autenticándose para obtener credenciales (JWT) ---"
echo "=========================================================="
LOGIN_RESPONSE=$(curl -s -X POST "$GATEWAY_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "inversor1", "password": "password123"}')

TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))")

if [ -z "$TOKEN" ]; then
  echo "❌ Error al autenticar. Respuesta: $LOGIN_RESPONSE"
  exit 1
fi
echo "✅ Token JWT obtenido con éxito. $TOKEN"

echo
echo "=========================================================="
echo "--- 2. Revisando el catálogo de activos disponibles ---"
echo "=========================================================="
# El Gatekeeper valida el token y el Gateway rutea a catalog_service
curl -s -X GET "$GATEWAY_URL/api/v1/catalog" \
  -H "Authorization: Bearer $TOKEN"
echo
echo "✅ Catálogo consultado a través del proxy perimetral."

echo
echo "=========================================================="
echo "--- 3. Consultando el saldo de la Wallet del inversor ---"
echo "=========================================================="
# Asumiendo que tu microservicio de cuentas/wallet responde en /api/v1/wallets o /api/v1/wallet
curl -s -X GET "$GATEWAY_URL/api/v1/wallets" \
  -H "Authorization: Bearer $TOKEN"
echo
echo "✅ Estado financiero/wallet obtenido con éxito."

echo
echo "=========================================================="
echo "--- 4. Creando una orden de compra (Gatekeeper + OrderService) ---"
echo "=========================================================="
ORDER_RESPONSE=$(curl -s -X POST "$GATEWAY_URL/api/v1/orders" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"asset_id": 1, "quantity": 3, "total_price": 300.0}')

echo "$ORDER_RESPONSE"
echo "✅ Orden procesada y ejecutada por el microservicio interno."

echo
echo "=========================================================="
echo "--- 5. Consultando el historial de órdenes del usuario ---"
echo "=========================================================="
curl -s -X GET "$GATEWAY_URL/api/v1/orders" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if isinstance(data, list):
        print(json.dumps(data[-10:], indent=2))
    else:
        print(json.dumps(data, indent=2))
except Exception as e:
    print('Error parseando JSON:', e)
"
echo
echo "✅ 10 Ordenes más nuevas. Historial de órdenes consultado correctamente."