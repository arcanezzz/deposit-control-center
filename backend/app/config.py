"""Central configuration, read from environment variables (see .env.example)."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://app:app_local_dev@postgres:5432/deposits"
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_topic: str = "deposit-requests"
    kafka_consumer_group: str = "deposit-workers"

    # Worker / recovery tuning
    processing_lease_seconds: int = 60
    recovery_interval_seconds: int = 10
    pending_watchdog_seconds: int = 120
    outbox_relay_interval_seconds: float = 2.0

    # Business limits
    max_deposit_amount: str = "1000000.00"
    max_simulated_delay_seconds: int = 30

    log_level: str = "INFO"


settings = Settings()
