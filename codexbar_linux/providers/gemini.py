"""Gemini provider — native Cloud Code quota via Gemini CLI OAuth.

Reads `~/.gemini/oauth_creds.json`, refreshes tokens when needed, and calls
`cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`.
"""

from __future__ import annotations

import ast
import base64
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.models import ProviderUsage, UsageWindow
from ..core.resilience import RetryConfig, retry
from ..path_env import enriched_env, resolve_agy

logger = logging.getLogger("codexbar-linux")

CREDENTIALS_PATH = Path.home() / ".gemini" / "oauth_creds.json"
QUOTA_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Public OAuth client embedded in the open-source Gemini CLI package.
OAUTH_CLIENT_ID = (
    "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)
OAUTH_CLIENT_SECRET = ""  # No third-party client secret is distributed.


def _b64url_json(segment: str) -> dict[str, Any]:
    padded = segment + "=" * ((4 - len(segment) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def _email_from_id_token(id_token: str) -> Optional[str]:
    try:
        claims = _b64url_json(id_token.split(".")[1])
    except (IndexError, ValueError, json.JSONDecodeError, OSError):
        return None
    email = claims.get("email")
    return str(email) if email else None


def _load_creds() -> Optional[dict[str, Any]]:
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_creds(data: dict[str, Any]) -> None:
    try:
        CREDENTIALS_PATH.write_text(json.dumps(data, indent=2) + "\n")
        CREDENTIALS_PATH.chmod(0o600)
    except OSError as exc:
        logger.warning("Gemini: could not persist refreshed token: %s", exc)


def _token_expired(creds: dict[str, Any], skew_ms: int = 60_000) -> bool:
    expiry = creds.get("expiry_date")
    if not isinstance(expiry, (int, float)):
        return True
    return float(expiry) <= (time.time() * 1000) + skew_ms


def _oauth_client_secret(creds: dict[str, Any]) -> Optional[str]:
    """Use explicit metadata or the existing legacy client constant, never copy it."""
    explicit = os.environ.get("GEMINI_OAUTH_CLIENT_SECRET") or creds.get("client_secret")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    # Migration compatibility: old code remains in place. Parse literals only;
    # do not execute/import legacy code or scan provider credential directories.
    legacy = Path.home() / ".local/share/codexbar-linux/codexbar_linux/providers/gemini.py"
    try:
        if legacy.is_symlink() or legacy.stat().st_uid != os.getuid():
            return None
        tree = ast.parse(legacy.read_text())
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"OAUTH_CLIENT_ID", "OAUTH_CLIENT_SECRET"}:
                        values[target.id] = node.value.value
        value = values.get("OAUTH_CLIENT_SECRET")
        if values.get("OAUTH_CLIENT_ID") == OAUTH_CLIENT_ID and isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, SyntaxError, UnicodeError):
        pass
    return None


def _refresh_access_token(creds: dict[str, Any]) -> Optional[str]:
    refresh = creds.get("refresh_token")
    if not isinstance(refresh, str) or not refresh.strip():
        return None

    client_secret = _oauth_client_secret(creds)
    if not client_secret:
        return None

    body = urllib.parse.urlencode(
        {
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": client_secret,
            "refresh_token": refresh.strip(),
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Gemini token refresh failed: %s", exc)
        return None

    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        return None

    creds["access_token"] = access
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        creds["expiry_date"] = time.time() * 1000 + float(expires_in) * 1000
    if isinstance(payload.get("id_token"), str):
        creds["id_token"] = payload["id_token"]
    _save_creds(creds)
    return access


def _get_access_token(creds: dict[str, Any]) -> Optional[str]:
    token = creds.get("access_token")
    if isinstance(token, str) and token and not _token_expired(creds):
        return token
    return _refresh_access_token(creds)


def _parse_reset(iso_string: Optional[str]) -> tuple[str, Optional[datetime]]:
    if not iso_string or not isinstance(iso_string, str):
        return "", None
    raw = iso_string.replace("Z", "+00:00")
    try:
        resets_at = datetime.fromisoformat(raw)
    except ValueError:
        return iso_string, None
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)
    delta = resets_at - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "resetting…", resets_at
    mins = secs // 60
    if mins < 60:
        return f"resets in {mins}m", resets_at
    if mins < 1440:
        hrs = mins // 60
        rem_mins = mins % 60
        return f"resets in {hrs}h" if rem_mins == 0 else f"resets in {hrs}h {rem_mins}m", resets_at
    days = mins // 1440
    rem_hrs = (mins % 1440) // 60
    return f"resets in {days}d" if rem_hrs == 0 else f"resets in {days}d {rem_hrs}h", resets_at


def _is_flash_lite(model_id: str) -> bool:
    return "flash-lite" in model_id.lower()


def _is_flash(model_id: str) -> bool:
    mid = model_id.lower()
    return "flash" in mid and not _is_flash_lite(mid)


def _is_pro(model_id: str) -> bool:
    return "pro" in model_id.lower()


def _window_from_quota(model_id: str, remaining_fraction: float, reset_raw: Optional[str]) -> UsageWindow:
    used = max(0, min(100, int(round((1.0 - remaining_fraction) * 100))))
    reset_desc, resets_at = _parse_reset(reset_raw)
    label = model_id.split("/")[-1] if model_id else "model"
    if reset_desc:
        reset_desc = f"{label}: {reset_desc}"
    else:
        reset_desc = label
    return UsageWindow(
        used_percent=used,
        window_minutes=1440,
        reset_description=reset_desc,
        resets_at=resets_at,
    )


def _pick_worst(windows: list[tuple[str, UsageWindow]]) -> Optional[UsageWindow]:
    if not windows:
        return None
    return max(windows, key=lambda item: item[1].used_percent)[1]


def _fetch_legacy_oauth_usage() -> list[ProviderUsage]:
    creds = _load_creds()
    if not creds:
        return [
            ProviderUsage(
                provider="gemini",
                source="oauth",
                error="Gemini OAuth missing. Run: gemini (login) or check ~/.gemini/oauth_creds.json",
            )
        ]

    token = _get_access_token(creds)
    if not token:
        return [
            ProviderUsage(
                provider="gemini",
                source="oauth",
                error="Gemini OAuth expired and refresh failed. Re-login with gemini CLI",
            )
        ]

    body = b"{}"
    req = urllib.request.Request(
        QUOTA_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "codexbar-linux/0.3",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")[:200]
        if exc.code in (401, 403):
            # Force one refresh retry path is already done; surface auth error.
            return [
                ProviderUsage(
                    provider="gemini",
                    source="oauth",
                    error=f"Gemini quota unauthorized (HTTP {exc.code}). Re-login with gemini CLI",
                )
            ]
        return [
            ProviderUsage(
                provider="gemini",
                source="oauth",
                error=f"Gemini quota HTTP {exc.code}: {text}",
            )
        ]
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return [
            ProviderUsage(
                provider="gemini",
                source="oauth",
                error=f"Gemini quota error: {exc}",
            )
        ]

    buckets = payload.get("buckets") if isinstance(payload, dict) else None
    if not isinstance(buckets, list) or not buckets:
        return [
            ProviderUsage(
                provider="gemini",
                source="oauth",
                error="Gemini quota: no buckets in response",
            )
        ]

    pro: list[tuple[str, UsageWindow]] = []
    flash: list[tuple[str, UsageWindow]] = []
    flash_lite: list[tuple[str, UsageWindow]] = []
    other: list[tuple[str, UsageWindow]] = []

    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        model_id = str(bucket.get("modelId") or bucket.get("model_id") or "")
        remaining = bucket.get("remainingFraction")
        if remaining is None:
            remaining = bucket.get("remaining_fraction")
        if not model_id or remaining is None:
            continue
        try:
            frac = float(remaining)
        except (TypeError, ValueError):
            continue
        reset_raw = bucket.get("resetTime") or bucket.get("reset_time")
        window = _window_from_quota(model_id, frac, reset_raw if isinstance(reset_raw, str) else None)
        if _is_pro(model_id):
            pro.append((model_id, window))
        elif _is_flash_lite(model_id):
            flash_lite.append((model_id, window))
        elif _is_flash(model_id):
            flash.append((model_id, window))
        else:
            other.append((model_id, window))

    primary = _pick_worst(pro) or _pick_worst(other)
    secondary = _pick_worst(flash)
    tertiary = _pick_worst(flash_lite)

    if not primary and not secondary and not tertiary:
        return [
            ProviderUsage(
                provider="gemini",
                source="oauth",
                error="Gemini quota: could not map model buckets",
            )
        ]

    email = None
    id_token = creds.get("id_token")
    if isinstance(id_token, str):
        email = _email_from_id_token(id_token)

    logger.info(
        "Gemini OAuth: pro=%s flash=%s",
        primary.used_percent if primary else "-",
        secondary.used_percent if secondary else "-",
    )
    return [
        ProviderUsage(
            provider="gemini",
            source="oauth",
            account_email=email,
            primary=primary,
            secondary=secondary,
            tertiary=tertiary,
            updated_at=datetime.now(timezone.utc),
        )
    ]


def _parse_agy_payload(payload: dict[str, Any]) -> Optional[ProviderUsage]:
    cmd_data = payload.get("command", {}).get("data", {})
    groups = cmd_data.get("groups") if isinstance(cmd_data, dict) else None
    if not isinstance(groups, list) or not groups:
        return None

    gemini_5h: Optional[UsageWindow] = None
    gemini_weekly: Optional[UsageWindow] = None
    claude_5h: Optional[UsageWindow] = None
    claude_weekly: Optional[UsageWindow] = None

    for group in groups:
        if not isinstance(group, dict):
            continue
        gname = str(group.get("name", "")).lower()
        buckets = group.get("buckets", [])
        if not isinstance(buckets, list):
            continue

        for b in buckets:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("id", "")).lower()
            bwin = str(b.get("window", "")).lower()
            rem = b.get("remaining_fraction")
            if rem is None:
                continue
            try:
                frac = float(rem)
            except (TypeError, ValueError):
                continue

            used = max(0, min(100, int(round((1.0 - frac) * 100))))
            reset_raw = b.get("reset_time")
            reset_desc, resets_at = _parse_reset(str(reset_raw) if reset_raw else None)

            if "gemini" in gname:
                if "5h" in bid or bwin == "5h":
                    gemini_5h = UsageWindow(
                        used_percent=used,
                        window_minutes=300,
                        reset_description=reset_desc,
                        resets_at=resets_at,
                    )
                elif "weekly" in bid or bwin == "weekly":
                    gemini_weekly = UsageWindow(
                        used_percent=used,
                        window_minutes=10080,
                        reset_description=reset_desc,
                        resets_at=resets_at,
                    )
            elif "claude" in gname or "gpt" in gname or "3p" in gname:
                desc = f"Claude/GPT: {reset_desc}" if reset_desc else "Claude/GPT"
                if "5h" in bid or bwin == "5h":
                    claude_5h = UsageWindow(
                        used_percent=used,
                        window_minutes=300,
                        reset_description=desc,
                        resets_at=resets_at,
                    )
                elif "weekly" in bid or bwin == "weekly":
                    claude_weekly = UsageWindow(
                        used_percent=used,
                        window_minutes=10080,
                        reset_description=desc,
                        resets_at=resets_at,
                    )

    if not gemini_5h and not gemini_weekly:
        return None

    # For tertiary, prioritize 5h or the window with higher usage
    tertiary = claude_5h
    if claude_weekly and (not tertiary or claude_weekly.used_percent > tertiary.used_percent):
        tertiary = claude_weekly

    return ProviderUsage(
        provider="gemini",
        source="agy",
        login_method="Antigravity CLI",
        primary=gemini_5h,
        secondary=gemini_weekly,
        tertiary=tertiary,
        updated_at=datetime.now(timezone.utc),
    )


def fetch_via_agy_cli(timeout_secs: float = 30.0) -> Optional[list[ProviderUsage]]:
    agy_bin = resolve_agy()
    if not agy_bin:
        return None

    cmd = [agy_bin, "-p", "/usage", "--output-format", "json"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
            env=enriched_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Antigravity CLI execution error: %s", exc)
        return None

    if result.returncode != 0:
        err_msg = result.stderr.strip() or result.stdout.strip()
        logger.warning("Antigravity CLI returned %d: %s", result.returncode, err_msg[:200])
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("Antigravity CLI output is not JSON: %s", exc)
        return None

    usage = _parse_agy_payload(payload)
    if not usage:
        return None
    return [usage]


@retry(RetryConfig(max_tries=2, backoff=1.0))
def fetch_gemini_usage() -> list[ProviderUsage]:
    # 1. Antigravity CLI (`agy`) is the modern primary source
    agy_results = fetch_via_agy_cli()
    if agy_results:
        return agy_results

    # 2. Legacy OAuth fallback if credentials file exists
    if CREDENTIALS_PATH.exists():
        return _fetch_legacy_oauth_usage()

    agy_bin = resolve_agy()
    if agy_bin:
        return [
            ProviderUsage(
                provider="gemini",
                source="agy",
                error="Antigravity CLI (agy) failed to report usage. Run: agy (check login)",
            )
        ]

    return [
        ProviderUsage(
            provider="gemini",
            source="agy",
            error="Antigravity CLI (agy) not found in PATH and ~/.gemini/oauth_creds.json missing.",
        )
    ]
