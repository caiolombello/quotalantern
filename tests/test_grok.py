"""Unit tests for the Grok usage provider."""

from __future__ import annotations

import json
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from codexbar_linux.config import DEFAULT_ENABLED_PROVIDERS
from codexbar_linux.providers import PROVIDER_BY_ID
from codexbar_linux.providers.grok import (
    BILLING_URL,
    _GrokAuthError,
    _GrokCredentials,
    _credentials_from_payload,
    _parse_billing,
    _request_billing,
    _request_once,
    fetch_grok_usage,
)
from codexbar_linux.ui_common import minimax_window


def _billing_payload(percent: float = 42.0) -> dict:
    return {
        "config": {
            "creditUsagePercent": percent,
            "currentPeriod": {
                "start": "2026-07-10T04:06:22Z",
                "end": "2026-07-17T04:06:22Z",
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
            },
        }
    }


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict):
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.raw


class GrokProviderTests(unittest.TestCase):
    def test_prefers_oidc_credentials_over_legacy(self) -> None:
        credentials = _credentials_from_payload(
            {
                "https://accounts.x.ai/sign-in": {
                    "key": "legacy-token",
                    "auth_mode": "session",
                },
                "https://auth.x.ai::client": {
                    "key": "oidc-token",
                    "auth_mode": "oidc",
                    "email": "grok@example.com",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            }
        )

        self.assertEqual(credentials.access_token, "oidc-token")
        self.assertEqual(credentials.email, "grok@example.com")

    def test_rejects_auth_without_access_token(self) -> None:
        with self.assertRaises(_GrokAuthError):
            _credentials_from_payload({"https://auth.x.ai::client": {"auth_mode": "oidc"}})

    def test_parses_weekly_usage_and_reset(self) -> None:
        window = _parse_billing(
            _billing_payload(42.0),
            now=datetime(2026, 7, 10, 5, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(window.used_percent, 42)
        self.assertEqual(window.window_minutes, 7 * 24 * 60)
        self.assertEqual(window.resets_at, datetime(2026, 7, 17, 4, 6, 22, tzinfo=timezone.utc))
        self.assertEqual(window.reset_description, "resets in 6d 23h")

    def test_rejects_invalid_billing_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "usage percent"):
            _parse_billing({"config": {}})
        with self.assertRaisesRegex(ValueError, "unsupported period"):
            _parse_billing(
                {
                    "config": {
                        "creditUsagePercent": 10,
                        "currentPeriod": {"type": "USAGE_PERIOD_TYPE_DAILY"},
                    }
                }
            )

    def test_billing_request_uses_oauth_without_leaking_it_to_url(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return _FakeResponse(_billing_payload())

        with patch("codexbar_linux.providers.grok.urllib.request.urlopen", fake_urlopen):
            status, payload = _request_once("test-oauth-token")

        request, timeout = captured[0]
        self.assertEqual(status, 200)
        self.assertEqual(payload, _billing_payload())
        self.assertEqual(request.full_url, BILLING_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-oauth-token")
        self.assertNotIn("test-oauth-token", request.full_url)
        self.assertEqual(timeout, 15)

    def test_retries_one_network_failure(self) -> None:
        with patch(
            "codexbar_linux.providers.grok._request_once",
            side_effect=[urllib.error.URLError("temporary"), (200, _billing_payload())],
        ) as request:
            status, _ = _request_billing("test-token")

        self.assertEqual(status, 200)
        self.assertEqual(request.call_count, 2)

    def test_retries_one_transient_http_failure(self) -> None:
        with patch(
            "codexbar_linux.providers.grok._request_once",
            side_effect=[(503, None), (200, _billing_payload())],
        ) as request:
            status, _ = _request_billing("test-token")

        self.assertEqual(status, 200)
        self.assertEqual(request.call_count, 2)

    def test_maps_usage_to_weekly_slot(self) -> None:
        credentials = _GrokCredentials(
            access_token="test-token",
            email="grok@example.com",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        with (
            patch("codexbar_linux.providers.grok._load_credentials", return_value=credentials),
            patch(
                "codexbar_linux.providers.grok._request_billing",
                return_value=(200, _billing_payload(73.0)),
            ),
        ):
            usage = fetch_grok_usage()[0]

        self.assertIsNone(usage.primary)
        self.assertEqual(usage.secondary.used_percent, 73)
        self.assertEqual(minimax_window(usage)[0], "Weekly")
        self.assertIsNone(usage.login_method)
        self.assertEqual(usage.account_email, "grok@example.com")
        self.assertIsNone(usage.error)

    def test_expired_session_does_not_call_billing(self) -> None:
        credentials = _GrokCredentials(
            access_token="expired-token",
            email=None,
            expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        with (
            patch("codexbar_linux.providers.grok._load_credentials", return_value=credentials),
            patch("codexbar_linux.providers.grok._request_billing") as request,
        ):
            usage = fetch_grok_usage()[0]

        request.assert_not_called()
        self.assertIn("grok login", usage.error)

    def test_unauthorized_billing_requests_reauthentication(self) -> None:
        credentials = _GrokCredentials(
            access_token="test-token",
            email=None,
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        with (
            patch("codexbar_linux.providers.grok._load_credentials", return_value=credentials),
            patch("codexbar_linux.providers.grok._request_billing", return_value=(401, None)),
        ):
            usage = fetch_grok_usage()[0]

        self.assertIn("grok login", usage.error)
        self.assertIsNone(usage.secondary)

    def test_provider_is_registered_and_enabled_by_default(self) -> None:
        self.assertIn("grok", DEFAULT_ENABLED_PROVIDERS)
        self.assertTrue(PROVIDER_BY_ID["grok"].default_enabled)
        self.assertEqual(PROVIDER_BY_ID["grok"].display_name, "Grok")


if __name__ == "__main__":
    unittest.main()
