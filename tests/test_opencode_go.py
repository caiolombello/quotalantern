"""OpenCode Go uses Console JSON and its own OAuth session."""

import os
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from codexbar_linux import opencode_auth
from codexbar_linux.opencode_auth import OAuthSession
from codexbar_linux.providers.opencode_go import fetch_opencode_go_usage


def status_payload(product="go"):
    return {
        "product": product,
        "access": {
            "meters": {
                "fiveHour": {"usedMicroCents": "1042418276", "limitMicroCents": "1200000000", "resetsAt": "2026-10-06T17:48:28Z"},
                "week": {"usedMicroCents": "1042418276", "limitMicroCents": "3000000000", "resetsAt": "2026-10-12T00:00:00Z"},
                "month": {"usedMicroCents": "1049183724", "limitMicroCents": "6000000000", "resetsAt": "2026-10-27T14:29:06Z"},
            },
        },
    }


class Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.raw[:limit]


class OpenCodeGoTests(unittest.TestCase):
    def test_missing_login_points_to_settings_instead_of_a_cookie(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch.dict(os.environ, {"HOME": temporary}, clear=True),
                mock.patch.object(opencode_auth, "SESSION_DIR", Path(temporary) / "oauth"),
                mock.patch.object(opencode_auth, "SESSION_PATH", Path(temporary) / "oauth/oauth.json"),
                mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network")),
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")),
            ):
                usage = fetch_opencode_go_usage()[0]
        self.assertIsNone(usage.primary)
        self.assertIn("Settings", usage.error)
        self.assertNotIn("cookie", usage.error.lower())

    def fetch(self, payload):
        session = OAuthSession("test-access", "test-refresh", 9999999999, "org_test")
        with (
            mock.patch("codexbar_linux.opencode_provider.get_session", return_value=session),
            mock.patch("urllib.request.OpenerDirector.open", return_value=Response(payload)) as request,
        ):
            usage = fetch_opencode_go_usage()[0]
        return usage, request

    def test_console_meters_are_loaded_with_oauth_and_reset_dates(self):
        usage, request = self.fetch(status_payload())
        self.assertIsNone(usage.error)
        self.assertEqual(usage.source, "console-api")
        self.assertEqual([usage.primary.used_percent, usage.secondary.used_percent, usage.tertiary.used_percent], [87, 35, 17])
        self.assertEqual(usage.primary.resets_at, datetime(2026, 10, 6, 17, 48, 28, tzinfo=timezone.utc))
        self.assertEqual(usage.secondary.window_minutes, 10080)
        self.assertEqual(usage.primary.reset_description, "5 hour: $10.42/$12")
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://opencode.ai/console/api/go/status")
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-access")
        self.assertEqual(sent.get_header("X-org-id"), "org_test")
        self.assertIsNone(sent.get_header("Cookie"))

    def test_go_plus_uses_limits_from_the_response(self):
        payload = status_payload("go-plus")
        payload["access"]["meters"]["fiveHour"]["limitMicroCents"] = "4800000000"
        usage, _ = self.fetch(payload)
        self.assertEqual(usage.login_method, "OpenCode Go Plus (OAuth)")
        self.assertEqual(usage.primary.used_percent, 22)
        self.assertEqual(usage.primary.reset_description, "5 hour: $10.42/$48")

    def test_unknown_response_is_not_reported_as_no_subscription(self):
        usage, _ = self.fetch({"different_schema": True})
        self.assertIsNone(usage.primary)
        self.assertIn("incompatible", usage.error)

    def test_no_subscription_is_reported_without_usage(self):
        for payload in (None, {"product": "go", "access": None}):
            with self.subTest(payload=payload):
                usage, _ = self.fetch(payload)
                self.assertIsNone(usage.primary)
                self.assertIn("no active subscription", usage.error)

    def test_invalid_or_partial_meters_never_appear_as_zero_usage(self):
        for value in (-1, True, "NaN", 0):
            payload = status_payload()
            payload["access"]["meters"]["week"]["limitMicroCents"] = value
            with self.subTest(value=value):
                usage, _ = self.fetch(payload)
                self.assertIsNone(usage.primary)
                self.assertIn("incompatible", usage.error)
        payload = status_payload()
        del payload["access"]["meters"]["month"]
        usage, _ = self.fetch(payload)
        self.assertIsNone(usage.secondary)
        self.assertIn("incompatible", usage.error)

    def test_an_unused_rolling_window_can_have_no_reset_time(self):
        payload = status_payload()
        payload["access"]["meters"]["fiveHour"].update(usedMicroCents="0", resetsAt=None)
        usage, _ = self.fetch(payload)
        self.assertIsNone(usage.error)
        self.assertEqual(usage.primary.used_percent, 0)
        self.assertIsNone(usage.primary.resets_at)

    def test_above_limit_is_clamped_but_actual_amount_is_preserved(self):
        payload = status_payload()
        payload["access"]["meters"]["fiveHour"]["usedMicroCents"] = "2400000000"
        usage, _ = self.fetch(payload)
        self.assertEqual(usage.primary.used_percent, 100)
        self.assertEqual(usage.primary.reset_description, "5 hour: $24.00/$12")

    def test_errors_never_include_response_bodies_or_credentials(self):
        session = OAuthSession("test-access", "test-refresh", 9999999999, "org_test")
        failures = [
            urllib.error.HTTPError("https://opencode.ai/console/api/go/status", 401, "test-access", {}, io.BytesIO(b"test-refresh")),
            urllib.error.HTTPError("https://opencode.ai/console/api/go/status", 403, "test-access", {}, io.BytesIO(b"test-refresh")),
            urllib.error.URLError("test-access test-refresh"),
        ]
        for failure in failures:
            with self.subTest(type=type(failure).__name__):
                with (
                    mock.patch("codexbar_linux.opencode_provider.get_session", return_value=session),
                    mock.patch("urllib.request.OpenerDirector.open", side_effect=failure),
                ):
                    usage = fetch_opencode_go_usage()[0]
                self.assertIsNone(usage.primary)
                self.assertNotIn("test-access", usage.error)
                self.assertNotIn("test-refresh", usage.error)

    def test_json_shape_changes_and_invalid_dates_are_rejected(self):
        payload = status_payload()
        payload["access"]["meters"]["month"]["resetsAt"] = "2026-10-27T14:29:06"
        usage, _ = self.fetch(payload)
        self.assertIsNone(usage.primary)
        self.assertIn("incompatible", usage.error)

    def test_redirect_handler_rejects_forwarding_oauth(self):
        session = OAuthSession("test-access", "test-refresh", 9999999999, "org_test")
        with mock.patch("codexbar_linux.opencode_provider.get_session", return_value=session):
            with mock.patch("urllib.request.build_opener") as build:
                def reject_redirect(request, timeout):
                    return build.call_args.args[0].redirect_request(request, None, 302, "Moved", {}, "https://other.example/")
                build.return_value.open.side_effect = reject_redirect
                usage = fetch_opencode_go_usage()[0]
        self.assertIsNone(usage.primary)
        self.assertIn("incompatible", usage.error)


if __name__ == "__main__":
    unittest.main()
