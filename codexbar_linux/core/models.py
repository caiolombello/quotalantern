"""Shared data models for all providers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class UsageWindow:
    used_percent: int
    window_minutes: Optional[int] = None
    reset_description: str = ""
    resets_at: Optional[datetime] = None


@dataclass
class ProviderUsage:
    provider: str
    source: str
    version: Optional[str] = None
    account_email: Optional[str] = None
    login_method: Optional[str] = None
    primary: Optional[UsageWindow] = None
    secondary: Optional[UsageWindow] = None
    tertiary: Optional[UsageWindow] = None
    credits_remaining: Optional[int] = None
    balance_usd: Optional[float] = None
    updated_at: Optional[datetime] = None
    error: Optional[str] = None
