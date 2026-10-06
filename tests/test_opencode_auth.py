import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from codexbar_linux import opencode_auth as auth


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "opencode"
        self.path = self.directory / "oauth.json"
        self.patch = mock.patch.object(auth, "SESSION_DIR", self.directory)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.patch2 = mock.patch.object(auth, "SESSION_PATH", self.path)
        self.patch2.start()
        self.addCleanup(self.patch2.stop)

    def response(self, payload):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode()
        response.status = 200
        return response

    def test_begin_login_posts_form_and_resolves_trusted_relative_url(self):
        payload = dict(device_code="synthetic-device", user_code="ABCD-EFGH",
                       verification_uri_complete="/console/device?user_code=ABCD-EFGH",
                       expires_in=600, interval=5)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(payload)) as http:
            attempt = auth.begin_login()
        self.assertEqual(attempt.verification_url, "https://opencode.ai/console/device?user_code=ABCD-EFGH")
        request = http.call_args.args[0]
        self.assertEqual(request.full_url, "https://opencode.ai/console/auth/device/code")
        self.assertEqual(request.data, b"client_id=quotalantern&supports_org_scope=true")
        self.assertNotIn("synthetic-device", repr(attempt))

    def test_device_and_refresh_requests_identify_application(self):
        device = dict(device_code="synthetic-device", user_code="ABCD-EFGH",
                      verification_uri_complete="/console/device?user_code=ABCD-EFGH",
                      expires_in=600, interval=5)
        responses = iter([device, self.token(10), self.token(refresh="rotated")])

        def server(request, *args, **kwargs):
            if request.get_header("User-agent") != "QuotaLantern/0.2":
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", {},
                    io.BytesIO(b'{"error":"Forbidden"}'),
                )
            return self.response(next(responses))

        with mock.patch("urllib.request.OpenerDirector.open", side_effect=server) as http, mock.patch("time.sleep"):
            attempt = auth.begin_login()
            auth.finish_login(attempt)
            session = auth.get_session()

        self.assertEqual(session.refresh_token, "rotated")
        self.assertEqual(http.call_count, 3)
        self.assertEqual(
            [call.args[0].full_url for call in http.call_args_list],
            ["https://opencode.ai/console/auth/device/code",
             "https://opencode.ai/console/auth/device/token",
             "https://opencode.ai/console/auth/device/token"],
        )

    def token(self, expiry=3600, refresh="synthetic-refresh"):
        return dict(access_token="synthetic-access", refresh_token=refresh, expires_in=expiry, org_id="wrk_SYNTHETIC")

    def login(self, expiry=3600):
        attempt = auth.DeviceLogin("https://opencode.ai/console/device", "ABCD", "synthetic-device", 9999999999, 1)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(self.token(expiry))), mock.patch("time.sleep"):
            auth.finish_login(attempt)

    def test_device_success_persists_private_workspace_bound_session(self):
        self.login()
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(auth.login_status(), "connected")
        with mock.patch("urllib.request.OpenerDirector.open") as http:
            session = auth.get_session()
            http.assert_not_called()
        self.assertEqual(session.org_id, "wrk_SYNTHETIC")
        self.assertNotIn("synthetic-access", repr(session))
        auth.disconnect()
        self.assertEqual(auth.login_status(), "disconnected")

    def test_refresh_transport_failure_marks_pending_before_request_and_never_replays(self):
        self.login(10)
        def fail(*args, **kwargs):
            self.assertTrue(json.loads(self.path.read_text())["refresh_pending"])
            raise urllib.error.URLError("synthetic-refresh private URL secret")
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=fail) as http:
            with self.assertRaises(auth.OAuthError) as raised:
                auth.get_session()
            self.assertNotIn("synthetic-refresh", str(raised.exception))
            with self.assertRaises(auth.OAuthError):
                auth.get_session()
            self.assertEqual(http.call_count, 1)
        self.assertEqual(auth.login_status(), "reconnect_required")

    def test_refresh_success_rotates_and_preserves_workspace(self):
        self.login(10)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(self.token(refresh="rotated"))) as http:
            self.assertEqual(auth.get_session().refresh_token, "rotated")
            self.assertEqual(auth.get_session().refresh_token, "rotated")
            self.assertEqual(http.call_count, 1)
        self.assertFalse(json.loads(self.path.read_text())["refresh_pending"])

    def test_untrusted_verification_urls_are_rejected_without_leaking_codes(self):
        for url in ["https://evil.example/console/device?secret", "//evil.example/console/device",
                    "http://opencode.ai/console/device", "https://opencode.ai@evil.example/console/device"]:
            with self.subTest(url=url), mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(dict(
                device_code="synthetic-device", user_code="ABCD", verification_uri_complete=url, expires_in=600, interval=5))):
                with self.assertRaises(auth.OAuthError) as raised:
                    auth.begin_login()
                self.assertNotIn(url, str(raised.exception))

    def test_no_redirects_are_followed(self):
        with self.assertRaises(auth.OAuthError):
            auth._NoRedirect().redirect_request(None, None, 307, "redirect", {}, "https://evil.example/token")

    def test_cancellation_is_offline_and_saves_nothing(self):
        import threading
        cancel = threading.Event()
        cancel.set()
        attempt = auth.DeviceLogin("https://opencode.ai/console/device", "ABCD", "synthetic-device", 9999999999, 5)
        with mock.patch("urllib.request.OpenerDirector.open") as http:
            with self.assertRaises(auth.OAuthError):
                auth.finish_login(attempt, cancel)
            http.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_pending_and_slow_down_respect_intervals(self):
        attempt = auth.DeviceLogin("https://opencode.ai/console/device", "ABCD", "synthetic-device", 9999999999, 5)
        pending = urllib.error.HTTPError("private-url", 400, "private-text", {}, io.BytesIO(b"{\"error\":\"authorization_pending\"}"))
        slow = urllib.error.HTTPError("private-url", 400, "private-text", {}, io.BytesIO(b"{\"error\":\"slow_down\"}"))
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=[pending, slow, self.response(self.token())]), mock.patch("time.sleep") as sleep:
            auth.finish_login(attempt)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 5, 10])
        self.assertEqual(auth.login_status(), "connected")

    def test_device_rejection_or_expiry_does_not_save(self):
        for error in ["access_denied", "expired_token"]:
            attempt = auth.DeviceLogin("https://opencode.ai/console/device", "ABCD", "synthetic-device", 9999999999, 1)
            rejected = urllib.error.HTTPError("private-url", 400, "private-text", {}, io.BytesIO(json.dumps({"error": error}).encode()))
            with mock.patch("urllib.request.OpenerDirector.open", side_effect=rejected), mock.patch("time.sleep"):
                with self.assertRaises(auth.OAuthError):
                    auth.finish_login(attempt)
        self.assertFalse(self.path.exists())
        with mock.patch("urllib.request.OpenerDirector.open") as http:
            with self.assertRaises(auth.OAuthError):
                auth.finish_login(auth.DeviceLogin("https://opencode.ai/console/device", "ABCD", "synthetic-device", 1, 5))
            http.assert_not_called()

    def test_missing_workspace_and_header_injection_are_rejected(self):
        for org in [None, "", "wrk_BAD\r\nheader", "unknown"]:
            with mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(dict(self.token(), org_id=org))), mock.patch("time.sleep"):
                with self.assertRaises(auth.OAuthError):
                    auth.finish_login(auth.DeviceLogin("https://opencode.ai/console/device", "ABCD", "synthetic-device", 9999999999, 1))
        self.assertFalse(self.path.exists())

    def test_permissive_files_and_symlinks_are_refused(self):
        self.login()
        self.path.chmod(0o644)
        with self.assertRaises(auth.OAuthError):
            auth.get_session()
        self.path.chmod(0o600)
        target = self.path.with_name("other.json")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(auth.OAuthError):
            auth.get_session()
        self.assertEqual(auth.login_status(), "reconnect_required")
        self.path.unlink()
        self.directory.chmod(0o755)
        with self.assertRaises(auth.OAuthError):
            auth.get_session()

    def test_uncertain_refresh_save_cannot_replay_old_token(self):
        import os
        self.login(10)
        real_replace = os.replace
        counter = 0
        def replacement(*args, **kwargs):
            nonlocal counter
            counter += 1
            if counter > 1:
                raise OSError("private-token")
            return real_replace(*args, **kwargs)
        with mock.patch("os.replace", side_effect=replacement), mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(self.token(refresh="rotated"))) as http:
            with self.assertRaises(auth.OAuthError):
                auth.get_session()
            with self.assertRaises(auth.OAuthError):
                auth.get_session()
            self.assertEqual(http.call_count, 1)
        self.assertEqual(auth.login_status(), "reconnect_required")

    def test_threads_share_single_use_refresh(self):
        from concurrent.futures import ThreadPoolExecutor
        self.login(10)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(self.token(refresh="rotated"))) as http:
            with ThreadPoolExecutor(max_workers=4) as pool:
                sessions = list(pool.map(lambda _: auth.get_session(), range(4)))
            self.assertEqual(http.call_count, 1)
        self.assertTrue(all(session.refresh_token == "rotated" for session in sessions))

    def test_server_reusing_consumed_refresh_token_requires_reconnect(self):
        self.login(10)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(self.token())) as http:
            with self.assertRaises(auth.OAuthError):
                auth.get_session()
            with self.assertRaises(auth.OAuthError):
                auth.get_session()
            self.assertEqual(http.call_count, 1)
        self.assertEqual(auth.login_status(), "reconnect_required")

    def test_failed_pending_save_never_sends_refresh(self):
        self.login(10)
        with mock.patch("os.replace", side_effect=OSError("synthetic-private")), mock.patch("urllib.request.OpenerDirector.open") as http:
            with self.assertRaises(auth.OAuthError):
                auth.get_session()
            http.assert_not_called()

    def test_processes_share_single_use_refresh(self):
        import os
        self.login(10)
        read_fd, write_fd = os.pipe()
        def response(*args, **kwargs):
            os.write(write_fd, b"R")
            return self.response(self.token(refresh="rotated"))
        children = []
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=response):
            for _ in range(3):
                pid = os.fork()
                if pid == 0:
                    os.close(read_fd)
                    try:
                        session = auth.get_session()
                        os.write(write_fd, b"S" if session.refresh_token == "rotated" else b"E")
                    except Exception:
                        os.write(write_fd, b"E")
                    finally:
                        os.close(write_fd)
                        os._exit(0)
                children.append(pid)
        os.close(write_fd)
        try:
            result = b""
            while True:
                chunk = os.read(read_fd, 1024)
                if not chunk:
                    break
                result += chunk
        finally:
            os.close(read_fd)
            for pid in children:
                os.waitpid(pid, 0)
        self.assertEqual(result.count(b"R"), 1)
        self.assertEqual(result.count(b"S"), 3)
        self.assertNotIn(b"E", result)

    def test_cancellation_while_waiting_storage_lock_cannot_save(self):
        import threading
        from contextlib import contextmanager
        cancel = threading.Event()
        waiting = threading.Event()
        release = threading.Event()
        actual_storage = auth._storage
        @contextmanager
        def delayed_storage(*args, **kwargs):
            waiting.set()
            self.assertTrue(release.wait(5))
            with actual_storage(*args, **kwargs) as directory:
                yield directory
        attempt = auth.DeviceLogin("https://opencode.ai/console/device", "ABCD", "synthetic-device", 9999999999, 1)
        errors = []
        def finish():
            try:
                auth.finish_login(attempt, cancel)
            except auth.OAuthError as error:
                errors.append(error)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=self.response(self.token())), mock.patch.object(auth, "_storage", delayed_storage), mock.patch.object(cancel, "wait", return_value=False):
            worker = threading.Thread(target=finish)
            worker.start()
            try:
                self.assertTrue(waiting.wait(5))
                cancel.set()
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertFalse(self.path.exists())
