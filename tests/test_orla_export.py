"""Opt-in Orla quota export: projection, consent and private publication."""

import contextlib
import fcntl
import io
import json
import os
import re
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from codexbar_linux import orla_export as ox
from codexbar_linux.core.models import ProviderUsage, UsageWindow

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "contrib/orla/orla-quota-v1.schema.json").read_text())
NOW = 1_800_000_000_000
INTERVAL = 300
LEASE = 3000 * INTERVAL


def at(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def usage(pid, *, error=None, observed=NOW - 60_000, windows=((40, 300, NOW + 3_600_000),)):
    slots = [UsageWindow(used_percent=p, window_minutes=m, reset_description="Pro plan $20 · resets soon",
                         resets_at=at(r) if r else None) for p, m, r in windows]
    slots += [None] * (3 - len(slots))
    return ProviderUsage(provider=pid, source="oauth", version="9.9.9", account_email="person@example.invalid",
                         login_method="Max (OAuth)", primary=slots[0], secondary=slots[1], tertiary=slots[2],
                         credits_remaining=4321, balance_usd=12.5, updated_at=at(observed) if observed else None,
                         error=error)


def check_schema(schema, value, path="$"):
    """Minimal walker for the keywords the vendored Orla schema uses."""
    known = {"type", "const", "enum", "properties", "required", "additionalProperties", "items",
             "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum", "$schema", "$comment"}
    assert set(schema) <= known, (path, set(schema) - known)
    if "const" in schema:
        assert value == schema["const"], path
    if "enum" in schema:
        assert value in schema["enum"], path
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        checks = {"integer": lambda v: type(v) is int, "null": lambda v: v is None,
                  "string": lambda v: type(v) is str, "object": lambda v: type(v) is dict,
                  "array": lambda v: type(v) is list}
        assert any(checks[kind](value) for kind in kinds), (path, value)
    if type(value) is dict:
        assert set(schema["required"]) <= set(value), path
        assert schema.get("additionalProperties") is not False or set(value) <= set(schema["properties"]), path
        for key, item in value.items():
            check_schema(schema["properties"][key], item, f"{path}.{key}")
    elif type(value) is list:
        assert len(value) <= schema["maxItems"], path
        for index, item in enumerate(value):
            check_schema(schema["items"], item, f"{path}[{index}]")
    elif type(value) is str:
        assert schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)), path
        assert "pattern" not in schema or re.search(schema["pattern"], value), path
    elif type(value) is int:
        assert schema.get("minimum", value) <= value <= schema.get("maximum", value), path


def strings(value):
    if type(value) is dict:
        return {s for item in value.values() for s in strings(item)}
    if type(value) is list:
        return {s for item in value for s in strings(item)}
    return {value} if type(value) is str else set()


def project(usages, **kwargs):
    kwargs.setdefault("interval_seconds", INTERVAL)
    return ox.project(usages, now_ms=NOW, producer_version="0.1.0", **kwargs)


class ProjectionTests(unittest.TestCase):
    def test_export_keeps_only_quota_fields_and_matches_the_orla_schema(self):
        value = project([
            usage("codex", windows=((0, 300, NOW + 3_600_000), (42, 10080, NOW + 86_400_000))),
            usage("claude", error="HTTP 500 from provider (showing stale data)", observed=NOW - 600_000),
            usage("gemini", error="Not authenticated: person@example.invalid", observed=None, windows=()),
            usage("grok", windows=()),
            usage("claude-api"),
            usage("not-a-provider"),
        ])
        check_schema(SCHEMA, value)
        self.assertEqual(value["expiresAtMs"], NOW + LEASE)
        rows = {p["id"]: p for p in value["providers"]}
        self.assertEqual([p["id"] for p in value["providers"]], ["codex", "claude", "gemini", "grok"])
        self.assertEqual({k: p["state"] for k, p in rows.items()},
                         dict(codex="ready", claude="stale", gemini="unavailable", grok="unknown"))
        self.assertEqual([w["usedPercent"] for w in rows["codex"]["windows"]], [0, 42])
        self.assertEqual([w["key"] for w in rows["codex"]["windows"]], ["primary", "secondary"])
        # A failed refresh keeps the window shape and original time, never the percentage.
        self.assertEqual(rows["claude"]["windows"],
                         [dict(key="primary", usedPercent=None, windowMinutes=300, resetAtMs=NOW + 3_600_000)])
        self.assertEqual(rows["claude"]["observedAtMs"], NOW - 600_000)
        self.assertEqual((rows["gemini"]["observedAtMs"], rows["gemini"]["windows"]), (None, []))
        # Accounts, sign-in, provider messages, descriptions, credits and money never leave.
        allowed = {"0.1-experimental", "quotalantern", "0.1.0", "codex", "claude", "gemini", "grok",
                   "ready", "stale", "unavailable", "unknown", "primary", "secondary"}
        self.assertLessEqual(strings(value), allowed)
        text = ox.encode(value).decode()
        for private in ("example.invalid", "OAuth", "$20", "4321", "12.5", "HTTP 500", "9.9.9"):
            self.assertNotIn(private, text)

    def test_zero_stays_known_and_invalid_percentages_stay_unknown(self):
        value = project([usage("codex", windows=((0, 300, None), (150, None, None), (float("nan"), None, None))),
                         usage("cursor", windows=((-1, None, None), (True, None, None), (42.6, None, None)))])
        self.assertEqual([[w["usedPercent"] for w in p["windows"]] for p in value["providers"]],
                         [[0, 100, None], [None, None, 43]])
        self.assertEqual([p["state"] for p in value["providers"]], ["ready", "ready"])
        only_invalid = project([usage("codex", windows=((None, 300, None),))])
        self.assertEqual(only_invalid["providers"][0]["state"], "unknown")

    def test_old_future_and_missing_observations_are_not_confirmed(self):
        for observed, expected in ((NOW - LEASE, NOW - LEASE), (NOW + 1000, None), (None, None)):
            with self.subTest(observed=observed):
                row = project([usage("codex", observed=observed)])["providers"][0]
                self.assertEqual((row["state"], row["observedAtMs"]), ("stale", expected))
                self.assertIsNone(row["windows"][0]["usedPercent"])
        fresh = project([usage("codex", observed=NOW - LEASE + 1)])["providers"][0]
        self.assertEqual(fresh["state"], "ready")

    def test_interval_and_window_fields_are_bounded(self):
        for interval, expected in ((5, 10), (300, 300), (7200, 3600)):
            value = project([], interval_seconds=interval)
            self.assertEqual((value["intervalSeconds"], value["expiresAtMs"]), (expected, NOW + 3000 * expected))
        row = project([usage("codex", windows=((10, 0, None), (10, 525601, None), (10, True, None)))])["providers"][0]
        self.assertEqual([w["windowMinutes"] for w in row["windows"]], [None, None, None])

    def test_duplicate_and_excess_providers_are_dropped(self):
        ids = [f"p{n}" for n in range(40)]
        value = project([usage("p0", windows=((1, None, None),)), usage("p0", windows=((99, None, None),))]
                        + [usage(pid) for pid in ids], subscription_ids=set(ids))
        self.assertEqual(len(value["providers"]), 32)
        self.assertEqual(value["providers"][0]["windows"][0]["usedPercent"], 1)
        check_schema(SCHEMA, value)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = ox.Paths(config_home=self.root / "config", state_home=self.root / "state")
        self.export = self.root / "state/quotalantern/exports/orla-quota-v1.json"
        self.consent = self.root / "config/quotalantern/exports/orla.json"

    def tearDown(self):
        self.temporary.cleanup()

    def publish(self, **kwargs):
        return ox.publish([usage("codex")], interval_seconds=INTERVAL, paths=self.paths, now_ms=NOW, **kwargs)

    def plant_export(self, text=b"{}"):
        for directory in (self.root / "state", self.root / "state/quotalantern", self.export.parent):
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        self.export.write_bytes(text)
        self.export.chmod(0o600)

    def mode(self, path):
        return stat.S_IMODE(path.lstat().st_mode)

    def test_default_is_off_and_status_or_publish_create_nothing(self):
        self.assertEqual(ox.status(self.paths), dict(enabled=False, exportPresent=False, exportBytes=None))
        self.assertFalse(self.publish())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_enable_waits_for_a_refresh_then_publishes_private_atomic_files(self):
        self.plant_export()
        self.assertEqual(ox.command("enable", self.paths), dict(enabled=True, exportPresent=False, exportBytes=None))
        self.assertEqual(self.consent.read_bytes(), b'{"formatVersion":"0.1-experimental","enabled":true}')
        self.assertTrue(self.publish())
        value = json.loads(self.export.read_bytes())
        check_schema(SCHEMA, value)
        self.assertEqual(value["providers"][0]["windows"][0]["usedPercent"], 40)
        self.assertEqual(ox.status(self.paths)["exportBytes"], self.export.stat().st_size)
        for path in (self.export.parent, self.export.parent.parent, self.consent.parent, self.consent.parent.parent):
            self.assertEqual(self.mode(path), 0o700, path)
        for path in (self.export, self.consent):
            self.assertEqual(self.mode(path), 0o600, path)
        self.assertTrue(self.publish())
        self.assertEqual(os.listdir(self.export.parent), ["orla-quota-v1.json"])

    def test_disable_removes_the_export_and_blocks_later_publication(self):
        ox.command("enable", self.paths)
        self.publish()
        self.assertEqual(ox.command("disable", self.paths), dict(enabled=False, exportPresent=False, exportBytes=None))
        self.assertEqual(json.loads(self.consent.read_bytes())["enabled"], False)
        self.assertFalse(self.publish())
        self.assertFalse(self.export.exists())

    def test_disable_without_consent_creates_nothing_and_clears_a_stray_export(self):
        self.plant_export()
        self.assertEqual(ox.command("disable", self.paths)["exportPresent"], False)
        self.assertFalse((self.root / "config").exists())

    def test_invalid_consent_is_off_and_removes_an_old_export(self):
        valid = b'{"formatVersion":"0.1-experimental","enabled":true}'
        outside = self.root / "outside.json"
        outside.write_bytes(valid)
        outside.chmod(0o600)
        cases = {
            "duplicate": b'{"formatVersion":"0.1-experimental","enabled":false,"enabled":true}',
            "extra": b'{"formatVersion":"0.1-experimental","enabled":true,"x":1}',
            "array": b'[true]',
            "string": b'{"formatVersion":"0.1-experimental","enabled":"true"}',
            "version": b'{"formatVersion":"0.2","enabled":true}',
            "nan": b'{"formatVersion":"0.1-experimental","enabled":NaN}',
            "utf8": b'\xff',
            "large": valid + b" " * 2048,
        }
        for name, data in [*cases.items(), ("mode", valid), ("symlink", None), ("hardlink", None)]:
            with self.subTest(name=name):
                ox.command("enable", self.paths)
                self.assertTrue(self.publish())
                self.consent.unlink()
                if name == "symlink":
                    self.consent.symlink_to(outside)
                elif name == "hardlink":
                    os.link(outside, self.consent)
                else:
                    self.consent.write_bytes(data)
                    self.consent.chmod(0o644 if name == "mode" else 0o600)
                self.assertFalse(ox.status(self.paths)["enabled"])
                self.assertFalse(self.publish())
                self.assertFalse(self.export.exists())
                self.consent.unlink()
        self.assertEqual(outside.read_bytes(), valid)

    def test_unsafe_paths_are_refused_without_touching_targets(self):
        ox.command("enable", self.paths)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (self.root / "state/quotalantern").mkdir(parents=True, mode=0o700)
        (self.root / "state/quotalantern/exports").symlink_to(outside)
        with self.assertRaises(ox.ExportError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, "unsafe")
        self.assertEqual(list(outside.iterdir()), [])

        (self.root / "state/quotalantern/exports").unlink()
        self.export.parent.mkdir(mode=0o700)
        target = outside / "target.json"
        target.write_bytes(b"keep")
        self.export.symlink_to(target)
        with self.assertRaises(ox.ExportError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, "unsafe")
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertFalse(self.export.is_symlink())

        self.export.parent.chmod(0o755)
        with self.assertRaises(ox.ExportError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, "unsafe")

    def test_a_held_lock_refuses_consent_changes_and_publication(self):
        ox.command("enable", self.paths)
        with open(self.consent.parent / "orla.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for call in (lambda: ox.command("disable", self.paths, timeout=0.1), lambda: self.publish(timeout=0.1)):
                with self.assertRaises(ox.ExportError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "busy")
        self.assertTrue(ox.status(self.paths)["enabled"])
        self.assertFalse(self.export.exists())


class CommandLineTests(unittest.TestCase):
    def run_main(self, *arguments):
        from codexbar_linux import __main__ as entry

        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.argv", ["quotalantern", "--orla-export", *arguments]), \
             mock.patch("codexbar_linux.codexbar.fetch_all", side_effect=AssertionError("fetch")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                entry.main()
                code = 0
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def test_commands_manage_consent_without_fetching_providers(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(HOME=temporary, XDG_CONFIG_HOME=f"{temporary}/c", XDG_STATE_HOME=f"{temporary}/s")
            with mock.patch.dict(os.environ, env):
                self.assertEqual(self.run_main("status")[1].strip(),
                                 '{"enabled": false, "exportBytes": null, "exportPresent": false}')
                self.assertEqual(json.loads(self.run_main("enable")[1])["enabled"], True)
                self.assertFalse(Path(temporary, "s").exists())
                Path(temporary, "c/quotalantern/exports").chmod(0o755)
                self.assertEqual(self.run_main("disable"), (1, "", '{"error": "unsafe"}\n'))

    def test_relative_xdg_paths_fall_back_to_home(self):
        paths = ox.Paths.system(dict(HOME="/tmp/h", XDG_CONFIG_HOME="relative", XDG_STATE_HOME="/tmp/s"))
        self.assertEqual(paths, ox.Paths(Path("/tmp/h/.config"), Path("/tmp/s")))


try:
    from codexbar_linux import app as app_module
except (ImportError, ValueError):
    app_module = None


@unittest.skipIf(app_module is None, "GTK bindings unavailable")
class AppGlueTests(unittest.TestCase):
    def make(self):
        from codexbar_linux import config

        app = object.__new__(app_module.CodexBarLinuxApp)
        app.config = config.AppConfig(refresh_interval_seconds=120)
        app._state = {}
        app._usages = []
        app._reset_feed = mock.Mock()
        app.tray = mock.Mock()
        app._notify = mock.Mock()
        return app

    @contextlib.contextmanager
    def quiet(self):
        with mock.patch.object(app_module, "append_history"), mock.patch.object(app_module, "save_state"), \
             mock.patch.object(app_module.GLib, "idle_add"), \
             mock.patch.object(app_module.orla_export, "publish") as publish:
            yield publish

    def test_refresh_publishes_only_after_a_genuine_fetch(self):
        app = self.make()
        reading = [usage("codex")]
        with self.quiet() as publish, mock.patch.object(app_module, "fetch_all", return_value=reading):
            app._refresh_locked()
        publish.assert_called_once_with(reading, interval_seconds=120)
        with self.quiet() as publish, mock.patch.object(app_module, "fetch_all", side_effect=RuntimeError("down")), \
             self.assertLogs("codexbar-linux", level="ERROR"):
            app._refresh_locked()
        publish.assert_not_called()

    def test_export_failure_logs_only_its_code_and_never_stops_the_refresh(self):
        app = self.make()
        with self.quiet() as publish, mock.patch.object(app_module, "fetch_all", return_value=[usage("codex")]), \
             self.assertLogs("codexbar-linux", level="WARNING") as logs:
            publish.side_effect = ox.ExportError("unsafe")
            app._refresh_locked()
        self.assertEqual(logs.output, ["WARNING:codexbar-linux:Orla export not published: unsafe"])


if __name__ == "__main__":
    unittest.main()
