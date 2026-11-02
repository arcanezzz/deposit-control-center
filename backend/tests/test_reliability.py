"""Concurrency and failure handling, exercising the worker's processing code directly.

These tests insert deposits WITHOUT an outbox row (so the live worker never sees them through Kafka)
and call `process_message` / recovery functions themselves — in parallel — to prove the DB-level
guarantees hold regardless of Kafka ordering.
"""
import asyncio
import json
import uuid
from decimal import Decimal

from app.worker.processor import process_message
from app.worker.recovery import recover_stuck_processing
from tests.conftest import assert_ledger_consistent, balance, create, wait_for


async def insert_pending(db, account_id: str, amount: str) -> tuple[uuid.UUID, str]:
    dep_id, dispatch = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        """INSERT INTO deposits (id, idempotency_key, request_hash, employee_id, account_id, amount,
                                 status, current_dispatch_id)
           VALUES ($1, $2, 'test', 'E-1001', $3, $4, 'PENDING', $5)""",
        dep_id, f"test-direct-{dep_id}", account_id, Decimal(amount), dispatch)
    return dep_id, str(dispatch)


def message(dep_id, dispatch) -> bytes:
    return json.dumps({"schema_version": 1, "deposit_id": str(dep_id), "dispatch_id": dispatch,
                       "reason": "NEW"}).encode()


async def test_concurrent_credits_to_same_account_lose_no_update(db, test_account):
    acc = await test_account("1000.00")
    deposits = [await insert_pending(db, acc, "12.34") for _ in range(30)]
    outcomes = await asyncio.gather(*(process_message(db, message(d, disp), -1, i)
                                      for i, (d, disp) in enumerate(deposits)))
    assert outcomes.count("completed") == 30
    assert await balance(db, acc) == Decimal("1000.00") + 30 * Decimal("12.34")
    await assert_ledger_consistent(db, acc)


async def test_duplicate_kafka_message_credits_once(db, test_account):
    acc = await test_account("0.00")
    dep, disp = await insert_pending(db, acc, "50.00")
    outcomes = await asyncio.gather(*(process_message(db, message(dep, disp), -1, i) for i in range(10)))
    assert outcomes.count("completed") == 1 and outcomes.count("ignored") == 9
    assert await db.fetchval("SELECT count(*) FROM credits WHERE deposit_id = $1", dep) == 1
    assert await balance(db, acc) == Decimal("50.00")
    assert await db.fetchval(
        "SELECT count(*) FROM deposit_events WHERE deposit_id = $1 AND event_type = 'DUPLICATE_IGNORED'", dep) == 9


async def test_stale_message_after_retry_is_ignored(db, test_account):
    acc = await test_account("0.00")
    dep, old_dispatch = await insert_pending(db, acc, "8.00")
    await db.execute("UPDATE deposits SET current_dispatch_id = $2 WHERE id = $1", dep, uuid.uuid4())
    assert await process_message(db, message(dep, old_dispatch), -1, 0) == "ignored"
    assert await db.fetchval("SELECT status FROM deposits WHERE id = $1", dep) == "PENDING"
    assert await db.fetchval(
        "SELECT count(*) FROM deposit_events WHERE deposit_id = $1 AND event_type = 'STALE_MESSAGE_IGNORED'", dep) == 1


async def test_interrupted_processing_is_recovered_without_credit(db, test_account):
    """Simulates a worker that crashed after Tx1 (PROCESSING committed) and before Tx2."""
    acc = await test_account("0.00")
    dep, _ = await insert_pending(db, acc, "9.99")
    await db.execute(
        """UPDATE deposits SET status = 'PROCESSING', attempts = 1,
                  processing_started_at = clock_timestamp() - interval '10 minutes' WHERE id = $1""", dep)
    await recover_stuck_processing(db)  # the live worker's loop may also do it; the outcome is the same
    row = await db.fetchrow("SELECT status, failure_retryable, failure_code FROM deposits WHERE id = $1", dep)
    assert (row["status"], row["failure_retryable"], row["failure_code"]) == ("FAILED", True, "PROCESSING_INTERRUPTED")
    assert await balance(db, acc) == Decimal("0.00")
    assert await db.fetchval(
        "SELECT count(*) FROM deposit_events WHERE deposit_id = $1 AND event_type = 'RECOVERED_INTERRUPTED'", dep) == 1


async def test_slow_attempt_cannot_credit_after_recovery(db, test_account):
    """An attempt that outlived its lease and was recovered must not credit when it finally resumes."""
    acc = await test_account("0.00")
    dep, disp = await insert_pending(db, acc, "4.00")
    await db.execute("UPDATE deposits SET simulate_delay_seconds = 2 WHERE id = $1", dep)
    task = asyncio.create_task(process_message(db, message(dep, disp), -1, 0))
    await asyncio.sleep(0.5)  # attempt is now PROCESSING and sleeping
    await db.execute("""UPDATE deposits SET processing_started_at = clock_timestamp() - interval '10 minutes'
                         WHERE id = $1""", dep)
    await recover_stuck_processing(db)
    assert await task == "aborted"
    assert await db.fetchval("SELECT count(*) FROM credits WHERE deposit_id = $1", dep) == 0
    assert await balance(db, acc) == Decimal("0.00")


async def test_many_api_deposits_through_kafka_all_counted(client, db, test_account):
    acc = await test_account("0.00")
    responses = await asyncio.gather(*(create(client, acc, amount="1.11") for _ in range(25)))
    assert all(r.status_code == 202 for r in responses)
    await asyncio.gather(*(wait_for(client, r.json()["deposit_id"], {"COMPLETED"}, timeout=60) for r in responses))
    assert await balance(db, acc) == 25 * Decimal("1.11")
    await assert_ledger_consistent(db, acc)
