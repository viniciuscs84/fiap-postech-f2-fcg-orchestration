"""Create the local Kind Secret from the ignored Compose environment file."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("JWT_SIGNING_KEY", "RABBITMQ_USER", "RABBITMQ_PASSWORD", "ADMIN_EMAIL", "ADMIN_PASSWORD")


def main() -> None:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value

    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise SystemExit(f"Missing required .env values: {', '.join(missing)}")
    if len(values["JWT_SIGNING_KEY"]) < 32:
        raise SystemExit("JWT_SIGNING_KEY must contain at least 32 characters")

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "fcg-secrets", "namespace": "fcg-phase2"},
        "type": "Opaque",
        "data": {
            key: base64.b64encode(values[key].encode()).decode()
            for key in REQUIRED
        },
    }
    subprocess.run(
        ["kubectl", "--context", "kind-fcg-phase2", "apply", "--server-side", "-f", "-"],
        input=json.dumps(secret), text=True, check=True,
    )


if __name__ == "__main__":
    main()
