# FCG Phase 2 local orchestration

Place this repository beside `UsersAPI`, `CatalogAPI`, `PaymentsAPI`, and `NotificationsAPI` inside `Fase 2/`. The Compose file builds each service from its own repository and starts RabbitMQ with four application containers. This is a prepared local environment; image and end-to-end validation require Docker Compose.

## Prerequisites and configuration

- Docker Engine with Docker Compose v2.
- Local ports 5672, 15672, and 8081-8084 available.
- A copy of each of the five sibling repositories.

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

The two event flows and image builds have not yet been run in this environment because Docker and RabbitMQ are unavailable. Capture actual output and gate results after a runtime is available.
