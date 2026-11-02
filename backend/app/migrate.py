"""One-shot migration + seed. Runs on every `docker compose up`; fully idempotent.

A transaction-scoped advisory lock makes concurrent runs safe.
"""
import asyncio
import logging
from pathlib import Path

from app.db import connect_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s migrate %(message)s")
log = logging.getLogger("migrate")

SQL_DIR = Path(__file__).parent / "sql"
MIGRATION_LOCK_ID = 727001


async def main() -> None:
    conn = await connect_with_retry()
    try:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", MIGRATION_LOCK_ID)
            for name in ("schema.sql", "seed.sql"):
                log.info("applying %s", name)
                await conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))
        counts = await conn.fetchrow(
            """SELECT (SELECT count(*) FROM employees) AS employees,
                      (SELECT count(*) FROM accounts)  AS accounts,
                      (SELECT count(*) FROM deposits)  AS deposits,
                      (SELECT count(*) FROM credits)   AS credits"""
        )
        log.info("done: %s", dict(counts))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
