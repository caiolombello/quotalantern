"""OpenCode Go provider — wraps dashboard scraper as ProviderUsage."""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.models import ProviderUsage, UsageWindow
from ..opencode_provider import fetch_opencode_stats


def fetch_opencode_go_usage() -> list[ProviderUsage]:
    stats = fetch_opencode_stats(days=7)
    if stats.error and not stats.usage_available:
        err = stats.error
        if stats.cookie_expired:
            err = "OpenCode cookie expired. Update opencode_auth_cookie"
        return [
            ProviderUsage(
                provider="opencode-go",
                source="dashboard",
                login_method=stats.plan,
                error=err,
            )
        ]

    windows: list[UsageWindow] = []
    for limit in stats.limits:
        if limit.used_percent is None:
            continue
        used_usd = limit.amount_usd * limit.used_percent / 100
        reset = limit.reset_description or limit.description
        desc = f"${used_usd:.0f}/${limit.amount_usd}"
        if reset:
            desc = f"{desc} · {reset}"
        windows.append(
            UsageWindow(
                used_percent=limit.used_percent,
                reset_description=f"{limit.name}: {desc}",
            )
        )

    if not windows and stats.error:
        return [
            ProviderUsage(
                provider="opencode-go",
                source="dashboard",
                login_method=stats.plan,
                error=stats.error,
            )
        ]

    if not windows:
        return [
            ProviderUsage(
                provider="opencode-go",
                source="dashboard",
                login_method=stats.plan,
                error="OpenCode usage unavailable. Set OPENCODE_AUTH_COOKIE",
            )
        ]

    return [
        ProviderUsage(
            provider="opencode-go",
            source="dashboard",
            login_method=stats.plan,
            primary=windows[0] if len(windows) > 0 else None,
            secondary=windows[1] if len(windows) > 1 else None,
            tertiary=windows[2] if len(windows) > 2 else None,
            updated_at=datetime.now(timezone.utc),
            error=stats.error if stats.cookie_expired else None,
        )
    ]
