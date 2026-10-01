import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codexbar_linux import desktop


class DesktopIntegrationTests(unittest.TestCase):
    def test_explicit_theme_override_wins(self):
        self.assertEqual(
            desktop.detect_desktop_theme(
                {"XDG_CURRENT_DESKTOP": "KDE", "CODEXBAR_THEME": "light"}
            ),
            "light",
        )

    @mock.patch("codexbar_linux.desktop._kde_theme", return_value="dark")
    def test_plasma_uses_kde_theme_first(self, kde_theme):
        self.assertEqual(
            desktop.detect_desktop_theme({"XDG_CURRENT_DESKTOP": "KDE"}),
            "dark",
        )
        kde_theme.assert_called_once_with()

    def test_rgb_brightness_handles_custom_kde_schemes(self):
        self.assertTrue(desktop._rgb_is_dark("20,22,24"))
        self.assertFalse(desktop._rgb_is_dark("245,245,245"))
        self.assertIsNone(desktop._rgb_is_dark("not-rgb"))

    @mock.patch(
        "codexbar_linux.desktop._command_output",
        return_value="org.kde.StatusNotifierWatcher 123 kded6",
    )
    def test_status_notifier_probe_is_read_only(self, command_output):
        self.assertTrue(desktop.status_notifier_available())
        command_output.assert_called_once()

    def test_private_file_status_does_not_read_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credential"
            path.write_text("do-not-read")
            path.chmod(0o600)
            self.assertEqual(
                desktop.private_file_status(path),
                {"present": True, "private": True, "mode": "0600"},
            )

    def test_current_desktop_fallback(self):
        self.assertEqual(
            desktop.current_desktop({"DESKTOP_SESSION": "plasma"}), "plasma"
        )
        self.assertTrue(desktop.is_plasma_session({"DESKTOP_SESSION": "plasma"}))


if __name__ == "__main__":
    unittest.main()
