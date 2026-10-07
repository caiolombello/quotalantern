"""Shared GTK palette and CSS for QuotaLantern windows (presentation only)."""

from __future__ import annotations

import logging

logger = logging.getLogger("codexbar-linux")

# Status colors match the tray gauge in icons.py so ring, bars and pills agree.
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0b1522",
        "surface": "#111f31",
        "raised": "#172a42",
        "border": "#233854",
        "fg": "#eef4fe",
        "muted": "#a3b5cc",
        "faint": "#7a90ab",
        "accent": "#86d8ec",
        "accent_fg": "#06202a",
        "track": "#22344d",
        "ok": "#4ade80",
        "warning": "#facc15",
        "critical": "#f87171",
        "neutral": "#94a3b8",
        "focus": "#ffd18a",
    },
    "light": {
        "bg": "#f3f6fa",
        "surface": "#ffffff",
        "raised": "#eef3f9",
        "border": "#d6e0ec",
        "fg": "#132338",
        "muted": "#4f6178",
        "faint": "#6b819b",
        "accent": "#087f9e",
        "accent_fg": "#ffffff",
        "track": "#e2e9f2",
        "ok": "#14603c",
        "warning": "#875000",
        "critical": "#a52b34",
        "neutral": "#64748b",
        "focus": "#9a5c00",
    },
}


def palette(theme: str) -> dict[str, str]:
    return PALETTES["light" if theme == "light" else "dark"]


def build_css(theme: str) -> str:
    """Scoped CSS: .ql-dash is fully themed; .ql-settings only adds accents."""
    p = palette(theme)
    return f"""
window.ql-dash {{ background-color: {p['bg']}; }}
.ql-dash label {{ color: {p['fg']}; }}
.ql-dash viewport, .ql-dash scrolledwindow {{ background-color: transparent; border: none; }}
.ql-dash separator {{ background-color: {p['border']}; min-height: 1px; }}

.ql-header {{ background-color: {p['surface']}; border-bottom: 1px solid {p['border']}; padding: 16px 18px; }}
.ql-dash .ql-title {{ font-size: 16px; font-weight: 700; }}
.ql-dash .ql-subtitle {{ font-size: 12px; color: {p['muted']}; }}
.ql-dash .ql-unconfirmed {{ font-size: 12px; color: {p['warning']}; }}
.ql-dash .ql-ring-value {{ font-size: 15px; font-weight: 700; }}

.ql-body {{ padding: 6px 16px 16px 16px; }}
.ql-dash .ql-section {{ font-size: 10px; font-weight: 700; letter-spacing: 1px; color: {p['faint']}; margin-top: 12px; }}

.ql-card {{ background-color: {p['surface']}; border: 1px solid {p['border']}; border-radius: 12px; padding: 14px 16px; }}
.ql-card.ql-critical {{ border-color: alpha({p['critical']}, 0.65); }}
.ql-card.ql-warning {{ border-color: alpha({p['warning']}, 0.55); }}
.ql-card.ql-error {{ border-color: alpha({p['critical']}, 0.45); }}
.ql-dash .ql-card-title {{ font-size: 14px; font-weight: 700; }}
.ql-dash .ql-card-plan {{ font-size: 11px; color: {p['muted']}; }}
.ql-dash .ql-card-pct {{ font-size: 18px; font-weight: 700; }}
.ql-dash .ql-meter-name {{ font-size: 12px; color: {p['muted']}; }}
.ql-dash .ql-meter-pct {{ font-size: 12px; font-weight: 700; }}
.ql-dash .ql-caption {{ font-size: 11px; color: {p['faint']}; }}
.ql-dash .ql-meta {{ font-size: 11px; color: {p['faint']}; }}
.ql-dash .ql-cost {{ font-size: 15px; font-weight: 700; }}
.ql-dash .ql-reset-head {{ font-size: 13px; font-weight: 700; }}
.ql-dash .ql-quote {{ font-size: 12px; font-style: italic; color: {p['muted']}; }}
.ql-dash .ql-empty-title {{ font-size: 15px; font-weight: 700; }}
.ql-dash .ql-error-text {{ font-size: 12px; color: {p['critical']}; }}
.ql-dash .ql-footer {{ font-size: 11px; color: {p['faint']}; }}
.ql-footer-bar {{ border-top: 1px solid {p['border']}; padding: 8px 18px; background-color: {p['surface']}; }}

.ql-dash .ql-ok {{ color: {p['ok']}; }}
.ql-dash .ql-warning {{ color: {p['warning']}; }}
.ql-dash .ql-critical {{ color: {p['critical']}; }}
.ql-dash .ql-neutral {{ color: {p['neutral']}; }}

.ql-pill {{ font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 99px;
           background-color: alpha({p['neutral']}, 0.16); color: {p['neutral']}; }}
.ql-pill.ql-pill-ok {{ background-color: alpha({p['ok']}, 0.16); color: {p['ok']}; }}
.ql-pill.ql-pill-stale {{ background-color: alpha({p['warning']}, 0.16); color: {p['warning']}; }}
.ql-pill.ql-pill-error {{ background-color: alpha({p['critical']}, 0.16); color: {p['critical']}; }}
.ql-pill.ql-pill-spend {{ background-color: alpha({p['accent']}, 0.16); color: {p['accent']}; }}

.ql-dash progressbar trough {{ min-height: 8px; min-width: 40px; background-color: {p['track']};
                              background-image: none; border: none; border-radius: 99px; box-shadow: none; }}
.ql-dash progressbar progress {{ min-height: 8px; background-color: {p['accent']}; background-image: none;
                                border: none; border-radius: 99px; box-shadow: none; }}
.ql-dash progressbar.ql-ok progress {{ background-color: {p['ok']}; }}
.ql-dash progressbar.ql-warning progress {{ background-color: {p['warning']}; }}
.ql-dash progressbar.ql-critical progress {{ background-color: {p['critical']}; }}
.ql-dash progressbar.ql-neutral progress {{ background-color: {p['neutral']}; }}

.ql-dash button {{ background-image: none; background-color: {p['raised']}; color: {p['fg']};
                  border: 1px solid {p['border']}; border-radius: 8px; box-shadow: none;
                  text-shadow: none; padding: 4px 12px; min-height: 28px; }}
.ql-dash button label {{ color: inherit; }}
.ql-dash button:hover {{ background-color: alpha({p['accent']}, 0.14); border-color: alpha({p['accent']}, 0.5); }}
.ql-dash button:disabled {{ opacity: 0.6; }}
.ql-dash button.ql-primary {{ background-color: {p['accent']}; border-color: {p['accent']}; color: {p['accent_fg']}; font-weight: 700; }}
.ql-dash button.ql-primary:hover {{ background-color: shade({p['accent']}, 1.08); }}
.ql-dash button.ql-link {{ background-color: transparent; border-color: transparent; color: {p['accent']}; padding: 4px 8px; }}
.ql-dash button.ql-link:hover {{ background-color: alpha({p['accent']}, 0.12); }}
.ql-dash button:focus {{ outline-color: {p['focus']}; outline-style: solid; outline-width: 2px; outline-offset: 1px; }}
.ql-dash spinner {{ color: {p['accent']}; }}

.ql-settings .ql-page-title {{ font-size: 16px; font-weight: 700; }}
.ql-settings .ql-group-title {{ font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {p['faint']}; }}
.ql-settings .ql-hint {{ font-size: 11px; }}
.ql-settings .ql-row-title {{ font-weight: 600; }}
.ql-settings .ql-error-text {{ color: {p['critical']}; }}
.ql-settings list.ql-boxed {{ border: 1px solid alpha(currentColor, 0.18); border-radius: 10px; }}
.ql-settings list.ql-boxed row {{ padding: 10px 12px; border-bottom: 1px solid alpha(currentColor, 0.08); }}
.ql-settings list.ql-boxed row:last-child {{ border-bottom: none; }}
.ql-settings .ql-pill {{ font-size: 11px; }}
"""


_installed_theme: str | None = None


def install_css(theme: str) -> bool:
    """Load one palette per process. Failure keeps the system theme."""
    global _installed_theme
    if _installed_theme is not None:
        return True
    key = "light" if theme == "light" else "dark"
    try:
        from gi.repository import Gdk, Gtk

        provider = Gtk.CssProvider()
        provider.load_from_data(build_css(key).encode("utf-8"))
        screen = Gdk.Screen.get_default()
        if screen is None:
            return False
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    except Exception:
        logger.warning("QuotaLantern CSS could not be loaded; using system theme", exc_info=True)
        return False
    _installed_theme = key
    return True


def theme_from_gtk() -> str:
    """Guess dark/light from GTK settings only (no subprocesses)."""
    try:
        from gi.repository import Gtk

        settings = Gtk.Settings.get_default()
        if settings is None:
            return "light"
        if settings.get_property("gtk-application-prefer-dark-theme"):
            return "dark"
        name = settings.get_property("gtk-theme-name") or ""
        return "dark" if "dark" in name.lower() else "light"
    except Exception:
        return "light"
