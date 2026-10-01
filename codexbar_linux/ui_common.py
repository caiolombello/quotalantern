"""Shared UI helpers for tray menu and dashboard window."""

from __future__ import annotations

import re
import math
from datetime import datetime, timezone
from typing import Optional

from .core.models import ProviderUsage, UsageWindow
from .providers import DISPLAY_NAMES

# Semantic colors (hex) for GTK markup / CSS
COLOR_OK = "#4ade80"
COLOR_WARN = "#facc15"
COLOR_CRIT = "#f87171"
COLOR_MUTED = "#94a3b8"
COLOR_SOFT = "#64748b"
COLOR_BG_DARK = "#1e293b"
COLOR_FG_DARK = "#e2e8f0"


def display_name(provider_id: str) -> str:
    return DISPLAY_NAMES.get(provider_id, provider_id)


def monetary_label(usage: ProviderUsage) -> str:
    """Label API month-to-date spend separately from other balances."""
    amount = usage.balance_usd
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
        return ""
    if usage.provider in {"openai-api", "claude-api"}:
        return f"Month-to-date cost: ${amount:.2f} USD"
    return f"Balance: ${amount:.2f}"


def worst_usage_pct(usage: ProviderUsage) -> int:
    windows = (usage.primary, usage.secondary, usage.tertiary)
    return max((window.used_percent for window in windows if window), default=0)


def minimax_window(usage: ProviderUsage) -> tuple[str, int, int]:
    """Return (best_window_name, best_pct, worst_pct)."""
    candidates: list[tuple[str, int]] = []
    for name, window in (
        ("Session", usage.primary),
        ("Weekly", usage.secondary),
        ("Extra", usage.tertiary),
    ):
        if window:
            candidates.append((name, window.used_percent))
    if not candidates:
        return ("", 0, 0)
    worst = max(candidates, key=lambda item: item[1])
    best = min(candidates, key=lambda item: item[1])
    return (best[0], best[1], worst[1])


def is_soft_status(error: Optional[str]) -> bool:
    if not error:
        return False
    lower = error.lower()
    if any(x in lower for x in ("timeout", "network", "http 5", "parse error", "circuit open")):
        return False
    needles = (
        "not authenticated",
        "not logged in",
        "not signed",
        "session missing",
        "api key missing",
        "missing. set",
        "missing. run",
        "optional",
        "no subscription",
        "login",
        "cookie",
        "credentials missing",
        "oauth missing",
        "expired and refresh failed",
    )
    return any(n in lower for n in needles)


def is_stale(error: Optional[str]) -> bool:
    return bool(error and "stale" in error.lower())


def is_hard_error(usage: ProviderUsage) -> bool:
    if not usage.error or is_stale(usage.error) or is_soft_status(usage.error):
        return False
    return True


def has_usage_data(usage: ProviderUsage) -> bool:
    return bool(usage.primary or usage.secondary or usage.tertiary)


def status_level(usage: ProviderUsage, warn_at: int = 70, crit_at: int = 90) -> str:
    """Return one of: ok | warn | crit | soft | error | empty."""
    if is_soft_status(usage.error) and not has_usage_data(usage):
        return "soft"
    if is_hard_error(usage):
        return "error"
    if not has_usage_data(usage):
        return "empty"
    pct = worst_usage_pct(usage)
    if pct >= crit_at:
        return "crit"
    if pct >= warn_at:
        return "warn"
    return "ok"


def status_emoji(level: str) -> str:
    return {
        "ok": "🟢",
        "warn": "🟡",
        "crit": "🔴",
        "soft": "⚪",
        "error": "⚠",
        "empty": "○",
    }.get(level, "○")


def status_color(level: str) -> str:
    return {
        "ok": COLOR_OK,
        "warn": COLOR_WARN,
        "crit": COLOR_CRIT,
        "soft": COLOR_SOFT,
        "error": COLOR_CRIT,
        "empty": COLOR_MUTED,
    }.get(level, COLOR_MUTED)


def color_for_pct(pct: int, warn_at: int = 70, crit_at: int = 90) -> str:
    if pct >= crit_at:
        return COLOR_CRIT
    if pct >= warn_at:
        return COLOR_WARN
    return COLOR_OK


def block_bar(pct: int, width: int = 10) -> str:
    filled = max(0, min(width, int(width * pct / 100)))
    return "█" * filled + "░" * (width - filled)


def clean_reset(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"(?i)resets?\s*", "resets ", cleaned)
    return cleaned[:80]


def short_error(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def human_age(delta) -> str:
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


def time_left(target: Optional[datetime]) -> str:
    if not target:
        return ""
    delta = target - datetime.now(timezone.utc)
    secs = delta.total_seconds()
    if secs <= 0:
        return "resetting…"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h"
    return f"{hrs // 24}d {hrs % 24}h"


def window_label(name: str, window: UsageWindow) -> str:
    pct = window.used_percent
    bar = block_bar(pct, width=10)
    reset = clean_reset(window.reset_description or "")
    if not reset and window.resets_at:
        reset = time_left(window.resets_at)
    text = f"{name}: {bar}  {pct}%"
    if reset:
        text += f"  ({reset})"
    return text


def classify_usages(
    usages: list[ProviderUsage],
    hide_offline: bool = False,
    warn_at: int = 70,
    crit_at: int = 90,
) -> dict[str, list[ProviderUsage]]:
    """Bucket providers for menu sections."""
    buckets: dict[str, list[ProviderUsage]] = {
        "critical": [],
        "active": [],
        "errors": [],
        "offline": [],
    }
    for u in usages:
        level = status_level(u, warn_at=warn_at, crit_at=crit_at)
        if level == "soft":
            if not hide_offline:
                buckets["offline"].append(u)
            continue
        if level == "error":
            buckets["errors"].append(u)
            continue
        if level in ("crit", "warn") or worst_usage_pct(u) >= warn_at:
            if level == "crit" or worst_usage_pct(u) >= crit_at:
                buckets["critical"].append(u)
            else:
                buckets["active"].append(u)
            continue
        if has_usage_data(u) or level == "empty":
            buckets["active"].append(u)
        else:
            buckets["offline"].append(u)

    def sort_pct(items: list[ProviderUsage]) -> list[ProviderUsage]:
        return sorted(items, key=lambda u: (-worst_usage_pct(u), display_name(u.provider).lower()))

    for key in buckets:
        buckets[key] = sort_pct(buckets[key])
    return buckets


def recommendation(
    usages: list[ProviderUsage],
    *,
    now: Optional[datetime] = None,
    max_age_seconds: float = 600.0,
) -> tuple[str, str, int]:
    """Compare only fresh, known time-quota sources with identical constraints.

    This is a headroom heuristic, not a model-quality or cost recommendation.
    Credits, money, mixed-source quotas and unqualified adapters are excluded.
    """
    current = now or datetime.now(timezone.utc)
    candidates = []
    # Only the parser validated in this patch is qualified. Other adapters
    # can currently clamp or omit invalid constraints before reaching the UI.
    known_sources = {("codex", "oauth")}
    for usage in usages:
        if (usage.provider, usage.source) not in known_sources or usage.error:
            continue
        if usage.updated_at is None or usage.updated_at.tzinfo is None:
            continue
        age = (current - usage.updated_at).total_seconds()
        if not 0 <= age <= max_age_seconds:
            continue
        windows = []
        invalid = False
        for slot, label, window in (
            ("primary", "Session", usage.primary),
            ("secondary", "Weekly", usage.secondary),
            ("tertiary", "Extra", usage.tertiary),
        ):
            if window is None:
                continue
            pct = window.used_percent
            if isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100:
                invalid = True
                break
            minutes = window.window_minutes
            expected = {"primary": 300, "secondary": 10080}.get(slot)
            if expected is None or minutes != expected:
                invalid = True
                break
            if window.resets_at is not None:
                if window.resets_at.tzinfo is None or window.resets_at <= current:
                    invalid = True
                    break
            windows.append((slot, minutes, label, pct))
        if invalid or not windows or max(w[3] for w in windows) >= 100:
            continue
        signature = tuple((w[0], w[1]) for w in windows)
        worst = max(windows, key=lambda w: w[3])
        candidates.append((signature, usage, worst))
    if not candidates or len({item[0] for item in candidates}) != 1:
        return "", "", 0
    _, usage, worst = min(candidates, key=lambda item: item[2][3])
    return display_name(usage.provider), worst[2], worst[3]


def bottleneck(usages: list[ProviderUsage]) -> tuple[str, int]:
    """Provider with highest usage among healthy ones."""
    worst_name = ""
    worst_pct = -1
    for u in usages:
        if is_hard_error(u) or is_soft_status(u.error):
            continue
        if not has_usage_data(u):
            continue
        pct = worst_usage_pct(u)
        if pct > worst_pct:
            worst_pct = pct
            worst_name = display_name(u.provider)
    if worst_pct < 0:
        return "", 0
    return worst_name, worst_pct
