"""Tests end-to-end de UT5 contra el stack de Docker Compose levantado.

Cubren el recorrido real de punta a punta: Gateway -> Order (outbox) ->
RabbitMQ -> Catalog -> Wallet -> Order, más Audit, SOAP, seguridad y
resiliencia (cola durable, idempotencia y DLQ).

Requisitos: Docker Compose, Python 3 (solo librería estándar) y el stack
levantado. Ejecutar desde la raíz del repo:

    bash scripts/test_ut5_e2e.sh                 # suite completa
    E2E_SKIP_SLOW=1 bash scripts/test_ut5_e2e.sh # sin la prueba de caída

Variables opcionales: GATEWAY_URL (default http://localhost:8000),
COMPOSE_ENV_FILE (default .env o .env.example), E2E_SKIP_SLOW=1.

Los tests crean órdenes reales y consumen saldo de la billetera de demo
(usuario 1), igual que los scripts de demo.
"""

import json
import os
import subprocess
import time
import unittest
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000").rstrip("/")
SKIP_SLOW = os.getenv("E2E_SKIP_SLOW") == "1"

INVESTOR = {"username": "inversor1", "password": "password123", "user_id": 1}
ADMIN = {"username": "admin1", "password": "adminpassword", "user_id": 2}

SOAP_NS = {
    "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
    "wal": "http://investnow.com/wallet",
}
SAGA_TIMEOUT_SECONDS = 30


def _env_file() -> str:
    if os.getenv("COMPOSE_ENV_FILE"):
        return os.environ["COMPOSE_ENV_FILE"]
    return ".env" if (REPO_ROOT / ".env").exists() else ".env.example"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def compose(*args: str, input_text: str | None = None, check: bool = True,
            timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "--env-file", _env_file(), *args],
        cwd=REPO_ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def http(method: str, path: str, *, token: str | None = None, json_body=None,
         data: bytes | None = None, headers: dict | None = None):
    """Devuelve (status, headers, body_text). No lanza por códigos 4xx/5xx."""
    request_headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        request_headers.setdefault("Content-Type", "application/json")
    if token:
        request_headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        GATEWAY_URL + path, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode()


def login(user: dict) -> str:
    status, _, body = http(
        "POST", "/api/v1/auth/login",
        json_body={"username": user["username"], "password": user["password"]},
    )
    assert status == 200, f"login {user['username']}: HTTP {status} {body}"
    return json.loads(body)["access_token"]


def psql(database: str, query: str) -> str:
    result = compose(
        "exec", "-T", "postgres_db", "sh", "-c",
        'psql -U "$POSTGRES_USER" -d "$1" -Atc "$2"', "sh", database, query,
    )
    return result.stdout.strip()


def run_python_in(service: str, code: str) -> subprocess.CompletedProcess:
    """Ejecuta código Python dentro de un contenedor, con sus credenciales."""
    return compose("exec", "-T", service, "python", "-", input_text=code, check=False)


def queue_stats() -> dict[str, dict[str, int]]:
    output = compose(
        "exec", "-T", "rabbitmq", "rabbitmqctl", "list_queues", "-q",
        "name", "messages_ready", "messages_unacknowledged", "consumers",
    ).stdout
    stats = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[1].isdigit():
            stats[parts[0]] = {
                "ready": int(parts[1]),
                "unacked": int(parts[2]),
                "consumers": int(parts[3]),
            }
    return stats


def wait_until(predicate, timeout: float = SAGA_TIMEOUT_SECONDS,
               interval: float = 0.5, message: str = "condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"Timeout ({timeout}s) esperando {message}; último valor: {last!r}")


def soap_envelope(user_id) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="{SOAP_NS['soapenv']}" xmlns:wal="{SOAP_NS['wal']}">
  <soapenv:Header/>
  <soapenv:Body>
    <wal:GetWalletRequest><wal:user_id>{user_id}</wal:user_id></wal:GetWalletRequest>
  </soapenv:Body>
</soapenv:Envelope>""".encode()


SOAP_HEADERS = {
    "Content-Type": "text/xml; charset=utf-8",
    "SOAPAction": "http://investnow.com/wallet/GetWallet",
}


def soap_balance(token: str, user_id: int) -> Decimal:
    status, _, body = http(
        "POST", "/api/v1/wallets/soap/wallet",
        token=token, data=soap_envelope(user_id), headers=SOAP_HEADERS,
    )
    assert status == 200, f"SOAP GetWallet: HTTP {status} {body}"
    balance = ET.fromstring(body).find(".//wal:balance", SOAP_NS)
    assert balance is not None, body
    return Decimal(balance.text)


def place_order(token: str, asset_id: int, total_price: float, quantity: int = 1) -> int:
    status, _, body = http(
        "POST", "/api/v1/orders", token=token,
        json_body={"asset_id": asset_id, "quantity": quantity, "total_price": total_price},
    )
    assert status == 202, f"POST /orders: HTTP {status} {body}"
    data = json.loads(body)
    assert data["status"] == "PENDING", data
    return int(data["order_id"])


def order_status(token: str, order_id: int) -> str | None:
    status, _, body = http("GET", "/api/v1/orders", token=token)
    assert status == 200, f"GET /orders: HTTP {status} {body}"
    for order in json.loads(body):
        if order["order_id"] == order_id:
            return order["status"]
    return None


def wait_final_status(token: str, order_id: int, timeout: float = SAGA_TIMEOUT_SECONDS) -> str:
    return wait_until(
        lambda: (s := order_status(token, order_id)) in ("COMPLETED", "FAILED") and s,
        timeout=timeout,
        message=f"estado final de la orden {order_id}",
    )


def audit_event_types(order_id: int) -> list[str]:
    rows = psql(
        "audit_db",
        f"SELECT event_type FROM audit_events WHERE correlation_id = '{order_id}' "
        "ORDER BY occurred_at",
    )
    return rows.splitlines() if rows else []


def wait_audit(order_id: int, expected: set[str]) -> list[str]:
    return wait_until(
        lambda: (types := audit_event_types(order_id)) and expected <= set(types) and types,
        message=f"eventos {sorted(expected)} en audit_db para la orden {order_id}",
    )


def wallet_transactions(order_id: int) -> int:
    return int(psql(
        "wallet_db",
        f"SELECT count(*) FROM wallet_transactions WHERE transaction_id = 'order:{order_id}'",
    ))


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

class E2ETestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            status, _, _ = http("GET", "/health")
        except OSError as error:
            raise unittest.SkipTest(f"El Gateway no responde en {GATEWAY_URL}: {error}")
        if status != 200:
            raise unittest.SkipTest(f"El Gateway respondió HTTP {status} en /health")
        cls.token = login(INVESTOR)


class T01InfrastructureTests(E2ETestCase):
    """Despliegue: servicios sanos, topología del broker y red interna."""

    def test_gateway_and_services_are_healthy(self):
        self.assertEqual(http("GET", "/health")[0], 200)
        for service in ("catalog", "orders", "wallets"):
            with self.subTest(service=service):
                status, _, body = http("GET", f"/api/v1/{service}/health")
                self.assertEqual(status, 200, body)

    def test_every_consumer_queue_has_retry_dlq_and_a_consumer(self):
        stats = queue_stats()
        for queue in ("order.events", "catalog.events", "wallet.events", "audit.events"):
            with self.subTest(queue=queue):
                self.assertIn(queue, stats)
                self.assertIn(f"{queue}.retry", stats)
                self.assertIn(f"{queue}.dlq", stats)
                self.assertGreaterEqual(stats[queue]["consumers"], 1)

    def test_only_the_gateway_publishes_ports_to_the_host(self):
        output = compose("ps", "--format", "json").stdout.strip()
        containers = (
            json.loads(output) if output.startswith("[")
            else [json.loads(line) for line in output.splitlines() if line]
        )
        exposed = {
            c["Service"]
            for c in containers
            for p in (c.get("Publishers") or [])
            if p.get("PublishedPort")
        }
        self.assertEqual(exposed, {"api"})

    def test_each_service_has_its_own_database(self):
        databases = set(psql("postgres", "SELECT datname FROM pg_database").splitlines())
        self.assertTrue({"order_db", "wallet_db", "audit_db"} <= databases)
        self.assertEqual(
            psql("order_db", "SELECT count(*) FROM outbox WHERE published_at IS NULL "
                             "AND created_at < now() - interval '10 seconds'"),
            "0",
            "El outbox de Order tiene eventos sin publicar hace más de 10 s",
        )


class T02GatewaySecurityTests(E2ETestCase):
    """Gatekeeper y Gateway Offloading (falacia 3: la red es segura)."""

    def test_private_routes_require_a_valid_token(self):
        self.assertEqual(http("GET", "/api/v1/orders")[0], 401)
        self.assertEqual(http("GET", "/api/v1/orders", token="not-a-jwt")[0], 401)
        self.assertEqual(
            http("POST", "/api/v1/auth/login",
                 json_body={"username": "inversor1", "password": "wrong"})[0],
            401,
        )

    def test_catalog_is_public_and_uses_cache_aside(self):
        status, headers, body = http("GET", "/api/v1/catalog")
        self.assertEqual(status, 200, body)
        symbols = {asset["symbol"] for asset in json.loads(body)}
        self.assertTrue({"AAPL", "SPY", "TSLA"} <= symbols)
        self.assertIn(headers.get("X-Cache"), {"HIT", "MISS"})
        self.assertEqual(http("GET", "/api/v1/catalog")[1].get("X-Cache"), "HIT")

    def test_invalid_order_is_rejected_synchronously(self):
        for body in (
            {"asset_id": 0, "quantity": 1, "total_price": 1},
            {"asset_id": 1, "quantity": 0, "total_price": 1},
            {"asset_id": 1, "quantity": 1, "total_price": -5},
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    http("POST", "/api/v1/orders", token=self.token, json_body=body)[0], 422
                )

    def test_client_cannot_spoof_user_context_headers(self):
        """El cliente no puede hacerse pasar por otro usuario ni por admin."""
        admin_token = login(ADMIN)
        admin_order = place_order(admin_token, asset_id=3, total_price=1)  # se rechaza, no debita
        wait_final_status(admin_token, admin_order)

        status, _, body = http(
            "GET", "/api/v1/orders", token=self.token,
            headers={"X-User-Id": str(ADMIN["user_id"])},
        )
        self.assertEqual(status, 200, body)
        owners = {order["user_id"] for order in json.loads(body)}
        self.assertLessEqual(owners, {INVESTOR["user_id"]})

        status, _, _ = http(
            "GET", f"/api/v1/orders/admin/{ADMIN['user_id']}", token=self.token,
            headers={"X-User-Roles": "admin"},
        )
        self.assertEqual(status, 403)

        status, _, body = http(
            "GET", f"/api/v1/orders/admin/{INVESTOR['user_id']}", token=admin_token
        )
        self.assertEqual(status, 200, body)

    def test_service_credentials_cannot_forge_other_services_events(self):
        """Catalog no puede publicar FundsDebited: solo Wallet tiene ese permiso."""
        result = run_python_in("catalog_service", """
from app.events import Event, contract, publish_event
try:
    publish_event(Event.new(
        contract.FUNDS_DEBITED, correlation_id=1,
        payload={"order_id": 1, "user_id": 1, "amount": 1.0, "new_balance": 1.0},
    ))
    print("PUBLISHED")
except Exception as error:
    print("REFUSED", type(error).__name__, error)
""")
        self.assertIn("REFUSED", result.stdout, result.stdout + result.stderr)
        self.assertIn("ACCESS_REFUSED", result.stdout)


class T03PurchaseSagaTests(E2ETestCase):
    """Saga coreografiada de compra, con Audit como consumidor adicional."""

    def test_valid_purchase_completes_and_debits_wallet_once(self):
        balance_before = soap_balance(self.token, INVESTOR["user_id"])
        order_id = place_order(self.token, asset_id=1, total_price=1.25)

        self.assertEqual(wait_final_status(self.token, order_id), "COMPLETED")
        self.assertEqual(wallet_transactions(order_id), 1)
        balance_after = soap_balance(self.token, INVESTOR["user_id"])
        self.assertEqual(balance_after, balance_before - Decimal("1.25"))

        types = wait_audit(order_id, {"OrderPlaced", "AssetValidated", "FundsDebited"})
        self.assertEqual(types[0], "OrderPlaced")
        self.assertNotIn("OrderRejected", types)

    def test_inactive_asset_is_rejected_by_catalog(self):
        order_id = place_order(self.token, asset_id=3, total_price=1)
        self.assertEqual(wait_final_status(self.token, order_id), "FAILED")
        self.assertEqual(wallet_transactions(order_id), 0)
        wait_audit(order_id, {"OrderPlaced", "OrderRejected"})
        self.assertEqual(
            psql("order_db", f"SELECT error_message FROM orders WHERE order_id = {order_id}"),
            "Asset is inactive. Trading blocked.",
        )

    def test_unknown_asset_is_rejected_by_catalog(self):
        order_id = place_order(self.token, asset_id=999999, total_price=1)
        self.assertEqual(wait_final_status(self.token, order_id), "FAILED")
        wait_audit(order_id, {"OrderPlaced", "OrderRejected"})
        self.assertEqual(
            psql("order_db", f"SELECT error_message FROM orders WHERE order_id = {order_id}"),
            "Asset not found in catalog",
        )

    def test_insufficient_funds_are_rejected_by_wallet(self):
        balance_before = soap_balance(self.token, INVESTOR["user_id"])
        order_id = place_order(self.token, asset_id=1, total_price=50_000_000)

        self.assertEqual(wait_final_status(self.token, order_id), "FAILED")
        self.assertEqual(wallet_transactions(order_id), 0)
        wait_audit(order_id, {"OrderPlaced", "AssetValidated", "FundsRejected"})
        self.assertEqual(
            psql("order_db", f"SELECT error_message FROM orders WHERE order_id = {order_id}"),
            "Insufficient funds",
        )
        self.assertEqual(soap_balance(self.token, INVESTOR["user_id"]), balance_before)

    def test_orders_are_isolated_per_user(self):
        admin_token = login(ADMIN)
        order_id = place_order(self.token, asset_id=2, total_price=0.5)
        wait_final_status(self.token, order_id)
        _, _, body = http("GET", "/api/v1/orders", token=admin_token)
        self.assertNotIn(order_id, {order["order_id"] for order in json.loads(body)})


class T04SoapTests(E2ETestCase):
    """Migración REST -> SOAP de la consulta de saldo (Parte 2, punto 2)."""

    def test_wsdl_is_published_through_the_gateway(self):
        status, headers, body = http(
            "GET", "/api/v1/wallets/soap/wallet/wsdl", token=self.token
        )
        self.assertEqual(status, 200, body)
        self.assertIn("xml", headers.get("Content-Type", ""))
        root = ET.fromstring(body)
        self.assertTrue(root.tag.endswith("definitions"), root.tag)
        self.assertIn("GetWallet", body)

    def test_get_wallet_returns_soap_envelope(self):
        status, _, body = http(
            "POST", "/api/v1/wallets/soap/wallet", token=self.token,
            data=soap_envelope(INVESTOR["user_id"]), headers=SOAP_HEADERS,
        )
        self.assertEqual(status, 200, body)
        root = ET.fromstring(body)
        self.assertEqual(root.tag, f"{{{SOAP_NS['soapenv']}}}Envelope")
        self.assertEqual(root.findtext(".//wal:user_id", namespaces=SOAP_NS), "1")
        self.assertEqual(root.findtext(".//wal:status", namespaces=SOAP_NS), "success")
        Decimal(root.findtext(".//wal:balance", namespaces=SOAP_NS))

    def test_malformed_request_returns_soap_fault(self):
        status, _, body = http(
            "POST", "/api/v1/wallets/soap/wallet", token=self.token,
            data=b"<not-xml", headers=SOAP_HEADERS,
        )
        self.assertEqual(status, 400)
        self.assertIsNotNone(ET.fromstring(body).find(".//soapenv:Fault", SOAP_NS))

    def test_user_cannot_read_another_users_wallet(self):
        status, _, body = http(
            "POST", "/api/v1/wallets/soap/wallet", token=self.token,
            data=soap_envelope(ADMIN["user_id"]), headers=SOAP_HEADERS,
        )
        self.assertEqual(status, 403, body)
        self.assertIsNotNone(ET.fromstring(body).find(".//soapenv:Fault", SOAP_NS))

    def test_soap_service_is_consumed_by_another_component(self):
        """Order Service consume el SOAP de Wallet por la red interna."""
        result = compose("exec", "-T", "order_service", "python", "app/soap/test_soap.py")
        self.assertIn("STATUS HTTP: 200", result.stdout, result.stdout + result.stderr)
        self.assertIn("GetWalletResponse", result.stdout)

        missing = run_python_in("order_service", """
import urllib.request, urllib.error
body = b'''<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:wal="http://investnow.com/wallet"><soapenv:Body><wal:GetWalletRequest>
 <wal:user_id>987654</wal:user_id></wal:GetWalletRequest></soapenv:Body></soapenv:Envelope>'''
req = urllib.request.Request("http://wallet_service:8000/api/v1/wallets/soap/wallet",
    data=body, headers={"Content-Type": "text/xml"}, method="POST")
try:
    urllib.request.urlopen(req, timeout=5)
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
""")
        self.assertTrue(missing.stdout.startswith("404"), missing.stdout + missing.stderr)
        self.assertIn("Fault", missing.stdout)


class T05ResilienceTests(E2ETestCase):
    """Falacia 1 (la red es confiable): idempotencia, DLQ y cola durable."""

    def test_redelivered_asset_validated_does_not_debit_twice(self):
        order_id = place_order(self.token, asset_id=1, total_price=0.75)
        self.assertEqual(wait_final_status(self.token, order_id), "COMPLETED")
        wait_audit(order_id, {"AssetValidated"})
        balance_before = soap_balance(self.token, INVESTOR["user_id"])

        original = psql(
            "audit_db",
            "SELECT json_build_object('event_id', event_id, 'payload', payload) "
            "FROM audit_events "
            f"WHERE correlation_id = '{order_id}' AND event_type = 'AssetValidated'",
        )
        new_event_id = str(uuid.uuid4())
        # Reentrega del mismo evento (mismo event_id) y del mismo pedido con
        # otro event_id (por ejemplo, Catalog procesó dos veces OrderPlaced).
        result = run_python_in("catalog_service", f"""
import json
from app.events import Event, contract, publish_event
original = json.loads({original!r})
for event_id in (original["event_id"], {new_event_id!r}):
    publish_event(Event(
        event_type=contract.ASSET_VALIDATED,
        correlation_id={str(order_id)!r},
        payload=original["payload"],
        event_id=event_id,
    ))
print("OK")
""")
        self.assertIn("OK", result.stdout, result.stdout + result.stderr)

        wait_until(
            lambda: psql(
                "wallet_db",
                f"SELECT count(*) FROM processed_events WHERE event_id = '{new_event_id}'",
            ) == "1",
            message="que Wallet procese el evento duplicado",
        )
        self.assertEqual(wallet_transactions(order_id), 1)
        self.assertEqual(soap_balance(self.token, INVESTOR["user_id"]), balance_before)
        self.assertEqual(order_status(self.token, order_id), "COMPLETED")

    def test_malformed_event_goes_to_dlq_and_consumer_keeps_working(self):
        dlq_before = queue_stats()["catalog.events.dlq"]["ready"]
        probe_id = int(time.time() * 1_000_000)
        result = run_python_in("order_service", f"""
from contextlib import closing
import pika
from app.events import Event, broker, contract
with closing(broker.connect()) as connection:
    channel = broker.open_publish_channel(connection)
    channel.basic_publish(
        exchange=contract.EXCHANGE, routing_key="order.placed", body=b"{{malformed",
        properties=pika.BasicProperties(delivery_mode=pika.DeliveryMode.Persistent),
        mandatory=True,
    )
    broker.publish(channel, Event.new(contract.ORDER_PLACED, correlation_id={probe_id},
        payload={{"order_id": {probe_id}, "user_id": 1, "asset_id": 3,
                  "quantity": 1, "total_price": 1}}))
print("OK")
""")
        self.assertIn("OK", result.stdout, result.stdout + result.stderr)

        wait_until(
            lambda: queue_stats()["catalog.events.dlq"]["ready"] > dlq_before,
            message="el mensaje malformado en catalog.events.dlq",
        )
        wait_audit(probe_id, {"OrderRejected"})

    @unittest.skipIf(SKIP_SLOW, "E2E_SKIP_SLOW=1")
    def test_order_survives_wallet_outage(self):
        """Con Wallet caído la orden queda PENDING y se completa al volver."""
        compose("stop", "wallet_service")
        try:
            order_id = place_order(self.token, asset_id=1, total_price=0.5)
            wait_until(
                lambda: queue_stats()["wallet.events"]["ready"] >= 1,
                message="AssetValidated esperando en wallet.events",
            )
            time.sleep(2)
            self.assertEqual(order_status(self.token, order_id), "PENDING")
            self.assertEqual(wallet_transactions(order_id), 0)
        finally:
            compose("start", "wallet_service")

        self.assertEqual(wait_final_status(self.token, order_id, timeout=60), "COMPLETED")
        self.assertEqual(wallet_transactions(order_id), 1)
        compose("up", "-d", "--wait", "wallet_service", timeout=180)


if __name__ == "__main__":
    unittest.main(verbosity=2)
