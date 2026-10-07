"""GTK settings dialog: providers, display, alerts and credentials."""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from . import gtk_style
from . import opencode_auth
from .config import AppConfig, save_config
from .desktop import private_file_status
from .providers import CATEGORY_LABELS, PROVIDER_SPECS

logger = logging.getLogger("codexbar-linux")

DATA_DIR = Path.home() / ".local/share/codexbar-linux"
CREDENTIAL_HINTS = [
    ("OpenCode Zen cookie (legacy)", DATA_DIR / "opencode_auth_cookie"),
    ("Kiro headers (legacy)", DATA_DIR / "kiro_headers.json"),
    ("Cursor cookie", DATA_DIR / "cursor_cookie"),
    ("GLM / z.ai API key", DATA_DIR / "zai_api_key"),
    ("Anthropic admin key", DATA_DIR / "anthropic_admin_key"),
    ("Google cookies (API)", DATA_DIR / "google_cookies"),
]
PILL_KINDS = ("ql-pill-ok", "ql-pill-stale", "ql-pill-error", "ql-pill-spend")


def _classes(widget, *names: str):
    context = widget.get_style_context()
    for name in names:
        if name:
            context.add_class(name)
    return widget


def _text(text: str, *classes: str, wrap: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(wrap)
    return _classes(label, *classes)


def _set_pill(label: Gtk.Label, text: str, kind: str = "") -> None:
    label.set_text(text)
    context = label.get_style_context()
    for name in PILL_KINDS:
        context.remove_class(name)
    if kind:
        context.add_class(f"ql-pill-{kind}")


class SettingsDialog:
    def __init__(
        self,
        parent: Optional[Gtk.Window],
        config: AppConfig,
        on_saved: Callable[[AppConfig], None],
        on_refresh: Optional[Callable[[], None]] = None,
        theme: Optional[str] = None,
    ):
        self.config = config
        self.on_saved = on_saved
        self.on_refresh = on_refresh
        self._provider_toggles: dict[str, Gtk.Switch] = {}
        self._destroyed = False
        self._opencode_attempt: Optional[threading.Event] = None
        gtk_style.install_css(theme or gtk_style.theme_from_gtk())

        self.dialog = Gtk.Dialog(
            title="QuotaLantern Settings",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.dialog.get_style_context().add_class("ql-settings")
        self.dialog.set_default_size(760, 580)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self._save_button = self.dialog.add_button("Save", Gtk.ResponseType.OK)
        self._save_button.get_style_context().add_class("suggested-action")
        self.dialog.set_default_response(Gtk.ResponseType.OK)

        content = self.dialog.get_content_area()
        content.set_spacing(0)
        content.set_border_width(0)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_hexpand(True)
        stack.add_titled(self._page(self._build_providers_page()), "providers", "Providers")
        stack.add_titled(self._page(self._build_display_page()), "display", "Display")
        stack.add_titled(self._page(self._build_thresholds_page()), "alerts", "Alerts")
        stack.add_titled(self._page(self._build_credentials_page()), "credentials", "Credentials")
        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(stack)
        sidebar.set_size_request(170, -1)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.pack_start(sidebar, False, False, 0)
        body.pack_start(stack, True, True, 0)
        content.pack_start(body, True, True, 0)

        self.dialog.connect("response", self._on_response)
        self.dialog.connect("destroy", self._on_destroy)
        self.dialog.show_all()
        self._validate()

    # ── building blocks ───────────────────────────────────────────────────

    @staticmethod
    def _page(box: Gtk.Widget) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(box)
        return scroll

    @staticmethod
    def _page_box(title: str, intro: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(22)
        box.pack_start(_text(title, "ql-page-title"), False, False, 0)
        box.pack_start(_text(intro, "dim-label"), False, False, 4)
        return box

    @staticmethod
    def _group(box: Gtk.Box, title: str) -> Gtk.ListBox:
        box.pack_start(_text(title.upper(), "ql-group-title"), False, False, 10)
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.get_style_context().add_class("ql-boxed")
        box.pack_start(listbox, False, False, 0)
        return listbox

    @staticmethod
    def _row(listbox: Gtk.ListBox, title: str, hint: str, control: Optional[Gtk.Widget]) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        texts.set_valign(Gtk.Align.CENTER)
        texts.pack_start(_text(title, "ql-row-title"), False, False, 0)
        if hint:
            texts.pack_start(_text(hint, "dim-label", "ql-hint"), False, False, 0)
        row.pack_start(texts, True, True, 0)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            row.pack_end(control, False, False, 0)
        item = Gtk.ListBoxRow()
        item.set_activatable(False)
        item.add(row)
        listbox.add(item)
        return row

    # ── pages ─────────────────────────────────────────────────────────────

    def _build_providers_page(self) -> Gtk.Widget:
        box = self._page_box(
            "Providers",
            "Turn on only what you use. An enabled adapter reads its own local "
            "credential source and queries that service; disabled ones stay idle.",
        )
        self._enabled_count = _text("", "dim-label", "ql-hint")
        box.pack_start(self._enabled_count, False, False, 0)

        by_category: dict[str, list] = {"subscription": [], "api_cost": [], "other": []}
        for spec in PROVIDER_SPECS:
            by_category.setdefault(spec.category, []).append(spec)

        for category in ("subscription", "api_cost", "other"):
            specs = by_category.get(category) or []
            if not specs:
                continue
            listbox = self._group(box, CATEGORY_LABELS.get(category, category))
            for spec in specs:
                switch = Gtk.Switch()
                switch.set_active(spec.id in self.config.enabled_providers)
                switch.connect("notify::active", lambda *_: self._update_enabled_count())
                self._provider_toggles[spec.id] = switch
                self._row(listbox, spec.display_name, spec.auth_hint, switch)

        listbox = self._group(box, "Announcements")
        self.codex_resets = Gtk.Switch()
        self.codex_resets.set_active(self.config.codex_resets_enabled)
        self._row(
            listbox, "Codex reset announcements",
            "Global resets announced for paid Codex plans, read from codex-resets.com at most every "
            "15 minutes. Third-party data, not affiliated with OpenAI; no account data is sent. "
            "Shown apart from your own quota, never in the ring.",
            self.codex_resets,
        )

        listbox = self._group(box, "Endpoints")
        self.glm_region = Gtk.ComboBoxText()
        self.glm_region.append("global", "Global (api.z.ai)")
        self.glm_region.append("bigmodel-cn", "BigModel CN (open.bigmodel.cn)")
        self.glm_region.set_active_id(self.config.glm_region or "global")
        self._row(listbox, "GLM region", "Which z.ai endpoint the GLM adapter queries.", self.glm_region)
        self._update_enabled_count()
        return box

    def _update_enabled_count(self) -> None:
        enabled = sum(1 for toggle in self._provider_toggles.values() if toggle.get_active())
        self._enabled_count.set_text(f"{enabled} of {len(self._provider_toggles)} enabled")

    def _build_display_page(self) -> Gtk.Widget:
        box = self._page_box("Display", "How the tray and the overview window present readings.")
        listbox = self._group(box, "Tray")

        self.label_mode = Gtk.ComboBoxText()
        self.label_mode.append("bottleneck", "Most-used window")
        self.label_mode.append("recommend", "Most headroom (Codex only)")
        self.label_mode.set_active_id(self.config.label_mode or "bottleneck")
        self._row(
            listbox, "Tray label",
            "Most-used matches the ring. Headroom is a heuristic limited to fresh, qualified Codex readings.",
            self.label_mode,
        )
        self.hide_offline = Gtk.Switch()
        self.hide_offline.set_active(self.config.hide_offline)
        self._row(
            listbox, "Hide providers that aren't connected",
            "Signed-out or optional adapters disappear from the menu and the overview.",
            self.hide_offline,
        )

        listbox = self._group(box, "Overview window")
        self.open_dash = Gtk.Switch()
        self.open_dash.set_active(self.config.open_dashboard_on_start)
        self._row(listbox, "Open at startup", "Show the overview as soon as QuotaLantern starts.", self.open_dash)
        return box

    def _build_thresholds_page(self) -> Gtk.Widget:
        box = self._page_box(
            "Alerts",
            "Notifications fire when a window crosses these levels. The same levels color the ring and bars.",
        )

        def spin(value: int, lower: int, upper: int, unit: str) -> tuple[Gtk.Widget, Gtk.SpinButton]:
            button = Gtk.SpinButton.new_with_range(lower, upper, 1)
            button.set_value(value)
            button.connect("value-changed", lambda *_: self._validate())
            holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            holder.pack_start(button, False, False, 0)
            holder.pack_start(_text(unit, "dim-label", wrap=False), False, False, 0)
            return holder, button

        listbox = self._group(box, "Collection")
        holder, self.refresh_spin = spin(self.config.refresh_interval_seconds, 30, 86400, "seconds")
        self._row(listbox, "Check every", "How often enabled providers are queried.", holder)

        listbox = self._group(box, "Levels")
        holder, self.info_spin = spin(self.config.threshold_info, 1, 100, "%")
        self._row(listbox, "Info", "First heads-up notification.", holder)
        holder, self.warn_spin = spin(self.config.threshold_warning, 1, 100, "%")
        self._row(listbox, "Warning", "Ring and bars turn amber.", holder)
        holder, self.crit_spin = spin(self.config.threshold_critical, 1, 100, "%")
        self._row(listbox, "Critical", "Ring and bars turn red.", holder)

        listbox = self._group(box, "Recovery")
        holder, self.reset_spin = spin(self.config.reset_drop_percent, 1, 100, "%")
        self._row(listbox, "Reset detected", "Notify when usage drops by at least this much (and ends at 10% or less).", holder)
        holder, self.recovery_spin = spin(self.config.recovery_below_percent, 1, 100, "%")
        self._row(listbox, "Available again", "After a critical level, notify once usage falls below this.", holder)

        self._levels_error = _text("", "ql-error-text")
        self._levels_error.set_no_show_all(True)
        box.pack_start(self._levels_error, False, False, 6)
        return box

    def _validate(self) -> None:
        info, warn, crit = (int(s.get_value()) for s in (self.info_spin, self.warn_spin, self.crit_spin))
        problem = ""
        if warn >= crit:
            problem = "Warning must be lower than Critical."
        elif info > warn:
            problem = "Info must be at or below Warning."
        self._levels_error.set_text(problem)
        self._levels_error.set_visible(bool(problem))
        self._save_button.set_sensitive(not problem)
        self._save_button.set_tooltip_text(problem or None)

    def _build_credentials_page(self) -> Gtk.Widget:
        box = self._page_box(
            "Credentials",
            "Credentials stay where their provider keeps them. QuotaLantern never copies "
            "them into its settings and never shows their contents here.",
        )

        listbox = self._group(box, "OpenCode Go")
        self._opencode_status = _classes(Gtk.Label(xalign=0), "ql-pill")
        account = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.pack_start(_text("Browser sign-in (OAuth)", "ql-row-title", wrap=False), False, False, 0)
        top.pack_start(self._opencode_status, False, False, 0)
        account.pack_start(top, False, False, 0)
        account.pack_start(
            _text(
                "Authorize QuotaLantern in your browser and pick the workspace with your Go "
                "subscription. It keeps its own session; Disconnect removes only that session.",
                "dim-label", "ql-hint",
            ),
            False, False, 0,
        )
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._opencode_sign_in = Gtk.Button(label="Sign in")
        self._opencode_sign_in.get_style_context().add_class("suggested-action")
        self._opencode_sign_in.connect("clicked", self._start_opencode_login)
        buttons.pack_start(self._opencode_sign_in, False, False, 0)
        self._opencode_cancel = Gtk.Button(label="Cancel sign-in")
        self._opencode_cancel.set_sensitive(False)
        self._opencode_cancel.connect("clicked", self._cancel_opencode_login)
        buttons.pack_start(self._opencode_cancel, False, False, 0)
        self._opencode_disconnect = Gtk.Button(label="Disconnect")
        self._opencode_disconnect.set_tooltip_text("Remove only QuotaLantern's OpenCode session")
        self._opencode_disconnect.connect("clicked", self._disconnect_opencode)
        buttons.pack_end(self._opencode_disconnect, False, False, 0)
        account.pack_start(buttons, False, False, 0)
        self._opencode_progress = _text("", "ql-row-title")
        self._opencode_progress.set_selectable(True)
        account.pack_start(self._opencode_progress, False, False, 0)
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.add(account)
        listbox.add(row)
        self._update_opencode_status()

        listbox = self._group(box, "Local credential files")
        self._file_pills: dict[Path, Gtk.Label] = {}
        self._file_buttons: dict[Path, Gtk.Button] = {}
        for title, path in CREDENTIAL_HINTS:
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            pill = _classes(Gtk.Label(), "ql-pill")
            self._file_pills[path] = pill
            controls.pack_start(pill, False, False, 0)
            btn = Gtk.Button(label="Create")
            btn.set_tooltip_text("Create an empty file readable only by you (mode 600)")
            btn.connect("clicked", lambda _b, p=path: self._ensure_secret_file(p))
            self._file_buttons[path] = btn
            controls.pack_start(btn, False, False, 0)
            self._row(listbox, title, path.name, controls)
            self._update_file_status(path)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_data = Gtk.Button(label="Open data folder")
        open_data.connect("clicked", lambda *_: self._open_path(DATA_DIR))
        footer.pack_start(open_data, False, False, 0)
        footer.pack_start(
            _text("Kiro prefers kiro-cli login (SSO cache) over kiro_headers.json.", "dim-label", "ql-hint"),
            True, True, 0,
        )
        box.pack_start(footer, False, False, 12)
        return box

    def _update_file_status(self, path: Path) -> None:
        """Presence and permissions only; contents are never read."""
        status = private_file_status(path)
        button = self._file_buttons[path]
        if not status["present"]:
            _set_pill(self._file_pills[path], "Missing")
            button.set_label("Create")
            button.set_sensitive(True)
        elif status["private"]:
            _set_pill(self._file_pills[path], "✓ Private", "ok")
            button.set_label("Create")
            button.set_sensitive(False)
        else:
            _set_pill(self._file_pills[path], f"! Mode {status['mode']}", "error")
            button.set_label("Make private")
            button.set_tooltip_text("Restrict the file to mode 600 without reading it")
            button.set_sensitive(True)

    def _update_opencode_status(self) -> None:
        try:
            status = opencode_auth.login_status()
        except Exception as exc:
            logger.warning("OpenCode status failed (%s)", type(exc).__name__)
            status = "reconnect_required"
        labels = {
            "connected": ("Connected", "ok"),
            "disconnected": ("Not connected", ""),
            "reconnect_required": ("Reconnect required", "stale"),
        }
        text, kind = labels.get(status, labels["reconnect_required"])
        _set_pill(self._opencode_status, text, kind)
        self._opencode_sign_in.set_label("Sign in" if status == "disconnected" else "Reconnect")
        self._opencode_disconnect.set_sensitive(status != "disconnected")

    def _start_opencode_login(self, _button: Gtk.Button) -> None:
        if self._destroyed or self._opencode_attempt is not None:
            return
        cancel = threading.Event()
        self._opencode_attempt = cancel
        self._opencode_sign_in.set_sensitive(False)
        self._opencode_disconnect.set_sensitive(False)
        self._opencode_cancel.set_sensitive(True)
        self._opencode_progress.set_text("Starting sign-in…")
        threading.Thread(target=self._run_opencode_login, args=(cancel,), daemon=True).start()

    def _run_opencode_login(self, cancel: threading.Event) -> None:
        error = None
        success = False
        try:
            attempt = opencode_auth.begin_login()
            if not cancel.is_set():
                GLib.idle_add(self._show_opencode_code, cancel, attempt.user_code)
                subprocess.Popen(
                    ["xdg-open", attempt.verification_url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                opencode_auth.finish_login(attempt, cancel=cancel)
                # A normal return means the session was durably saved, even
                # if cancellation arrived while that final save was underway.
                success = True
        except opencode_auth.OAuthError as exc:
            error = str(exc)
        except Exception as exc:
            logger.warning("OpenCode sign-in failed (%s)", type(exc).__name__)
            error = "Could not sign in. Please try again."
        GLib.idle_add(self._finish_opencode_login, cancel, success, error)

    def _show_opencode_code(self, cancel: threading.Event, user_code: str) -> bool:
        if not self._destroyed and self._opencode_attempt is cancel and not cancel.is_set():
            self._opencode_progress.set_text(
                f"Enter code {user_code} in the OpenCode browser page.\n"
                "Waiting for authorization…"
            )
        return False

    def _finish_opencode_login(
        self, cancel: threading.Event, success: bool, error: Optional[str],
    ) -> bool:
        if self._destroyed or self._opencode_attempt is not cancel:
            return False
        self._opencode_attempt = None
        self._opencode_cancel.set_sensitive(False)
        self._opencode_sign_in.set_sensitive(True)
        self._update_opencode_status()
        self._opencode_progress.set_text(
            "" if success else ("Sign-in cancelled." if cancel.is_set() else (error or ""))
        )
        if success and self.on_refresh:
            self.on_refresh()
        return False

    def _cancel_opencode_login(self, _button: Gtk.Button) -> None:
        if self._opencode_attempt is not None:
            self._opencode_attempt.set()
            self._opencode_cancel.set_sensitive(False)
            self._opencode_progress.set_text("Cancelling sign-in…")

    def _disconnect_opencode(self, _button: Gtk.Button) -> None:
        if self._destroyed or self._opencode_attempt is not None:
            return
        try:
            opencode_auth.disconnect()
        except opencode_auth.OAuthError as exc:
            self._opencode_progress.set_text(str(exc))
        except Exception as exc:
            logger.warning("OpenCode disconnect failed (%s)", type(exc).__name__)
            self._opencode_progress.set_text("Could not disconnect. Please try again.")
        else:
            self._opencode_progress.set_text("")
            self._update_opencode_status()
            if self.on_refresh:
                self.on_refresh()

    def _on_destroy(self, _dialog: Gtk.Dialog) -> None:
        self._destroyed = True
        if self._opencode_attempt is not None:
            self._opencode_attempt.set()

    @staticmethod
    def _open_path(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _ensure_secret_file(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("")
            path.chmod(0o600)
            logger.info("Ensured credential file %s", path)
        except OSError as exc:
            logger.warning("Could not create %s: %s", path, exc)
        if path in getattr(self, "_file_pills", {}):
            self._update_file_status(path)

    def _collect(self) -> AppConfig:
        enabled = [pid for pid, toggle in self._provider_toggles.items() if toggle.get_active()]
        order = [spec.id for spec in PROVIDER_SPECS]
        enabled_sorted = [pid for pid in order if pid in enabled]

        return AppConfig(
            refresh_interval_seconds=int(self.refresh_spin.get_value()),
            threshold_info=int(self.info_spin.get_value()),
            threshold_warning=int(self.warn_spin.get_value()),
            threshold_critical=int(self.crit_spin.get_value()),
            reset_drop_percent=int(self.reset_spin.get_value()),
            recovery_below_percent=int(self.recovery_spin.get_value()),
            enabled_providers=enabled_sorted,
            glm_region=self.glm_region.get_active_id() or "global",
            hide_offline=self.hide_offline.get_active(),
            label_mode=self.label_mode.get_active_id() or "bottleneck",
            open_dashboard_on_start=self.open_dash.get_active(),
            codex_resets_enabled=self.codex_resets.get_active(),
            open_urls=dict(self.config.open_urls),
        )

    def _on_response(self, dialog: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            cfg = self._collect()
            try:
                save_config(cfg)
            except OSError as exc:
                logger.error("Failed to save config: %s", exc)
            else:
                # The app refreshes by itself when providers or the GLM region change.
                self.on_saved(cfg)
        dialog.destroy()

    def present(self) -> None:
        self.dialog.present()
