#!/usr/bin/env bash
# Demo UT4: Cache-Aside en catalog_service.
set -uo pipefail

GATEWAY_URL="http://localhost:8000"

URL="http://localhost:8000/api/v1/catalog"
REQUESTS=100
TIMEOUT=5

echo "--- Obteniendo token JWT mediante el Login simulado ---"
LOGIN_RESPONSE=$(curl -s -X POST "$GATEWAY_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "inversor1", "password": "password123"}')
  TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))")

usage() {
  echo "Uso: $0 [--url URL] [--requests N] [--timeout SEGUNDOS]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --url)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      URL=$2
      shift 2
      ;;
    --requests)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      REQUESTS=$2
      shift 2
      ;;
    --timeout)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      TIMEOUT=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Opción desconocida: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "$REQUESTS" =~ ^[0-9]+$ ]] || [ "$REQUESTS" -le 0 ]; then
  echo "--requests debe ser un entero mayor que cero." >&2
  exit 2
fi
if ! [[ "$TIMEOUT" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! awk "BEGIN {exit !($TIMEOUT > 0)}"; then
  echo "--timeout debe ser mayor que cero." >&2
  exit 2
fi

tmp_dir=$(mktemp -d) || { echo "No se pudo crear un directorio temporal." >&2; exit 1; }
trap 'rm -rf "$tmp_dir"' EXIT
hits_file="$tmp_dir/hits"
misses_file="$tmp_dir/misses"
: > "$hits_file"
: > "$misses_file"
failures=0

echo "Midiendo $REQUESTS lecturas de $URL"
for ((request_number = 1; request_number <= REQUESTS; request_number++)); do
  metadata=""
  if metadata=$(curl -sS --max-time "$TIMEOUT" \
    -D "$tmp_dir/headers" -o /dev/null \
    -H "Authorization: Bearer $TOKEN" \
    -w '%{http_code} %{time_total}' "$URL" 2>"$tmp_dir/curl_error"); then
    read -r http_code elapsed_seconds <<< "$metadata"
    cache_status=$(awk '
      tolower($1) == "x-cache:" {
        gsub("\r", "", $2)
        value = toupper($2)
      }
      END { print value }
    ' "$tmp_dir/headers")
    elapsed_ms=$(awk -v seconds="${elapsed_seconds:-0}" 'BEGIN {printf "%.2f", seconds * 1000}')

    if [ "$http_code" = "200" ] && { [ "$cache_status" = "HIT" ] || [ "$cache_status" = "MISS" ]; }; then
      if [ "$cache_status" = "HIT" ]; then
        printf '%s\n' "$elapsed_ms" >> "$hits_file"
      else
        printf '%s\n' "$elapsed_ms" >> "$misses_file"
      fi
      printf '%03d/%d: %-4s %8s ms\n' "$request_number" "$REQUESTS" "$cache_status" "$elapsed_ms"
    else
      failures=$((failures + 1))
      echo "$(printf '%03d' "$request_number")/$REQUESTS: ERROR HTTP ${http_code:-unknown}, X-Cache=${cache_status:-missing}"
    fi
  else
    failures=$((failures + 1))
    echo "$(printf '%03d' "$request_number")/$REQUESTS: ERROR $(cat "$tmp_dir/curl_error")"
  fi
done

hits=$(wc -l < "$hits_file" | tr -d ' ')
misses=$(wc -l < "$misses_file" | tr -d ' ')
average() {
  awk '{sum += $1; count++} END {
    if (count) printf "%.2f ms (%d muestras)", sum / count, count
    else printf "n/a (0 muestras)"
  }' "$1"
}

echo
echo "Resumen"
echo "  HIT: $hits"
echo "  MISS: $misses"
echo "  Errores: $failures"
echo "  Latencia promedio MISS: $(average "$misses_file")"
echo "  Latencia promedio HIT: $(average "$hits_file")"

[ "$failures" -eq 0 ]
