"""Behavior checks for visible uncertainty, not cosmetics or collection."""
from datetime import datetime, timezone, timedelta
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree
from codexbar_linux.core.models import ProviderUsage, UsageWindow
from codexbar_linux.presentation import reading_details, tray_icon_name
from codexbar_linux.icons import ensure_icons

class ReadingPresentationTests(unittest.TestCase):
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    def details(self, **kwargs):
        return reading_details(ProviderUsage(provider="codex", source="synthetic", **kwargs), now=self.now)
    def test_missing_reading_stays_unknown(self):
        self.assertEqual(self.details(), ("Source: synthetic", "Updated: unknown", "Quota unknown — no percentage available"))
    def test_zero_is_reported_without_unknown_quota(self):
        details = self.details(primary=UsageWindow(0), updated_at=self.now)
        self.assertEqual(details[2], "Reported usage — recent")
    def test_cached_usage_never_claims_current_confirmation(self):
        details = self.details(primary=UsageWindow(50), error="Stale cached response", updated_at=self.now-timedelta(minutes=46))
        self.assertEqual(details[1], "Updated: 46m ago")
        self.assertIn("not confirmed now", details[2])
    def test_cost_only_does_not_imply_quota(self):
        usage = ProviderUsage(provider="openai-api", source="synthetic", balance_usd=12.5, updated_at=self.now)
        self.assertIn("quota unknown", reading_details(usage, now=self.now)[2])
    def test_invalid_timestamps_remain_unknown(self):
        for stamp in (None, self.now.replace(tzinfo=None), self.now+timedelta(seconds=1)):
            self.assertEqual(self.details(primary=UsageWindow(10), updated_at=stamp)[1], "Updated: unknown")
    def test_old_reading_is_not_recent(self):
        self.assertIn("older than 10 min", self.details(primary=UsageWindow(10), updated_at=self.now-timedelta(seconds=601))[2])
    def test_error_is_visible_even_with_usage(self):
        self.assertIn("reported a problem", self.details(primary=UsageWindow(10), error="HTTP 500")[2])
    def test_icons_retain_four_status_keys_and_distinct_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            for theme in ("light", "dark"):
                paths = ensure_icons(directory, theme=theme)
                self.assertEqual(set(paths), {"neutral", "ok", "warning", "critical"})
                marks=[]
                for path in paths.values():
                    svg=ElementTree.parse(path).getroot()
                    marks.append(list(svg)[-1].attrib["d"])
                self.assertEqual(len(set(marks)), 4)

class TrayUncertaintyTests(unittest.TestCase):
    now = ReadingPresentationTests.now
    def icon(self, **kwargs):
        return tray_icon_name([ProviderUsage(provider="codex", source="synthetic", **kwargs)], now=self.now)
    def test_no_quota_cost_only_and_errors_are_neutral(self):
        self.assertEqual(tray_icon_name([], now=self.now), "neutral")
        for data in ({}, {"balance_usd": 12.5}, {"primary": UsageWindow(40), "error": "HTTP 500"}):
            self.assertEqual(self.icon(updated_at=self.now, **data), "neutral")
    def test_old_cached_missing_future_and_invalid_quota_are_neutral(self):
        for stamp in (None, self.now.replace(tzinfo=None), self.now+timedelta(seconds=1), self.now-timedelta(minutes=11)):
            self.assertEqual(self.icon(primary=UsageWindow(40), updated_at=stamp), "neutral")
        for invalid in (-1, 101, True, float("nan")):
            self.assertEqual(self.icon(primary=UsageWindow(invalid), updated_at=self.now), "neutral")
        self.assertEqual(self.icon(primary=UsageWindow(40), updated_at=self.now, error="Stale"), "neutral")
    def test_fresh_zero_valid_and_worst_window_not_best_window(self):
        self.assertEqual(self.icon(primary=UsageWindow(0), updated_at=self.now), "ok")
        self.assertEqual(self.icon(primary=UsageWindow(10), secondary=UsageWindow(95), updated_at=self.now), "critical")
        self.assertEqual(self.icon(primary=UsageWindow(80), updated_at=self.now), "warning")

    def test_countdown_recalculates_icon_after_freshness_expires(self):
        from unittest import mock
        # The earlier builder test supplies GTK doubles; no real display is needed.
        from tests.test_cost_presentation import CostPresentationTests
        CostPresentationTests().build(ProviderUsage(provider="openai-api", source="synthetic", balance_usd=0))
        from codexbar_linux.tray import TrayApp
        usage = ProviderUsage(provider="codex", source="synthetic", primary=UsageWindow(20), updated_at=self.now)
        with tempfile.TemporaryDirectory() as directory:
            app = object.__new__(TrayApp)
            app.indicator = mock.Mock()
            app.icon_paths = ensure_icons(directory, theme="light")
            app._last_usages = [usage]
            app._last_next_reset = ""
            app._dashboard = None
            app.get_config = None
            app._build_menu = mock.Mock()
            with mock.patch("codexbar_linux.tray.tray_icon_name", side_effect=lambda usages, **thresholds: tray_icon_name(usages, now=self.now, **thresholds)):
                app._apply_status_icon()
            self.assertEqual(app.indicator.set_icon_full.call_args.args[0], app.icon_paths["ok"])
            with mock.patch("codexbar_linux.tray.tray_icon_name", side_effect=lambda usages, **thresholds: tray_icon_name(usages, now=self.now+timedelta(seconds=601), **thresholds)):
                self.assertTrue(app._on_countdown_tick())
            self.assertEqual(app.indicator.set_icon_full.call_args.args[0], app.icon_paths["neutral"])

    def test_icon_uses_current_configuration_thresholds(self):
        usage=ProviderUsage(provider="codex",source="synthetic",primary=UsageWindow(80),updated_at=self.now)
        self.assertEqual(tray_icon_name([usage],now=self.now,warn_at=85,crit_at=95),"ok")
        self.assertEqual(tray_icon_name([usage],now=self.now,warn_at=75,crit_at=95),"warning")
        self.assertEqual(tray_icon_name([usage],now=self.now,warn_at=70,crit_at=80),"critical")
