"""Claude API provider — fetches organization usage & cost via Admin API.

Requires an Anthropic Admin API key (sk-ant-admin...) to access
/v1/organizations/cost_report and optionally /v1/organizations/usage_report/messages.
"""

import json
import logging
import os
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..core.models import ProviderUsage
from ._costs import sum_cost_pages, openai_amount, anthropic_amount
from ..core.resilience import RetryConfig, retry

logger = logging.getLogger("codexbar-linux")

ANTHROPIC_API_BASE = "https://api.anthropic.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) codexbar-linux/0.2"


def _load_admin_key() -> Optional[str]:
    """Load Anthropic Admin API key from env or private file."""
    key = os.environ.get("ANTHROPIC_ADMIN_API_KEY", "").strip()
    if key:
        return key

    key_path = Path.home() / ".local/share/codexbar-linux/anthropic_admin_key"
    try:
        mode = key_path.stat().st_mode
        if mode & 0o077:
            logger.warning("Anthropic admin key file %s has unsafe permissions", key_path)
            return None
        key = key_path.read_text().strip()
    except OSError:
        return None
    return key if key else None


@retry(RetryConfig(max_tries=3, backoff=1.0))
def fetch_claude_api_usage() -> list[ProviderUsage]:
    """Return a complete month-to-date cost, or an explicit error without a partial total."""
    key = _load_admin_key()
    if not key:
        return [ProviderUsage(provider="claude-api", source="api", error="Admin API key missing")]

    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    url = "https://api.anthropic.com/v1/organizations/cost_report?" + urllib.parse.urlencode({"starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "ending_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "bucket_width": "1d", "limit": 31})
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "Accept": "application/json", "User-Agent": USER_AGENT}
    try:
        total = sum_cost_pages(url, headers, anthropic_amount)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return [ProviderUsage(provider="claude-api", source="api", error=f"Cost report incomplete: HTTP {status}")]
    except (urllib.error.URLError, TimeoutError, OSError):
        return [ProviderUsage(provider="claude-api", source="api", error="Cost report incomplete: network/timeout error")]
    except ValueError as exc:
        return [ProviderUsage(provider="claude-api", source="api", error=f"Cost report unavailable: {exc}")]

    # These are costs, not a quota percentage. Keep the existing monetary field
    # for compatibility; do not manufacture a 0% allowance window.
    return [ProviderUsage(
        provider="claude-api", source="api", balance_usd=float(total),
        updated_at=datetime.now(timezone.utc),
    )]
