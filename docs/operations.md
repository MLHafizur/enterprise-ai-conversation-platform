# Operations runbook

This runbook covers the complete local stack: frontend gateway, FastAPI,
PostgreSQL, worker, notification relay, Redis, WebSockets, and the optional
Gemini provider. Run commands from the project root.

## Service responsibilities

| Service | Responsibility | Durable state |
|---|---|---|
| `frontend` | Serve React and proxy same-origin `/api` and `/ws` traffic | None |
| `api` | Authenticate, validate commands, serve REST, and own local sockets | None outside PostgreSQL |
| `postgres` | Source of truth and concurrency boundary | Named PostgreSQL volume |
| `migrate` | Run `alembic upgrade head` with the DDL-capable role | None; exits `0` |
| `worker` | Lease queued work, call the provider, and commit results | None outside PostgreSQL |
| `relay` | Lease terminal outbox rows and publish signals to Redis | None outside PostgreSQL |
| `redis` | Pub/Sub, expiring presence, and disposable quota counters | No authoritative chat state |

The API, worker, and relay use the restricted runtime database role. Only the
migration job uses the DDL-capable migrator role. The bootstrap superuser is
used only when PostgreSQL initializes an empty volume.

Redis is intentionally disposable. PostgreSQL stores both unfinished worker
jobs and unfinished terminal notifications.

## Configure local development

The recommended helper creates local configuration and starts the complete
stack:

```bash
./scripts/start-local.sh
```

On its first run, the helper copies `.env.example` to `.env`, restricts the
file to mode `600`, generates three different PostgreSQL passwords and a long
JWT secret without printing them, validates configuration, builds images, and
waits for health checks. Existing nonblank secrets are preserved.

The full browser demonstration uses these non-secret settings:

```dotenv
CHAT_AUTH_MODE=jwt
CHAT_DEMO_AUTH_ENABLED=true
CHAT_JWT_ALGORITHM=HS256
CHAT_REALTIME_ENABLED=true
CHAT_PROVIDER_MODE=echo
```

Never commit `.env`. To customize ports or provider mode before the first
start, copy `.env.example` manually, edit only the intended settings, leave
secret fields blank for generation, and then run the helper.

## Normal start, observation, and stop

Start or reconcile all services and wait until they are healthy:

```bash
./scripts/start-local.sh
docker compose ps --all
```

Expected steady state:

- `postgres`: healthy
- `redis`: healthy
- `migrate`: exited with code `0`
- `api`: healthy
- `worker`: running
- `relay`: running
- `frontend`: healthy or running

Open <http://localhost:3000>. The Compose API is intentionally private and is
reachable from the host only through the frontend gateway.

Follow the normal request, provider, and notification path:

```bash
docker compose logs --follow --tail=100 frontend api worker relay
```

Inspect dependency logs:

```bash
docker compose logs --tail=100 postgres redis migrate
```

Stop processes while preserving PostgreSQL data:

```bash
./scripts/stop-local.sh
```

Restart one stateless component after a configuration or transient process
problem:

```bash
docker compose restart api
docker compose restart worker
docker compose restart relay
docker compose restart frontend
```

## Health and traffic decisions

Check through the frontend gateway:

```bash
curl --fail-with-body --include http://localhost:3000/api/health/live
curl --fail-with-body --include http://localhost:3000/api/health/ready
```

Use liveness to decide whether the API process should restart. Use readiness
to decide whether it should receive traffic. Readiness performs bounded probes
against PostgreSQL and, when realtime is enabled, Redis. A required dependency
outage returns `503`; liveness should remain `200` while the process is alive.

The frontend exposes its own lightweight container health route at `/healthz`.
Worker and relay health is observed through process/container status, logs,
and outbox-age metrics in a production deployment.

## Local demo login

The browser calls `POST /auth/login` with an allowlisted demo tenant alias and
role. It should work only when all of the following are true:

- environment is `local` or `test`;
- auth mode is `jwt`;
- demo auth is explicitly enabled;
- JWT algorithm is `HS256`; and
- a signing secret is configured.

This route is a learning convenience, not SSO. Staging and production settings
reject it. A production frontend must redirect to a real OIDC/SAML-backed
identity provider and send the resulting access token to this API.

If local login returns `403`, inspect effective API configuration without
printing the JWT key and verify the five conditions above. Recreate the API
and frontend after configuration changes:

```bash
docker compose up --build --detach api frontend
```

## Provider modes

The default local mode is `echo`, which performs no external I/O and returns a
deterministic answer. `disabled` fails closed when no approved provider exists.

To opt into Gemini locally:

```dotenv
CHAT_PROVIDER_MODE=gemini
CHAT_GEMINI_API_KEY=replace-locally
CHAT_GEMINI_MODEL=gemini-2.5-flash
```

Recreate only the worker:

```bash
docker compose up --build --detach worker
docker compose logs --follow --tail=100 worker
```

Do not print the key, provider request, or raw provider response into shared
logs. Gemini can incur cost and send content to an external processor; Echo is
the correct mode for architecture exercises and deterministic tests.

## Migration workflow

The two database URLs have different jobs:

- `CHAT_MIGRATION_DATABASE_URL` uses the migrator role for DDL.
- `CHAT_DATABASE_URL` uses the runtime role for DML.

Never give the API, worker, or relay the migrator URL.

Compose constructs private container URLs automatically. The optional host
URLs in `.env` are blank by default. Before running Alembic or integration
tests from the host, load the generated values and construct host URLs using
the published PostgreSQL port:

```bash
set -a
source .env
set +a
export CHAT_DATABASE_URL="postgresql+asyncpg://${POSTGRES_RUNTIME_USER}:${POSTGRES_RUNTIME_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/${POSTGRES_APP_DATABASE}"
export CHAT_MIGRATION_DATABASE_URL="postgresql+asyncpg://${POSTGRES_MIGRATOR_USER}:${POSTGRES_MIGRATOR_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/${POSTGRES_APP_DATABASE}"
```

The local helper generates hexadecimal passwords, so they are safe in these
URLs. For other passwords, URL-encode reserved characters. Do not echo either
URL or place it in shell history with a real production secret.

Inspect and apply migrations from the host:

```bash
uv run alembic heads
uv run alembic current
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Run the one-shot Compose migration again:

```bash
docker compose run --rm migrate
```

Preview SQL without applying it:

```bash
uv run alembic upgrade head --sql
```

After intentionally changing SQLAlchemy metadata, create and review a
migration:

```bash
uv run alembic revision --autogenerate -m "describe the schema change"
uv run ruff format migrations
uv run ruff check --fix migrations
uv run alembic upgrade head --sql
uv run alembic upgrade head
uv run alembic check
```

Autogenerated migration code is not trusted output. Review tenant columns,
keys, constraint/index names, grants, accidental drops, and downgrade order.
Downgrades may destroy data; exercise them only against disposable databases.

## Test workflow

Python checks, excluding integration tests by project configuration:

```bash
uv lock --check
uv sync --frozen --all-groups
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy src tests migrations/env.py migrations/versions
uv run pytest -q
```

Frontend checks:

```bash
npm --prefix frontend ci
CI=true npm --prefix frontend test -- --watchAll=false --runInBand
npm --prefix frontend run build
```

Real PostgreSQL integration verification, with the host database URLs exported
as described in the migration workflow above:

```bash
docker compose up --detach postgres
uv run alembic upgrade head
uv run pytest -m integration -q
uv run alembic check
```

`alembic check` must finish with `No new upgrade operations detected.` A
reported operation means model metadata and committed migrations disagree.
Integration tests must target only the disposable local Compose database.

## How durable processing recovers

Accepting a turn commits the conversation compare-and-swap update, user
message, queued run, idempotency receipt, and worker outbox event together.
Either all become durable or none do.

The worker:

1. selects one due work event with `FOR UPDATE SKIP LOCKED`;
2. increments its attempt counter and stores a lease token/expiry;
3. commits the short claim transaction;
4. calls the provider outside all DB transactions with a timeout;
5. commits an assistant result or terminal safe failure; and
6. creates the terminal notification in that same final transaction.

The relay independently:

1. selects one due `agent_run.terminal.v1` row with `SKIP LOCKED`;
2. stores its own attempt count, lease token, and expiry;
3. publishes a content-free event to the tenant's Redis channel;
4. marks the row published when its lease is still valid; or
5. reschedules it with bounded backoff when Redis publication fails.

Lease tokens fence late processes. A crashed process leaves durable work
behind, and another process can reclaim it after expiry. Both external
provider calls and Redis publication are at-least-once around crash windows.

## WebSocket and REST recovery

The WebSocket is an accelerator, not a data source. It carries terminal IDs,
status, version, and sequence—not message content. On a signal, the browser
uses REST to refetch the run and conversation messages.

While a run is pending, the browser also polls. It polls less frequently while
the socket is healthy and more frequently when disconnected. A Redis outage,
missed signal, duplicate signal, browser sleep, or API restart can therefore
delay the UI but cannot lose a completed answer.

The first WebSocket frame authenticates with the JWT. The API checks the
Origin header, token, connection quota, frame size, and authorization for each
conversation subscription. Do not put tokens in WebSocket query strings.

## Incident: readiness is `503`

1. Confirm container state and logs:

   ```bash
   docker compose ps --all
   docker compose logs --tail=200 postgres redis migrate api
   ```

2. Confirm PostgreSQL accepts connections:

   ```bash
   docker compose exec -T postgres sh -c \
     'pg_isready --host=127.0.0.1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'
   ```

3. Confirm Redis answers:

   ```bash
   docker compose exec -T redis redis-cli ping
   ```

4. Confirm migrations reached the head:

   ```bash
   uv run alembic current
   uv run alembic heads
   ```

5. Verify runtime URLs, hostnames, passwords, and drivers without printing
   secrets. Restart only the stateless API after fixing configuration:

   ```bash
   docker compose restart api
   ```

## Incident: migration job failed

Read the complete log and compare current/head revisions:

```bash
docker compose logs migrate
uv run alembic current
uv run alembic heads
```

Fix the migration or configuration, review offline SQL, and rerun the job:

```bash
uv run alembic upgrade head --sql
docker compose run --rm migrate
```

Never use `alembic stamp` merely to silence an error. It changes the version
marker without applying schema changes.

## Incident: runs stay queued or running

Inspect and restart the stateless worker:

```bash
docker compose logs --tail=200 worker
docker compose restart worker
```

Inspect queue metadata without selecting payload or message content:

```bash
docker compose exec -T postgres sh -c '
  psql --no-psqlrc --username="$POSTGRES_USER" --dbname="$POSTGRES_APP_DATABASE" \
    --command="SELECT event_type, attempt_count, available_at, lease_expires_at, published_at FROM chat.outbox_events ORDER BY occurred_at;"
'
```

Interpretation:

- A future `available_at` means backoff is active.
- A future `lease_expires_at` means a process currently owns the attempt.
- An expired lease with no `published_at` is reclaimable.
- A queued run beyond the configured attempt limit should become safely
  failed without another provider call.

Do not manually clear leases or edit run state during normal recovery.

## Incident: answer exists but live update did not arrive

Check the durable result first through the run and message REST endpoints. If
it exists, the correctness path succeeded and the problem is notification
latency.

Inspect relay, Redis, API subscriber, and frontend logs:

```bash
docker compose logs --tail=200 relay redis api frontend
```

Inspect only terminal-notification queue metadata:

```bash
docker compose exec -T postgres sh -c '
  psql --no-psqlrc --username="$POSTGRES_USER" --dbname="$POSTGRES_APP_DATABASE" \
    --command="SELECT id, attempt_count, available_at, lease_expires_at, published_at FROM chat.outbox_events WHERE event_type = '\''agent_run.terminal.v1'\'' ORDER BY occurred_at;"
'
```

- Pending rows indicate a stopped relay or Redis publication failure.
- A future lease means a relay currently owns the event.
- A published row with no browser update points to the Redis subscriber,
  WebSocket connection/subscription, or gateway upgrade path.

Restarting `relay` or `api` is safe. The browser polling fallback should still
reconcile the result. Do not copy message content into Redis to “fix” delivery.

## Incident: Redis is unavailable

Expected behavior:

- API readiness returns `503` while realtime is enabled.
- New durable chat writes may still be accepted when the quota check encounters
  a transient Redis error, but routing should respect readiness policy.
- The worker continues committing provider results to PostgreSQL.
- The relay reschedules terminal notifications with backoff.
- Connected clients lose live signals and continue REST polling.

Restore Redis, then restart unhealthy stateless processes if they do not
reconnect:

```bash
docker compose restart redis
docker compose restart api relay
```

No PostgreSQL replay or data repair should be required.

## Incident: Gemini is failing

Inspect sanitized worker logs and the run's public failure state. Timeouts,
network failures, `408`, `429`, and `5xx` responses are transient and use the
worker's bounded retry policy. Other rejected/invalid responses become
terminal safe failures.

Verify the configured model, secret injection, provider quota, and outbound
network policy without logging the request, key, or raw response. After a run
fails terminally, submit a new turn with a new idempotency key; do not reopen or
mutate the terminal run.

## Incident: client did not receive the turn response

The DB commit may have succeeded after the network response was lost. Retry the
exact POST using the same tenant, actor, conversation, idempotency key, and
content.

- If the first commit succeeded, the API returns the original IDs with
  `replayed: true`.
- If it did not commit, the retry creates the turn once.
- If fingerprinted input changed, the API returns `409`.

Generating a new key asks for a second turn and is not recovery from an unknown
outcome.

## Local reset

PostgreSQL initialization scripts run only for an empty data directory.
Changing passwords in `.env` does not update roles in an existing volume.

For a disposable local environment only, this deletes all Day 8 PostgreSQL
data and recreates roles/schema on the next start:

```bash
./scripts/stop-local.sh --volumes
./scripts/start-local.sh
```

This is destructive and is not a production credential-rotation procedure.

## Production checklist

### Identity and edge

- Disable demo login and insecure development-header auth.
- Integrate an external IdP and validate RS256/ES256 JWT issuer, audience,
  expiry, subject, and tenant claims.
- Use TLS/WSS, secure headers, intentional token storage/refresh, and a strict
  WebSocket origin allowlist.
- Rate-limit login, HTTP, and WebSocket upgrade paths at the edge.

### Secrets and least privilege

- Store DB, Redis, JWT, and Gemini credentials in a secret manager.
- Keep migrator credentials out of API, worker, and relay containers.
- Rotate credentials through audited procedures and never log secret URLs.
- Give the provider only the minimum approved content and permissions.

### Data and recovery

- Configure PostgreSQL backups, point-in-time recovery, restore drills,
  replication/failover, and migration approval/rollback procedures.
- Use authenticated and encrypted Redis with memory/eviction policy suitable
  for disposable coordination.
- Assess PostgreSQL row-level security as defense in depth for hostile tenants.
- Document retention, deletion, legal hold, residency, and provider retention.

### Reliability and scale

- Scale API, worker, and relay independently and test `SKIP LOCKED` contention.
- Keep worker leases longer than provider timeouts and relay leases longer than
  relay poll intervals.
- Confirm provider rate limits, idempotency, timeout, and retry behavior.
- Load-test REST, WebSocket upgrades, Redis fan-out, reconnect storms, and
  PostgreSQL connection pools.
- Rehearse worker, relay, Redis, API, gateway, and database failures.

### Observability and privacy

- Alert on readiness, restart loops, outbox depth/oldest age, lease expiry,
  retries, failed runs, relay backoff, Redis errors, WebSocket counts, provider
  latency, and HTTP error rate.
- Correlate requests, agent runs, and events with safe IDs.
- Never log bearer tokens, API keys, prompts, completions, raw provider errors,
  or terminal-event payload expansions containing content.
- Define SLOs, escalation ownership, and a tested incident runbook.
