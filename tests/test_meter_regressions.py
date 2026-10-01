import io
import json
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from codexbar_linux.core.models import ProviderUsage, UsageWindow
from codexbar_linux.providers import _costs, claude_api, codex, kiro, openai_api
from codexbar_linux.ui_common import recommendation

CASES = json.loads((Path(__file__).parent / "fixtures/meter_cases.json").read_text())


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


def page(amount, more=False, cursor=None):
    return {"data": [{"results": [{"amount": amount}]}], "has_more": more, "next_page": cursor}


class CostRegressionTests(unittest.TestCase):
    def fetch(self, module, pages):
        with mock.patch.object(module, "_load_admin_key", return_value="synthetic"), mock.patch.object(
            _costs.urllib.request, "urlopen", side_effect=[Response(p) for p in pages]
        ) as request:
            result = (module.fetch_openai_api_usage if module is openai_api else module.fetch_claude_api_usage)()[0]
        return result, request

    def test_openai_dollars_are_not_divided_by_100(self):
        result, _ = self.fetch(openai_api, [page({"value": 12.5, "currency": "usd"})])
        self.assertEqual(result.balance_usd, 12.5)
        self.assertIsNone(result.error)
        self.assertIsNone(result.primary)  # A dollar cost is not an allowance.

    def test_claude_cents_conversion_is_preserved(self):
        result, _ = self.fetch(claude_api, [page("1250")])
        self.assertEqual(result.balance_usd, 12.5)
        self.assertIsNone(result.primary)

    def test_openai_all_pages_and_opaque_cursor(self):
        result, request = self.fetch(openai_api, CASES["openai_pages"])
        self.assertEqual(result.balance_usd, 12.75)
        self.assertEqual(request.call_count, 2)
        first = request.call_args_list[0].args[0].full_url
        second = request.call_args_list[1].args[0].full_url
        self.assertEqual(urllib.parse.urlsplit(first).netloc, urllib.parse.urlsplit(second).netloc)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(second).query)["page"], ["fixture & next"])

    def test_claude_all_pages(self):
        result, request = self.fetch(claude_api, CASES["anthropic_pages"])
        self.assertEqual(result.balance_usd, 12.505)
        self.assertEqual(request.call_count, 2)

    def test_empty_complete_cost_report_is_zero_cost_without_quota(self):
        result, _ = self.fetch(openai_api, [{"data": [], "has_more": False}])
        self.assertEqual(result.balance_usd, 0.0)
        self.assertIsNone(result.primary)

    def test_invalid_amount_or_currency_is_not_a_zero_total(self):
        for amount in [None, 123, {"currency": "eur", "value": 1}, {"currency": "usd", "value": True}, {"currency": "usd", "value": "NaN"}, {"currency": "usd", "value": "1e999"}]:
            with self.subTest(amount=amount):
                result, _ = self.fetch(openai_api, [page(amount)])
                self.assertIsNone(result.balance_usd)
                self.assertIsNotNone(result.error)

    def test_invalid_anthropic_amount_rejects_partial_report(self):
        result, _ = self.fetch(claude_api, [page("invalid")])
        self.assertIsNone(result.balance_usd)
        self.assertIsNotNone(result.error)

    def test_missing_or_invalid_pagination_is_error(self):
        for payload in [{"data": []}, {"data": [], "has_more": "false"}, {"data": [], "has_more": True, "next_page": None}]:
            result, _ = self.fetch(openai_api, [payload])
            self.assertIsNone(result.balance_usd)
            self.assertIsNotNone(result.error)

    def test_second_page_http_error_does_not_expose_partial_or_body(self):
        error = urllib.error.HTTPError("https://example.invalid", 500, "Error", {}, io.BytesIO(b"private-marker"))
        with mock.patch.object(openai_api, "_load_admin_key", return_value="synthetic"), mock.patch.object(
            _costs.urllib.request, "urlopen", side_effect=[Response(CASES["openai_pages"][0]), error]
        ):
            result = openai_api.fetch_openai_api_usage()[0]
        self.assertIsNone(result.balance_usd)
        self.assertIn("incomplete", result.error)
        self.assertNotIn("private-marker", result.error)
        self.assertTrue(error.closed)

    def test_repeated_cursor_is_bounded(self):
        payload = page({"currency": "usd", "value": 1}, True, "repeat")
        result, request = self.fetch(openai_api, [payload, payload])
        self.assertEqual(request.call_count, 2)
        self.assertIsNone(result.balance_usd)
        self.assertIn("Repeated", result.error)

    def test_page_limit_returns_no_partial_total(self):
        pages = [page({"currency": "usd", "value": 1}, True, "one"), page({"currency": "usd", "value": 1}, True, "two")]
        with mock.patch.object(_costs.urllib.request, "urlopen", side_effect=[Response(p) for p in pages]) as request:
            with self.assertRaisesRegex(ValueError, "page limit"):
                _costs.sum_cost_pages("https://example.invalid?q=1", {}, _costs.openai_amount, max_pages=2)
        self.assertEqual(request.call_count, 2)

    def test_total_deadline_stops_before_next_request(self):
        with mock.patch.object(_costs.time, "monotonic", side_effect=[0, 0, 1, 31]), mock.patch.object(
            _costs.urllib.request, "urlopen", return_value=Response(CASES["openai_pages"][0])
        ) as request:
            with self.assertRaises(TimeoutError):
                _costs.sum_cost_pages("https://example.invalid", {}, _costs.openai_amount)
        self.assertEqual(request.call_count, 1)

    def test_size_limit_and_schema_error(self):
        with mock.patch.object(_costs.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * (_costs.MAX_RESPONSE_BYTES + 1))):
            with self.assertRaisesRegex(ValueError, "size limit"):
                _costs.sum_cost_pages("https://example.invalid", {}, _costs.openai_amount)
        result, _ = self.fetch(openai_api, [{"data": [{"results": "invalid"}], "has_more": False}])
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.balance_usd)


class UnknownMeterTests(unittest.TestCase):
    def codex_fetch(self, payload):
        with mock.patch.object(codex, "_load_auth_token", return_value="synthetic"), mock.patch.object(
            codex.urllib.request, "urlopen", return_value=Response(payload)
        ):
            return codex.fetch_codex_usage()[0]

    def test_codex_invalid_percent_is_unavailable(self):
        for value in [None, "invalid", True, float("nan"), float("inf"), -1, 101]:
            with self.subTest(value=value):
                result = self.codex_fetch({"rate_limit": {"primary_window": {"used_percent": value}}})
                self.assertIsNone(result.primary)
                self.assertIsNotNone(result.error)

    def test_codex_missing_schema_is_unavailable(self):
        for payload in [{}, [], {"rate_limit": {}}, {"rate_limit": {"primary_window": []}}]:
            result = self.codex_fetch(payload)
            self.assertIsNone(result.primary)
            self.assertIsNotNone(result.error)

    def test_codex_zero_and_secondary_only_remain_valid(self):
        result = self.codex_fetch(CASES["codex_valid"])
        self.assertEqual(result.primary.used_percent, 0)
        self.assertEqual(result.secondary.used_percent, 20)
        self.assertIsNone(result.error)
        result = self.codex_fetch({"rate_limit": {"secondary_window": {"used_percent": 40}}})
        self.assertEqual(result.secondary.used_percent, 40)
        self.assertIsNone(result.error)

    def test_codex_invalid_secondary_does_not_hide_a_constraint(self):
        result = self.codex_fetch({"rate_limit": {"primary_window": {"used_percent": 10}, "secondary_window": {"used_percent": "bad"}}})
        self.assertIsNone(result.primary)
        self.assertIsNotNone(result.error)

    def test_kiro_missing_and_invalid_used_or_limit_is_unavailable(self):
        payloads = [{}, CASES["kiro_missing_used"]]
        for limit, used in [(0, 0), (None, 0), (100, True), (100, float("nan")), (float("inf"), 1), (100, -1)]:
            payloads.append({"usageBreakdownList": [{"resourceType": "CREDIT", "usageLimit": limit, "currentUsage": used}]})
        for payload in payloads:
            result = kiro._parse_usage_limits(payload)
            self.assertIsNone(result.primary)
            self.assertIsNotNone(result.error)

    def test_kiro_valid_zero_and_credit_overage_are_preserved(self):
        result = kiro._parse_usage_limits(CASES["kiro_valid"])
        self.assertEqual(result.primary.used_percent, 20)
        self.assertEqual(result.credits_remaining, 80)
        self.assertIsNone(result.error)
        result = kiro._parse_usage_limits({"usageBreakdownList": [{"resourceType": "CREDIT", "usageLimit": 100, "currentUsage": 120, "overageCap": 50}]})
        self.assertEqual(result.primary.used_percent, 100)
        self.assertEqual(result.secondary.used_percent, 40)
        result = kiro._parse_usage_limits({"usageBreakdownList": [{"resourceType": "CREDIT", "usageLimit": 100, "currentUsage": 0}]})
        self.assertEqual(result.primary.used_percent, 0)


class RecommendationTests(unittest.TestCase):
    NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)

    def quota(self, provider="codex", first=20, second=30):
        return ProviderUsage(provider=provider, source="oauth", updated_at=self.NOW,
            primary=UsageWindow(first, window_minutes=300), secondary=UsageWindow(second, window_minutes=10080))

    def test_fresh_comparable_quotas_use_worst_constraint(self):
        a = self.quota(first=5, second=80)
        b = self.quota(first=20, second=40)
        self.assertEqual(recommendation([a, b], now=self.NOW), ("Codex", "Weekly", 40))

    def test_error_stale_old_missing_naive_and_future_timestamps_rejected(self):
        variants = []
        for error in ["Network timeout (showing stale data)", "auth expired"]:
            usage = self.quota(first=1, second=2)
            usage.error = error
            variants.append(usage)
        for stamp in [None, self.NOW.replace(tzinfo=None), self.NOW - timedelta(seconds=601), self.NOW + timedelta(seconds=1)]:
            usage = self.quota(first=1, second=2)
            usage.updated_at = stamp
            variants.append(usage)
        for usage in variants:
            self.assertEqual(recommendation([usage], now=self.NOW), ("", "", 0))

    def test_api_money_and_unqualified_sources_are_rejected(self):
        for provider, source in [("openai-api", "admin-api"), ("claude-api", "api"), ("gemini", "agy"), ("kiro", "kiro-cli"), ("cursor", "web"), ("codex", "unknown"), ("claude", "oauth"), ("grok", "oauth")]:
            usage = self.quota(provider, first=0, second=0)
            usage.source = source
            self.assertEqual(recommendation([usage], now=self.NOW), ("", "", 0))

    def test_different_constraint_sets_do_not_get_compared(self):
        weekly = self.quota()
        weekly.primary = None
        self.assertEqual(recommendation([self.quota(), weekly], now=self.NOW), ("", "", 0))

    def test_invalid_or_blocked_windows_are_rejected(self):
        for pct in [True, -1, 101, 100, float("nan")]:
            self.assertEqual(recommendation([self.quota(first=pct)], now=self.NOW), ("", "", 0))
        usage = self.quota()
        usage.primary.window_minutes = 1440
        self.assertEqual(recommendation([usage], now=self.NOW), ("", "", 0))
        usage = self.quota()
        usage.primary.resets_at = self.NOW - timedelta(seconds=1)
        self.assertEqual(recommendation([usage], now=self.NOW), ("", "", 0))

    def test_permissive_legacy_adapters_are_not_qualified(self):
        for provider in ("claude", "grok"):
            usage = self.quota(provider, first=0, second=1)
            self.assertEqual(recommendation([usage], now=self.NOW), ("", "", 0))
            usage.primary = None
            self.assertEqual(recommendation([usage], now=self.NOW), ("", "", 0))
