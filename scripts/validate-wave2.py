"""Exercise the Phase 2 event flows against a running Docker Compose stack."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = os.environ.get("COMPOSE_PROJECT_NAME", "fcg-phase2")
COMPOSE = ["docker", "compose", "-p", PROJECT_NAME, "-f", str(ROOT / "docker-compose.yml"),
           "--env-file", str(ROOT / ".env")]
BASE_USERS = "http://127.0.0.1:8081"
BASE_CATALOG = "http://127.0.0.1:8082"


def configuration() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def call(method: str, url: str, payload: dict | None = None, token: str | None = None) -> tuple[int, object]:
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else (b"" if method == "POST" else None)
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as error:
        raw = error.read().decode()
        return error.code, json.loads(raw) if raw else None


def expect(actual: int, wanted: int, action: str) -> None:
    if actual != wanted:
        raise AssertionError(f"{action}: expected HTTP {wanted}, received {actual}")


def compose(*args: str, outcome: str | None = None) -> str:
    env = os.environ.copy()
    if outcome is not None:
        env["PAYMENT_OUTCOME"] = outcome
    result = subprocess.run([*COMPOSE, *args], env=env, text=True, capture_output=True, check=True)
    return result.stdout


def wait_until(description: str, condition, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except (ConnectionError, TimeoutError, URLError):
            pass
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {description}")


def notifications() -> str:
    return compose("logs", "--no-color", "notificationsapi")


def library(token: str) -> list[dict]:
    status, body = call("GET", f"{BASE_CATALOG}/api/library/me", token=token)
    expect(status, 200, "read private library")
    assert isinstance(body, list)
    return body


def create_game(token: str, title: str) -> str:
    status, body = call("POST", f"{BASE_CATALOG}/api/admin/games", {
        "title": title, "description": "Wave 2 broker validation", "genre": "Strategy", "price": 19.95
    }, token)
    expect(status, 201, "create priced game")
    assert isinstance(body, dict)
    return body["id"]


def pending_count(user_id: str, game_id: str) -> int:
    result = subprocess.run(["docker", "volume", "inspect", f"{PROJECT_NAME}_catalog-data", "--format", "{{.Mountpoint}}"],
                            text=True, capture_output=True, check=True)
    database = Path(result.stdout.strip()) / "catalog.db"
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM PendingPurchases WHERE lower(UserId) = lower(?) AND lower(GameId) = lower(?)",
            (user_id, game_id),
        ).fetchone()[0]


def notification_queue_drained() -> bool:
    result = subprocess.run(
        ["docker", "exec", f"{PROJECT_NAME}-rabbitmq-1", "rabbitmqctl", "list_queues",
         "name", "messages_ready", "messages_unacknowledged", "--formatter", "json"],
        text=True, capture_output=True, check=True,
    )
    queues = json.loads(result.stdout)
    return any(
        queue["name"] == "notifications.payment-processed"
        and queue["messages_ready"] == 0
        and queue["messages_unacknowledged"] == 0
        for queue in queues
    )


def assert_no_rejected_confirmation(game_id: str) -> None:
    quiet_since = time.monotonic()
    deadline = quiet_since + 15
    while time.monotonic() < deadline:
        if f"a compra do jogo {game_id} foi confirmada" in notifications():
            raise AssertionError("Rejected purchase produced an approval notification")
        if notification_queue_drained():
            if time.monotonic() - quiet_since >= 3:
                return
        else:
            quiet_since = time.monotonic()
        time.sleep(0.5)
    raise AssertionError("Rejected notification queue did not settle")


def main() -> None:
    config = configuration()
    suffix = uuid4().hex[:12]
    email = f"wave2-{suffix}@example.test"
    password = f"Wave2!{suffix}"

    compose("up", "--no-build", "-d", "--force-recreate", "paymentsapi", outcome="Approved")
    wait_until("approved payment service health", lambda: call("GET", "http://127.0.0.1:8083/health")[0] == 200)
    for port in (8081, 8082, 8083, 8084):
        status, _ = call("GET", f"http://127.0.0.1:{port}/health")
        expect(status, 200, f"health {port}")

    status, user = call("POST", f"{BASE_USERS}/api/auth/register", {
        "name": "Wave Two User", "email": email, "password": password
    })
    expect(status, 201, "register user")
    assert isinstance(user, dict)
    user_id = user["id"]
    if any(key.lower() in {"password", "passwordhash", "accesstoken"} for key in user):
        raise AssertionError("Registration response exposed credentials")
    welcome = f"E-mail simulado para {email}: bem-vindo"
    wait_until("welcome notification", lambda: notifications().count(welcome) == 1)

    status, _ = call("POST", f"{BASE_USERS}/api/auth/register", {
        "name": "Wave Two User", "email": email, "password": password
    })
    expect(status, 409, "reject duplicate registration")
    if notifications().count(welcome) != 1:
        raise AssertionError("Duplicate registration produced another welcome notification")

    status, admin_login = call("POST", f"{BASE_USERS}/api/auth/login", {
        "email": config["ADMIN_EMAIL"], "password": config["ADMIN_PASSWORD"]
    })
    expect(status, 200, "administrator login")
    assert isinstance(admin_login, dict)
    admin_token = admin_login["accessToken"]
    status, user_login = call("POST", f"{BASE_USERS}/api/auth/login", {
        "email": email, "password": password
    })
    expect(status, 200, "user login")
    assert isinstance(user_login, dict)
    user_token = user_login["accessToken"]

    approved_game = create_game(admin_token, f"Wave 2 approved {suffix}")
    if any(item["gameId"] == approved_game for item in library(user_token)):
        raise AssertionError("Game was acquired before payment")
    status, _ = call("POST", f"{BASE_CATALOG}/api/library/me/purchases/{approved_game}", token=user_token)
    expect(status, 202, "initiate approved purchase")
    wait_until("approved library acquisition", lambda: any(
        item["gameId"] == approved_game for item in library(user_token)))
    approved_confirmation = f"E-mail simulado para {email}: a compra do jogo {approved_game} foi confirmada."
    wait_until("approved purchase notification", lambda: notifications().count(approved_confirmation) == 1)
    if pending_count(user_id, approved_game) != 0:
        raise AssertionError("Approved pending purchase was not settled")

    compose("up", "--no-build", "-d", "--force-recreate", "paymentsapi", outcome="Rejected")
    try:
        wait_until("rejected payment service health", lambda: call("GET", "http://127.0.0.1:8083/health")[0] == 200)
        rejected_game = create_game(admin_token, f"Wave 2 rejected {suffix}")
        status, _ = call("POST", f"{BASE_CATALOG}/api/library/me/purchases/{rejected_game}", token=user_token)
        expect(status, 202, "initiate rejected purchase")
        wait_until("rejected purchase settlement", lambda: pending_count(user_id, rejected_game) == 0)
        if any(item["gameId"] == rejected_game for item in library(user_token)):
            raise AssertionError("Rejected purchase acquired a game")
        assert_no_rejected_confirmation(rejected_game)
    finally:
        compose("up", "--no-build", "-d", "--force-recreate", "paymentsapi")
        wait_until("restored payment service health", lambda: call("GET", "http://127.0.0.1:8083/health")[0] == 200)

    print(json.dumps({
        "registration": 201,
        "duplicateRegistration": 409,
        "welcomeNotifications": 1,
        "approvedPurchase": 202,
        "approvedLibraryItems": 1,
        "approvedNotifications": 1,
        "rejectedPurchase": 202,
        "rejectedLibraryItems": 0,
        "rejectedApprovalNotifications": 0,
        "settledPendingPurchases": 2,
        "healthEndpoints": 4,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
