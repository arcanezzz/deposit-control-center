"""Deposits: create (async, idempotent), search, track, event history."""
from datetime import date
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from app.api.deps import get_pool, get_relay
from app.errors import ApiError
from app.money import parse_amount
from app.outbox_relay import OutboxRelay
from app.schemas import DepositAccepted, DepositCreate, DepositDetail, DepositEvent, DepositPage
from app.services import deposits as svc

router = APIRouter(prefix="/api/deposits", tags=["deposits"])

ACCEPTED_MESSAGE = ("The deposit request was accepted for processing. "
                    "The account has NOT been credited yet — track the status with the deposit_id.")
REPLAY_MESSAGE = ("Idempotent replay: a deposit with this Idempotency-Key and the same data already exists. "
                  "Returned the existing deposit; nothing new was created or credited.")


@router.post(
    "",
    status_code=202,
    response_model=DepositAccepted,
    summary="Request a deposit (asynchronous; requires Idempotency-Key)",
    responses={
        200: {"description": "Idempotent replay — same key and same data; existing deposit returned",
              "model": DepositAccepted},
        202: {"description": "Accepted for processing (status PENDING). NOT credited yet."},
        400: {"description": "IDEMPOTENCY_KEY_REQUIRED / INVALID_IDEMPOTENCY_KEY"},
        404: {"description": "EMPLOYEE_NOT_FOUND / ACCOUNT_NOT_FOUND"},
        409: {"description": "IDEMPOTENCY_KEY_REUSED — same key, different employee/account/amount"},
        422: {"description": "INVALID_AMOUNT / AMOUNT_TOO_LARGE / ACCOUNT_BLOCKED / VALIDATION_ERROR"},
    },
)
async def create_deposit(
    body: DepositCreate,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key",
                                         description="Client-generated unique key (e.g. UUID), 8-128 chars"),
    pool: asyncpg.Pool = Depends(get_pool),
    relay: OutboxRelay = Depends(get_relay),
):
    key = svc.validate_idempotency_key(idempotency_key)
    amount = parse_amount(body.amount)
    deposit, created = await svc.create_deposit(pool, key, body, amount)
    if created:
        relay.kick()  # publish to Kafka right away (fast path); the relay loop is the safety net

    payload = DepositAccepted(
        deposit_id=deposit["id"], status=deposit["status"], idempotent_replay=not created,
        message=ACCEPTED_MESSAGE if created else REPLAY_MESSAGE, deposit=deposit,
    ).model_dump(mode="json")
    return JSONResponse(
        payload,
        status_code=202 if created else 200,
        headers={"Location": f"/api/deposits/{deposit['id']}", "X-Deposit-Id": str(deposit["id"]),
                 "Idempotent-Replay": "true" if not created else "false"},
    )


@router.get("", response_model=DepositPage, summary="Search deposits (investigation)")
async def search_deposits(
    employee_id: str | None = Query(None, description="Initiating employee"),
    account_id: str | None = Query(None),
    status: list[str] | None = Query(None, description="Repeatable: ?status=FAILED&status=PENDING"),
    created_from: date | None = Query(None, description="YYYY-MM-DD, Israel time, inclusive"),
    created_to: date | None = Query(None, description="YYYY-MM-DD, Israel time, inclusive (whole day)"),
    idempotency_key: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
):
    return await svc.list_deposits(pool, employee_id=employee_id, account_id=account_id, statuses=status,
                                   created_from=created_from, created_to=created_to,
                                   idempotency_key=idempotency_key, limit=limit, offset=offset)


@router.get("/{deposit_id}", response_model=DepositDetail, summary="Track one deposit: status, credit, timeline")
async def get_deposit(deposit_id: UUID, pool: asyncpg.Pool = Depends(get_pool)):
    deposit = await svc.get_deposit(pool, deposit_id)
    return JSONResponse(DepositDetail(**deposit).model_dump(mode="json"),
                        headers={"X-Deposit-Id": str(deposit_id)})


@router.get("/{deposit_id}/events", response_model=list[DepositEvent], summary="Event history of a deposit")
async def get_deposit_events(deposit_id: UUID, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM deposits WHERE id = $1)", deposit_id):
            raise ApiError(404, "DEPOSIT_NOT_FOUND", f"Deposit {deposit_id} does not exist")
        return await svc.get_events(conn, deposit_id)
