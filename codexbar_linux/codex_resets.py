"""Codex reset announcements from codex-resets.com (third party, opt-in).

The public API is free and needs no key; the site asks for a visible link to
codex-resets.com wherever its data is shown. Its data is classified from
@thsottiaux's posts by the site, not published by OpenAI. These are global
announcements for paid plans, never an account reading: they stay out of the
tray ring and are labeled as third-party data everywhere they appear.

No credentials, cookies or account data are sent. Responses are validated
strictly; text is shown as plain text and links are limited to known hosts.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger("codexbar-linux")

SITE_URL = "https://codex-resets.com"
STATUS_URL = SITE_URL + "/api/v1/status"
USER_AGENT = "QuotaLantern/0.1 (+https://github.com/caiolombello/quotalantern)"
ATTRIBUTION = "Data from Codex Resets (codex-resets.com) · not affiliated with OpenAI"
MIN_INTERVAL_SECONDS = 900  # at most one check every 15 minutes
MIN_RETRY_SECONDS = 60
DEFAULT_RETRY_AFTER_SECONDS = 900
MAX_RETRY_AFTER_SECONDS = 6 * 3600
MAX_RESPONSE_BYTES = 262_144
TEXT_LIMIT = 600
LINK_HOSTS = {"x.com", "twitter.com", "codex-resets.com"}
RESET_TYPES = {"regular", "banked"}
WATCH_LEVELS = {"elevated", "strong"}


@dataclass(frozen=True)
class ResetEvent:
    id: str
    reset_type: str
    announced_at: datetime
    text: str
    url: str
    scheduled_for: Optional[datetime] = None


@dataclass(frozen=True)
class ResetWatch:
    level: str
    chance_percent: Optional[int]
    window: str
    observed_at: datetime
    expires_at: datetime
    text: str
    url: str


@dataclass(frozen=True)
class ResetStatus:
    latest: Optional[ResetEvent]
    scheduled: Optional[ResetEvent]
    watch: Optional[ResetWatch]
    total: int
    avg_interval_days: Optional[float]
    generated_at: datetime


@dataclass(frozen=True)
class ResetSnapshot:
    """What the UI shows: last good status, latest problem and check time."""

    status: Optional[ResetStatus]
    error: Optional[str]
    checked_at: Optional[datetime]


# ── validation ─────────────────────────────────────────────────────────────

def _timestamp(value, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError(f"Invalid {field}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp without timezone in {field}")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value, field: str) -> Optional[datetime]:
    return None if value is None else _timestamp(value, field)


def _text(value, field: str, limit: int = TEXT_LIMIT) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field}")
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def safe_link(value) -> str:
    """Only https links to known hosts; anything else points at the site."""
    if isinstance(value, str) and len(value) <= 512:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme == "https" and parts.hostname in LINK_HOSTS and not parts.username and not parts.password:
            return value
    return SITE_URL


def _source_link(value) -> str:
    return safe_link(value.get("url")) if isinstance(value, dict) else SITE_URL


def _event(value, field: str, scheduled: bool = False) -> Optional[ResetEvent]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {field}")
    reset_type = value.get("reset_type")
    if reset_type not in RESET_TYPES:
        raise ValueError(f"Unknown reset type in {field}")
    return ResetEvent(
        id=_text(value.get("id"), f"{field}.id", 128),
        reset_type=reset_type,
        announced_at=_timestamp(value.get("announced_at"), f"{field}.announced_at"),
        text=_text(value.get("text"), f"{field}.text"),
        url=_source_link(value.get("source")),
        scheduled_for=_optional_timestamp(value.get("scheduled_for"), f"{field}.scheduled_for") if scheduled else None,
    )


def _watch(value) -> Optional[ResetWatch]:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("level") not in WATCH_LEVELS:
        raise ValueError("Invalid active_watch")
    chance = value.get("reset_chance_percent")
    if chance is not None and (type(chance) is not int or not 0 <= chance <= 100):
        raise ValueError("Invalid reset_chance_percent")
    return ResetWatch(
        level=value["level"],
        chance_percent=chance,
        window=_text(value.get("forecast_window"), "forecast_window", 80),
        observed_at=_timestamp(value.get("observed_at"), "observed_at"),
        expires_at=_timestamp(value.get("expires_at"), "expires_at"),
        text=_text(value.get("text"), "active_watch.text"),
        url=_source_link(value.get("source")),
    )


def parse_status(payload) -> ResetStatus:
    """Validate a /api/v1/status payload. Raises ValueError when malformed."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ValueError("Invalid Codex Resets response")
    meta = payload.get("meta")
    if not isinstance(meta, dict) or meta.get("api_version") != "v1":
        raise ValueError("Unsupported Codex Resets API version")
    data = payload["data"]
    stats = data.get("stats")
    if not isinstance(stats, dict):
        raise ValueError("Invalid Codex Resets stats")
    total = stats.get("total")
    if type(total) is not int or total < 0:
        raise ValueError("Invalid reset total")
    avg = stats.get("avg_interval_days")
    if avg is not None and (isinstance(avg, bool) or not isinstance(avg, (int, float)) or not 0 <= avg < 10_000):
        raise ValueError("Invalid average interval")
    scheduled = _event(data.get("scheduled_reset"), "scheduled_reset", scheduled=True)
    if scheduled is not None and data["scheduled_reset"].get("status") != "scheduled":
        raise ValueError("Invalid scheduled reset status")
    return ResetStatus(
        latest=_event(data.get("latest_reset"), "latest_reset"),
        scheduled=scheduled,
        watch=_watch(data.get("active_watch")),
        total=total,
        avg_interval_days=float(avg) if avg is not None else None,
        generated_at=_timestamp(meta.get("generated_at"), "generated_at"),
    )


# ── client ─────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Codex Resets redirect rejected")


def _default_open(request: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _retry_after(value) -> int:
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(MIN_RETRY_SECONDS, min(seconds, MAX_RETRY_AFTER_SECONDS))


class ResetFeed:
    """Throttled, ETag-aware reader that keeps the last good status."""

    def __init__(
        self,
        open_url: Callable = _default_open,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._open = open_url
        self._clock = clock
        self._now = now
        self._etag: Optional[str] = None
        self._status: Optional[ResetStatus] = None
        self._error: Optional[str] = None
        self._checked_at: Optional[datetime] = None
        self._last_attempt: Optional[float] = None
        self._blocked_until = 0.0

    def snapshot(self) -> ResetSnapshot:
        return ResetSnapshot(self._status, self._error, self._checked_at)

    def clear(self) -> None:
        """Forget everything, e.g. when the user turns the feature off."""
        self._etag = None
        self._status = None
        self._error = None
        self._checked_at = None
        self._last_attempt = None
        self._blocked_until = 0.0

    def refresh(self) -> bool:
        """Check the API when due. Returns True when a request was made."""
        tick = self._clock()
        if tick < self._blocked_until:
            return False
        if self._last_attempt is not None and tick - self._last_attempt < MIN_INTERVAL_SECONDS:
            return False
        self._last_attempt = tick
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self._etag and self._status is not None:
            headers["If-None-Match"] = self._etag
        request = urllib.request.Request(STATUS_URL, headers=headers)
        try:
            with self._open(request, 15) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("Codex Resets response too large")
                status = parse_status(json.loads(raw))
                etag = response.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and self._status is not None:
                self._error = None
                self._checked_at = self._now()
                return True
            if exc.code == 429:
                self._blocked_until = tick + _retry_after(exc.headers.get("Retry-After") if exc.headers else None)
                self._error = "Rate limited by Codex Resets; will retry later"
            else:
                self._error = f"Codex Resets HTTP {exc.code}"
            logger.info("Codex Resets check failed: %s", self._error)
            return True
        except (urllib.error.URLError, TimeoutError, OSError):
            self._error = "Codex Resets unreachable"
            logger.info("Codex Resets check failed: network")
            return True
        except ValueError as exc:
            self._error = "Codex Resets sent an unexpected response"
            logger.warning("Codex Resets response rejected (%s)", exc)
            return True
        self._status = status
        self._etag = etag if isinstance(etag, str) and len(etag) <= 200 else None
        self._error = None
        self._checked_at = self._now()
        return True


# ── announcements → notifications ─────────────────────────────────────────

WATCH_RANK = {"elevated": 1, "strong": 2}


def new_announcements(
    seen: dict,
    status: Optional[ResetStatus],
    now: Optional[datetime] = None,
    hints: bool = True,
) -> tuple[list[tuple[str, str]], dict]:
    """Compare with what was already announced; each item notifies once.

    A past reset found on the very first check stays silent (old news). A
    pending scheduled reset or a live hint is reported even then, because it
    is still ahead. Hints are AI-classified forecasts and are labeled so.
    """
    seen = seen if isinstance(seen, dict) else {}
    record = {
        "initialized": bool(seen.get("initialized")),
        "reset_id": seen.get("reset_id"),
        "scheduled_id": seen.get("scheduled_id"),
        "watch_key": seen.get("watch_key"),
        "watch_level": seen.get("watch_level"),
    }
    if status is None:
        return [], record
    now = now or datetime.now(timezone.utc)
    first = not record["initialized"]
    record["initialized"] = True
    notes: list[tuple[str, str]] = []
    latest, scheduled, watch = status.latest, status.scheduled, status.watch
    if latest is not None and latest.id != record["reset_id"]:
        record["reset_id"] = latest.id
        if not first:
            if latest.reset_type == "banked":
                notes.append(("Banked Codex reset granted",
                              "Paid plans got a reset credit to apply when you choose. Data: codex-resets.com"))
            else:
                notes.append(("Codex limits were reset",
                              "A reset for paid plans was announced. Data: codex-resets.com"))
    if scheduled is not None and scheduled.id != record["scheduled_id"]:
        record["scheduled_id"] = scheduled.id
        when = scheduled.scheduled_for.astimezone().strftime("%a %H:%M") if scheduled.scheduled_for else "a time not given yet"
        if scheduled.reset_type == "banked":
            notes.append(("Banked Codex reset announced",
                          f"A reset credit for paid plans is due {when}; you apply it when you choose. "
                          "Not confirmed until it arrives. Data: codex-resets.com"))
        else:
            notes.append(("Codex reset announced",
                          f"Scheduled for {when}; not confirmed until it happens. Data: codex-resets.com"))
    if hints and watch is not None and watch.expires_at > now:
        key = watch.observed_at.isoformat()
        new_hint = key != record["watch_key"]
        stronger = WATCH_RANK[watch.level] > WATCH_RANK.get(record["watch_level"], 0)
        if new_hint or stronger:
            chance = f", {watch.chance_percent}% chance" if watch.chance_percent is not None else ""
            notes.append((
                "Codex reset may be coming" if new_hint else "Codex reset signal got stronger",
                f"{watch.level.capitalize()} signal{chance}, {watch.window}. "
                "AI forecast from codex-resets.com, not confirmed by OpenAI.",
            ))
        record["watch_key"], record["watch_level"] = key, watch.level
    return notes, record
