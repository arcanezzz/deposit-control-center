"""Employees and accounts (reference data + balances)."""
import json

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_pool
from app.errors import ApiError
from app.schemas import Account, AccountStatusUpdate, Credit, Employee

router = APIRouter(prefix="/api", tags=["employees & accounts"])

ACCOUNT_COLS = "id, account_number, owner_name, balance, opening_balance, status, updated_at"


@router.get("/employees", response_model=list[Employee], summary="List employees (simulated identities)")
async def list_employees(pool: asyncpg.Pool = Depends(get_pool)):
    return [dict(r) for r in await pool.fetch("SELECT id, name, role FROM employees ORDER BY id")]


@router.get("/accounts", response_model=list[Account], summary="List accounts with current balances")
async def list_accounts(pool: asyncpg.Pool = Depends(get_pool)):
    return [dict(r) for r in await pool.fetch(f"SELECT {ACCOUNT_COLS} FROM accounts ORDER BY id")]


@router.get("/accounts/{account_id}", response_model=Account, summary="Get one account and its current balance")
async def get_account(account_id: str, pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(f"SELECT {ACCOUNT_COLS} FROM accounts WHERE id = $1", account_id)
    if row is None:
        raise ApiError(404, "ACCOUNT_NOT_FOUND", f"Account '{account_id}' does not exist")
    return dict(row)


@router.get("/accounts/{account_id}/credits", response_model=list[Credit],
            summary="Credit ledger of an account (balance = opening_balance + sum of credits)")
async def list_account_credits(account_id: str, limit: int = Query(50, ge=1, le=500),
                               pool: asyncpg.Pool = Depends(get_pool)):
    if not await pool.fetchval("SELECT EXISTS (SELECT 1 FROM accounts WHERE id = $1)", account_id):
        raise ApiError(404, "ACCOUNT_NOT_FOUND", f"Account '{account_id}' does not exist")
    rows = await pool.fetch(
        "SELECT * FROM credits WHERE account_id = $1 ORDER BY created_at DESC LIMIT $2", account_id, limit)
    return [dict(r) for r in rows]


@router.patch("/accounts/{account_id}/status", response_model=Account,
              summary="DEMO ONLY: block / unblock an account (to demonstrate a permanent failure)")
async def set_account_status(account_id: str, body: AccountStatusUpdate, pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(
        f"""UPDATE accounts SET status = $2, updated_at = clock_timestamp()
             WHERE id = $1 RETURNING {ACCOUNT_COLS}""",
        account_id, body.status)
    if row is None:
        raise ApiError(404, "ACCOUNT_NOT_FOUND", f"Account '{account_id}' does not exist")
    return dict(row)
