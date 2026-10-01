"""GTK tray icon using the cross-desktop Ayatana AppIndicator protocol.

Plasma consumes it through StatusNotifier over D-Bus; GNOME consumes it through
its AppIndicator host. Rich UI lives in the Dashboard window.
"""

from __future__ import annotations

import logging
import os
import subprocess
import webbrowser
from datetime import datetime, timezone
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from gi.repository import GLib, Gtk

from .config import AppConfig
from .core.models import ProviderUsage, UsageWindow
from .dashboard import DashboardWindow
from .providers import PROVIDER_BY_ID
from . import ui_common as ui
from .presentation import APP_NAME, reading_details, tray_icon_name

logger = logging.getLogger("codexbar-linux")


def _status_icon_name(worst: int) -> str:
    if worst >= 90:
        return "critical"
    if worst >= 70:
        return "warning"
    return "ok"


class TrayApp:
    def __init__(
        self,
        icon_paths: dict[str, str],
        on_refresh: Callable[[], None],
        on_refresh_provider: Callable[[str], None],
        on_quit: Callable[[], None],
        open_urls: dict[str, str] | None = None,
        get_config: Optional[Callable[[], AppConfig]] = None,
        on_config_saved: Optional[Callable[[AppConfig], None]] = None,
    ):
        self.icon_paths = icon_paths
        self.on_refresh = on_refresh
        self.on_refresh_provider = on_refresh_provider
        self.on_quit = on_quit
        self.open_urls = open_urls or {}
        self.get_config = get_config
        self.on_config_saved = on_config_saved
        self.indicator: Optional[AppIndicator3.Indicator] = None
        self.menu: Optional[Gtk.Menu] = None
        self._countdown_tag: int = 0
        self._last_usages: list[ProviderUsage] = []
        self._last_next_reset: str = ""
        self._last_updated_at: Optional[datetime] = None
        self._refreshing = False
        self._dashboard: Optional[DashboardWindow] = None

    def _cfg(self) -> AppConfig:
        if self.get_config:
            return self.get_config()
        return AppConfig()

    def _build_menu(self, usages: list[ProviderUsage], next_reset: str = "") -> Gtk.Menu:
        menu = Gtk.Menu()
        cfg = self._cfg()

        header = self._build_header_label(usages, next_reset, cfg)
        if header:
            item = Gtk.MenuItem(label=header)
            item.set_sensitive(False)
            menu.append(item)
            menu.append(Gtk.SeparatorMenuItem())

        if self._refreshing:
            loading = Gtk.MenuItem(label="Refreshing…")
            loading.set_sensitive(False)
            menu.append(loading)
            menu.append(Gtk.SeparatorMenuItem())

        buckets = ui.classify_usages(
            usages,
            hide_offline=cfg.hide_offline,
            warn_at=cfg.threshold_warning,
            crit_at=cfg.threshold_critical,
        )

        sections = [
            ("Critical", buckets["critical"]),
            ("Active", buckets["active"]),
            ("Errors", buckets["errors"]),
            ("Offline", buckets["offline"]),
        ]
        any_item = False
        for title, items in sections:
            if not items:
                continue
            any_item = True
            section = Gtk.MenuItem(label=f"── {title} ──")
            section.set_sensitive(False)
            menu.append(section)
            for usage in items:
                menu.append(self._provider_item(usage, cfg))

        if not any_item:
            no_data = Gtk.MenuItem(label="No providers to show — open Settings")
            no_data.set_sensitive(False)
            menu.append(no_data)

        menu.append(Gtk.SeparatorMenuItem())

        overview = Gtk.MenuItem(label="📊  Open overview…")
        overview.connect("activate", lambda *_: self.open_dashboard())
        menu.append(overview)

        menu.append(Gtk.SeparatorMenuItem())
        actions = Gtk.MenuItem(label="Actions ▸")
        sub = Gtk.Menu()

        refresh = Gtk.MenuItem(label="🔄  Refresh now")
        refresh.connect("activate", lambda *_: self._trigger_refresh())
        sub.append(refresh)

        settings = Gtk.MenuItem(label="⚙️  Settings…")
        settings.connect("activate", lambda *_: self._open_settings())
        sub.append(settings)

        logs = Gtk.MenuItem(label="📄  Open logs")
        logs.connect("activate", lambda *_: self._open_logs())
        sub.append(logs)

        about = Gtk.MenuItem(label="ℹ️  About")
        about.connect("activate", lambda *_: self._show_about())
        sub.append(about)

        actions.set_submenu(sub)
        menu.append(actions)

        menu.append(Gtk.SeparatorMenuItem())
        quit_it = Gtk.MenuItem(label="⏻  Quit")
        quit_it.connect("activate", lambda *_: self.on_quit())
        menu.append(quit_it)

        menu.show_all()
        return menu

    def _trigger_refresh(self) -> None:
        self._refreshing = True
        if self.indicator is not None:
            self.menu = self._build_menu(self._last_usages, self._last_next_reset)
            self.indicator.set_menu(self.menu)
        self.on_refresh()

    def _build_header_label(
        self,
        usages: list[ProviderUsage],
        next_reset: str,
        cfg: AppConfig,
    ) -> str:
        parts: list[str] = []
        if cfg.label_mode == "recommend":
            best_name, best_window, best_pct = ui.recommendation(usages)
            if best_name:
                parts.append(f"Use {best_name} {best_window} ({best_pct}%)")
        else:
            bot_name, bot_pct = ui.bottleneck(usages)
            if bot_name:
                level = (
                    "crit"
                    if bot_pct >= cfg.threshold_critical
                    else "warn"
                    if bot_pct >= cfg.threshold_warning
                    else "ok"
                )
                parts.append(f"{ui.status_emoji(level)} {bot_name} {bot_pct}%")

        if next_reset:
            parts.append(f"Next: {ui.clean_reset(next_reset)}")
        if self._last_updated_at:
            age = datetime.now(timezone.utc) - self._last_updated_at
            parts.append(ui.human_age(age))
        return "  ·  ".join(parts)

    def _provider_item(self, usage: ProviderUsage, cfg: AppConfig) -> Gtk.MenuItem:
        level = ui.status_level(
            usage, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical
        )
        emoji = ui.status_emoji(level)
        name = ui.display_name(usage.provider)
        stale = ui.is_stale(usage.error)

        if level == "soft":
            label = f"{emoji}  {name}  · off"
        elif level == "error":
            label = f"{emoji}  {name}"
        elif ui.has_usage_data(usage):
            pct = ui.worst_usage_pct(usage)
            bar = ui.block_bar(pct, width=6)
            label = f"{emoji} {bar}  {name}  {pct}%"
            if stale:
                label += " · cached / stale"
        else:
            label = f"{emoji}  {name} · quota unknown"

        item = Gtk.MenuItem(label=label)
        submenu = Gtk.Menu()

        if usage.error:
            if level == "soft":
                status = Gtk.MenuItem(label=f"Status: {ui.short_error(usage.error, 100)}")
                status.set_sensitive(False)
                submenu.append(status)
                tip = Gtk.MenuItem(label="Tip: hide offline in Settings, or disable provider")
                tip.set_sensitive(False)
                submenu.append(tip)
            else:
                err_item = Gtk.MenuItem(label=f"⚠ {ui.short_error(usage.error)}")
                err_item.set_sensitive(False)
                submenu.append(err_item)
                spec = PROVIDER_BY_ID.get(usage.provider)
                if spec and spec.auth_hint:
                    fix = Gtk.MenuItem(label=f"Fix: {spec.auth_hint}")
                    fix.set_sensitive(False)
                    submenu.append(fix)

        if usage.account_email:
            self._plain_row(submenu, f"Account: {usage.account_email}")
        if usage.login_method:
            self._plain_row(submenu, f"Plan: {usage.login_method}")
        cost_text = ui.monetary_label(usage)
        if cost_text:
            self._plain_row(submenu, cost_text)
        if usage.credits_remaining is not None:
            self._plain_row(submenu, f"Credits: {usage.credits_remaining}")

        if usage.primary:
            submenu.append(self._window_item("Session", usage.primary))
        if usage.secondary:
            submenu.append(self._window_item("Weekly", usage.secondary))
        if usage.tertiary:
            submenu.append(self._window_item("Extra", usage.tertiary))

        for detail in reading_details(usage):
            self._plain_row(submenu, detail, sensitive=False)

        self._append_refresh_provider_item(submenu, usage.provider)
        self._append_open_item(submenu, usage.provider)

        item.set_submenu(submenu)
        return item

    def _window_item(self, name: str, window: UsageWindow) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=ui.window_label(name, window))
        item.set_sensitive(False)
        return item

    def _plain_row(self, menu: Gtk.Menu, text: str, sensitive: bool = False) -> None:
        item = Gtk.MenuItem(label=text)
        item.set_sensitive(sensitive)
        menu.append(item)

    def _append_open_item(self, menu: Gtk.Menu, provider: str) -> None:
        url = self.open_urls.get(provider) or (
            PROVIDER_BY_ID[provider].open_url if provider in PROVIDER_BY_ID else None
        )
        if not url:
            return
        menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label="🌐  Open web dashboard")
        item.connect("activate", lambda *_: webbrowser.open(url))
        menu.append(item)

    def _append_refresh_provider_item(self, menu: Gtk.Menu, provider: str) -> None:
        label = ui.display_name(provider)
        menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label=f"🔄  Refresh {label}")
        item.connect("activate", lambda *_: self.on_refresh_provider(provider))
        menu.append(item)

    def open_dashboard(self) -> None:
        if self._dashboard is None:
            self._dashboard = DashboardWindow(
                on_refresh=self.on_refresh,
                on_refresh_provider=self.on_refresh_provider,
                get_config=self._cfg,
                open_urls=self.open_urls,
                on_open_settings=self._open_settings,
            )
        self._dashboard.open_urls = self.open_urls
        self._dashboard.update(
            self._last_usages,
            self._last_next_reset,
            self._last_updated_at,
        )
        self._dashboard.present()

    def _open_settings(self) -> None:
        if not self.get_config or not self.on_config_saved:
            path = os.path.expanduser("~/.config/codexbar-linux/config.json")
            if os.path.exists(path):
                webbrowser.open(f"file://{path}")
            else:
                logger.warning("Settings unavailable")
            return
        from .settings_dialog import SettingsDialog

        SettingsDialog(
            parent=None,
            config=self.get_config(),
            on_saved=self._on_settings_saved,
            on_refresh=self.on_refresh,
        ).present()

    def _on_settings_saved(self, cfg: AppConfig) -> None:
        if self.on_config_saved:
            self.on_config_saved(cfg)
        # Rebuild menu immediately with new prefs (hide offline, label mode)
        if self.indicator is not None:
            self.menu = self._build_menu(self._last_usages, self._last_next_reset)
            self.indicator.set_menu(self.menu)
            label = self._tray_label(self._last_usages, cfg)
            if label:
                self.indicator.set_label(label, label)
        if self._dashboard is not None:
            self._dashboard.update(
                self._last_usages, self._last_next_reset, self._last_updated_at
            )

    def _open_logs(self) -> None:
        log_dir = os.path.expanduser("~/.local/share/codexbar-linux/log")
        if os.path.exists(log_dir):
            subprocess.Popen(
                ["xdg-open", log_dir],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            logger.warning("Log directory not found: %s", log_dir)

    def _show_about(self) -> None:
        dialog = Gtk.AboutDialog()
        dialog.set_program_name(APP_NAME)
        dialog.set_version("0.4.0 — local review candidate")
        dialog.set_comments(
            "AI coding provider usage monitor for Linux\n"
            "Local review candidate · source, freshness and uncertainty"
        )
        dialog.set_website("https://github.com/caiolombello/codexbar-linux")
        dialog.set_website_label("GitHub")
        dialog.run()
        dialog.destroy()

    def _icon_for_worst(self, worst_pct: int) -> str:
        return self.icon_paths.get(_status_icon_name(worst_pct), "")

    def _tray_label(self, usages: list[ProviderUsage], cfg: Optional[AppConfig] = None) -> str:
        cfg = cfg or self._cfg()
        if cfg.label_mode == "recommend":
            best_name, _, best_pct = ui.recommendation(usages)
            if best_name:
                return f"{best_name} {best_pct}%"
        name, pct = ui.bottleneck(usages)
        if name:
            emoji = ui.status_emoji(
                "crit"
                if pct >= cfg.threshold_critical
                else "warn"
                if pct >= cfg.threshold_warning
                else "ok"
            )
            # Some panels strip emoji; keep name+pct primary
            return f"{name} {pct}%"
        return ""

    def _start_countdown(self) -> None:
        if self._countdown_tag:
            GLib.source_remove(self._countdown_tag)
        self._countdown_tag = GLib.timeout_add(60_000, self._on_countdown_tick)

    def _on_countdown_tick(self) -> bool:
        if self.indicator is None:
            return False
        self.menu = self._build_menu(self._last_usages, self._last_next_reset)
        self.indicator.set_menu(self.menu)
        self._apply_status_icon()
        if self._dashboard is not None:
            self._dashboard.update(
                self._last_usages, self._last_next_reset, self._last_updated_at
            )
        return True

    def _stop_countdown(self) -> None:
        if self._countdown_tag:
            GLib.source_remove(self._countdown_tag)
            self._countdown_tag = 0

    def _apply_status_icon(self) -> None:
        cfg = self._cfg()
        icon_path = self.icon_paths.get(tray_icon_name(
            self._last_usages, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical
        ), "")
        if self.indicator is not None and icon_path and os.path.exists(icon_path):
            self.indicator.set_icon_full(icon_path, APP_NAME)

    def update(
        self,
        usages: list[ProviderUsage],
        next_reset: str = "",
        updated_at: Optional[datetime] = None,
    ) -> None:
        if self.indicator is None:
            return
        self._refreshing = False
        self._last_usages = usages
        self._last_next_reset = next_reset
        self._last_updated_at = updated_at or datetime.now(timezone.utc)

        self.menu = self._build_menu(usages, next_reset)
        self.indicator.set_menu(self.menu)

        cfg = self._cfg()
        self._apply_status_icon()
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        label = self._tray_label(usages, cfg)
        if label:
            self.indicator.set_label(label, label)
        else:
            self.indicator.set_label("", "")

        if self._dashboard is not None:
            self._dashboard.update(usages, next_reset, self._last_updated_at)

    def run(self) -> None:
        import signal

        signal.signal(signal.SIGINT, signal.SIG_DFL)

        icon_path = self.icon_paths.get("neutral", "")
        if not icon_path or not os.path.exists(icon_path):
            icon_path = "applications-system"

        self.indicator = AppIndicator3.Indicator.new(
            "codexbar-linux",
            icon_path,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_title(APP_NAME)
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_icon_full(icon_path, APP_NAME)

        self.menu = self._build_menu([], "")
        self.indicator.set_menu(self.menu)
        self._start_countdown()

        cfg = self._cfg()
        if cfg.open_dashboard_on_start:

            def _open_once() -> bool:
                self.open_dashboard()
                return False

            GLib.idle_add(_open_once)

        Gtk.main()

    def quit(self) -> None:
        self._stop_countdown()
        Gtk.main_quit()
