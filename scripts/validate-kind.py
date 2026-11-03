"""Exercise registration and both payment outcomes in the local Kind cluster."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
KUBECTL = ["kubectl", "--context", "kind-fcg-phase2", "-n", "fcg-phase2"]
USERS = "http://127.0.0.1:8081"
CATALOG = "http://127.0.0.1:8082"
QUEUES = {
    "notifications.user-created", "payments.order-placed",
    "catalog.payment-processed", "notifications.payment-processed",
}


def config() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def kubectl(*args: str) -> str:
    return subprocess.run([*KUBECTL, *args], capture_output=True, text=True, check=True).stdout


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


def expect(actual: int, wanted: int, description: str) -> None:
    if actual != wanted:
        raise AssertionError(f"{description}: expected HTTP {wanted}, received {actual}")


def wait_until(description: str, condition, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except (ConnectionError, TimeoutError, URLError):
            pass
        time.sleep(0.5)
    raise AssertionError(f"Timed out waiting for {description}")


def notification_logs() -> str:
    return kubectl("logs", "deployment/notificationsapi")


def library(token: str) -> list[dict]:
    status, body = call("GET", f"{CATALOG}/api/library/me", token=token)
    expect(status, 200, "read private library")
    assert isinstance(body, list)
    return body


def create_game(token: str, title: str) -> str:
    status, body = call("POST", f"{CATALOG}/api/admin/games", {
        "title": title, "description": "Wave 4 Kind validation", "genre": "Strategy", "price": 19.95,
    }, token)
    expect(status, 201, "create priced game")
    assert isinstance(body, dict)
    return body["id"]


def queues_drained() -> bool:
    raw = kubectl("exec", "deployment/rabbitmq", "--", "rabbitmqctl", "list_queues",
                  "name", "messages_ready", "messages_unacknowledged", "--formatter", "json")
    rows = json.loads(raw)
    names = {row["name"] for row in rows}
    return QUEUES.issubset(names) and all(
        row["messages_ready"] == 0 and row["messages_unacknowledged"] == 0
        for row in rows if row["name"] in QUEUES
    )


def assert_rejection_stable(token: str, game_id: str) -> None:
    quiet_since = time.monotonic()
    deadline = quiet_since + 30
    while time.monotonic() < deadline:
        if any(item["gameId"] == game_id for item in library(token)):
            raise AssertionError("Rejected payment acquired a game")
        if f"a compra do jogo {game_id} foi confirmada" in notification_logs():
            raise AssertionError("Rejected payment produced an approval notification")
        if queues_drained():
            if time.monotonic() - quiet_since >= 3:
                return
        else:
            quiet_since = time.monotonic()
        time.sleep(0.5)
    raise AssertionError("Payment queues did not settle after rejection")


def main() -> None:
    values = config()
    forwards: list[subprocess.Popen] = []
    payment_overridden = False
    try:
        for service, port in (("usersapi", 8081), ("catalogapi", 8082),
                              ("paymentsapi", 8083), ("notificationsapi", 8084)):
            forwards.append(subprocess.Popen(
                [*KUBECTL, "port-forward", f"service/{service}", f"{port}:8080"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        for port in (8081, 8082, 8083, 8084):
            wait_until(f"health {port}", lambda port=port: call("GET", f"http://127.0.0.1:{port}/health")[0] == 200)
        wait_until("broker consumers", queues_drained, timeout=90)

        suffix = uuid4().hex[:12]
        email = f"wave4-{suffix}@example.test"
        password = f"Wave4!{suffix}"
        status, _ = call("POST", f"{USERS}/api/auth/register", {
            "name": "Wave Four User", "email": email, "password": password,
        })
        expect(status, 201, "register user")
        welcome = f"E-mail simulado para {email}: bem-vindo"
        wait_until("welcome notification", lambda: notification_logs().count(welcome) == 1)
        status, _ = call("POST", f"{USERS}/api/auth/register", {
            "name": "Wave Four User", "email": email, "password": password,
        })
        expect(status, 409, "reject duplicate registration")
        if notification_logs().count(welcome) != 1:
            raise AssertionError("Duplicate registration produced another welcome notification")

        status, admin = call("POST", f"{USERS}/api/auth/login", {
            "email": values["ADMIN_EMAIL"], "password": values["ADMIN_PASSWORD"],
        })
        expect(status, 200, "administrator login")
        assert isinstance(admin, dict)
        status, user = call("POST", f"{USERS}/api/auth/login", {
            "email": email, "password": password,
        })
        expect(status, 200, "user login")
        assert isinstance(user, dict)
        admin_token, user_token = admin["accessToken"], user["accessToken"]

        approved_game = create_game(admin_token, f"Wave 4 approved {suffix}")
        status, _ = call("POST", f"{CATALOG}/api/library/me/purchases/{approved_game}", token=user_token)
        expect(status, 202, "initiate approved purchase")
        wait_until("approved library acquisition", lambda: any(
            item["gameId"] == approved_game for item in library(user_token)))
        confirmation = f"E-mail simulado para {email}: a compra do jogo {approved_game} foi confirmada."
        wait_until("approved purchase confirmation", lambda: notification_logs().count(confirmation) == 1)

        kubectl("set", "env", "deployment/paymentsapi", "PaymentSimulation__Outcome=Rejected")
        payment_overridden = True
        kubectl("rollout", "status", "deployment/paymentsapi", "--timeout=120s")
        rejected_game = create_game(admin_token, f"Wave 4 rejected {suffix}")
        status, _ = call("POST", f"{CATALOG}/api/library/me/purchases/{rejected_game}", token=user_token)
        expect(status, 202, "initiate rejected purchase")
        assert_rejection_stable(user_token, rejected_game)
        status, _ = call("POST", f"{CATALOG}/api/library/me/purchases/{rejected_game}", token=user_token)
        expect(status, 202, "retry settled rejected purchase")
        assert_rejection_stable(user_token, rejected_game)

        print(json.dumps({
            "healthEndpoints": 4, "registration": 201, "duplicateRegistration": 409,
            "welcomeNotifications": 1, "approvedPurchase": 202, "approvedLibraryItems": 1,
            "approvedNotifications": 1, "rejectedPurchase": 202, "rejectedLibraryItems": 0,
            "rejectedApprovalNotifications": 0, "rejectedRetry": 202,
        }, sort_keys=True))
    finally:
        try:
            if payment_overridden:
                kubectl("set", "env", "deployment/paymentsapi", "PaymentSimulation__Outcome-")
                kubectl("rollout", "status", "deployment/paymentsapi", "--timeout=120s")
        finally:
            for process in forwards:
                process.terminate()
            for process in forwards:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    main()
