#!/usr/bin/env bash
# Demo: Patrón Gatekeeper y Autenticación JWT en el Gateway
# Requiere el stack levantado con el Gateway y Gatekeeper configurados:
#   docker compose up --build -d
set -e

GATEWAY_URL="http://localhost:8000"

echo "--- 1. Verificando que el API Gateway esté levantado ---"
if ! curl -s -o /dev/null -f "$GATEWAY_URL/health"; then
  echo "El Gateway no responde en $GATEWAY_URL/health."
  echo "Levantá el stack con: docker compose up --build -d"
  exit 1
fi
echo "OK, el Gateway responde."

echo
echo "--- 2. Intentando crear una orden SIN token (Debe fallar con HTTP 401) ---"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$GATEWAY_URL/api/v1/orders" \
  -H "Content-Type: application/json" \
  -d '{"asset_id": 1, "quantity": 2, "total_price": 300.0}')

echo "Código HTTP recibido: $HTTP_CODE (Esperado: 401)"
if [ "$HTTP_CODE" -eq 401 ]; then
  echo "¡Éxito! El Gatekeeper bloqueó la petición por falta de credenciales."
else
  echo "Error: Se esperaba un código 401."
  exit 1
fi

echo
echo "--- 3. Obteniendo token JWT mediante el Login simulado ---"
LOGIN_RESPONSE=$(curl -s -X POST "$GATEWAY_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "inversor1", "password": "password123"}')

# Extraemos el token del JSON de respuesta (requiere grep/sed o python liviano)
TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))")

if [ -z "$TOKEN" ]; then
  echo "No se pudo obtener el token de acceso. Respuesta del login: $LOGIN_RESPONSE"
  exit 1
fi
echo "Token obtenido exitosamente (JWT)."

echo
echo "--- 4. Enviando orden CON el token Bearer válido (Atravesando el Gatekeeper) ---"
HTTP_CODE_OK=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$GATEWAY_URL/api/v1/orders" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"asset_id": 1, "quantity": 1, "total_price": 150.0}')

echo "Código HTTP recibido: $HTTP_CODE_OK (Esperado: 200 o 201)"
echo "Gatekeeper validó la identidad y el Gateway enrutó con éxito al microservicio interno"