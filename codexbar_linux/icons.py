"""Dynamic SVG icon generation with cross-desktop theme detection."""

import hashlib
import os
from typing import Optional

from .desktop import detect_desktop_theme


# Colors for icon status
COLOR_GREEN = "#14603c"   # green-400
COLOR_YELLOW = "#875000"  # yellow-400
COLOR_RED = "#a52b34"     # red-400
COLOR_NEUTRAL = "#94a3b8"  # slate-400

# Background colors for light/dark theme
BG_DARK = "#1e293b"
BG_LIGHT = "#f8fafc"

# Approved Facho optical silhouette: filled paths preserve small-panel legibility.
# A separate shape badge conveys state; the neutral dash does not imply success.
_SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="5" fill="{bg}"/>
  <path d="M9 3H15V6H18L23 11H1L6 6H9Z" fill="{fg}"/>
  <path d="M3 13H7L10 26H18L21 13H25L21 30H7Z" fill="{fg}"/>
  <path d="M12 14L30 12V20L12 18Z" fill="{fg}"/>
  <circle cx="26" cy="26" r="6" fill="{bg}"/>
  <path d="{mark}" fill="none" stroke="{accent}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def _svg_fingerprint(bg: str, fg: str, accent: str, pct: int) -> str:
    """Hash the SVG parameters so we can detect when regeneration is needed."""
    payload = f"{bg}:{fg}:{accent}:{pct}:{_SVG_TEMPLATE}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _dash_array(pct: int) -> str:
    """Compute stroke-dasharray for a partial arc.

    Circumference of r=24 is ~150.8. We map pct 0-100 to 0-150.
    """
    circumference = 150.8
    filled = circumference * min(max(pct, 0), 100) / 100
    return f"{filled:.1f} {circumference:.1f}"


def _write_svg(path: str, bg: str, fg: str, accent: str, pct: int, badge: str) -> str:
    marks = {"?": "M23 26h6", "✓": "M23 26l2 2 4-5", "~": "M23 24h6M23 28h6", "!": "M26 22v4M26 29v.1"}
    svg = _SVG_TEMPLATE.format(bg=bg, fg=fg, accent=accent, mark=marks[badge])
    with open(path, "w") as f:
        f.write(svg)
    return path


def detect_gnome_theme() -> str:
    """Backward-compatible alias for callers from the GNOME-only version."""
    return detect_desktop_theme()


def _accent_for_pct(pct: int) -> str:
    if pct >= 90:
        return COLOR_RED
    if pct >= 70:
        return COLOR_YELLOW
    return COLOR_GREEN


def _badge_for_pct(pct: int) -> str:
    if pct >= 90:
        return "!"
    if pct >= 70:
        return "~"
    return "✓"


def ensure_icons(base_dir: str, theme: Optional[str] = None) -> dict[str, str]:
    """Generate status icons for the given theme. Returns paths by status.

    Lantern status uses large shape marks; the menu gives the full reading.
    """
    if theme is None:
        theme = detect_desktop_theme()

    icons_dir = os.path.join(base_dir, "icons")
    os.makedirs(icons_dir, exist_ok=True)

    bg = BG_DARK if theme == "dark" else BG_LIGHT
    fg = "#F3F8FE" if theme == "dark" else "#16324B"

    paths = {}
    # Generate icons for specific percentage buckets + a neutral one
    for status, pct in [
        ("neutral", 0),
        ("ok", 30),
        ("warning", 80),
        ("critical", 95),
    ]:
        accent = fg if status == "neutral" else _accent_for_pct(pct)
        if theme == "dark" and status != "neutral":
            accent = {"ok": "#83d5ab", "warning": "#ffd18a", "critical": "#ffadb1"}[status]
        badge = "?" if status == "neutral" else _badge_for_pct(pct)
        fingerprint = _svg_fingerprint(bg, fg, accent, pct)
        path = os.path.join(icons_dir, f"icon-{status}-{fingerprint}.svg")
        if not os.path.exists(path):
            _write_svg(path, bg, fg, accent, pct, badge)
        paths[status] = path

    return paths
