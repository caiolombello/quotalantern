"""OpenAI API Platform provider — fetches organization usage & costs.

Uses the official OpenAI Admin API (/v1/organization/costs) which requires
an Admin API key (not a regular API key). Generate one at:
https://platform.openai.com/settings/organization/admin-keys
"""

import json
import logging
import os
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.models import ProviderUsage
from ._costs import sum_cost_pages, openai_amount, anthropic_amount
from ..core.resilience import RetryConfig, retry

logger = logging.getLogger("codexbar-linux")

OPENAI_API_BASE = "https://api.openai.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) codexbar-linux/0.2"


def _load_admin_key() -> Optional[str]:
    """Load OpenAI Admin API key from env or private file."""
    key = os.environ.get("OPENAI_ADMIN_API_KEY", "").strip()
    if key:
        return key

    key_path = Path.home() / ".local/share/codexbar-linux/openai_admin_key"
    try:
        mode = key_path.stat().st_mode
        if mode & 0o077:
            logger.warning("OpenAI admin key file %s has unsafe permissions", key_path)
            return None
        key = key_path.read_text().strip()
    except OSError:
        return None
    return key if key else None


@retry(RetryConfig(max_tries=3, backoff=1.0))
def fetch_openai_api_usage() -> list[ProviderUsage]:
    """Return a complete month-to-date cost, or an explicit error without a partial total."""
    key = _load_admin_key()
    if not key:
        return [ProviderUsage(provider="openai-api", source="admin-api", error="Admin API key missing")]

    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    url = "https://api.openai.com/v1/organization/costs?" + urllib.parse.urlencode({"start_time": int(start.timestamp()), "end_time": int(now.timestamp()), "bucket_width": "1d", "limit": 31})
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": USER_AGENT}
    try:
        total = sum_cost_pages(url, headers, openai_amount)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return [ProviderUsage(provider="openai-api", source="admin-api", error=f"Cost report incomplete: HTTP {status}")]
    except (urllib.error.URLError, TimeoutError, OSError):
        return [ProviderUsage(provider="openai-api", source="admin-api", error="Cost report incomplete: network/timeout error")]
    except ValueError as exc:
        return [ProviderUsage(provider="openai-api", source="admin-api", error=f"Cost report unavailable: {exc}")]

    # These are costs, not a quota percentage. Keep the existing monetary field
    # for compatibility; do not manufacture a 0% allowance window.
    return [ProviderUsage(
        provider="openai-api", source="admin-api", balance_usd=float(total),
        updated_at=datetime.now(timezone.utc),
    )]
