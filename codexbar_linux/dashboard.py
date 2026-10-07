"""GTK overview window: the tray ring in large, then one card per provider."""

from __future__ import annotations

import logging
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .config import AppConfig
from .core.models import ProviderUsage, UsageWindow
from .providers import PROVIDER_BY_ID
from . import gtk_style
from . import ui_common as ui
from .icons import gauge_svg
from .codex_resets import SITE_URL
from .presentation import (
    APP_NAME,
    format_interval,
    format_next_reset,
    reading_badge,
    reading_details,
    reset_view,
    tray_reading,
    window_span,
)

logger = logging.getLogger("codexbar-linux")

SECTION_TITLES = (
    ("critical", "AT THE LIMIT"),
    ("active", "ACTIVE"),
    ("errors", "PROBLEMS"),
    ("offline", "NOT CONNECTED"),
)
CARD_TONES = {"crit": "ql-critical", "warn": "ql-warning", "error": "ql-error"}
REFRESH_FALLBACK_SECONDS = 90
RING_PIXELS = 68


def _tone(pct: int, cfg: AppConfig) -> str:
    if pct >= cfg.threshold_critical:
        return "ql-critical"
    if pct >= cfg.threshold_warning:
        return "ql-warning"
    return "ql-ok"


def _valid_pct(pct) -> bool:
    return type(pct) is int and 0 <= pct <= 100


def _add_classes(widget, *names: str):
    context = widget.get_style_context()
    for name in names:
        if name:
            context.add_class(name)
    return widget


def _label(text: str, *classes: str, wrap: bool = False):
    label = Gtk.Label(label=text, xalign=0)
    if wrap:
        label.set_line_wrap(True)
    return _add_classes(label, *classes)


class DashboardWindow:
    """Singleton-ish overview window for provider usage."""

    def __init__(
        self,
        on_refresh: Callable[[], None],
        on_refresh_provider: Callable[[str], None],
        get_config: Callable[[], AppConfig],
        open_urls: dict[str, str],
        on_open_settings: Optional[Callable[[], None]] = None,
        theme: str = "dark",
    ):
        self.on_refresh = on_refresh
        self.on_refresh_provider = on_refresh_provider
        self.get_config = get_config
        self.open_urls = open_urls
        self.on_open_settings = on_open_settings
        self._theme = theme

        self._window: Optional[Gtk.Window] = None
        self._cards_box: Optional[Gtk.Box] = None
        self._header_label: Optional[Gtk.Label] = None
        self._subtitle_label: Optional[Gtk.Label] = None
        self._unconfirmed_label: Optional[Gtk.Label] = None
        self._ring: Optional[Gtk.Image] = None
        self._ring_value: Optional[Gtk.Label] = None
        self._footer_label: Optional[Gtk.Label] = None
        self._refresh_button: Optional[Gtk.Button] = None
        self._usages: list[ProviderUsage] = []
        self._next_reset: str = ""
        self._updated_at: Optional[datetime] = None
        self._refreshing = False
        self._refresh_timeout = 0
        self._codex_resets = None

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
        self._set_refreshing(False)
        if self._window is not None and self._window.get_visible():
            self._rebuild_cards()

    def set_codex_resets(self, snapshot) -> None:
        self._codex_resets = snapshot
        if self._window is not None and self._window.get_visible():
            self._rebuild_cards()

    # ── layout ────────────────────────────────────────────────────────────

    def _build(self) -> None:
        gtk_style.install_css(self._theme)
        win = Gtk.Window(title=APP_NAME)
        try:
            win.set_icon_from_file(str(Path(__file__).resolve().parent.parent / "assets/icon.svg"))
        except GLib.Error:
            logger.debug("Window icon unavailable", exc_info=True)
        win.set_default_size(560, 720)
        win.set_size_request(420, 360)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.connect("delete-event", self._on_delete)
        win.connect("key-press-event", self._on_key)
        win.get_style_context().add_class("ql-dash")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.add(root)
        root.pack_start(self._build_header(), False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        _add_classes(self._cards_box, "ql-body")
        scroll.add(self._cards_box)
        root.pack_start(scroll, True, True, 0)

        footer = _add_classes(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8), "ql-footer-bar")
        self._footer_label = _label("", "ql-footer", wrap=True)
        footer.pack_start(self._footer_label, True, True, 0)
        root.pack_end(footer, False, False, 0)

        self._window = win
        self._rebuild_cards()

    def _build_header(self) -> Gtk.Widget:
        header = _add_classes(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14), "ql-header")

        overlay = Gtk.Overlay()
        self._ring = Gtk.Image()
        self._ring.set_size_request(RING_PIXELS, RING_PIXELS)
        overlay.add(self._ring)
        self._ring_value = _add_classes(Gtk.Label(label="?"), "ql-ring-value")
        self._ring_value.set_halign(Gtk.Align.CENTER)
        self._ring_value.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(self._ring_value)
        overlay.set_valign(Gtk.Align.START)
        header.pack_start(overlay, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        text.set_valign(Gtk.Align.CENTER)
        self._header_label = _label("Collecting usage…", "ql-title", wrap=True)
        self._subtitle_label = _label("", "ql-subtitle", wrap=True)
        self._unconfirmed_label = _label("", "ql-unconfirmed", wrap=True)
        self._unconfirmed_label.set_no_show_all(True)
        for widget in (self._header_label, self._subtitle_label, self._unconfirmed_label):
            text.pack_start(widget, False, False, 0)
        header.pack_start(text, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_valign(Gtk.Align.START)
        if self.on_open_settings:
            settings = _add_classes(Gtk.Button(label="Settings"), "ql-link")
            settings.set_tooltip_text("Providers, alerts and credentials")
            settings.connect("clicked", lambda *_: self.on_open_settings and self.on_open_settings())
            buttons.pack_start(settings, False, False, 0)
        self._refresh_button = _add_classes(Gtk.Button(label="Refresh"), "ql-primary")
        self._refresh_button.set_tooltip_text("Refresh all enabled providers (F5)")
        self._refresh_button.connect("clicked", lambda *_: self._request_refresh())
        buttons.pack_start(self._refresh_button, False, False, 0)
        header.pack_end(buttons, False, False, 0)
        return header

    def _on_delete(self, *_args) -> bool:
        if self._window is not None:
            self._window.hide()
        return True  # don't destroy — reuse

    def _on_key(self, _widget, event) -> bool:
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if event.keyval == Gdk.KEY_Escape or (ctrl and event.keyval in (Gdk.KEY_w, Gdk.KEY_q)):
            self._on_delete()
            return True
        if event.keyval == Gdk.KEY_F5 or (ctrl and event.keyval == Gdk.KEY_r):
            self._request_refresh()
            return True
        return False

    # ── refresh feedback ──────────────────────────────────────────────────

    def _request_refresh(self) -> None:
        if self._refreshing:
            return
        self._set_refreshing(True)
        self.on_refresh()

    def _set_refreshing(self, busy: bool) -> None:
        self._refreshing = busy
        if self._refresh_timeout:
            GLib.source_remove(self._refresh_timeout)
            self._refresh_timeout = 0
        if busy:
            # A refresh skipped by the app lock never calls update(); recover.
            self._refresh_timeout = GLib.timeout_add_seconds(
                REFRESH_FALLBACK_SECONDS, self._refresh_timed_out
            )
        button = getattr(self, "_refresh_button", None)
        if button is not None:
            button.set_label("Refreshing…" if busy else "Refresh")
            button.set_sensitive(not busy)

    def _refresh_timed_out(self) -> bool:
        self._refresh_timeout = 0
        self._set_refreshing(False)
        return False

    # ── content ───────────────────────────────────────────────────────────

    def _rebuild_cards(self) -> None:
        if self._cards_box is None:
            return
        for child in list(self._cards_box.get_children()):
            self._cards_box.remove(child)

        cfg = self.get_config()
        self._update_header(cfg)
        if self._footer_label is not None:
            self._footer_label.set_text(
                f"Auto-refresh every {format_interval(cfg.refresh_interval_seconds)}"
                "  ·  The ring follows one window — never a total or average"
            )

        if self._updated_at is None and not self._usages:
            self._cards_box.pack_start(self._loading_state(), False, False, 0)
            self._cards_box.show_all()
            return

        buckets = ui.classify_usages(
            self._usages,
            hide_offline=cfg.hide_offline,
            warn_at=cfg.threshold_warning,
            crit_at=cfg.threshold_critical,
        )
        any_card = False
        for key, title in SECTION_TITLES:
            items = buckets[key]
            if not items:
                continue
            any_card = True
            self._cards_box.pack_start(_label(f"{title}  ·  {len(items)}", "ql-section"), False, False, 0)
            for usage in items:
                self._cards_box.pack_start(self._card_for(usage, cfg), False, False, 0)

        if not any_card:
            self._cards_box.pack_start(self._empty_state(), False, False, 0)

        view = reset_view(self._codex_resets)
        if view is not None:
            self._cards_box.pack_start(_label("ANNOUNCEMENTS", "ql-section"), False, False, 0)
            self._cards_box.pack_start(self._reset_card(view), False, False, 0)
        self._cards_box.show_all()

    def _update_header(self, cfg: AppConfig) -> None:
        if self._header_label is None:
            return
        reading = tray_reading(
            self._usages, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical
        )
        loading = self._updated_at is None and not self._usages
        self._set_ring(reading.percent, reading.state)
        # Unknown/stale rings carry their own dash/clock symbol in the SVG.
        self._ring_value.set_text(f"{reading.percent}%" if reading.percent is not None else "")
        context = self._ring_value.get_style_context()
        for name in ("ql-ok", "ql-warning", "ql-critical", "ql-neutral"):
            context.remove_class(name)
        context.add_class(
            {"ok": "ql-ok", "warning": "ql-warning", "critical": "ql-critical"}.get(reading.state, "ql-neutral")
        )
        self._ring.set_tooltip_text(reading.description)

        self._header_label.set_text("Collecting usage…" if loading else reading.label)
        parts: list[str] = []
        if self._updated_at:
            parts.append(f"Last check {ui.human_age(datetime.now(timezone.utc) - self._updated_at)}")
        if self._next_reset:
            parts.append(format_next_reset(self._next_reset))
        if cfg.label_mode == "recommend":
            rec_name, rec_win, rec_pct = ui.recommendation(self._usages)
            if rec_name:
                parts.append(f"Most headroom (qualified Codex only): {rec_name} {rec_win} {rec_pct}%")
        self._subtitle_label.set_text("  ·  ".join(parts) if parts else "First reading usually takes a few seconds.")

        unconfirmed = next(
            (line for line in reading.description.split("\n") if line.startswith("Unconfirmed: ")), ""
        )
        self._unconfirmed_label.set_text(unconfirmed)
        self._unconfirmed_label.set_visible(bool(unconfirmed))

    def _set_ring(self, pct: Optional[int], state: str) -> None:
        """Same SVG vocabulary as the tray: arc = usage, dotted = unknown, dashed = stale."""
        try:
            svg = gauge_svg(pct, state, self._theme, style="plain")
        except ValueError:
            svg = gauge_svg(None, "unknown", self._theme, style="plain")
        self._ring.set_from_gicon(Gio.BytesIcon.new(GLib.Bytes.new(svg.encode("utf-8"))), Gtk.IconSize.DIALOG)
        self._ring.set_pixel_size(RING_PIXELS)

    def _loading_state(self) -> Gtk.Widget:
        box = _add_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "ql-card")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        spinner = Gtk.Spinner()
        spinner.start()
        row.pack_start(spinner, False, False, 0)
        row.pack_start(_label("Collecting usage…", "ql-empty-title"), False, False, 0)
        box.pack_start(row, False, False, 0)
        box.pack_start(
            _label("Readings appear here as each enabled provider answers.", "ql-caption", wrap=True),
            False, False, 0,
        )
        return box

    def _empty_state(self) -> Gtk.Widget:
        box = _add_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "ql-card")
        box.pack_start(_label("Nothing to show yet", "ql-empty-title"), False, False, 0)
        box.pack_start(
            _label(
                "Enable providers in Settings, or turn off “Hide providers that aren't connected”.",
                "ql-caption", wrap=True,
            ),
            False, False, 0,
        )
        if self.on_open_settings:
            open_settings = _add_classes(Gtk.Button(label="Open Settings"), "ql-primary")
            open_settings.set_halign(Gtk.Align.START)
            open_settings.connect("clicked", lambda *_: self.on_open_settings and self.on_open_settings())
            box.pack_start(open_settings, False, False, 0)
        return box

    def _reset_card(self, view) -> Gtk.Widget:
        """Third-party announcements: global, attributed, separate from any quota."""
        card = _add_classes(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "ql-card")
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_row.pack_start(_label("Codex resets", "ql-card-title"), False, False, 0)
        pill = _add_classes(Gtk.Label(label="All paid plans"), "ql-pill")
        pill.set_valign(Gtk.Align.CENTER)
        title_row.pack_start(pill, False, False, 0)
        card.pack_start(title_row, False, False, 0)
        card.pack_start(_label(view.headline, "ql-reset-head", wrap=True), False, False, 0)
        if view.excerpt:
            card.pack_start(_label(f"“{view.excerpt}”", "ql-quote", wrap=True), False, False, 0)
        *details, attribution = view.lines
        for line in details:
            card.pack_start(_label(line, "ql-caption", wrap=True), False, False, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.pack_start(_label(attribution, "ql-meta", wrap=True), True, True, 0)
        site = _add_classes(Gtk.Button(label="codex-resets.com ↗"), "ql-link")
        site.set_valign(Gtk.Align.CENTER)
        site.connect("clicked", lambda *_: webbrowser.open(SITE_URL))
        footer.pack_end(site, False, False, 0)
        if view.announcement_url != SITE_URL:
            post = _add_classes(Gtk.Button(label="Announcement ↗"), "ql-link")
            post.set_valign(Gtk.Align.CENTER)
            post.connect("clicked", lambda *_, u=view.announcement_url: webbrowser.open(u))
            footer.pack_end(post, False, False, 0)
        card.pack_start(footer, False, False, 0)
        return card

    def _card_for(self, usage: ProviderUsage, cfg: AppConfig) -> Gtk.Widget:
        level = ui.status_level(usage, warn_at=cfg.threshold_warning, crit_at=cfg.threshold_critical)
        badge_kind, badge_text = reading_badge(usage)
        confirmed = badge_kind == "ok"
        card = _add_classes(
            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10), "ql-card", CARD_TONES.get(level, "")
        )

        # Title row: name, status pill, plan; worst window on the right.
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_row.pack_start(_label(ui.display_name(usage.provider), "ql-card-title"), False, False, 0)
        pill = _add_classes(Gtk.Label(label=badge_text), "ql-pill", f"ql-pill-{badge_kind}")
        pill.set_valign(Gtk.Align.CENTER)
        title_row.pack_start(pill, False, False, 0)
        if ui.has_usage_data(usage):
            pct = ui.worst_usage_pct(usage)
            if _valid_pct(pct):
                tone = _tone(pct, cfg) if confirmed else "ql-neutral"
                title_row.pack_end(_label(f"{pct}%", "ql-card-pct", tone), False, False, 0)
        card.pack_start(title_row, False, False, 0)

        account = " · ".join(str(bit) for bit in (usage.login_method, usage.account_email) if bit)
        if account:
            plan = _label(account, "ql-card-plan")
            plan.set_ellipsize(Pango.EllipsizeMode.END)
            card.pack_start(plan, False, False, 0)

        cost_text = ui.monetary_label(usage)
        if cost_text:
            card.pack_start(_label(cost_text, "ql-cost"), False, False, 0)
            if usage.provider in {"openai-api", "claude-api"}:
                card.pack_start(
                    _label("Spend in currency — not a quota or remaining balance.", "ql-caption", wrap=True),
                    False, False, 0,
                )

        if ui.has_usage_data(usage):
            for name, window in (
                ("Session", usage.primary),
                ("Weekly", usage.secondary),
                ("Extra", usage.tertiary),
            ):
                if window:
                    card.pack_start(self._meter_row(name, window, cfg, confirmed), False, False, 0)
        elif not cost_text and not usage.error:
            card.pack_start(
                _label("No valid reading — usage and headroom can't be inferred.", "ql-caption", wrap=True),
                False, False, 0,
            )

        if usage.error and not ui.is_stale(usage.error):
            soft = level == "soft"
            card.pack_start(
                _label(ui.short_error(usage.error, 160), "ql-caption" if soft else "ql-error-text", wrap=True),
                False, False, 0,
            )
            spec = PROVIDER_BY_ID.get(usage.provider)
            if spec and spec.auth_hint:
                card.pack_start(_label(f"Fix: {spec.auth_hint}", "ql-caption", wrap=True), False, False, 0)

        # Source, age and uncertainty stay visible for every reading.
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        details = " · ".join(reading_details(usage))
        meta = _label(details, "ql-meta", wrap=True)
        meta.set_tooltip_text(details)
        footer.pack_start(meta, True, True, 0)

        refresh = _add_classes(Gtk.Button(label="Refresh"), "ql-link")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.connect("clicked", lambda *_, p=usage.provider: self.on_refresh_provider(p))
        url = self.open_urls.get(usage.provider) or (
            PROVIDER_BY_ID[usage.provider].open_url if usage.provider in PROVIDER_BY_ID else None
        )
        if url:
            open_btn = _add_classes(Gtk.Button(label="Open ↗"), "ql-link")
            open_btn.set_valign(Gtk.Align.CENTER)
            open_btn.set_tooltip_text(url)
            open_btn.connect("clicked", lambda *_, u=url: webbrowser.open(u))
            footer.pack_end(open_btn, False, False, 0)
        footer.pack_end(refresh, False, False, 0)
        card.pack_start(footer, False, False, 0)
        return card

    def _meter_row(self, name: str, window: UsageWindow, cfg: AppConfig, confirmed: bool = True) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        span = window_span(window.window_minutes)
        top.pack_start(_label(f"{name} · {span}" if span else name, "ql-meter-name"), True, True, 0)

        pct = window.used_percent
        valid = _valid_pct(pct)
        tone = (_tone(pct, cfg) if confirmed else "ql-neutral") if valid else "ql-neutral"
        top.pack_end(_label(f"{pct}%" if valid else "?", "ql-meter-pct", tone), False, False, 0)
        row.pack_start(top, False, False, 0)

        bar = _add_classes(Gtk.ProgressBar(), tone)
        bar.set_fraction(pct / 100.0 if valid else 0.0)
        row.pack_start(bar, False, False, 0)

        reset = ui.clean_reset(window.reset_description or "")
        if not reset and window.resets_at:
            left = ui.time_left(window.resets_at)
            reset = f"resets in {left}" if left and left != "resetting…" else left
        if reset:
            caption = _label(reset, "ql-caption")
            caption.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(caption, False, False, 0)
        return row
