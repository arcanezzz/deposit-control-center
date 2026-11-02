"""Integration tests: run against the LIVE stack (API + real Kafka + worker + Postgres).

    docker compose --profile test run --rm tests

Every test creates its own TEST-xxxx accounts directly in the DB, so demo balances are never touched.
"""
import asyncio
import os
import uuid
from decimal import Decimal

import asyncpg
import httpx
import pytest

API_URL = os.environ.get("API_URL", "http://api:8000")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://app:app_local_dev@postgres:5432/deposits")


@pytest.fixture
async def db():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=30)
    yield pool
    await pool.close()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=API_URL, timeout=30) as c:
        yield c


@pytest.fixture
async def test_account(db):
    """A fresh ACTIVE account with a known opening balance."""
    async def make(opening: str = "100.00", status: str = "ACTIVE") -> str:
        acc_id = f"TEST-{uuid.uuid4().hex[:8]}"
        await db.execute(
            """INSERT INTO accounts (id, account_number, owner_name, opening_balance, balance, status)
               VALUES ($1, $2, 'חשבון בדיקה', $3, $3, $4)""",
            acc_id, f"99-{uuid.uuid4().hex[:10]}", Decimal(opening), status)
        return acc_id
    return make


def new_key() -> str:
    return f"test-{uuid.uuid4()}"


async def create(client: httpx.AsyncClient, account_id: str, amount="10.00", employee="E-1001",
                 key: str | None = None, **simulate) -> httpx.Response:
    return await client.post(
        "/api/deposits",
        headers={"Idempotency-Key": key or new_key()},
        json={"employee_id": employee, "account_id": account_id, "amount": amount,
              "simulate": {"delay_seconds": simulate.get("delay", 0),
                           "transient_failure": simulate.get("transient", False)}},
    )


async def wait_for(client: httpx.AsyncClient, deposit_id: str, statuses: set[str], timeout: float = 30) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        d = (await client.get(f"/api/deposits/{deposit_id}")).json()
        if d["status"] in statuses:
            return d
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"deposit {deposit_id} stuck in {d['status']}, expected {statuses}")
        await asyncio.sleep(0.3)


async def balance(db, account_id: str) -> Decimal:
    return await db.fetchval("SELECT balance FROM accounts WHERE id = $1", account_id)


async def assert_ledger_consistent(db, account_id: str) -> None:
    row = await db.fetchrow(
        """SELECT a.balance, a.opening_balance + COALESCE(SUM(c.amount), 0) AS expected
             FROM accounts a LEFT JOIN credits c ON c.account_id = a.id
            WHERE a.id = $1 GROUP BY a.id""", account_id)
    assert row["balance"] == row["expected"], f"balance {row['balance']} != opening + credits {row['expected']}"
