"""End-to-end processing through real Kafka + worker: statuses, credit, retry rules, timeline."""
import asyncio

from tests.conftest import assert_ledger_consistent, balance, create, wait_for


async def test_deposit_completes_and_credits_once(client, db, test_account):
    acc = await test_account("100.00")
    r = await create(client, acc, amount="23.45")
    d = await wait_for(client, r.json()["deposit_id"], {"COMPLETED"})
    assert d["attempts"] == 1
    assert d["credit"]["amount"] == "23.45" and d["credit"]["balance_after"] == "123.45"
    assert str(await balance(db, acc)) == "123.45"
    types = [e["event_type"] for e in d["events"]]
    assert types[:4] == ["RECEIVED", "PUBLISHED", "ATTEMPT_STARTED", "COMPLETED"]
    await assert_ledger_consistent(db, acc)


async def test_processing_is_visible_and_not_a_credit(client, db, test_account):
    acc = await test_account("0.00")
    r = await create(client, acc, amount="5", delay=3)
    d = await wait_for(client, r.json()["deposit_id"], {"PROCESSING"})
    assert d["credit"] is None
    assert str(await balance(db, acc)) == "0.00"  # PROCESSING never implies a credit
    await wait_for(client, d["id"], {"COMPLETED"})
    assert str(await balance(db, acc)) == "5.00"


async def test_transient_failure_then_retry_by_other_employee(client, db, test_account):
    acc = await test_account("0.00")
    r = await create(client, acc, amount="40", employee="E-1003", transient=True)
    dep_id = r.json()["deposit_id"]
    failed = await wait_for(client, dep_id, {"FAILED"})
    assert failed["failure_retryable"] is True and failed["failure_code"] == "SIMULATED_TRANSIENT"
    assert failed["credit"] is None and str(await balance(db, acc)) == "0.00"

    retry = await client.post(f"/api/deposits/{dep_id}/retry", json={"employee_id": "E-1002"})
    assert retry.status_code == 202 and retry.json()["status"] == "PENDING"
    done = await wait_for(client, dep_id, {"COMPLETED"})

    assert done["attempts"] == 2
    assert done["employee_id"] == "E-1003"  # original initiator is NOT overwritten
    retry_events = [e for e in done["events"] if e["event_type"] == "RETRY_REQUESTED"]
    assert len(retry_events) == 1 and retry_events[0]["actor_employee_id"] == "E-1002"
    types = [e["event_type"] for e in done["events"]]
    for expected in ("RECEIVED", "ATTEMPT_STARTED", "FAILED", "RETRY_REQUESTED", "COMPLETED"):
        assert expected in types
    assert str(await balance(db, acc)) == "40.00"
    await assert_ledger_consistent(db, acc)


async def test_retry_rules(client, test_account):
    acc = await test_account()
    # completed -> rejected
    done = (await create(client, acc, amount="1")).json()["deposit_id"]
    await wait_for(client, done, {"COMPLETED"})
    r = await client.post(f"/api/deposits/{done}/retry", json={"employee_id": "E-1002"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEPOSIT_ALREADY_COMPLETED"
    # active (processing) -> rejected
    active = (await create(client, acc, amount="1", delay=3)).json()["deposit_id"]
    r = await client.post(f"/api/deposits/{active}/retry", json={"employee_id": "E-1002"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROCESSING_ALREADY_ACTIVE"
    # unknown employee / unknown deposit
    r = await client.post(f"/api/deposits/{done}/retry", json={"employee_id": "E-0000"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "EMPLOYEE_NOT_FOUND"
    r = await client.post("/api/deposits/00000000-0000-4000-8000-000000000000/retry", json={"employee_id": "E-1002"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "DEPOSIT_NOT_FOUND"


async def test_concurrent_retries_only_one_wins(client, db, test_account):
    acc = await test_account("0.00")
    dep = (await create(client, acc, amount="7", transient=True)).json()["deposit_id"]
    await wait_for(client, dep, {"FAILED"})
    results = await asyncio.gather(*(client.post(f"/api/deposits/{dep}/retry", json={"employee_id": "E-1004"})
                                     for _ in range(5)))
    assert sorted(r.status_code for r in results) == [202, 409, 409, 409, 409]
    await wait_for(client, dep, {"COMPLETED"})
    assert str(await balance(db, acc)) == "7.00"


async def test_account_blocked_after_acceptance_is_permanent_failure(client, db, test_account):
    acc = await test_account("10.00")
    dep = (await create(client, acc, amount="5", delay=3)).json()["deposit_id"]
    await wait_for(client, dep, {"PROCESSING"})
    await client.patch(f"/api/accounts/{acc}/status", json={"status": "BLOCKED"})
    failed = await wait_for(client, dep, {"FAILED", "COMPLETED"})
    assert failed["status"] == "FAILED"
    assert failed["failure_retryable"] is False and failed["failure_code"] == "ACCOUNT_BLOCKED"
    assert str(await balance(db, acc)) == "10.00"
    r = await client.post(f"/api/deposits/{dep}/retry", json={"employee_id": "E-1002"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PERMANENT_FAILURE"


async def test_search_filters(client, test_account):
    acc = await test_account()
    dep = (await create(client, acc, amount="3", employee="E-1005")).json()["deposit_id"]
    await wait_for(client, dep, {"COMPLETED"})
    page = (await client.get("/api/deposits", params={"account_id": acc, "employee_id": "E-1005",
                                                       "status": ["COMPLETED", "FAILED"]})).json()
    assert page["total"] == 1 and page["items"][0]["id"] == dep
    assert page["status_counts"]["COMPLETED"] == 1
    none = (await client.get("/api/deposits", params={"account_id": acc, "status": "PENDING"})).json()
    assert none["total"] == 0
    seed = (await client.get("/api/deposits", params={"created_from": "2026-10-29", "created_to": "2026-10-29"})).json()
    assert {i["idempotency_key"] for i in seed["items"]} >= {"seed-0002"}


async def test_deposit_is_immutable_in_db(db):
    import asyncpg
    import pytest
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE deposits SET amount = amount + 1 WHERE idempotency_key = 'seed-0001'")


async def test_timeline_order_published_before_attempt(client, test_account):
    """Regression: the worker can consume a message before the relay commits PUBLISHED;
    the PUBLISHED timestamp is taken before sending, so the timeline order is always right."""
    import asyncio as _asyncio
    acc = await test_account()
    ids = [(await create(client, acc, amount="1")).json()["deposit_id"] for _ in range(10)]
    details = await _asyncio.gather(*(wait_for(client, i, {"COMPLETED"}) for i in ids))
    for d in details:
        types = [e["event_type"] for e in d["events"]]
        assert types.index("RECEIVED") < types.index("PUBLISHED") < types.index("ATTEMPT_STARTED") \
            < types.index("COMPLETED"), types
