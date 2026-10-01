"""Provider registry contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from ..core.models import ProviderUsage

ProviderCategory = Literal["subscription", "api_cost", "other"]

Fetcher = Callable[[], list[ProviderUsage]]


@dataclass(frozen=True)
class ProviderSpec:
    """Metadata + fetch callable for one usage provider."""

    id: str
    display_name: str
    category: ProviderCategory
    open_url: str
    auth_hint: str
    default_enabled: bool
    fetch: Fetcher


CATEGORY_LABELS: dict[str, str] = {
    "subscription": "Subscription / plan limits",
    "api_cost": "API cost / org billing",
    "other": "Other",
}
