# Decision Log

Each decision lists: **Decision**, *Alternatives considered*, *Why*, *Trade-off / risk*, *Where in the code*.
Updated at the end of every phase. Decisions are grouped by area.

---

## A. Technology stack

### A1. Backend: Python 3.12 + FastAPI
- **Alternatives:** Node.js + TypeScript (Express/Fastify + kafkajs), Java + Spring Boot.
- **Why:**
  - **Exact money:** `decimal.Decimal` is in the standard library, and asyncpg maps Postgres `NUMERIC` straight to `Decimal`, so no float ever touches an amount. In Node, `pg` returns NUMERIC as a string and you need `decimal.js` or to store integer agorot.
  - **Validation:** Pydantic v2 validates types and ranges on its own and returns field-level errors, which covers the "clear error messages" requirement.
  - **API contract:** FastAPI generates OpenAPI and Swagger UI (`/docs`) from the code, so the contract can't drift from the implementation (the assignment asks for documentation of the API contract).
  - **Readability:** short code that's easy to explain in the interview.
- **Trade-off:** a different language from the frontend (no shared types). Python is slower than Java/Go, which doesn't matter at this scale.
- **Where:** `backend/`.

### A2. Frontend: React + Vite + TypeScript, built to static files and served by nginx
- **Alternatives:** plain HTML/JS, Next.js, serving the frontend from FastAPI.
- **Why:** both screens need rich state (filters, polling, a drawer, a timeline). Vite builds fast. The build output is static, so nginx serves it in a small image. nginx also proxies `/api` to the backend, so the page and the API share one origin: no CORS and no hardcoded API URL in the frontend.
- **Trade-off:** a build step (a multi-stage Dockerfile with Node only at build time). Next.js would add SSR we don't need.
- **Where:** `frontend/` (phase 4).

### A3. One backend image, several roles (migrate / api / worker / tests)
- **Alternatives:** a separate image per service, or a single process that runs both the API and the consumer.
- **Why:**
  - **API and worker are separate containers**, so the line between "request accepted" and "deposit processed" is a real process boundary, the way the assignment describes it. A worker crash doesn't take the API down, and the worker can be scaled on its own (`--scale worker=2`).
  - **One image** means shared code (config, DB, schemas), one build and one `requirements.txt`. Only the `command` in compose decides the role.
- **Trade-off:** each container carries code it doesn't run (a few KB, not significant).
- **Where:** `backend/Dockerfile`, the `command:` lines in `docker-compose.yml`.

---

## B. Database (PostgreSQL)

### B1. PostgreSQL 16 (`postgres:16-alpine`)
- **Alternatives:** MySQL 8, MongoDB.
- **Why:** everything we need is built in:
  - ACID transactions: credit, balance and status in one commit.
  - `NUMERIC(18,2)`: exact decimal arithmetic.
  - `SELECT ... FOR UPDATE`: row locks that prevent lost updates and races.
  - `INSERT ... ON CONFLICT DO NOTHING RETURNING`: race-free idempotency in a single statement.
  - `CHECK` and `UNIQUE` constraints, FKs and triggers: invariants enforced by the DB itself, not only by our code.
  - MySQL has no `RETURNING` on `INSERT ... ON DUPLICATE KEY`, so idempotency takes more code. MongoDB needs a replica set for multi-document transactions and has no FKs or CHECKs, which is a weak fit for a ledger.
- **Why alpine:** a small image (about 80MB versus about 400MB) that downloads faster on the reviewer's machine.
- **Trade-off:** none significant for this use case.

### B2. Money as `NUMERIC(18,2)`, never float
- **Alternatives:** `DOUBLE PRECISION`, `BIGINT` agorot.
- **Why:** the assignment requires exact representation. `NUMERIC(18,2)` is exactly 2 decimal places, with up to 16 digits before the point (about 10 quadrillion ₪), and maps directly to Python `Decimal`. Integer agorot would also work, but it's less readable in the DB and in the API.
- **Trade-off:** NUMERIC is slower than integers, which isn't noticeable here.
- **Where:** `accounts.balance`, `deposits.amount`, `credits.amount/balance_after`.

### B3. A separate `credits` table (ledger) in addition to `accounts.balance`
- **Alternatives:** only update the balance; or only keep a ledger and compute the balance with SUM.
- **Why:** both. `balance` gives a fast current-balance read, and `credits` is an audit record of every credit (with `balance_after`). `UNIQUE(deposit_id)` on credits is the **last line of defense** against a double credit: even if every check in the code failed, the DB would refuse a second credit for the same deposit.
- **Trade-off:** data is duplicated (balance and ledger), so they must be updated in the same transaction (see D2).

### B4. `opening_balance` column on accounts
- **Why:** it makes the invariant verifiable at any moment with SQL: `balance = opening_balance + SUM(credits.amount)`. We use it in checks and tests to prove that no credit was lost or duplicated.
- **Where:** `schema.sql`, the verification queries in the README.

### B5. Identifiers: UUID for deposits, readable TEXT for employees and accounts
- **Alternatives:** BIGSERIAL everywhere.
- **Why:**
  - **Deposits:** a UUID is generated in the application before the insert. It's unguessable and unique across systems, and it serves as the **correlation ID** that flows through the API, the Kafka message, the DB and the logs.
  - **Employees and accounts:** readable IDs (`E-1001`, `ACC-001`) make the demo, the logs and the investigation clearer.
  - **Events and credits:** UUIDs, so the seed can use fixed IDs with `ON CONFLICT DO NOTHING`.
- **Trade-off:** a UUID index is larger than a bigint one, which doesn't matter at this scale.

### B6. An append-only `deposit_events` table as the processing history
- **Alternatives:** only keep the current status on the deposit; or a generic audit log.
- **Why:** the investigation screen needs a full timeline (received, attempt, failure, retry request, completion) with times and actors. Rows are only ever inserted, never updated, so history is never lost. `actor_employee_id` records **who** asked for a retry without overwriting `deposits.employee_id` (the original initiator). `details JSONB` holds context such as the failure code, the dispatch_id and balance_after without needing a column for each.
- **Trade-off:** event types are defined by a CHECK, so a new type needs a schema change (which is deliberate, to keep the event types controlled).

### B7. Timestamps from `clock_timestamp()` in the DB
- **Alternatives:** `now()`, times from the application.
- **Why:** a single clock (the DB) for every component, so the timeline order is reliable even if the API and worker containers' clocks differ. `now()` returns the start time of the transaction, so several events inside one transaction would get the **same** time; `clock_timestamp()` returns the real time of each row.
- **Where:** column defaults in `schema.sql`.

### B8. Constraints in the DB (defense in depth)
- `CHECK (amount > 0)`, `CHECK (balance >= 0)`, a CHECK on allowed statuses, `simulate_delay_seconds BETWEEN 0 AND 30`.
- `failed_has_retryable_flag`: every FAILED must state whether it's retryable or permanent.
- **Immutability trigger:** blocks any UPDATE of employee, account, amount, idempotency_key, request_hash or created_at on a deposit (the assignment: "changing the employee, amount or account is not an allowed operation").
- **Why:** even a bug in the code, or someone running SQL by hand, can't break the business rules.
- **Trade-off:** the logic lives in two places (code and DB). The DB is the safety net.

### B9. Migrations: idempotent SQL in a one-shot `migrate` container
- **Alternatives:** Alembic, `docker-entrypoint-initdb.d` (runs only on an empty volume), migrations at API startup.
- **Why:** `CREATE ... IF NOT EXISTS` and `CREATE OR REPLACE` are safe to run on every startup. A separate container runs **before** the API and the worker (`service_completed_successfully`), so they never start without a schema. `pg_advisory_xact_lock` prevents two concurrent runs from colliding. Unlike `initdb.d`, schema changes also take effect on an existing volume.
- **Trade-off:** there are no versioned migrations or rollback. Alembic would be needed in production.
- **Where:** `backend/app/migrate.py`, `backend/app/sql/`.

### B10. Synthetic, idempotent seed data
- **Why:** the assignment requires automatic loading on the first run, and a rerun must not reset balances or create duplicates. Every row has a **fixed ID**, and `ON CONFLICT DO NOTHING` skips rows that already exist, so an existing balance is never overwritten. The seed includes 4 historical deposits (completed, completed after a retry, transient failure, permanent failure), so the investigation screen has content right away. Names are in Hebrew to match the Hebrew UI.
- **Seed contents:** 5 employees and 6 accounts, one of them **BLOCKED** (to demo rejection), one with a zero balance, and one business account.

---

## C. Kafka

### C1. Real Kafka, `apache/kafka:3.8.0`, KRaft mode, one broker
- **Alternatives:** Kafka + ZooKeeper (Confluent/Bitnami images), Redpanda (Kafka-compatible), a cluster of 3 brokers.
- **Why:**
  - The assignment requires **real Kafka**. The official Apache image is the real thing, with no vendor layer.
  - **KRaft:** since Kafka 3.3 the broker manages its own metadata without ZooKeeper. One container instead of two means less memory and a faster startup.
  - **One broker:** enough for a local demo. Three brokers would triple the memory use without adding anything to the demo.
- **Trade-off:** no high availability. If the broker goes down, nothing is processed (the API keeps accepting requests thanks to the outbox; see D4). In production you'd want at least 3 brokers.

### C2. 3 partitions on the `deposit-requests` topic
- **Alternatives:** 1 partition; many partitions (12+).
- **Why 3:**
  - **A partition is the unit of parallelism in Kafka.** In a consumer group, each partition goes to exactly one consumer. With 1 partition, a second worker would sit idle. With 3, we can run up to 3 workers in parallel (`--scale worker=3`), and Kafka splits the partitions between them automatically (rebalance).
  - **Ordering per account is preserved** because key = account_id (C3): all of an account's messages land on the same partition, and therefore with one consumer, in order.
  - **Small and cheap:** each partition costs files, memory and a leader election. 3 is enough to demonstrate scaling without loading one local broker.
  - **You can't reduce partitions later** (only add them), so it's better to start small and grow.
- **Trade-off:** with only 3 partitions, the maximum is 3 parallel workers. Adding partitions later changes the key → partition mapping, and that temporarily breaks the per-account ordering guarantee during the transition. With a single worker (the default), the worker handles all 3 partitions, one message at a time.
- **Where:** `kafka-init` in `docker-compose.yml`.

### C3. Message key = account_id
- **Alternatives:** key = deposit_id, no key (round-robin).
- **Why:** Kafka guarantees ordering only within a partition. Keying by account sends all deposits for one account to the same partition, so they're processed in order. That reduces contention on the account row lock and makes the processing order intuitive in the investigation.
- **Trade-off (hot partition):** a busy account or a long delay (the demo) holds up the other accounts that share its partition. The DB lock protects correctness anyway, so the key is an ordering and performance choice, not a correctness one.

### C4. Replication factor 1
- **Why:** a single broker can't hold more than one replica. The internal topics (`__consumer_offsets`, transactions) are also set to 1 explicitly (`KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1`), or Kafka would fail to create them.
- **Trade-off:** losing the broker's volume means losing messages that weren't yet processed. The outbox in the DB (D4) and the PENDING watchdog (E3) let us re-send them.

### C5. Topics are created explicitly (`kafka-init`), auto-create is off
- **Alternatives:** `auto.create.topics.enable=true`, creating topics from the code (AdminClient).
- **Why:** with auto-create, a typo in a topic name silently creates a new topic with default settings (1 partition), and messages get "lost" there. Explicit creation with `--if-not-exists` is idempotent, documented and controls the partition count.
- **Trade-off:** one more short-lived container in compose.

### C6. Fixed `CLUSTER_ID`
- **Why:** in KRaft, the data directory is "stamped" with the cluster ID when it's formatted. If the ID changed between runs, the broker would refuse to start on the existing volume. A fixed value (22-character base64) means a restart reuses the existing data. It isn't a secret.

### C7. Listeners: an internal `kafka:9092` and an external `localhost:9094`
- **Why:** the containers talk over the Docker network at `kafka:9092`. A separate EXTERNAL listener on 9094 lets you connect from the host (debugging tools) without breaking the addresses the broker hands to the containers (advertised listeners). The CONTROLLER (9093) is internal to KRaft only.

### C8. Messages carry only IDs (a command), and the DB is the source of truth
- **Alternatives:** a message carrying all the data (amount, account, employee).
- **Why:** the message is `{deposit_id, dispatch_id, reason, schema_version}`. The worker reads the amount and account from the DB, so the message can't contradict the DB or change a deposit's details. It also keeps sensitive data out of Kafka.
- **Trade-off:** a DB read for every message (cheap).

---

## D. Reliability and consistency

### D1. At-least-once delivery + an idempotent consumer
- **Alternatives:** at-most-once (auto-commit), exactly-once with Kafka transactions.
- **Why:** the offset is committed **only after** the DB work finishes, so a worker crash means the message is redelivered and nothing is lost. Duplicates are harmless because of three layers: (1) a status check under `FOR UPDATE` (only PENDING gets processed), (2) `current_dispatch_id` (an old message is ignored), (3) `UNIQUE(credits.deposit_id)`. Kafka exactly-once only covers Kafka→Kafka; it doesn't cover the write to Postgres, so idempotency in the DB is needed regardless.
- **Trade-off:** duplicates do arrive. They're recorded as `DUPLICATE_IGNORED` / `STALE_MESSAGE_IGNORED` events (which is useful in an investigation).

### D2. Credit + balance + status in one DB transaction
- **Alternatives:** separate updates, a saga, optimistic locking with a version column.
- **Why:** this is the core invariant: **COMPLETED ⇔ a credits row exists ⇔ the balance was increased**. All three are written in one commit or none of them are. So a PROCESSING deposit **never** means it was credited, and recovery can safely mark it FAILED.
- **Trade-off:** the account row is locked for a few milliseconds while the credit is written.

### D3. No lost updates: `UPDATE accounts SET balance = balance + :amount`
- **Alternatives:** read the balance in code, add, and write it back (read-modify-write).
- **Why:** read-modify-write loses updates when two deposits arrive together (both read 100, both write 100+x). An atomic UPDATE in the DB takes a row lock, and the second waits for the first and sees its result. Together with `SELECT account FOR UPDATE` before the update, we also check the account status (blocked) under the same lock.

### D4. Transactional outbox
- **Alternatives:** publish to Kafka from the API right after the DB commit; publish before the commit.
- **Why:** with a direct publish, a crash between the commit and the publish leaves a deposit that will never be processed (or the reverse: a message for a deposit that doesn't exist). With an outbox, the deposit and the outbox row are written in **one transaction**, and a relay publishes and marks `published_at`. Bonus: the API keeps returning 202 even when Kafka is down, and the messages go out once it's back.
- **Trade-off:** a possible duplicate publish (published but crashed before marking), which is harmless (D1). Up to a 2-second delay in the background relay (plus an immediate publish attempt on the fast path).

### D5. Idempotency-Key: global scope, no TTL, hash over business data
- **Alternatives:** a per-employee scope, a key that expires after 24 hours, a hash over the whole body.
- **Why:**
  - `UNIQUE(idempotency_key)` plus `ON CONFLICT DO NOTHING`: two concurrent requests with the same key always produce one row, with no race (the DB decides).
  - `request_hash = sha256(employee|account|amount)`: the same key with the same data returns the same deposit (200). The same key with different data gets 409.
  - The amount is normalized (`100` = `100.00`) before hashing.
  - The simulation flags aren't part of the hash, because they're not business data.
- **Trade-off:** keys are kept forever (fine for a demo; in production, a TTL plus archiving). With a global scope, two employees can't use the same key (the client generates UUIDs, so there's no real collision).

---

## E. Statuses, retry and recovery

### E1. Four statuses + a separate `failure_retryable` field
- **Alternatives:** FAILED_TRANSIENT / FAILED_PERMANENT statuses, RETRYING.
- **Why:** it sticks exactly to the status table in the assignment. The retryable vs permanent distinction is in a separate field, as the assignment suggests. `failure_code` (for example `SIMULATED_TRANSIENT`, `ACCOUNT_BLOCKED`, `PROCESSING_INTERRUPTED`) lets the investigation filter and understand the cause.

### E2. Manual retry only, with no DLQ or automatic retry
- **Why:** the assignment describes retry as an action by an employee, whose identity is saved. A retry goes back to PENDING, gets a new `dispatch_id` and adds a `RETRY_REQUESTED` event with the requester. Retry is rejected for COMPLETED, PENDING/PROCESSING (already active) or a permanent failure.
- **Trade-off:** a transient failure isn't fixed by itself; it needs an employee.

### E3. Recovery: lease on PROCESSING plus a PENDING watchdog
- **Alternatives:** a heartbeat from the worker, a return to PENDING with an automatic retry.
- **Why:** a PROCESSING deposit older than 60 seconds (configurable) is stuck, for example because the worker crashed. Thanks to D2 it's guaranteed not to have been credited, so it's marked FAILED with `retryable=true` and a `RECOVERED_INTERRUPTED` event, and an employee decides whether to retry. The update is conditional (`WHERE status='PROCESSING' AND processing_started_at < now()-lease`), so it can't collide with a live worker. The watchdog re-sends a deposit that's been PENDING for more than 2 minutes.
- **Trade-off:** up to a minute (lease plus the scan interval) until detection. The simulated delay is capped at 30 seconds so it never exceeds the lease.

---

## F. Docker and operations

### F1. Readiness: healthchecks + `depends_on` conditions + retries in the code
- **Why:** a started container isn't a ready service. Postgres: `pg_isready`. Kafka: `kafka-broker-api-versions.sh` (it really answers a broker request). Init containers: `service_completed_successfully`. On top of that, the code retries connections with backoff (`connect_with_retry`), because a service can drop out even after it has started.

### F2. Named volumes `pgdata` and `kafkadata`
- **Why:** `docker compose down` followed by `up` keeps all the data. A reset is `docker compose down -v` (documented separately). Kafka's volume is mounted at `/var/lib/kafka/data`, a directory the image already creates owned by the Kafka user, so there's no permission problem.

### F3. Working without a `.env` file
- **Why:** every variable has a default in compose (`${VAR:-default}`), so the reviewer runs `docker compose up --build` with no preparation. `.env.example` documents what can be changed, such as a port that's already taken. The values are local, not real secrets.

### F4. Pinned image versions (not `latest`)
- **Why:** reproducibility. The same code runs the same way on the reviewer's machine a month from now.

### F5. Ports
| Service | Host port | Why |
|---|---|---|
| Frontend | 8080 | Standard port for local web apps |
| API / Swagger | 8000 | Uvicorn's default |
| Postgres | **5433** | Not 5432, to avoid clashing with a local Postgres on the reviewer's machine |
| Kafka (external) | 9094 | Separate from the internal listener |
| Kafka UI | 8081 | Viewing tool |
| Adminer | 8082 | Viewing tool |

### F6. Containers run as non-root
- **Why:** basic security hygiene. The backend runs as `appuser` (uid 10001).

### F7. Viewing tools: Adminer and Kafka UI (free, no registration)
- **Why:** they let the reviewer and the interviewer see the tables and the messages in Kafka without installing anything.

### F8. Line endings: `.gitattributes` with `eol=lf`
- **Why:** if the reviewer is on Windows, CRLF line endings can break scripts inside Linux containers. We also avoid shell entrypoint scripts entirely.

---

## G. Scope and product

### G1. Deposits only; no balance or sufficient-funds check
- **Why:** a deposit only adds money. There's no source account and no debit. A sufficient-funds check is relevant to withdrawals and transfers, which are outside the assignment.
- **Future extension:** transfers would need double-entry (a debit and a credit in one transaction), a sufficient-funds check, and locking both accounts in a fixed order (by ID) to avoid deadlocks.
- **Limits:** at most 1,000,000 ₪ per deposit. An overflow of NUMERIC(18,2) is turned into a permanent failure with an explanation.

### G2. Polling instead of WebSocket/SSE
- **Why:** the assignment explicitly says polling is enough. It's simple and stateless, and it survives refreshes and restarts. Every second on the execution screen, every 3 seconds in the investigation.
- **Trade-off:** more requests and up to a second of lag.

### G3. Simulated employee selection, not authentication
- **Why:** as the assignment requires. The employee is sent in the request body. It isn't a security mechanism, and that's stated in the UI.

### G4. UI in Hebrew RTL with Ant Design 5
- **Alternatives:** MUI, custom CSS.
- **Why:** built for data-heavy admin systems: Table with filters and pagination, Timeline, Drawer, RangePicker and Tag, with RTL and `he_IL` support included. MIT license.
- **Trade-off:** a relatively large bundle (not significant for an internal system).

### G5. Screen design based on research
- Status shown with text, an icon and a color, never color alone. Visible filters stored in the URL. Pagination instead of infinite scroll. A timeline in a Drawer with the actor and the time elapsed between steps. Retry placed in context next to the failure.
- Sources: Wise, Primer, Ripple payment timelines, and enterprise-table UX guides.

---

## H. Changes during implementation
| Phase | Change | Reason |
|---|---|---|
| 1 | The SQL folder was renamed from `app/db/` to `app/sql/` | Name clash with the module `app/db.py` |
| 1 | `opening_balance` was added to accounts | Lets the balance invariant be verified (B4) |
| 1 | A `REPUBLISHED` event type was added | Records a re-send by the PENDING watchdog (E3) |
| 2 | The outbox relay is the only publishing path (the fast path is `kick()` that wakes it), instead of a separate direct publish | One code path for publishing means less duplication and fewer bugs. The latency is the same |
| 2 | Each outbox row is published in its own short transaction (`LIMIT 1 FOR UPDATE SKIP LOCKED`) | A Kafka failure in the middle doesn't roll back messages that were already published |
| 2 | The `PUBLISHED` event stores the partition and offset | An investigator can find the exact message in Kafka UI |
| 2 | `/health` checks a real TCP connection to the broker | Bug found in testing: health reported `kafka: up` while Kafka was down, because it only checked that a producer object existed |
| 2 | Status counts (`status_counts`) are returned in the search response and ignore the status filter | The summary tiles on the investigation screen without an extra request |
| 2 | `amount` accepts a string or a JSON number but validates its **text**; a boolean is rejected (Strict types) | Exactness. A string is the recommendation in the documentation |
| 3 | The worker processes different partitions in parallel within a batch (`asyncio.gather`), with messages inside a partition processed in order | A delay on one account doesn't hold up deposits on other partitions in the same batch. Trade-off: the next poll waits for the slowest partition |
| 3 | On an infrastructure error (DB down) the worker `seek`s back to the same message instead of committing | No message is lost. Kafka "re-reads" it after a backoff |
| 3 | A poison message (invalid JSON or an unknown deposit) is logged and committed | It doesn't block the partition forever. It's recorded in the log for investigation |
| 3 | The PENDING watchdog writes to the outbox and the API's relay publishes | The worker has no producer at all, so there's one publishing path. Trade-off: if the API is down, the re-send waits for it |
| 3 | `stop_grace_period: 40s` on the worker | A regular `docker compose stop` lets an attempt with a 30-second simulated delay finish instead of cutting it off |
| 3 | `failure_code` and `failure_reason` are kept after a retry until the next attempt starts | An employee can see on the table why it was retried. The full history is in the events anyway |
| 3 | After a crash, the redelivered message is caught as `DUPLICATE_IGNORED` (status PROCESSING) and the recovery loop handles it after the lease | We don't try to "take over" a PROCESSING deposit from the message, because there's no way to know whether the original worker is still alive. The lease is the single source of truth |
