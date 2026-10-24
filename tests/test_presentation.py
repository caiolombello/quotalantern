"""Behavior checks for visible uncertainty, not cosmetics or collection."""
from datetime import datetime, timezone, timedelta
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree
from codexbar_linux.core.models import ProviderUsage, UsageWindow
from codexbar_linux.presentation import reading_details, tray_icon_name, tray_reading
from codexbar_linux.icons import ensure_icons, gauge_svg

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
    def test_ring_is_proportional_and_unknown_is_not_zero(self):
        ns = {"s": "http://www.w3.org/2000/svg"}
        for theme in ("light", "dark"):
            arcs = []
            for pct in (0, 50, 100):
                root = ElementTree.fromstring(gauge_svg(pct, "critical" if pct == 100 else "ok", theme))
                arc = root.findall("s:circle", ns)[1]
                arcs.append(float(arc.attrib["stroke-dasharray"].split()[0]))
            self.assertEqual(arcs[0], 0)
            self.assertAlmostEqual(arcs[1] * 2, arcs[2], places=2)
            unknown = ElementTree.fromstring(gauge_svg(None, "unknown", theme))
            stale = ElementTree.fromstring(gauge_svg(None, "stale", theme))
            self.assertNotEqual(unknown.findall("s:circle", ns)[1].attrib["stroke-dasharray"], stale.findall("s:circle", ns)[1].attrib["stroke-dasharray"])
            self.assertIn("quota unconfirmed", gauge_svg(None, "unknown", theme))
        for pct in (-1, 101, True, float("nan")):
            with self.assertRaises(ValueError): gauge_svg(pct, "ok")

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
        builder = CostPresentationTests()
        builder.build(ProviderUsage(provider="openai-api", source="synthetic", balance_usd=0))
        TrayApp = builder.tray_class
        usage = ProviderUsage(provider="codex", source="synthetic", primary=UsageWindow(20), updated_at=self.now)
        with tempfile.TemporaryDirectory() as directory:
            app = object.__new__(TrayApp)
            app.indicator = mock.Mock()
            app.icon_paths = ensure_icons(directory, theme="light")
            app.icon_theme = "light"
            app._last_usages = [usage]
            app._last_next_reset = ""
            app._dashboard = None
            app.get_config = None
            app._build_menu = mock.Mock()
            with mock.patch.object(builder.tray_module, "tray_reading", side_effect=lambda usages, **thresholds: tray_reading(usages, now=self.now, **thresholds)):
                app._apply_status_icon()
            self.assertIn("quota-ok-20-", app.indicator.set_icon_full.call_args.args[0])
            self.assertIn("Source: synthetic", app.indicator.set_title.call_args.args[0])
            with mock.patch.object(builder.tray_module, "tray_reading", side_effect=lambda usages, **thresholds: tray_reading(usages, now=self.now+timedelta(seconds=601), **thresholds)):
                self.assertTrue(app._on_countdown_tick())
            self.assertIn("quota-stale-none-", app.indicator.set_icon_full.call_args.args[0])
            self.assertIn("stale", app.indicator.set_label.call_args.args[0])

    def test_icon_uses_current_configuration_thresholds(self):
        usage=ProviderUsage(provider="codex",source="synthetic",primary=UsageWindow(80),updated_at=self.now)
        self.assertEqual(tray_icon_name([usage],now=self.now,warn_at=85,crit_at=95),"ok")
        self.assertEqual(tray_icon_name([usage],now=self.now,warn_at=75,crit_at=95),"warning")
        self.assertEqual(tray_icon_name([usage],now=self.now,warn_at=70,crit_at=80),"critical")

    def test_mixed_providers_select_one_named_window_not_an_average(self):
        usages = [ProviderUsage(provider="codex", source="synthetic", primary=UsageWindow(20, 300), updated_at=self.now),
                  ProviderUsage(provider="claude", source="synthetic", secondary=UsageWindow(80, 10080), updated_at=self.now),
                  ProviderUsage(provider="gemini-api", source="synthetic", primary=UsageWindow(100), updated_at=self.now)]
        reading = tray_reading(usages, now=self.now)
        self.assertEqual(reading.percent, 80)
        self.assertIn("Claude Secondary (168h)", reading.label)
        # Two subscriptions share the ring as segments; costs never enter it.
        self.assertIn("never a total or average", reading.description)
        self.assertEqual([(s.provider, s.percent) for s in reading.segments], [("Codex", 20), ("Claude", 80)])
        self.assertIn("not total quota", tray_reading(usages[:1], now=self.now).description)
        self.assertIn("Source: synthetic", reading.description)
        self.assertIn("Updated:", reading.description)

    def test_stale_is_distinct_and_partial_coverage_is_disclosed(self):
        stale = ProviderUsage(provider="claude", source="synthetic", primary=UsageWindow(100), updated_at=self.now-timedelta(minutes=11))
        reading = tray_reading([stale], now=self.now)
        self.assertEqual((reading.state, reading.percent), ("stale", None))
        fresh = ProviderUsage(provider="codex", source="synthetic", primary=UsageWindow(0), updated_at=self.now)
        reading = tray_reading([stale, fresh], now=self.now)
        self.assertEqual((reading.state, reading.percent), ("ok", 0))
        self.assertIn("Unconfirmed: Claude: stale", reading.description)
        self.assertIn("Codex", reading.label)


class InterfaceCopyTests(unittest.TestCase):
    """Badges, menu rows and labels keep uncertainty visible in words, not color."""
    now = ReadingPresentationTests.now

    def badge(self, **kwargs):
        from codexbar_linux.presentation import reading_badge
        return reading_badge(ProviderUsage(provider="codex", source="synthetic", **kwargs), now=self.now)

    def test_badges_name_every_state(self):
        self.assertEqual(self.badge(primary=UsageWindow(10), updated_at=self.now), ("ok", "✓ Fresh"))
        self.assertEqual(self.badge(primary=UsageWindow(10), updated_at=self.now - timedelta(minutes=11)), ("neutral", "◷ Older"))
        self.assertEqual(self.badge(primary=UsageWindow(10)), ("neutral", "? Unverified"))
        self.assertEqual(self.badge(primary=UsageWindow(10), error="Stale cached response"), ("stale", "◷ Stale"))
        self.assertEqual(self.badge(error="HTTP 500"), ("error", "! Error"))
        self.assertEqual(self.badge(error="Not signed in — cookie missing"), ("neutral", "○ Not connected"))
        self.assertEqual(self.badge(), ("neutral", "? Unknown"))
        from codexbar_linux.presentation import reading_badge
        spend = ProviderUsage(provider="openai-api", source="synthetic", balance_usd=1.0, updated_at=self.now)
        self.assertEqual(reading_badge(spend, now=self.now), ("spend", "$ Spend"))
        for _, text in (self.badge(), reading_badge(spend, now=self.now)):
            self.assertNotIn("%", text)

    def test_menu_rows_keep_every_description_line(self):
        from codexbar_linux.presentation import menu_rows
        usages = [ProviderUsage(provider="codex", source="synthetic", primary=UsageWindow(42, 300), updated_at=self.now),
                  ProviderUsage(provider="claude", source="synthetic", primary=UsageWindow(5), error="Stale", updated_at=self.now)]
        for reading in (tray_reading(usages, now=self.now), tray_reading([], now=self.now), tray_reading(usages[1:], now=self.now)):
            rows = menu_rows(reading)
            self.assertEqual(rows[0], reading.label)
            joined = " · ".join(rows)
            for line in reading.description.split("\n"):
                self.assertIn(line, joined)
        rows = menu_rows(tray_reading(usages, now=self.now))
        self.assertIn("Source: synthetic · Updated: just now", rows[1])
        self.assertIn("Unconfirmed: Claude: stale", rows)

    def test_window_and_reset_labels(self):
        from codexbar_linux.presentation import format_interval, format_next_reset, window_span
        self.assertEqual([window_span(m) for m in (300, 10080, 1440, 45, None, 0, True)], ["5h", "7d", "1d", "45m", "", "", ""])
        self.assertEqual(format_next_reset("codex Session | resets in 2h"), "Next reset: Codex Session — resets in 2h")
        self.assertEqual(format_next_reset("unknown-id Weekly | 14:30"), "Next reset: unknown-id Weekly — 14:30")
        self.assertEqual(format_next_reset(""), "")
        self.assertEqual([format_interval(s) for s in (300, 3600, 90)], ["5 min", "1 h", "90 s"])

    def test_tray_rows_color_only_confirmed_readings(self):
        from tests.test_cost_presentation import CostPresentationTests
        from codexbar_linux.config import AppConfig
        builder = CostPresentationTests()
        builder.build(ProviderUsage(provider="openai-api", source="synthetic", balance_usd=0))
        tray = object.__new__(builder.tray_class)
        tray._append_refresh_provider_item = lambda *args: None
        tray._append_open_item = lambda *args: None
        stale = tray._provider_item(ProviderUsage(provider="grok", source="synthetic", primary=UsageWindow(18), error="Stale cached response"), AppConfig())
        self.assertTrue(stale.label.startswith("⚪"))
        self.assertIn("◷ Stale", stale.label)
        fresh = tray._provider_item(ProviderUsage(provider="codex", source="synthetic", primary=UsageWindow(18), updated_at=datetime.now(timezone.utc)), AppConfig())
        self.assertTrue(fresh.label.startswith("🟢"))
        self.assertNotIn("Stale", fresh.label)


class SegmentedRingTests(unittest.TestCase):
    """Several providers: one segment each, so one exhausted service is not 'everything'."""
    now = ReadingPresentationTests.now
    ns = {"s": "http://www.w3.org/2000/svg"}

    def usage(self, provider, pct=None, **kwargs):
        kwargs.setdefault("updated_at", self.now)
        if pct is not None:
            kwargs["primary"] = UsageWindow(pct, 300)
        return ProviderUsage(provider=provider, source="synthetic", **kwargs)

    def arcs(self, svg):
        root = ElementTree.fromstring(svg)
        return [(path.attrib.get("stroke"), path.attrib.get("opacity"), path.attrib.get("stroke-dasharray"), path.attrib["d"])
                for path in root.findall("s:path", self.ns) if path.attrib.get("fill") == "none"]

    def test_one_exhausted_provider_does_not_paint_the_whole_ring(self):
        from codexbar_linux.icons import segmented_gauge_svg
        reading = tray_reading([self.usage("codex", 100), self.usage("claude", 20)], now=self.now)
        self.assertTrue(reading.segmented)
        self.assertEqual((reading.state, reading.percent), ("critical", 100))
        self.assertEqual([(s.provider, s.state, s.percent) for s in reading.segments],
                         [("Codex", "critical", 100), ("Claude", "ok", 20)])
        self.assertIn("Segments: Codex 100% · Claude 20%", reading.description)
        for theme in ("dark", "light"):
            svg = segmented_gauge_svg([(s.percent, s.state) for s in reading.segments], theme)
            strokes = {stroke for stroke, opacity, _, _ in self.arcs(svg) if opacity is None}
            self.assertEqual(len(strokes), 2)  # red fill for Codex, green fill for Claude
            self.assertIn("segments: critical 100, ok 20", svg)

    def test_segment_fill_is_proportional_within_its_own_span(self):
        from codexbar_linux.icons import segmented_gauge_svg
        import math, re
        def sweep(d):
            nums = [float(n) for n in re.findall(r"-?\d+\.\d+", d)]
            (x0, y0), (x1, y1) = nums[0:2], nums[-2:]
            a0 = math.degrees(math.atan2(x0 - 16, 16 - y0)) % 360
            a1 = math.degrees(math.atan2(x1 - 16, 16 - y1)) % 360
            return (a1 - a0) % 360
        arcs = self.arcs(segmented_gauge_svg([(50, "ok"), (100, "critical")]))
        tracks = [sweep(d) for _, opacity, _, d in arcs if opacity == "0.25"]
        fills = [sweep(d) for _, opacity, _, d in arcs if opacity is None]
        self.assertAlmostEqual(tracks[0], tracks[1], places=2)
        self.assertAlmostEqual(fills[0] * 2, fills[1], places=2)
        self.assertAlmostEqual(fills[1], tracks[1], places=2)
        # A zero reading keeps its track and draws no fill; it is not unknown.
        self.assertEqual(len([a for a in self.arcs(segmented_gauge_svg([(0, "ok"), (10, "ok")])) if a[1] is None]), 1)

    def test_unknown_and_stale_segments_are_neutral_not_empty(self):
        from codexbar_linux.icons import segmented_gauge_svg
        failed = self.usage("claude", error="HTTP 500")
        stale = self.usage("grok", primary=UsageWindow(90, 300), error="Stale cached response")
        reading = tray_reading([self.usage("codex", 40), failed, stale], now=self.now)
        self.assertEqual([(s.state, s.percent) for s in reading.segments], [("ok", 40), ("unknown", None), ("stale", None)])
        self.assertIn("Segments: Codex 40% · Claude unknown · Grok stale", reading.description)
        svg = segmented_gauge_svg([(s.percent, s.state) for s in reading.segments])
        dashes = [dash for _, _, dash, _ in self.arcs(svg) if dash]
        self.assertEqual(len(dashes), 2)
        self.assertNotEqual(dashes[0], dashes[1])

    def test_not_connected_providers_stay_out_of_the_ring(self):
        offline = self.usage("grok", error="Not signed in — cookie missing")
        reading = tray_reading([self.usage("codex", 30), offline], now=self.now)
        self.assertFalse(reading.segmented)
        self.assertEqual([s.provider for s in reading.segments], ["Codex"])
        self.assertIn("not total quota", reading.description)

    def test_no_confirmed_reading_keeps_the_whole_ring_neutral(self):
        reading = tray_reading([self.usage("codex", error="HTTP 500"),
                                self.usage("claude", 10, updated_at=self.now - timedelta(minutes=11))], now=self.now)
        self.assertFalse(reading.segmented)
        self.assertEqual((reading.state, reading.percent), ("stale", None))

    def test_segments_follow_provider_order_and_current_thresholds(self):
        usages = [self.usage("claude", 80), self.usage("codex", 10)]
        reading = tray_reading(usages, now=self.now, warn_at=70, crit_at=90)
        self.assertEqual([(s.provider, s.state) for s in reading.segments], [("Claude", "warning"), ("Codex", "ok")])
        reading = tray_reading(usages, now=self.now, warn_at=50, crit_at=75)
        self.assertEqual(reading.segments[0].state, "critical")

    def test_invalid_segment_input_is_rejected(self):
        from codexbar_linux.icons import segmented_gauge_svg
        with self.assertRaises(ValueError):
            segmented_gauge_svg([(10, "ok")])
        for bad in ((101, "ok"), (True, "ok"), (None, "ok"), (10, "bogus")):
            with self.assertRaises(ValueError):
                segmented_gauge_svg([(10, "ok"), bad])

    def test_icon_file_is_segmented_and_cache_is_bounded(self):
        from codexbar_linux import icons
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            path = icons.ensure_gauge_icon(directory, 100, "critical", "dark", segments=[(100, "critical"), (20, "ok")])
            self.assertIn("segments: critical 100, ok 20", Path(path).read_text())
            single = icons.ensure_gauge_icon(directory, 100, "critical", "dark")
            self.assertNotEqual(single, path)  # same state and percent, different drawing
            self.assertIn("<title>critical: 100</title>", Path(single).read_text())
            with mock.patch.object(icons, "ICON_CACHE_LIMIT", 3):
                for pct in range(6):
                    latest = icons.ensure_gauge_icon(directory, pct, "ok", "dark", segments=[(pct, "ok"), (1, "ok")])
                    self.assertTrue(Path(latest).exists())
                self.assertEqual(len(list(Path(directory).glob("quota-*.svg"))), 3)


class TraySegmentedIconTests(unittest.TestCase):
    now = ReadingPresentationTests.now

    def tray(self, usages, directory):
        from unittest import mock
        from tests.test_cost_presentation import CostPresentationTests
        builder = CostPresentationTests()
        builder.build(ProviderUsage(provider="openai-api", source="synthetic", balance_usd=0))
        app = object.__new__(builder.tray_class)
        app.indicator = mock.Mock()
        app.icon_paths = ensure_icons(directory, theme="dark")
        app.icon_theme = "dark"
        app._last_usages = usages
        app._last_next_reset = ""
        app._last_updated_at = self.now
        app._dashboard = None
        app.on_config_saved = None
        app._build_menu = mock.Mock()
        return app, builder.tray_module

    def test_tray_icon_and_label_with_two_providers(self):
        from unittest import mock
        from codexbar_linux.config import AppConfig
        usages = [ProviderUsage(provider="codex", source="synthetic", primary=UsageWindow(100, 300), updated_at=self.now),
                  ProviderUsage(provider="claude", source="synthetic", primary=UsageWindow(20, 300), updated_at=self.now)]
        with tempfile.TemporaryDirectory() as directory:
            app, module = self.tray(usages, directory)
            config = AppConfig()
            app.get_config = lambda: config
            reading = lambda usages, **thresholds: tray_reading(usages, now=self.now, **thresholds)
            with mock.patch.object(module, "tray_reading", side_effect=reading):
                app._apply_status_icon()
                icon = Path(app.indicator.set_icon_full.call_args.args[0]).read_text()
                self.assertIn("segments: critical 100, ok 20", icon)
                self.assertIn("Segments: Codex 100% · Claude 20%", app.indicator.set_title.call_args.args[0])
                self.assertEqual(app.indicator.set_label.call_args.args[0], "Codex Primary (5h): 100% used")
                # Saving new levels re-colors the ring at once, not on the next minute tick.
                config = AppConfig(threshold_warning=10, threshold_critical=101)
                app._on_settings_saved(config)
                icon = Path(app.indicator.set_icon_full.call_args.args[0]).read_text()
                self.assertIn("segments: warning 100, warning 20", icon)

    def test_middle_click_opens_overview(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            app, _ = self.tray([], directory)
            overview = mock.Mock()
            def build(*args):
                app._overview_item = overview
                return mock.Mock()
            app._build_menu = build
            app._set_menu([], "")
            self.assertEqual(app.indicator.set_secondary_activate_target.call_args_list, [mock.call(None), mock.call(overview)])
