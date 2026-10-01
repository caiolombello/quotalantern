"""Cursor provider — usage-summary via session cookie.

Auth order:
1. CURSOR_COOKIE env or ~/.local/share/codexbar-linux/cursor_cookie
2. Firefox cookies for cursor.com domains
3. Clear auth error (Chromium cookie DB is encrypted on Linux)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.models import ProviderUsage, UsageWindow
from ..core.resilience import RetryConfig, retry

logger = logging.getLogger("codexbar-linux")

USAGE_SUMMARY_URL = "https://cursor.com/api/usage-summary"
AUTH_ME_URL = "https://cursor.com/api/auth/me"
COOKIE_FILE = Path.home() / ".local/share/codexbar-linux/cursor_cookie"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) codexbar-linux/0.3"

SESSION_COOKIE_NAMES = {
    "WorkosCursorSessionToken",
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
    "wos-session",
    "__Secure-wos-session",
    "authjs.session-token",
    "__Secure-authjs.session-token",
}


def _sanitize_cookie(value: str) -> Optional[str]:
    cleaned = value.strip()
    if not cleaned or "\r" in cleaned or "\n" in cleaned:
        return None
    # Accept raw token or full Cookie header
    if "=" not in cleaned and cleaned:
        cleaned = f"WorkosCursorSessionToken={cleaned}"
    return cleaned


def _load_manual_cookie() -> Optional[str]:
    env = os.environ.get("CURSOR_COOKIE", "").strip()
    if env:
        return _sanitize_cookie(env)
    try:
        mode = COOKIE_FILE.stat().st_mode
        # Only refuse if world-readable/writable; tighten to 600 when possible.
        if mode & 0o004:
            try:
                COOKIE_FILE.chmod(0o600)
            except OSError:
                logger.warning("Cursor: cookie file is world-readable; chmod 600 failed")
                return None
        return _sanitize_cookie(COOKIE_FILE.read_text())
    except OSError:
        return None


def _firefox_cookie_header() -> Optional[str]:
    ff_root = Path.home() / ".mozilla" / "firefox"
    if not ff_root.is_dir():
        return None

    for cookies_db in ff_root.glob("*/cookies.sqlite"):
        try:
            # Copy to temp — Firefox may lock the live DB
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            tmp_path.write_bytes(cookies_db.read_bytes())
            con = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute(
                """
                SELECT name, value FROM moz_cookies
                WHERE host LIKE '%cursor.com%' OR host LIKE '%cursor.sh%'
                """
            )
            rows = cur.fetchall()
            con.close()
            tmp_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug("Cursor: firefox cookie read failed for %s: %s", cookies_db, exc)
            continue

        by_name = {name: value for name, value in rows if name and value}
        if not by_name:
            continue
        if not any(name in SESSION_COOKIE_NAMES for name in by_name):
            continue
        header = "; ".join(f"{k}={v}" for k, v in by_name.items())
        logger.debug("Cursor: loaded %d cookies from Firefox", len(by_name))
        return header
    return None


def _load_cookie_header() -> Optional[str]:
    return _load_manual_cookie() or _firefox_cookie_header()


def _http_get_json(url: str, cookie: str) -> tuple[int, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        try:
            return exc.code, json.loads(body) if body.startswith("{") else {"error": body}
        except json.JSONDecodeError:
            return exc.code, {"error": body}


def _pct_from_cents(used: Any, limit: Any) -> Optional[int]:
    try:
        used_i = int(used)
        limit_i = int(limit)
    except (TypeError, ValueError):
        return None
    if limit_i <= 0:
        return None
    return max(0, min(100, int(round(used_i / limit_i * 100))))


def _window_plan(individual: dict[str, Any], cycle_end: Optional[str]) -> Optional[UsageWindow]:
    plan = individual.get("plan") if isinstance(individual, dict) else None
    if not isinstance(plan, dict):
        return None
    pct = plan.get("totalPercentUsed")
    if pct is None:
        pct = _pct_from_cents(plan.get("used"), plan.get("limit"))
    if pct is None:
        return None
    try:
        used_pct = max(0, min(100, int(round(float(pct)))))
    except (TypeError, ValueError):
        return None

    used_cents = plan.get("used")
    limit_cents = plan.get("limit")
    reset = "plan"
    if isinstance(used_cents, (int, float)) and isinstance(limit_cents, (int, float)):
        reset = f"${used_cents / 100:.2f} / ${limit_cents / 100:.2f}"
    if cycle_end:
        reset = f"{reset} · cycle ends {cycle_end[:10]}"
    return UsageWindow(used_percent=used_pct, reset_description=reset)


def _window_on_demand(individual: dict[str, Any]) -> Optional[UsageWindow]:
    on_demand = individual.get("onDemand") if isinstance(individual, dict) else None
    if not isinstance(on_demand, dict):
        return None
    if on_demand.get("enabled") is False and on_demand.get("used") in (None, 0):
        return None
    pct = _pct_from_cents(on_demand.get("used"), on_demand.get("limit"))
    if pct is None:
        used = on_demand.get("used")
        if isinstance(used, (int, float)) and used > 0 and not on_demand.get("limit"):
            # Unlimited budget but with spend — show as info window at 0% used of cap
            return UsageWindow(
                used_percent=0,
                reset_description=f"on-demand ${used / 100:.2f} (no cap)",
            )
        return None
    used = on_demand.get("used") or 0
    limit = on_demand.get("limit") or 0
    return UsageWindow(
        used_percent=pct,
        reset_description=f"on-demand ${used / 100:.2f} / ${limit / 100:.2f}",
    )


def _window_auto_api(individual: dict[str, Any]) -> Optional[UsageWindow]:
    plan = individual.get("plan") if isinstance(individual, dict) else None
    if not isinstance(plan, dict):
        return None
    auto = plan.get("autoPercentUsed")
    api = plan.get("apiPercentUsed")
    parts = []
    worst = 0
    if isinstance(auto, (int, float)):
        parts.append(f"auto {int(auto)}%")
        worst = max(worst, int(auto))
    if isinstance(api, (int, float)):
        parts.append(f"api {int(api)}%")
        worst = max(worst, int(api))
    if not parts:
        return None
    return UsageWindow(used_percent=max(0, min(100, worst)), reset_description=" · ".join(parts))


@retry(RetryConfig(max_tries=2, backoff=1.0))
def fetch_cursor_usage() -> list[ProviderUsage]:
    cookie = _load_cookie_header()
    if not cookie:
        return [
            ProviderUsage(
                provider="cursor",
                source="web",
                error=(
                    "Cursor session missing. Save Cookie header to "
                    f"{COOKIE_FILE} (chmod 600) or set CURSOR_COOKIE. "
                    "Copy WorkosCursorSessionToken from cursor.com in the browser."
                ),
            )
        ]

    status, payload = _http_get_json(USAGE_SUMMARY_URL, cookie)
    if status in (401, 403):
        return [
            ProviderUsage(
                provider="cursor",
                source="web",
                error="Cursor session expired. Update cookie file / log in at cursor.com",
            )
        ]
    if status != 200 or not isinstance(payload, dict):
        return [
            ProviderUsage(
                provider="cursor",
                source="web",
                error=f"Cursor usage HTTP {status}",
            )
        ]

    email = None
    me_status, me = _http_get_json(AUTH_ME_URL, cookie)
    if me_status == 200 and isinstance(me, dict):
        email = me.get("email") or me.get("name")
        if email is not None:
            email = str(email)

    individual = payload.get("individualUsage") or {}
    if not isinstance(individual, dict):
        individual = {}

    cycle_end = payload.get("billingCycleEnd")
    if cycle_end is not None:
        cycle_end = str(cycle_end)

    primary = _window_plan(individual, cycle_end)
    secondary = _window_on_demand(individual)
    tertiary = _window_auto_api(individual)
    membership = payload.get("membershipType")
    plan = str(membership) if membership else None

    if not primary and not secondary and not tertiary:
        # Unlimited plans may report empty windows
        if payload.get("isUnlimited"):
            primary = UsageWindow(used_percent=0, reset_description="unlimited")
        else:
            return [
                ProviderUsage(
                    provider="cursor",
                    source="web",
                    account_email=email,
                    login_method=plan,
                    error="Cursor usage-summary returned no plan windows",
                )
            ]

    logger.info(
        "Cursor: plan=%s on_demand=%s",
        primary.used_percent if primary else "-",
        secondary.used_percent if secondary else "-",
    )
    return [
        ProviderUsage(
            provider="cursor",
            source="web",
            account_email=email,
            login_method=plan,
            primary=primary,
            secondary=secondary,
            tertiary=tertiary,
            updated_at=datetime.now(timezone.utc),
        )
    ]
