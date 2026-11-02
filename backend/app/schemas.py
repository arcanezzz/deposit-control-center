"""API request/response models (also drive the OpenAPI contract at /docs).

Money is always serialized as a STRING with exactly 2 decimals ("1250.50") so no client
ever parses it into a float by accident.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from app.config import settings

DepositStatus = Literal["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
AccountStatus = Literal["ACTIVE", "BLOCKED"]


# --------------------------------------------------------------------------- requests
class SimulationOptions(BaseModel):
    """Demo-only knobs. Not business data: not part of the idempotency hash."""
    model_config = ConfigDict(extra="forbid")

    delay_seconds: int = Field(0, ge=0, le=settings.max_simulated_delay_seconds,
                               description="Hold the deposit in PROCESSING for N seconds (0-30)")
    transient_failure: bool = Field(False, description="Fail before crediting on the FIRST attempt only")


class DepositCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {
            "employee_id": "E-1001", "account_id": "ACC-001", "amount": "250.00",
            "simulate": {"delay_seconds": 0, "transient_failure": False}}},
    )

    employee_id: StrictStr = Field(..., min_length=1, max_length=50)
    account_id: StrictStr = Field(..., min_length=1, max_length=50)
    # Strict types: a JSON boolean is rejected. A string is recommended (exact); a JSON number is
    # accepted but its text must still have at most 2 decimals.
    amount: StrictStr | StrictInt | StrictFloat = Field(
        ..., description='Positive ILS amount, max 2 decimals. Prefer a string, e.g. "1250.50"')
    simulate: SimulationOptions = Field(default_factory=SimulationOptions)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"employee_id": "E-1002"}})
    employee_id: StrictStr = Field(..., min_length=1, max_length=50,
                                   description="Employee requesting the retry (recorded; does not replace the initiator)")


class AccountStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: AccountStatus


# --------------------------------------------------------------------------- responses
class Employee(BaseModel):
    id: str
    name: str
    role: str


class Account(BaseModel):
    id: str
    account_number: str
    owner_name: str
    balance: Decimal
    opening_balance: Decimal
    status: AccountStatus
    updated_at: datetime


class Credit(BaseModel):
    id: UUID
    deposit_id: UUID
    account_id: str
    amount: Decimal
    balance_after: Decimal
    created_at: datetime


class DepositEvent(BaseModel):
    id: UUID
    event_type: str
    actor_employee_id: str | None
    actor_name: str | None
    attempt_no: int | None
    details: dict
    created_at: datetime


class Deposit(BaseModel):
    id: UUID
    idempotency_key: str
    employee_id: str
    employee_name: str
    account_id: str
    account_number: str
    owner_name: str
    amount: Decimal
    status: DepositStatus
    failure_code: str | None
    failure_reason: str | None
    failure_retryable: bool | None
    attempts: int
    simulate_delay_seconds: int
    simulate_transient_failure: bool
    processing_started_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class DepositDetail(Deposit):
    credit: Credit | None
    events: list[DepositEvent]


class DepositAccepted(BaseModel):
    deposit_id: UUID
    status: DepositStatus
    idempotent_replay: bool
    message: str
    deposit: Deposit


class DepositPage(BaseModel):
    items: list[Deposit]
    total: int
    limit: int
    offset: int
    status_counts: dict[str, int]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["up", "down"]
    kafka: Literal["up", "down"]
    outbox_backlog: int | None
