"""Opt-in passive quota export for Orla Desktop.

QuotaLantern owns collection and the export permission. A publication follows a
genuine refresh and carries, per enabled subscription provider, only a finite
state, the observation time and up to three windows (percent used, length and
reset time). Accounts, sign-in methods, provider messages, costs, credits and
balances are never exported.

Consent and export paths refuse symlinks, special files and directories that
are not private to the user. This does not sandbox QuotaLantern or protect
against another program running as the same user.
"""

from __future__ import annotations

import errno
import fcntl
import json
import math
import os
import re
import stat
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional

from . import __version__
from .core.models import ProviderUsage

FORMAT_VERSION = "0.1-experimental"
NAMESPACE = "quotalantern"
EXPORT_NAME = "orla-quota-v1.json"
CONSENT_NAME = "orla.json"
LOCK_NAME = "orla.lock"
MAX_CONSENT_BYTES = 1024
MAX_EXPORT_BYTES = 65536
MAX_PROVIDERS = 32
MAX_SAFE_INTEGER = 9007199254740991
WINDOW_KEYS = ("primary", "secondary", "tertiary")
_PROVIDER_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class ExportError(Exception):
    """Finite diagnostic code; never carries provider text or paths."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Paths:
    config_home: Path
    state_home: Path

    @classmethod
    def system(cls, environ: Optional[Mapping[str, str]] = None) -> "Paths":
        env = os.environ if environ is None else environ
        home = Path(env.get("HOME") or Path.home())

        def base(name: str, default: str) -> Path:
            value = env.get(name, "")
            return Path(value) if value and os.path.isabs(value) else home / default

        return cls(base("XDG_CONFIG_HOME", ".config"), base("XDG_STATE_HOME", ".local/state"))


# Projection ---------------------------------------------------------------


def _ms(value: object) -> Optional[int]:
    if not isinstance(value, datetime):
        return None
    try:
        result = int(value.timestamp() * 1000)
    except (OverflowError, OSError, ValueError):
        return None
    return result if 1 <= result <= MAX_SAFE_INTEGER else None


def _percent(value: object) -> Optional[int]:
    # Missing or invalid readings stay unknown, never zero; overuse reads as full.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return min(100, int(round(value)))


def _minutes(value: object) -> Optional[int]:
    return value if type(value) is int and 1 <= value <= 525600 else None


def _provider(usage: ProviderUsage, now: int, lease: int) -> dict:
    windows = [(key, window) for key, window in zip(WINDOW_KEYS, (usage.primary, usage.secondary, usage.tertiary))
               if window is not None]
    observed = _ms(usage.updated_at)
    if observed is not None and observed > now:
        observed = None
    if usage.error:
        # A failed refresh may still show cached windows; they are no longer confirmed.
        state = "stale" if windows else "unavailable"
    elif not any(_percent(window.used_percent) is not None for _, window in windows):
        state = "unknown"
    elif observed is None or now - observed >= lease:
        state = "stale"
    else:
        state = "ready"
    return {
        "id": usage.provider,
        "state": state,
        "observedAtMs": None if state == "unavailable" else observed,
        "windows": [] if state == "unavailable" else [
            {
                "key": key,
                "usedPercent": _percent(window.used_percent) if state == "ready" else None,
                "windowMinutes": _minutes(window.window_minutes),
                "resetAtMs": _ms(window.resets_at),
            }
            for key, window in windows
        ],
    }


def _subscription_ids() -> set[str]:
    from .providers import PROVIDER_BY_ID

    return {pid for pid, spec in PROVIDER_BY_ID.items() if spec.category == "subscription"}


def project(
    usages: Iterable[ProviderUsage],
    *,
    interval_seconds: int,
    now_ms: Optional[int] = None,
    producer_version: str = __version__,
    subscription_ids: Optional[set[str]] = None,
) -> dict:
    """Orla quota-usage envelope; the lease covers three refresh intervals."""
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    interval = min(3600, max(10, int(interval_seconds)))
    allowed = _subscription_ids() if subscription_ids is None else subscription_ids
    providers: list[dict] = []
    seen: set[str] = set()
    for usage in usages:
        pid = usage.provider
        if not isinstance(pid, str) or pid in seen or pid not in allowed or not _PROVIDER_ID.fullmatch(pid):
            continue
        seen.add(pid)
        providers.append(_provider(usage, now, 3000 * interval))
        if len(providers) == MAX_PROVIDERS:
            break
    return {
        "formatVersion": FORMAT_VERSION,
        "producer": NAMESPACE,
        "producerVersion": producer_version,
        "generatedAtMs": now,
        "expiresAtMs": now + 3000 * interval,
        "intervalSeconds": interval,
        "providers": providers,
    }


def encode(value: dict) -> bytes:
    data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    if len(data) > MAX_EXPORT_BYTES:
        raise ExportError("size")
    return data


# Private files ------------------------------------------------------------


def _private_dir(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ExportError("unsafe")


def _open_exports(base: Path, create: bool) -> Optional[int]:
    """Open <base>/quotalantern/exports without following links; None if absent."""
    try:
        if create:
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError:
        raise ExportError("io") from None
    try:
        for part in (NAMESPACE, "exports"):
            created = False
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                return None
            except OSError as error:
                raise ExportError("unsafe" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "io") from None
            os.close(fd)
            fd = child
            if created:
                os.fchmod(fd, 0o700)
            _private_dir(fd)
        result, fd = fd, -1
        return result
    except OSError:
        raise ExportError("io") from None
    finally:
        if fd >= 0:
            os.close(fd)


@contextmanager
def _exports(base: Path, create: bool) -> Iterator[Optional[int]]:
    fd = _open_exports(base, create)
    try:
        yield fd
    finally:
        if fd is not None:
            os.close(fd)


def _entry(fd: int, name: str) -> Optional[os.stat_result]:
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise ExportError("io") from None


def _private_file(info: os.stat_result) -> bool:
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600)


def _unique(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite")


def _consent(fd: Optional[int]) -> bool:
    """Strict and fail-closed: anything but the exact enabled record is off."""
    if fd is None:
        return False
    try:
        file_fd = os.open(CONSENT_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    except OSError:
        return False
    try:
        info = os.fstat(file_fd)
        if not _private_file(info) or info.st_size > MAX_CONSENT_BYTES:
            return False
        data = os.read(file_fd, MAX_CONSENT_BYTES + 1)
    except OSError:
        return False
    finally:
        os.close(file_fd)
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_reject_constant)
    except ValueError:
        return False
    return (type(value) is dict and set(value) == {"formatVersion", "enabled"}
            and value["formatVersion"] == FORMAT_VERSION and value["enabled"] is True)


def _write(fd: int, name: str, data: bytes) -> None:
    """Exclusive temporary file, fsync, same-directory replace, directory fsync."""
    info = _entry(fd, name)
    if info is not None and not _private_file(info):
        raise ExportError("unsafe")
    temporary = f".{name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    renamed = False
    try:
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                          0o600, dir_fd=fd)
        try:
            os.fchmod(file_fd, 0o600)
            view = memoryview(data)
            while view:
                view = view[os.write(file_fd, view):]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        renamed = True
        os.fsync(fd)
    except OSError:
        raise ExportError("io") from None
    finally:
        if not renamed:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=fd)


def _remove(fd: Optional[int], name: str) -> None:
    # Unlinking a symlink or special file never touches what it points to.
    if fd is None:
        return
    info = _entry(fd, name)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode):
        raise ExportError("unsafe")
    try:
        os.unlink(name, dir_fd=fd)
        os.fsync(fd)
    except FileNotFoundError:
        return
    except OSError:
        raise ExportError("io") from None


@contextmanager
def _locked(fd: int, timeout: float) -> Iterator[None]:
    """Serialize consent changes with publication across processes."""
    try:
        lock_fd = os.open(LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                          0o600, dir_fd=fd)
    except OSError as error:
        raise ExportError("unsafe" if error.errno == errno.ELOOP else "io") from None
    try:
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ExportError("unsafe")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ExportError("busy") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(lock_fd)


def _remove_export(paths: Paths) -> None:
    with _exports(paths.state_home, False) as state:
        _remove(state, EXPORT_NAME)


# Public operations --------------------------------------------------------


def status(paths: Optional[Paths] = None) -> dict:
    """Read-only; never creates directories."""
    paths = paths or Paths.system()
    with _exports(paths.config_home, False) as config:
        enabled = _consent(config)
    with _exports(paths.state_home, False) as state:
        info = _entry(state, EXPORT_NAME) if state is not None else None
    present = info is not None and stat.S_ISREG(info.st_mode)
    return {"enabled": enabled, "exportPresent": present, "exportBytes": info.st_size if present else None}


def command(action: str, paths: Optional[Paths] = None, *, timeout: float = 5.0) -> dict:
    """enable/disable/status manage consent only; they never fetch providers or publish."""
    paths = paths or Paths.system()
    if action == "status":
        return status(paths)
    if action not in ("enable", "disable"):
        raise ExportError("format")
    enable = action == "enable"
    with _exports(paths.config_home, enable) as config:
        if config is None:
            # Never enabled: nothing can publish, but clear any stray export.
            _remove_export(paths)
            return status(paths)
        with _locked(config, timeout):
            record = {"formatVersion": FORMAT_VERSION, "enabled": enable}
            _write(config, CONSENT_NAME, json.dumps(record, separators=(",", ":")).encode("ascii"))
            # Fresh consent never republishes an old reading; the next refresh does.
            _remove_export(paths)
    return status(paths)


def publish(
    usages: Iterable[ProviderUsage],
    *,
    interval_seconds: int,
    paths: Optional[Paths] = None,
    now_ms: Optional[int] = None,
    timeout: float = 5.0,
) -> bool:
    """After a genuine refresh: publish with consent, otherwise remove any old export."""
    paths = paths or Paths.system()
    with _exports(paths.config_home, False) as config:
        if config is None:
            _remove_export(paths)
            return False
        with _locked(config, timeout):
            if not _consent(config):
                _remove_export(paths)
                return False
            try:
                data = encode(project(usages, interval_seconds=interval_seconds, now_ms=now_ms))
                with _exports(paths.state_home, True) as state:
                    _write(state, EXPORT_NAME, data)
            except ExportError:
                # A refused publication never leaves an older reading in place.
                with suppress(ExportError):
                    _remove_export(paths)
                raise
    return True
