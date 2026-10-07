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
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from gi.repository import GdkPixbuf, GLib, Gtk

from .config import AppConfig
from .core.models import ProviderUsage, UsageWindow
from .dashboard import DashboardWindow
from .providers import PROVIDER_BY_ID
from . import ui_common as ui
from .presentation import APP_NAME, format_next_reset, menu_rows, reading_badge, reading_details, reset_view, tray_reading
from .codex_resets import SITE_URL
from .icons import ensure_gauge_icon

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
        icon_theme: str = "dark",
    ):
        self.icon_paths = icon_paths
        self.icon_theme = icon_theme
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
        self._codex_resets = None

    def _cfg(self) -> AppConfig:
        if self.get_config:
            return self.get_config()
        return AppConfig()

    def _build_menu(self, usages: list[ProviderUsage], next_reset: str = "") -> Gtk.Menu:
        menu = Gtk.Menu()
        cfg = self._cfg()

        # Ring context first: what the icon shows, then source, age and coverage.
        reading = tray_reading(usages, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical)
        for row in menu_rows(reading):
            self._plain_row(menu, row)
        header = self._build_header_label(usages, next_reset, cfg)
        if header:
            self._plain_row(menu, header)
        menu.append(Gtk.SeparatorMenuItem())

        overview = Gtk.MenuItem(label="Open Overview…")
        overview.connect("activate", lambda *_: self.open_dashboard())
        menu.append(overview)
        refresh = Gtk.MenuItem(label="Refreshing…" if self._refreshing else "Refresh Now")
        refresh.set_sensitive(not self._refreshing)
        refresh.connect("activate", lambda *_: self._trigger_refresh())
        menu.append(refresh)
        menu.append(Gtk.SeparatorMenuItem())

        buckets = ui.classify_usages(
            usages,
            hide_offline=cfg.hide_offline,
            warn_at=cfg.threshold_warning,
            crit_at=cfg.threshold_critical,
        )

        sections = [
            ("At the limit", buckets["critical"]),
            ("Active", buckets["active"]),
            ("Problems", buckets["errors"]),
            ("Not connected", buckets["offline"]),
        ]
        any_item = False
        for title, items in sections:
            if not items:
                continue
            any_item = True
            self._plain_row(menu, f"{title} · {len(items)}")
            for usage in items:
                menu.append(self._provider_item(usage, cfg))

        if not any_item:
            self._plain_row(menu, "No providers to show — enable some in Settings")

        view = reset_view(getattr(self, "_codex_resets", None))
        if view is not None:
            menu.append(Gtk.SeparatorMenuItem())
            menu.append(self._codex_resets_item(view))

        menu.append(Gtk.SeparatorMenuItem())
        settings = Gtk.MenuItem(label="Settings…")
        settings.connect("activate", lambda *_: self._open_settings())
        menu.append(settings)
        logs = Gtk.MenuItem(label="Open Logs")
        logs.connect("activate", lambda *_: self._open_logs())
        menu.append(logs)
        about = Gtk.MenuItem(label=f"About {APP_NAME}")
        about.connect("activate", lambda *_: self._show_about())
        menu.append(about)

        menu.append(Gtk.SeparatorMenuItem())
        quit_it = Gtk.MenuItem(label=f"Quit {APP_NAME}")
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
        # The ring rows above already name the most-used window; only the
        # opt-in headroom heuristic adds something here.
        if cfg.label_mode == "recommend":
            best_name, best_window, best_pct = ui.recommendation(usages)
            if best_name:
                parts.append(f"Most headroom: {best_name} {best_window} ({best_pct}%)")

        if next_reset:
            parts.append(format_next_reset(next_reset))
        if self._last_updated_at:
            age = datetime.now(timezone.utc) - self._last_updated_at
            parts.append(f"Last check {ui.human_age(age)}")
        return "  ·  ".join(parts)

    def _provider_item(self, usage: ProviderUsage, cfg: AppConfig) -> Gtk.MenuItem:
        level = ui.status_level(
            usage, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical
        )
        emoji = ui.status_emoji(level)
        name = ui.display_name(usage.provider)
        badge_kind, badge = reading_badge(usage)
        if ui.has_usage_data(usage) and badge_kind in ("stale", "neutral"):
            emoji = "⚪"  # color only for confirmed readings

        if level == "soft":
            label = f"{emoji}  {name}  —  not connected"
        elif level == "error":
            label = f"{emoji}  {name}  —  error"
        elif ui.has_usage_data(usage):
            pct = ui.worst_usage_pct(usage)
            bar = ui.block_bar(pct, width=8)
            label = f"{emoji}  {name}  {pct}%  {bar}"
            if badge_kind != "ok":
                label += f"  · {badge}"
        elif ui.monetary_label(usage):
            label = f"{emoji}  {name}  —  spend only"
        else:
            label = f"{emoji}  {name}  —  quota unknown"

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

    def _codex_resets_item(self, view) -> Gtk.MenuItem:
        """Global announcements from codex-resets.com, attributed and kept apart from quotas."""
        item = Gtk.MenuItem(label=view.headline)
        submenu = Gtk.Menu()
        for line in view.lines:
            self._plain_row(submenu, line)
        if view.announcement_url != SITE_URL:
            announcement = Gtk.MenuItem(label="Open Announcement ↗")
            announcement.connect("activate", lambda *_, u=view.announcement_url: webbrowser.open(u))
            submenu.append(announcement)
        site = Gtk.MenuItem(label="Open codex-resets.com ↗")
        site.connect("activate", lambda *_: webbrowser.open(SITE_URL))
        submenu.append(site)
        item.set_submenu(submenu)
        return item

    def set_codex_resets(self, snapshot) -> bool:
        """Receive Codex Resets data on the GTK thread; None hides it."""
        if snapshot is not None and not self._cfg().codex_resets_enabled:
            snapshot = None
        self._codex_resets = snapshot
        if self.indicator is not None:
            self.menu = self._build_menu(self._last_usages, self._last_next_reset)
            self.indicator.set_menu(self.menu)
        if self._dashboard is not None:
            self._dashboard.set_codex_resets(snapshot)
        return False

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
        item = Gtk.MenuItem(label="Open Usage Page ↗")
        item.connect("activate", lambda *_: webbrowser.open(url))
        menu.append(item)

    def _append_refresh_provider_item(self, menu: Gtk.Menu, provider: str) -> None:
        label = ui.display_name(provider)
        menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label=f"Refresh {label}")
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
                theme=self.icon_theme,
            )
        self._dashboard.open_urls = self.open_urls
        self._dashboard.set_codex_resets(getattr(self, "_codex_resets", None))
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

        parent = None
        if self._dashboard is not None and self._dashboard._window is not None:
            if self._dashboard._window.get_visible():
                parent = self._dashboard._window
        SettingsDialog(
            parent=parent,
            config=self.get_config(),
            on_saved=self._on_settings_saved,
            on_refresh=self.on_refresh,
            theme=self.icon_theme,
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
        dialog.set_version("0.1.0-alpha.4")
        dialog.set_comments(
            "AI usage, quotas, resets and costs in your Linux tray.\n"
            "Every reading shows its source and age; unknown is never zero."
        )
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_website("https://caiolombello.github.io/quotalantern/")
        dialog.set_website_label("Website")
        dialog.set_copyright("Independent project · third-party notices in NOTICE")
        icon = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.svg")
        try:
            dialog.set_logo(GdkPixbuf.Pixbuf.new_from_file_at_size(icon, 96, 96))
        except (GLib.Error, TypeError):
            logger.debug("About logo unavailable", exc_info=True)
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
        reading = tray_reading(
            self._last_usages, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical
        )
        directory = os.path.dirname(self.icon_paths["neutral"])
        icon_path = ensure_gauge_icon(directory, reading.percent, reading.state, self.icon_theme)
        if self.indicator is not None and icon_path and os.path.exists(icon_path):
            description = APP_NAME + " — " + reading.description
            self.indicator.set_icon_full(icon_path, description)
            # New bindings expose a dedicated tooltip. Older ones export the
            # full context through Title/IconAccessibleDesc and the tray menu.
            self.indicator.set_title(description)
            if self.indicator.find_property("tooltip-body") is not None:
                self.indicator.set_property("tooltip-title", APP_NAME + " — " + reading.label)
                self.indicator.set_property("tooltip-body", reading.description)
            label = self._tray_label(self._last_usages, cfg) if cfg.label_mode == "recommend" else reading.label
            self.indicator.set_label(label, label)

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
