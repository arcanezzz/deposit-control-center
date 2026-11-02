"""Kafka producer wrapper.

- acks=all + enable_idempotence: the broker de-duplicates producer retries.
- Lazy start with retry: the API must keep accepting deposits even if Kafka is down
  (requests wait in the outbox table and are published once Kafka is back).
"""
import asyncio
import json
import logging

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.config import settings

log = logging.getLogger(__name__)


class KafkaPublisher:
    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    @property
    def connected(self) -> bool:
        return self._producer is not None

    async def broker_reachable(self, timeout: float = 2.0) -> bool:
        """Cheap, real liveness probe: can we open a TCP connection to a bootstrap broker right now?
        (A producer object existing does not mean the broker is up.)"""
        for server in settings.kafka_bootstrap_servers.split(","):
            host, _, port = server.strip().rpartition(":")
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout)
                writer.close()
                return True
            except (OSError, asyncio.TimeoutError):
                continue
        return False

    async def ensure_started(self) -> bool:
        if self._producer is not None:
            return True
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            acks="all",
            enable_idempotence=True,
            key_serializer=lambda k: k.encode("utf-8"),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=10000,
        )
        try:
            await producer.start()
        except (KafkaError, OSError) as exc:
            log.warning("kafka not available yet: %s", exc)
            try:
                await producer.stop()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
            return False
        self._producer = producer
        log.info("kafka producer connected")
        return True

    async def publish(self, key: str, payload: dict, headers: dict[str, str]):
        """Send and wait for the broker ack. Returns RecordMetadata (partition, offset)."""
        if not await self.ensure_started():
            raise ConnectionError("kafka is not available")
        assert self._producer is not None
        try:
            return await self._producer.send_and_wait(
                settings.kafka_topic,
                key=key,
                value=payload,
                headers=[(k, v.encode("utf-8")) for k, v in headers.items()],
            )
        except KafkaError:
            # drop the producer so the next call reconnects cleanly
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._producer is not None:
            producer, self._producer = self._producer, None
            try:
                await producer.stop()
            except Exception:  # noqa: BLE001
                pass
