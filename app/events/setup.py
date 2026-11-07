"""Inicialización de RabbitMQ para el stack de Docker Compose."""

import base64
import json
import os
import re
import time
from contextlib import closing
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.events import broker, contract

NO_ACCESS = "^$"
LEGACY_VHOST = "ut4"
# Audit escribe únicamente en sus colas de retry y DLQ.
SERVICES = {
    "order": (contract.QUEUE_ORDER, (contract.ORDER_PLACED,)),
    "catalog": (contract.QUEUE_CATALOG, (contract.ASSET_VALIDATED, contract.ORDER_REJECTED)),
    "wallet": (contract.QUEUE_WALLET, (contract.FUNDS_DEBITED, contract.FUNDS_REJECTED)),
    "audit": (contract.QUEUE_AUDIT, ()),
}


def exact_names(*names: str) -> str:
    if not names:
        return NO_ACCESS
    return "^(" + "|".join(map(re.escape, names)) + ")$"


def management_request(method: str, path: str, body: dict | None = None) -> None:
    username = os.environ["RABBITMQ_ADMIN_USER"]
    password = os.environ["RABBITMQ_ADMIN_PASSWORD"]
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = Request(
        f"http://rabbitmq:15672/api/{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        method=method,
    )
    with urlopen(request, timeout=5):
        pass


def wait_for_management() -> None:
    for attempt in range(15):
        try:
            management_request("GET", "whoami")
        except HTTPError:
            raise
        except URLError:
            if attempt == 14:
                raise
            time.sleep(1)
        else:
            return


def set_permissions(
    vhost: str, username: str, *, configure: str, write: str, read: str
) -> None:
    management_request(
        "PUT",
        f"permissions/{quote(vhost, safe='')}/{username}",
        {"configure": configure, "write": write, "read": read},
    )


def setup_service(username: str, queue: str, event_types: tuple[str, ...]) -> None:
    management_request(
        "PUT",
        f"users/{username}",
        {"password": os.environ[f"RABBITMQ_{username.upper()}_PASSWORD"], "tags": ""},
    )
    exchanges = [broker.DELIVERY_EXCHANGE]
    if event_types:
        exchanges.append(contract.EXCHANGE)

    set_permissions(
        "/",
        username,
        configure=NO_ACCESS,
        write=exact_names(*exchanges),
        read=exact_names(queue),
    )
    management_request(
        "PUT", f"topic-permissions/%2F/{username}",
        {
            "exchange": broker.DELIVERY_EXCHANGE,
            "write": exact_names(contract.retry_queue(queue), contract.dead_letter_queue(queue)),
            "read": NO_ACCESS,
        },
    )
    if event_types:
        management_request(
            "PUT", f"topic-permissions/%2F/{username}",
            {
                "exchange": contract.EXCHANGE,
                "write": exact_names(*(contract.ROUTING_KEYS[event] for event in event_types)),
                "read": NO_ACCESS,
            },
        )


def main() -> None:
    wait_for_management()
    with closing(broker.connect(os.environ["RABBITMQ_ADMIN_URL"])) as connection:
        broker.declare_topology(connection.channel())

    for username, (queue, event_types) in SERVICES.items():
        setup_service(username, queue, event_types)

    # La cola de trabajo de UT4 se mantiene aislada hasta integrar Order y Wallet.
    management_request("PUT", f"vhosts/{LEGACY_VHOST}", {})
    management_request(
        "PUT",
        "users/order_legacy",
        {"password": os.environ["RABBITMQ_LEGACY_PASSWORD"], "tags": ""},
    )
    set_permissions(
        LEGACY_VHOST,
        "order_legacy",
        configure=exact_names("orders_queue"),
        write=exact_names("amq.default"),
        read=exact_names("orders_queue"),
    )
    print("Topología y permisos de RabbitMQ configurados.")


if __name__ == "__main__":
    main()
