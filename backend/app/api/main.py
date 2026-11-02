"""FastAPI application: REST API + background outbox relay."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import routes_deposits, routes_reference
from app.db import create_pool_with_retry
from app.errors import register_error_handlers
from app.kafka import KafkaPublisher
from app.logging_setup import setup_logging
from app.outbox_relay import OutboxRelay
from app.schemas import Health

setup_logging("api")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool_with_retry()
    app.state.publisher = KafkaPublisher()
    await app.state.publisher.ensure_started()  # ok if Kafka is down: relay retries later
    app.state.relay = OutboxRelay(app.state.pool, app.state.publisher)
    relay_task = asyncio.create_task(app.state.relay.run(), name="outbox-relay")
    log.info("api started")
    try:
        yield
    finally:
        app.state.relay.stop()
        await asyncio.wait([relay_task], timeout=5)
        await app.state.publisher.stop()
        await app.state.pool.close()
        log.info("api stopped")


app = FastAPI(
    title="Deposit Control Center API",
    version="1.0.0",
    description=(
        "Simulated bank deposits processed asynchronously through Kafka.\n\n"
        "* `POST /api/deposits` only **accepts** a request (202, status PENDING) — the account is credited "
        "later by the worker.\n"
        "* Every create requires an `Idempotency-Key` header (global scope, no expiry).\n"
        "* Money is always a string with 2 decimals.\n"
        "* Errors: `{\"error\": {\"code\", \"message\", \"details\"}}`."
    ),
    lifespan=lifespan,
)
register_error_handlers(app)
app.include_router(routes_deposits.router)
app.include_router(routes_reference.router)


@app.middleware("http")
async def access_log(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/api/health":
        log.info("http request", extra={
            "http_method": request.method, "http_path": request.url.path, "http_status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "deposit_id": response.headers.get("X-Deposit-Id")})
    return response


@app.get("/api/health", response_model=Health, tags=["ops"],
         summary="Liveness/readiness: DB is required; Kafka is reported but not required (outbox)")
async def health(request: Request):
    db_up, backlog = True, None
    try:
        backlog = await request.app.state.relay.backlog()
    except Exception:  # noqa: BLE001
        db_up = False
    kafka_up = await request.app.state.publisher.broker_reachable()
    body = Health(status="ok" if db_up and kafka_up else "degraded", database="up" if db_up else "down",
                  kafka="up" if kafka_up else "down", outbox_backlog=backlog)
    return JSONResponse(body.model_dump(), status_code=200 if db_up else 503)
