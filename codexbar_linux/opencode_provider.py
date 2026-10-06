"""OpenCode Go usage from the Console API using QuotaLantern's OAuth session.

The Console currently uses /api/go/status for these meters. This endpoint is
not a documented public API, so validate its response before displaying usage.
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .opencode_auth import OAuthError, OAuthSession, get_session


OPENCODE_GO_STATUS_URL = "https://opencode.ai/console/api/go/status"
USER_AGENT = "QuotaLantern/0.2"
MICROCENTS_PER_DOLLAR = 100_000_000


@dataclass
class GoLimitWindow:
    name: str
    amount_usd: float
    description: str
    used_percent: Optional[int] = None
    reset_description: str = ""
    resets_at: Optional[datetime] = None
    used_usd: Optional[float] = None


@dataclass
class OpenCodeStats:
    plan: str = "OpenCode Go"
    model_count: int = 0
    limits: tuple[GoLimitWindow, ...] = ()
    usage_available: bool = False
    workspace_id: str = ""
    cookie_expired: bool = False  # Kept for existing snapshot readers.
    error: Optional[str] = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("OpenCode Console redirect rejected")


def _request_go_status(session: OAuthSession) -> Optional[dict]:
    request = urllib.request.Request(
        OPENCODE_GO_STATUS_URL,
        headers={
            "Authorization": f"Bearer {session.access_token}",
            "x-org-id": session.org_id,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("OpenCode Console response too large")
        payload = json.loads(raw)
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("Invalid OpenCode Console response")
    return payload


def _microcents(value) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        return int(value)
    if type(value) is int and 0 <= value <= 10**20:
        return value
    raise ValueError("Invalid OpenCode Go meter")


def _parse_status(payload: dict) -> tuple[str, tuple[GoLimitWindow, ...]]:
    product = payload.get("product")
    if product not in ("go", "go-plus"):
        raise ValueError("Unknown OpenCode Go plan")
    access = payload.get("access")
    if not isinstance(access, dict) or not isinstance(access.get("meters"), dict):
        raise ValueError("Invalid OpenCode Go meters")
    windows = []
    for key, name in (("fiveHour", "5 hour"), ("week", "Weekly"), ("month", "Monthly")):
        meter = access["meters"].get(key)
        if not isinstance(meter, dict):
            raise ValueError("Invalid OpenCode Go meter")
        used = _microcents(meter.get("usedMicroCents"))
        limit = _microcents(meter.get("limitMicroCents"))
        if not limit:
            raise ValueError("Invalid OpenCode Go limit")
        reset = meter.get("resetsAt")
        resets_at = None
        if reset is not None:
            if not isinstance(reset, str) or len(reset) > 64:
                raise ValueError("Invalid OpenCode Go reset time")
            resets_at = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            if resets_at.tzinfo is None:
                raise ValueError("Invalid OpenCode Go reset time")
            resets_at = resets_at.astimezone(timezone.utc)
        elif key != "fiveHour":
            raise ValueError("Invalid OpenCode Go reset time")
        windows.append(
            GoLimitWindow(
                name=name,
                amount_usd=limit / MICROCENTS_PER_DOLLAR,
                description=name,
                used_percent=max(0, min(100, int(round(100 * used / limit)))),
                resets_at=resets_at,
                used_usd=used / MICROCENTS_PER_DOLLAR,
            )
        )
    plan = "OpenCode Go Plus" if product == "go-plus" else "OpenCode Go"
    return plan, tuple(windows)


def fetch_opencode_stats(days: int = 7) -> OpenCodeStats:
    """Fetch all three Go meters; no CLI keys or dashboard cookies are read."""
    del days
    stats = OpenCodeStats()
    try:
        session = get_session()
        stats.workspace_id = session.org_id
        payload = _request_go_status(session)
        if payload is None or (
            payload.get("product") in ("go", "go-plus")
            and "access" in payload and payload["access"] is None
        ):
            stats.error = "OpenCode Go has no active subscription in the selected workspace"
            return stats
        stats.plan, stats.limits = _parse_status(payload)
        stats.usage_available = True
    except OAuthError as exc:
        stats.error = str(exc)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            stats.error = "OpenCode Go session expired or revoked. Reconnect in Settings → Credentials"
        elif exc.code == 403:
            stats.error = "OpenCode Go access denied. Check the workspace selected during sign-in"
        else:
            stats.error = f"OpenCode Console returned HTTP {exc.code}"
        exc.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        stats.error = "OpenCode Console network error. Try refreshing later"
    except (ValueError, TypeError, OverflowError):
        stats.error = "OpenCode Console usage response is unavailable or incompatible"
    return stats
