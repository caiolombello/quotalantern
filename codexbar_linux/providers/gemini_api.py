"""Gemini API provider — fetches project usage limit from Google AI Studio.

Uses the internal gRPC-Web endpoint via Google AI Studio cookies.
Requires Google session cookies (SAPISID, SID, etc.) which expire quickly.
"""

import hashlib
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

GEMINI_RPC_URL = (
    "https://alkalimakersuite-pa.clients6.google.com/"
    "$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetProjectUsageLimit"
)
GEMINI_ORIGIN = "https://aistudio.google.com"
GEMINI_REFERER = "https://aistudio.google.com/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"


def _load_google_cookies() -> Optional[str]:
    """Load raw Google cookies from env or a private file."""
    raw = os.environ.get("GOOGLE_COOKIES", "").strip()
    if raw:
        return raw

    cookie_path = Path.home() / ".local/share/codexbar-linux/google_cookies"
    try:
        mode = cookie_path.stat().st_mode
        if mode & 0o077:
            logger.warning("Gemini: cookie file %s has unsafe permissions", cookie_path)
            return None
        raw = cookie_path.read_text().strip()
    except OSError:
        return None
    return raw if raw else None


def _extract_cookie_value(cookie_str: str, name: str) -> Optional[str]:
    """Extract a specific cookie value from a Cookie header string."""
    match = re.search(rf"{re.escape(name)}=([^;\s]+)", cookie_str)
    if match:
        return match.group(1)
    # Also try without escaping for simple names
    match = re.search(rf"{name}=([^;\s]+)", cookie_str)
    if match:
        return match.group(1)
    return None


def _compute_sapisid_hash(sapisid: str, origin: str = GEMINI_ORIGIN) -> str:
    """Compute SAPISIDHASH as SHA1(timestamp + ' ' + sapisid + ' ' + origin)."""
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    msg = f"{ts} {sapisid} {origin}"
    digest = hashlib.sha1(msg.encode("utf-8")).hexdigest()  # noqa: S324
    return f"{ts}_{digest}"


def _build_auth_header(cookie_str: str) -> Optional[str]:
    """Build the Authorization header with SAPISIDHASH."""
    sapisid = _extract_cookie_value(cookie_str, "SAPISID")
    if not sapisid:
        # Try alternate cookie names
        for name in ("__Secure-1PAPISID", "__Secure-3PAPISID", "APISID"):
            sapisid = _extract_cookie_value(cookie_str, name)
            if sapisid:
                break
    if not sapisid:
        return None

    hash_val = _compute_sapisid_hash(sapisid)
    return (
        f"SAPISIDHASH {hash_val} "
        f"SAPISID1PHASH {hash_val} "
        f"SAPISID3PHASH {hash_val}"
    )


def _sanitize_cookie(value: str) -> Optional[str]:
    """Reject cookies with newlines."""
    if "\r" in value or "\n" in value:
        return None
    return value


@retry(RetryConfig(max_tries=2, backoff=1.0))
def fetch_gemini_api_usage() -> list[ProviderUsage]:
    """Fetch Gemini project usage limit via Google AI Studio internal API."""
    # No embedded service key or project: these must be explicitly configured.
    api_key = os.environ.get("GEMINI_AISTUDIO_API_KEY", "").strip()
    project = os.environ.get("GEMINI_AISTUDIO_PROJECT", "").strip()
    if not api_key or "\r" in api_key or "\n" in api_key or not re.fullmatch(r"projects/[A-Za-z0-9_-]+", project):
        return [ProviderUsage(provider="gemini-api", source="grpc-web", error="Gemini API requires GEMINI_AISTUDIO_API_KEY and GEMINI_AISTUDIO_PROJECT (projects/<id>). No embedded defaults are shipped.")]

    raw_cookies = _load_google_cookies()
    if not raw_cookies:
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error="Google cookies not found. Set GOOGLE_COOKIES env var.",
            )
        ]

    cookies = _sanitize_cookie(raw_cookies)
    if not cookies:
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error="Invalid Google cookies (contains newlines)",
            )
        ]

    auth_header = _build_auth_header(cookies)
    if not auth_header:
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error="Could not extract SAPISID from Google cookies",
            )
        ]

    # Visit ID is optional but recommended; we try to extract or generate a placeholder
    visit_id = os.environ.get("AISTUDIO_VISIT_ID", "v1_codexbar")

    body = json.dumps([project]).encode("utf-8")

    req = urllib.request.Request(
        GEMINI_RPC_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": GEMINI_REFERER,
            "X-AIStudio-Visit-Id": visit_id,
            "X-Goog-Api-Key": api_key,
            "X-Goog-AuthUser": "0",
            "Authorization": auth_header,
            "Content-Type": "application/json+protobuf",
            "X-User-Agent": "grpc-web-javascript/0.1",
            "Origin": GEMINI_ORIGIN,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "Cookie": cookies,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
            # Handle gzip if present
            if response.getheader("Content-Encoding") == "gzip":
                import gzip

                raw = gzip.decompress(raw)
            text = raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        logger.warning("Gemini API HTTP %s: %s", exc.code, body)
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error=f"Gemini API HTTP {exc.code}: {body}",
            )
        ]
    except urllib.error.URLError as exc:
        logger.warning("Gemini API network error: %s", exc.reason)
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error=f"Gemini API network error: {exc.reason}",
            )
        ]
    except (TimeoutError, OSError) as exc:
        logger.warning("Gemini API timeout/OS error: %s", exc)
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error=f"Gemini API timeout: {exc}",
            )
        ]
    except Exception as exc:
        logger.exception("Gemini API unexpected error")
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error=f"Gemini API unexpected error: {exc}",
            )
        ]

    # Parse gRPC-Web JSON array response.
    # Real response format: ["project_id", ["BRL","100"], ["BRL","2",977774000], ["USD","2000"]]
    # - Index 0: project_id (ignore)
    # - Index 1: [currency, limit_amount] → project limit
    # - Index 2: [currency, _, usage_in_nanos] → current usage
    # - Index 3+: other limits (e.g. USD global) → ignore
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Gemini API: non-JSON response: %s...", text[:300])
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error="Gemini API returned non-JSON response",
            )
        ]

    if not isinstance(data, list) or len(data) < 3:
        return [
            ProviderUsage(
                provider="gemini-api",
                source="grpc-web",
                error="Gemini API returned unexpected response structure",
            )
        ]

    # Extract limit (second element)
    limit_item = data[1]
    limit_currency = limit_item[0] if len(limit_item) > 0 else ""
    limit_amount = float(limit_item[1]) if len(limit_item) > 1 and limit_item[1] else 0.0

    # Extract usage (third element)
    usage_item = data[2]
    usage_currency = usage_item[0] if len(usage_item) > 0 else ""
    # Response format: [currency, whole_or_indicator, fraction_in_nanos]
    # The actual value is fraction_in_nanos / 1_000_000_000
    usage_amount = 0.0
    if len(usage_item) >= 3 and isinstance(usage_item[2], (int, float)):
        usage_amount = float(usage_item[2]) / 1_000_000_000.0
    elif len(usage_item) >= 2:
        try:
            usage_amount = float(usage_item[1])
        except (ValueError, TypeError):
            usage_amount = 0.0

    used_percent = 0
    if limit_amount > 0:
        used_percent = min(100, int((usage_amount / limit_amount) * 100))

    logger.info(
        "Gemini API: %s %.2f / %.2f (%d%%)",
        usage_currency,
        usage_amount,
        limit_amount,
        used_percent,
    )

    return [
        ProviderUsage(
            provider="gemini-api",
            source="grpc-web",
            primary=UsageWindow(
                used_percent=used_percent,
                reset_description=f"${usage_amount:.2f} / ${limit_amount:.0f} {usage_currency}",
            ),
            balance_usd=usage_amount,
            updated_at=datetime.now(timezone.utc),
        )
    ]
