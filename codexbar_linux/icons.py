"""Small-panel quota ring; app/launcher retain the approved Facho artwork."""
import hashlib
import os
import math
from typing import Optional
from .desktop import detect_desktop_theme

_STATES = ("ok", "warning", "critical", "unknown", "stale")
_KNOWN = ("ok", "warning", "critical")
# Gap between provider segments, in degrees (~3 px at a 22 px tray size);
# narrower when many providers share the ring.
SEGMENT_GAP_DEG = 20.0
# Generated tray icons kept on disk; older ones are pruned on each new write.
ICON_CACHE_LIMIT = 48


def _palette(theme):
    dark = theme == "dark"
    fg = "#F3F8FE" if dark else "#16324B"
    muted = "#94a3b8" if dark else "#64748b"
    colors = ({"ok": "#4ade80", "warning": "#facc15", "critical": "#f87171"}
              if dark else {"ok": "#14603c", "warning": "#875000", "critical": "#a52b34"})
    return fg, muted, colors


def _check(pct, state):
    if state not in _STATES:
        raise ValueError("Unknown gauge state")
    if state in _KNOWN and (type(pct) is not int or not 0 <= pct <= 100):
        raise ValueError("Gauge percentage must be an integer from 0 to 100")


def _center(state, style, fg, muted, accent):
    if state not in _KNOWN:
        center = 'M11 16H21' if state == "unknown" else 'M16 10V16L20 19'
        return f'<path d="{center}" fill="none" stroke="{muted}" stroke-width="3" stroke-linecap="round"/>'
    if style == "circle":
        return f'<circle cx="16" cy="16" r="5" fill="{accent}"/>'
    if style == "plain":
        return ""  # the overview window prints the percentage inside
    return f'<g transform="translate(7.68 7.68) scale(.52)" fill="{fg}"><path d="M9 3H15V6H18L23 11H1L6 6H9Z"/><path d="M3 13H7L10 26H18L21 13H25L21 30H7Z"/><path d="M12 14L30 12V20L12 18Z"/></g>'


def gauge_svg(pct=None, state="unknown", theme="dark", style="facho"):
    """Full-size ring carries usage. Unknown/stale never get a quota arc."""
    _check(pct, state)
    known = state in _KNOWN
    fg, muted, colors = _palette(theme)
    accent = colors.get(state, muted)
    ring = f'<circle cx="16" cy="16" r="13" fill="none" stroke="{accent}" stroke-width="3" opacity="0.25"/>'
    if known:
        circumference = 2 * math.pi * 13
        ring += f'<circle cx="16" cy="16" r="13" fill="none" stroke="{accent}" stroke-width="3" stroke-dasharray="{circumference * pct / 100:.3f} {circumference:.3f}" transform="rotate(-90 16 16)"/>'
    else:
        dash = "2 3" if state == "unknown" else "7 3"
        ring += f'<circle cx="16" cy="16" r="13" fill="none" stroke="{muted}" stroke-width="3" stroke-dasharray="{dash}"/>'
    center = _center(state, style, fg, muted, accent)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><title>{state}: {pct if known else "quota unconfirmed"}</title>{ring}{center}</svg>'


def bar_svg(pct=None, state="unknown", theme="dark"):
    """Menu-row meter as a short horizontal bar, same states as the ring.

    Drawn as an icon so it renders the same in every panel font; hosts scale
    it into their square menu icon slot. Unknown/stale never get a fill.
    """
    _check(pct, state)
    known = state in _KNOWN
    _, muted, colors = _palette(theme)
    accent = colors.get(state, muted)
    track = f'<rect x="1" y="10" width="30" height="12" rx="3" fill="{accent}" opacity="0.25"/>'
    if known:
        width = 30 * pct / 100
        fill = f'<rect x="1" y="10" width="{width:.3f}" height="12" rx="3" fill="{accent}"/>' if pct > 0 else ""
    else:
        dash = "2 2" if state == "unknown" else "5 2"
        fill = f'<rect x="2" y="11" width="28" height="10" rx="2.5" fill="none" stroke="{muted}" stroke-width="2" stroke-dasharray="{dash}"/>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><title>{state}: {pct if known else "quota unconfirmed"}</title>{track}{fill}</svg>'


def _point(deg):
    rad = math.radians(deg)
    return f"{16 + 13 * math.sin(rad):.3f} {16 - 13 * math.cos(rad):.3f}"


def _arc(start, end):
    """Clockwise arc on the r=13 ring; angles in degrees from 12 o'clock."""
    large = 1 if end - start > 180 else 0
    return f"M{_point(start)}A13 13 0 {large} 1 {_point(end)}"


def segmented_gauge_svg(segments, theme="dark", style="facho"):
    """One ring segment per provider, clockwise from the top, in the given order.

    Each segment is filled to that provider's own reported fraction and takes
    its own color, so one exhausted provider never paints the whole ring.
    Unknown/stale segments stay neutral (dotted/dashed), never an empty arc.
    """
    segments = list(segments)
    if len(segments) < 2:
        raise ValueError("A segmented ring needs at least two segments")
    for pct, state in segments:
        _check(pct, state)
    fg, muted, colors = _palette(theme)
    count = len(segments)
    step = 360.0 / count
    gap = min(SEGMENT_GAP_DEG, step * 0.3)
    span = step - gap
    ring = ""
    for index, (pct, state) in enumerate(segments):
        start = index * step + gap / 2
        end = start + span
        if state in _KNOWN:
            accent = colors[state]
            ring += f'<path d="{_arc(start, end)}" fill="none" stroke="{accent}" stroke-width="3" opacity="0.25"/>'
            if pct > 0:
                ring += f'<path d="{_arc(start, start + span * pct / 100)}" fill="none" stroke="{accent}" stroke-width="3"/>'
        else:
            dash = "2 2" if state == "unknown" else "5 2"
            ring += f'<path d="{_arc(start, end)}" fill="none" stroke="{muted}" stroke-width="3" opacity="0.25"/>'
            ring += f'<path d="{_arc(start, end)}" fill="none" stroke="{muted}" stroke-width="3" stroke-dasharray="{dash}"/>'
    # Segments carry their own state; the center stays the neutral mark.
    center = _center("ok", style, fg, muted, fg)
    title = ", ".join(f"{state} {pct}" if state in _KNOWN else state for pct, state in segments)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><title>segments: {title}</title>{ring}{center}</svg>'


def _prune_icons(icons_dir, keep):
    """Drop the oldest generated ring files; the one just written is newest."""
    entries = []
    try:
        for entry in os.scandir(icons_dir):
            if entry.name.startswith("quota-") and entry.name.endswith(".svg") and entry.is_file(follow_symlinks=False):
                entries.append((entry.stat(follow_symlinks=False).st_mtime, entry.path))
    except OSError:
        return
    if len(entries) <= keep:
        return
    entries.sort(reverse=True)
    for _, path in entries[keep:]:
        try:
            os.unlink(path)
        except OSError:
            pass


def ensure_gauge_icon(icons_dir, pct=None, state="unknown", theme="dark", segments=None):
    """Write the ring once per distinct drawing; a new file name forces trays to reload it."""
    if segments is not None and len(segments) >= 2:
        svg = segmented_gauge_svg(segments, theme)
    else:
        svg = gauge_svg(pct, state, theme)
    digest = hashlib.sha256(svg.encode()).hexdigest()[:16]
    os.makedirs(icons_dir, exist_ok=True)
    path = os.path.join(icons_dir, f"quota-{state}-{pct if pct is not None else 'none'}-{digest}.svg")
    if os.path.exists(path):
        try:
            os.utime(path)  # keep the icon in use out of the pruning window
        except OSError:
            pass
        return path
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(svg)
    _prune_icons(icons_dir, ICON_CACHE_LIMIT)
    return path


def detect_gnome_theme():
    return detect_desktop_theme()


def ensure_icons(base_dir: str, theme: Optional[str] = None) -> dict[str, str]:
    theme = theme or detect_desktop_theme()
    directory = os.path.join(base_dir, "icons")
    return {key: ensure_gauge_icon(directory, pct, state, theme)
            for key, pct, state in (("neutral", None, "unknown"), ("ok", 30, "ok"),
                                    ("warning", 80, "warning"), ("critical", 95, "critical"))}
