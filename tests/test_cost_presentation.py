"""Exercise real card/menu builders with in-memory GTK doubles, no display."""
import importlib
import sys
import types
import unittest
from unittest import mock

from codexbar_linux.config import AppConfig
from codexbar_linux.core.models import ProviderUsage
from codexbar_linux.ui_common import monetary_label


class Widget:
    def __init__(self, *args, **kwargs):
        self.label = kwargs.get("label", "")
        self.children = []

    def get_style_context(self):
        return self

    def add_class(self, *args):
        pass

    def add(self, widget):
        self.children.append(widget)

    def pack_start(self, widget, *args):
        self.add(widget)

    pack_end = pack_start
    append = add
    set_submenu = add

    def set_border_width(self, *args):
        pass

    def set_tooltip_text(self, *args):
        pass

    def set_line_wrap(self, *args):
        pass

    def set_ellipsize(self, *args):
        pass

    def set_sensitive(self, *args):
        pass

    def connect(self, *args):
        pass

    def labels(self):
        return [self.label] + [label for child in self.children for label in child.labels()]

    def __getattr__(self, name):
        # Layout-only setters (alignment, expansion) do not affect labels.
        if name.startswith("set_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


class CostPresentationTests(unittest.TestCase):
    def build(self, usage):
        gtk = types.SimpleNamespace(**{name: Widget for name in ("Frame", "Box", "Label", "Button", "Menu", "MenuItem", "ProgressBar")},
            Orientation=types.SimpleNamespace(VERTICAL=1, HORIZONTAL=0),
            Align=types.SimpleNamespace(START=1, CENTER=3, END=2))
        gi = types.ModuleType("gi")
        gi.require_version = lambda *args: None
        repo = types.ModuleType("gi.repository")
        repo.Gtk = gtk
        repo.Gdk = repo.GdkPixbuf = repo.Gio = repo.GLib = repo.AyatanaAppIndicator3 = types.SimpleNamespace()
        repo.Pango = types.SimpleNamespace(EllipsizeMode=types.SimpleNamespace(END=0))
        gi.repository = repo
        with mock.patch.dict(sys.modules, {"gi": gi, "gi.repository": repo}):
            # Fresh imports ensure this test never binds a real GTK display.
            sys.modules.pop("codexbar_linux.dashboard", None)
            sys.modules.pop("codexbar_linux.tray", None)
            dashboard = importlib.import_module("codexbar_linux.dashboard")
            tray = importlib.import_module("codexbar_linux.tray")
            self.tray_class = tray.TrayApp
            self.tray_module = tray
            card_app = object.__new__(dashboard.DashboardWindow)
            card_app.open_urls = {}
            card_app.on_refresh_provider = mock.Mock()
            card = card_app._card_for(usage, AppConfig())
            tray_app = object.__new__(tray.TrayApp)
            tray_app._append_refresh_provider_item = mock.Mock()
            tray_app._append_open_item = mock.Mock()
            menu = tray_app._provider_item(usage, AppConfig())
            return card.labels(), menu.labels()

    def test_monthly_spend_appears_in_card_and_tray_without_percent(self):
        for provider in ("openai-api", "claude-api"):
            usage = ProviderUsage(provider=provider, source="synthetic", balance_usd=12.5)
            for labels in self.build(usage):
                self.assertIn("Month-to-date cost: $12.50 USD", labels)
                self.assertFalse(any("%" in label for label in labels))

    def test_true_zero_cost_is_visible_without_allowance(self):
        for labels in self.build(ProviderUsage(provider="openai-api", source="synthetic", balance_usd=0.0)):
            self.assertIn("Month-to-date cost: $0.00 USD", labels)
            self.assertFalse(any("%" in label for label in labels))

    def test_other_balances_preserved_and_invalid_money_hidden(self):
        self.assertEqual(monetary_label(ProviderUsage(provider="other", source="synthetic", balance_usd=1.5)), "Balance: $1.50")
        for invalid in (None, True, float("nan"), float("inf"), "1"):
            self.assertEqual(monetary_label(ProviderUsage(provider="openai-api", source="synthetic", balance_usd=invalid)), "")
