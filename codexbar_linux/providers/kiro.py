"""Kiro provider — uses kiro-cli SSO token + CodeWhisperer GetUsageLimits.

Primary path (preferred):
  1. Read ~/.aws/sso/cache/kiro-auth-token.json (written by `kiro-cli login`)
  2. Refresh access token via AWS SSO OIDC when expired
  3. POST AmazonCodeWhispererService.GetUsageLimits

Fallback: legacy browser headers in kiro_headers.json (web portal RPC).
"""

from __future__ import annotations

import gzip
import json
import math
import logging
import os
import re
import struct
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.models import ProviderUsage, UsageWindow
from ..core.resilience import RetryConfig, retry

logger = logging.getLogger("codexbar-linux")

SSO_CACHE = Path.home() / ".aws" / "sso" / "cache"
KIRO_TOKEN_PATH = SSO_CACHE / "kiro-auth-token.json"
CODEWHISPERER_HOST = "https://codewhisperer.{region}.amazonaws.com/"
OIDC_TOKEN_URL = "https://oidc.{region}.amazonaws.com/token"

# Legacy web portal path
KIRO_RPC_URL = "https://app.kiro.dev/service/KiroWebPortalService/operation/GetUserUsageAndLimits"
KIRO_ORIGIN = "https://app.kiro.dev"
KIRO_REFERER = "https://app.kiro.dev/settings/account"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"


class _CborDecodeError(ValueError):
    pass


# ── CLI token path ──────────────────────────────────────────────


def _load_kiro_token() -> Optional[dict[str, Any]]:
    try:
        data = json.loads(KIRO_TOKEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_oidc_client(client_id_hash: str) -> Optional[dict[str, Any]]:
    path = SSO_CACHE / f"{client_id_hash}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("clientId") or not data.get("clientSecret"):
        return None
    return data


def _token_expired(token: dict[str, Any], skew_seconds: int = 120) -> bool:
    raw = token.get("expiresAt")
    if not isinstance(raw, str) or not raw:
        return True
    try:
        exp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= datetime.now(timezone.utc) + timedelta(seconds=skew_seconds)


def _persist_token(token: dict[str, Any]) -> None:
    try:
        KIRO_TOKEN_PATH.write_text(json.dumps(token))
        # Keep group-readable if CLI left it that way; never world-writable.
        mode = KIRO_TOKEN_PATH.stat().st_mode
        if mode & 0o002:
            KIRO_TOKEN_PATH.chmod(0o600)
    except OSError as exc:
        logger.warning("Kiro: could not persist refreshed token: %s", exc)


def _refresh_access_token(token: dict[str, Any]) -> Optional[str]:
    client_hash = token.get("clientIdHash")
    refresh = token.get("refreshToken")
    region = str(token.get("region") or "us-east-1")
    if not isinstance(client_hash, str) or not isinstance(refresh, str):
        return None
    client = _load_oidc_client(client_hash)
    if not client:
        return None

    body = json.dumps(
        {
            "clientId": client["clientId"],
            "clientSecret": client["clientSecret"],
            "grantType": "refresh_token",
            "refreshToken": refresh,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OIDC_TOKEN_URL.format(region=region),
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "codexbar-linux/0.3",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Kiro token refresh failed: %s", exc)
        return None

    access = payload.get("accessToken") or payload.get("access_token")
    if not isinstance(access, str) or not access:
        return None

    token["accessToken"] = access
    new_refresh = payload.get("refreshToken") or payload.get("refresh_token")
    if isinstance(new_refresh, str) and new_refresh:
        token["refreshToken"] = new_refresh
    expires_in = payload.get("expiresIn") or payload.get("expires_in") or 3600
    try:
        expires_in_i = int(expires_in)
    except (TypeError, ValueError):
        expires_in_i = 3600
    exp = datetime.now(timezone.utc) + timedelta(seconds=expires_in_i)
    token["expiresAt"] = exp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    _persist_token(token)
    logger.info("Kiro: refreshed SSO access token")
    return access


def _get_access_token() -> Optional[tuple[str, dict[str, Any]]]:
    token = _load_kiro_token()
    if not token:
        return None
    access = token.get("accessToken")
    if isinstance(access, str) and access and not _token_expired(token):
        return access, token
    refreshed = _refresh_access_token(token)
    if refreshed:
        return refreshed, token
    # Last resort: try existing token even if parse of expiry failed
    if isinstance(access, str) and access:
        return access, token
    return None


def _guess_profile_arn(token: dict[str, Any]) -> Optional[str]:
    """Best-effort profile ARN for GetUsageLimits (optional)."""
    env = os.environ.get("KIRO_PROFILE_ARN", "").strip()
    if env:
        return env
    # whoami is fast when logged in
    try:
        import shutil
        import subprocess

        if not shutil.which("kiro-cli"):
            return None
        result = subprocess.run(
            ["kiro-cli", "whoami"],
            capture_output=True,
            text=True,
            timeout=8,
            env={**os.environ, "TERM": "dumb"},
        )
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"(arn:aws:codewhisperer:[^\s]+)", text)
        if match:
            return match.group(1)
    except Exception as exc:
        logger.debug("Kiro whoami profile lookup failed: %s", exc)
    return None


def _reset_from_epoch(value: Any) -> tuple[str, Optional[datetime]]:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return "", None
    # AWS sometimes returns seconds as float
    if ts > 1e12:
        ts = ts / 1000.0
    resets_at = datetime.fromtimestamp(ts, tz=timezone.utc)
    delta = resets_at - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "resetting…", resets_at
    mins = secs // 60
    if mins < 60:
        return f"resets in {mins}m", resets_at
    if mins < 1440:
        return f"resets in {mins // 60}h", resets_at
    return f"resets in {mins // 1440}d", resets_at


def _parse_usage_limits(data: dict[str, Any]) -> ProviderUsage:
    sub = data.get("subscriptionInfo") if isinstance(data.get("subscriptionInfo"), dict) else {}
    plan = sub.get("subscriptionTitle") or sub.get("type") or "Kiro"
    plan_s = str(plan)

    breakdown = data.get("usageBreakdownList") or []
    primary = secondary = tertiary = None
    credits_remaining: Optional[int] = None

    if isinstance(breakdown, list):
        for item in breakdown:
            if not isinstance(item, dict):
                continue
            if str(item.get("resourceType") or "").upper() != "CREDIT" and item.get("displayName") != "Credit":
                continue

            limit = item.get("usageLimitWithPrecision")
            if limit is None:
                limit = item.get("usageLimit")
            used = item.get("currentUsageWithPrecision")
            if used is None:
                used = item.get("currentUsage")
            overages = item.get("currentOveragesWithPrecision")
            if overages is None:
                overages = item.get("currentOverages")

            try:
                if limit is None or used is None or isinstance(limit, bool) or isinstance(used, bool):
                    continue
                limit_f = float(limit)
                used_f = float(used)
                if not math.isfinite(limit_f) or not math.isfinite(used_f) or limit_f <= 0 or used_f < 0:
                    continue
                over_f = float(overages) if overages is not None else max(0.0, used_f - limit_f)
                if isinstance(overages, bool) or not math.isfinite(over_f) or over_f < 0:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue

            plan_used = min(used_f, limit_f) if limit_f > 0 else used_f
            plan_pct = int(round((plan_used / limit_f) * 100)) if limit_f > 0 else 0
            plan_pct = max(0, min(100, plan_pct))

            reset_desc, resets_at = _reset_from_epoch(
                item.get("nextDateReset") or data.get("nextDateReset")
            )
            reset_label = f"{plan_used:g}/{limit_f:g} credits"
            if reset_desc:
                reset_label = f"{reset_label} · {reset_desc}"

            primary = UsageWindow(
                used_percent=plan_pct,
                reset_description=reset_label,
                resets_at=resets_at,
            )
            credits_remaining = int(max(0, limit_f - plan_used))

            over_cap = item.get("overageCapWithPrecision")
            if over_cap is None:
                over_cap = item.get("overageCap")
            try:
                over_cap_f = float(over_cap) if over_cap is not None else 0.0
            except (TypeError, ValueError):
                over_cap_f = 0.0
            if not isinstance(over_cap, bool) and math.isfinite(over_cap_f) and over_cap_f > 0:
                over_pct = max(0, min(100, int(round((over_f / over_cap_f) * 100))))
                charges = item.get("overageCharges")
                charge_s = ""
                if isinstance(charges, (int, float)):
                    charge_s = f" · ${float(charges):.2f}"
                secondary = UsageWindow(
                    used_percent=over_pct,
                    reset_description=f"overage {over_f:g}/{over_cap_f:g}{charge_s}",
                    resets_at=resets_at,
                )
            break

    error = None if primary else "Kiro usage unavailable: no valid credit meter"

    return ProviderUsage(
        provider="kiro",
        source="kiro-cli",
        login_method=plan_s,
        primary=primary,
        secondary=secondary,
        tertiary=tertiary,
        credits_remaining=credits_remaining,
        updated_at=datetime.now(timezone.utc),
        error=error,
    )


def _fetch_via_cli_token() -> Optional[ProviderUsage]:
    got = _get_access_token()
    if not got:
        return None
    access, token = got
    region = str(token.get("region") or "us-east-1")
    profile = _guess_profile_arn(token)

    payload: dict[str, Any] = {"origin": "CLI"}
    if profile:
        payload["profileArn"] = profile
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CODEWHISPERER_HOST.format(region=region),
        data=body,
        headers={
            "Content-Type": "application/x-amz-json-1.0",
            "x-amz-target": "AmazonCodeWhispererService.GetUsageLimits",
            "Authorization": f"Bearer {access}",
            "User-Agent": "codexbar-linux/0.3",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")[:200]
        if exc.code in (401, 403):
            # One forced refresh then retry once
            refreshed = _refresh_access_token(token)
            if refreshed and refreshed != access:
                return _fetch_via_cli_token()
            return ProviderUsage(
                provider="kiro",
                source="kiro-cli",
                error="Kiro CLI session expired. Run: kiro-cli login",
            )
        logger.warning("Kiro GetUsageLimits HTTP %s: %s", exc.code, text)
        return ProviderUsage(
            provider="kiro",
            source="kiro-cli",
            error=f"Kiro API HTTP {exc.code}",
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("Kiro GetUsageLimits error: %s", exc)
        return None

    if not isinstance(data, dict):
        return None
    usage = _parse_usage_limits(data)
    logger.info(
        "Kiro CLI: plan=%s credits=%s%%",
        usage.login_method,
        usage.primary.used_percent if usage.primary else "-",
    )
    return usage


# ── Legacy web headers path ─────────────────────────────────────


def _load_kiro_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    env_map = {
        "authorization": "KIRO_AUTHORIZATION",
        "access_token": "KIRO_ACCESS_TOKEN",
        "x-csrf-token": "KIRO_CSRF_TOKEN",
        "x-kiro-userid": "KIRO_USER_ID",
        "x-kiro-visitorid": "KIRO_VISITOR_ID",
        "cookie": "KIRO_COOKIE",
        "profileArn": "KIRO_PROFILE_ARN",
    }
    for key, env_name in env_map.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            cfg[key] = value

    if cfg:
        return _normalise_config(cfg)

    config_path = Path.home() / ".local/share/codexbar-linux/kiro_headers.json"
    try:
        mode = config_path.stat().st_mode
        if mode & 0o077:
            logger.warning("Kiro: credential file %s has unsafe permissions", config_path)
            return {}
        raw = json.loads(config_path.read_text())
    except OSError:
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("Kiro: invalid JSON in credential file: %s", exc)
        return {}

    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("headers"), dict):
        merged = {str(k): str(v) for k, v in raw["headers"].items()}
        for key in ("profileArn", "profile_arn"):
            if raw.get(key):
                merged[key] = str(raw[key])
        return _normalise_config(merged)
    return _normalise_config({str(k): str(v) for k, v in raw.items()})


def _normalise_config(raw: dict[str, str]) -> dict[str, str]:
    cfg: dict[str, str] = {}
    for key, value in raw.items():
        clean = value.strip()
        if not clean or "\r" in clean or "\n" in clean:
            continue
        lower = key.lower()
        if lower in ("authorization", "access_token", "accesstoken"):
            if lower != "authorization" and not clean.lower().startswith("bearer "):
                clean = f"Bearer {clean}"
            cfg["authorization"] = clean
        elif lower in ("x-csrf-token", "csrf", "csrf_token", "xcsrftoken"):
            cfg["x-csrf-token"] = clean
        elif lower in ("x-kiro-userid", "user_id", "userid"):
            cfg["x-kiro-userid"] = clean
        elif lower in ("x-kiro-visitorid", "visitor_id", "visitorid"):
            cfg["x-kiro-visitorid"] = clean
        elif lower == "cookie":
            cfg["cookie"] = clean
        elif lower in ("profilearn", "profile_arn"):
            cfg["profileArn"] = clean

    if "profileArn" not in cfg and cfg.get("cookie"):
        profile_arn = _extract_cookie_value(cfg["cookie"], "ProfileArn")
        if profile_arn:
            cfg["profileArn"] = urllib.parse.unquote(profile_arn)
    return cfg


def _extract_cookie_value(cookie_str: str, name: str) -> Optional[str]:
    match = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]+)", cookie_str)
    return match.group(1) if match else None


def _missing_config_error(cfg: dict[str, str]) -> Optional[str]:
    required = ("authorization", "x-csrf-token", "x-kiro-userid", "x-kiro-visitorid", "cookie", "profileArn")
    missing = [key for key in required if not cfg.get(key)]
    if not missing:
        return None
    return "Kiro web headers incomplete: " + ", ".join(missing)


def _cbor_head(major: int, length: int) -> bytes:
    if length < 24:
        return bytes([(major << 5) | length])
    if length <= 0xFF:
        return bytes([(major << 5) | 24, length])
    if length <= 0xFFFF:
        return bytes([(major << 5) | 25]) + length.to_bytes(2, "big")
    if length <= 0xFFFFFFFF:
        return bytes([(major << 5) | 26]) + length.to_bytes(4, "big")
    return bytes([(major << 5) | 27]) + length.to_bytes(8, "big")


def _cbor_encode(value: Any) -> bytes:
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        if value >= 0:
            return _cbor_head(0, value)
        return _cbor_head(1, -1 - value)
    if isinstance(value, bytes):
        return _cbor_head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _cbor_head(3, len(raw)) + raw
    if isinstance(value, (list, tuple)):
        return _cbor_head(4, len(value)) + b"".join(_cbor_encode(item) for item in value)
    if isinstance(value, dict):
        return _cbor_head(5, len(value)) + b"".join(
            _cbor_encode(str(key)) + _cbor_encode(item) for key, item in value.items()
        )
    raise TypeError(f"Unsupported CBOR value: {type(value).__name__}")


class _CborDecoder:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def decode(self) -> Any:
        return self._read_value()

    def _read(self, length: int) -> bytes:
        end = self.pos + length
        if end > len(self.data):
            raise _CborDecodeError("Unexpected end of CBOR data")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def _read_length(self, addl: int) -> int:
        if addl < 24:
            return addl
        if addl == 24:
            return self._read(1)[0]
        if addl == 25:
            return int.from_bytes(self._read(2), "big")
        if addl == 26:
            return int.from_bytes(self._read(4), "big")
        if addl == 27:
            return int.from_bytes(self._read(8), "big")
        raise _CborDecodeError("Indefinite CBOR values are not supported")

    def _peek_is_break(self) -> bool:
        return self.pos < len(self.data) and self.data[self.pos] == 0xFF

    def _read_break(self) -> None:
        if not self._peek_is_break():
            raise _CborDecodeError("Expected CBOR break marker")
        self.pos += 1

    def _read_indefinite_bytes(self) -> bytes:
        chunks: list[bytes] = []
        while not self._peek_is_break():
            chunk = self._read_value()
            if not isinstance(chunk, bytes):
                raise _CborDecodeError("Invalid chunk in indefinite byte string")
            chunks.append(chunk)
        self._read_break()
        return b"".join(chunks)

    def _read_indefinite_text(self) -> str:
        chunks: list[str] = []
        while not self._peek_is_break():
            chunk = self._read_value()
            if not isinstance(chunk, str):
                raise _CborDecodeError("Invalid chunk in indefinite text string")
            chunks.append(chunk)
        self._read_break()
        return "".join(chunks)

    def _read_indefinite_array(self) -> list[Any]:
        items: list[Any] = []
        while not self._peek_is_break():
            items.append(self._read_value())
        self._read_break()
        return items

    def _read_indefinite_map(self) -> dict[Any, Any]:
        items: dict[Any, Any] = {}
        while not self._peek_is_break():
            key = self._read_value()
            if self._peek_is_break():
                raise _CborDecodeError("CBOR map ended after a key without a value")
            items[key] = self._read_value()
        self._read_break()
        return items

    def _read_value(self) -> Any:
        first = self._read(1)[0]
        major = first >> 5
        addl = first & 0x1F
        if major == 0:
            return self._read_length(addl)
        if major == 1:
            return -1 - self._read_length(addl)
        if major == 2:
            if addl == 31:
                return self._read_indefinite_bytes()
            return self._read(self._read_length(addl))
        if major == 3:
            if addl == 31:
                return self._read_indefinite_text()
            return self._read(self._read_length(addl)).decode("utf-8", "replace")
        if major == 4:
            if addl == 31:
                return self._read_indefinite_array()
            return [self._read_value() for _ in range(self._read_length(addl))]
        if major == 5:
            if addl == 31:
                return self._read_indefinite_map()
            return {self._read_value(): self._read_value() for _ in range(self._read_length(addl))}
        if major == 6:
            self._read_length(addl)
            return self._read_value()
        if major == 7:
            if addl == 31:
                raise _CborDecodeError("Unexpected CBOR break marker")
            if addl == 20:
                return False
            if addl == 21:
                return True
            if addl in (22, 23):
                return None
            if addl == 25:
                return None
            if addl == 26:
                return struct.unpack(">f", self._read(4))[0]
            if addl == 27:
                return struct.unpack(">d", self._read(8))[0]
        raise _CborDecodeError(f"Unsupported CBOR major={major} addl={addl}")


def _cbor_decode(data: bytes) -> Any:
    return _CborDecoder(data).decode()


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("value", "amount", "count", "total", "limit", "usage", "used", "remaining"):
            for item_key, item_value in value.items():
                if isinstance(item_key, str) and item_key.lower() == key:
                    number = _as_number(item_value)
                    if number is not None:
                        return number
    if isinstance(value, list) and len(value) == 1:
        return _as_number(value[0])
    return None


def _find_value(obj: Any, names: tuple[str, ...]) -> Any:
    wanted = {name.lower() for name in names}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in wanted:
                return value
        for value in obj.values():
            found = _find_value(value, names)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_value(item, names)
            if found is not None:
                return found
    return None


def _first_number_by_key(
    mapping: dict[str, Any],
    needles: tuple[str, ...],
    excludes: tuple[str, ...] = (),
) -> Optional[float]:
    for key, value in mapping.items():
        if any(exclude in key for exclude in excludes):
            continue
        if any(needle in key for needle in needles):
            number = _as_number(value)
            if number is not None:
                return number
    return None


def _label_from_mapping(mapping: dict[str, Any]) -> str:
    for needle in ("name", "type", "dimension", "feature", "period"):
        for key, value in mapping.items():
            if needle in key and isinstance(value, str) and value:
                return value
    return "Usage"


def _window_from_mapping(mapping: dict[Any, Any]) -> Optional[UsageWindow]:
    lowered = {str(key).lower(): value for key, value in mapping.items() if isinstance(key, str)}
    limit = _first_number_by_key(lowered, ("limit", "quota", "total", "maximum", "max"))
    used = _first_number_by_key(
        lowered,
        ("used", "usage", "consumed", "current"),
        excludes=("limit", "quota", "total", "maximum", "max"),
    )
    remaining = _first_number_by_key(lowered, ("remaining", "available", "left", "balance"))
    label = _label_from_mapping(lowered)
    is_credit = any(word in label.lower() for word in ("credit", "balance", "token", "coin"))

    if is_credit and remaining is None and used is not None:
        remaining = used
        used = None
    if limit is not None and used is None and remaining is not None:
        used = max(0.0, limit - remaining)
    if limit is None or limit <= 0 or used is None:
        return None

    pct = max(0, min(100, int((used / limit) * 100)))
    reset = ""
    for key in ("resetdescription", "reset", "resetat", "resetsat", "period"):
        if lowered.get(key):
            reset = str(lowered[key])
            break
    display_num = (limit - used) if is_credit else used
    return UsageWindow(
        used_percent=pct,
        reset_description=f"{label}: {display_num:g}/{limit:g}" + (f" | {reset}" if reset else ""),
    )


def _collect_windows(obj: Any, windows: list[UsageWindow]) -> None:
    if len(windows) >= 3:
        return
    if isinstance(obj, dict):
        window = _window_from_mapping(obj)
        if window and all(window.reset_description != existing.reset_description for existing in windows):
            windows.append(window)
        for value in obj.values():
            _collect_windows(value, windows)
    elif isinstance(obj, list):
        for item in obj:
            _collect_windows(item, windows)


def _parse_usage_web(data: Any) -> ProviderUsage:
    windows: list[UsageWindow] = []
    _collect_windows(data, windows)
    if not windows:
        return ProviderUsage(
            provider="kiro",
            source="kiro-web-api",
            error="Kiro web API: unsupported usage structure",
        )
    remaining = _as_number(_find_value(data, ("remaining", "available", "left")))
    email = _find_value(data, ("accountEmail", "email"))
    plan = _find_value(data, ("plan", "tier", "subscription"))
    return ProviderUsage(
        provider="kiro",
        source="kiro-web-api",
        account_email=email if isinstance(email, str) else None,
        login_method=plan if isinstance(plan, str) else None,
        primary=windows[0] if len(windows) > 0 else None,
        secondary=windows[1] if len(windows) > 1 else None,
        tertiary=windows[2] if len(windows) > 2 else None,
        credits_remaining=int(remaining) if remaining is not None else None,
        updated_at=datetime.now(timezone.utc),
    )


def _fetch_via_web_headers() -> Optional[ProviderUsage]:
    cfg = _load_kiro_config()
    error = _missing_config_error(cfg)
    if error:
        return None

    body = _cbor_encode(
        {
            "origin": "KIRO_IDE",
            "isEmailRequired": True,
            "profileArn": cfg["profileArn"],
        }
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/cbor",
        "Content-Type": "application/cbor",
        "smithy-protocol": "rpc-v2-cbor",
        "Authorization": cfg["authorization"],
        "x-csrf-token": cfg["x-csrf-token"],
        "x-kiro-userid": cfg["x-kiro-userid"],
        "x-kiro-visitorid": cfg["x-kiro-visitorid"],
        "Origin": KIRO_ORIGIN,
        "Referer": KIRO_REFERER,
        "Cookie": cfg["cookie"],
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "amz-sdk-request": "attempt=1; max=1",
    }
    req = urllib.request.Request(KIRO_RPC_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
            if response.getheader("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return ProviderUsage(
                provider="kiro",
                source="kiro-web-api",
                error="Kiro web session expired (prefer kiro-cli login)",
            )
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    try:
        data = _cbor_decode(raw)
    except _CborDecodeError:
        return None
    return _parse_usage_web(data)


@retry(RetryConfig(max_tries=2, backoff=1.0))
def fetch_kiro_usage() -> list[ProviderUsage]:
    """Fetch Kiro usage: CLI SSO token first, then web headers fallback."""
    cli_usage = _fetch_via_cli_token()
    if cli_usage is not None:
        return [cli_usage]

    web_usage = _fetch_via_web_headers()
    if web_usage is not None:
        return [web_usage]

    return [
        ProviderUsage(
            provider="kiro",
            source="kiro-cli",
            error="Kiro not logged in. Run: kiro-cli login",
        )
    ]
