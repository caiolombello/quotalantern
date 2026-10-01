"""GTK dashboard window with colored provider cards."""

from __future__ import annotations

import logging
import webbrowser
from datetime import datetime, timezone
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from .config import AppConfig
from .core.models import ProviderUsage
from .providers import PROVIDER_BY_ID
from . import ui_common as ui
from .presentation import APP_NAME, reading_details
from pathlib import Path

logger = logging.getLogger("codexbar-linux")

_CSS = f"""
window.codexbar-dash {{
  background-color: #101b2b;
}}
.codexbar-title {{
  font-size: 18px;
  font-weight: bold;
  color: {ui.COLOR_FG_DARK};
}}
.codexbar-subtitle {{
  font-size: 12px;
  color: {ui.COLOR_MUTED};
}}
.codexbar-card {{
  background-color: {ui.COLOR_BG_DARK};
  border-radius: 10px;
  padding: 12px;
  margin: 4px;
  border: 1px solid #344762;
}}
.codexbar-card-title {{
  font-size: 14px;
  font-weight: bold;
  color: {ui.COLOR_FG_DARK};
}}
.codexbar-card-meta {{
  font-size: 11px;
  color: {ui.COLOR_MUTED};
}}
window.codexbar-dash label {{ color: #eef4fe; }}
window.codexbar-dash button {{ background-image: none; background-color: #213550; color: #eef4fe; border-color: #526887; min-height: 32px; }}
window.codexbar-dash button:focus {{ border: 2px solid #ffd18a; }}
window.codexbar-dash progressbar trough {{ background-color: #34445d; }}
window.codexbar-dash progressbar progress {{ background-color: #87baff; }}
.codexbar-section {{
  font-size: 11px;
  font-weight: bold;
  color: {ui.COLOR_MUTED};
  margin-top: 8px;
}}
"""


class DashboardWindow:
    """Singleton-ish overview window for provider usage."""

    def __init__(
        self,
        on_refresh: Callable[[], None],
        on_refresh_provider: Callable[[str], None],
        get_config: Callable[[], AppConfig],
        open_urls: dict[str, str],
        on_open_settings: Optional[Callable[[], None]] = None,
    ):
        self.on_refresh = on_refresh
        self.on_refresh_provider = on_refresh_provider
        self.get_config = get_config
        self.open_urls = open_urls
        self.on_open_settings = on_open_settings

        self._window: Optional[Gtk.Window] = None
        self._cards_box: Optional[Gtk.Box] = None
        self._header_label: Optional[Gtk.Label] = None
        self._subtitle_label: Optional[Gtk.Label] = None
        self._usages: list[ProviderUsage] = []
        self._next_reset: str = ""
        self._updated_at: Optional[datetime] = None
        self._css_loaded = False

    def _ensure_css(self) -> None:
        if self._css_loaded:
            return
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(_CSS.encode("utf-8"))
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(
                    screen,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
            self._css_loaded = True
        except Exception:
            logger.debug("Dashboard CSS load failed", exc_info=True)

    def present(self) -> None:
        if self._window is not None:
            self._rebuild_cards()
            self._window.present()
            return
        self._build()
        self._window.show_all()
        self._window.present()

    def update(
        self,
        usages: list[ProviderUsage],
        next_reset: str = "",
        updated_at: Optional[datetime] = None,
    ) -> None:
        self._usages = usages
        self._next_reset = next_reset
        self._updated_at = updated_at or datetime.now(timezone.utc)
        if self._window is not None and self._window.get_visible():
            self._rebuild_cards()

    def _build(self) -> None:
        self._ensure_css()
        win = Gtk.Window(title=f"{APP_NAME} — Overview")
        win.set_icon_from_file(str(Path(__file__).resolve().parent.parent / "assets/icon.svg"))
        win.set_default_size(520, 640)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.connect("delete-event", self._on_delete)
        win.get_style_context().add_class("codexbar-dash")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(14)
        win.add(root)

        # Header
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._header_label = Gtk.Label(label="Usage overview", xalign=0)
        self._header_label.get_style_context().add_class("codexbar-title")
        self._subtitle_label = Gtk.Label(label="", xalign=0)
        self._subtitle_label.get_style_context().add_class("codexbar-subtitle")
        self._subtitle_label.set_line_wrap(True)
        titles.pack_start(self._header_label, False, False, 0)
        titles.pack_start(self._subtitle_label, False, False, 0)
        header_row.pack_start(titles, True, True, 0)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda *_: self.on_refresh())
        header_row.pack_end(refresh_btn, False, False, 0)

        if self.on_open_settings:
            settings_btn = Gtk.Button(label="Settings")
            settings_btn.connect("clicked", lambda *_: self.on_open_settings and self.on_open_settings())
            header_row.pack_end(settings_btn, False, False, 0)

        root.pack_start(header_row, False, False, 0)
        scope = Gtk.Label(label="Heuristic: qualified Codex readings only. Other providers are not compared.", xalign=0)
        scope.set_line_wrap(True)
        scope.get_style_context().add_class("codexbar-subtitle")
        root.pack_start(scope, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(self._cards_box)
        root.pack_start(scroll, True, True, 0)

        self._window = win
        self._rebuild_cards()

    def _on_delete(self, *_args) -> bool:
        if self._window is not None:
            self._window.hide()
        return True  # don't destroy — reuse

    def _rebuild_cards(self) -> None:
        if self._cards_box is None:
            return
        for child in list(self._cards_box.get_children()):
            self._cards_box.remove(child)

        cfg = self.get_config()
        buckets = ui.classify_usages(
            self._usages,
            hide_offline=cfg.hide_offline,
            warn_at=cfg.threshold_warning,
            crit_at=cfg.threshold_critical,
        )

        # Header text
        rec_name, rec_win, rec_pct = ui.recommendation(self._usages)
        bot_name, bot_pct = ui.bottleneck(self._usages)
        if bot_name:
            emoji = ui.status_emoji(
                "crit" if bot_pct >= cfg.threshold_critical else (
                    "warn" if bot_pct >= cfg.threshold_warning else "ok"
                )
            )
            self._header_label.set_text(f"{emoji}  {bot_name}  {bot_pct}%")
        else:
            self._header_label.set_text("Usage overview")

        parts: list[str] = []
        if rec_name:
            parts.append(f"Qualified: {rec_name} {rec_win} ({rec_pct}%)")
        if self._next_reset:
            parts.append(f"Next: {ui.clean_reset(self._next_reset)}")
        if self._updated_at:
            age = datetime.now(timezone.utc) - self._updated_at
            parts.append(f"Last collection {ui.human_age(age)}")
        self._subtitle_label.set_text("  ·  ".join(parts) if parts else "No data yet — hit Refresh")

        sections = [
            ("Critical", buckets["critical"]),
            ("Active", buckets["active"]),
            ("Errors", buckets["errors"]),
            ("Offline", buckets["offline"]),
        ]
        any_card = False
        for title, items in sections:
            if not items:
                continue
            any_card = True
            sec = Gtk.Label(label=title.upper(), xalign=0)
            sec.get_style_context().add_class("codexbar-section")
            self._cards_box.pack_start(sec, False, False, 0)
            for usage in items:
                self._cards_box.pack_start(self._card_for(usage, cfg), False, False, 0)

        if not any_card:
            empty = Gtk.Label(
                label="No providers to show.\nEnable some in Settings, or uncheck Hide offline.",
                xalign=0,
            )
            empty.set_line_wrap(True)
            empty.get_style_context().add_class("codexbar-subtitle")
            self._cards_box.pack_start(empty, False, False, 0)

        self._cards_box.show_all()

    def _card_for(self, usage: ProviderUsage, cfg: AppConfig) -> Gtk.Widget:
        level = ui.status_level(usage, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical)
        color = ui.status_color(level)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("codexbar-card")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(10)
        frame.add(box)

        # Title row
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = ui.display_name(usage.provider)
        emoji = ui.status_emoji(level)
        title = Gtk.Label(label=f"{emoji}  {name}", xalign=0)
        title.get_style_context().add_class("codexbar-card-title")
        title_row.pack_start(title, True, True, 0)

        if ui.has_usage_data(usage):
            pct = ui.worst_usage_pct(usage)
            pct_label = Gtk.Label()
            pct_label.set_markup(
                f'<span foreground="{color}" weight="bold" size="large">{pct}%</span>'
            )
            title_row.pack_end(pct_label, False, False, 0)
        box.pack_start(title_row, False, False, 0)

        # Source, age and uncertainty stay visible for every reading.
        for detail in reading_details(usage):
            metadata = Gtk.Label(label=detail, xalign=0)
            metadata.get_style_context().add_class("codexbar-card-meta")
            metadata.set_ellipsize(Pango.EllipsizeMode.END)
            metadata.set_tooltip_text(detail)
            box.pack_start(metadata, False, False, 0)

        # Meta
        meta_bits: list[str] = []
        if usage.login_method:
            meta_bits.append(str(usage.login_method))
        if usage.account_email:
            meta_bits.append(str(usage.account_email))
        if meta_bits:
            meta = Gtk.Label(label=" · ".join(meta_bits), xalign=0)
            meta.get_style_context().add_class("codexbar-card-meta")
            meta.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(meta, False, False, 0)

        cost_text = ui.monetary_label(usage)
        if cost_text:
            cost = Gtk.Label(label=cost_text, xalign=0)
            cost.get_style_context().add_class("codexbar-card-meta")
            box.pack_start(cost, False, False, 0)

        # Progress bars for windows
        if ui.has_usage_data(usage):
            for label, window in (
                ("Session", usage.primary),
                ("Weekly", usage.secondary),
                ("Extra", usage.tertiary),
            ):
                if not window:
                    continue
                box.pack_start(self._meter_row(label, window.used_percent, window.reset_description, cfg), False, False, 0)
        if usage.error:
            err = Gtk.Label(label=ui.short_error(usage.error, 140), xalign=0)
            err.set_line_wrap(True)
            err.get_style_context().add_class("codexbar-card-meta")
            box.pack_start(err, False, False, 0)

        # Actions
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ref = Gtk.Button(label="Refresh")
        ref.connect("clicked", lambda *_ , p=usage.provider: self.on_refresh_provider(p))
        actions.pack_start(ref, False, False, 0)

        url = self.open_urls.get(usage.provider) or (
            PROVIDER_BY_ID[usage.provider].open_url if usage.provider in PROVIDER_BY_ID else None
        )
        if url:
            open_btn = Gtk.Button(label="Open web")
            open_btn.connect("clicked", lambda *_ , u=url: webbrowser.open(u))
            actions.pack_start(open_btn, False, False, 0)
        box.pack_start(actions, False, False, 0)

        return frame

    def _meter_row(self, name: str, pct: int, reset: str, cfg: AppConfig) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.pack_start(Gtk.Label(label=name, xalign=0), True, True, 0)
        color = ui.color_for_pct(pct, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical)
        pct_l = Gtk.Label()
        pct_l.set_markup(f'<span foreground="{color}">{pct}%</span>')
        top.pack_end(pct_l, False, False, 0)
        row.pack_start(top, False, False, 0)

        bar = Gtk.ProgressBar()
        bar.set_fraction(max(0.0, min(1.0, pct / 100.0)))
        bar.set_show_text(False)
        # Color via CSS class on style context is limited; still useful as structure
        row.pack_start(bar, False, False, 0)

        if reset:
            r = Gtk.Label(label=ui.clean_reset(reset), xalign=0)
            r.get_style_context().add_class("codexbar-card-meta")
            r.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(r, False, False, 0)
        return row
