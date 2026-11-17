# Staff and Principal Engineer Interview Guide

Case study: Enterprise AI Conversation Platform

Target roles: Staff Engineer, Senior Staff Engineer, and Principal Engineer

Purpose: system-design study guide, technical deep dive, leadership preparation, and revision workbook

> “Principal,” not “principle,” is the job title. A principle is a rule; a
> Principal Engineer applies sound principles across teams and systems.

## How to study this guide

Do not memorize every sentence. Learn a repeatable answer structure:

1. clarify requirements and scale;
2. state the strongest invariant;
3. draw ownership boundaries;
4. walk one write and one read/recovery path;
5. explain failure semantics precisely;
6. cover security and tenant isolation;
7. discuss scale, operations, and evolution;
8. name tradeoffs and limitations honestly.

Use the project as evidence, not as a claim that every production problem is solved.

## Part I — The story you should be able to tell

## 1. Thirty-second version

> I designed a multi-tenant enterprise AI chat platform where PostgreSQL is
> the durable source of truth, slow model work runs asynchronously through
> leased workers, and Redis plus WebSockets provides low-latency notifications
> without owning business state. A user turn, idempotency receipt, queued run,
> conversation version, and outbox event commit atomically. Workers use lease
> fencing, bounded retries, deterministic pre-provider governance, versioned
> prompt profiles, and durable human review. The browser treats WebSockets as
> a signal and refetches authoritative content through REST.

## 2. Two-minute version

> The API validates a signed JWT, derives tenant and actor identity from trusted
> claims, authorizes the conversation, and accepts a turn with an idempotency
> key. In one PostgreSQL transaction it advances the conversation with an
> optimistic compare-and-swap, appends the immutable user message, creates a
> queued agent run pinned to an assistant-profile version, stores an idempotency
> receipt, and inserts a worker outbox event. The API returns `202` without
> waiting for the model.
>
> A worker claims due work using `FOR UPDATE SKIP LOCKED`, records a lease token,
> and commits that short transaction. It loads bounded conversation context and
> the exact pinned profile. A versioned deterministic policy runs before any
> provider call. Blocked requests terminate safely; escalated requests also
> create a durable human-review case. Allowed requests call Echo or Gemini with
> a timeout while no database transaction is open. Completion uses the lease
> token as a fence, so an expired worker cannot overwrite a newer attempt.
>
> A successful or terminal completion transaction also creates a durable
> notification row. A separate relay publishes a content-free event to Redis.
> The WebSocket tells the browser that state changed; the browser refetches
> messages and run state through REST. If Redis or the socket fails, polling and
> PostgreSQL still preserve correctness. The design is at-least-once at external
> boundaries and effectively-once for durable state through idempotency,
> uniqueness, optimistic concurrency, and fencing.

## 3. Ten-minute design-round outline

Use this order on the whiteboard:

1. Functional and non-functional requirements.
2. Capacity questions and assumptions.
3. Trust boundaries and tenant ownership.
4. Component diagram and ownership.
5. Accept-turn transaction.
6. Worker claim/call/complete path.
7. Governance, prompting, and human review.
8. Notification and browser recovery path.
9. Failure scenarios and guarantees.
10. Scaling, observability, rollout, and next steps.

## 4. The architecture to draw from memory

```text
                          ┌──────────────────────────┐
                          │ React browser            │
                          │ REST + WebSocket + poll  │
                          └────────────┬─────────────┘
                                       │ one origin
                          ┌────────────▼─────────────┐
                          │ Nginx gateway            │
                          └────────────┬─────────────┘
                                       │
                ┌──────────────────────▼──────────────────────┐
                │ FastAPI                                    │
                │ auth, authorization, commands, queries, WS │
                └───────────────┬─────────────────────────────┘
                                │ atomic commands / reads
                         ┌──────▼──────┐
                         │ PostgreSQL  │
                         │ ledger      │
                         └───┬─────┬───┘
               queued work   │     │ terminal notification
                             │     │
                       ┌─────▼─┐ ┌─▼─────┐
                       │worker │ │ relay │
                       └──┬────┘ └──┬────┘
                          │         │
                    Echo/Gemini   Redis Pub/Sub
                                      │
                                      └──► API socket replicas
```

### Ownership sentence

Say this early:

> PostgreSQL owns durable facts. The API owns transport policy. The worker owns
> bounded execution attempts. The relay owns notification attempts. Redis and
> WebSockets own immediacy, not correctness.

## Part II — Requirements and system-design framing

## 5. Functional requirements

- Create and retrieve conversations.
- Accept user turns safely under retries.
- Produce assistant responses asynchronously.
- Preserve ordered conversation history.
- Expose run status and safe failure information.
- Notify connected clients promptly.
- Support tenant-specific users and roles.
- Apply versioned input policy before provider use.
- Surface durable policy notices.
- Support compliance reporting and human review.
- Configure and activate versioned assistant profiles.

## 6. Non-functional requirements

- No cross-tenant disclosure or relationship.
- No lost acknowledged turn.
- Deterministic behavior for exact retries.
- Correctness during worker, relay, Redis, socket, or browser failure.
- Independent horizontal scaling of APIs, workers, and relays.
- Bounded latency, retries, context, payloads, and costs.
- Auditable policy/configuration decisions.
- Safe degraded behavior when optional dependencies fail.
- Operability through metrics, health checks, runbooks, and migrations.

## 7. Questions to ask before designing

### Product

- Is this internal support chat, customer-facing chat, or an agent platform?
- Is asynchronous completion acceptable?
- Are token streaming and tool execution required?
- What does “human review” mean operationally?
- Must a reviewer approve and resume work, or only classify an escalation?

### Scale

- Peak accepted turns per second?
- Concurrent WebSockets?
- Average and p99 provider latency?
- Average conversation length and retention?
- Tenants, active users, and noisy-tenant distribution?
- Provider quotas and budget?

### Consistency and reliability

- Is losing an accepted turn ever acceptable?
- What is the completion SLO?
- What duplicate external effects are tolerable?
- Required RPO and RTO?
- Is regional failover required?

### Security and compliance

- What identity provider and authorization model?
- Are tenants mutually hostile?
- Data residency, retention, deletion, legal hold, audit export?
- May prompts leave the region or organization?
- Which policy decisions require explainability?

### Strong capacity answer

Do not invent scale. Say:

> I would not finalize capacity from a diagram. I need peak turns per second,
> concurrent sockets, context size, provider latency, retention, and tenant
> skew. I expect provider latency to dominate, so API, worker, and relay pools
> scale independently. I would confirm the database queue and connection-pool
> limits with load tests before introducing a broker.

## Part III — Core technical deep dives

## 8. The strongest invariant

Memorize this:

> Once the API acknowledges a turn, the conversation update, user message,
> queued run, idempotency result, and durable work record either all exist or
> none exist.

That invariant drives the transactional outbox and most reliability decisions.

## 9. Idempotency

### Problem

The server can commit and the response can be lost. The client cannot tell
whether retrying creates a duplicate.

### Design

Scope the key by:

```text
tenant + conversation + operation + idempotency key
```

Store a canonical request fingerprint and original result IDs.

- Same key and fingerprint: return the original result with `replayed=true`.
- Same key and different fingerprint: return `409 Conflict`.
- Concurrent exact retries: serialize the receipt scope so one result wins.

### Interview distinction

Idempotency is not deduplication by message text. Two intentional identical
messages need different keys. A retry of one action reuses the same key.

## 10. Optimistic concurrency

### Problem

Two writers load conversation version 7 and both try to append sequence 8.

### Design

```sql
UPDATE conversations
SET version = 8, last_message_sequence = 8
WHERE tenant_id = :tenant
  AND id = :conversation
  AND version = 7;
```

Exactly one writer updates a row. The other observes zero rows and handles a
conflict. A version field in memory does not prevent a database race; the
conditional write does.

### When to prefer pessimistic locking

Use an explicit lock when:

- the critical section is short;
- contention is expected and retries are costly;
- a sequence or next version must be allocated atomically.

This project combines optimistic versions with targeted row locks where needed.

## 11. Transactional outbox

### Unsafe dual write

```text
commit business data
publish queue message
```

A crash between those operations loses the job.

### Safe design

Store the job/event in the same PostgreSQL transaction as the business data.
A separate process later dispatches it.

### Tradeoff

- **Benefit:** atomicity and simpler recovery.
- **Cost:** polling, table growth, relay logic, retention, and DB load.
- **Evolution:** if Kafka/SQS becomes useful, publish outbox rows to it; do not
  remove the database-to-broker bridge.

## 12. Worker leasing and fencing

### Why `SKIP LOCKED`

Multiple workers can compete without waiting on the same row. Each gets
different available work.

### Why a lease

A worker can crash after claiming. Lease expiry permits reclamation.

### Why fencing

Expiry alone does not stop a paused worker from resuming. A unique token is
stored at claim time and included in every completion predicate. A stale token
updates zero rows.

### Why the provider call is outside the transaction

- model latency can be seconds;
- long transactions consume pool connections and retain locks;
- they increase contention and vacuum pressure;
- provider timeouts should not roll back a long-held transaction.

Use two short transactions around one bounded network call.

## 13. Exactly-once language

Weak answer: “The system is exactly once.”

Strong answer:

> End-to-end exactly once is not available across PostgreSQL, Redis, and an
> external model provider. Work and publication attempts are at-least-once.
> Durable results are effectively-once because idempotency receipts, unique
> constraints, compare-and-swap transitions, and lease fencing make duplicates
> harmless or reject them.

If the provider accepted a call and the worker crashed before recording the
result, another attempt may call the provider again. Use provider idempotency
with the run ID when supported.

## 14. Retry and backpressure

### Retry only when useful

Transient:

- timeout;
- network interruption;
- HTTP `408`, `429`, or selected `5xx`;
- temporary provider unavailability.

Terminal:

- invalid request;
- unsupported content or configuration;
- malformed/unsafe provider result;
- exhausted attempt budget.

### Backoff

Use bounded exponential backoff with jitter. Bound:

- provider timeout;
- lease duration;
- max attempts;
- initial/max delay;
- worker concurrency;
- per-tenant admission and provider quota.

### Backpressure answer

> I would observe queue age, not only queue depth. I would bound worker
> concurrency, apply tenant-aware admission and fair scheduling, respect
> provider quotas, and show honest pending state. If PostgreSQL polling became
> the measured bottleneck, I would add a broker while retaining the outbox.

## 15. Realtime delivery and recovery

### Why not send full content over WebSocket?

- sockets can disconnect;
- Pub/Sub can lose messages;
- duplicates occur;
- content expands the sensitive-data surface;
- authoritative REST already enforces query authorization.

The terminal signal contains IDs and versions. The browser refetches through REST.

### Failure behavior

| Failure | Result |
|---|---|
| Redis down | Relay retries durable rows; REST polling still works |
| Duplicate publish | Browser performs another idempotent refetch |
| Missed socket event | Polling discovers terminal state |
| API socket process restarts | Browser reconnects and reconciles |
| Browser sleeps | On wake, it refetches durable state |

## 16. Multi-tenancy

### Defense layers

1. Derive tenant from validated authentication claims.
2. Do not accept trusted tenant identity from mutable request bodies.
3. Include tenant in every repository query.
4. Validate linked domain entities belong to the same tenant.
5. Use composite tenant primary/foreign keys.
6. Return safe not-found behavior for invisible cross-tenant records.
7. Separate runtime and migration DB privileges.
8. Add PostgreSQL RLS for hostile shared-database tenancy.

### Good interview phrasing

> Tenant isolation is a system invariant, not a convention that depends on
> every engineer remembering one predicate.

### RLS tradeoff

RLS adds defense in depth but also operational and testing complexity, session
context management, and possible query surprises. For a hostile multi-tenant
database, that tradeoff is usually worthwhile.

## 17. Authentication and authorization

### Authentication

- Pin the accepted JWT algorithm.
- Validate signature, issuer, audience, expiry, issued-at, subject, and tenant.
- Use an external IdP and RS256/ES256 in production.
- Keep demo login and development headers local/test-only.
- Use TLS/WSS and an intentional browser token lifecycle.

### Authorization

- Backend dependencies enforce roles.
- Admin manages profiles.
- Agent/admin can inspect governance and reviews.
- User accesses chat and owned policy notices.
- Frontend route hiding is usability only.

### Avoiding information leaks

Return an equivalent `404` for absent and cross-tenant resources where the
caller must not learn that another tenant's object exists.

## 18. AI-specific governance

### Why deterministic input policy first

- predictable cost and latency;
- explainable, versioned findings;
- simple regression testing;
- no dependency on another model;
- blocks/escalates before sending content to an external provider.

An ML moderator can be added as a second layer. Keep the deterministic baseline.

The implemented baseline normalizes Unicode with NFKC, case-folds, removes
formatting controls, normalizes whitespace, and applies order-independent
precedence: `BLOCK > ESCALATE > ALLOW`. This improves reproducibility but does
not eliminate obfuscation, multilingual, false-positive, or false-negative risk.

### First-write-wins policy assessment

The durable decision is authoritative on retries. Re-evaluating under a newer
policy could make one run allowed on the first attempt and blocked on the next.

### Human review semantics

Escalation terminalizes the run and creates one durable review. Resolution
records `confirmed` or `dismissed`; it does not secretly resume the original
request. Resumption would be a separate explicit workflow with new authorization,
idempotency, and audit requirements.

### Prompt injection answer

Do not claim prompt injection is solved. Explain layers:

- structured user messages remain untrusted;
- system instructions contain typed server-owned fields only;
- arbitrary admin/user text is not inserted into trusted instructions;
- deterministic policy blocks known high-risk patterns;
- sensitive cases escalate;
- provider output remains untrusted and is safely rendered;
- logs and Redis omit prompt content;
- ongoing evaluations and red-team testing are still required.

## 19. Bounded context and prompt construction

- Store full immutable history in PostgreSQL.
- Require positive evidence before reusing history: an allowed assessment for
  prior user content and a succeeded run for prior assistant content.
- Exclude blocked, escalated, unassessed, legacy, or orphaned messages rather
  than assuming the absence of a rejection means safe.
- Load only a bounded window for provider use.
- Retain the newest complete-message suffix.
- Do not split a message in the middle.
- Drop an orphan leading assistant response.
- Keep system instruction separate from untrusted history.
- Use a deterministic prompt fingerprint for observability without content leakage.

### Character versus token budget

The implementation uses a provider-neutral character limit. It is deterministic
but only approximates tokens. At higher scale/cost sensitivity, introduce
provider-aware tokenization behind an application port.

## 20. Immutable assistant profiles

### Design

- Append-only profile versions contain allowlisted configuration.
- One tenant assignment points to the active version.
- Activation uses an expected revision.
- Each run stores the exact profile ID/version selected at acceptance time.

### Scenario to explain

> Run R was queued under Professional v1. An administrator activates Friendly
> v2 before R executes. R still uses v1 because its row pins that version. New
> runs use v2. This protects auditability and retry stability.

### Save versus activate

Creating a version should not roll it out automatically. Separate preview,
save, and activate so administrators can review the rendered instruction and
so activation is an explicit audited event.

## 21. Database constraints as executable architecture

Examples:

- check legal run lifecycle shapes;
- positive version and sequence values;
- unique conversation sequence;
- composite tenant foreign keys;
- bounded JSON arrays and risk scores;
- one policy assessment per run;
- review only for an escalated assessment;
- immutable profile version keys;
- assignment points to a real same-tenant version.

Strong answer:

> Application checks provide useful errors; database constraints defend the
> invariant under races, alternate code paths, and future mistakes.

## Part IV — Architecture decisions and tradeoffs

## 22. Decision table

| Decision | Why | Cost/tradeoff | Revisit when |
|---|---|---|---|
| PostgreSQL ledger | Transactions, constraints, one durable truth | DB carries queue and history workload | Measured contention or regional needs |
| PostgreSQL outbox queue | Atomic with chat state | Polling and retention overhead | Queue scale demands broker dispatch |
| Async provider worker | Keeps API fast and recoverable | Eventual completion, more components | Still appropriate for slow model work |
| `SKIP LOCKED` | Simple horizontal worker competition | DB scans/locks at high scale | Queue age/DB load proves bottleneck |
| Lease fencing | Prevents stale writers | Token/expiry complexity | Required for reclaimable work |
| Redis Pub/Sub | Fast ephemeral fan-out | No replay guarantee | Fine because REST/DB recover |
| Signal + REST refetch | Small safe events, authoritative reads | Extra read per completion | Prefer correctness/privacy |
| Optimistic conversation version | Avoids long locks | Conflicts require retry | Hot conversations need serialization |
| Composite tenant keys | DB-enforced isolation | Wider indexes and joins | Keep; add RLS rather than remove |
| Deterministic policy | Explainable and testable | Less adaptive than ML | Add layered classifier/evaluation |
| Immutable profiles | Audit and stable retry | Append-only growth | Archive old versions |
| Profile pinning | Reproducible runs | More join/storage | Keep |
| Echo local provider | Deterministic and free | Not representative of model quality | Separate provider contract/eval tests |

## 23. PostgreSQL queue versus Kafka/SQS

### Why PostgreSQL is reasonable here

- business state and work ticket need one atomic commit;
- existing operational dependency;
- simple queryability and recovery;
- current scale supports indexed polling;
- `SKIP LOCKED` supports competing workers.

### When a broker becomes attractive

- very high sustained dispatch throughput;
- many independent consumer groups;
- long retention/replay requirements;
- cross-region event distribution;
- database queue scans affect OLTP latency.

### Principal-level answer

Do not frame migration as a rewrite. Keep the transactional outbox and add a
publisher that transfers events to the broker. Roll consumers over gradually,
measure duplicates/lag, and retain reconciliation.

## 24. REST polling versus WebSocket-only

WebSocket-only is lower-read but fragile. REST-only is simpler but less
responsive. The hybrid provides:

- WebSocket latency;
- REST authorization and authoritative data;
- polling recovery;
- safe duplicate handling;
- smaller notification payloads.

## 25. Build versus buy questions

### Identity

Buy/use a real IdP. Do not build password, MFA, recovery, federation, and user lifecycle.

### Queue/broker

Start with PostgreSQL if it meets measured requirements. Adopt managed
Kafka/SQS/Pub/Sub when scale or topology justifies it.

### Policy/moderation

Keep organization-specific deterministic policy in-house. Evaluate managed
moderation as an additional signal, with privacy and explainability review.

### Model provider

Hide providers behind a neutral port. Avoid leaking SDK objects into domain/application code.

## Part V — Scaling and operations

## 26. Scaling path

### 10× traffic

1. Measure API latency, queue age, transaction duration, provider utilization,
   socket counts, and tenant skew.
2. Scale workers first if provider latency dominates.
3. Tune indexes, pool sizes, batch sizes, and polling intervals.
4. Add tenant-aware quotas and fair scheduling.
5. Scale API and relay replicas independently.
6. Partition/archive large outbox and history tables when measured.
7. Use read replicas only for queries that tolerate replica lag.
8. Add broker dispatch when database contention is proven.

### Hot conversation

A single conversation is a serialization boundary because message ordering is
strict. Options:

- reject/return conflict and let the client retry;
- queue commands per conversation;
- use a short row lock;
- accept only one in-flight user turn by product policy.

Do not sacrifice deterministic order silently.

### Noisy tenant

- per-tenant quotas;
- weighted/fair worker scheduling;
- tenant-specific concurrency limits;
- budget/rate visibility;
- isolation tiers for very large customers.

## 27. SLOs

Agree with product before choosing numbers. Candidate indicators:

- accepted-turn availability;
- p95 command acceptance latency, excluding model completion;
- p95 accepted-to-terminal duration;
- oldest queued-job age;
- terminal notification lag;
- WebSocket successful connection rate;
- pending human-review age;
- cross-tenant incidents: zero tolerance.

### Error budget use

If completion latency burns the budget, slow feature rollout and prioritize
provider capacity, queue health, or worker reliability. An SLO should change
decisions, not decorate a dashboard.

## 28. Metrics and alerts

### API

- request rate, latency, status;
- idempotency replay/conflict;
- conversation CAS conflict;
- auth/authorization rejection.

### Worker/provider

- queue depth and oldest age;
- attempts and lease loss;
- success/terminal failure;
- provider latency, timeout, `429`, `5xx`;
- context size and omitted-message count;
- policy allow/block/escalate.

### Relay/realtime

- terminal outbox backlog and oldest age;
- publish retries and latency;
- Redis disconnects;
- active sockets, auth failures, origin rejections;
- REST polling fallback volume.

### Governance

- pending review count and oldest age;
- resolution rate/SLA;
- profile activation conflict;
- policy version distribution.

### Database

- pool saturation;
- transaction/lock duration;
- query latency and deadlocks;
- storage growth and vacuum health;
- replication lag and backup status.

## 29. Deployment and migration strategy

Use expand-and-contract:

1. add nullable/additive schema;
2. deploy code compatible with old and new shape;
3. backfill in bounded batches;
4. switch writes/reads;
5. enforce constraints/non-null;
6. remove old shape in a later release.

Separate migration and runtime roles. Preview offline SQL. Prefer forward fixes
over destructive production downgrades.

## 30. Disaster recovery

Be ready to discuss:

- PostgreSQL backups and point-in-time recovery;
- agreed RPO/RTO;
- restore drills, not just backup jobs;
- rebuilding Redis from nothing;
- replay/reconciliation of pending outbox work;
- uncertain provider calls after restore;
- secret/key recovery and rotation;
- regional failover and DNS/traffic strategy;
- consistency of external audit exports.

## Part VI — Testing strategy

## 31. Test pyramid

| Layer | Examples |
|---|---|
| Domain | state transitions, typed IDs, tenant mismatch, UTC, policy precedence |
| Application | idempotent replay, auth-before-side-effect, retry classification, prompt selection |
| Models/mappers | metadata constraints and lossless domain conversion |
| Repositories | tenant predicates, locks, CAS, bounded ordering |
| PostgreSQL integration | real FKs/checks/grants, concurrent writers, fencing, rollback |
| API | `401/403/404/409/422`, strict bodies, safe errors, no client audit identity |
| Frontend | role navigation, stale response suppression, conflict refresh, safe rendering |
| System | full Docker path, Redis/provider failures, reconnect/poll recovery |

## 32. How to test races

Strong answer:

> I use two real PostgreSQL transactions with the same expected version or
> revision and assert exactly one commits. For fencing, I let worker A's lease
> expire, reclaim with worker B, and prove A's token cannot complete. Mocks are
> useful for orchestration but cannot prove database concurrency behavior.

Races to test:

- same idempotency key concurrently;
- conversation CAS conflict;
- two workers claiming work;
- stale lease completion;
- two reviewers resolving one case;
- two admins activating one revision;
- migration and old/new application compatibility.

## 33. Failure injection

Inject failures:

- before transaction commit;
- after commit but before HTTP response;
- after work claim;
- during provider call;
- after provider response but before completion;
- after Redis publish but before marking published;
- during WebSocket disconnect;
- during profile activation or review resolution.

For each point, state the durable facts, next owner, duplicate possibility, and recovery path.

## Part VII — Staff and Principal leadership signals

## 34. Senior versus Staff versus Principal answers

| Level signal | Typical focus |
|---|---|
| Senior | Implements a robust component and handles its edge cases |
| Staff | Defines cross-component invariants, leads design, sequencing, and adoption |
| Principal | Aligns multiple teams/domains, creates evolution strategy and reusable standards |

To elevate an answer:

- connect decisions to business/security risk;
- explain alternatives and decision criteria;
- define ownership and interfaces;
- describe rollout/migration, not just end state;
- establish SLOs and operational accountability;
- show how other teams extend the design safely;
- distinguish current evidence from future proposals.

## 35. A decision record format

Use this in interviews and real work:

```text
Context:     What problem/risk exists?
Decision:    What are we doing?
Invariant:   What must always remain true?
Alternatives:What else did we consider?
Tradeoffs:   What cost did we accept?
Evidence:    What test/metric validates it?
Rollout:     How do we introduce it safely?
Revisit:     What measurable trigger changes the decision?
```

## 36. Behavioral story framework

Use STAR plus influence and learning:

```text
Situation  → technical and business risk
Task       → decision/outcome you owned
Action     → alternatives, alignment, sequencing, delegation
Result     → evidence and reduced risk
Learning   → limitation and next improvement
```

Do not invent a team or production impact. If this was a learning project,
say so and focus on how you reasoned, tested, documented, and would lead adoption.

## 37. Story prompts from this project

### Preventing lost work

- Risk: database update and background dispatch diverge.
- Decision: transactional outbox.
- Tradeoff: relay/polling complexity.
- Evidence: rollback and crash-window tests.
- Broader standard: every cross-boundary event uses a durable handoff.

### Making tenant safety structural

- Risk: one forgotten predicate exposes data.
- Decision: trusted tenant context + scoped repositories + composite keys.
- Tradeoff: wider keys and more verbose joins.
- Next: RLS as defense in depth.

### Avoiding configuration drift

- Risk: profile changes alter queued/retried work.
- Decision: immutable versions pinned to runs.
- Result: reproducible execution and rollback through pointer movement.

### Governance before cost/exposure

- Risk: unsafe content sent to external model before policy.
- Decision: deterministic policy before provider, durable first decision.
- Result: explainable block/escalation and human workflow.

### Incremental delivery

Explain the sequence from pure domain through persistence, worker, realtime,
governance, review, prompting, profiles, admin UI, and operations. This shows
risk-first decomposition and reversible delivery.

## 38. Handling disagreement

Example:

> A proposal put full assistant messages on Redis for lower latency. I agreed
> with the latency goal but separated it from the recovery requirement. Pub/Sub
> cannot guarantee replay, and content would increase exposure. I proposed a
> content-free signal plus REST refetch, documented the extra-read tradeoff,
> and would validate latency with a prototype and measurements.

Good disagreement habits:

- align on the invariant and decision criteria;
- separate goals from proposed mechanisms;
- make tradeoffs explicit;
- prototype disputed performance assumptions;
- record the decision and revisit trigger;
- disagree without making ownership personal.

## 39. Mentoring and organizational leverage

For this project, reusable teaching artifacts include:

- explicit domain language;
- ledger/mailbox/doorbell analogy;
- architecture and failure diagrams;
- invariant-focused tests;
- small vertical commits;
- runbook and checklists.

In an organization, use these in design reviews, onboarding, paved-road
libraries, migration templates, and service scorecards. Principal impact is
often the safe behavior that many teams adopt, not the amount of code one person writes.

## Part VIII — Likely questions and strong answer outlines

## 40. System-design questions

### “Design an enterprise AI chat platform.”

1. Clarify tenants, latency, history, compliance, provider, and scale.
2. Separate synchronous acceptance from async completion.
3. Choose PostgreSQL as ledger and transaction boundary.
4. Use idempotency, conversation CAS, and transactional outbox.
5. Explain leased worker and provider boundary.
6. Add policy, bounded prompts, human review, profile pinning.
7. Add terminal outbox, Redis/WebSocket signal, REST recovery.
8. Finish with SLOs, isolation, testing, scale, and evolution.

### “Why PostgreSQL rather than Kafka?”

At current requirements, atomic chat-state + work creation and lower
operational complexity matter more than broker throughput. `SKIP LOCKED` meets
the current queue model. Add a broker when measured contention, consumer count,
or topology demands it, while retaining the outbox.

### “Where are the consistency boundaries?”

- Strong transaction: accept-turn durable facts.
- Strong conditional transaction: worker completion under lease token.
- Eventual: provider result availability and UI notification.
- At-least-once: provider attempt and Redis publish around crash windows.
- Authoritative read: PostgreSQL through REST.

### “How would you support token streaming?”

Do not weaken durable final-state guarantees. Treat streaming as a separate
ephemeral channel with sequence IDs and cancellation. Persist final output and
terminal state atomically. Decide whether partial tokens are recoverable,
billable, or auditable; if they must be durable, add a separate append log.

### “How would you add RAG?”

Keep ingestion/indexing/retrieval as a separate bounded context. Define tenant-
scoped document ACLs, index versioning, provenance, deletion propagation,
retrieval budgets, prompt separation, and evaluation. The chat ledger should
reference retrieval evidence rather than hide document authority inside messages.

### “How would you add tools?”

Introduce explicit tool intents, authorization, policy, approval, idempotency,
timeouts, sandboxing, audit, and compensation. Never let arbitrary model text
directly execute side effects. Separate read-only from mutating tools.

## 41. Distributed-systems questions

### “What happens if the API commits but the response is lost?”

The client retries the same body/key. The durable receipt returns the original IDs.

### “What happens if a worker crashes after the provider responds?”

The DB may not contain the result. After lease expiry another worker retries,
possibly calling the provider again. The stale token cannot write. Use provider
idempotency when available.

### “What happens if the relay publishes twice?”

The browser may receive a duplicate event and performs another safe REST refetch.

### “What is the difference between a lock and a lease?”

A database lock is tied to a transaction/connection and should be short. A
lease is durable ownership with an expiry across a long external operation.
Fencing is required because an expired owner may resume.

### “How do you preserve message order?”

Server-assigned conversation-local sequence, unique constraint, and
conversation-version CAS. Timestamps are metadata, not the ordering authority.

### “How do you handle poison work?”

Classify failures, bound attempts, back off, terminalize safely, alert on
exhaustion/age, and optionally add an operator dead-letter/replay workflow with
explicit authorization and idempotency.

## 42. Security questions

### “How do you stop tenant A reading tenant B?”

JWT-derived tenant, no trusted body tenant, scoped queries, domain checks,
composite FKs, safe not-found responses, cross-tenant tests, runtime privilege
limits, and production RLS.

### “JWT is valid. Is the request authorized?”

No. Authentication establishes identity. Authorization still checks roles,
resource ownership, operation, and current state.

### “Why not put the token in the WebSocket URL?”

URLs leak through logs, history, proxies, and telemetry. Authenticate in the
first frame over WSS, with a short timeout and origin validation.

### “How do you secure model prompts?”

Typed server-owned instruction fields, structured untrusted context, policy
before provider, bounded context, safe rendering, no prompt logs, output treated
as untrusted, and ongoing evaluation/red-team tests.

## 43. Reliability questions

### “What happens when Redis is down?”

Relay keeps durable pending notifications and retries. WebSocket immediacy is
degraded; polling/REST still returns the durable result. Readiness policy should
reflect whether realtime is a required dependency for traffic.

### “What would you alert on first?”

Oldest queued job age, accepted-to-terminal SLO burn, PostgreSQL readiness,
provider failure/latency, relay oldest age, and pending-review SLA.

### “How do you deploy schema changes safely?”

Expand, backfill, deploy compatible code, enforce, then contract. Separate
migrator privileges and prefer forward fixes.

### “How would you recover after a database restore?”

Restore to the agreed point, verify schema, reconcile outbox and terminal state,
restart stateless services, account for external calls whose results may be
uncertain, monitor duplicate attempts, and validate tenant/audit correctness.

## 44. Governance questions

### “Why deterministic policy instead of an LLM judge?”

Explainability, latency, cost, regression stability, availability, and privacy.
Use an ML judge as a layered signal if its quality and data handling are acceptable.

### “Can a reviewer approve and continue an escalated run?”

Not in this design. Resolution records judgment only. Continuing requires a new
explicit, authorized, idempotent workflow; silently resuming would blur audit
and user intent.

### “Why immutable profiles?”

Auditability, reproducible retries, clear rollout, and rollback by moving an
active pointer rather than rewriting history.

## 45. Leadership questions

### “What makes this Staff-level?”

- cross-component invariants;
- failure-mode-driven architecture;
- layered tenant/security controls;
- independent scaling boundaries;
- migration and rollout strategy;
- operational ownership and SLOs;
- reusable patterns and documentation.

### “What would you improve with six more months?”

Prioritize risk:

1. CI required checks and branch protection.
2. External IdP, asymmetric keys, secrets management, and RLS.
3. SLO dashboards, load/chaos tests, and restore drills.
4. Retention/privacy/audit lifecycle.
5. Provider cost, quota, idempotency, and evaluation controls.
6. Fair scheduling and tenant isolation tiers.
7. Broker only when measurements justify it.

### “What did you choose not to build?”

RAG, tool execution, token streaming, billing, and document ingestion remain
separate bounded contexts. This is intentional scope control, not accidental omission.

## Part IX — Honest limitations

## 46. What this project proves

- coherent domain and state models;
- tenant-scoped transactional persistence;
- idempotency and optimistic concurrency;
- recoverable asynchronous work with fencing;
- realtime signal plus REST recovery;
- deterministic governance and durable review;
- bounded prompting and immutable profiles;
- layered automated tests and local operations.

## 47. What it does not yet prove

- production traffic volume or cost;
- multi-region active-active correctness;
- external IdP lifecycle;
- hostile-tenant RLS deployment;
- formal compliance certification;
- production backup/restore execution;
- provider quality, safety, or retention guarantees;
- comprehensive CI/CD and supply-chain controls;
- sustained chaos/load behavior.

One explicit tradeoff is availability versus quota enforcement: Redis-backed
rate limiting fails open when Redis errors, preserving durable chat while
temporarily weakening abuse protection. A production choice should be tied to
the product's threat model and may add a local fallback limiter.

Say this confidently. Principal engineers distinguish evidence from aspiration.

## Part X — Practice drills

## 48. Whiteboard drills

Time each drill.

### Five minutes

- Draw components and ownership.
- Explain one accepted turn.
- Explain why Redis is not authoritative.

### Ten minutes

- Walk every worker crash point.
- Explain idempotency versus optimistic concurrency.
- Explain lease versus fencing.

### Fifteen minutes

- Threat-model tenant isolation.
- Design profile rollout and rollback.
- Design human-review SLAs and privacy.

### Thirty minutes

- Scale from one region to 10× traffic.
- Add a broker without losing transactional correctness.
- Add RAG or tools as a separate bounded context.

## 49. Failure matrix exercise

For each row, answer: what committed, can it duplicate, who retries, what does
the user see, and what metric alerts?

| Failure point | Your answer notes |
|---|---|
| Before accept-turn commit | |
| After commit before HTTP response | |
| After worker claim | |
| During provider call | |
| After provider response before DB completion | |
| After terminal row commit | |
| After Redis publish before relay mark | |
| During socket disconnect | |
| During profile activation conflict | |
| During human-review resolution conflict | |

## 50. Flashcards

1. **Why transactional outbox?** Atomic business state and durable work creation.
2. **Idempotency scope?** Tenant + conversation + operation + key.
3. **Same key, changed request?** Conflict.
4. **Ordering authority?** Conversation-local server sequence, not timestamp.
5. **Why optimistic CAS?** Detect stale concurrent writers without long locks.
6. **Why `SKIP LOCKED`?** Competing workers take different rows without waiting.
7. **Why lease?** Reclaim work after worker failure.
8. **Why fence?** Prevent expired worker's stale write.
9. **Why no DB transaction during provider call?** Avoid long locks/pool use.
10. **Delivery guarantee?** At-least-once attempts; effectively-once durable state.
11. **Redis role?** Ephemeral doorbell, not ledger.
12. **WebSocket payload?** IDs/status/version, no content.
13. **Recovery after missed signal?** REST polling/refetch.
14. **Tenant source?** Validated authentication context.
15. **DB tenant defense?** Composite keys/FKs, optionally RLS.
16. **Policy timing?** Before provider call.
17. **Retry policy decision?** Reuse durable first-write-wins assessment.
18. **Escalation effect?** Terminal run plus durable review; no provider.
19. **Review resolution?** Audit judgment, no automatic resume.
20. **Trusted prompt input?** Typed server-owned profile fields only.
21. **Context strategy?** Newest complete-message suffix.
22. **Why immutable profiles?** Auditability and reproducible retries.
23. **Why pin run profile?** Activation changes affect only future runs.
24. **Deployed JWT?** External IdP, asymmetric algorithm, pinned claims.
25. **Safe migration?** Expand, backfill, compatible deploy, enforce, contract.
26. **Best queue metric?** Oldest job age tied to SLO.
27. **When broker?** Measured DB bottleneck or topology/consumer requirement.
28. **Current major security gap?** Production IdP/RLS/secrets/TLS hardening.
29. **Current scope exclusions?** RAG, tools, streaming, billing, ingestion.
30. **Staff/Principal signal?** Invariants, influence, rollout, operations, evolution.

## Part XI — Study plan

## 51. Seven-day intensive plan

### Day 1 — Narrative and diagram

- Recite 30-second and two-minute versions.
- Draw architecture three times without notes.
- Explain ledger/mailbox/doorbell analogy.

### Day 2 — Transactional command path

- Idempotency receipt.
- Conversation CAS.
- Transactional outbox.
- Practice the lost HTTP response scenario.

### Day 3 — Worker and distributed systems

- Claim, lease, fence.
- Retry/backoff.
- Exactly-once language.
- Walk five crash points.

### Day 4 — Security and AI governance

- JWT and authorization.
- Tenant layers and RLS.
- Policy, review, prompt trust, profile pinning.
- Name current limitations.

### Day 5 — Scale and operations

- SLOs, metrics, alerts.
- PostgreSQL/broker evolution.
- migration and disaster recovery.

### Day 6 — Leadership

- Prepare four truthful stories.
- Include alternatives, alignment, evidence, and learning.
- Practice a disagreement and a migration proposal.

### Day 7 — Mock loop

- 45-minute design round.
- 30-minute technical deep dive.
- 30-minute behavioral round.
- Review only missed concepts.

## 52. Fourteen-day spaced plan

Use the seven topics twice. On the second pass:

- answer without notes;
- add capacity assumptions;
- state one alternative and revisit trigger;
- add one metric and one failure test;
- explain how multiple teams would adopt the decision.

## 53. Interview-day checklist

Before answering:

- [ ] Clarify requirements and exclusions.
- [ ] State assumptions instead of inventing facts.
- [ ] Name the durable source of truth.
- [ ] Define ownership boundaries.
- [ ] Walk the main write path.
- [ ] Describe consistency and delivery guarantees precisely.
- [ ] Cover tenant/security boundaries.
- [ ] Discuss failure and recovery.
- [ ] Add scale, observability, and rollout.
- [ ] Name a tradeoff and measurable revisit trigger.
- [ ] Separate implemented evidence from future design.

## 54. Self-scoring rubric

Score each answer from 0 to 2:

| Area | 0 | 1 | 2 |
|---|---|---|---|
| Requirements | Jumps to tools | Some questions | Clear functional/NFR/scale framing |
| Invariants | None | Implied | Explicit and drives design |
| Data ownership | Ambiguous | Mostly clear | Authoritative owner per component |
| Failure semantics | Happy path | Some failures | Crash windows and recovery precise |
| Security | JWT only | App checks | Layered trust, tenant, DB, transport |
| Scale | “Add servers” | Component scaling | Metrics, bottlenecks, evolution triggers |
| Operations | Logs only | Metrics/health | SLO, alerts, deploy, DR |
| Tradeoffs | One solution | Alternative named | Decision criteria and revisit signal |
| Leadership | “I coded” | Owned design | Alignment, leverage, rollout, learning |
| Honesty | Overclaims | Some caveats | Evidence and gaps clearly separated |

A strong Staff/Principal answer consistently scores 16–20 without becoming
needlessly complicated.

## 55. Final mental model

When you get stuck, return to these questions:

1. What fact must survive every crash?
2. What must be atomic?
3. What can be duplicated?
4. How is stale ownership fenced?
5. Who is trusted to supply identity and policy?
6. How does a tenant boundary survive a coding mistake?
7. How does the system degrade when an optional component fails?
8. What metric proves the design meets the user promise?
9. How can the architecture evolve without a rewrite?
10. How will other engineers understand and safely extend it?

Answer those clearly and you will sound like an engineer who can own systems,
not merely implement endpoints.

## Related project material

- [Enterprise AI Platform Cheatsheet](enterprise-ai-platform-cheatsheet.md)
- [Full-stack architecture](../architecture/full-stack.md)
- [Chat domain contract](../architecture/chat-domain.md)
- [Operations runbook](../operations.md)
- [Repository README](../../README.md)
