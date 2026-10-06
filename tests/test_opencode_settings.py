"""GTK interaction checks; OAuth and browser boundaries never use real credentials."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
except (ImportError, ValueError):
    settings_dialog = None
else:
    from codexbar_linux import settings_dialog


@unittest.skipIf(settings_dialog is None, "GTK bindings unavailable")
class OpenCodeSettingsTests(unittest.TestCase):
    def setUp(self):
        if not Gtk.init_check()[0]:
            self.skipTest("GTK display unavailable")
        self.finished = threading.Event()
        self.started = threading.Event()
        self.status = "disconnected"

        def finish(_attempt, cancel=None):
            self.started.set()
            while not self.finished.wait(0.01):
                if cancel.is_set():
                    raise self.auth.OAuthError("Sign-in cancelled.")
            self.status = "connected"

        self.auth = SimpleNamespace(
            OAuthError=type("OAuthError", (Exception,), {}),
            login_status=mock.Mock(side_effect=lambda: self.status),
            begin_login=mock.Mock(return_value=SimpleNamespace(
                verification_url="https://opencode.ai/test-verification",
                user_code="TEST-CODE",
            )),
            finish_login=mock.Mock(side_effect=finish),
            disconnect=mock.Mock(),
        )
        self.patches = [
            mock.patch.object(settings_dialog, "opencode_auth", self.auth, create=True),
            mock.patch.object(settings_dialog.subprocess, "Popen"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.refresh = mock.Mock()
        self.view = settings_dialog.SettingsDialog(
            None, settings_dialog.AppConfig(), mock.Mock(), self.refresh,
        )
        self.addCleanup(self.view.dialog.destroy)
        self.addCleanup(self.finished.set)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if predicate():
                return
            time.sleep(0.005)
        self.fail("GTK OAuth interaction did not complete")

    def test_sign_in_requires_click_then_refreshes_once_on_success(self):
        self.auth.begin_login.assert_not_called()
        self.view._opencode_sign_in.emit("clicked")
        self.wait_for(lambda: "TEST-CODE" in self.view._opencode_progress.get_text())
        self.assertFalse(self.view._opencode_sign_in.get_sensitive())
        self.finished.set()
        self.wait_for(lambda: self.refresh.call_count == 1)
        self.assertEqual(self.view._opencode_status.get_text(), "Connected")
        self.assertEqual(self.view._opencode_sign_in.get_label(), "Reconnect")
        self.assertEqual(self.view._opencode_progress.get_text(), "")

    def test_cancel_stops_polling_and_allows_retry_without_refresh(self):
        self.view._opencode_sign_in.emit("clicked")
        self.wait_for(self.started.is_set)
        self.view._opencode_cancel.emit("clicked")
        self.wait_for(lambda: self.view._opencode_sign_in.get_sensitive())
        self.refresh.assert_not_called()
        self.assertNotIn("TEST-CODE", self.view._opencode_progress.get_text())

    def test_cancellation_after_session_commit_still_reports_success(self):
        def finish(_attempt, cancel=None):
            self.status = "connected"
            cancel.set()

        self.auth.finish_login.side_effect = finish
        self.view._opencode_sign_in.emit("clicked")
        self.wait_for(lambda: self.view._opencode_sign_in.get_sensitive())
        self.assertEqual(self.view._opencode_status.get_text(), "Connected")
        self.assertEqual(self.view._opencode_progress.get_text(), "")
        self.refresh.assert_called_once_with()

    def test_destroyed_dialog_cancels_pending_login_without_refresh(self):
        self.view._opencode_sign_in.emit("clicked")
        self.wait_for(self.started.is_set)
        self.view.dialog.destroy()
        self.assertTrue(self.auth.finish_login.call_args.kwargs["cancel"].is_set())
        self.finished.set()
        self.wait_for(lambda: self.auth.finish_login.call_count == 1)
        self.refresh.assert_not_called()

    def test_unexpected_error_is_safe_and_sign_in_can_be_retried(self):
        self.auth.begin_login.side_effect = RuntimeError("sensitive-response-body")
        with self.assertLogs("codexbar-linux", level="WARNING") as logs:
            self.view._opencode_sign_in.emit("clicked")
            self.wait_for(lambda: self.view._opencode_sign_in.get_sensitive())
        self.assertEqual(
            self.view._opencode_progress.get_text(), "Could not sign in. Please try again.",
        )
        self.assertNotIn("sensitive-response-body", " ".join(logs.output))
        self.refresh.assert_not_called()
        self.auth.begin_login.side_effect = None
        self.view._opencode_sign_in.emit("clicked")
        self.wait_for(self.started.is_set)
        self.assertEqual(self.auth.begin_login.call_count, 2)

    def test_disconnect_clears_app_session_and_refreshes(self):
        self.status = "connected"
        self.view._update_opencode_status()

        def disconnect():
            self.status = "disconnected"

        self.auth.disconnect.side_effect = disconnect
        self.view._opencode_disconnect.emit("clicked")
        self.auth.disconnect.assert_called_once_with()
        self.assertEqual(self.view._opencode_status.get_text(), "Not connected")
        self.assertFalse(self.view._opencode_disconnect.get_sensitive())
        self.refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
