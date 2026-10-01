"""State and history persistence."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.models import ProviderUsage
from .opencode_provider import OpenCodeStats


STATE_DIR = Path.home() / ".local/share/codexbar-linux"
STATE_PATH = STATE_DIR / "state.json"
HISTORY_PATH = STATE_DIR / "history.jsonl"
MAX_HISTORY_LINES = 10_000


def _ensure_private_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_DIR.chmod(0o700)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"windows": {}, "notifications": {}}


def save_state(state: dict[str, Any]) -> None:
    _ensure_private_state_dir()
    text = json.dumps(state, indent=2, sort_keys=True)
    # Only write if content changed
    if STATE_PATH.exists():
        try:
            existing = STATE_PATH.read_text()
            if existing == text:
                STATE_PATH.chmod(0o600)
                return
        except OSError:
            pass
    tmp = STATE_PATH.with_suffix(".json.tmp")
    _write_private(tmp, text)
    tmp.replace(STATE_PATH)
    STATE_PATH.chmod(0o600)


def _dt(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def provider_to_dict(provider: ProviderUsage) -> dict[str, Any]:
    def window_to_dict(window):
        if not window:
            return None
        return {
            "used_percent": window.used_percent,
            "window_minutes": window.window_minutes,
            "reset_description": window.reset_description,
            "resets_at": _dt(window.resets_at),
        }

    return {
        "provider": provider.provider,
        "source": provider.source,
        "version": provider.version,
        "account_email": provider.account_email,
        "login_method": provider.login_method,
        "credits_remaining": provider.credits_remaining,
        "updated_at": _dt(provider.updated_at),
        "error": provider.error,
        "primary": window_to_dict(provider.primary),
        "secondary": window_to_dict(provider.secondary),
        "tertiary": window_to_dict(provider.tertiary),
    }


def opencode_to_dict(stats: OpenCodeStats | None) -> dict[str, Any] | None:
    if not stats:
        return None
    return {
        "provider": "opencode-go",
        "plan": stats.plan,
        "model_count": stats.model_count,
        "usage_available": stats.usage_available,
        "workspace_id": stats.workspace_id,
        "cookie_expired": stats.cookie_expired,
        "error": stats.error,
        "limits": [
            {
                "name": limit.name,
                "amount_usd": limit.amount_usd,
                "description": limit.description,
                "used_percent": limit.used_percent,
                "reset_description": limit.reset_description,
            }
            for limit in stats.limits
        ],
    }


def snapshot(usages: list[ProviderUsage], opencode: OpenCodeStats | None) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "providers": [provider_to_dict(u) for u in usages],
        "opencode_go": opencode_to_dict(opencode),
    }


def append_history(data: dict[str, Any]) -> None:
    _ensure_private_state_dir()
    line = json.dumps(data, sort_keys=True) + "\n"
    fd = os.open(HISTORY_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line)
    HISTORY_PATH.chmod(0o600)
    # Rotate if too large
    _rotate_history_if_needed()


def _rotate_history_if_needed() -> None:
    """Keep history file under MAX_HISTORY_LINES by dropping oldest entries."""
    if not HISTORY_PATH.exists():
        return
    try:
        with HISTORY_PATH.open("r") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= MAX_HISTORY_LINES:
        return
    # Keep the newest lines
    keep = lines[-MAX_HISTORY_LINES:]
    tmp = HISTORY_PATH.with_suffix(".jsonl.tmp")
    try:
        _write_private(tmp, "".join(keep))
        tmp.replace(HISTORY_PATH)
        HISTORY_PATH.chmod(0o600)
    except OSError:
        pass
