"""Bounded, all-or-error aggregation of documented organization cost pages."""

import json
import math
import time
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Callable

MAX_PAGES = 32
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 30.0


def _decimal(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("Invalid monetary amount")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid monetary amount") from exc
    if not result.is_finite() or not math.isfinite(float(result)):
        raise ValueError("Invalid monetary amount")
    return result


def openai_amount(item: dict) -> Decimal:
    amount = item.get("amount")
    if not isinstance(amount, dict):
        raise ValueError("Invalid OpenAI monetary schema")
    if amount.get("currency") != "usd":
        raise ValueError("Unsupported OpenAI cost currency")
    # OpenAI amount.value is already in the named currency, not cents.
    return _decimal(amount.get("value"))


def anthropic_amount(item: dict) -> Decimal:
    # Anthropic cost_report amount is a decimal string in cents.
    currency = item.get("currency", "USD")
    if not isinstance(currency, str) or currency.lower() != "usd":
        raise ValueError("Unsupported Anthropic cost currency")
    return _decimal(item.get("amount")) / Decimal(100)


def sum_cost_pages(
    url: str,
    headers: dict[str, str],
    amount_parser: Callable[[dict], Decimal],
    *,
    max_pages: int = MAX_PAGES,
    timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
) -> Decimal:
    """Never return a partial total when a page, cursor, or budget is invalid."""
    if max_pages < 1 or timeout_seconds <= 0:
        raise ValueError("Invalid cost report budget")
    deadline = time.monotonic() + timeout_seconds
    seen_cursors: set[str] = set()
    next_url = url
    total = Decimal(0)
    for page_number in range(max_pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Cost report deadline exceeded")
        req = urllib.request.Request(next_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=min(15.0, remaining)) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Cost page exceeds size limit")
        if time.monotonic() >= deadline:
            raise TimeoutError("Cost report deadline exceeded")
        try:
            data = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid cost page JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError("Invalid cost page schema")
        for bucket in data["data"]:
            if not isinstance(bucket, dict) or not isinstance(bucket.get("results"), list):
                raise ValueError("Invalid cost bucket schema")
            for item in bucket["results"]:
                if not isinstance(item, dict):
                    raise ValueError("Invalid cost result schema")
                total += amount_parser(item)
        if not isinstance(data.get("has_more"), bool):
            raise ValueError("Missing cost pagination state")
        if not data["has_more"]:
            return _decimal(total)
        cursor = data.get("next_page")
        if not isinstance(cursor, str) or not cursor.strip() or len(cursor) > 2048:
            raise ValueError("Invalid cost pagination cursor")
        if cursor in seen_cursors:
            raise ValueError("Repeated cost pagination cursor")
        seen_cursors.add(cursor)
        if page_number + 1 == max_pages:
            raise ValueError("Cost report page limit exceeded")
        # Cursor is opaque data, never an endpoint or an unescaped query suffix.
        separator = "&" if "?" in url else "?"
        next_url = url + separator + urllib.parse.urlencode({"page": cursor})
    raise ValueError("Cost report page limit exceeded")
