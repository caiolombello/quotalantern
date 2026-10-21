"""Launch the installed tray independently of the terminal, with one instance."""
from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
import subprocess
import sys


@contextlib.contextmanager
def instance_lock():
    base = Path.home() / ".local/share/codexbar-linux"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (base / ".instance.lock").open("a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield lock


def launch_background(verbose: bool = False) -> int:
    with instance_lock() as lock:
        if lock is None:
            print("QuotaLantern is already running.")
            return 0

    log_dir = Path.home() / ".local/share/codexbar-linux/log"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_dir.chmod(0o700)
    log_path = log_dir / "launcher.log"
    if log_path.exists() and log_path.stat().st_size > 1024 * 1024:
        log_path.replace(log_dir / "launcher.log.1")
    command = [sys.executable, "-m", "codexbar_linux.launcher", "--foreground"]
    if verbose:
        command.append("--verbose")
    with log_path.open("a") as log:
        os.fchmod(log.fileno(), 0o600)
        child = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True,
            env={**os.environ, "QUOTALANTERN_BACKGROUND": "1"},
        )
    try:
        code = child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        print(f"QuotaLantern started (PID {child.pid}). Logs: {log_dir}")
        return 0
    if code == 0:
        print("QuotaLantern is already running.")
        return 0
    print(f"QuotaLantern could not start. See {log_path}", file=sys.stderr)
    return 1


def main() -> int:
    from .__main__ import argument_parser, main as application_main

    args = argument_parser().parse_args()
    if args.check or args.json:
        application_main()
        return 0
    if not args.foreground:
        return launch_background(verbose=args.verbose)
    with instance_lock() as lock:
        if lock is None:
            print("QuotaLantern is already running.")
            return 0
        application_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
