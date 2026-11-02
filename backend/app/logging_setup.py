"""Structured JSON logging. Every line can carry deposit_id / dispatch_id / attempt so a single
deposit can be traced across the API, the outbox relay, Kafka and the worker:

    docker compose logs api worker | grep <deposit_id>
"""
import json
import logging
import sys
from datetime import datetime, timezone

from app.config import settings

CONTEXT_FIELDS = ("deposit_id", "dispatch_id", "attempt", "employee_id", "account_id",
                  "event", "status", "http_method", "http_path", "http_status", "duration_ms",
                  "partition", "offset", "error_code")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = str(value) if not isinstance(value, (int, float, bool)) else value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(service: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    # keep third-party noise down
    for noisy in ("aiokafka", "kafka", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
