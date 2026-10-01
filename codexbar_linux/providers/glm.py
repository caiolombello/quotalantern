"""GLM (z.ai / BigModel) provider — quota limit API.

GET {base}/api/monitor/usage/quota/limit with Bearer API key.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.models import ProviderUsage, UsageWindow
from ..core.resilience import RetryConfig, retry

logger = logging.getLogger("codexbar-linux")

KEY_FILE = Path.home() / ".local/share/codexbar-linux/zai_api_key"
QUOTA_PATH = "/api/monitor/usage/quota/limit"

REGION_BASES = {
    "global": "https://api.z.ai",
    "bigmodel-cn": "https://open.bigmodel.cn",
}


def _load_api_key() -> Optional[str]:
    for env_name in ("Z_AI_API_KEY", "ZAI_API_KEY", "GLM_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    try:
        mode = KEY_FILE.stat().st_mode
        if mode & 0o077:
            logger.warning("GLM: key file has unsafe permissions")
            return None
        key = KEY_FILE.read_text().strip()
        return key or None
    except OSError:
        return None


def _region() -> str:
    # Lazy import to avoid circular import at module load of config
    try:
        from ..config import load_config

        region = load_config().glm_region
        if region in REGION_BASES:
            return region
    except Exception:
        pass
    env = os.environ.get("Z_AI_REGION", "").strip().lower()
    if env in REGION_BASES:
        return env
    return "global"


def _used_percent(limit_entry: dict[str, Any]) -> int:
    percentage = limit_entry.get("percentage")
    usage_limit = limit_entry.get("usage")  # z.ai: `usage` field is often the limit
    remaining = limit_entry.get("remaining")
    current = limit_entry.get("currentValue")

    computed: Optional[float] = None
    if isinstance(usage_limit, (int, float)) and usage_limit > 0:
        used_raw: Optional[float] = None
        if isinstance(remaining, (int, float)):
            used_from_remaining = float(usage_limit) - float(remaining)
            if isinstance(current, (int, float)):
                used_raw = max(used_from_remaining, float(current))
            else:
                used_raw = used_from_remaining
        elif isinstance(current, (int, float)):
            used_raw = float(current)
        if used_raw is not None:
            used = max(0.0, min(float(usage_limit), used_raw))
            computed = (used / float(usage_limit)) * 100.0

    if computed is not None:
        return max(0, min(100, int(round(computed))))
    if isinstance(percentage, (int, float)):
        return max(0, min(100, int(round(float(percentage)))))
    return 0


def _window_label(limit_entry: dict[str, Any]) -> str:
    unit = limit_entry.get("unit")
    number = limit_entry.get("number")
    limit_type = str(limit_entry.get("type") or "")
    unit_map = {1: "day", 3: "hour", 5: "minute", 6: "week"}
    if isinstance(number, int) and number > 0 and unit in unit_map:
        name = unit_map[unit]
        suffix = name if number == 1 else f"{name}s"
        return f"{number} {suffix}"
    if limit_type == "TIME_LIMIT":
        return "Monthly"
    if limit_type == "TOKENS_LIMIT":
        return "Tokens"
    return limit_type or "Limit"


def _resets_at(limit_entry: dict[str, Any]) -> Optional[datetime]:
    raw = limit_entry.get("nextResetTime")
    if isinstance(raw, (int, float)):
        # milliseconds
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
    return None


def _to_window(limit_entry: dict[str, Any]) -> UsageWindow:
    pct = _used_percent(limit_entry)
    label = _window_label(limit_entry)
    resets = _resets_at(limit_entry)
    reset_desc = label
    if resets:
        delta = resets - datetime.now(timezone.utc)
        mins = int(delta.total_seconds() // 60)
        if mins > 0:
            if mins < 60:
                reset_desc = f"{label}: resets in {mins}m"
            elif mins < 1440:
                reset_desc = f"{label}: resets in {mins // 60}h"
            else:
                reset_desc = f"{label}: resets in {mins // 1440}d"
    return UsageWindow(used_percent=pct, reset_description=reset_desc, resets_at=resets)


@retry(RetryConfig(max_tries=2, backoff=1.0))
def fetch_glm_usage() -> list[ProviderUsage]:
    api_key = _load_api_key()
    if not api_key:
        return [
            ProviderUsage(
                provider="glm",
                source="api",
                error=(
                    "GLM API key missing. Set Z_AI_API_KEY or write key to "
                    f"{KEY_FILE} (chmod 600)"
                ),
            )
        ]

    base = REGION_BASES[_region()]
    url = f"{base}{QUOTA_PATH}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "codexbar-linux/0.3",
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
                    provider="glm",
                    source="api",
                    error="GLM API key unauthorized. Check Z_AI_API_KEY / region",
                )
            ]
        return [
            ProviderUsage(
                provider="glm",
                source="api",
                error=f"GLM quota HTTP {exc.code}: {body}",
            )
        ]
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return [
            ProviderUsage(
                provider="glm",
                source="api",
                error=f"GLM quota error: {exc}",
            )
        ]

    if not isinstance(payload, dict):
        return [ProviderUsage(provider="glm", source="api", error="GLM: invalid response")]

    success = payload.get("success")
    code = payload.get("code")
    if success is False or (code is not None and code != 200):
        msg = payload.get("msg") or "API error"
        return [ProviderUsage(provider="glm", source="api", error=f"GLM API: {msg}")]

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return [ProviderUsage(provider="glm", source="api", error="GLM: missing data")]

    limits = data.get("limits") or []
    if not isinstance(limits, list) or not limits:
        return [ProviderUsage(provider="glm", source="api", error="GLM: no limits in response")]

    token_limits = [x for x in limits if isinstance(x, dict) and x.get("type") == "TOKENS_LIMIT"]
    time_limits = [x for x in limits if isinstance(x, dict) and x.get("type") == "TIME_LIMIT"]

    # Prefer longer token window as primary; shorter as tertiary; time as secondary
    token_limits_sorted = sorted(
        token_limits,
        key=lambda x: (x.get("unit") or 0, x.get("number") or 0),
        reverse=True,
    )
    primary = _to_window(token_limits_sorted[0]) if token_limits_sorted else None
    tertiary = _to_window(token_limits_sorted[1]) if len(token_limits_sorted) > 1 else None
    secondary = _to_window(time_limits[0]) if time_limits else None

    if not primary:
        # Fall back to first limit
        first = next((x for x in limits if isinstance(x, dict)), None)
        if first:
            primary = _to_window(first)

    plan = data.get("planName") or data.get("plan") or data.get("plan_type")
    plan_s = str(plan) if plan else None

    logger.info("GLM: primary=%s", primary.used_percent if primary else "-")
    return [
        ProviderUsage(
            provider="glm",
            source="api",
            login_method=plan_s,
            primary=primary,
            secondary=secondary,
            tertiary=tertiary,
            updated_at=datetime.now(timezone.utc),
        )
    ]
