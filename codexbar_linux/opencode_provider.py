"""OpenCode Go provider.

The public Go model API does not document current usage windows. This provider
validates the API key through `/models` and optionally scrapes the authenticated
workspace Go dashboard when `OPENCODE_AUTH_COOKIE` is available.
"""

import html
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .core.resilience import RetryConfig, retry


OPENCODE_GO_MODELS_URL = "https://opencode.ai/zen/go/v1/models"
DEFAULT_WORKSPACE_ID = "wrk_01KEAF6115M0KXGH91W0881STM"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) codexbar-linux/0.2"


@dataclass
class GoLimitWindow:
    name: str
    amount_usd: int
    description: str
    used_percent: Optional[int] = None
    reset_description: str = ""


@dataclass
class OpenCodeStats:
    plan: str = "OpenCode Go"
    model_count: int = 0
    limits: tuple[GoLimitWindow, ...] = (
        GoLimitWindow("5 hour", 12, "$12 of usage every 5 hours"),
        GoLimitWindow("Weekly", 30, "$30 of usage per week"),
        GoLimitWindow("Monthly", 60, "$60 of usage per month"),
    )
    usage_available: bool = False
    workspace_id: str = ""
    cookie_expired: bool = False
    error: Optional[str] = None


def _load_api_key() -> Optional[str]:
    """Load OpenCode Go API key without logging or exposing it."""
    env_key = os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("OPENCODE_API_KEY")
    if env_key:
        return env_key.strip()

    auth_path = Path.home() / ".local/share/opencode/auth.json"
    try:
        data = json.loads(auth_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    provider = data.get("opencode-go")
    if isinstance(provider, dict):
        key = provider.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    return None


def _load_dashboard_cookie() -> Optional[str]:
    """Load opencode.ai dashboard auth cookie from env or a local private file."""
    raw = os.environ.get("OPENCODE_AUTH_COOKIE", "").strip()
    if raw:
        return _sanitize_cookie(raw)

    cookie_path = Path.home() / ".local/share/codexbar-linux/opencode_auth_cookie"
    try:
        mode = cookie_path.stat().st_mode
        if mode & 0o077:
            return None
        raw = cookie_path.read_text().strip()
    except OSError:
        return None
    return _sanitize_cookie(raw) if raw else None


def _sanitize_cookie(value: str) -> Optional[str]:
    """Reject cookies that contain newlines (header injection)."""
    if "\r" in value or "\n" in value:
        return None
    return value


def _workspace_id() -> str:
    raw = os.environ.get("OPENCODE_GO_WORKSPACE_ID", "").strip()
    if raw:
        return raw

    raw_url = os.environ.get("OPENCODE_GO_URL", "").strip()
    match = re.search(r"/workspace/(wrk_[A-Z0-9]+)/go", raw_url)
    if match:
        return match.group(1)
    return DEFAULT_WORKSPACE_ID


@retry(RetryConfig(max_tries=3, backoff=1.0))
def _validate_go_api_key() -> tuple[int, Optional[str]]:
    key = _load_api_key()
    if not key:
        return 0, "OpenCode Go API key not found"

    req = urllib.request.Request(
        OPENCODE_GO_MODELS_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return 0, f"OpenCode Go API returned HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return 0, f"OpenCode Go API error: {exc}"

    models = payload.get("data") if isinstance(payload, dict) else None
    return (len(models) if isinstance(models, list) else 0), None


@retry(RetryConfig(max_tries=2, backoff=1.0))
def _dashboard_html(cookie: str, workspace_id: str) -> str:
    cookie_header = cookie if "=" in cookie else f"auth={cookie}"
    req = urllib.request.Request(
        f"https://opencode.ai/workspace/{workspace_id}/go",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": cookie_header,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read().decode("utf-8", "replace")


def _strip_tags(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_window(text: str, names: tuple[str, ...]) -> tuple[Optional[int], str]:
    for name in names:
        match = re.search(rf"{re.escape(name)}(.{{0,240}}?)(\d{{1,3}})\s*%", text, flags=re.I)
        if not match:
            continue
        percent = min(100, int(match.group(2)))
        tail = text[match.end() : match.end() + 180]
        reset = ""
        reset_match = re.search(
            r"(resets?\s+(?:in\s+)?.*?|reinicia\s+em\s+.*?)(?=\s+(?:Uso\s+Semanal|Uso\s+Mensal|Use\s+seu\s+saldo|Weekly|Monthly|©)|$)",
            tail,
            flags=re.I,
        )
        if reset_match:
            reset = reset_match.group(0).strip()
        return percent, reset
    return None, ""


def _scrape_usage(workspace_id: str) -> Optional[tuple[GoLimitWindow, ...]]:
    cookie = _load_dashboard_cookie()
    if not cookie:
        return None

    raw = _dashboard_html(cookie, workspace_id)
    text = _strip_tags(raw)

    windows = []
    specs = [
        ("5 hour", 12, "$12 of usage every 5 hours", ("Rolling usage", "5 hour", "5-hour", "Uso Contínuo", "Uso Continuo")),
        ("Weekly", 30, "$30 of usage per week", ("Weekly usage", "Weekly", "Uso Semanal")),
        ("Monthly", 60, "$60 of usage per month", ("Monthly usage", "Monthly", "Uso Mensal")),
    ]
    found = False
    for name, amount, description, labels in specs:
        percent, reset = _parse_window(text, labels)
        if percent is not None:
            found = True
        windows.append(GoLimitWindow(name, amount, description, percent, reset))
    return tuple(windows) if found else None


def _is_login_page(text: str) -> bool:
    lower = text.lower()
    return "sign in" in lower or "login" in lower or "entrar" in lower or "auth" in lower


def fetch_opencode_stats(days: int = 7) -> OpenCodeStats:
    """Fetch OpenCode Go metadata, limits, and dashboard usage when available."""
    del days

    model_count, api_error = _validate_go_api_key()
    workspace_id = _workspace_id()
    stats = OpenCodeStats(model_count=model_count, workspace_id=workspace_id, error=api_error)

    cookie = _load_dashboard_cookie()
    if not cookie:
        return stats

    try:
        raw = _dashboard_html(cookie, workspace_id)
    except urllib.error.HTTPError as exc:
        stats.error = stats.error or f"OpenCode dashboard returned HTTP {exc.code}"
        return stats
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        stats.error = stats.error or f"OpenCode dashboard error: {exc}"
        return stats

    text = _strip_tags(raw)

    if _is_login_page(text):
        stats.cookie_expired = True
        stats.error = stats.error or "OpenCode dashboard cookie expired"
        return stats

    scraped = []
    specs = [
        ("5 hour", 12, "$12 of usage every 5 hours", ("Rolling usage", "5 hour", "5-hour", "Uso Contínuo", "Uso Continuo")),
        ("Weekly", 30, "$30 of usage per week", ("Weekly usage", "Weekly", "Uso Semanal")),
        ("Monthly", 60, "$60 of usage per month", ("Monthly usage", "Monthly", "Uso Mensal")),
    ]
    found = False
    for name, amount, description, labels in specs:
        percent, reset = _parse_window(text, labels)
        if percent is not None:
            found = True
        scraped.append(GoLimitWindow(name, amount, description, percent, reset))

    if found:
        stats.limits = tuple(scraped)
        stats.usage_available = True
    return stats
