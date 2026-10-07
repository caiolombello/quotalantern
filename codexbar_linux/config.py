"""Local configuration for codexbar-linux."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("codexbar-linux")

CONFIG_DIR = Path.home() / ".config/codexbar-linux"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Subscription/plan providers ON by default; API cost providers OFF.
DEFAULT_ENABLED_PROVIDERS: list[str] = [
    "codex",
    "claude",
    "gemini",
    "grok",
    "kiro",
    "opencode-go",
    "zen",
    "cursor",
    "glm",
]

DEFAULT_OPEN_URLS: dict[str, str] = {
    "codex": "https://chatgpt.com/codex/settings/usage",
    "claude": "https://claude.ai/settings/usage",
    "claude-api": "https://console.anthropic.com/settings/usage",
    "gemini": "https://aistudio.google.com/",
    "gemini-api": "https://aistudio.google.com/app/apikey",
    "grok": "https://grok.com/?_s=usage",
    "kiro": "https://app.kiro.dev/settings/account",
    "openai-api": "https://platform.openai.com/settings/organization/billing/overview",
    "opencode-go": "https://opencode.ai/console/",
    "zen": "https://opencode.ai/",
    "cursor": "https://cursor.com/dashboard",
    "glm": "https://z.ai/",
}


@dataclass
class AppConfig:
    refresh_interval_seconds: int = 300
    threshold_info: int = 70
    threshold_warning: int = 85
    threshold_critical: int = 95
    reset_drop_percent: int = 50
    recovery_below_percent: int = 50
    enabled_providers: list[str] = field(default_factory=lambda: list(DEFAULT_ENABLED_PROVIDERS))
    glm_region: str = "global"  # global | bigmodel-cn
    # UI preferences
    hide_offline: bool = False
    label_mode: str = "bottleneck"  # bottleneck | recommend
    open_dashboard_on_start: bool = False
    # Third-party global reset announcements (codex-resets.com); opt-in.
    codex_resets_enabled: bool = False
    open_urls: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_OPEN_URLS))

    def is_enabled(self, provider_id: str) -> bool:
        return provider_id in self.enabled_providers


def _coerce_positive_int(value: Any, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return default


def _ensure_private_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG_DIR.chmod(0o700)


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        cfg = AppConfig()
        try:
            ensure_config_file(cfg)
        except OSError:
            logger.debug("Could not create default config at %s", CONFIG_PATH)
        return cfg

    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read config %s: %s", CONFIG_PATH, exc)
        return AppConfig()

    if not isinstance(data, dict):
        return AppConfig()

    cfg = AppConfig()
    cfg.refresh_interval_seconds = _coerce_positive_int(
        data.get("refresh_interval_seconds"), cfg.refresh_interval_seconds
    )
    cfg.threshold_info = _coerce_positive_int(data.get("threshold_info"), cfg.threshold_info)
    cfg.threshold_warning = _coerce_positive_int(
        data.get("threshold_warning"), cfg.threshold_warning
    )
    cfg.threshold_critical = _coerce_positive_int(
        data.get("threshold_critical"), cfg.threshold_critical
    )
    cfg.reset_drop_percent = _coerce_positive_int(
        data.get("reset_drop_percent"), cfg.reset_drop_percent
    )
    cfg.recovery_below_percent = _coerce_positive_int(
        data.get("recovery_below_percent"), cfg.recovery_below_percent
    )

    enabled = data.get("enabled_providers")
    if isinstance(enabled, list) and all(isinstance(x, str) for x in enabled):
        cfg.enabled_providers = list(dict.fromkeys(enabled))

    region = data.get("glm_region")
    if region in ("global", "bigmodel-cn"):
        cfg.glm_region = region

    if isinstance(data.get("hide_offline"), bool):
        cfg.hide_offline = data["hide_offline"]
    label_mode = data.get("label_mode")
    if label_mode in ("bottleneck", "recommend"):
        cfg.label_mode = label_mode
    if isinstance(data.get("open_dashboard_on_start"), bool):
        cfg.open_dashboard_on_start = data["open_dashboard_on_start"]
    if isinstance(data.get("codex_resets_enabled"), bool):
        cfg.codex_resets_enabled = data["codex_resets_enabled"]

    if isinstance(data.get("open_urls"), dict):
        for key, value in data["open_urls"].items():
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                cfg.open_urls[key] = value.strip()

    return cfg


def save_config(cfg: AppConfig) -> None:
    _ensure_private_config_dir()
    payload = {
        "refresh_interval_seconds": cfg.refresh_interval_seconds,
        "threshold_info": cfg.threshold_info,
        "threshold_warning": cfg.threshold_warning,
        "threshold_critical": cfg.threshold_critical,
        "reset_drop_percent": cfg.reset_drop_percent,
        "recovery_below_percent": cfg.recovery_below_percent,
        "enabled_providers": list(cfg.enabled_providers),
        "glm_region": cfg.glm_region,
        "hide_offline": cfg.hide_offline,
        "label_mode": cfg.label_mode,
        "open_dashboard_on_start": cfg.open_dashboard_on_start,
        "codex_resets_enabled": cfg.codex_resets_enabled,
        "open_urls": dict(cfg.open_urls),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(text)
    tmp.chmod(0o600)
    tmp.replace(CONFIG_PATH)
    CONFIG_PATH.chmod(0o600)


def ensure_config_file(cfg: Optional[AppConfig] = None) -> Path:
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    save_config(cfg or AppConfig())
    return CONFIG_PATH


def config_to_dict(cfg: AppConfig) -> dict[str, Any]:
    return asdict(cfg)
