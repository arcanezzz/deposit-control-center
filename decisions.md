# Decision Log

Format: **Decision**, then *Alternatives*, *Why*, *Trade-off*. Updated at every phase.

---

### D1. Backend: Python 3.12 + FastAPI
- **Alternatives:** Node.js + TypeScript, Java + Spring Boot.
- **Why:** `Decimal` is built in and maps directly to Postgres `NUMERIC`, which satisfies "no Float". Pydantic handles validation and clear error messages. OpenAPI documentation (`/docs`) is generated automatically. The code is short and readable.
- **Trade-off:** a different language from the frontend, so types aren't shared.

### D2. Frontend: React + Vite + TypeScript, served by nginx
- **Alternatives:** plain HTML/JS.
- **Why:** a rich UI for both screens. nginx serves the build and proxies `/api`, so everything is on the same origin and there's no CORS.
- **Trade-off:** a build step (multi-stage Dockerfile).

### D3. API and worker as separate containers from one image
- **Alternatives:** a single process (API + consumer).
- **Why:** a clear separation between receiving a request and processing it asynchronously. Each can be scaled independently (`--scale worker=2`), and a worker crash doesn't bring down the API.
- **Trade-off:** one more container in compose.

### D4. Database: PostgreSQL 16
- **Alternatives:** MySQL, MongoDB.
- **Why:** ACID, exact `NUMERIC`, `SELECT ... FOR UPDATE`, `INSERT ... ON CONFLICT` for idempotency, `CHECK` constraints, FKs and triggers.
- **Trade-off:** none significant for this case. MySQL would need more idempotency code, and MongoDB needs a replica set for transactions and has no FKs or CHECKs.

### D5. Kafka in KRaft mode, single broker (`apache/kafka:3.8.0`)
- **Alternatives:** Kafka + ZooKeeper, a multi-broker cluster, Redpanda.
- **Why:** real Kafka (as required) that's light enough to run locally, with no ZooKeeper.
- **Trade-off:** no replication (replication factor 1). That's not suitable for production.

### D6. One topic `deposit-requests`, 3 partitions, key = account_id
- **Alternatives:** separate topics for new requests and retries, a DLQ.
- **Why:** ordering per account and simplicity. A retry is the same command again.
- **Trade-off:** processing is sequential per partition, so a delay on one account holds up the other accounts on the same partition.

### D7. The message carries only an ID; the DB is the source of truth
- **Alternatives:** a message with the full data (amount, account).
- **Why:** prevents divergence between the message and the DB, and the message can't change the deposit's details.
- **Trade-off:** the worker reads from the DB for every message.

### D8. Transactional outbox
- **Alternatives:** publishing to Kafka directly from the API after the commit.
- **Why:** the deposit and the outbox row are saved in one transaction, so a crash between the DB and Kafka loses nothing. The API keeps accepting requests even while Kafka is down.
- **Trade-off:** a relay component, and a possible duplicate publish (harmless, see D9).

### D9. At-least-once delivery + an idempotent consumer
- **Alternatives:** exactly-once with Kafka transactions.
- **Why:** manual offset commit after the DB work, plus a status check, `dispatch_id` and `UNIQUE(credits.deposit_id)`, make a duplicate harmless.
- **Trade-off:** duplicates arrive and get recorded as ignored events.

### D10. Credit + balance + status in one transaction
- **Alternatives:** separate updates, or optimistic locking with a version.
- **Why:** COMPLETED ⇔ a credit row exists ⇔ the balance was increased. `balance = balance + x` under a row lock means no update is lost.
- **Trade-off:** a short lock on the account row while the credit is written.

### D11. Global Idempotency-Key with no TTL; hash = employee + account + amount
- **Alternatives:** a per-employee scope, a key with an expiry.
- **Why:** simple and strict. The same key with different data is rejected (409). Simulation flags aren't business data.
- **Trade-off:** keys are kept forever (fine at demo scale).

### D12. Retryable vs permanent failure via `failure_retryable` (no new statuses)
- **Alternatives:** separate FAILED_RETRYABLE / FAILED_PERMANENT statuses, automatic retry, a DLQ.
- **Why:** sticks to the 4 statuses in the spec. A retry is a manual, attributed decision by an employee.
- **Trade-off:** no automatic retry.

### D13. Recovery from interrupted PROCESSING by lease (60 seconds, configurable)
- **Alternatives:** a heartbeat from the worker, automatic return to PENDING.
- **Why:** simple. A PROCESSING deposit with no credit is guaranteed not to have been credited (D10), so it's safe to mark it FAILED as retryable.
- **Trade-off:** up to a minute until a stuck deposit is detected.

### D14. Deposits only; no balance check
- **Why:** a deposit only adds money, so there's no debit and no source account. Transfers are a future extension (double-entry, sufficient funds, locking accounts in a fixed order).

### D15. Polling instead of WebSocket/SSE
- **Why:** the spec allows it, and it's simple and robust. **Trade-off:** a little more network traffic.

### D16. Plain SQL (asyncpg) instead of an ORM
- **Why:** transactions, `FOR UPDATE` and `ON CONFLICT` stay explicit and easy to explain. **Trade-off:** more SQL by hand.

### D17. Synthetic, idempotent seed data in a one-shot `migrate` container
- **Why:** runs on every startup and uses `ON CONFLICT DO NOTHING`, so balances aren't reset and nothing is duplicated.
- **Trade-off:** schema changes have to be written idempotently (`IF NOT EXISTS`).

### D18. Free, with no registration; Adminer and Kafka UI for viewing the data
- **Why:** public images, open source, nothing beyond Docker to install.

### D19. UI in Hebrew RTL with Ant Design 5
- **Alternatives:** MUI, custom CSS.
- **Why:** built for data-heavy admin systems (Table, Timeline, Drawer, RangePicker), with RTL and `he_IL` included.
- **Trade-off:** a relatively large bundle.

### D20. Screen design based on research
- Status shown with text, an icon and a color. Visible filters stored in the URL. Pagination. A timeline in a Drawer with the actor and elapsed time. Retry placed next to the failure.
