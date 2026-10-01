"""Presentation only: no collection, eligibility, storage or credential changes."""
from datetime import datetime, timezone
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


def tray_icon_name(usages, now=None, warn_at=70, crit_at=90):
    """Aggregate recent, error-free reported quota for the icon, never infer zero."""
    now = now or datetime.now(timezone.utc)
    percentages = []
    for usage in usages:
        stamp = usage.updated_at
        if usage.error or not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            continue
        if not 0 <= (now - stamp).total_seconds() <= 600:
            continue
        windows = [w for w in (usage.primary, usage.secondary, usage.tertiary) if w]
        if not windows or any(type(w.used_percent) is not int or not 0 <= w.used_percent <= 100 for w in windows):
            continue
        percentages.extend(w.used_percent for w in windows)
    if not percentages:
        return "neutral"
    worst = max(percentages)
    return "critical" if worst >= crit_at else "warning" if worst >= warn_at else "ok"
