"""OpenCode Go provider backed by the Console API and QuotaLantern OAuth."""

from datetime import datetime, timezone

from ..core.models import ProviderUsage, UsageWindow
from ..opencode_provider import fetch_opencode_stats


def fetch_opencode_go_usage() -> list[ProviderUsage]:
    stats = fetch_opencode_stats()
    usage = ProviderUsage(
        provider="opencode-go",
        source="console-api",
        login_method=f"{stats.plan} (OAuth)",
        error=stats.error,
    )
    if not stats.usage_available:
        return [usage]

    windows = []
    for limit, minutes in zip(stats.limits, (300, 7 * 24 * 60, None)):
        description = f"{limit.name}: " + "$" + f"{limit.used_usd:.2f}/" + "$" + f"{limit.amount_usd:g}"
        windows.append(
            UsageWindow(
                used_percent=limit.used_percent,
                window_minutes=minutes,
                reset_description=description,
                resets_at=limit.resets_at,
            )
        )
    usage.primary, usage.secondary, usage.tertiary = windows
    usage.updated_at = datetime.now(timezone.utc)
    return [usage]
