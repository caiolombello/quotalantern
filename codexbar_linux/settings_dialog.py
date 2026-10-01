"""GTK settings dialog for provider toggles and thresholds."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .config import AppConfig, save_config
from .providers import CATEGORY_LABELS, PROVIDER_SPECS

logger = logging.getLogger("codexbar-linux")

DATA_DIR = Path.home() / ".local/share/codexbar-linux"
CREDENTIAL_HINTS = [
    ("OpenCode cookie", DATA_DIR / "opencode_auth_cookie"),
    ("Kiro headers (legacy)", DATA_DIR / "kiro_headers.json"),
    ("Cursor cookie", DATA_DIR / "cursor_cookie"),
    ("GLM / z.ai API key", DATA_DIR / "zai_api_key"),
    ("Anthropic admin key", DATA_DIR / "anthropic_admin_key"),
    ("Google cookies (API)", DATA_DIR / "google_cookies"),
]


class SettingsDialog:
    def __init__(
        self,
        parent: Optional[Gtk.Window],
        config: AppConfig,
        on_saved: Callable[[AppConfig], None],
        on_refresh: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self.on_saved = on_saved
        self.on_refresh = on_refresh
        self._provider_toggles: dict[str, Gtk.CheckButton] = {}

        self.dialog = Gtk.Dialog(
            title="QuotaLantern Settings",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.dialog.set_default_size(540, 680)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.dialog.add_button("Save", Gtk.ResponseType.OK)
        if on_refresh:
            refresh_btn = self.dialog.add_button("Save & Refresh", Gtk.ResponseType.APPLY)
            refresh_btn.get_style_context().add_class("suggested-action")

        content = self.dialog.get_content_area()
        content.set_spacing(8)
        content.set_border_width(12)

        notebook = Gtk.Notebook()
        content.pack_start(notebook, True, True, 0)

        notebook.append_page(self._build_providers_page(), Gtk.Label(label="Providers"))
        notebook.append_page(self._build_display_page(), Gtk.Label(label="Display"))
        notebook.append_page(self._build_thresholds_page(), Gtk.Label(label="Alerts"))
        notebook.append_page(self._build_credentials_page(), Gtk.Label(label="Credentials"))

        self.dialog.connect("response", self._on_response)
        self.dialog.show_all()

    def _build_providers_page(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(8)
        scroll.add(box)

        intro = Gtk.Label(
            label="Enable only the providers you want.\n"
            "Disabled providers stay in the codebase — just hidden."
        )
        intro.set_xalign(0)
        intro.set_line_wrap(True)
        box.pack_start(intro, False, False, 0)

        by_category: dict[str, list] = {"subscription": [], "api_cost": [], "other": []}
        for spec in PROVIDER_SPECS:
            by_category.setdefault(spec.category, []).append(spec)

        for category in ("subscription", "api_cost", "other"):
            specs = by_category.get(category) or []
            if not specs:
                continue
            frame = Gtk.Frame(label=CATEGORY_LABELS.get(category, category))
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            inner.set_border_width(8)
            frame.add(inner)
            for spec in specs:
                row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                check = Gtk.CheckButton(label=spec.display_name)
                check.set_active(spec.id in self.config.enabled_providers)
                self._provider_toggles[spec.id] = check
                hint = Gtk.Label(label=f"  {spec.auth_hint}")
                hint.set_xalign(0)
                hint.get_style_context().add_class("dim-label")
                row.pack_start(check, False, False, 0)
                row.pack_start(hint, False, False, 0)
                inner.pack_start(row, False, False, 0)
            box.pack_start(frame, False, False, 0)

        glm_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        glm_box.pack_start(Gtk.Label(label="GLM region:"), False, False, 0)
        self.glm_region = Gtk.ComboBoxText()
        self.glm_region.append("global", "Global (api.z.ai)")
        self.glm_region.append("bigmodel-cn", "BigModel CN (open.bigmodel.cn)")
        self.glm_region.set_active_id(self.config.glm_region or "global")
        glm_box.pack_start(self.glm_region, False, False, 0)
        box.pack_start(glm_box, False, False, 0)

        return scroll

    def _build_display_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_border_width(12)

        self.hide_offline = Gtk.CheckButton(label="Hide offline / optional providers from menu")
        self.hide_offline.set_active(self.config.hide_offline)
        box.pack_start(self.hide_offline, False, False, 0)
        hint1 = Gtk.Label(
            label="When enabled, items like Claude (not signed in) disappear from the tray menu."
        )
        hint1.set_xalign(0)
        hint1.set_line_wrap(True)
        hint1.get_style_context().add_class("dim-label")
        box.pack_start(hint1, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 0)

        box.pack_start(Gtk.Label(label="Tray label mode", xalign=0), False, False, 0)
        self.label_mode = Gtk.ComboBoxText()
        self.label_mode.append("bottleneck", "Bottleneck — show highest usage (recommended)")
        self.label_mode.append("recommend", "Recommend — show best provider to use")
        self.label_mode.set_active_id(self.config.label_mode or "bottleneck")
        box.pack_start(self.label_mode, False, False, 0)

        self.open_dash = Gtk.CheckButton(label="Open overview dashboard on startup")
        self.open_dash.set_active(self.config.open_dashboard_on_start)
        box.pack_start(self.open_dash, False, False, 0)

        tip = Gtk.Label(
            label="Tip: use tray → “Open overview…” for colored cards and progress bars."
        )
        tip.set_xalign(0)
        tip.set_line_wrap(True)
        tip.get_style_context().add_class("dim-label")
        box.pack_start(tip, False, False, 0)

        return box

    def _build_thresholds_page(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.set_border_width(12)

        def add_spin(row: int, label: str, value: int, lower: int = 1, upper: int = 3600) -> Gtk.SpinButton:
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            spin = Gtk.SpinButton.new_with_range(lower, upper, 1)
            spin.set_value(value)
            grid.attach(spin, 1, row, 1, 1)
            return spin

        self.refresh_spin = add_spin(
            0, "Refresh interval (seconds)", self.config.refresh_interval_seconds, 30, 86400
        )
        self.info_spin = add_spin(1, "Info threshold %", self.config.threshold_info, 1, 100)
        self.warn_spin = add_spin(2, "Warning threshold %", self.config.threshold_warning, 1, 100)
        self.crit_spin = add_spin(3, "Critical threshold %", self.config.threshold_critical, 1, 100)
        self.reset_spin = add_spin(4, "Reset drop detect %", self.config.reset_drop_percent, 1, 100)
        self.recovery_spin = add_spin(
            5, "Recovery below %", self.config.recovery_below_percent, 1, 100
        )
        return grid

    def _build_credentials_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        note = Gtk.Label(
            label="Secrets live under ~/.local/share/codexbar-linux/\n"
            "Files should be mode 600. Never commit them.\n"
            "Kiro prefers kiro-cli login (SSO cache) over kiro_headers.json."
        )
        note.set_xalign(0)
        note.set_line_wrap(True)
        box.pack_start(note, False, False, 0)

        open_data = Gtk.Button(label="Open data directory")
        open_data.connect("clicked", lambda *_: self._open_path(DATA_DIR))
        box.pack_start(open_data, False, False, 0)

        for title, path in CREDENTIAL_HINTS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            status = "exists" if path.exists() else "missing"
            label = Gtk.Label(label=f"{title}: {path.name} ({status})")
            label.set_xalign(0)
            row.pack_start(label, True, True, 0)
            btn = Gtk.Button(label="Ensure file")
            btn.connect("clicked", lambda _b, p=path: self._ensure_secret_file(p))
            row.pack_start(btn, False, False, 0)
            box.pack_start(row, False, False, 0)

        return box

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
            open_urls=dict(self.config.open_urls),
        )

    def _on_response(self, dialog: Gtk.Dialog, response: int) -> None:
        if response in (Gtk.ResponseType.OK, Gtk.ResponseType.APPLY):
            cfg = self._collect()
            try:
                save_config(cfg)
            except OSError as exc:
                logger.error("Failed to save config: %s", exc)
            else:
                self.on_saved(cfg)
                if response == Gtk.ResponseType.APPLY and self.on_refresh:
                    self.on_refresh()
        dialog.destroy()

    def present(self) -> None:
        self.dialog.present()
