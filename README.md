# FCG Phase 2 local orchestration

Place this repository beside `UsersAPI`, `CatalogAPI`, `PaymentsAPI`, and `NotificationsAPI` inside `Fase 2/`. The Compose file builds each service from its own repository and starts RabbitMQ with four application containers. The published images and event flows were validated with Docker Compose on 2026-11-02.

## Prerequisites and configuration

- Docker Engine with Docker Compose v2.
- Local ports 5672, 15672, and 8081-8084 available.
- A copy of each of the five sibling repositories.

From an empty parent directory, obtain the documented sibling layout with:

```powershell
git clone https://github.com/viniciuscs84/fiap-postech-f2-fcg-orchestration.git Orchestration
git clone https://github.com/viniciuscs84/fiap-postech-f2-fcg-users-api.git UsersAPI
git clone https://github.com/viniciuscs84/fiap-postech-f2-fcg-catalog-api.git CatalogAPI
git clone https://github.com/viniciuscs84/fiap-postech-f2-fcg-payments-api.git PaymentsAPI
git clone https://github.com/viniciuscs84/fiap-postech-f2-fcg-notifications-api.git NotificationsAPI
```

Copy `.env.example` to `.env`. Fill `JWT_SIGNING_KEY` with at least 32 random bytes encoded as text, and set nonempty `RABBITMQ_PASSWORD`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD`. Keep `.env` private; it is ignored by Git. `JWT_ISSUER`, `JWT_AUDIENCE`, `RABBITMQ_USER`, and `PAYMENT_OUTCOME` have documented local defaults. `PAYMENT_OUTCOME` must be `Approved` or `Rejected`. `IMAGE_NAMESPACE=fcg-local` and `IMAGE_TAG=local` name images built on the local machine.

For example, PowerShell can create a signing value without displaying it:

```powershell
$signingValue = [Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
```

The service containers receive the same JWT issuer, audience, and signing key. RabbitMQ credentials are supplied to the broker and all four services through Compose. UsersAPI and CatalogAPI keep separate SQLite files in named volumes. CatalogAPI addresses the UsersAPI administrative lookup through the internal URL `http://usersapi:8080/`.

## Start and inspect

Run these commands from this directory:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f notificationsapi
```

Health endpoints are `http://localhost:8081/health` (users), `:8082/health` (catalog), `:8083/health` (payments), and `:8084/health` (notifications). RabbitMQ's management UI is at `http://localhost:15672`; use the credentials in the private `.env` file.

## Docker Hub Personal preparation

The free Docker Personal plan permits four public image repositories for this challenge. Use the `viniciuscs84` Docker Hub namespace with one public repository per service: `viniciuscs84/fcg-users-api`, `viniciuscs84/fcg-catalog-api`, `viniciuscs84/fcg-payments-api`, and `viniciuscs84/fcg-notifications-api`. The four source repositories each contain `.github/workflows/docker-hub.yml`. On a pull request the workflow runs .NET tests and validates the image build without publishing. On `main`, it runs the tests, then publishes the public image with `main` and `sha-<full Git commit SHA>` tags once credentials are configured.

The five public GitHub repositories use the `fiap-postech-f2-fcg-` prefix and track the local `main` branches. The four public Docker Hub repositories listed above exist under `viniciuscs84`, and all four images have `main` and `sha-<full Git commit SHA>` tags. Each service repository has `DOCKERHUB_USERNAME=viniciuscs84` as a variable and `DOCKERHUB_TOKEN` as an encrypted secret. The publication token has repository Read & Write permission, no Delete or Cloud Sandboxes access, and expires on 2027-02-01; rotate it and update all four secrets before expiry. Do not put the token in `.env`, Git, or a workflow file.

To run with the published images, set `IMAGE_NAMESPACE=viniciuscs84` and `IMAGE_TAG=main` in the private `.env`, then run from this directory:

```powershell
docker compose pull usersapi catalogapi paymentsapi notificationsapi
docker compose up --no-build -d
```

Use a `sha-...` tag for a fixed revision. Since the four services have independent Git commits, fixed-revision deployment can set individual image tags in a later Kubernetes manifest or Compose override. The local `docker compose up --build -d` path remains available for source-checkout validation.

## Registration and purchase scenarios

1. Register a new user with `POST http://localhost:8081/api/auth/register`. Provide `name`, `email`, and a Phase 1-compliant password. Check `notificationsapi` logs for one pt-BR welcome message containing the recipient address and no credential.
2. Obtain the administrator JWT with `POST http://localhost:8081/api/auth/login`, using `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Create a priced game through `POST http://localhost:8082/api/admin/games` using that bearer token and JSON fields `title`, `description`, `genre`, and `price`.
3. Obtain the registered user's JWT through the same login endpoint. Send `POST http://localhost:8082/api/library/me/purchases/{gameId}` with that bearer token. The request should return `202`. With `PAYMENT_OUTCOME=Approved`, `GET http://localhost:8082/api/library/me` should eventually contain the game, and `notificationsapi` should log one pt-BR purchase confirmation.
4. To demonstrate rejection, set `PAYMENT_OUTCOME=Rejected` in `.env` and run `docker compose up -d --force-recreate paymentsapi`. Create another game and initiate its purchase as the same user. The new game must remain absent from `/api/library/me`, and no approved-purchase confirmation should be logged for that game.
5. To verify the preserved Phase 1 administrator operation, send `POST http://localhost:8082/api/admin/users/{userId}/games/{gameId}` with an administrator JWT. CatalogAPI checks the target account through UsersAPI. A regular-user JWT must receive `403`; an unknown user must receive `404`.

The automated broker check runs both registration and purchase outcomes against the running Compose stack. It creates unique test users and games, checks the simulated notification logs and library state, and temporarily recreates `paymentsapi` with each configured outcome before restoring the `.env` setting. It requires Python 3 and access to the Docker Engine and the catalog data volume from the same host context. With Docker Engine inside Ubuntu WSL, run it there:

```bash
python3 scripts/validate-wave2.py
```

To isolate a fresh run from existing Compose volumes, pass the same project name to startup and validation:

```bash
docker compose -p fcg-wave3-clean up --build -d
COMPOSE_PROJECT_NAME=fcg-wave3-clean python3 scripts/validate-wave2.py
```

The successful run on 2026-11-02 returned one welcome notification, one approved purchase confirmation and library item, no library item or approval confirmation for the rejected purchase, and zero remaining pending records for both purchases. See `sdd/phases/phase-2/wave-2-implementation-evidence.md` in the parent workspace for the result and limits.
