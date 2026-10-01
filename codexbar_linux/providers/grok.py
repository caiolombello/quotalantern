"""Grok provider — weekly usage from the Grok Build OAuth session."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.models import ProviderUsage, UsageWindow

BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
OIDC_SCOPE_PREFIX = "https://auth.x.ai::"
LEGACY_SCOPE = "https://accounts.x.ai/sign-in"
MAX_RESPONSE_BYTES = 1_048_576
TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class _GrokCredentials:
    access_token: str
    email: Optional[str]
    expires_at: Optional[datetime]


class _GrokAuthError(ValueError):
    pass


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _credentials_from_payload(payload: Any) -> _GrokCredentials:
    if not isinstance(payload, dict):
        raise _GrokAuthError("Grok auth.json is invalid. Run `grok login`")

    oidc_entries: list[tuple[str, dict[str, Any]]] = []
    legacy_entries: list[tuple[str, dict[str, Any]]] = []
    for scope, value in payload.items():
        if not isinstance(scope, str) or not isinstance(value, dict):
            continue
        token = value.get("key")
        if not isinstance(token, str) or not token.strip():
            continue
        if scope.startswith(OIDC_SCOPE_PREFIX):
            oidc_entries.append((scope, value))
        elif scope == LEGACY_SCOPE or "/sign-in" in scope:
            legacy_entries.append((scope, value))

    candidates = oidc_entries or legacy_entries
    if not candidates:
        raise _GrokAuthError("Grok credentials missing. Run `grok login`")

    def expiry_key(item: tuple[str, dict[str, Any]]) -> tuple[float, str]:
        scope, entry = item
        expires_at = _parse_datetime(entry.get("expires_at"))
        timestamp = expires_at.timestamp() if expires_at else 0.0
        return timestamp, scope

    _, entry = max(candidates, key=expiry_key)
    token = str(entry["key"]).strip()
    email = entry.get("email")
    if not isinstance(email, str) or not email.strip():
        email = None
    else:
        email = email.strip()[:254]
    return _GrokCredentials(
        access_token=token,
        email=email,
        expires_at=_parse_datetime(entry.get("expires_at")),
    )


def _load_credentials() -> _GrokCredentials:
    configured_home = os.environ.get("GROK_HOME", "").strip()
    grok_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".grok"
    auth_path = grok_home / "auth.json"
    try:
        payload = json.loads(auth_path.read_text())
    except FileNotFoundError as exc:
        raise _GrokAuthError("Grok auth.json not found. Run `grok login`") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise _GrokAuthError("Grok auth.json is invalid. Run `grok login`") from exc
    return _credentials_from_payload(payload)


def _request_once(access_token: str) -> tuple[int, Any]:
    request = urllib.request.Request(
        BILLING_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "codexbar-linux/0.4",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        exc.read(1024)
        return exc.code, None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Grok billing response is too large")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Grok billing returned invalid JSON") from exc


def _request_billing(access_token: str) -> tuple[int, Any]:
    for attempt in range(2):
        try:
            status, payload = _request_once(access_token)
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 0:
                continue
            raise
        if status not in TRANSIENT_HTTP_STATUSES or attempt == 1:
            return status, payload
    raise RuntimeError("Grok billing request failed")


def _reset_description(resets_at: Optional[datetime], now: datetime) -> str:
    if not resets_at:
        return "weekly"
    seconds = int((resets_at - now).total_seconds())
    if seconds <= 0:
        return "resetting…"
    minutes = seconds // 60
    if minutes < 60:
        return f"resets in {minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"resets in {hours}h"
    return f"resets in {hours // 24}d {hours % 24}h"


def _parse_billing(payload: Any, now: Optional[datetime] = None) -> UsageWindow:
    if not isinstance(payload, dict) or not isinstance(payload.get("config"), dict):
        raise ValueError("Grok billing response is missing config")
    config = payload["config"]
    raw_percent = config.get("creditUsagePercent")
    if isinstance(raw_percent, bool) or not isinstance(raw_percent, (int, float)):
        raise ValueError("Grok billing response is missing usage percent")
    percent = float(raw_percent)
    if not math.isfinite(percent):
        raise ValueError("Grok billing response has invalid usage percent")

    period = config.get("currentPeriod")
    if not isinstance(period, dict):
        period = {}
    period_type = period.get("type")
    if period_type not in (None, "USAGE_PERIOD_TYPE_WEEKLY"):
        raise ValueError(f"Grok billing returned unsupported period: {period_type}")

    start = _parse_datetime(period.get("start") or config.get("billingPeriodStart"))
    end = _parse_datetime(period.get("end") or config.get("billingPeriodEnd"))
    window_minutes = None
    if start and end and end > start:
        window_minutes = int((end - start).total_seconds() // 60)
    elif period_type == "USAGE_PERIOD_TYPE_WEEKLY":
        window_minutes = 7 * 24 * 60

    current = now or datetime.now(timezone.utc)
    return UsageWindow(
        used_percent=max(0, min(100, int(round(percent)))),
        window_minutes=window_minutes,
        reset_description=_reset_description(end, current),
        resets_at=end,
    )


def fetch_grok_usage() -> list[ProviderUsage]:
    try:
        credentials = _load_credentials()
    except _GrokAuthError as exc:
        return [ProviderUsage(provider="grok", source="oauth", error=str(exc))]

    if credentials.expires_at and credentials.expires_at <= datetime.now(timezone.utc):
        return [
            ProviderUsage(
                provider="grok",
                source="oauth",
                account_email=credentials.email,
                error="Grok session expired. Run `grok login`",
            )
        ]

    try:
        status, payload = _request_billing(credentials.access_token)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None)
        detail = str(reason or type(exc).__name__)[:160]
        return [
            ProviderUsage(
                provider="grok",
                source="oauth",
                account_email=credentials.email,
                error=f"Grok billing network error: {detail}",
            )
        ]
    except ValueError as exc:
        return [ProviderUsage(provider="grok", source="oauth", error=str(exc))]

    if status in (401, 403):
        return [
            ProviderUsage(
                provider="grok",
                source="oauth",
                account_email=credentials.email,
                error="Grok session expired. Run `grok login`",
            )
        ]
    if status != 200:
        return [
            ProviderUsage(
                provider="grok",
                source="oauth",
                account_email=credentials.email,
                error=f"Grok billing HTTP {status}",
            )
        ]

    try:
        weekly = _parse_billing(payload)
    except ValueError as exc:
        return [ProviderUsage(provider="grok", source="oauth", error=str(exc))]

    return [
        ProviderUsage(
            provider="grok",
            source="oauth",
            account_email=credentials.email,
            secondary=weekly,
            updated_at=datetime.now(timezone.utc),
        )
    ]
