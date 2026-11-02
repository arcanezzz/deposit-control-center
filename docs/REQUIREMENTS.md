# Requirements Traceability

Each requirement in the assignment → where it's implemented → how to check it (automated test or manual step).
Tests live in `backend/tests/` and run with `docker compose --profile test run --rm tests`.

## Required technologies

| Requirement | Implementation | Check |
|---|---|---|
| Browser frontend: execution screen and investigation screen | `frontend/src/pages/ExecutePage.tsx`, `InvestigatePage.tsx` (React + Ant Design, Hebrew RTL) | http://localhost:8080 |
| REST: create, search, track deposits, accounts and balances, event history, retry | `backend/app/api/routes_deposits.py`, `routes_reference.py` | [docs/API.md](API.md), http://localhost:8000/docs |
| Real Apache Kafka before the credit; not in-memory or a mock | `apache/kafka:3.8.0`, outbox → topic `deposit-requests` → `worker`. The credit happens **only** in `worker/processor.py` | Kafka UI :8081 → Messages. `test_deposit_completes_and_credits_once` |
| A persistent DB: accounts, deposits, credits, processing history | PostgreSQL 16: `accounts`, `deposits`, `credits`, `deposit_events`, `outbox` (`app/sql/schema.sql`), volume `pgdata` | Adminer :8082 |
| Docker and Compose for everything, Dockerfiles and Compose written by us | `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage), `docker-compose.yml` | `docker compose up --build` |
| Explain the choices and the trade-offs | [decisions.md](../decisions.md), [DESIGN.md](DESIGN.md) §10 | |

## Business flow

| Requirement | Implementation | Check |
|---|---|---|
| The employee picks an identity, an account and an amount | Employee selection in the header, account and amount on the execution screen | UI |
| The API validates and saves the request with a tracking ID | `services/deposits.py::create_deposit`, `money.py::parse_amount` | `test_validation.py` |
| Passed through Kafka for asynchronous processing | `outbox_relay.py` → Kafka → `worker/main.py` | `test_many_api_deposits_through_kafka_all_counted` |
| Processing credits the account and saves the result and history | `processor.py::_credit` (Tx2) + `deposit_events` | `test_deposit_completes_and_credits_once` |
| The screen shows the updated status and balance | Polling every 1s (status) and 3s (balances) | UI |
| Accepting a request ≠ a credit; tracking ID + "accepted for processing" response; the balance grows only after the credit is saved | 202 + `message` + `Location`. The balance changes only in Tx2 | `test_accepted_response_is_not_a_credit`, `test_processing_is_visible_and_not_a_credit` |

## Execution screen

| Requirement | Implementation | Check |
|---|---|---|
| Employee and account selection, current balance, amount in shekels | `ExecutePage.tsx` (balance card + "updated X ago") | UI |
| Button, acceptance confirmation, operation ID, current status | "Last request" card + Steps + StatusBadge | UI |
| Clear errors for invalid input or a forbidden action | Client-side validation + `messages.ts` (Hebrew per error code) | UI: amount `0.001`, account `משה ישראלי` (blocked) |
| Demo of a processing delay and a transient failure (fails on the 1st attempt, succeeds on the next) | `simulate.delay_seconds`, `simulate.transient_failure` | `test_transient_failure_then_retry_by_other_employee` |
| Resend the same request with the same key (double click or communication uncertainty) | "Resend (same key)", "Double click", the "unknown state" state on a NetworkError | `test_concurrent_same_key_creates_one_deposit`, UI |
| No authentication; polling | Selection in localStorage, `usePolling` | |

## Investigation screen

| Requirement | Implementation | Check |
|---|---|---|
| All requests: employee, account, amount, status, created and updated, attempts, failure reason | Table in `InvestigatePage.tsx` | UI |
| Filters for employee, account, status and creation date range | Filter bar + `GET /api/deposits?...` (stored in the URL) | `test_search_filters`, `test_date_range_and_status_filters_validated` |
| An event timeline with timestamps: received, attempt start, failure, retry request, completion | `DepositTimeline.tsx` in a Drawer, `deposit_events` | `test_transient_failure_then_retry_by_other_employee`, `test_timeline_order_published_before_attempt` |
| Retry for transient failures only | `retry_deposit`: only `FAILED AND failure_retryable` | `test_account_blocked_after_acceptance_is_permanent_failure` |
| Store who requested the retry without overwriting the initiator | `RETRY_REQUESTED.actor_employee_id`; `deposits.employee_id` immutable (trigger) | `test_transient_failure_then_retry_by_other_employee`, `test_deposit_is_immutable_in_db` |
| Reject a retry for a completed deposit or one already being processed | 409 `DEPOSIT_ALREADY_COMPLETED` / `PROCESSING_ALREADY_ACTIVE` under `FOR UPDATE` | `test_retry_rules`, `test_concurrent_retries_only_one_wins` |
| Information survives a refresh and a restart | DB + volumes. UI: localStorage + URL | `docker compose down && docker compose up` |

## Statuses and consistency

| Requirement | Implementation | Check |
|---|---|---|
| PENDING / PROCESSING / COMPLETED / FAILED with their meanings | CHECK on `deposits.status`, [DESIGN.md](DESIGN.md) §3 | |
| Retry returns to PENDING and adds history | `retry_deposit` + `RETRY_REQUESTED` | `test_transient_failure_then_retry_by_other_employee` |
| A distinction between a retryable failure and a permanent one | `failure_retryable` + `failure_code` + the `failed_has_retryable_flag` CHECK | `test_account_blocked_after_acceptance_is_permanent_failure` |
| Consistency between the balance, the credit and the status | One Tx2 transaction, [DESIGN.md](DESIGN.md) §4 | `assert_ledger_consistent` in the tests |
| Recovery from interrupted processing; PROCESSING doesn't get stuck | `worker/recovery.py` (lease + watchdog), [DESIGN.md](DESIGN.md) §5 | `test_interrupted_processing_is_recovered_without_credit`, `test_slow_attempt_cannot_credit_after_recovery`, manual: `docker compose kill worker` mid-delay |

## Business rules and reliability

| Requirement | Implementation | Check |
|---|---|---|
| Positive amount, up to 2 decimals, exact representation (no float) | Regex on the text + `Decimal` + `NUMERIC(18,2)` + CHECK | `test_invalid_amounts_rejected_with_reason`, `test_valid_amounts_accepted` |
| Don't credit a missing or blocked account; reject in the API or end in a permanent failure | API: 404/422 with no record. Worker: check under `FOR UPDATE` → FAILED permanent | `test_blocked_account_rejected_without_record`, `test_account_blocked_after_acceptance_is_permanent_failure` |
| Idempotency-Key: same data → same deposit with no extra credit; different data → rejected; scope documented | ON CONFLICT + `request_hash`; 200 / 409; global scope ([API.md](API.md)) | `test_same_key_same_data_returns_same_deposit`, `test_same_key_different_data_rejected` |
| A duplicate Kafka message, a concurrent send, and a retry don't cause a double credit | dispatch_id + status check + Tx2 recheck + `UNIQUE(credits.deposit_id)` | `test_duplicate_kafka_message_credits_once`, `test_stale_message_after_retry_is_ignored`, `test_concurrent_same_key_creates_one_deposit` |
| Two deposits to the same account are both counted; no lost update | `balance = balance + x` under a row lock | `test_concurrent_credits_to_same_account_lose_no_update` (30 in parallel) |
| A deposit ID linking the API, messages, the DB and logs | `deposit_id` in the response + header, Kafka key and headers, every table, JSON logs | `docker compose logs api worker \| grep <id>` |
| Documented attempts and failure reasons | `attempts`, `failure_code`/`failure_reason`, events with `attempt_no` | Timeline |
| Changing the employee, amount or account isn't allowed; a retry continues the same deposit | No update endpoint + trigger | `test_deposit_is_immutable_in_db` |

## Docker and running locally

| Requirement | Implementation | Check |
|---|---|---|
| `docker compose up --build` from the root | `docker-compose.yml` at the root | |
| A Dockerfile for every component we developed | `backend/Dockerfile` (shared by migrate/api/worker/tests), `frontend/Dockerfile` | |
| Nothing to install except Docker | Multi-stage builds, the lock file generated in a container | |
| Automatic sample data; a rerun doesn't reset or duplicate | `migrate` + `seed.sql` with fixed IDs and `ON CONFLICT DO NOTHING` | Running `up` again leaves the counts unchanged |
| Persistent volumes for the DB and Kafka + separate reset instructions | `pgdata`, `kafkadata`; README → "Stop, restart and reset" | |
| Dependency readiness: healthchecks or retries | Healthchecks + `depends_on` conditions + `connect_with_retry` / consumer backoff | |
| Document the screen URL, ports, env, tests and logs; local values with no secrets | [README.md](../README.md), `.env.example` | |
| API contract documentation with request and response examples | [docs/API.md](API.md) + OpenAPI `/docs` | |
