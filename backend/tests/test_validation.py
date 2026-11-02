"""Input validation: clear errors, and rejected requests never create a deposit record."""
import pytest

from tests.conftest import create, new_key


@pytest.mark.parametrize("amount, reason", [
    ("-5", "negative"),
    ("0", "not_positive"),
    ("0.00", "not_positive"),
    ("0.001", "too_many_decimals"),
    ("10.555", "too_many_decimals"),
    ("", "required"),
    ("1e3", "invalid_format"),
    ("abc", "invalid_format"),
    ("NaN", "invalid_format"),
    ("Infinity", "invalid_format"),
    ("1,000", "invalid_format"),
])
async def test_invalid_amounts_rejected_with_reason(client, db, test_account, amount, reason):
    acc = await test_account()
    key = new_key()
    r = await create(client, acc, amount=amount, key=key)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "INVALID_AMOUNT"
    assert err["details"]["reason"] == reason
    assert await db.fetchval("SELECT count(*) FROM deposits WHERE idempotency_key = $1", key) == 0


async def test_amount_over_limit(client, test_account):
    r = await create(client, await test_account(), amount="1000000.01")
    assert r.status_code == 422 and r.json()["error"]["code"] == "AMOUNT_TOO_LARGE"


async def test_boolean_amount_rejected(client, test_account):
    r = await client.post("/api/deposits", headers={"Idempotency-Key": new_key()},
                          json={"employee_id": "E-1001", "account_id": await test_account(), "amount": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("amount", ["0.01", "1", "1250.5", "1250.50", "1000000.00", 99, 12.5])
async def test_valid_amounts_accepted(client, test_account, amount):
    r = await create(client, await test_account(), amount=amount)
    assert r.status_code == 202, r.text


async def test_missing_idempotency_key(client, test_account):
    r = await client.post("/api/deposits", json={"employee_id": "E-1001", "account_id": await test_account(),
                                                 "amount": "10"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


@pytest.mark.parametrize("key", ["short", "has space in it", "x" * 129])
async def test_invalid_idempotency_key(client, test_account, key):
    r = await create(client, await test_account(), key=key)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"


async def test_unknown_account_and_employee(client, test_account):
    r = await create(client, "NO-SUCH-ACCOUNT")
    assert r.status_code == 404 and r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    r = await create(client, await test_account(), employee="E-0000")
    assert r.status_code == 404 and r.json()["error"]["code"] == "EMPLOYEE_NOT_FOUND"


async def test_blocked_account_rejected_without_record(client, db, test_account):
    acc = await test_account(status="BLOCKED")
    key = new_key()
    r = await create(client, acc, key=key)
    assert r.status_code == 422 and r.json()["error"]["code"] == "ACCOUNT_BLOCKED"
    assert await db.fetchval("SELECT count(*) FROM deposits WHERE idempotency_key = $1", key) == 0


async def test_accepted_response_is_not_a_credit(client, db, test_account):
    acc = await test_account("100.00")
    r = await create(client, acc, amount="50", delay=2)
    body = r.json()
    assert r.status_code == 202
    assert body["status"] == "PENDING" and body["idempotent_replay"] is False
    assert "NOT been credited" in body["message"]
    assert r.headers["Location"] == f"/api/deposits/{body['deposit_id']}"
    # Right after acceptance the balance has not changed.
    assert str(await db.fetchval("SELECT balance FROM accounts WHERE id = $1", acc)) == "100.00"


async def test_date_range_and_status_filters_validated(client):
    r = await client.get("/api/deposits", params={"created_from": "2026-10-30", "created_to": "2026-10-29"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_DATE_RANGE"
    r = await client.get("/api/deposits", params={"status": "BOGUS"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_STATUS"
