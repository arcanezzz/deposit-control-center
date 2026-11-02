"""Worker process: Kafka consumer + recovery loop.

Delivery semantics: AT-LEAST-ONCE.
- enable_auto_commit=False; the offset is committed only AFTER the DB work for the message is done.
- If processing raises an infrastructure error (e.g. DB down) the offset is NOT committed; the
  consumer seeks back to the same message and retries after a backoff.
- Poison messages (bad JSON / unknown deposit) are logged and committed (skipped) so they never
  block the partition.
- Partitions in one poll batch are processed concurrently; messages within a partition in order.
- SIGTERM: finish the in-flight batch, commit, then stop (compose stop_grace_period covers the
  max simulated delay).
"""
import asyncio
import logging
import signal

from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.errors import KafkaError

from app.config import settings
from app.db import create_pool_with_retry
from app.logging_setup import setup_logging
from app.worker.processor import PoisonMessage, process_message
from app.worker.recovery import run_recovery_loop

setup_logging("worker")
log = logging.getLogger("worker")


async def start_consumer_with_retry(stop: asyncio.Event) -> AIOKafkaConsumer | None:
    delay = 2.0
    while not stop.is_set():
        consumer = AIOKafkaConsumer(
            settings.kafka_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=300_000,
            session_timeout_ms=30_000,
            heartbeat_interval_ms=3_000,
        )
        try:
            await consumer.start()
            log.info("kafka consumer started", extra={"event": settings.kafka_consumer_group})
            return consumer
        except (KafkaError, OSError) as exc:
            log.warning("kafka not ready, retrying in %.0fs: %s", delay, exc)
            try:
                await consumer.stop()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
    return None


async def handle_partition(consumer: AIOKafkaConsumer, pool, tp: TopicPartition, messages) -> None:
    for msg in messages:
        try:
            outcome = await process_message(pool, msg.value, msg.partition, msg.offset)
        except PoisonMessage as exc:
            log.error("poison message skipped: %s", exc, extra={"partition": msg.partition, "offset": msg.offset})
            outcome = "poison"
        except Exception as exc:  # infrastructure failure: do NOT commit, re-read this message later
            log.error("processing failed, will retry the same message: %s", exc,
                      extra={"partition": msg.partition, "offset": msg.offset})
            consumer.seek(tp, msg.offset)
            await asyncio.sleep(3)
            return
        await consumer.commit({tp: msg.offset + 1})
        log.debug("offset committed", extra={"partition": msg.partition, "offset": msg.offset, "event": outcome})


async def consume_loop(pool, stop: asyncio.Event) -> None:
    consumer = await start_consumer_with_retry(stop)
    if consumer is None:
        return
    try:
        while not stop.is_set():
            try:
                batch = await consumer.getmany(timeout_ms=1000, max_records=20)
            except KafkaError as exc:
                log.warning("kafka poll failed: %s", exc)
                await asyncio.sleep(2)
                continue
            if batch:
                await asyncio.gather(*(handle_partition(consumer, pool, tp, msgs) for tp, msgs in batch.items()))
    finally:
        await consumer.stop()
        log.info("kafka consumer stopped")


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    pool = await create_pool_with_retry()
    log.info("worker started", extra={"event": f"lease={settings.processing_lease_seconds}s"})
    try:
        await asyncio.gather(consume_loop(pool, stop), run_recovery_loop(pool, stop))
    finally:
        await pool.close()
        log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
