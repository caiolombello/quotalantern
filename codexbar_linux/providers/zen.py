"""Zen provider — scrapes authenticated billing dashboard for balance & usage.

The public Zen API does not expose billing or balance.  We scrape the workspace
billing page (same auth cookie as the OpenCode dashboard) and extract the
SolidJS-hydrated state that contains balance, monthlyUsage and monthlyLimit.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.models import ProviderUsage, UsageWindow
from ..core.resilience import RetryConfig, retry

logger = logging.getLogger("codexbar-linux")

ZEN_BILLING_URL = "https://opencode.ai/workspace/{workspace_id}/billing"
DEFAULT_WORKSPACE_ID = "wrk_01KEAF6115M0KXGH91W0881STM"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) codexbar-linux/0.2"
ZEN_DIVISOR = 100_000_000


def _load_dashboard_cookie() -> Optional[str]:
    """Load opencode.ai dashboard auth cookie from env or a local private file."""
    raw = os.environ.get("OPENCODE_AUTH_COOKIE", "").strip()
    if raw:
        return _sanitize_cookie(raw)

    cookie_path = Path.home() / ".local/share/codexbar-linux/opencode_auth_cookie"
    try:
        mode = cookie_path.stat().st_mode
        if mode & 0o077:
            return None
        raw = cookie_path.read_text().strip()
    except OSError:
        return None
    return _sanitize_cookie(raw) if raw else None


def _sanitize_cookie(value: str) -> Optional[str]:
    """Reject cookies that contain newlines (header injection)."""
    if "\r" in value or "\n" in value:
        return None
    return value


def _workspace_id() -> str:
    raw = os.environ.get("OPENCODE_GO_WORKSPACE_ID", "").strip()
    if raw:
        return raw
    return DEFAULT_WORKSPACE_ID


def _extract_billing_dict(html: str) -> Optional[dict]:
    """Extract the billing object from SolidJS hydration scripts.

    The data lives inside a ``<script>`` tag as part of ``window._$HY`` or
    ``$R[...]`` assignments.  We try several strategies, from most specific
    to broad regex fallbacks.
    """
    # Strategy 1 – find a script that contains both balance and monthlyUsage
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)
    for script in scripts:
        if "balance" not in script or "monthlyUsage" not in script:
            continue

        # Try to grab a JSON-like object that has balance + monthlyUsage
        # SolidJS serialises objects without quotes on keys, so we look for
        #   { ... balance:12345 ... monthlyUsage:67890 ... }
        for pattern in (
            # $R[23]={balance:1385...,monthlyUsage:2066...,...}
            r"\$R\[\d+\]\s*=\s*(\{[^{}]*balance\s*:\s*\d+[^{}]*\})",
            # Generic object with balance and monthlyUsage
            r"(\{[^{}]*balance\s*:\s*\d+[^{}]*monthlyUsage\s*:\s*\d+[^{}]*\})",
            r"(\{[^{}]*monthlyUsage\s*:\s*\d+[^{}]*balance\s*:\s*\d+[^{}]*\})",
        ):
            m = re.search(pattern, script, re.DOTALL)
            if m:
                obj_str = m.group(1)
                return _parse_js_object(obj_str)

        # Strategy 2 – the whole script might be an assignment to window._$HY
        # e.g. window._$HY={r:[{balance:...,monthlyUsage:...}]}
        m = re.search(
            r"window\._\$HY\s*=\s*(\{.*?balance\s*:\s*\d+.*?\});?\s*$",
            script,
            re.DOTALL,
        )
        if m:
            parsed = _parse_js_object(m.group(1))
            if parsed:
                return parsed

    # Strategy 3 – fallback: search the raw HTML for key:value pairs
    # This is less robust but catches edge cases where script tags are malformed
    return _parse_js_object(html)


def _parse_js_object(text: str) -> Optional[dict]:
    """Heuristic parser for a JavaScript/JSON-like object string.

    Extracts known billing keys by regex so we do not need a full JS parser.
    """
    result: dict = {}
    keys = (
        "balance",
        "monthlyUsage",
        "monthlyLimit",
        "reloadAmount",
        "reloadTrigger",
        "reload",
        "timeMonthlyUsageUpdated",
    )
    found = False
    for key in keys:
        # Try numeric values first (most common)
        m = re.search(rf'["\']?{re.escape(key)}["\']?\s*:\s*(\d+(?:\.\d+)?)', text)
        if m:
            val = m.group(1)
            result[key] = float(val) if "." in val else int(val)
            found = True
            continue
        # Boolean values
        m = re.search(rf'["\']?{re.escape(key)}["\']?\s*:\s*(true|false)', text, re.IGNORECASE)
        if m:
            result[key] = m.group(1).lower() == "true"
            found = True
    return result if found else None


@retry(RetryConfig(max_tries=3, backoff=1.0))
def fetch_zen_usage() -> list[ProviderUsage]:
    """Fetch Zen balance & usage from the OpenCode billing dashboard."""
    cookie = _load_dashboard_cookie()
    if not cookie:
        logger.warning("Zen: no auth cookie found (OPENCODE_AUTH_COOKIE)")
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error="Zen auth cookie not found. Set OPENCODE_AUTH_COOKIE.",
            )
        ]

    workspace_id = _workspace_id()
    url = ZEN_BILLING_URL.format(workspace_id=workspace_id)
    cookie_header = cookie if "=" in cookie else f"auth={cookie}"
    logger.debug("Zen: fetching %s", url)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": cookie_header,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        logger.warning("Zen: HTTP %s – %s", exc.code, body)
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error=f"Zen billing HTTP {exc.code}: {body}",
            )
        ]
    except urllib.error.URLError as exc:
        logger.warning("Zen: network error – %s", exc.reason)
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error=f"Zen billing network error: {exc.reason}",
            )
        ]
    except (TimeoutError, OSError) as exc:
        logger.warning("Zen: timeout/OS error – %s", exc)
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error=f"Zen billing timeout/OS error: {exc}",
            )
        ]
    except Exception as exc:
        logger.exception("Zen: unexpected error during fetch")
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error=f"Zen billing unexpected error: {exc}",
            )
        ]

    lower_html = html.lower()
    if "sign in" in lower_html or "login" in lower_html or "entrar" in lower_html:
        logger.warning("Zen: login page detected (cookie expired/invalid)")
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error="Zen auth cookie expired or invalid",
            )
        ]

    billing = _extract_billing_dict(html)
    if not billing:
        # Save a snippet of the HTML for debugging
        snippet = html[:500].replace("\n", " ")
        logger.warning("Zen: could not parse billing data. HTML snippet: %s...", snippet)
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error="Could not parse Zen billing data from dashboard HTML",
            )
        ]

    logger.debug("Zen: parsed billing %s", billing)

    balance_raw = billing.get("balance")
    monthly_usage_raw = billing.get("monthlyUsage")
    monthly_limit = billing.get("monthlyLimit")

    if balance_raw is None or monthly_usage_raw is None or monthly_limit is None:
        logger.warning("Zen: incomplete billing data – %s", billing)
        return [
            ProviderUsage(
                provider="zen",
                source="scraper",
                error="Incomplete Zen billing data (missing balance/usage/limit)",
            )
        ]

    balance_usd = balance_raw / ZEN_DIVISOR
    monthly_usage_usd = monthly_usage_raw / ZEN_DIVISOR
    monthly_limit_f = float(monthly_limit)

    used_percent = 0
    if monthly_limit_f > 0:
        used_percent = min(100, int((monthly_usage_usd / monthly_limit_f) * 100))

    logger.info(
        "Zen: balance=$%.2f usage=$%.2f/%s limit=%.0f%%",
        balance_usd,
        monthly_usage_usd,
        monthly_limit_f,
        used_percent,
    )

    return [
        ProviderUsage(
            provider="zen",
            source="scraper",
            primary=UsageWindow(
                used_percent=used_percent,
                reset_description=f"${monthly_usage_usd:.2f} / ${monthly_limit_f:.0f}",
            ),
            credits_remaining=int(balance_usd),
            balance_usd=balance_usd,
            updated_at=datetime.now(timezone.utc),
        )
    ]
