"""Claude provider — native OAuth usage from Anthropic API.

Uses credentials written by Claude Code (`~/.claude/.credentials.json`) and
calls `GET /api/oauth/usage` directly. No codexbar CLI dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.models import ProviderUsage, UsageWindow
from ..core.resilience import RetryConfig, retry

logger = logging.getLogger("codexbar-linux")

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
USER_AGENT = "claude-code/2.1.0"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def _load_access_token() -> Optional[str]:
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken") or oauth.get("access_token") or ""
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def _load_identity() -> tuple[Optional[str], Optional[str]]:
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict):
        return None, None
    plan = oauth.get("subscriptionType") or oauth.get("rateLimitTier")
    plan_s = str(plan) if plan else None
    return None, plan_s


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    raw = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _window_from_oauth(data: Any, label: str) -> Optional[UsageWindow]:
    if not isinstance(data, dict):
        return None
    util = data.get("utilization")
    if util is None:
        return None
    try:
        pct = int(round(float(util)))
    except (TypeError, ValueError):
        return None
    pct = max(0, min(100, pct))
    resets_at = _parse_iso(data.get("resets_at") or data.get("resetsAt"))
    reset_desc = ""
    if resets_at:
        delta = resets_at - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            reset_desc = f"{label}: resetting…"
        else:
            mins = secs // 60
            if mins < 60:
                reset_desc = f"{label}: resets in {mins}m"
            elif mins < 1440:
                reset_desc = f"{label}: resets in {mins // 60}h"
            else:
                reset_desc = f"{label}: resets in {mins // 1440}d"
    else:
        reset_desc = label
    return UsageWindow(
        used_percent=pct,
        reset_description=reset_desc,
        resets_at=resets_at,
    )


@retry(RetryConfig(max_tries=2, backoff=1.0))
def fetch_claude_usage() -> list[ProviderUsage]:
    token = _load_access_token()
    if not token:
        return [
            ProviderUsage(
                provider="claude",
                source="oauth",
                error="Not signed in (optional). Disable in Settings or run: claude auth login",
            )
        ]

    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        if exc.code in (401, 403):
            return [
                ProviderUsage(
                    provider="claude",
                    source="oauth",
                    error="Session expired (optional). Disable in Settings or re-login",
                )
            ]
        return [
            ProviderUsage(
                provider="claude",
                source="oauth",
                error=f"Claude OAuth HTTP {exc.code}: {body}",
            )
        ]
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return [
            ProviderUsage(
                provider="claude",
                source="oauth",
                error=f"Claude OAuth network/parse error: {exc}",
            )
        ]

    if not isinstance(payload, dict):
        return [
            ProviderUsage(
                provider="claude",
                source="oauth",
                error="Claude OAuth: invalid response",
            )
        ]

    primary = _window_from_oauth(payload.get("five_hour"), "Session (5h)")
    secondary = _window_from_oauth(payload.get("seven_day"), "Weekly")
    tertiary = (
        _window_from_oauth(payload.get("seven_day_opus"), "Opus weekly")
        or _window_from_oauth(payload.get("seven_day_sonnet"), "Sonnet weekly")
        or _window_from_oauth(payload.get("seven_day_oauth_apps"), "OAuth apps")
    )

    _, plan = _load_identity()
    if not primary and not secondary and not tertiary:
        return [
            ProviderUsage(
                provider="claude",
                source="oauth",
                login_method=plan,
                error="Claude OAuth returned no usage windows",
            )
        ]

    logger.info("Claude OAuth: session=%s weekly=%s",
                primary.used_percent if primary else "-",
                secondary.used_percent if secondary else "-")
    return [
        ProviderUsage(
            provider="claude",
            source="oauth",
            login_method=plan,
            primary=primary,
            secondary=secondary,
            tertiary=tertiary,
            updated_at=datetime.now(timezone.utc),
        )
    ]
