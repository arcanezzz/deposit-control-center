"""Idempotency-Key semantics, including concurrent sends of the same request."""
import asyncio

from tests.conftest import assert_ledger_consistent, balance, create, new_key, wait_for


async def test_same_key_same_data_returns_same_deposit(client, db, test_account):
    acc = await test_account("0.00")
    key = new_key()
    first = await create(client, acc, amount="100", key=key)
    second = await create(client, acc, amount="100.00", key=key)  # same amount, different text
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert first.json()["deposit_id"] == second.json()["deposit_id"]

    await wait_for(client, first.json()["deposit_id"], {"COMPLETED"})
    third = await create(client, acc, amount="100", key=key)  # replay after completion
    assert third.status_code == 200 and third.json()["status"] == "COMPLETED"
    await asyncio.sleep(1)
    assert str(await balance(db, acc)) == "100.00"  # credited exactly once
    events = [e["event_type"] for e in (await client.get(f"/api/deposits/{first.json()['deposit_id']}")).json()["events"]]
    assert events.count("IDEMPOTENT_REPLAY") == 2


async def test_same_key_different_data_rejected(client, test_account):
    acc, other_acc = await test_account(), await test_account()
    key = new_key()
    assert (await create(client, acc, amount="10", key=key)).status_code == 202
    different_amount = await create(client, acc, amount="11", key=key)
    different_employee = await create(client, acc, amount="10", employee="E-1002", key=key)
    different_account = await create(client, other_acc, amount="10", key=key)
    for r in (different_amount, different_employee, different_account):
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_concurrent_same_key_creates_one_deposit(client, db, test_account):
    acc = await test_account("0.00")
    key = new_key()
    responses = await asyncio.gather(*(create(client, acc, amount="25.00", key=key) for _ in range(20)))
    codes = sorted(r.status_code for r in responses)
    assert codes.count(202) == 1 and codes.count(200) == 19
    assert len({r.json()["deposit_id"] for r in responses}) == 1
    assert await db.fetchval("SELECT count(*) FROM deposits WHERE idempotency_key = $1", key) == 1
    assert await db.fetchval(
        "SELECT count(*) FROM outbox o JOIN deposits d ON d.id = o.deposit_id WHERE d.idempotency_key = $1", key) == 1
    await wait_for(client, responses[0].json()["deposit_id"], {"COMPLETED"})
    assert str(await balance(db, acc)) == "25.00"
    await assert_ledger_consistent(db, acc)
