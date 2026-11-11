# Enterprise Chat Platform

A production-oriented, multi-tenant chat and AI-agent platform built with
Python 3.12 and FastAPI.

This project is developed incrementally using software-engineering practices
focused on reliability, tenant isolation, deterministic testing, fault
tolerance, and operational safety.

## Project status

- ✅ Service foundation and application factory
- ✅ Validated environment configuration
- ✅ Liveness and readiness endpoints
- ✅ Strict linting, formatting, type checking, and testing
- ✅ Tenant-safe chat domain model and agent-run lifecycle
- ✅ User-turn application orchestration and outbox contract
- ⏳ PostgreSQL persistence, concurrent idempotency, and row-level security
- ⏳ Transactional outbox relay and durable event delivery
- ⏳ Durable agent worker and AI-provider abstraction
- ⏳ Redis-backed realtime delivery and rate limiting
- ⏳ Production observability, deployment, and recovery

## Architecture direction

The platform begins as a modular monolith with clear boundaries. Components
can later be deployed and scaled independently without prematurely creating a
large microservice system.

```text
Client
  │
  ├── REST commands ───────────────► Chat API
  │                                      │
  └── Realtime events ◄──────────── Realtime Gateway
                                         │
                 PostgreSQL ◄────────────┼────────► Redis
                 source of truth         │         ephemeral coordination
                                         │
                                  Durable broker
                                         │
                                         ▼
                                    Agent Worker
                                         │
                                         ▼
                                  AI Provider Adapter
```

### Architectural principles

- PostgreSQL is the durable source of truth.
- Redis is used only for ephemeral coordination, caching, rate limiting,
  presence, and realtime fan-out.
- Tenant identity comes from trusted authentication context.
- Every tenant-owned record carries a tenant identifier.
- Messages are immutable and append-only.
- Commands are idempotent.
- Asynchronous delivery is assumed to be at least once.
- Database transactions are never held open during AI-provider calls.
- Domain logic remains independent of FastAPI, SQLAlchemy, Redis, and AI SDKs.
- External providers are accessed through explicit adapters.
- Failures, retries, timeouts, and recovery behavior are designed explicitly.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python `>=3.12,<3.13`
- Git

The project uses uv to manage Python, the virtual environment, dependencies,
and the lockfile.

## Local setup

Clone the repository and enter this project directory:

```bash
cd day8-enterprise_chat_agent
```

Install all application and development dependencies:

```bash
uv sync --all-groups
```

Create the local environment file:

```bash
cp .env.example .env
```

Confirm the active project interpreter:

```bash
uv run python --version
```

Expected major and minor version:

```text
Python 3.12.x
```

## Running the API

Start the development server:

```bash
uv run uvicorn enterprise_chat_platform.main:create_app \
  --factory \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

The API is available at `http://127.0.0.1:8000` and its interactive
documentation is available at `http://127.0.0.1:8000/docs`.

## Health endpoints

### Liveness

Liveness reports whether the process is running and able to serve HTTP.

```bash
curl -i http://127.0.0.1:8000/health/live
```

Successful response:

```json
{
  "status": "ok",
  "service": "enterprise-chat-api"
}
```

Liveness must not fail merely because an external dependency is unavailable.

### Readiness

Readiness reports whether the service can safely receive traffic.

```bash
curl -i http://127.0.0.1:8000/health/ready
```

Successful response:

```json
{
  "status": "ok",
  "service": "enterprise-chat-api"
}
```

When a required dependency is unavailable, readiness returns HTTP `503` while
liveness remains healthy.

## Configuration

Configuration uses environment variables prefixed with `CHAT_`.

| Variable | Default | Description |
|---|---|---|
| `CHAT_ENVIRONMENT` | `local` | `local`, `test`, `staging`, or `production` |
| `CHAT_SERVICE_NAME` | `enterprise-chat-api` | Service name used in responses and telemetry |
| `CHAT_LOG_LEVEL` | `INFO` | Application logging level |
| `CHAT_DEBUG` | `false` | Enables FastAPI debug behavior |

Example `.env`:

```dotenv
CHAT_ENVIRONMENT=local
CHAT_SERVICE_NAME=enterprise-chat-api
CHAT_LOG_LEVEL=INFO
CHAT_DEBUG=false
```

Configuration is validated during application startup. Unsafe combinations
fail fast. For example, production cannot run with debug mode enabled:

```bash
CHAT_ENVIRONMENT=production CHAT_DEBUG=true \
  uv run python -c \
  'from enterprise_chat_platform.main import create_app; create_app()'
```

Local `.env` files must never be committed. Only `.env.example` is tracked.

## Quality gates

Run these checks before committing:

```bash
uv lock --check
uv sync --frozen --all-groups
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

Apply automatic formatting and safe lint fixes when needed:

```bash
uv run ruff format src tests
uv run ruff check --fix src tests
```

Formatting and linting are separate operations. `ruff format` does not sort
imports; import sorting is performed by `ruff check --fix`.

## Project structure

```text
.
├── .env.example
├── .python-version
├── README.md
├── pyproject.toml
├── uv.lock
├── docs/
│   └── architecture/
│       └── chat-domain.md
├── src/
│   └── enterprise_chat_platform/
│       ├── application/
│       │   ├── __init__.py
│       │   ├── accept_user_turn.py
│       │   └── errors.py
│       ├── api/
│       │   └── health.py
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── agent_run.py
│       │   ├── chat.py
│       │   ├── errors.py
│       │   ├── identifiers.py
│       │   └── primitives.py
│       ├── __init__.py
│       ├── main.py
│       └── settings.py
└── tests/
    ├── api/
    │   └── test_health.py
    ├── unit/
    │   ├── application/
    │   │   └── test_accept_user_turn.py
    │   ├── domain/
    │   │   ├── test_agent_run.py
    │   │   ├── test_chat.py
    │   │   ├── test_identifiers.py
    │   │   └── test_primitives.py
    │   └── test_settings.py
    └── conftest.py
```

The planned source boundaries are:

```text
enterprise_chat_platform/
├── api/              # HTTP transport and Pydantic schemas
├── application/      # Use cases and transaction orchestration
├── domain/           # Framework-independent business rules
├── infrastructure/   # PostgreSQL, Redis, broker, and provider adapters
├── main.py           # Application composition
└── settings.py       # Validated configuration
```

Dependencies must point inward:

```text
API ───────► Application ───────► Domain
Infrastructure ─────────────────► Domain
```

The domain layer must not import application, API, or infrastructure modules.

## Domain documentation

The chat-domain contract is documented in
`docs/architecture/chat-domain.md`. It defines tenant ownership, immutable
messages, idempotency, conversation ordering, agent-run states, UTC time
handling, and concurrency boundaries before those rules are encoded in
persistence models.

The `AcceptUserTurn` application use case coordinates trusted actor context,
authorization, deterministic request fingerprints, domain behavior, an
idempotency receipt, and an outbox event behind one unit-of-work contract.
Its in-memory tests prove sequential replay and definitive rollback behavior.
Concurrent same-key races, compare-and-swap enforcement, and unknown database
commit outcomes require the upcoming PostgreSQL adapter and integration tests.

## Development workflow

Each milestone follows the same workflow:

1. Define the architecture and invariants.
2. Write deterministic tests for the contract.
3. Implement the smallest vertical slice.
4. Run formatting, linting, type checking, and tests.
5. Perform a deliberate failure drill.
6. Review the staged diff.
7. Commit a focused checkpoint.

Avoid combining unrelated changes in one commit.

## Security baseline

- Do not commit secrets, credentials, tokens, or local `.env` files.
- Do not trust a tenant identifier supplied only by the client.
- Do not log authorization headers, cookies, prompts, or sensitive content.
- Do not store durable conversation history only in Redis.
- Do not expose raw provider exceptions to API clients.
- Do not retry external side effects unless they are idempotent.
- Do not enable debug mode in production.

## License

No license has been selected yet.
