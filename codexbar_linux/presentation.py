"""Presentation only: no collection, eligibility, storage or credential changes."""
from datetime import datetime, timezone
from dataclasses import dataclass
from .providers import PROVIDER_BY_ID
from . import ui_common as ui

APP_NAME = "QuotaLantern"

def reading_details(usage, now=None):
    now = now or datetime.now(timezone.utc)
    source = f"Source: {usage.source or 'unknown'}"
    stamp = usage.updated_at
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None or stamp > now:
        age = "Updated: unknown"
        timing = "timestamp unknown"
    else:
        delta = now - stamp
        age = f"Updated: {ui.human_age(delta)}"
        timing = "recent" if delta.total_seconds() <= 600 else "older than 10 min"
    if ui.is_stale(usage.error):
        state = "Cached / stale — not confirmed now"
    elif usage.error:
        state = "Attention — collection reported a problem"
    elif ui.monetary_label(usage) and not ui.has_usage_data(usage):
        state = f"Cost / balance only — quota unknown; {timing}"
    elif not ui.has_usage_data(usage):
        state = "Quota unknown — no percentage available"
    else:
        state = f"Reported usage — {timing}"
    return (source, age, state)


@dataclass(frozen=True)
class TrayReading:
    state: str
    percent: int | None
    label: str
    description: str


def tray_reading(usages, now=None, warn_at=70, crit_at=90):
    """Select ONE window, never add or average incompatible allowances.

    Largest consumed fraction is an attention heuristic, not shared capacity
    or a headroom recommendation. Exclude costs, unknown, failed and old reads.
    """
    now = now or datetime.now(timezone.utc)
    candidates = []
    unconfirmed = []
    has_stale = False
    for usage in usages:
        spec = PROVIDER_BY_ID.get(usage.provider)
        if spec is None or spec.category != "subscription":
            continue
        stamp = usage.updated_at
        dated = isinstance(stamp, datetime) and stamp.tzinfo is not None and stamp.utcoffset() is not None
        age = (now - stamp).total_seconds() if dated else None
        windows = [(name, w) for name, w in (("Primary", usage.primary), ("Secondary", usage.secondary), ("Tertiary", usage.tertiary)) if w]
        valid = bool(windows) and all(type(w.used_percent) is int and 0 <= w.used_percent <= 100 for _, w in windows)
        stale = ui.is_stale(usage.error) or (valid and age is not None and age > 600)
        has_stale |= stale
        if usage.error or age is None or not 0 <= age <= 600 or not valid:
            unconfirmed.append(f"{ui.display_name(usage.provider)}: {'stale' if stale else 'unknown / error'}")
            continue
        for name, window in windows:
            duration = window.window_minutes
            if type(duration) is int and duration > 0:
                name += f" ({duration // 60}h)" if duration % 60 == 0 else f" ({duration}m)"
            candidates.append((window.used_percent, usage, name))
    policy = "Ring: one reported window; largest consumed fraction, not total quota."
    if candidates:
        pct, usage, window = max(candidates, key=lambda item: item[0])
        state = "critical" if pct >= crit_at else "warning" if pct >= warn_at else "ok"
        label = f"{ui.display_name(usage.provider)} {window}: {pct}% used"
        description = "\n".join((label, *reading_details(usage, now), policy))
    else:
        pct, state = None, "stale" if has_stale else "unknown"
        label = "Cached / stale — quota unconfirmed" if has_stale else "Quota unknown"
        description = "\n".join((label, policy))
        for usage in usages:
            description += "\n" + ui.display_name(usage.provider) + " · " + " · ".join(reading_details(usage, now)[:2])
    if unconfirmed:
        description += "\nUnconfirmed: " + "; ".join(unconfirmed)
    return TrayReading(state, pct, label, description)


def tray_icon_name(usages, now=None, warn_at=70, crit_at=90):
    state = tray_reading(usages, now, warn_at, crit_at).state
    return "neutral" if state in ("unknown", "stale") else state
