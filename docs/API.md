# API Contract

Base URL: `http://localhost:8000` (or through the UI's proxy: `http://localhost:8080/api/...`).
The live interactive contract (OpenAPI 3, generated from the code): **http://localhost:8000/docs** · JSON: `/openapi.json`.

## General conventions

- **Money** is always a **string** with exactly 2 decimals: `"1250.50"`. Requests should send a string too. A JSON number is accepted, but its text is validated, and float values are never used for arithmetic.
- **Times** are ISO-8601 in UTC (`2026-11-02T08:45:48.241500Z`). The UI displays them in Asia/Jerusalem time.
- **IDs**: deposit = UUID, employee = `E-1001`, account = `ACC-001`.
- **Errors** always use the same shape:
  ```json
  { "error": { "code": "INVALID_AMOUNT", "message": "Amount may have at most 2 decimal places",
               "details": { "field": "amount", "reason": "too_many_decimals", "value": "0.001" } } }
  ```
  `code` is stable for programmatic handling (the UI translates it into Hebrew). `message` is an English explanation.
- **Correlation**: deposit responses include the `X-Deposit-Id` header. The same ID appears in Kafka, in the DB and in the logs.

---

## Deposits

### `POST /api/deposits`: request a deposit (asynchronous)

| Header | Required | Description |
|---|---|---|
| `Idempotency-Key` | **Yes** | A unique client-generated key (recommended: a UUID). 8–128 characters `[A-Za-z0-9_-:.]` |

```http
POST /api/deposits
Idempotency-Key: 7d4a2f0e-8f5c-4b8e-9a51-2a3c4d5e6f70
Content-Type: application/json

{
  "employee_id": "E-1001",
  "account_id": "ACC-004",
  "amount": "250.00",
  "simulate": { "delay_seconds": 0, "transient_failure": false }
}
```

`simulate` is optional and for demos only. `delay_seconds` (0–30) holds the deposit in PROCESSING. `transient_failure` makes the **first** attempt fail before the credit; the next attempt succeeds.

**202 Accepted**: a new request. **The account has not been credited yet.**
```http
HTTP/1.1 202 Accepted
Location: /api/deposits/a6f84641-92fe-4ee4-be1f-0f185f90f164
X-Deposit-Id: a6f84641-92fe-4ee4-be1f-0f185f90f164
Idempotent-Replay: false
```
```json
{
  "deposit_id": "a6f84641-92fe-4ee4-be1f-0f185f90f164",
  "status": "PENDING",
  "idempotent_replay": false,
  "message": "The deposit request was accepted for processing. The account has NOT been credited yet — track the status with the deposit_id.",
  "deposit": { "id": "a6f84641-...", "status": "PENDING", "amount": "250.00", "attempts": 0, "...": "see Deposit" }
}
```

**200 OK**: *idempotent replay*. The same key with the same data (employee, account, amount; `"100"` and `"100.00"` count as identical). The **existing** deposit is returned in its current status. Nothing new is created and nothing is credited again.
```json
{
  "deposit_id": "a6f84641-92fe-4ee4-be1f-0f185f90f164",
  "status": "COMPLETED",
  "idempotent_replay": true,
  "message": "Idempotent replay: a deposit with this Idempotency-Key and the same data already exists. Returned the existing deposit; nothing new was created or credited.",
  "deposit": { "...": "..." }
}
```

**Errors:**

| HTTP | `code` | When |
|---|---|---|
| 400 | `IDEMPOTENCY_KEY_REQUIRED` | No `Idempotency-Key` header |
| 400 | `INVALID_IDEMPOTENCY_KEY` | Wrong length or characters in the key |
| 404 | `EMPLOYEE_NOT_FOUND` / `ACCOUNT_NOT_FOUND` | Unknown employee or account |
| 409 | `IDEMPOTENCY_KEY_REUSED` | **The same key with different data.** `details.existing_deposit_id` |
| 422 | `INVALID_AMOUNT` | `details.reason`: `required` / `negative` / `not_positive` / `too_many_decimals` / `invalid_format` |
| 422 | `AMOUNT_TOO_LARGE` | Above 1,000,000.00 ₪ per deposit |
| 422 | `ACCOUNT_BLOCKED` | The account is blocked (no deposit record is created) |
| 422 | `VALIDATION_ERROR` | Wrong structure or types (for example `"amount": true`, an extra field) |

A request rejected with 4xx **does not create** a deposit record, so the same key can be reused with corrected data.

#### Idempotency contract
- **Scope:** the key is unique **globally** (across all employees) and **has no expiry**.
- Same key + same business data → the same deposit (200). Same key + different data → 409.
- Concurrent requests with the same key: exactly one gets 202 and the rest get 200 with the same `deposit_id` (enforced by a UNIQUE constraint in the DB).
- The `simulate` fields aren't business data and aren't compared. The first request wins.
- Every replay is recorded on the deposit's timeline (`IDEMPOTENT_REPLAY`).

### `GET /api/deposits`: search (investigation)

| Parameter | Description |
|---|---|
| `employee_id` | The initiating employee |
| `account_id` | Account |
| `status` | Repeatable: `?status=FAILED&status=PENDING` |
| `created_from`, `created_to` | `YYYY-MM-DD`, calendar days in Israel time, **inclusive** (`created_to` covers the whole day) |
| `idempotency_key` | Find a deposit by its key (useful after communication uncertainty) |
| `limit` (1–200, default 20), `offset` | Pagination. Sorted by creation time, newest first |

```http
GET /api/deposits?status=FAILED&created_from=2026-10-29&created_to=2026-10-31
```
```json
{
  "items": [ { "id": "11111111-1111-4111-8111-000000000004", "status": "FAILED", "failure_retryable": false,
               "failure_code": "ACCOUNT_BLOCKED", "employee_name": "נועה פרידמן", "amount": "300.00", "...": "..." } ],
  "total": 1, "limit": 20, "offset": 0,
  "status_counts": { "COMPLETED": 2, "FAILED": 1, "PENDING": 0, "PROCESSING": 0 }
}
```
`status_counts` counts by status under all the filters **except** the status filter (used for the summary tiles).
Errors: `422 INVALID_DATE_RANGE` (from > to), `422 INVALID_STATUS`.

### `GET /api/deposits/{id}`: tracking: status, credit and timeline

```json
{
  "id": "a6f84641-92fe-4ee4-be1f-0f185f90f164",
  "idempotency_key": "7d4a2f0e-8f5c-4b8e-9a51-2a3c4d5e6f70",
  "employee_id": "E-1001", "employee_name": "דנה כהן",
  "account_id": "ACC-004", "account_number": "12-602-200004", "owner_name": "סאמר נסאר",
  "amount": "250.00",
  "status": "COMPLETED",
  "failure_code": null, "failure_reason": null, "failure_retryable": null,
  "attempts": 1,
  "simulate_delay_seconds": 0, "simulate_transient_failure": false,
  "processing_started_at": "2026-11-02T08:45:48.258889Z",
  "created_at": "2026-11-02T08:45:48.241500Z",
  "updated_at": "2026-11-02T08:45:48.275033Z",
  "completed_at": "2026-11-02T08:45:48.275033Z",
  "credit": {
    "id": "e8aa2c87-68bc-4151-9266-7a6b4ae3c916", "deposit_id": "a6f84641-...",
    "account_id": "ACC-004", "amount": "250.00", "balance_after": "1274.20",
    "created_at": "2026-11-02T08:45:48.274727Z"
  },
  "events": [
    { "event_type": "RECEIVED", "actor_employee_id": "E-1001", "actor_name": "דנה כהן", "attempt_no": null,
      "details": { "amount": "250.00", "account_id": "ACC-004", "dispatch_id": "0f14c1e0-..." },
      "created_at": "2026-11-02T08:45:48.242838Z" },
    { "event_type": "PUBLISHED", "actor_employee_id": null, "attempt_no": null,
      "details": { "topic": "deposit-requests", "partition": 2, "offset": 38, "dispatch_id": "0f14c1e0-..." },
      "created_at": "2026-11-02T08:45:48.250000Z" },
    { "event_type": "ATTEMPT_STARTED", "actor_employee_id": null, "attempt_no": 1,
      "details": { "worker": "7e23bc207e8a", "partition": 2, "offset": 38 }, "created_at": "..." },
    { "event_type": "COMPLETED", "actor_employee_id": null, "attempt_no": 1,
      "details": { "credit_id": "e8aa2c87-...", "amount": "250.00", "balance_after": "1274.20" }, "created_at": "..." }
  ]
}
```
`credit` is `null` as long as no credit has been made. `404 DEPOSIT_NOT_FOUND`, `422` for an invalid UUID.

**Event types** (`event_type`):

| Type | Meaning | `actor` |
|---|---|---|
| `RECEIVED` | The request was saved (PENDING) | Initiating employee |
| `IDEMPOTENT_REPLAY` | A resend with the same key; nothing new was created | The sending employee |
| `PUBLISHED` / `REPUBLISHED` | Sent to Kafka (partition and offset) / re-sent by the watchdog | System |
| `ATTEMPT_STARTED` | A processing attempt started (PROCESSING), `attempt_no` | System |
| `FAILED` | The attempt failed with no credit. `details.retryable`, `failure_code`, `reason` | System |
| `RETRY_REQUESTED` | A retry was requested (back to PENDING) | **The requesting employee** |
| `COMPLETED` | Credit saved. `balance_after` | System |
| `RECOVERED_INTERRUPTED` | Recovery from an interrupted attempt (lease expired) → FAILED retryable | System |
| `DUPLICATE_IGNORED` | A duplicate Kafka message, ignored with no credit | System |
| `STALE_MESSAGE_IGNORED` | A message from before a retry (old dispatch), ignored | System |
| `CREDIT_ABORTED` | The attempt lost ownership (for example after recovery) and didn't credit | System |

### `GET /api/deposits/{id}/events`
The event list alone (the same shape as `events` above).

### `POST /api/deposits/{id}/retry`: retry a transient failure

```http
POST /api/deposits/11111111-1111-4111-8111-000000000003/retry
Content-Type: application/json

{ "employee_id": "E-1002" }
```
**202 Accepted**: the **same deposit** goes back to PENDING and is sent to Kafka again (with a new `dispatch_id`). The original initiator (`employee_id`) **isn't changed**. The requester is recorded on the `RETRY_REQUESTED` event.
```json
{ "deposit_id": "11111111-1111-4111-8111-000000000003", "status": "PENDING", "idempotent_replay": false,
  "message": "Retry accepted: the same deposit is back in PENDING and was re-queued for processing. The account has NOT been credited yet.",
  "deposit": { "...": "..." } }
```

| HTTP | `code` | When |
|---|---|---|
| 409 | `DEPOSIT_ALREADY_COMPLETED` | The deposit is already completed |
| 409 | `PROCESSING_ALREADY_ACTIVE` | Status PENDING/PROCESSING: a request is already active (including two concurrent retries, where only one succeeds) |
| 409 | `PERMANENT_FAILURE` | `failure_retryable=false` (for example `ACCOUNT_BLOCKED`) |
| 404 | `DEPOSIT_NOT_FOUND` / `EMPLOYEE_NOT_FOUND` | |

---

## Employees and accounts

### `GET /api/employees`
```json
[ { "id": "E-1001", "name": "דנה כהן", "role": "טלרית" }, { "id": "E-1002", "name": "יוסי לוי", "role": "מנהל סניף" } ]
```

### `GET /api/accounts` · `GET /api/accounts/{id}`
```json
{ "id": "ACC-004", "account_number": "12-602-200004", "owner_name": "סאמר נסאר",
  "balance": "1274.20", "opening_balance": "0.00", "status": "ACTIVE", "updated_at": "2026-11-02T08:45:48.274727Z" }
```

### `GET /api/accounts/{id}/credits?limit=50`
The account's credit ledger (newest first). Always: `balance = opening_balance + Σ amount`.

### `PATCH /api/accounts/{id}/status` (**demo only**)
```json
{ "status": "BLOCKED" }
```
Blocks or unblocks an account, used to demonstrate a permanent failure. A new deposit to a blocked account is rejected in the API (`ACCOUNT_BLOCKED`). A deposit that was already accepted ends as FAILED with `failure_retryable=false`.

---

## Operations

### `GET /api/health`
```json
{ "status": "ok", "database": "up", "kafka": "up", "outbox_backlog": 0 }
```
- HTTP 200 if the DB is up (even when Kafka is down: `"status": "degraded"`, `"kafka": "down"`, and requests wait in the outbox).
- HTTP 503 if the DB is down.
- `outbox_backlog` is the number of messages waiting to be published to Kafka.

---

## Kafka message contract

| | |
|---|---|
| Topic | `deposit-requests` (3 partitions) |
| Key | `account_id` (ordering per account) |
| Headers | `deposit_id`, `dispatch_id` |
| Delivery | At-least-once; the consumer is idempotent |

```json
{ "schema_version": 1,
  "deposit_id": "a6f84641-92fe-4ee4-be1f-0f185f90f164",
  "dispatch_id": "0f14c1e0-e3f5-4d70-bf8d-b065028bba80",
  "reason": "NEW" }
```
`reason` is one of `NEW` | `RETRY` | `WATCHDOG`. The message carries **IDs only**; the amount and account are read from the DB (the source of truth). A message whose `dispatch_id` isn't the deposit's current one is ignored.
