"""Presentation only: no collection, eligibility, storage or credential changes."""
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from .providers import PROVIDER_BY_ID
from .codex_resets import ATTRIBUTION, SITE_URL
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
class RingSegment:
    """One provider's slice of the tray ring: its most-used window, or neutral."""
    provider: str
    state: str
    percent: int | None
    window: str = ""

    def text(self) -> str:
        return f"{self.provider} {self.percent}%" if self.percent is not None else f"{self.provider} {self.state}"


@dataclass(frozen=True)
class TrayReading:
    state: str
    percent: int | None
    label: str
    description: str
    segments: tuple = ()
    context: tuple = ()  # compact source/age lines for the tray menu header
    unconfirmed: str = ""

    @property
    def segmented(self) -> bool:
        """Two or more providers share the ring and at least one reading is confirmed."""
        return self.percent is not None and len(self.segments) >= 2


SINGLE_RING_POLICY = "Ring: one reported window; largest consumed fraction, not total quota."
SEGMENTED_RING_POLICY = "Ring: one segment per provider, clockwise from the top; never a total or average."


def tray_reading(usages, now=None, warn_at=70, crit_at=90):
    """Select ONE window per provider, never add or average incompatible allowances.

    With one provider the ring is that provider's largest consumed fraction.
    With several, each connected provider gets its own ring segment (in the
    order given) so one exhausted service does not paint the whole ring.
    The label still names the largest consumed fraction: an attention
    heuristic, not shared capacity or a headroom recommendation.
    Exclude costs; unknown, failed and old reads stay neutral.
    """
    now = now or datetime.now(timezone.utc)

    def level(pct):
        return "critical" if pct >= crit_at else "warning" if pct >= warn_at else "ok"

    candidates = []
    unconfirmed = []
    segments = []
    has_stale = False
    for usage in usages:
        spec = PROVIDER_BY_ID.get(usage.provider)
        if spec is None or spec.category != "subscription":
            continue
        name = ui.display_name(usage.provider)
        stamp = usage.updated_at
        dated = isinstance(stamp, datetime) and stamp.tzinfo is not None and stamp.utcoffset() is not None
        age = (now - stamp).total_seconds() if dated else None
        windows = [(label, w) for label, w in (("Primary", usage.primary), ("Secondary", usage.secondary), ("Tertiary", usage.tertiary)) if w]
        valid = bool(windows) and all(type(w.used_percent) is int and 0 <= w.used_percent <= 100 for _, w in windows)
        stale = ui.is_stale(usage.error) or (valid and age is not None and age > 600)
        has_stale |= stale
        if usage.error or age is None or not 0 <= age <= 600 or not valid:
            # Not-connected adapters stay out of the ring and its coverage
            # note; anything else keeps a neutral segment so missing data is
            # visible, never an empty arc.
            if not (ui.is_soft_status(usage.error) and not ui.has_usage_data(usage)):
                unconfirmed.append(f"{name}: {'stale' if stale else 'unknown / error'}")
                segments.append(RingSegment(name, "stale" if stale else "unknown", None))
            continue
        own = []
        for label, window in windows:
            duration = window.window_minutes
            if type(duration) is int and duration > 0:
                label += f" ({duration // 60}h)" if duration % 60 == 0 else f" ({duration}m)"
            own.append((window.used_percent, usage, label))
        candidates.extend(own)
        pct, _, window = max(own, key=lambda item: item[0])
        segments.append(RingSegment(name, level(pct), pct, window))
    if candidates:
        pct, usage, window = max(candidates, key=lambda item: item[0])
        state = level(pct)
        label = f"{ui.display_name(usage.provider)} {window}: {pct}% used"
        details = reading_details(usage, now)
        context = (f"{details[0]} · {details[1]}",)
        lines = [label, *details]
        if len(segments) >= 2:
            lines += [SEGMENTED_RING_POLICY, "Segments: " + " · ".join(segment.text() for segment in segments)]
        else:
            lines.append(SINGLE_RING_POLICY)
        description = "\n".join(lines)
    else:
        pct, state = None, "stale" if has_stale else "unknown"
        context = ()
        label = "Cached / stale — quota unconfirmed" if has_stale else "Quota unknown"
        description = "\n".join((label, SINGLE_RING_POLICY))
        for usage in usages:
            description += "\n" + ui.display_name(usage.provider) + " · " + " · ".join(reading_details(usage, now)[:2])
    unconfirmed_text = "Unconfirmed: " + "; ".join(unconfirmed) if unconfirmed else ""
    if unconfirmed_text:
        description += "\n" + unconfirmed_text
    return TrayReading(state, pct, label, description, tuple(segments), context, unconfirmed_text)


def tray_icon_name(usages, now=None, warn_at=70, crit_at=90):
    state = tray_reading(usages, now, warn_at, crit_at).state
    return "neutral" if state in ("unknown", "stale") else state


def reading_badge(usage, now=None):
    """Short status pill (kind, text). Text carries a symbol, never color alone."""
    now = now or datetime.now(timezone.utc)
    if ui.is_stale(usage.error):
        return "stale", "◷ Stale"
    if usage.error and ui.is_soft_status(usage.error) and not ui.has_usage_data(usage):
        return "neutral", "○ Not connected"
    if usage.error:
        return "error", "! Error"
    if not ui.has_usage_data(usage):
        return ("spend", "$ Spend") if ui.monetary_label(usage) else ("neutral", "? Unknown")
    stamp = usage.updated_at
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None or stamp > now:
        return "neutral", "? Unverified"
    if (now - stamp).total_seconds() > 600:
        return "neutral", "◷ Older"
    return "ok", "✓ Fresh"


def window_span(minutes):
    """Human window length: 300 → 5h, 10080 → 7d. Unknown lengths stay empty."""
    if type(minutes) is not int or minutes <= 0:
        return ""
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def detail_rows(usage, now=None):
    """Submenu context: source and age on one row; the state only when it adds something."""
    source, age, state = reading_details(usage, now)
    rows = [f"{source} · {age}"]
    if state != "Reported usage — recent":
        rows.append(state)
    return rows


def menu_rows(reading):
    """Tray menu header: the ring label, its source and age, and what is unconfirmed.

    The ring policy and the per-segment list stay in the icon title/tooltip and
    the Overview; the provider rows right below already show every segment.
    """
    rows = [reading.label, *reading.context]
    if reading.unconfirmed:
        rows.append(reading.unconfirmed)
    return rows


def _without_window_label(text):
    """'Session (5h): resets in 3h' → 'resets in 3h' when the row already names the window."""
    _, sep, rest = text.partition(": ")
    return rest if sep and rest.lower().startswith("reset") else text


def _countdown(resets_at, now):
    if not isinstance(resets_at, datetime) or resets_at.tzinfo is None or resets_at.utcoffset() is None:
        return ""
    seconds = (resets_at - now).total_seconds()
    if seconds <= 0:
        return "resetting…"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"resets in {minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"resets in {hours}h {minutes}m" if minutes else f"resets in {hours}h"
    days, hours = divmod(hours, 24)
    return f"resets in {days}d {hours}h" if hours else f"resets in {days}d"


def reset_text(window, now=None):
    """Reset hint for one window, live from resets_at when the provider only gave a countdown.

    Provider text that says more than a countdown (credits, spend caps) is kept as is.
    """
    now = now or datetime.now(timezone.utc)
    text = _without_window_label(re.sub(r"\s+", " ", window.reset_description or "").strip())
    if not text or text.lower().startswith("reset"):
        live = _countdown(window.resets_at, now)
        if live:
            return live
    return text[:80]


def window_row(name, window, now=None):
    """'Session (5h) — 60% used · resets in 2h': plain text, no block glyphs."""
    span = window_span(window.window_minutes)
    head = f"{name} ({span})" if span else name
    pct = window.used_percent
    text = f"{head} — {pct}% used" if type(pct) is int and 0 <= pct <= 100 else f"{head} — usage unknown"
    reset = reset_text(window, now)
    return f"{text} · {reset}" if reset else text


def format_next_reset(next_reset: str) -> str:
    """'codex Session | resets in 2h' → 'Next reset: Codex Session — resets in 2h'."""
    if not next_reset:
        return ""
    name, _, when = next_reset.partition(" | ")
    provider, _, window = name.partition(" ")
    pretty = " ".join(part for part in (ui.display_name(provider), window) if part)
    when = ui.clean_reset(_without_window_label(when.strip()))
    return f"Next reset: {pretty} — {when}" if when else f"Next reset: {pretty}"


def format_interval(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def _excerpt(text, limit=160):
    first = text.split("\n")[0].strip()
    if len(first) <= limit:
        return first
    return first[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


@dataclass(frozen=True)
class ResetView:
    headline: str
    excerpt: str
    lines: tuple
    announcement_url: str


def reset_view(snapshot, now=None):
    """Plain text for Codex Resets data. None when there is nothing to show.

    Global, third-party announcements: always attributed, never a quota.
    """
    if snapshot is None or (snapshot.status is None and not snapshot.error):
        return None
    now = now or datetime.now(timezone.utc)
    status = snapshot.status
    lines = []
    excerpt = ""
    url = SITE_URL
    if status is None:
        headline = "Codex reset announcements unavailable"
    else:
        latest = status.latest
        if latest is None:
            headline = "No global Codex reset recorded yet"
        else:
            kind = "banked credit" if latest.reset_type == "banked" else "regular"
            age = ui.human_age(now - latest.announced_at) if latest.announced_at <= now else "just now"
            headline = f"Last global Codex reset: {age} ({kind})"
            excerpt = _excerpt(latest.text)
            url = latest.url
        if status.scheduled is not None:
            when = status.scheduled.scheduled_for
            when = when.astimezone().strftime("%a %d %b %H:%M") if when else "time not given"
            lines.append(f"Scheduled: {when} · announced, not confirmed yet")
        watch = status.watch
        if watch is not None and watch.expires_at > now:
            chance = f"{watch.chance_percent}% chance" if watch.chance_percent is not None else "chance not given"
            lines.append(f"Reset watch ({watch.level}): {chance}, {watch.window} · AI forecast, not official")
        if status.avg_interval_days is not None:
            lines.append(f"{status.total} resets tracked · one every {status.avg_interval_days:g} days on average")
    if snapshot.error:
        checked = snapshot.checked_at
        if status is not None and checked is not None:
            lines.append(f"{snapshot.error} · showing data checked {ui.human_age(now - checked)}")
        else:
            lines.append(snapshot.error)
    lines.append(ATTRIBUTION)
    return ResetView(headline, excerpt, tuple(lines), url)
