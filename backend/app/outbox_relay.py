"""Transactional-outbox relay: publishes rows from the `outbox` table to Kafka.

Why an outbox: the deposit row and its outbox row are committed in ONE transaction, so there is
never a deposit without a message (or a message without a deposit) — even if the API crashes
right after the commit or Kafka is down.

How it works:
- Fast path: after creating a deposit the API calls `kick()`, so publishing starts immediately.
- Safety net: the loop also wakes every OUTBOX_RELAY_INTERVAL_SECONDS and publishes whatever is left.
- Each row is claimed with FOR UPDATE SKIP LOCKED, so several API replicas never publish the same
  row concurrently.
- If the publish succeeds but marking `published_at` fails, the row is published again later.
  That duplicate is harmless: the worker is idempotent (status check + dispatch_id + UNIQUE credit).
"""
import asyncio
import json
import logging

import asyncpg

from app.config import settings
from app.kafka import KafkaPublisher

log = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(self, pool: asyncpg.Pool, publisher: KafkaPublisher) -> None:
        self._pool = pool
        self._publisher = publisher
        self._wake = asyncio.Event()
        self._stopping = False

    def kick(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()

    async def run(self) -> None:
        log.info("outbox relay started")
        while not self._stopping:
            try:
                published = await self._publish_pending()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive no matter what
                log.warning("outbox relay cycle failed, will retry: %s", exc)
                published = 0
            if published == 0:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=settings.outbox_relay_interval_seconds)
                except asyncio.TimeoutError:
                    pass
        log.info("outbox relay stopped")

    async def backlog(self) -> int:
        return await self._pool.fetchval("SELECT count(*) FROM outbox WHERE published_at IS NULL")

    async def _publish_pending(self) -> int:
        """Publish rows one at a time (one short transaction each). Returns how many were published."""
        published = 0
        while not self._stopping:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """SELECT id, deposit_id, message_key, payload
                             FROM outbox
                            WHERE published_at IS NULL
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED"""
                    )
                    if row is None:
                        return published
                    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                    deposit_id = str(row["deposit_id"])
                    dispatch_id = payload["dispatch_id"]

                    # Timestamp taken BEFORE handing the message to Kafka: the worker may consume it
                    # (and write ATTEMPT_STARTED) before this transaction commits, and the timeline
                    # must still show "published" before "attempt started".
                    sent_at = await conn.fetchval("SELECT clock_timestamp()")

                    # Raises if Kafka is down -> transaction rolls back -> row stays unpublished.
                    meta = await self._publisher.publish(
                        key=row["message_key"],
                        payload=payload,
                        headers={"deposit_id": deposit_id, "dispatch_id": dispatch_id},
                    )

                    await conn.execute("UPDATE outbox SET published_at = $2 WHERE id = $1", row["id"], sent_at)
                    event_type = "REPUBLISHED" if payload.get("reason") == "WATCHDOG" else "PUBLISHED"
                    await conn.execute(
                        """INSERT INTO deposit_events (deposit_id, event_type, details, created_at)
                           VALUES ($1, $2, $3::jsonb, $4)""",
                        row["deposit_id"], event_type,
                        json.dumps({"topic": meta.topic, "partition": meta.partition, "offset": meta.offset,
                                    "dispatch_id": dispatch_id, "reason": payload.get("reason")}),
                        sent_at,
                    )
            published += 1
            log.info("published deposit request to kafka", extra={
                "deposit_id": deposit_id, "dispatch_id": dispatch_id,
                "partition": meta.partition, "offset": meta.offset, "event": event_type})
        return published
