# Enterprise Chat Platform

A reliable, multi-tenant chat backend built with Python 3.12, FastAPI,
PostgreSQL, SQLAlchemy, and Alembic. It accepts a user message once, records
all durable work atomically, processes the queued agent run in a recoverable
worker, and exposes polling APIs for the result.

This is the completed Day 8 backend MVP. The emphasis is not a flashy chat
screen; it is the engineering underneath one: tenant boundaries, replay-safe
commands, concurrency control, crash recovery, bounded provider calls, and
tests against a real PostgreSQL database.

## Project status

- ✅ Python 3.12 project managed and locked with uv
- ✅ FastAPI application factory, validated settings, and managed DB lifespan
- ✅ Liveness plus real PostgreSQL readiness probes
- ✅ Development-header auth and validated JWT auth for deployed environments
- ✅ Tenant-scoped conversations, immutable messages, and agent-run lifecycle
- ✅ Idempotent conversation creation and user-turn acceptance
- ✅ One atomic transaction for message, run, receipt, outbox, and conversation CAS
- ✅ Transactional outbox worker with leases, fencing, timeout, retry, and recovery
- ✅ Conversation, message-page, and agent-run polling APIs
- ✅ Alembic migrations and separate bootstrap, migrator, and runtime DB roles
- ✅ Containerized PostgreSQL, migration, API, and worker services
- ✅ Unit, contract, concurrency, migration-drift, and PostgreSQL integration tests

The local worker intentionally uses `EchoChatProvider`; it returns
`Echo: <your message>` without external I/O. Replace and inject this adapter
before calling the system a real AI deployment. Redis, WebSockets, RAG, tool
execution, and a frontend are deliberate post-MVP work.

## Architecture in plain language

Think of the platform as a restaurant:

- The API is the waiter. It authenticates the customer and writes the order.
- PostgreSQL is the order ledger. It is the durable source of truth.
- The outbox row is the kitchen ticket written in the same transaction as the
  order, so the food request cannot be lost after the customer gets a receipt.
- The worker is a cook. It leases one ticket, calls the provider, then records
  the assistant message and marks the ticket complete.
- If a cook crashes, the lease expires and another cook can safely take over.
- The client polls the run and message endpoints like checking the order board.

```text
Client
  │
  ├─ PUT conversation / POST turn
  ▼
FastAPI API ── one DB transaction ──► PostgreSQL
                                         ├─ conversation + message
                                         ├─ queued agent run
                                         ├─ idempotency receipt
                                         └─ outbox event
                                                │ leased with SKIP LOCKED
                                                ▼
                                         Agent worker
                                                │ bounded call
                                                ▼
                                         ChatProvider
                                                │
                                                └─ assistant message + terminal run
```

The provider call is never made while a database transaction is open. See
[the operations runbook](docs/operations.md) for failure and recovery details,
and [the domain contract](docs/architecture/chat-domain.md) for business
invariants.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python `>=3.12,<3.13`
- Docker with Docker Compose
- Git

Confirm the project interpreter:

```bash
uv run python --version
```

It must report Python `3.12.x`.

## Quick start with Docker Compose

Enter the project and install the locked development environment:

```bash
cd day8-enterprise_chat_agent
uv sync --frozen --all-groups
cp .env.example .env
```

Edit `.env`. At minimum, set three different local-only passwords:

```dotenv
POSTGRES_BOOTSTRAP_PASSWORD=replace-with-a-long-local-password
POSTGRES_MIGRATOR_PASSWORD=replace-with-a-different-long-password
POSTGRES_RUNTIME_PASSWORD=replace-with-another-long-password
```

For host-run development and tests, also set these URLs with the same
migrator and runtime passwords. URL-encode a password if it contains reserved
URL characters.

```dotenv
CHAT_DATABASE_URL=postgresql+asyncpg://enterprise_chat_app:RUNTIME_PASSWORD@127.0.0.1:55432/enterprise_chat
CHAT_MIGRATION_DATABASE_URL=postgresql+asyncpg://enterprise_chat_migrator:MIGRATOR_PASSWORD@127.0.0.1:55432/enterprise_chat
```

Start the complete stack:

```bash
docker compose up --build --detach
docker compose ps --all
```

Compose waits for PostgreSQL, runs `alembic upgrade head` once, then starts the
API and worker. Check the endpoints:

```bash
curl --fail-with-body http://127.0.0.1:8000/health/live
curl --fail-with-body http://127.0.0.1:8000/health/ready
```

Interactive OpenAPI documentation is at <http://127.0.0.1:8000/docs>.

## End-to-end local walkthrough

Local Compose uses development authentication. Identity is supplied through
headers only in `local` and `test`; it is never accepted in request JSON.

Set stable example IDs:

```bash
BASE_URL=http://127.0.0.1:8000
TENANT_ID=11111111-1111-4111-8111-111111111111
USER_ID=22222222-2222-4222-8222-222222222222
CONVERSATION_ID=33333333-3333-4333-8333-333333333333
```

Create the conversation. The first request returns `201`; an identical replay
returns `200` with `"replayed": true`.

```bash
curl --fail-with-body --request PUT \
  --header "X-Dev-Tenant-Id: $TENANT_ID" \
  --header "X-Dev-User-Id: $USER_ID" \
  "$BASE_URL/v1/conversations/$CONVERSATION_ID"
```

Submit one turn with a client-generated idempotency key. The API returns `202`
without waiting for the provider.

```bash
TURN_RESPONSE="$(curl --fail-with-body --silent --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "X-Dev-Tenant-Id: $TENANT_ID" \
  --header "X-Dev-User-Id: $USER_ID" \
  --header 'Idempotency-Key: walkthrough-turn-001' \
  --data '{"content":"Explain transactional outbox in one sentence."}' \
  "$BASE_URL/v1/conversations/$CONVERSATION_ID/turns")"
printf '%s\n' "$TURN_RESPONSE"
```

Extract the generated run ID with the project Python:

```bash
RUN_ID="$(printf '%s' "$TURN_RESPONSE" | \
  uv run python -c 'import json, sys; print(json.load(sys.stdin)["agent_run_id"])')"
printf 'run=%s\n' "$RUN_ID"
```

Poll the run. Its status moves through `queued`/`running` to `succeeded` (or
`failed` with a safe public message).

```bash
curl --fail-with-body \
  --header "X-Dev-Tenant-Id: $TENANT_ID" \
  --header "X-Dev-User-Id: $USER_ID" \
  "$BASE_URL/v1/agent-runs/$RUN_ID"
```

List messages in ascending sequence order. With the local echo provider, the
second item is the assistant response.

```bash
curl --fail-with-body \
  --header "X-Dev-Tenant-Id: $TENANT_ID" \
  --header "X-Dev-User-Id: $USER_ID" \
  "$BASE_URL/v1/conversations/$CONVERSATION_ID/messages?limit=50"
```

To prove idempotency, repeat the turn request with the same key and exact
payload. It returns the original message/run IDs with `"replayed": true` and
creates no duplicate rows. Reusing that key for different content returns
`409 idempotency_conflict`.

## Run services directly during development

Start only PostgreSQL, apply migrations from the host, then run the API and
worker in two terminals:

```bash
docker compose up --detach postgres
uv run alembic upgrade head
```

```bash
uv run uvicorn enterprise_chat_platform.main:create_app \
  --factory \
  --host 127.0.0.1 \
  --port 8000 \
  --no-access-log \
  --reload
```

```bash
uv run enterprise-chat-worker
```

Stop the Compose services without deleting data:

```bash
docker compose down
```

## Idempotency and recovery guarantees

Accepting a turn atomically commits the conversation compare-and-swap update,
user message, queued run, idempotency receipt, and outbox event. Either all
five become durable, or none do.

The idempotency scope is tenant + conversation + operation + key:

- Same scope and same request returns the original result.
- Same scope but different request returns `409`.
- Concurrent same-key requests serialize safely in PostgreSQL.
- Concurrent different-key writes use optimistic version checks; a loser gets
  `409 conversation_conflict` and can retry.
- After a network/commit uncertainty, retry the exact request with the same
  key instead of inventing a new key.

The worker leases due outbox rows with `FOR UPDATE SKIP LOCKED`. A lease token
fences stale workers. Provider timeouts and transient failures are retried with
bounded exponential backoff and jitter; the default is at most five attempts.
A crashed worker leaves durable work behind, and another worker can reclaim it
after the lease expires. Provider calls are therefore at-least-once; a real
provider adapter should also use its own idempotency facility when available.

## Authentication

Development mode accepts `X-Dev-Tenant-Id` and `X-Dev-User-Id` only when
`CHAT_ENVIRONMENT` is `local` or `test`, `CHAT_AUTH_MODE=development`, and
`CHAT_ALLOW_INSECURE_DEVELOPMENT_AUTH=true`. The opt-in defaults to `false`,
so missing deployment configuration fails closed instead of trusting headers.

Staging and production fail startup unless JWT mode is enabled. A production
baseline is:

```dotenv
CHAT_ENVIRONMENT=production
CHAT_DEBUG=false
CHAT_AUTH_MODE=jwt
CHAT_ALLOW_INSECURE_DEVELOPMENT_AUTH=false
CHAT_PROVIDER_MODE=disabled
CHAT_JWT_ALGORITHM=RS256
CHAT_JWT_KEY=<verification-public-key-injected-by-your-secret-manager>
CHAT_JWT_ISSUER=enterprise-chat-platform
CHAT_JWT_AUDIENCE=enterprise-chat-api
```

The bearer token must contain `exp`, `iat`, `iss`, `aud`, `sub`, and
`tenant_id`. Both `sub` and `tenant_id` must be UUIDs. The algorithm is pinned
from configuration; staging/production reject symmetric `HS256`. Never commit
the verification key or place a private signing key in this service.

## Health behavior

- `GET /health/live` proves the HTTP process is alive; dependency failures do
  not make it fail.
- `GET /health/ready` runs a bounded `SELECT 1` against PostgreSQL. It returns
  `503` when the database is unavailable, so an orchestrator can stop routing
  new traffic.

## Quality gates

The default test command excludes integration tests:

```bash
uv lock --check
uv sync --frozen --all-groups
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy src tests migrations/env.py migrations/versions
uv run pytest -q
```

Run real PostgreSQL tests after the local database is healthy and migrated:

```bash
docker compose up --detach postgres
uv run alembic upgrade head
uv run pytest -m integration -q
uv run alembic check
```

Integration tests perform real I/O and should target only the local Compose
database. More migration and recovery commands are in
[docs/operations.md](docs/operations.md).

## Project structure

```text
.
├── compose.yaml                  # postgres + migrate + api + worker
├── Dockerfile                    # non-root Python 3.12 runtime image
├── docker/postgres/init/         # one-time roles, DB, and schema bootstrap
├── migrations/                   # ordered Alembic schema changes
├── docs/
│   ├── architecture/chat-domain.md
│   └── operations.md
├── src/enterprise_chat_platform/
│   ├── api/                      # auth, health, commands, and query routes
│   ├── application/              # create, accept-turn, and worker use cases
│   ├── domain/                   # framework-independent business rules
│   ├── infrastructure/
│   │   ├── chat_provider.py      # local EchoChatProvider boundary
│   │   └── persistence/          # models, mappers, repos, UoWs, job store
│   ├── main.py                   # API composition
│   ├── runtime.py                # lifespan-owned DB engine/session factory
│   ├── settings.py               # validated environment configuration
│   └── worker.py                 # recoverable worker entry point
└── tests/
    ├── api/
    ├── unit/
    └── integration/              # real local PostgreSQL tests
```

Dependencies point inward: transport and infrastructure depend on application
and domain contracts; the domain does not import FastAPI or SQLAlchemy.

## Deliberate MVP boundaries

- `EchoChatProvider` is only for local learning and deterministic tests. A real
  production provider adapter, credentials, provider-specific idempotency,
  quotas, and safety policy must be injected at worker composition.
- Clients poll durable HTTP query endpoints. Redis and WebSockets are deferred
  until realtime fan-out is actually required.
- RAG, embeddings, model routing, and tool execution are separate bounded
  contexts and are not hidden inside this reliable chat core.
- PostgreSQL is the durable source of truth; Redis must not later become the
  only home of conversation history or pending work.

## License

No license has been selected yet.
