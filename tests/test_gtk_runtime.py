"""Desktop bindings are selected without a display or real system libraries."""
import sys
import types
import unittest
from unittest import mock

from codexbar_linux import diagnostics


class GtkRuntimeTests(unittest.TestCase):
    def bindings(self, available, broken=()):
        gi = types.ModuleType("gi")
        repo = types.ModuleType("gi.repository")

        def require_version(namespace, version):
            if namespace not in available:
                raise ValueError(f"Namespace {namespace} not available")

        def get_binding(namespace):
            if namespace in broken:
                raise ImportError(f"Cannot load {namespace}")
            raise AttributeError(namespace)

        gi.require_version = mock.Mock(side_effect=require_version)
        repo.__getattr__ = get_binding
        for namespace in available:
            if namespace not in broken:
                setattr(repo, namespace, types.SimpleNamespace(
                    __name__=namespace, MAJOR_VERSION=3, MINOR_VERSION=24, MICRO_VERSION=52,
                ))
        gi.repository = repo
        return gi, repo

    def status(self, available, broken=()):
        gi, repo = self.bindings(available, broken)
        with mock.patch.dict(sys.modules, {"gi": gi, "gi.repository": repo}):
            return diagnostics._gi_status()

    def test_fedora_appindicator_is_ready_without_ayatana(self):
        status = self.status({"Gtk", "AppIndicator3", "Notify"})
        self.assertTrue(status["gtk3"])
        self.assertTrue(status["notify"])
        self.assertTrue(status["appindicator"])
        self.assertFalse(status["ayatana_appindicator"])
        self.assertEqual(status["appindicator_backend"], "AppIndicator3")
        with mock.patch.object(diagnostics, "_gi_status", return_value=status), \
             mock.patch.object(diagnostics, "status_notifier_available", return_value=True), \
             mock.patch.object(diagnostics, "detect_desktop_theme", return_value="dark"), \
             mock.patch.object(diagnostics, "private_file_status", return_value={}), \
             mock.patch.object(diagnostics.shutil, "which", return_value="/usr/bin/tool"):
            self.assertTrue(diagnostics.platform_report()["ready"])

    def test_ayatana_is_preferred_when_both_bindings_exist(self):
        status = self.status({"Gtk", "AyatanaAppIndicator3", "AppIndicator3", "Notify"})
        self.assertTrue(status["ayatana_appindicator"])
        self.assertEqual(status["appindicator_backend"], "AyatanaAppIndicator3")

    def test_failed_ayatana_import_uses_appindicator(self):
        status = self.status(
            {"Gtk", "AyatanaAppIndicator3", "AppIndicator3", "Notify"},
            broken={"AyatanaAppIndicator3"},
        )
        self.assertTrue(status["appindicator"])
        self.assertEqual(status["appindicator_backend"], "AppIndicator3")

    def test_missing_indicator_does_not_hide_working_gtk_and_notify(self):
        status = self.status({"Gtk", "Notify"})
        self.assertTrue(status["gtk3"])
        self.assertTrue(status["notify"])
        self.assertFalse(status["appindicator"])
        self.assertIn("AyatanaAppIndicator3", status["error"])
        self.assertIn("AppIndicator3", status["error"])

    def test_missing_pygobject_is_reported_without_crashing(self):
        with mock.patch.dict(sys.modules, {"gi": None}):
            status = diagnostics._gi_status()
        self.assertFalse(status["gtk3"])
        self.assertFalse(status["appindicator"])
        self.assertFalse(status["notify"])
        self.assertIn("gi", status["error"])
