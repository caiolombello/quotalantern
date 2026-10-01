"""Structured logging with file rotation and thread exception hook."""

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from typing import Optional

LOG_DIR = Path.home() / ".local/share/codexbar-linux/log"
LOG_PATH = LOG_DIR / "codexbar-linux.log"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 3


class SecureRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating handler that keeps every newly-created log private."""

    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


def setup_logging(
    verbose: bool = False,
    log_dir: Optional[Path] = None,
) -> logging.Logger:
    """Configure root logger for codexbar-linux with rotation.

    - Console handler: stderr, INFO (or DEBUG if verbose)
    - File handler: rotating, DEBUG level
    """
    root = logging.getLogger("codexbar-linux")
    root.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if called multiple times
    if root.handlers:
        root.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Rotating file
    directory = log_dir or LOG_DIR
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    file_handler = SecureRotatingFileHandler(
        str(directory / "codexbar-linux.log"),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    return root


def _thread_exception_handler(args: threading.ExceptHookArgs) -> None:
    """Log uncaught exceptions from daemon threads instead of printing to stderr."""
    logger = logging.getLogger("codexbar-linux")
    if args.exc_type is None or args.exc_value is None:
        return
    logger.critical(
        "Uncaught exception in thread %s: %s: %s",
        args.thread.name if args.thread else "unknown",
        args.exc_type.__name__,
        args.exc_value,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def install_thread_exception_hook() -> None:
    """Install global thread exception hook (Python 3.8+)."""
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_exception_handler
