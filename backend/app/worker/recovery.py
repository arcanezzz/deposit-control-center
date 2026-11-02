"""Recovery loop (runs inside the worker every RECOVERY_INTERVAL_SECONDS).

1. Interrupted PROCESSING (lease expired)
   A deposit PROCESSING for longer than PROCESSING_LEASE_SECONDS means its attempt was interrupted
   (worker crashed / was killed / lost the DB mid-attempt). Because credit+balance+status are written
   in ONE transaction, such a deposit is guaranteed NOT to be credited, so it is safe to mark it
   FAILED with failure_retryable=true (code PROCESSING_INTERRUPTED). An employee can then retry it.
   If — against the invariant — a credit row exists, the status is repaired to COMPLETED instead.
   The UPDATE is conditional (status + started_at), so it never races a live attempt; a slow live
   attempt that later reaches Tx2 sees the change and aborts without crediting (CREDIT_ABORTED).

2. PENDING watchdog
   A PENDING deposit whose message was published long ago but never picked up (e.g. the topic was
   deleted, or Kafka lost the message) gets a new outbox row with the SAME dispatch_id, so the relay
   re-publishes it (REPUBLISHED event). Duplicates are harmless (idempotent consumer).
"""
import asyncio
import json
import logging

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)


async def recover_stuck_processing(pool: asyncpg.Pool) -> int:
    recovered = 0
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """SELECT d.id, d.attempts, d.processing_started_at,
                      EXISTS (SELECT 1 FROM credits c WHERE c.deposit_id = d.id) AS has_credit
                 FROM deposits d
                WHERE d.status = 'PROCESSING'
                  AND d.processing_started_at < clock_timestamp() - make_interval(secs => $1)
                FOR UPDATE OF d SKIP LOCKED""",
            float(settings.processing_lease_seconds),
        )
        for r in rows:
            if r["has_credit"]:
                await conn.execute(
                    """UPDATE deposits SET status = 'COMPLETED', completed_at = clock_timestamp(),
                              updated_at = clock_timestamp() WHERE id = $1""", r["id"])
                details = {"action": "repaired_to_completed", "reason": "credit row exists"}
            else:
                await conn.execute(
                    """UPDATE deposits
                          SET status = 'FAILED', failure_code = 'PROCESSING_INTERRUPTED',
                              failure_reason = $2, failure_retryable = true, updated_at = clock_timestamp()
                        WHERE id = $1""",
                    r["id"],
                    f"העיבוד נקטע (לא הסתיים תוך {settings.processing_lease_seconds} שניות). "
                    "לא בוצע זיכוי — ניתן לנסות שוב",
                )
                details = {"action": "marked_failed_retryable", "lease_seconds": settings.processing_lease_seconds,
                           "processing_started_at": r["processing_started_at"].isoformat(), "credited": False}
            await conn.execute(
                """INSERT INTO deposit_events (deposit_id, event_type, attempt_no, details)
                   VALUES ($1, 'RECOVERED_INTERRUPTED', $2, $3::jsonb)""",
                r["id"], r["attempts"], json.dumps(details, ensure_ascii=False))
            log.warning("recovered interrupted processing", extra={
                "deposit_id": r["id"], "attempt": r["attempts"], "event": details["action"]})
            recovered += 1
    return recovered


async def republish_stuck_pending(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """SELECT d.id, d.account_id, d.current_dispatch_id
                 FROM deposits d
                WHERE d.status = 'PENDING'
                  AND d.updated_at < clock_timestamp() - make_interval(secs => $1)
                  AND NOT EXISTS (                       -- nothing waiting / nothing recently sent
                        SELECT 1 FROM outbox o
                         WHERE o.deposit_id = d.id
                           AND (o.published_at IS NULL
                                OR o.published_at > clock_timestamp() - make_interval(secs => $1)))
                FOR UPDATE OF d SKIP LOCKED""",
            float(settings.pending_watchdog_seconds),
        )
        for r in rows:
            await conn.execute(
                "INSERT INTO outbox (deposit_id, message_key, payload) VALUES ($1, $2, $3::jsonb)",
                r["id"], r["account_id"],
                json.dumps({"schema_version": 1, "deposit_id": str(r["id"]),
                            "dispatch_id": str(r["current_dispatch_id"]), "reason": "WATCHDOG"}))
            log.warning("pending deposit re-queued by watchdog", extra={"deposit_id": r["id"]})
    return len(rows)


async def run_recovery_loop(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    log.info("recovery loop started", extra={"duration_ms": settings.processing_lease_seconds * 1000})
    while not stop.is_set():
        try:
            await recover_stuck_processing(pool)
            await republish_stuck_pending(pool)
        except Exception as exc:  # noqa: BLE001 - keep looping
            log.warning("recovery cycle failed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.recovery_interval_seconds)
        except asyncio.TimeoutError:
            pass
