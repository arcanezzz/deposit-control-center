-- Deposit Control Center schema.
-- Idempotent: safe to run on every startup (IF NOT EXISTS / OR REPLACE).
-- Money is NUMERIC(18,2) everywhere — never float.

CREATE TABLE IF NOT EXISTS employees (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS accounts (
    id               TEXT PRIMARY KEY,
    account_number   TEXT NOT NULL UNIQUE,
    owner_name       TEXT NOT NULL,
    -- opening_balance lets anyone verify: balance = opening_balance + SUM(credits.amount)
    opening_balance  NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (opening_balance >= 0),
    balance          NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
    status           TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'BLOCKED')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS deposits (
    id                          UUID PRIMARY KEY,
    -- Idempotency scope: GLOBAL (unique across all employees), no expiry.
    idempotency_key             TEXT NOT NULL UNIQUE,
    -- sha256 of the business data (employee_id, account_id, normalized amount)
    request_hash                TEXT NOT NULL,
    employee_id                 TEXT NOT NULL REFERENCES employees(id),  -- original initiator, never changes
    account_id                  TEXT NOT NULL REFERENCES accounts(id),
    amount                      NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    status                      TEXT NOT NULL DEFAULT 'PENDING'
                                CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    failure_code                TEXT,
    failure_reason              TEXT,
    failure_retryable           BOOLEAN,   -- NULL unless FAILED; true = transient, false = permanent
    attempts                    INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    current_dispatch_id         UUID,      -- only a Kafka message carrying this id may process the deposit
    simulate_delay_seconds      INT NOT NULL DEFAULT 0 CHECK (simulate_delay_seconds BETWEEN 0 AND 30),
    simulate_transient_failure  BOOLEAN NOT NULL DEFAULT false,
    processing_started_at       TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at                TIMESTAMPTZ,
    CONSTRAINT failed_has_retryable_flag
        CHECK (status <> 'FAILED' OR failure_retryable IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_deposits_employee   ON deposits (employee_id);
CREATE INDEX IF NOT EXISTS idx_deposits_account    ON deposits (account_id);
CREATE INDEX IF NOT EXISTS idx_deposits_status     ON deposits (status);
CREATE INDEX IF NOT EXISTS idx_deposits_created_at ON deposits (created_at DESC);

-- Credit ledger. UNIQUE(deposit_id) is the last line of defence against a double credit.
CREATE TABLE IF NOT EXISTS credits (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deposit_id     UUID NOT NULL UNIQUE REFERENCES deposits(id),
    account_id     TEXT NOT NULL REFERENCES accounts(id),
    amount         NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    balance_after  NUMERIC(18,2) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_credits_account ON credits (account_id, created_at DESC);

-- Append-only processing history (the investigation timeline).
CREATE TABLE IF NOT EXISTS deposit_events (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deposit_id         UUID NOT NULL REFERENCES deposits(id),
    event_type         TEXT NOT NULL CHECK (event_type IN (
                           'RECEIVED', 'IDEMPOTENT_REPLAY', 'PUBLISHED', 'ATTEMPT_STARTED', 'FAILED',
                           'RETRY_REQUESTED', 'COMPLETED', 'RECOVERED_INTERRUPTED',
                           'DUPLICATE_IGNORED', 'STALE_MESSAGE_IGNORED', 'CREDIT_ABORTED', 'REPUBLISHED')),
    actor_employee_id  TEXT REFERENCES employees(id),   -- NULL = system / worker
    attempt_no         INT,
    details            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_events_deposit ON deposit_events (deposit_id, created_at);

-- Transactional outbox: written in the same transaction as the deposit / retry,
-- published to Kafka by the relay.
CREATE TABLE IF NOT EXISTS outbox (
    id            BIGSERIAL PRIMARY KEY,
    deposit_id    UUID NOT NULL REFERENCES deposits(id),
    message_key   TEXT NOT NULL,         -- Kafka key (account_id)
    payload       JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    published_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;

-- Defence in depth: the business identity of a deposit can never change.
CREATE OR REPLACE FUNCTION deposits_guard_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.employee_id     IS DISTINCT FROM OLD.employee_id
    OR NEW.account_id      IS DISTINCT FROM OLD.account_id
    OR NEW.amount          IS DISTINCT FROM OLD.amount
    OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
    OR NEW.request_hash    IS DISTINCT FROM OLD.request_hash
    OR NEW.created_at      IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'deposit % is immutable: employee, account, amount and idempotency key cannot change', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_deposits_immutable ON deposits;
CREATE TRIGGER trg_deposits_immutable
    BEFORE UPDATE ON deposits
    FOR EACH ROW EXECUTE FUNCTION deposits_guard_immutable();
