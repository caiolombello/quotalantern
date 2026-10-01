"""Generic provider that delegates to the official codexbar CLI."""

import json
import logging
import shutil
import subprocess
import time
from typing import Optional

from ..core.models import ProviderUsage, UsageWindow
from ..path_env import enriched_env

logger = logging.getLogger("codexbar-linux")


def _parse_window(data: dict) -> Optional[UsageWindow]:
    if not data:
        return None
    resets_at = None
    raw_reset = data.get("resetsAt")
    if raw_reset:
        try:
            from datetime import datetime
            resets_at = datetime.fromisoformat(raw_reset.replace("Z", "+00:00"))
        except ValueError:
            pass
    return UsageWindow(
        used_percent=int(data.get("usedPercent", 0)),
        window_minutes=data.get("windowMinutes"),
        reset_description=data.get("resetDescription", ""),
        resets_at=resets_at,
    )


def _parse_item(item: dict) -> ProviderUsage:
    error = None
    err_data = item.get("error")
    if err_data:
        error = err_data.get("message", str(err_data))

    usage_data = item.get("usage") or {}
    identity = usage_data.get("identity") or {}
    updated_at = None
    raw_updated = usage_data.get("updatedAt")
    if raw_updated:
        try:
            from datetime import datetime
            updated_at = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
        except ValueError:
            pass

    return ProviderUsage(
        provider=item.get("provider", "unknown"),
        source=item.get("source", ""),
        version=item.get("version"),
        account_email=identity.get("accountEmail") or usage_data.get("accountEmail"),
        login_method=identity.get("loginMethod") or usage_data.get("loginMethod"),
        primary=_parse_window(usage_data.get("primary")),
        secondary=_parse_window(usage_data.get("secondary")),
        tertiary=_parse_window(usage_data.get("tertiary")),
        credits_remaining=usage_data.get("credits", {}).get("remaining")
        if isinstance(usage_data.get("credits"), dict)
        else None,
        updated_at=updated_at,
        error=error,
    )


def fetch_via_codexbar_cli(
    provider: str,
    force_cli: bool = False,
    cli_timeout: float = 30.0,
) -> list[ProviderUsage]:
    """Run the official codexbar CLI for a provider and parse JSON output."""
    cmd = ["codexbar", "usage", "--provider", provider, "--format", "json"]
    if force_cli:
        cmd += ["--source", "cli"]

    # Check if codexbar CLI exists before running
    codexbar_path = shutil.which("codexbar")
    if not codexbar_path:
        logger.error("codexbar CLI not found in PATH for provider %s", provider)
        raise RuntimeError("codexbar CLI not found in PATH")

    logger.debug("Running codexbar CLI for %s: %s", provider, " ".join(cmd))

    env = enriched_env()

    start_time = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=cli_timeout,
            env=env,
        )
    except FileNotFoundError:
        logger.error("codexbar CLI not found when executing for %s", provider)
        raise RuntimeError("codexbar CLI not found in PATH")
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start_time
        logger.error("codexbar CLI timed out after %.1fs for provider %s", elapsed, provider)
        raise RuntimeError("codexbar CLI timed out")

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    logger.debug(
        "codexbar CLI for %s returned code=%d stdout=%dB stderr=%dB",
        provider,
        result.returncode,
        len(stdout),
        len(stderr),
    )

    if result.returncode != 0:
        logger.warning(
            "codexbar CLI for %s exited with code %d. stdout: %r stderr: %r",
            provider,
            result.returncode,
            stdout[:500],
            stderr[:500],
        )

    json_start = stdout.find("[")
    json_end = stdout.rfind("]")
    if json_start == -1 or json_end == -1 or json_end <= json_start:
        output = stdout or stderr
        logger.error(
            "codexbar CLI for %s returned no JSON. Output: %s",
            provider,
            output[:300],
        )
        return [
            ProviderUsage(
                provider=provider,
                source="",
                error=f"No JSON in output: {output[:200]}",
            )
        ]

    raw = stdout[json_start : json_end + 1]
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "codexbar CLI for %s returned invalid JSON: %s. Raw: %s",
            provider,
            exc,
            raw[:300],
        )
        return [
            ProviderUsage(
                provider=provider,
                source="",
                error=f"JSON parse error: {exc}",
            )
        ]

    logger.debug("codexbar CLI for %s parsed %d items", provider, len(items))
    return [_parse_item(item) for item in items]
