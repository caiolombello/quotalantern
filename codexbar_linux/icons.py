"""Small-panel quota ring; app/launcher retain the approved Facho artwork."""
import hashlib
import os
import math
from typing import Optional
from .desktop import detect_desktop_theme


def gauge_svg(pct=None, state="unknown", theme="dark", style="facho"):
    """Full-size ring carries usage. Unknown/stale never get a quota arc."""
    if state not in ("ok", "warning", "critical", "unknown", "stale"):
        raise ValueError("Unknown gauge state")
    known = state in ("ok", "warning", "critical")
    if known and (type(pct) is not int or not 0 <= pct <= 100):
        raise ValueError("Gauge percentage must be an integer from 0 to 100")
    dark = theme == "dark"
    fg = "#F3F8FE" if dark else "#16324B"
    muted = "#94a3b8" if dark else "#64748b"
    colors = ({"ok": "#4ade80", "warning": "#facc15", "critical": "#f87171"}
              if dark else {"ok": "#14603c", "warning": "#875000", "critical": "#a52b34"})
    accent = colors.get(state, muted)
    ring = f'<circle cx="16" cy="16" r="13" fill="none" stroke="{accent}" stroke-width="3" opacity="0.25"/>'
    if known:
        circumference = 2 * math.pi * 13
        ring += f'<circle cx="16" cy="16" r="13" fill="none" stroke="{accent}" stroke-width="3" stroke-dasharray="{circumference * pct / 100:.3f} {circumference:.3f}" transform="rotate(-90 16 16)"/>'
    else:
        dash = "2 3" if state == "unknown" else "7 3"
        ring += f'<circle cx="16" cy="16" r="13" fill="none" stroke="{muted}" stroke-width="3" stroke-dasharray="{dash}"/>'
    if not known:
        center = 'M11 16H21' if state == "unknown" else 'M16 10V16L20 19'
        center = f'<path d="{center}" fill="none" stroke="{muted}" stroke-width="3" stroke-linecap="round"/>'
    elif style == "circle":
        center = f'<circle cx="16" cy="16" r="5" fill="{accent}"/>'
    elif style == "plain":
        center = ""  # the overview window prints the percentage inside
    else:
        center = f'<g transform="translate(7.68 7.68) scale(.52)" fill="{fg}"><path d="M9 3H15V6H18L23 11H1L6 6H9Z"/><path d="M3 13H7L10 26H18L21 13H25L21 30H7Z"/><path d="M12 14L30 12V20L12 18Z"/></g>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><title>{state}: {pct if known else "quota unconfirmed"}</title>{ring}{center}</svg>'


def ensure_gauge_icon(icons_dir, pct=None, state="unknown", theme="dark"):
    svg = gauge_svg(pct, state, theme)
    digest = hashlib.sha256(svg.encode()).hexdigest()[:16]
    os.makedirs(icons_dir, exist_ok=True)
    path = os.path.join(icons_dir, f"quota-{state}-{pct if pct is not None else 'none'}-{digest}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(svg)
    return path


def detect_gnome_theme():
    return detect_desktop_theme()


def ensure_icons(base_dir: str, theme: Optional[str] = None) -> dict[str, str]:
    theme = theme or detect_desktop_theme()
    directory = os.path.join(base_dir, "icons")
    return {key: ensure_gauge_icon(directory, pct, state, theme)
            for key, pct, state in (("neutral", None, "unknown"), ("ok", 30, "ok"),
                                    ("warning", 80, "warning"), ("critical", 95, "critical"))}
