"""Workspace-bound OpenCode OAuth, independent of CLI credentials and GTK."""

from dataclasses import dataclass, field
import json
import fcntl
import hmac
import os
import secrets
import stat
from contextlib import contextmanager
import math
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


CONSOLE_URL = "https://opencode.ai/console"
CLIENT_ID = "quotalantern"
USER_AGENT = "QuotaLantern/0.2"
SESSION_DIR = Path.home() / ".local/share/codexbar-linux/opencode"
SESSION_PATH = SESSION_DIR / "oauth.json"


class OAuthError(Exception):
    """A safe message suitable for displaying to the user."""


@dataclass(frozen=True)
class DeviceLogin:
    verification_url: str = field(repr=False)
    user_code: str
    device_code: str = field(repr=False)
    expires_at: float
    interval: float


@dataclass(frozen=True)
class OAuthSession:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    org_id: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OAuthError("OpenCode redirect rejected. Reconnect your account.")


def _post(form: dict, endpoint: str) -> dict:
    request = urllib.request.Request(
        CONSOLE_URL + endpoint,
        data=urllib.parse.urlencode(form).encode("ascii"),
        # Console rejects urllib's default agent before the OAuth flow starts.
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise OAuthError("OpenCode authorization failed. Reconnect your account.")
            payload = json.loads(response.read(65537))
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            exc.close()
            raise OAuthError("OpenCode authorization failed. Reconnect your account.") from None
        try:
            payload = json.loads(exc.read(65537))
        except (ValueError, OSError):
            raise OAuthError("Invalid OpenCode authorization response.") from None
        finally:
            exc.close()
        if not isinstance(payload, dict) or payload.get("error") not in {
            "authorization_pending", "slow_down", "access_denied", "expired_token",
        }:
            raise OAuthError("OpenCode authorization failed. Reconnect your account.") from None
        return {"error": payload["error"]}
    except OAuthError:
        raise
    except (OSError, ValueError, urllib.error.URLError):
        raise OAuthError("Could not complete OpenCode authorization. Reconnect your account.") from None
    if not isinstance(payload, dict):
        raise OAuthError("Invalid OpenCode authorization response.")
    return payload


def _text(value, max_length=16384):
    if not isinstance(value, str) or not value or len(value) > max_length or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise OAuthError("Invalid OpenCode authorization response.")
    return value


def _seconds(value, maximum=86400 * 365):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise OAuthError("Invalid OpenCode authorization response.")
    return float(value)


def begin_login() -> DeviceLogin:
    payload = _post({"client_id": CLIENT_ID, "supports_org_scope": "true"}, "/auth/device/code")
    url = urllib.parse.urljoin("https://opencode.ai", _text(payload.get("verification_uri_complete"), 4096))
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "opencode.ai" or not parsed.path.startswith("/console/") or parsed.fragment:
        raise OAuthError("Untrusted OpenCode verification URL.")
    code = _text(payload.get("user_code"), 128)
    if not re.fullmatch(r'[A-Za-z0-9-]+', code):
        raise OAuthError("Invalid OpenCode authorization response.")
    return DeviceLogin(url, code, _text(payload.get("device_code")), time.time() + _seconds(payload.get("expires_in"), 3600), _seconds(payload.get("interval"), 300))


# flock on the dedicated directory serializes independent processes; the RLock
# also serializes threads (flock alone is insufficient for shared descriptors).
_LOCK = threading.RLock()


def _private(info, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OAuthError("OpenCode storage is not private. Reconnect after fixing its permissions.")
    if not directory and info.st_nlink != 1:
        raise OAuthError("OpenCode storage is unsafe. Reconnect your account.")


@contextmanager
def _storage(create=False):
    with _LOCK:
        descriptor = None
        try:
            # Open every component without following symlinks, then pin the
            # dedicated directory descriptor for all reads and atomic writes.
            directory = SESSION_DIR.absolute()
            descriptor = os.open(directory.anchor, os.O_RDONLY | os.O_DIRECTORY)
            for index, component in enumerate(directory.parts[1:]):
                final = index == len(directory.parts) - 2
                try:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        yield None
                        return
                    try:
                        os.mkdir(component, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                if final:
                    _private(os.fstat(descriptor), directory=True)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield descriptor
        except OAuthError:
            raise
        except (OSError, ValueError):
            raise OAuthError("Could not access private OpenCode storage. Reconnect your account.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _read(directory):
    if directory is None:
        return None
    try:
        fd = os.open(SESSION_PATH.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        _private(os.fstat(stream.fileno()))
        try:
            payload = json.loads(stream.read(65537))
        except (ValueError, UnicodeError):
            raise OAuthError("Invalid OpenCode session. Reconnect your account.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("refresh_pending"), bool):
        raise OAuthError("Invalid OpenCode session. Reconnect your account.")
    return payload


def _write(directory, payload):
    # Refuse unsafe existing targets even though replace would not follow them.
    try:
        _private(os.stat(SESSION_PATH.name, dir_fd=directory, follow_symlinks=False))
    except FileNotFoundError:
        pass
    name = ".oauth-" + secrets.token_hex(16)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, SESSION_PATH.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def _session(payload, response=False):
    org_id = _text(payload.get("org_id"), 128)
    if not re.fullmatch(r'(?:org|wrk)_[A-Za-z0-9]+', org_id):
        raise OAuthError("OpenCode did not bind this session to a workspace. Reconnect your account.")
    expires = time.time() + _seconds(payload.get("expires_in")) if response else _seconds(payload.get("expires_at"), 1e12)
    return OAuthSession(_text(payload.get("access_token")), _text(payload.get("refresh_token")), expires, org_id)


def _payload(session, pending=False):
    return dict(access_token=session.access_token, refresh_token=session.refresh_token,
                expires_at=session.expires_at, org_id=session.org_id, refresh_pending=pending)


def finish_login(attempt: DeviceLogin, cancel: threading.Event | None = None) -> None:
    interval = _seconds(attempt.interval, 300)
    device_code = _text(attempt.device_code)
    _seconds(attempt.expires_at, 1e12)
    # Wall time guards server expiration, monotonic time bounds the attempt even
    # if the wall clock moves backwards while the dialog is open.
    deadline = time.monotonic() + max(0, min(3600, attempt.expires_at - time.time()))
    while time.time() < attempt.expires_at and time.monotonic() < deadline:
        wait = min(interval, attempt.expires_at - time.time(), deadline - time.monotonic())
        if cancel is not None:
            if cancel.wait(max(0, wait)):
                raise OAuthError("OpenCode connection cancelled.")
        else:
            time.sleep(max(0, wait))
        if time.time() >= attempt.expires_at or time.monotonic() >= deadline:
            break
        payload = _post({"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                         "device_code": device_code, "client_id": CLIENT_ID}, "/auth/device/token")
        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error:
            raise OAuthError("OpenCode connection denied or expired. Start a new connection.")
        session = _session(payload, response=True)
        if cancel is not None and cancel.is_set():
            raise OAuthError("OpenCode connection cancelled.")
        with _storage(create=True) as directory:
            if cancel is not None and cancel.is_set():
                raise OAuthError("OpenCode connection cancelled.")
            # Cancellation is honored until this atomic save starts. A normal
            # return confirms the committed session, including a late cancel.
            _write(directory, _payload(session))
        return
    raise OAuthError("OpenCode connection expired. Start a new connection.")


def get_session() -> OAuthSession:
    with _storage() as directory:
        payload = _read(directory)
        if payload is None:
            raise OAuthError("Connect your OpenCode account in Settings.")
        if payload["refresh_pending"]:
            raise OAuthError("OpenCode needs to reconnect after an incomplete refresh.")
        session = _session(payload)
        if session.expires_at > time.time() + 300:
            return session
        # Durably fence the old single-use token BEFORE any HTTP request. Every
        # uncertain failure leaves this marker, so no later caller can replay it.
        _write(directory, _payload(session, pending=True))
        refreshed = _session(_post({"grant_type": "refresh_token", "refresh_token": session.refresh_token,
                                   "client_id": CLIENT_ID}, "/auth/device/token"), response=True)
        if hmac.compare_digest(refreshed.refresh_token.encode(), session.refresh_token.encode()):
            raise OAuthError("OpenCode did not rotate the refresh token. Reconnect your account.")
        if refreshed.org_id != session.org_id:
            raise OAuthError("OpenCode workspace changed unexpectedly. Reconnect your account.")
        try:
            _write(directory, _payload(refreshed))
        except OSError:
            # The old token is already fenced. If replacement completed before
            # fsync failed, fence the new one too rather than trusting durability.
            try:
                _write(directory, _payload(refreshed, pending=True))
            except (OSError, OAuthError):
                pass
            raise OAuthError("Could not save OpenCode refresh. Reconnect your account.") from None
        return refreshed


def login_status() -> str:
    """Offline status only; never consumes a refresh token."""
    try:
        with _storage() as directory:
            payload = _read(directory)
            if payload is None:
                return "disconnected"
            if payload["refresh_pending"]:
                return "reconnect_required"
            _session(payload)
            return "connected"
    except OAuthError:
        return "reconnect_required"


def disconnect() -> None:
    """Remove only this application's session; no remote revoke or CLI writes."""
    with _storage() as directory:
        if directory is None:
            return
        if _read(directory) is not None:
            os.unlink(SESSION_PATH.name, dir_fd=directory)
            os.fsync(directory)
