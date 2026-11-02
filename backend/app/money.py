"""Exact money parsing. Never float arithmetic: the TEXT of the amount is validated, then Decimal."""
import re
from decimal import Decimal

from app.config import settings
from app.errors import ApiError

_AMOUNT_RE = re.compile(r"^\d{1,13}(\.\d{1,2})?$")
TWO_PLACES = Decimal("0.01")


def parse_amount(raw: str | int | float) -> Decimal:
    """Accepts "1250.5", "1250.50", 1250, 1250.5. Rejects negatives, zero, >2 decimals,
    exponents (1e3), NaN/Infinity, empty strings and amounts above the per-deposit limit."""
    # repr() of a float gives the shortest exact-looking text (0.1 -> "0.1"); 1e-07 stays rejected.
    text = (repr(raw) if isinstance(raw, float) else str(raw)).strip()

    if text == "":
        raise ApiError(422, "INVALID_AMOUNT", "Amount is required", {"field": "amount", "reason": "required"})
    if text.startswith("-"):
        raise ApiError(422, "INVALID_AMOUNT", "Amount must be positive",
                       {"field": "amount", "reason": "negative", "value": text})
    if not _AMOUNT_RE.match(text):
        reason = "too_many_decimals" if re.match(r"^\d+\.\d{3,}$", text) else "invalid_format"
        message = ("Amount may have at most 2 decimal places" if reason == "too_many_decimals"
                   else "Amount must be a plain decimal number, e.g. 1250.50")
        raise ApiError(422, "INVALID_AMOUNT", message, {"field": "amount", "reason": reason, "value": text})

    amount = Decimal(text).quantize(TWO_PLACES)
    if amount <= 0:
        raise ApiError(422, "INVALID_AMOUNT", "Amount must be greater than zero",
                       {"field": "amount", "reason": "not_positive", "value": text})
    max_amount = Decimal(settings.max_deposit_amount)
    if amount > max_amount:
        raise ApiError(422, "AMOUNT_TOO_LARGE", f"Amount exceeds the per-deposit limit of {max_amount} ILS",
                       {"field": "amount", "max": str(max_amount), "value": text})
    return amount
