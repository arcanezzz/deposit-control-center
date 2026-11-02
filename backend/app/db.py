"""asyncpg connection pool with retry/backoff — never assume Postgres is ready just because its container started."""
import asyncio
import logging

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)


async def connect_with_retry(attempts: int = 30, delay: float = 2.0) -> asyncpg.Connection:
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await asyncpg.connect(settings.database_url)
        except (OSError, asyncpg.PostgresError) as exc:
            last_exc = exc
            log.warning("postgres not ready (attempt %d/%d): %s", i, attempts, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"could not connect to postgres after {attempts} attempts") from last_exc


async def create_pool_with_retry(attempts: int = 30, delay: float = 2.0) -> asyncpg.Pool:
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        except (OSError, asyncpg.PostgresError) as exc:
            last_exc = exc
            log.warning("postgres not ready (attempt %d/%d): %s", i, attempts, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"could not create postgres pool after {attempts} attempts") from last_exc
