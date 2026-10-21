"""Offline platform checks for CodexBar Linux."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any

from .gtk_runtime import runtime_status
from .desktop import (
    current_desktop,
    detect_desktop_theme,
    is_plasma_session,
    private_file_status,
    status_notifier_available,
)


def _gi_status() -> dict[str, Any]:
    return runtime_status()


def platform_report() -> dict[str, Any]:
    """Build a local-only report. This never reads credential contents or uses the network."""
    gi_status = _gi_status()
    watcher = status_notifier_available()
    plasma = is_plasma_session()
    report: dict[str, Any] = {
        "desktop": current_desktop(),
        "plasma": plasma,
        "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "theme": detect_desktop_theme(),
        "python": platform.python_version(),
        "runtime": gi_status,
        "status_notifier_watcher": watcher,
        "commands": {
            "codex": shutil.which("codex") is not None,
            "xdg-open": shutil.which("xdg-open") is not None,
        },
        "files": {
            "codex_auth": private_file_status(Path.home() / ".codex" / "auth.json"),
            "config": private_file_status(
                Path.home() / ".config" / "codexbar-linux" / "config.json"
            ),
        },
    }
    required = (
        gi_status["gtk3"],
        gi_status["appindicator"],
        gi_status["notify"],
        report["commands"]["xdg-open"],
    )
    report["ready"] = all(required)
    if plasma:
        report["ready"] = bool(report["ready"] and watcher)
    return report
