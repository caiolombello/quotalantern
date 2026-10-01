"""Main application logic."""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")
from gi.repository import GLib, Notify

from .codexbar import fetch_all, fetch_provider
from .config import AppConfig, ensure_config_file, load_config
from .core.logging import install_thread_exception_hook, setup_logging
from .core.models import ProviderUsage
from .desktop import detect_desktop_theme
from .icons import ensure_icons
from .state import append_history, load_state, save_state, snapshot
from .tray import TrayApp

logger = logging.getLogger("codexbar-linux")


class CodexBarLinuxApp:
    def __init__(self, verbose: bool = False):
        setup_logging(verbose=verbose)
        install_thread_exception_hook()

        ensure_config_file()
        self.config: AppConfig = load_config()
        self._state = load_state()
        theme = detect_desktop_theme()
        logger.info("Detected desktop theme: %s", theme)
        logger.info("Enabled providers: %s", ", ".join(self.config.enabled_providers) or "(none)")

        install_dir = os.path.expanduser("~/.local/share/codexbar-linux")
        icon_paths = ensure_icons(install_dir, theme=theme)

        self.tray = TrayApp(
            icon_paths=icon_paths,
            icon_theme=theme,
            on_refresh=self.refresh_async,
            on_refresh_provider=self.refresh_provider_async,
            on_quit=self.quit,
            open_urls=self.config.open_urls,
            get_config=lambda: self.config,
            on_config_saved=self.apply_config,
        )
        self._usages: list[ProviderUsage] = []
        self._timer: threading.Timer | None = None
        self._running = False
        self._refresh_lock = threading.Lock()

        Notify.init("codexbar-linux")

    def apply_config(self, cfg: AppConfig) -> None:
        """Hot-apply settings without restarting the tray."""
        prev_enabled = set(self.config.enabled_providers)
        self.config = cfg
        self.tray.open_urls = cfg.open_urls
        logger.info(
            "Config applied. Enabled: %s | hide_offline=%s label=%s",
            ", ".join(cfg.enabled_providers),
            cfg.hide_offline,
            cfg.label_mode,
        )
        if self._timer:
            self._timer.cancel()
        if self._running:
            self._schedule_refresh()
        # If provider set changed, refresh so menu reflects new data
        if set(cfg.enabled_providers) != prev_enabled:
            self.refresh_async()

    def _schedule_refresh(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self.config.refresh_interval_seconds, self._bg_refresh)
        self._timer.daemon = True
        self._timer.start()

    def _bg_refresh(self) -> None:
        try:
            self.refresh()
        except Exception:
            logger.exception("Background refresh failed")
        finally:
            self._schedule_refresh()

    def refresh_async(self) -> None:
        threading.Thread(target=self.refresh, daemon=True).start()

    def refresh_provider_async(self, provider: str) -> None:
        threading.Thread(target=self.refresh_provider, args=(provider,), daemon=True).start()

    def refresh(self) -> None:
        if not self._refresh_lock.acquire(blocking=False):
            logger.info("Refresh already in progress; skipping")
            return

        logger.info("Refreshing usage data...")
        try:
            self._refresh_locked()
        finally:
            self._refresh_lock.release()

    def _refresh_locked(self) -> None:
        usages: list[ProviderUsage] = []
        try:
            usages = fetch_all(enabled=self.config.enabled_providers)
        except Exception as exc:
            logger.error("Failed to fetch usage: %s", exc)

        self._usages = usages
        data = snapshot(usages, None)
        append_history(data)
        next_reset = self._next_reset(usages)
        updated_at = datetime.now(timezone.utc)
        GLib.idle_add(self._update_ui, usages, next_reset, updated_at)
        self._check_notifications(usages)
        save_state(self._state)

    def refresh_provider(self, provider: str) -> None:
        if provider not in self.config.enabled_providers:
            logger.info("Provider %s is disabled; skip refresh", provider)
            return
        if not self._refresh_lock.acquire(blocking=False):
            logger.info("Refresh already in progress; skipping %s provider refresh", provider)
            return

        logger.info("Refreshing %s provider...", provider)
        try:
            usage = fetch_provider(provider)
            self._replace_usage(usage)
            data = snapshot(self._usages, None)
            append_history(data)
            next_reset = self._next_reset(self._usages)
            updated_at = datetime.now(timezone.utc)
            GLib.idle_add(self._update_ui, self._usages, next_reset, updated_at)
            self._check_notifications(self._usages)
            save_state(self._state)
        finally:
            self._refresh_lock.release()

    def _replace_usage(self, usage: ProviderUsage) -> None:
        for index, current in enumerate(self._usages):
            if current.provider == usage.provider:
                self._usages[index] = usage
                return
        self._usages.append(usage)

    def _update_ui(
        self,
        usages: list[ProviderUsage],
        next_reset: str,
        updated_at: Optional[datetime] = None,
    ) -> bool:
        self.tray.update(usages, next_reset, updated_at)
        return False

    def _check_notifications(self, usages: list[ProviderUsage]) -> None:
        windows = self._window_percentages(usages)
        stored = self._state.setdefault("windows", {})
        for key, label, pct, reset in windows:
            prev = stored.get(key, {}).get("used_percent")
            if isinstance(prev, int):
                self._maybe_notify_transition(label, prev, pct, reset)
            stored[key] = {"used_percent": pct, "reset_description": reset}

        for usage in usages:
            if usage.error and "cookie expired" in usage.error.lower():
                self._notify(
                    f"{usage.provider} auth expired",
                    usage.error,
                    sound="dialog-warning",
                )

    def _maybe_notify_transition(self, label: str, previous: int, current: int, reset: str) -> None:
        thresholds = [
            (self.config.threshold_info, "Info"),
            (self.config.threshold_warning, "Warning"),
            (self.config.threshold_critical, "Critical"),
        ]
        for threshold, name in thresholds:
            if previous < threshold <= current:
                self._notify(
                    title=f"{label} {name}",
                    body=f"Usage reached {current}%. {reset or 'Reset time unavailable'}.",
                    sound="message",
                )

        if previous >= self.config.threshold_critical and current < self.config.recovery_below_percent:
            self._notify(
                f"{label} available again",
                f"Usage dropped to {current}%.",
                sound="message",
            )

        if previous - current >= self.config.reset_drop_percent and current <= 10:
            self._notify(
                f"{label} reset",
                f"Usage reset from {previous}% to {current}%.",
                sound="complete",
            )

    def _window_percentages(
        self,
        usages: list[ProviderUsage],
    ) -> list[tuple[str, str, int, str]]:
        windows: list[tuple[str, str, int, str]] = []
        for usage in usages:
            for name, window in (
                ("Session", usage.primary),
                ("Weekly", usage.secondary),
                ("Tertiary", usage.tertiary),
            ):
                if not window:
                    continue
                key = f"{usage.provider}:{name.lower()}"
                label = f"{usage.provider} {name}"
                windows.append((key, label, window.used_percent, window.reset_description))
        return windows

    def _next_reset(self, usages: list[ProviderUsage]) -> str:
        candidates: list[tuple[datetime, str]] = []
        for usage in usages:
            if any(
                window and window.used_percent >= 100
                for window in (usage.primary, usage.secondary, usage.tertiary)
            ):
                continue
            for name, window in (
                ("Session", usage.primary),
                ("Weekly", usage.secondary),
                ("Tertiary", usage.tertiary),
            ):
                if window and window.resets_at and window.used_percent < 100:
                    label = f"{usage.provider} {name}"
                    desc = window.reset_description or window.resets_at.astimezone().strftime("%H:%M")
                    candidates.append((window.resets_at, f"{label} | {desc}"))
        if candidates:
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]
        return ""

    @staticmethod
    def _notify(title: str, body: str, sound: Optional[str] = None) -> None:
        try:
            notification = Notify.Notification.new(f"QuotaLantern · {title}", body, "dialog-warning")
            notification.show()
            if sound:
                import subprocess

                subprocess.Popen(
                    ["canberra-gtk-play", "-i", sound, "-V", "-10.0"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            logger.exception("Failed to show notification or play sound")

    def run(self) -> None:
        self._running = True
        logger.info("Starting codexbar-linux tray app...")
        threading.Thread(target=self.refresh, daemon=True).start()
        self._schedule_refresh()
        self.tray.run()

    def quit(self) -> None:
        self._running = False
        if self._timer:
            self._timer.cancel()
        Notify.uninit()
        self.tray.quit()
        sys.exit(0)
