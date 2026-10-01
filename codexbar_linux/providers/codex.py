"""Codex provider — fetches usage directly from OpenAI OAuth API.

Bypasses the official codexbar CLI because codexbar v0.24 on Linux only
supports the CLI PTY source, which broke with Codex CLI 0.128.0+.
"""

import json
import math
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..core.models import ProviderUsage, UsageWindow
from ..core.resilience import RetryConfig, retry


def _load_auth_token() -> Optional[str]:
    """Read the access token from ~/.codex/auth.json."""
    auth_path = os.path.expanduser("~/.codex/auth.json")
    try:
        if os.stat(auth_path).st_mode & 0o077:
            return None
        with open(auth_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        token = tokens.get("access_token", "")
        if token:
            return token

    api_key = data.get("OPENAI_API_KEY", "")
    if api_key:
        return api_key

    return None


def _parse_window(win_data: dict) -> Optional[UsageWindow]:
    """Parse a window dict from the wham/usage API."""
    if win_data is None:
        return None
    if not isinstance(win_data, dict):
        raise ValueError("Invalid Codex window schema")
    pct = win_data.get("used_percent")
    if isinstance(pct, bool) or pct is None:
        raise ValueError("Missing or invalid Codex usage percent")
    try:
        percent = float(pct)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid Codex usage percent") from exc
    if not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError("Invalid Codex usage percent")
    pct = int(percent)

    window_secs = win_data.get("limit_window_seconds")
    window_minutes = None
    if isinstance(window_secs, (int, float)) and not isinstance(window_secs, bool) and math.isfinite(window_secs) and window_secs > 0:
        window_minutes = int(window_secs // 60)

    reset_desc = ""
    resets_at: Optional[datetime] = None
    now = datetime.now(timezone.utc)
    reset_after = win_data.get("reset_after_seconds")
    if isinstance(reset_after, (int, float)) and not isinstance(reset_after, bool) and math.isfinite(reset_after):
        resets_at = now + timedelta(seconds=reset_after)
        mins = int(reset_after // 60)
        if mins < 60:
            reset_desc = f"resets in {mins}m"
        elif mins < 1440:
            reset_desc = f"resets in {mins // 60}h"
        else:
            reset_desc = f"resets in {mins // 1440}d"
    else:
        reset_ts = win_data.get("reset_at")
        if isinstance(reset_ts, (int, float)) and not isinstance(reset_ts, bool) and math.isfinite(reset_ts):
            resets_at = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
            reset_desc = "resets soon"

    return UsageWindow(
        used_percent=pct,
        window_minutes=window_minutes,
        reset_description=reset_desc,
        resets_at=resets_at,
    )


@retry(RetryConfig(max_tries=3, backoff=1.0))
def fetch_codex_usage() -> list[ProviderUsage]:
    """Fetch Codex usage from OpenAI wham/usage API."""
    token = _load_auth_token()
    if not token:
        return [
            ProviderUsage(
                provider="codex",
                source="oauth",
                error="No access token found in ~/.codex/auth.json",
            )
        ]

    url = "https://chatgpt.com/backend-api/wham/usage"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "codexbar-linux/0.4",
    }
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return [
            ProviderUsage(
                provider="codex",
                source="oauth",
                error=f"OpenAI usage endpoint HTTP {status}",
            )
        ]
    except urllib.error.URLError as exc:
        return [
            ProviderUsage(
                provider="codex",
                source="oauth",
                error=f"wham/usage network error: {exc.reason}",
            )
        ]
    except json.JSONDecodeError as exc:
        return [
            ProviderUsage(
                provider="codex",
                source="oauth",
                error=f"wham/usage JSON decode: {exc}",
            )
        ]

    if not isinstance(data, dict):
        return [ProviderUsage(provider="codex", source="oauth", error="Invalid Codex usage schema")]

    now = datetime.now(timezone.utc)
    email = data.get("email") or data.get("account_id")
    plan = data.get("plan_type", "")

    credits_info = data.get("credits") or {}
    credits_remaining: Optional[int] = None
    if isinstance(credits_info, dict):
        balance = credits_info.get("balance")
        if balance is not None:
            try:
                credits_remaining = int(float(balance))
            except (ValueError, TypeError):
                credits_remaining = None

    rate_limit = data.get("rate_limit")
    primary = secondary = None
    error = None
    if not isinstance(rate_limit, dict):
        error = "Codex usage unavailable: missing rate-limit windows"
    else:
        try:
            primary = _parse_window(rate_limit.get("primary_window"))
            secondary = _parse_window(rate_limit.get("secondary_window"))
        except (ValueError, OverflowError, OSError):
            # Do not present a partial set of constraints as fully available.
            primary = secondary = None
            error = "Codex usage unavailable: invalid rate-limit window"
        if not primary and not secondary and error is None:
            error = "Codex usage unavailable: no rate-limit windows"

    return [
        ProviderUsage(
            provider="codex",
            source="oauth",
            account_email=email,
            login_method=plan,
            primary=primary,
            secondary=secondary,
            credits_remaining=credits_remaining,
            updated_at=now,
            error=error,
        )
    ]
