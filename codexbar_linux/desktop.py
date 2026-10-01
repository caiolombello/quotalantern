"""Desktop integration helpers with no provider or network access."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Mapping, Optional, Sequence


def _command_output(command: Sequence[str], timeout: float = 3.0) -> Optional[str]:
    """Return stripped stdout for a successful local command."""
    if not command or shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip().strip("'").strip('"')
    return value or None


def current_desktop(env: Optional[Mapping[str, str]] = None) -> str:
    values = os.environ if env is None else env
    return (
        values.get("XDG_CURRENT_DESKTOP") or values.get("DESKTOP_SESSION") or "unknown"
    )


def is_plasma_session(env: Optional[Mapping[str, str]] = None) -> bool:
    desktop = current_desktop(env).lower()
    return "kde" in desktop or "plasma" in desktop


def _rgb_is_dark(value: Optional[str]) -> Optional[bool]:
    if not value:
        return None
    channels = re.findall(r"\d+", value)
    if len(channels) < 3:
        return None
    red, green, blue = (min(int(channel), 255) for channel in channels[:3])
    # ITU-R BT.709 perceptual brightness, scaled to 0..255.
    brightness = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return brightness < 128


def _kde_theme() -> Optional[str]:
    reader = shutil.which("kreadconfig6") or shutil.which("kreadconfig5")
    if not reader:
        return None

    background = _command_output(
        [
            reader,
            "--file",
            "kdeglobals",
            "--group",
            "Colors:View",
            "--key",
            "BackgroundNormal",
        ]
    )
    dark = _rgb_is_dark(background)
    if dark is not None:
        return "dark" if dark else "light"

    scheme = _command_output(
        [reader, "--file", "kdeglobals", "--group", "General", "--key", "ColorScheme"]
    )
    if scheme:
        return "dark" if "dark" in scheme.lower() else "light"
    return None


def detect_desktop_theme(env: Optional[Mapping[str, str]] = None) -> str:
    """Detect a dark/light preference on Plasma, GNOME, and generic GTK desktops."""
    values = os.environ if env is None else env
    override = values.get("CODEXBAR_THEME", "").strip().lower()
    if override in {"dark", "light"}:
        return override

    if is_plasma_session(values):
        kde = _kde_theme()
        if kde:
            return kde

    gtk_env = values.get("GTK_THEME", "").lower()
    if "dark" in gtk_env:
        return "dark"

    color_scheme = _command_output(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]
    )
    if color_scheme and "dark" in color_scheme.lower():
        return "dark"

    gtk_theme = _command_output(
        ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"]
    )
    if gtk_theme and "dark" in gtk_theme.lower():
        return "dark"
    return "light"


def status_notifier_available() -> bool:
    """Check for a StatusNotifier watcher without registering an item."""
    services = _command_output(
        ["busctl", "--user", "--no-pager", "--no-legend", "list"]
    )
    return bool(services and "org.kde.StatusNotifierWatcher" in services)


def private_file_status(path: Path) -> dict[str, object]:
    """Report only presence and permission safety; never read file contents."""
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return {"present": False, "private": False}
    return {"present": True, "private": mode & 0o077 == 0, "mode": f"{mode:04o}"}
