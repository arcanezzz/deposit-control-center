"""Processing of one Kafka message = one processing attempt of one deposit.

Consistency invariant (the core of the system)
----------------------------------------------
    status = COMPLETED  <=>  a row exists in `credits`  <=>  the balance was increased
All three are written in ONE transaction (Tx2), so they either all happen or none do.
Therefore PROCESSING never implies a credit, and an interrupted attempt is always safe to retry.

Duplicate protection (a message may arrive more than once — at-least-once delivery)
-----------------------------------------------------------------------------------
1. `current_dispatch_id`: only the message of the CURRENT dispatch may process the deposit;
   a message from before a retry is ignored (STALE_MESSAGE_IGNORED).
2. Status check under `SELECT ... FOR UPDATE`: only a PENDING deposit starts an attempt;
   anything else is a duplicate (DUPLICATE_IGNORED).
3. Tx2 re-checks `status='PROCESSING' AND attempts=n` under lock before crediting.
4. `UNIQUE(credits.deposit_id)`: the database itself refuses a second credit.

Steps
-----
Tx1  lock deposit -> PENDING->PROCESSING, attempts+1, ATTEMPT_STARTED     (committed: visible on screen)
     [simulated delay]
     [simulated transient failure on attempt 1 -> FAILED retryable]
Tx2  lock deposit + lock account -> checks -> credit row + balance += amount + COMPLETED + event
"""
import asyncio
import json
import logging
import socket
import uuid
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

log = logging.getLogger(__name__)

WORKER_ID = socket.gethostname()
MAX_BALANCE = Decimal("9999999999999999.99")  # NUMERIC(18,2) upper bound

TRANSIENT_REASON = "כשל זמני מדומה לפני הזיכוי (ניסיון ראשון)"


class PoisonMessage(Exception):
    """Message can never be processed (bad JSON / unknown deposit). Logged and skipped."""


@dataclass
class Attempt:
    deposit_id: uuid.UUID
    dispatch_id: str
    attempt: int
    delay_seconds: int
    transient_failure: bool


def parse_message(value: bytes) -> tuple[uuid.UUID, str]:
    try:
        payload = json.loads(value)
        return uuid.UUID(payload["deposit_id"]), str(uuid.UUID(payload["dispatch_id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise PoisonMessage(f"invalid message: {exc}") from exc


async def _event(conn: asyncpg.Connection, deposit_id, event_type: str, attempt: int | None, details: dict) -> None:
    await conn.execute(
        """INSERT INTO deposit_events (deposit_id, event_type, attempt_no, details)
           VALUES ($1, $2, $3, $4::jsonb)""",
        deposit_id, event_type, attempt, json.dumps(details, ensure_ascii=False, default=str),
    )


async def process_message(pool: asyncpg.Pool, value: bytes, partition: int, offset: int) -> str:
    """Process one message. Returns a short outcome label (for logs/tests).
    Raises on infrastructure errors (DB down) so the consumer does NOT commit the offset."""
    deposit_id, dispatch_id = parse_message(value)
    ctx = {"deposit_id": deposit_id, "dispatch_id": dispatch_id, "partition": partition, "offset": offset}

    attempt = await _start_attempt(pool, deposit_id, dispatch_id, partition, offset, ctx)
    if attempt is None:
        return "ignored"

    ctx["attempt"] = attempt.attempt
    if attempt.delay_seconds > 0:
        log.info("simulated processing delay", extra={**ctx, "duration_ms": attempt.delay_seconds * 1000})
        await asyncio.sleep(attempt.delay_seconds)

    if attempt.transient_failure and attempt.attempt == 1:
        await _fail(pool, attempt, "SIMULATED_TRANSIENT", TRANSIENT_REASON, retryable=True, ctx=ctx)
        return "failed_transient"

    return await _credit(pool, attempt, ctx)


async def _start_attempt(pool, deposit_id, dispatch_id, partition, offset, ctx) -> Attempt | None:
    """Tx1: PENDING -> PROCESSING. Returns None when the message must be ignored."""
    async with pool.acquire() as conn, conn.transaction():
        d = await conn.fetchrow(
            """SELECT status, attempts, current_dispatch_id, simulate_delay_seconds, simulate_transient_failure
                 FROM deposits WHERE id = $1 FOR UPDATE""",
            deposit_id,
        )
        if d is None:
            raise PoisonMessage(f"deposit {deposit_id} does not exist")

        if str(d["current_dispatch_id"]) != dispatch_id:
            await _event(conn, deposit_id, "STALE_MESSAGE_IGNORED", None, {
                "message_dispatch_id": dispatch_id, "current_dispatch_id": str(d["current_dispatch_id"]),
                "status": d["status"], "partition": partition, "offset": offset, "worker": WORKER_ID})
            log.info("stale message ignored (superseded by a retry)", extra={**ctx, "status": d["status"]})
            return None

        if d["status"] != "PENDING":
            await _event(conn, deposit_id, "DUPLICATE_IGNORED", None, {
                "status": d["status"], "dispatch_id": dispatch_id, "partition": partition, "offset": offset,
                "worker": WORKER_ID})
            log.info("duplicate message ignored", extra={**ctx, "status": d["status"]})
            return None

        attempt_no = d["attempts"] + 1
        await conn.execute(
            """UPDATE deposits
                  SET status = 'PROCESSING', attempts = $2, processing_started_at = clock_timestamp(),
                      updated_at = clock_timestamp(),
                      failure_code = NULL, failure_reason = NULL, failure_retryable = NULL
                WHERE id = $1""",
            deposit_id, attempt_no,
        )
        await _event(conn, deposit_id, "ATTEMPT_STARTED", attempt_no, {
            "dispatch_id": dispatch_id, "partition": partition, "offset": offset, "worker": WORKER_ID})
    log.info("attempt started", extra={**ctx, "attempt": attempt_no, "status": "PROCESSING"})
    return Attempt(deposit_id, dispatch_id, attempt_no, d["simulate_delay_seconds"], d["simulate_transient_failure"])


async def _fail(pool, attempt: Attempt, code: str, reason: str, *, retryable: bool, ctx: dict,
                conn: asyncpg.Connection | None = None) -> None:
    """Mark the attempt FAILED (no credit). Conditional on still owning the attempt."""
    async def run(c: asyncpg.Connection) -> None:
        updated = await c.fetchval(
            """UPDATE deposits
                  SET status = 'FAILED', failure_code = $3, failure_reason = $4, failure_retryable = $5,
                      updated_at = clock_timestamp()
                WHERE id = $1 AND status = 'PROCESSING' AND attempts = $2
            RETURNING id""",
            attempt.deposit_id, attempt.attempt, code, reason, retryable,
        )
        if updated is None:
            await _event(c, attempt.deposit_id, "CREDIT_ABORTED", attempt.attempt, {
                "reason": "deposit state changed during the attempt (e.g. recovered after lease expiry)",
                "worker": WORKER_ID})
            return
        await _event(c, attempt.deposit_id, "FAILED", attempt.attempt, {
            "failure_code": code, "retryable": retryable, "reason": reason, "worker": WORKER_ID})

    if conn is not None:
        await run(conn)
    else:
        async with pool.acquire() as c, c.transaction():
            await run(c)
    log.warning("attempt failed", extra={**ctx, "status": "FAILED", "error_code": code})


async def _credit(pool: asyncpg.Pool, attempt: Attempt, ctx: dict) -> str:
    """Tx2: everything under row locks, all-or-nothing."""
    async with pool.acquire() as conn, conn.transaction():
        d = await conn.fetchrow(
            "SELECT status, attempts, account_id, amount FROM deposits WHERE id = $1 FOR UPDATE",
            attempt.deposit_id)
        if d["status"] != "PROCESSING" or d["attempts"] != attempt.attempt:
            # e.g. recovery marked it FAILED because this attempt exceeded the lease
            await _event(conn, attempt.deposit_id, "CREDIT_ABORTED", attempt.attempt, {
                "reason": "deposit is no longer in this processing attempt", "status": d["status"],
                "worker": WORKER_ID})
            log.warning("credit aborted: attempt no longer owns the deposit", extra={**ctx, "status": d["status"]})
            return "aborted"

        # Lock the account row: serializes concurrent credits to the same account (no lost update)
        # and makes the BLOCKED check atomic with the credit.
        account = await conn.fetchrow(
            "SELECT status, balance FROM accounts WHERE id = $1 FOR UPDATE", d["account_id"])
        if account is None:
            await _fail(pool, attempt, "ACCOUNT_NOT_FOUND", "החשבון אינו קיים — כשל סופי",
                        retryable=False, ctx=ctx, conn=conn)
            return "failed_permanent"
        if account["status"] == "BLOCKED":
            await _fail(pool, attempt, "ACCOUNT_BLOCKED", "החשבון חסום — לא ניתן לזכות. כשל סופי, אין לנסות שוב",
                        retryable=False, ctx=ctx, conn=conn)
            return "failed_permanent"
        if account["balance"] + d["amount"] > MAX_BALANCE:
            await _fail(pool, attempt, "BALANCE_LIMIT_EXCEEDED", "הזיכוי יחרוג מהיתרה המרבית המותרת — כשל סופי",
                        retryable=False, ctx=ctx, conn=conn)
            return "failed_permanent"

        # Defensive: a credit can't exist while PROCESSING (invariant), but if it ever did, repair the
        # status instead of crediting twice.
        existing = await conn.fetchrow("SELECT id, balance_after FROM credits WHERE deposit_id = $1",
                                       attempt.deposit_id)
        if existing is not None:
            await conn.execute(
                """UPDATE deposits SET status = 'COMPLETED', completed_at = clock_timestamp(),
                          updated_at = clock_timestamp() WHERE id = $1""", attempt.deposit_id)
            await _event(conn, attempt.deposit_id, "COMPLETED", attempt.attempt, {
                "balance_after": existing["balance_after"], "credit_id": existing["id"],
                "note": "credit already existed — status repaired, no second credit", "worker": WORKER_ID})
            return "repaired"

        # Atomic increment in the DB (not read-modify-write in Python) -> no lost update.
        balance_after = await conn.fetchval(
            """UPDATE accounts SET balance = balance + $2, updated_at = clock_timestamp()
                WHERE id = $1 RETURNING balance""",
            d["account_id"], d["amount"])
        credit_id = await conn.fetchval(
            """INSERT INTO credits (deposit_id, account_id, amount, balance_after)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            attempt.deposit_id, d["account_id"], d["amount"], balance_after)  # UNIQUE(deposit_id) = last guard
        await conn.execute(
            """UPDATE deposits SET status = 'COMPLETED', completed_at = clock_timestamp(),
                      updated_at = clock_timestamp() WHERE id = $1""",
            attempt.deposit_id)
        await _event(conn, attempt.deposit_id, "COMPLETED", attempt.attempt, {
            "credit_id": credit_id, "amount": d["amount"], "balance_after": balance_after, "worker": WORKER_ID})

    log.info("deposit completed", extra={**ctx, "status": "COMPLETED", "account_id": d["account_id"]})
    return "completed"
