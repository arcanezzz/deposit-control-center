# Deposit Control Center

A system for bank employees to make **simulated** deposits into accounts and investigate each one end to end: who requested it, for which account, the amount, whether the account was credited, and what happened when something failed.
Every request goes through **real Apache Kafka** before the account is credited. All data is synthetic.

## Quick start

Requirements: **Docker + Docker Compose only**. You don't need Node, Python, Java, Kafka or a database. The first run needs internet access to download images.

```bash
docker compose up --build
```

When everything is up (about 1–2 minutes on the first run), open:

| What | URL |
|---|---|
| **App (UI)** | **http://localhost:8080** |
| API docs (Swagger / OpenAPI) | http://localhost:8000/docs |
| Kafka UI (topics, messages, consumer lag) | http://localhost:8081 |
| Adminer (DB browser): System `PostgreSQL`, Server `postgres`, User `app`, Password `app_local_dev`, DB `deposits` | http://localhost:8082 |

Sample data loads automatically on the first run: 5 employees and 6 accounts (one of them **blocked**), plus 4 historical deposits. Running it again doesn't reset balances or duplicate data.

### A 2-minute demo

1. Pick an employee in the top bar (this is a simulation, not authentication).
2. **Execution screen**: pick an account, enter an amount, and press **הפקד** (Deposit). You get a deposit ID and a live status that moves from ממתין (pending) to בעיבוד (processing) to הושלם (completed), and the balance updates.
3. Under **אפשרויות הדגמה** (demo options): set a *processing delay* to see the PROCESSING status, or turn on a *transient failure on the first attempt*.
4. **שלח שוב (אותו מזהה)** (resend with the same key) and **לחיצה כפולה** (double click): you get the same deposit back and nothing is credited twice.
5. **Investigation screen**: filters, a table, and clicking a row opens the **event timeline** with the **retry** button. A retry by a different employee is recorded without overwriting the initiator.
6. Permanent failure: start a deposit with a 15-second delay, then block the account under "חשבונות ויתרות" (accounts and balances) on the investigation screen.

## Architecture (short)

```
Browser ─► frontend (nginx :8080, React) ─► /api ─► api (FastAPI :8000)
                                                     │ one transaction: deposit + event + outbox
                                                     ▼
                                               PostgreSQL 16 ◄──────────────┐
                                                     │ outbox relay         │ Tx2: credit + balance
                                                     ▼                      │      + status (atomic)
                                    Kafka topic "deposit-requests" ─► worker (consumer + recovery)
```

- **The API never credits accounts.** It validates the request, saves it as PENDING with an outbox row in **one transaction**, and returns **202 Accepted** with a `deposit_id`.
- **The worker** reads from Kafka and credits the account in **one atomic transaction**: credit row + `balance = balance + amount` + COMPLETED.
- Full design, consistency, recovery and trade-offs: **[docs/DESIGN.md](docs/DESIGN.md)**. The decision log: **[decisions.md](decisions.md)**.

## Services

| Service | Role | Host port |
|---|---|---|
| `frontend` | nginx: React build + reverse proxy for `/api` | 8080 |
| `api` | FastAPI REST + outbox relay to Kafka | 8000 |
| `worker` | Kafka consumer (the only component that credits) + recovery loop | — |
| `postgres` | PostgreSQL 16, volume `pgdata` | 5433 |
| `kafka` | Apache Kafka 3.8 (KRaft, single broker), volume `kafkadata` | 9094 (external listener) |
| `kafka-init` | Creates the `deposit-requests` topic (3 partitions) once, then exits | — |
| `migrate` | Idempotent schema + seed, runs once on every startup, then exits | — |
| `kafka-ui`, `adminer` | Tools for viewing the data | 8081, 8082 |

`migrate` and `kafka-init` showing **Exited (0)** is correct: they're one-shot jobs.

**Readiness:** healthchecks (`pg_isready`, a real broker request to Kafka, `/api/health`) plus `depends_on` conditions (`service_healthy` / `service_completed_successfully`). On top of that, all the code retries connections with backoff.

## Environment variables

Every variable has a built-in default, so **no `.env` file is needed**. To change something (for example a port that's taken), copy `.env.example` to `.env` and edit it. These are local development values with no real secrets.

| Variable | Default | Meaning |
|---|---|---|
| `UI_HOST_PORT` / `API_HOST_PORT` | 8080 / 8000 | Host ports for the UI and the API |
| `POSTGRES_HOST_PORT` | 5433 | Host port for Postgres (not 5432, to avoid clashing with a local Postgres) |
| `KAFKA_HOST_PORT` / `KAFKA_UI_HOST_PORT` / `ADMINER_HOST_PORT` | 9094 / 8081 / 8082 | Tool ports |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | app / app_local_dev / deposits | DB credentials (local only) |
| `PROCESSING_LEASE_SECONDS` | 60 | After how long a PROCESSING deposit counts as interrupted |
| `RECOVERY_INTERVAL_SECONDS` | 10 | How often the recovery loop runs |
| `PENDING_WATCHDOG_SECONDS` | 120 | After how long a PENDING deposit is re-sent to Kafka |
| `LOG_LEVEL` | INFO | Log level |

## Tests

46 integration tests run **against the live system** (API + real Kafka + worker + Postgres), inside a container:

```bash
docker compose up -d --build          # if it isn't already running
docker compose --profile test run --rm --build tests
```

The tests create dedicated `TEST-xxxx` accounts and don't change the demo balances. Coverage includes:
- Amount and key validation.
- Idempotency: replay, 409, 20 concurrent requests with the same key.
- The full flow through Kafka, transient failure and retry by another employee, and retry rules.
- 5 concurrent retries, and blocking during processing.
- **30 concurrent credits to the same account** with no lost update.
- **A duplicate Kafka message × 10** giving a single credit, plus a stale message after a retry.
- Recovery from interrupted processing, and a slow attempt that must not credit after recovery.
- An immutability trigger in the DB, and the timeline order.

## Logs and tracing a deposit

All logs are **JSON on a single line** and carry `deposit_id`, `dispatch_id`, `attempt`, `partition` and `offset` where relevant.

```bash
docker compose logs -f api worker                       # live
docker compose logs api worker | grep <deposit_id>      # one deposit, end to end
```

The same `deposit_id` appears in the API response (plus the `X-Deposit-Id` header), in the Kafka message (key = account, header `deposit_id`), in every DB table, and in the logs. The **timeline** on the investigation screen also records the partition and offset of each message, so you can find it in Kafka UI.

Example checks in SQL (Adminer, or `docker compose exec postgres psql -U app deposits`):

```sql
-- Invariant 1: balance = opening balance + sum of credits (for every account)
SELECT a.id, a.balance, a.opening_balance + COALESCE(SUM(c.amount),0) AS expected
FROM accounts a LEFT JOIN credits c ON c.account_id = a.id GROUP BY a.id;

-- Invariant 2: COMPLETED <=> a credit row exists
SELECT bool_and((d.status = 'COMPLETED') = (c.id IS NOT NULL))
FROM deposits d LEFT JOIN credits c ON c.deposit_id = d.id;
```

## Stop, restart and reset

```bash
docker compose stop          # stop (data preserved)
docker compose up -d         # start again; all data stays (volumes pgdata, kafkadata)
docker compose down          # remove containers; data still preserved in the volumes
```

**Full reset of the demo environment** (deletes all deposits, balances and Kafka messages, and reloads the sample data on the next run):

```bash
docker compose down -v
docker compose up --build
```

## Documentation

| File | Contents |
|---|---|
| [docs/API.md](docs/API.md) | The API contract with request and response examples, plus error codes |
| [docs/DESIGN.md](docs/DESIGN.md) | Architecture, data model, consistency, recovery, idempotency, trade-offs |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | Each requirement in the assignment, where it's implemented and how to check it |
| [decisions.md](decisions.md) | Decision log: alternatives, reasons, trade-offs |
| [plan.md](plan.md) | The work plan in phases |

## Project structure

```
docker-compose.yml        all services
backend/                  one Python image for migrate / api / worker / tests
  app/api/                FastAPI routes + app
  app/services/           deposit use cases (create, search, retry)
  app/worker/             Kafka consumer, processor (credit), recovery loop
  app/outbox_relay.py     outbox -> Kafka
  app/sql/                schema.sql, seed.sql (idempotent)
  tests/                  integration tests
frontend/                 React + Vite + Ant Design (Hebrew RTL), nginx
docs/                     API, design, requirements traceability
```
