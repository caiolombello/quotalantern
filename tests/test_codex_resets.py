"""Codex Resets integration: strict parsing, polite polling, attributed display.

Synthetic payloads only; the HTTP opener is replaced, so nothing leaves the machine.
"""
import copy
import email.message
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from codexbar_linux import codex_resets as cr
from codexbar_linux import config
from codexbar_linux.presentation import reset_view

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
PAYLOAD = {
    "data": {
        "latest_reset": {
            "id": "100", "reset_type": "regular", "announced_at": "2026-10-07T09:00:00.000Z",
            "text": "Synthetic announcement: usage limits were reset.\nSecond line.",
            "source": {"type": "x_post", "author": "thsottiaux", "url": "https://x.com/thsottiaux/status/100"},
        },
        "scheduled_reset": None,
        "active_watch": None,
        "stats": {"total": 41, "last_reset_at": "2026-10-07T09:00:00.000Z", "days_since_last": 0.1, "avg_interval_days": 7.2},
    },
    "meta": {"api_version": "v1", "generated_at": "2026-10-07T11:59:00.000Z"},
}


def payload(**data):
    result = copy.deepcopy(PAYLOAD)
    result["data"].update(data)
    return result


class Response:
    def __init__(self, body, etag='"v1"'):
        self.raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"ETag": etag} if etag else {}

    def read(self, limit):
        return self.raw[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(cr.STATUS_URL, code, "error", headers, io.BytesIO(b""))


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class ParsingTests(unittest.TestCase):
    def test_valid_status_is_parsed(self):
        status = cr.parse_status(PAYLOAD)
        self.assertEqual((status.latest.id, status.latest.reset_type, status.total), ("100", "regular", 41))
        self.assertEqual(status.latest.announced_at, datetime(2026, 10, 7, 9, tzinfo=timezone.utc))
        self.assertEqual(status.latest.url, "https://x.com/thsottiaux/status/100")
        self.assertIsNone(status.scheduled)

    def test_links_are_limited_to_known_https_hosts(self):
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "http://x.com/a", "https://evil.example/x",
                    "https://user:pw@x.com/a", None, 42):
            source = {"type": "observed", "url": bad} if bad is not None else {"type": "observed"}
            status = cr.parse_status(payload(latest_reset={**PAYLOAD["data"]["latest_reset"], "source": source}))
            self.assertEqual(status.latest.url, cr.SITE_URL, bad)

    def test_text_is_cleaned_and_bounded(self):
        latest = {**PAYLOAD["data"]["latest_reset"], "text": "a\x00b\x1b[31m" + "x" * 2000}
        text = cr.parse_status(payload(latest_reset=latest)).latest.text
        self.assertNotIn("\x00", text)
        self.assertNotIn("\x1b", text)
        self.assertLessEqual(len(text), cr.TEXT_LIMIT)

    def test_malformed_payloads_are_rejected(self):
        latest = PAYLOAD["data"]["latest_reset"]
        cases = [
            None, [], {"data": {}}, {**PAYLOAD, "meta": {"api_version": "v2", "generated_at": "2026-10-07T00:00:00Z"}},
            payload(latest_reset={**latest, "reset_type": "surprise"}),
            payload(latest_reset={**latest, "announced_at": "2026-10-07T09:00:00"}),
            payload(latest_reset={**latest, "id": 100}),
            payload(stats={"total": -1}),
            payload(stats={"total": True}),
            payload(stats={"total": 3, "avg_interval_days": "7"}),
            payload(active_watch={"level": "certain"}),
            payload(scheduled_reset={**latest, "status": "done", "scheduled_for": None}),
        ]
        for case in cases:
            with self.assertRaises(ValueError, msg=case):
                cr.parse_status(case)

    def test_watch_and_scheduled_reset(self):
        watch = {"level": "strong", "reset_chance_percent": 70, "forecast_window": "next 24–48h",
                 "observed_at": "2026-10-07T10:00:00Z", "expires_at": "2026-10-08T10:00:00Z",
                 "text": "Hint", "source": {"type": "observed"}}
        scheduled = {**PAYLOAD["data"]["latest_reset"], "id": "200", "status": "scheduled",
                     "scheduled_for": "2026-10-09T18:00:00Z"}
        status = cr.parse_status(payload(active_watch=watch, scheduled_reset=scheduled))
        self.assertEqual((status.watch.level, status.watch.chance_percent), ("strong", 70))
        self.assertEqual(status.scheduled.scheduled_for, datetime(2026, 10, 9, 18, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            cr.parse_status(payload(active_watch={**watch, "reset_chance_percent": 101}))


class FeedTests(unittest.TestCase):
    def feed(self, *responses):
        self.requests = []
        queue = list(responses)

        def opener(request, timeout):
            self.requests.append(request)
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        self.clock = Clock()
        return cr.ResetFeed(open_url=opener, clock=self.clock, now=lambda: NOW)

    def test_request_is_anonymous_and_conditional(self):
        feed = self.feed(Response(PAYLOAD), http_error(304))
        self.assertTrue(feed.refresh())
        request = self.requests[0]
        self.assertEqual(request.full_url, cr.STATUS_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual({key.lower() for key, _ in request.header_items()}, {"accept", "user-agent"})
        self.clock.value += cr.MIN_INTERVAL_SECONDS
        self.assertTrue(feed.refresh())
        self.assertEqual(self.requests[1].get_header("If-none-match"), '"v1"')
        snapshot = feed.snapshot()
        self.assertEqual(snapshot.status.latest.id, "100")
        self.assertIsNone(snapshot.error)

    def test_polling_is_throttled_and_retry_after_is_respected(self):
        feed = self.feed(http_error(429, retry_after=3600), Response(PAYLOAD))
        with self.assertLogs("codexbar-linux", level="INFO"):
            self.assertTrue(feed.refresh())
        self.assertIn("Rate limited", feed.snapshot().error)
        self.clock.value += cr.MIN_INTERVAL_SECONDS
        self.assertFalse(feed.refresh())
        self.assertEqual(len(self.requests), 1)
        self.clock.value += 3600
        self.assertTrue(feed.refresh())
        self.assertFalse(feed.refresh())
        self.assertEqual(len(self.requests), 2)

    def test_failures_keep_the_last_good_status(self):
        too_large = Response(b"{" + b" " * (cr.MAX_RESPONSE_BYTES + 10) + b"}")
        failures = [urllib.error.URLError("down"), http_error(503), Response(b"not json"), too_large,
                    Response({"data": {}})]
        feed = self.feed(Response(PAYLOAD), *failures)
        feed.refresh()
        for _ in failures:
            self.clock.value += cr.MIN_INTERVAL_SECONDS
            with self.assertLogs("codexbar-linux", level="INFO"):
                self.assertTrue(feed.refresh())
            snapshot = feed.snapshot()
            self.assertEqual(snapshot.status.latest.id, "100")
            self.assertTrue(snapshot.error)

    def test_clear_forgets_data(self):
        feed = self.feed(Response(PAYLOAD), Response(PAYLOAD))
        feed.refresh()
        feed.clear()
        self.assertEqual(feed.snapshot(), cr.ResetSnapshot(None, None, None))
        self.assertTrue(feed.refresh())
        self.assertIsNone(self.requests[1].get_header("If-none-match"))


class AnnouncementTests(unittest.TestCase):
    def status(self, reset_id="100", reset_type="regular", scheduled_id=None):
        latest = {**PAYLOAD["data"]["latest_reset"], "id": reset_id, "reset_type": reset_type}
        scheduled = None
        if scheduled_id:
            scheduled = {**latest, "id": scheduled_id, "status": "scheduled", "scheduled_for": "2026-10-09T18:00:00Z"}
        return cr.parse_status(payload(latest_reset=latest, scheduled_reset=scheduled))

    def test_first_observation_is_silent_then_new_ids_notify_once(self):
        notes, seen = cr.new_announcements({}, self.status())
        self.assertEqual(notes, [])
        notes, seen = cr.new_announcements(seen, self.status())
        self.assertEqual(notes, [])
        notes, seen = cr.new_announcements(seen, self.status("101"))
        self.assertEqual([title for title, _ in notes], ["Codex limits were reset"])
        notes, seen = cr.new_announcements(seen, self.status("102", "banked"))
        self.assertEqual([title for title, _ in notes], ["Banked Codex reset granted"])
        notes, seen = cr.new_announcements(seen, self.status("102", "banked", scheduled_id="300"))
        self.assertEqual([title for title, _ in notes], ["Codex reset scheduled"])
        self.assertTrue(all("codex-resets.com" in body for _, body in notes))
        notes, _ = cr.new_announcements(seen, None)
        self.assertEqual(notes, [])


class ViewTests(unittest.TestCase):
    def view(self, status, error=None, checked=NOW):
        return reset_view(cr.ResetSnapshot(status, error, checked), now=NOW)

    def test_view_is_attributed_global_and_plain(self):
        view = self.view(cr.parse_status(PAYLOAD))
        self.assertEqual(view.headline, "Last global Codex reset: 3h ago (regular)")
        self.assertEqual(view.excerpt, "Synthetic announcement: usage limits were reset.")
        self.assertEqual(view.lines[-1], cr.ATTRIBUTION)
        self.assertIn("41 resets tracked", " ".join(view.lines))
        self.assertIsNone(reset_view(None))
        self.assertIsNone(reset_view(cr.ResetSnapshot(None, None, None)))

    def test_expired_watch_is_hidden_and_errors_disclose_cache_age(self):
        watch = {"level": "elevated", "reset_chance_percent": None, "forecast_window": "soon",
                 "observed_at": "2026-10-06T10:00:00Z", "expires_at": "2026-10-07T11:00:00Z",
                 "text": "Hint", "source": {"type": "observed"}}
        view = self.view(cr.parse_status(payload(active_watch=watch)))
        self.assertNotIn("Reset watch", " ".join(view.lines))
        live = {**watch, "expires_at": "2026-10-08T11:00:00Z"}
        view = self.view(cr.parse_status(payload(active_watch=live)))
        self.assertIn("Reset watch (elevated): chance not given, soon · AI forecast, not official", view.lines)
        view = self.view(cr.parse_status(PAYLOAD), error="Codex Resets HTTP 503", checked=NOW - timedelta(hours=2))
        self.assertIn("Codex Resets HTTP 503 · showing data checked 2h ago", view.lines)
        view = self.view(None, error="Codex Resets unreachable", checked=None)
        self.assertEqual(view.headline, "Codex reset announcements unavailable")


class ConfigTests(unittest.TestCase):
    def test_announcements_are_opt_in_and_persisted(self):
        self.assertFalse(config.AppConfig().codex_resets_enabled)
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary) / "config"
            with (
                mock.patch.object(config, "CONFIG_DIR", config_dir),
                mock.patch.object(config, "CONFIG_PATH", config_dir / "config.json"),
            ):
                config.save_config(config.AppConfig(codex_resets_enabled=True))
                self.assertTrue(config.load_config().codex_resets_enabled)
                (config_dir / "config.json").write_text('{"codex_resets_enabled": "yes"}')
                self.assertFalse(config.load_config().codex_resets_enabled)


try:
    from codexbar_linux import app as app_module
except (ImportError, ValueError):
    app_module = None


@unittest.skipIf(app_module is None, "GTK bindings unavailable")
class AppGlueTests(unittest.TestCase):
    def make(self, enabled):
        app = object.__new__(app_module.CodexBarLinuxApp)
        app.config = config.AppConfig(codex_resets_enabled=enabled)
        app._state = {}
        app._reset_feed = mock.Mock()
        app._reset_feed.refresh.return_value = True
        app._reset_feed.snapshot.return_value = cr.ResetSnapshot(cr.parse_status(PAYLOAD), None, NOW)
        app.tray = mock.Mock()
        app._notify = mock.Mock()
        return app

    def test_disabled_feature_makes_no_request(self):
        app = self.make(False)
        with mock.patch.object(app_module.GLib, "idle_add") as idle:
            app._check_codex_resets()
        app._reset_feed.refresh.assert_not_called()
        idle.assert_not_called()

    def test_first_check_is_silent_and_a_new_reset_notifies_once(self):
        app = self.make(True)
        with mock.patch.object(app_module.GLib, "idle_add") as idle:
            app._check_codex_resets()
            app._notify.assert_not_called()
            idle.assert_called_once_with(app.tray.set_codex_resets, app._reset_feed.snapshot.return_value)
            newer = copy.deepcopy(PAYLOAD)
            newer["data"]["latest_reset"]["id"] = "101"
            app._reset_feed.snapshot.return_value = cr.ResetSnapshot(cr.parse_status(newer), None, NOW)
            app._check_codex_resets()
            app._check_codex_resets()
        app._notify.assert_called_once()
        self.assertEqual(app._notify.call_args.args[0], "Codex limits were reset")
        self.assertEqual(app._state["codex_resets"]["reset_id"], "101")


if __name__ == "__main__":
    unittest.main()
