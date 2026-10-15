#!/usr/bin/env bash
# Demo UT4: Queue-Based Load Leveling en order_service.
set -uo pipefail

BASE_URL="http://localhost:8000"
REQUESTS=200
CONCURRENCY=200
USER_ID=1
ASSET_ID=1
PRICE=0.01
POLL_INTERVAL=1
POLL_TIMEOUT=300
REQUEST_TIMEOUT=10

echo "--- Obteniendo token JWT mediante el Login simulado ---"
LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "inversor1", "password": "password123"}')
  TOKEN=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))")


usage() {
  echo "Uso: $0 [--base-url URL] [--requests N] [--concurrency N] [--user-id N] [--asset-id N] [--price IMPORTE] [--poll-interval SEGUNDOS] [--poll-timeout SEGUNDOS] [--request-timeout SEGUNDOS]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --base-url|--requests|--concurrency|--user-id|--asset-id|--price|--poll-interval|--poll-timeout|--request-timeout)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      option=$1
      value=$2
      case "$option" in
        --base-url) BASE_URL=$value ;;
        --requests) REQUESTS=$value ;;
        --concurrency) CONCURRENCY=$value ;;
        --user-id) USER_ID=$value ;;
        --asset-id) ASSET_ID=$value ;;
        --price) PRICE=$value ;;
        --poll-interval) POLL_INTERVAL=$value ;;
        --poll-timeout) POLL_TIMEOUT=$value ;;
        --request-timeout) REQUEST_TIMEOUT=$value ;;
      esac
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

BASE_URL=${BASE_URL%/}
for name in REQUESTS CONCURRENCY USER_ID ASSET_ID; do
  value=${!name}
  if ! [[ "$value" =~ ^[0-9]+$ ]] || [ "$value" -le 0 ]; then
    echo "$name debe ser un entero mayor que cero." >&2
    exit 2
  fi
done
for name in PRICE POLL_INTERVAL POLL_TIMEOUT REQUEST_TIMEOUT; do
  value=${!name}
  if ! [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! awk "BEGIN {exit !($value > 0)}"; then
    echo "$name debe ser mayor que cero." >&2
    exit 2
  fi
done

tmp_dir=$(mktemp -d) || { echo "No se pudo crear un directorio temporal." >&2; exit 1; }
trap 'rm -rf "$tmp_dir"' EXIT

monotonic_time() {
  python3 -c 'import time; print(time.monotonic())'
}

submit_one() {
  local request_number=$1 payload metadata="" curl_status
  payload=$(printf '{"asset_id":%s,"quantity":1,"total_price":%s}' "$ASSET_ID" "$PRICE")
  if metadata=$(curl -sS --max-time "$REQUEST_TIMEOUT" \
    -o "$tmp_dir/body.$request_number" \
    -w '%{http_code} %{time_total}' \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-User-Id: $USER_ID" \
    -d "$payload" "$BASE_URL/api/v1/orders" \
    2>"$tmp_dir/curl_error.$request_number"); then
    curl_status=0
  else
    curl_status=$?
  fi
  printf '%s\t%s\n' "$curl_status" "$metadata" > "$tmp_dir/result.$request_number"
}

echo "Enviando $REQUESTS órdenes a $BASE_URL (concurrencia=$CONCURRENCY, usuario=$USER_ID, activo=$ASSET_ID, importe=\$$PRICE c/u)."
echo "Aviso: el test deja órdenes persistidas y debita \$$(
  awk -v count="$REQUESTS" -v price="$PRICE" 'BEGIN {printf "%.2f", count * price}'
) en total si todas se completan."

submit_started_at=$(monotonic_time)
request_number=1
while [ "$request_number" -le "$REQUESTS" ]; do
  batch_end=$((request_number + CONCURRENCY - 1))
  [ "$batch_end" -le "$REQUESTS" ] || batch_end=$REQUESTS
  for ((i = request_number; i <= batch_end; i++)); do
    submit_one "$i" &
  done
  wait
  request_number=$((batch_end + 1))
done
submit_finished_at=$(monotonic_time)

python3 - "$tmp_dir" "$REQUESTS" <<'PY'
import json
import math
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
count = int(sys.argv[2])
order_ids = set()
latencies = []
failures = []

for request_number in range(1, count + 1):
    result_path = root / f"result.{request_number}"
    body_path = root / f"body.{request_number}"
    try:
        curl_status, metadata = result_path.read_text().rstrip("\n").split("\t", 1)
        http_code, elapsed = metadata.split()
        if int(curl_status) != 0:
            raise ValueError((root / f"curl_error.{request_number}").read_text().strip())
        if http_code != "202":
            raise ValueError(f"expected HTTP 202, got HTTP {http_code}: {body_path.read_text()}")
        body = json.loads(body_path.read_text())
        order_id = int(body["order_id"])
        if body["status"] != "PENDING":
            raise ValueError(f"expected PENDING, got {body['status']!r}")
        if order_id in order_ids:
            raise ValueError(f"duplicate order_id {order_id}")
        order_ids.add(order_id)
        latencies.append(float(elapsed))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        failures.append(f"request {request_number}: {exc}")

(root / "order_ids.txt").write_text("".join(f"{order_id}\n" for order_id in sorted(order_ids)))
(root / "latencies.txt").write_text("".join(f"{latency:.9f}\n" for latency in latencies))
(root / "submission_errors.txt").write_text("".join(f"{error}\n" for error in failures))

def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[math.ceil((len(ordered) - 1) * fraction)]

print("\nEncolado")
print(f"  Aceptadas: {len(order_ids)}/{count}")
if latencies:
    milliseconds = [latency * 1000 for latency in latencies]
    print(f"  Latencia API promedio: {sum(milliseconds) / len(milliseconds):.2f} ms")
    print(f"  Latencia API p50: {percentile(milliseconds, 0.50):.2f} ms")
    print(f"  Latencia API p95: {percentile(milliseconds, 0.95):.2f} ms")
if failures:
    print(f"  Errores de envío: {len(failures)}")
    for error in failures[:10]:
        print(f"    - {error}")
PY

accepted=$(wc -l < "$tmp_dir/order_ids.txt" | tr -d ' ')
if [ "$accepted" -eq 0 ]; then
  echo "No se aceptó ninguna orden."
  exit 1
fi
submit_elapsed=$(awk -v start="$submit_started_at" -v end="$submit_finished_at" 'BEGIN {printf "%.2f", end - start}')
echo "  Tiempo de envío concurrente: ${submit_elapsed} s"

drain_started_at=$submit_started_at
read_statuses() {
  if ! curl -fsS --max-time "$REQUEST_TIMEOUT" \
    "$BASE_URL/api/v1/orders/$USER_ID" -o "$tmp_dir/history.json" -H "Authorization: Bearer $TOKEN"; then
    echo "No se pudo consultar GET /api/v1/orders/$USER_ID." >&2
    return 1
  fi
  python3 - "$tmp_dir/order_ids.txt" "$tmp_dir/history.json" "$tmp_dir/status.txt" <<'PY'
import json
import pathlib
import sys

ids_path, history_path, status_path = map(pathlib.Path, sys.argv[1:])
order_ids = {int(line) for line in ids_path.read_text().splitlines()}
orders = json.loads(history_path.read_text())
if not isinstance(orders, list):
    raise SystemExit("El endpoint de historial no devolvió un array JSON.")
statuses = {
    int(order["order_id"]): str(order["status"])
    for order in orders
    if int(order["order_id"]) in order_ids
}
completed = sum(status == "COMPLETED" for status in statuses.values())
failed_ids = sorted(order_id for order_id, status in statuses.items() if status == "FAILED")
pending = len(order_ids) - completed - len(failed_ids)
status_path.write_text(f"{completed} {len(failed_ids)} {pending}\n")
(status_path.with_suffix(".failed")).write_text("".join(f"{order_id}\n" for order_id in failed_ids))
PY
  read -r completed failed pending < "$tmp_dir/status.txt"
}

echo
echo "Drenaje de cola (estado consultado por GET /api/v1/orders/$USER_ID)"
if ! read_statuses; then
  exit 1
fi
now=$(monotonic_time)
previous_sample_at=$now
previous_completed=$completed
elapsed=$(awk -v start="$drain_started_at" -v end="$now" 'BEGIN {printf "%.2f", end - start}')
echo "  +${elapsed}s  COMPLETED $completed/$accepted  FAILED $failed  pendientes $pending  ritmo esperando siguiente muestra"
if [ "$failed" -gt 0 ]; then
  echo "Ordenes FAILED (primeras 10): $(head -n 10 "$tmp_dir/status.failed" | tr '\n' ' ')"
  exit 1
fi

deadline=$(awk -v start="$drain_started_at" -v timeout="$POLL_TIMEOUT" 'BEGIN {printf "%.9f", start + timeout}')
while [ "$completed" -lt "$accepted" ]; do
  now=$(monotonic_time)
  if ! awk -v now="$now" -v deadline="$deadline" 'BEGIN {exit !(now < deadline)}'; then
    break
  fi
  sleep "$POLL_INTERVAL"
  if ! read_statuses; then
    exit 1
  fi
  now=$(monotonic_time)
  sample_seconds=$(awk -v start="$previous_sample_at" -v end="$now" 'BEGIN {d=end-start; if (d < 0.001) d=0.001; printf "%.9f", d}')
  rate=$(awk -v current="$completed" -v previous="$previous_completed" -v seconds="$sample_seconds" 'BEGIN {printf "%.2f", (current-previous)/seconds}')
  elapsed=$(awk -v start="$drain_started_at" -v end="$now" 'BEGIN {printf "%.2f", end-start}')
  echo "  +${elapsed}s  COMPLETED $completed/$accepted  FAILED $failed  pendientes $pending  ritmo $rate órdenes/s"
  if [ "$failed" -gt 0 ]; then
    echo "Órdenes FAILED (primeras 10): $(head -n 10 "$tmp_dir/status.failed" | tr '\n' ' ')"
    exit 1
  fi
  previous_completed=$completed
  previous_sample_at=$now
done

if [ "$completed" -ne "$accepted" ]; then
  echo "Timeout: no se completaron todas las órdenes en ${POLL_TIMEOUT}s."
  exit 1
fi

drain_finished_at=$(monotonic_time)
drain_elapsed=$(awk -v start="$drain_started_at" -v end="$drain_finished_at" 'BEGIN {printf "%.9f", end - start}')
completed_rate=$(awk -v count="$accepted" -v seconds="$drain_elapsed" 'BEGIN {if (seconds > 0) printf "%.2f", count / seconds; else print "0.00"}')
accepted_latency_sum=$(awk '{sum += $1} END {print sum + 0}' "$tmp_dir/latencies.txt")
queue_amplification=$(awk -v drain="$drain_elapsed" -v accepted="$accepted_latency_sum" 'BEGIN {if (accepted > 0) printf "%.1f", drain / accepted; else print "0.0"}')

echo
echo "Resultado"
echo "  COMPLETED: $completed/$accepted"
echo "  Tiempo hasta completar el drenaje: $(awk -v seconds="$drain_elapsed" 'BEGIN {printf "%.2f", seconds}') s"
echo "  Ritmo medio de drenaje observado: $completed_rate órdenes/s"
echo "  Comparación ilustrativa: las respuestas 202 se midieron al encolar; el trabajo total tardó $(awk -v seconds="$drain_elapsed" 'BEGIN {printf "%.2f", seconds}') s (${queue_amplification}x la suma de las latencias de aceptación)."
echo "  No hay endpoint sincrónico en esta versión: la comparación no es un benchmark A/B; en un diseño sincrónico cada respuesta esperaría su procesamiento, mientras aquí la API confirma la aceptación y el worker drena después."

[ ! -s "$tmp_dir/submission_errors.txt" ]
