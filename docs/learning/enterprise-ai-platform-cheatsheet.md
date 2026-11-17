# Enterprise AI Conversation Platform Cheatsheet

Status: based on the implemented Day 8 and Day 9 platform

Audience: engineers building a similar multi-tenant, asynchronous AI product

Purpose: a reusable build order, design checklist, command reference, and troubleshooting guide

## How to use this document

Use this guide in three ways:

1. **Before implementation:** follow the build order and define the invariants.
2. **During implementation:** copy the checklists and pseudocode, not the product-specific names.
3. **Before release:** run the verification and failure-mode checklists.

The central lesson is simple:

> Durable business facts belong in PostgreSQL. Workers, Redis, WebSockets, and
> model providers may accelerate work, but none of them replaces the ledger.

## 1. System at a glance

```text
Browser
  │
  ├── REST commands and queries
  └── authenticated WebSocket notifications
          │
          ▼
Nginx gateway ──► FastAPI API ──► PostgreSQL durable ledger
                                    ▲          │
                                    │          │ queued outbox work
                                    │          ▼
                             notification    worker ──► Echo/Gemini
                                relay          │
                                    │          │ durable completion
                                    ▼          ▼
                                  Redis ◄──── PostgreSQL
                                    │
                                    └── content-free terminal signal
```

### Component ownership

| Component | Owns | Must not own |
|---|---|---|
| React browser | Temporary UI state, polling, reconnect behavior | Trusted identity or final message state |
| Nginx | Static assets and same-origin `/api` and `/ws` routing | Business authorization |
| FastAPI | Authentication, transport validation, commands, queries | Slow provider execution |
| PostgreSQL | Conversations, messages, runs, receipts, policy decisions, review state, outbox | Live socket objects |
| Worker | One leased provider attempt and durable completion | Browser connections or long DB transactions |
| Provider | One bounded completion | Tenant authorization or conversation authority |
| Relay | Durable terminal-event delivery attempts | Message content |
| Redis | Pub/Sub, expiring presence, disposable quotas | Chat history or the only copy of pending work |
| WebSocket | Low-latency “something changed” signal | Authoritative assistant content |

### Useful analogy

- PostgreSQL is the official ledger.
- The transactional outbox is a mailbox written in the same ledger.
- The worker is the cook processing a durable ticket.
- The relay is the mail carrier.
- Redis and WebSockets are the doorbell.
- REST is the official receipt that can always be checked again.

## 2. Recommended implementation order

Build risk-first, not screen-first.

### Phase 0 — Write the invariants

Define these before choosing frameworks:

- Who owns each entity?
- What is the tenant boundary?
- Which values are immutable?
- Which state transitions are legal?
- What must commit atomically?
- Which operations may be retried?
- What is authoritative after a crash?
- Which data must never enter logs, Redis, or error messages?

**Done when:** the rules can be unit-tested without HTTP, SQLAlchemy, Redis, or a model API.

### Phase 1 — Create the project foundation

- Pin the Python range and lock dependencies.
- Use a `src/` package layout.
- Enable strict static typing.
- Configure formatting, linting, async test strictness, and warnings-as-errors.
- Add settings validation before adding services.

This project uses Python 3.12, uv, Ruff, mypy strict mode, pytest strict mode,
FastAPI, SQLAlchemy async, Alembic, PostgreSQL, Redis, and React.

**Remember:** in a `src/` layout, `src` is a filesystem directory. It is not
part of the import name. Import `enterprise_chat_platform`, not
`src.enterprise_chat_platform`.

### Phase 2 — Model the domain

Create framework-independent value objects and entities:

- strongly typed UUID wrappers;
- timezone-aware UTC timestamps;
- immutable dataclasses;
- conversation and run state machines;
- explicit domain errors;
- pure operations that receive clocks and generated IDs from callers.

**Done when:** invalid states cannot be constructed silently and transitions are exhaustively tested.

### Phase 3 — Define application ports

Use protocols/interfaces for infrastructure-facing behavior:

- unit of work;
- repositories;
- clock and ID generators;
- authorizer;
- provider;
- worker job store;
- notification publisher;
- policy engine;
- profile and review stores.

Handlers orchestrate use cases; they do not import FastAPI or SQLAlchemy.

### Phase 4 — Establish PostgreSQL safely

- Separate bootstrap, migration, and runtime roles.
- Put application tables in an explicit schema.
- Give runtime roles only the DML they require.
- Keep DDL credentials out of API and worker processes.
- Use named constraints and indexes.
- Add tenant columns to primary, unique, and foreign-key relationships.

**Done when:** a runtime-role test proves that it cannot create or alter tables.

### Phase 5 — Implement one transactional command

For accepting a turn, atomically write:

1. updated conversation version and sequence;
2. immutable user message;
3. queued agent run;
4. idempotency receipt;
5. worker outbox event.

All five commit, or none commit.

### Phase 6 — Add the recoverable worker

Use the claim, call, complete pattern:

1. claim with `FOR UPDATE SKIP LOCKED`;
2. assign a lease token and expiry;
3. commit the claim;
4. call the provider without an open DB transaction;
5. complete using the lease token as a fence;
6. retry transient failures with bounded exponential backoff and jitter;
7. persist safe terminal failures after the attempt budget.

### Phase 7 — Add reliable realtime notifications

- Write a terminal notification to the outbox in the same completion transaction.
- Let a separate relay lease and publish it.
- Publish only IDs, status, versions, and sequences.
- Use Redis as an ephemeral fan-out mechanism.
- Make the browser refetch through REST and poll while work is pending.

### Phase 8 — Add deterministic governance

- Version the input policy.
- Evaluate before every provider call.
- Persist the first decision immutably.
- Allow, block, or escalate.
- Block/escalate atomically terminalizes the run.
- Escalation creates a durable review case.

### Phase 9 — Add governed prompting

- Represent provider context as typed messages.
- Never interpolate user text into a trusted system instruction.
- Bound context and retain a newest complete-message suffix.
- Drop orphan assistant messages when an older user message is evicted.
- Fingerprint the prompt plan without logging its content.

### Phase 10 — Add immutable assistant profiles

- Store append-only profile versions.
- Keep one mutable tenant assignment pointer.
- Use optimistic revision checks when activating a version.
- Pin the exact profile ID and version to each queued run.
- Make retries use the pinned version even after a new version is activated.

### Phase 11 — Add UI, operations, and failure tests

- Make frontend role checks a usability feature, not a security boundary.
- Add health/readiness checks, structured logs, and a runbook.
- Test real database constraints and concurrent races.
- Test process crashes, lost signals, stale leases, and retries.

## 3. Domain-model checklist

### Strong identifiers

Use distinct types even when every ID is UUID-backed:

```python
TenantId
UserId
ConversationId
MessageId
AgentRunId
EventId
AssistantProfileId
```

Why:

- prevents accidentally passing a user ID where a tenant ID is expected;
- improves signatures and code review;
- centralizes nil-UUID and parse validation.

### Time rules

- Reject naive datetimes.
- Normalize aware datetimes to UTC.
- Pass time into domain operations.
- Do not call the system clock inside an entity.
- Use fixed clocks in tests; do not sleep.

### Immutability rules

- Messages are append-only.
- Policy assessments are append-only/first-write-wins.
- Assistant profile versions are immutable.
- Idempotency receipts preserve the original result.
- State changes return new domain snapshots or use explicit persistence transitions.

### State machines

Agent run:

```text
queued  ──► running ──► succeeded
   │          │
   ├──────────┴──────► failed
   └────────────────► cancelled
```

Terminal runs never reopen. A retry is a new run or a new leased attempt,
depending on the failure boundary.

Human review:

```text
pending(version=0) ──► resolved(version=1)
                         ├── confirmed
                         └── dismissed
```

Resolving a review records an audit decision. It does not silently resume the
terminal agent run.

## 4. Durable data model

| Table | Purpose | Key invariant |
|---|---|---|
| `conversations` | Lifecycle, version, last sequence | `(tenant_id, id)` ownership and optimistic version |
| `messages` | Immutable user/assistant history | unique conversation-local sequence |
| `agent_runs` | Asynchronous response lifecycle | tenant-safe input/output references and legal state shape |
| `idempotency_receipts` | Replay original command result | tenant + conversation + operation + key uniqueness |
| `outbox_events` | Durable worker and notification work | leased, retryable, uniquely identified logical event |
| `input_policy_assessments` | Immutable governance decision | one first-write-wins decision per run |
| `human_reviews` | Durable escalation workflow | only escalated assessments may create a case |
| `assistant_profile_versions` | Append-only prompt configuration | tenant + profile + positive version |
| `tenant_assistant_profile_assignments` | Active profile pointer | one assignment per tenant with optimistic revision |

### Tenant-safe relationship rule

Prefer:

```text
(tenant_id, child_reference)
    REFERENCES parent(tenant_id, id)
```

over a foreign key on `id` alone. This lets PostgreSQL reject a cross-tenant
relationship even when application code makes a mistake.

### Conversation ordering

Do not sort by timestamps alone. Assign a positive, never-reused sequence
inside each conversation and enforce uniqueness in the database.

## 5. Idempotent command pattern

### Scope

```text
(tenant_id, conversation_id, operation, idempotency_key)
```

### Rules

- Key is opaque, case-sensitive, bounded, and not silently trimmed.
- Canonicalize the request and hash it.
- Same key + same fingerprint returns the original result.
- Same key + different fingerprint returns `409 Conflict`.
- Store result IDs in the receipt.
- Lock or serialize the receipt scope so concurrent retries cannot duplicate work.

### Pseudocode

```text
BEGIN
  existing = lock_receipt(scope)
  if existing:
      if existing.fingerprint != request.fingerprint: conflict
      return existing.result as replay

  conversation = load_authorized_conversation(tenant, id)
  turn = domain.accept_user_turn(...)
  compare_and_swap_conversation(expected_version)
  insert(user_message)
  insert(queued_run_with_pinned_profile)
  insert(idempotency_receipt)
  insert(worker_outbox_event)
COMMIT
```

### Why the receipt is durable

The client may time out after the server commits. Without a durable receipt,
the client cannot know whether retrying will duplicate the turn.

## 6. Optimistic concurrency

Use compare-and-swap when collisions are possible but not constant:

```sql
UPDATE chat.conversations
SET version = :new_version,
    last_message_sequence = :new_sequence
WHERE tenant_id = :tenant_id
  AND id = :conversation_id
  AND version = :expected_version;
```

- One updated row means success.
- Zero rows means stale state; return a conflict or retry at the application boundary.
- A domain version in memory is not sufficient. The database predicate is the concurrency guard.

Also use optimistic revisions for assistant-profile activation and human-review resolution.

## 7. Transactional outbox

### Problem it solves

This unsafe dual write can lose work:

```text
1. commit user message
2. publish queue message
```

If the process crashes between steps, the message exists but no job exists.

### Safe approach

Insert the business data and an outbox row in the same PostgreSQL transaction.
A worker later leases the row.

### Keep the outbox even if a broker is added

Kafka, SQS, or another broker can become the dispatch target, but the outbox
still bridges the atomicity gap between the database transaction and broker publish.

## 8. Worker leases and fencing

### Claim

```sql
SELECT ...
FROM chat.outbox_events
WHERE published_at IS NULL
  AND available_at <= :now
  AND (lease_expires_at IS NULL OR lease_expires_at <= :now)
ORDER BY available_at, occurred_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

Then store:

- unique `lease_token`;
- `lease_expires_at`;
- incremented `attempt_count`;
- `last_attempt_at`.

### Fence

Every completion/reschedule/failure update must include the lease token:

```sql
... WHERE tenant_id = :tenant_id
      AND id = :event_id
      AND lease_token = :lease_token
      AND published_at IS NULL;
```

Lease expiry alone is not enough. A paused worker can resume after expiry.
The token predicate is what prevents its stale write.

### Provider-call boundary

Never hold a DB transaction open across a model call:

```text
short claim transaction
        ↓ commit
bounded external provider call
        ↓
short fenced completion transaction
```

### Retry policy

- classify transient versus terminal failures;
- use a timeout shorter than the lease;
- use bounded exponential backoff;
- add jitter to avoid synchronized retries;
- cap attempts and persist a safe terminal failure;
- never expose raw provider exceptions to the client.

### Guarantee wording

Do not claim exactly once. Say:

> Attempts are at-least-once. Durable state is effectively-once through
> idempotency, uniqueness, optimistic concurrency, and lease fencing.

An external provider can still be called twice around an ambiguous crash.
Send the run ID as a provider idempotency key when the provider supports it.

## 9. Realtime without losing reliability

### Terminal event payload

Send only:

- event ID;
- tenant-routed channel information;
- run ID and terminal status;
- nullable output message ID;
- conversation version;
- nullable message sequence.

Do not send prompts, messages, bearer tokens, or provider errors through Redis.

### Browser recovery algorithm

```text
connect WebSocket
authenticate in first frame
authorize each conversation subscription

when terminal signal arrives:
    refetch run and messages through REST

while run is pending:
    poll slowly when socket is healthy
    poll faster when socket is disconnected
```

Duplicate signals cause duplicate safe reads. Missed signals only delay the UI.

## 10. Multi-tenant security checklist

### Identity

- Derive `tenant_id` and actor ID from validated authentication.
- Never accept trusted tenant or audit actor values from the request body.
- Pin JWT algorithm, issuer, and audience.
- Require expiry, issued-at, subject, and tenant claims.
- Use asymmetric JWT verification in deployed environments.
- Keep demo authentication local/test-only.

### Authorization

- Authorize before creating data dependencies or side effects.
- Return the same safe not-found behavior for missing and cross-tenant records.
- Enforce roles in backend dependencies; frontend visibility is not security.
- Scope every repository method by tenant.

### Database

- Composite tenant keys and foreign keys.
- Runtime role cannot perform DDL.
- Migrator credentials appear only in migration jobs.
- Consider PostgreSQL row-level security as defense in depth.

### WebSocket

- Check an explicit origin allowlist.
- Authenticate in the first frame, not the query string.
- Bound authentication time and frame size.
- Apply connection/message quotas.
- Authorize every conversation subscription.
- Expire connection-presence leases.

Decide explicitly whether a Redis-backed quota fails open or closed. This
project preserves durable chat availability when Redis quota checks fail, so
abuse protection can temporarily weaken; a higher-risk product may choose a
different policy or a local fallback limiter.

### Secrets and logs

- Mask settings and connection URLs.
- Do not log message content, prompt plans, tokens, keys, or raw provider responses.
- Store only safe failure codes/messages.
- Inject provider credentials only into the worker when possible.

## 11. Deterministic input governance

### Evaluation order

```text
claim job
  ↓
evaluate versioned rules
  ↓
persist first-write-wins assessment
  ├── allow ─────► build prompt and call provider
  ├── block ─────► terminalize safely; no provider call
  └── escalate ──► terminalize + create review; no provider call
```

### Assessment fields

- policy version;
- action: allow, block, or escalate;
- bounded risk score;
- bounded safe findings;
- server-controlled assessment timestamp.

For deterministic phrase rules, normalize input consistently: Unicode NFKC,
case folding, formatting-control removal, and whitespace normalization. Make
precedence explicit and order-independent: `BLOCK > ESCALATE > ALLOW`.

### Why deterministic rules first

- predictable latency and cost;
- explainable findings;
- easy versioning and regression tests;
- works during provider outages;
- no sensitive pre-screening request to another model.

An ML classifier can be added later as another layer, not as the only control.

### Human-review privacy

- List view returns metadata and safe findings, not raw input.
- Detail view returns exact content only to an authorized reviewer.
- Add `Cache-Control: no-store` to sensitive detail/resolution responses.
- Resolve optimistically with an expected version.
- Record reviewer identity and server time.

## 12. Safe prompt construction

### Trust separation

```text
trusted system instruction = typed, server-owned profile fields
untrusted context          = structured user/assistant messages
```

Never construct this:

```text
system = "Company policy: " + arbitrary_admin_or_user_text
```

Prefer an allowlisted renderer:

```text
template_version + tone enum + expertise enum + style enum
    ──► deterministic system instruction
```

### Context budgeting

- Include prior user content only with positive evidence of an `allow` assessment.
- Include assistant content only from successfully completed runs.
- Exclude blocked, escalated, unassessed, legacy, and orphaned context.
- Keep the newest complete-message suffix.
- Remove whole oldest messages until under the character/token budget.
- Do not cut a message in the middle.
- Drop leading assistant messages whose user prompt was evicted.
- Keep the current input separate from prior context if the provider adapter expects that.
- Record selected/omitted counts and a content fingerprint, not raw content in logs.

### Character budget versus token budget

A character budget is deterministic and provider-neutral but only approximates
model tokens. Move to provider-specific token counting when cost or context
limits require greater precision.

## 13. Versioned assistant profiles

### Pattern

```text
assistant_profile_versions        tenant_assistant_profile_assignments
---------------------------       ------------------------------------
tenant + profile + version  ◄──── tenant active pointer
immutable fields                  optimistic revision
                                   activation actor/time

agent_runs ── pins ──► exact tenant + profile + version
```

### Creation and activation

1. Load/lock the tenant assignment.
2. Verify `expected_latest_version` before inserting the next version.
3. Insert the immutable version; do not activate implicitly.
4. Preview using the same server renderer used by the worker.
5. Activate with `expected_revision`.
6. Increment revision and record authenticated actor/time.
7. Return `409` to a stale administrator.

### Why pin each run

If v1 was active when a run was queued and v2 becomes active before execution,
the run must still use v1. This makes retries reproducible and preserves audit history.

## 14. API and frontend contract

### Important routes

| Route | Purpose |
|---|---|
| `POST /auth/login` | Explicitly local demo JWT login |
| `PUT /v1/conversations/{id}` | Idempotent conversation creation |
| `POST /v1/conversations/{id}/turns` | Accept an idempotent user turn |
| `GET /v1/conversations/{id}` | Authoritative conversation state |
| `GET /v1/conversations/{id}/messages` | Ordered immutable messages |
| `GET /v1/agent-runs/{id}` | Run status and safe failure |
| `GET /v1/conversations/{id}/input-policy-notices` | Durable user-visible policy notices |
| `GET /v1/governance/input-policy` | Tenant compliance counts and decisions |
| `GET /v1/governance/human-reviews` | Bounded review queue |
| `POST /v1/governance/human-reviews/{run}/resolve` | Optimistic review resolution |
| `GET /v1/admin/assistant-profiles` | Admin profile catalog |
| `POST /v1/admin/assistant-profiles/preview` | Server-rendered preview |
| `POST /v1/admin/assistant-profiles/{id}/versions` | Create next immutable version |
| `POST /v1/admin/assistant-profiles/{id}/versions/{version}/activate` | Activate with revision check |
| `WS /ws/{client_connection_id}` | Authenticated change notifications |

### Error contract

- `401`: unauthenticated or invalid token.
- `403`: authenticated but role not permitted.
- `404`: missing or invisible cross-tenant resource.
- `409`: idempotency/concurrency/revision conflict.
- `422`: structurally or semantically invalid request.
- `503`: required dependency not ready.

Use safe, stable error codes/messages. Do not include SQL, provider responses,
tenant existence, or secrets.

### Frontend rules

- Treat API state as authoritative.
- Generate a fresh idempotency key for a new user action; reuse it only for retrying the same action.
- Disable duplicate submit while a command is in flight.
- Ignore stale responses after selection changes.
- Reconcile on WebSocket notification.
- Show pending, blocked, escalated, failed, and succeeded states explicitly.
- Hide unavailable navigation by role, but depend on backend authorization.
- Confirm destructive or active-configuration changes.

## 15. Migrations and database privileges

### Migration loop

```bash
uv run alembic heads
uv run alembic current
uv run alembic revision --autogenerate -m "describe the change"
uv run ruff format migrations
uv run ruff check --fix migrations
uv run alembic upgrade head --sql
uv run alembic upgrade head
uv run alembic check
```

### Never trust autogenerated migrations blindly

Review:

- tenant columns and composite foreign keys;
- primary/unique constraints;
- check constraints and index predicates;
- column nullability and backfills;
- ownership and grants;
- accidental table/constraint drops;
- downgrade dependency order;
- compatibility with the currently deployed application version.

### Safe rollout pattern

```text
expand schema
  ↓
backfill existing rows
  ↓
deploy compatible readers/writers
  ↓
enforce non-null/constraints
  ↓
contract old shape in a later release
```

Prefer a forward fix over a destructive production downgrade.

## 16. Configuration rules

- Validate settings at startup and fail closed.
- Use secret-aware types that hide inputs in errors.
- Require `postgresql+asyncpg` URLs with user, password, host, and database.
- Require `redis` or `rediss` URLs.
- Enforce provider timeout < worker lease.
- Enforce initial backoff <= maximum backoff.
- Reject demo auth, development headers, HS256, Echo, debug mode, and wildcard WebSocket origins in deployed environments as appropriate.
- Require HTTPS for external provider endpoints.

## 17. Test strategy

### Layered test matrix

| Layer | Proves |
|---|---|
| Domain unit | Invariants, transitions, policy precedence, prompt determinism |
| Application unit | Orchestration, replay, authorization-before-side-effect, retry classification |
| Mapper/model unit | Domain/SQL translation and metadata constraints |
| Repository unit | Tenant predicates, locks, CAS conditions, bounds/order |
| API unit/contract | Status codes, safe bodies, role checks, strict DTOs |
| PostgreSQL integration | Real constraints, grants, transactions, races, lease fencing, migrations |
| Frontend unit | State transitions, role navigation, conflict recovery, escaped rendering |
| Full-stack manual/E2E | Container wiring, login, worker, relay, WebSocket, REST recovery |

### Race tests that matter

- two writers with the same conversation version: exactly one wins;
- concurrent identical idempotency retries: one durable result;
- same key with different fingerprint: conflict;
- expired worker lease reclaimed: stale token cannot complete;
- two admins activate with one expected revision: one wins;
- two reviewers resolve version 0: one wins;
- transaction fails at commit: no partial records;
- policy block/escalate and terminal notification commit atomically.

### Main quality gates

```bash
uv lock --check
uv sync --frozen --all-groups
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy src tests migrations/env.py migrations/versions
uv run pytest -q

npm --prefix frontend ci
CI=true npm --prefix frontend test -- --watchAll=false --runInBand
CI=true npm --prefix frontend run build
```

### Real PostgreSQL suite

Run only against the disposable local Compose database. The suite mutates the
schema, so stop application processes first.

```bash
./scripts/start-local.sh
./scripts/stop-local.sh
docker compose up --detach --wait postgres

set -a
source .env
set +a

export CHAT_DATABASE_URL="postgresql+asyncpg://${POSTGRES_RUNTIME_USER}:${POSTGRES_RUNTIME_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/${POSTGRES_APP_DATABASE}"
export CHAT_MIGRATION_DATABASE_URL="postgresql+asyncpg://${POSTGRES_MIGRATOR_USER}:${POSTGRES_MIGRATOR_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/${POSTGRES_APP_DATABASE}"

uv run alembic upgrade head
uv run pytest -m integration -q
uv run alembic check

./scripts/stop-local.sh
./scripts/start-local.sh
```

## 18. Local operations quick reference

```bash
# Start/reconcile everything and wait for health
./scripts/start-local.sh

# Inspect
docker compose ps --all
docker compose logs --follow --tail=100 api worker relay

# Health through the same-origin gateway
curl --fail-with-body http://localhost:3000/healthz
curl --fail-with-body http://localhost:3000/api/health/live
curl --fail-with-body http://localhost:3000/api/health/ready

# Preserve PostgreSQL data while stopping services
./scripts/stop-local.sh

# Destructive local reset: deletes the Compose volume and all local data
./scripts/stop-local.sh --volumes
```

If ports are occupied, update all coupled values:

```dotenv
APP_PORT=3008
POSTGRES_HOST_PORT=55438
CHAT_WEBSOCKET_ALLOWED_ORIGINS=http://localhost:3008,http://127.0.0.1:3008
```

## 19. Failure diagnosis

| Symptom | Likely cause | First checks |
|---|---|---|
| `ModuleNotFoundError` | File/package missing or wrong `src` import | Confirm file path and import package name |
| Ruff `I001` | Import groups/order wrong | `uv run ruff check --fix <path>` then inspect diff |
| Ruff `B010` | Constant-name `setattr` | Use assignment or a narrowly justified suppression inside the test |
| Undefined name after editing test | Code inserted outside function/import removed | Inspect first 80 lines and failing line before formatting again |
| Alembic detects added table | Model exists but migration not applied | Generate/review migration, upgrade local DB, rerun check |
| Alembic reports removed + added FK | Metadata and migration use different schema-qualified target/name | Compare exact FK target and constraint name |
| Integration table does not exist | Database not at head | `alembic current`, `heads`, `upgrade head` |
| Redis hostname `redis` fails on host | Container DNS name used by host process | Use `127.0.0.1` for host or run process in Compose |
| `localhost:3000` occupied | Another process/stack | Change `APP_PORT` and WebSocket origins together |
| PostgreSQL password changed but login fails | Existing volume retained old role password | For disposable data, reset volume and initialize again |
| Integration worker result is flaky | Live worker/relay racing the test | Stop full stack; run only PostgreSQL |
| mypy duplicate test module | Same filename in non-package directories | Rename one file or add intentional package boundaries |
| `react-scripts` missing | Frontend dependencies not installed | `npm --prefix frontend ci` |
| Tests read local `.env` unexpectedly | Environment not isolated in fixtures | Clear settings cache and provide explicit test settings |
| Stale worker overwrites result | Completion not fenced by lease token | Add token to every state-changing predicate |

## 20. Anti-patterns to reject

- Calling the model synchronously inside the API request.
- Holding a database transaction open during network I/O.
- Writing business data and then publishing a queue message as a separate dual write.
- Calling the design “exactly once.”
- Using Redis or WebSockets as the only copy of work or messages.
- Trusting tenant IDs, actor IDs, or audit fields from request bodies.
- Filtering by object ID without tenant scope.
- Using frontend role checks as authorization.
- Returning another tenant's resource existence through different errors.
- Mutating old prompt/profile versions.
- Re-evaluating a policy decision differently on every retry.
- Interpolating user content into the system instruction.
- Sending message content through Redis notifications.
- Logging bearer tokens, prompts, provider payloads, or raw errors.
- Running autogenerated migrations without manual review.
- Running integration tests against a valuable database.
- Adding Kafka, Kubernetes, RAG, or streaming before the measured need and invariants are clear.

## 21. Production-readiness checklist

### Identity and security

- [ ] External OIDC/SAML identity provider.
- [ ] RS256/ES256 verification with issuer/audience pinning.
- [ ] TLS/WSS everywhere.
- [ ] Secrets manager, rotation, and provider egress controls.
- [ ] PostgreSQL RLS evaluated and tested.
- [ ] Data retention, deletion, residency, and audit-export policies.
- [ ] Authenticated/TLS Redis.
- [ ] Dependency, container, SAST, DAST, and penetration scanning.

### Reliability and operations

- [ ] Explicit SLOs and error budgets.
- [ ] Metrics for queue age/depth, attempts, lease loss, provider latency, policy outcomes, review age, relay lag, sockets, and DB pools.
- [ ] Alerts tied to user/compliance impact.
- [ ] Load, soak, chaos, and restore tests.
- [ ] PostgreSQL backups, PITR, and restore drills.
- [ ] Outbox retention/archival strategy.
- [ ] Provider quota, cost, circuit-breaker, and idempotency strategy.
- [ ] Fair scheduling and tenant admission control.
- [ ] CI required checks and protected main branch.

### Product safety

- [ ] Policy rollout/version strategy.
- [ ] Human-review ownership and SLA.
- [ ] Safe fallback when policy/provider is unavailable.
- [ ] Prompt and output evaluation suite.
- [ ] Clear user disclosure for pending, blocked, escalated, and failed states.

## 22. Definition of done for a new vertical slice

- [ ] Domain invariant and state transition documented.
- [ ] Tenant boundary explicit.
- [ ] Authorization happens before side effects.
- [ ] Transaction boundary documented.
- [ ] Retry and idempotency behavior specified.
- [ ] Failure classification and safe client error defined.
- [ ] Database constraints enforce important invariants.
- [ ] Migration reviewed in offline SQL and real PostgreSQL.
- [ ] Unit, integration, API, and UI tests added at the right layers.
- [ ] Logs and metrics expose state without sensitive content.
- [ ] Runbook includes failure and recovery steps.
- [ ] Security and privacy review complete.
- [ ] Rollout and rollback/forward-fix plan defined.

## 23. Project code map

| Concern | Starting point |
|---|---|
| Domain entities and rules | `src/enterprise_chat_platform/domain/` |
| Use cases and ports | `src/enterprise_chat_platform/application/` |
| HTTP/WebSocket boundary | `src/enterprise_chat_platform/api/` |
| SQL models and constraints | `src/enterprise_chat_platform/infrastructure/persistence/models/` |
| Domain/SQL conversion | `src/enterprise_chat_platform/infrastructure/persistence/mappers/` |
| Tenant-scoped persistence | `src/enterprise_chat_platform/infrastructure/persistence/repositories/` |
| Worker transaction logic | `infrastructure/persistence/agent_run_job_store.py` |
| Worker orchestration | `application/process_agent_run.py` and `worker.py` |
| Notification relay | `application/process_terminal_notifications.py`, `notification_relay_store.py`, and `relay.py` |
| Realtime | `infrastructure/realtime/` and `api/websocket.py` |
| Input policy | `domain/input_policy.py` and `domain/conversation_policy.py` |
| Prompt building | `domain/prompting.py` and `application/tenant_prompting.py` |
| Human review | `domain/human_review.py`, `application/resolve_human_review.py`, and `api/human_reviews.py` |
| Assistant profiles | `domain/assistant_profile.py`, `application/manage_assistant_profiles.py`, and `api/assistant_profiles.py` |
| Composition/settings | `main.py`, `runtime.py`, and `settings.py` |
| Browser | `frontend/src/` |
| Migrations | `migrations/versions/` |
| Operations | `compose.yaml`, `scripts/`, and `docs/operations.md` |

## 24. The shortest reusable mental model

When designing the next system, ask:

1. What is the durable truth?
2. What must commit together?
3. How will a retry avoid duplication?
4. How will concurrent writers detect stale state?
5. How will a crashed worker's stale write be fenced?
6. What happens when every optional dependency is down?
7. Where does tenant identity come from?
8. Which invariants can the database enforce?
9. What sensitive data can be excluded entirely?
10. How will an operator prove and restore correctness?

If those ten answers are precise, most implementation decisions become much easier.
