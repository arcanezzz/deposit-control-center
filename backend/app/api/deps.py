import asyncpg
from fastapi import Request

from app.outbox_relay import OutboxRelay


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_relay(request: Request) -> OutboxRelay:
    return request.app.state.relay
