"""Exercise the real installer preflight with a synthetic HOME and probe."""
import contextlib
import hashlib
import io
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install_local.py"


class InstallerPreflightTests(unittest.TestCase):
    def test_explicit_autostart_enables_disabled_entry_and_preserves_other_options(self):
        for enable in (False, True):
            with self.subTest(enable=enable), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                autostart = home / ".config/autostart/codexbar-linux.desktop"
                autostart.parent.mkdir(parents=True)
                autostart.write_text("[Desktop Entry]\nName=Old name\nHidden=true\n"
                                     "X-GNOME-Autostart-enabled=false\nOnlyShowIn=KDE;\n"
                                     "X-GNOME-Autostart-Delay=15\n")
                def probe(command, **kwargs):
                    output = '{"appindicator_backend":"AppIndicator3"}' if '-c' in command else ''
                    return subprocess.CompletedProcess(command, 0, stdout=output, stderr='')
                arguments = [str(INSTALLER)] + (["--enable-autostart"] if enable else [])
                with mock.patch("sys.argv", arguments), \
                     mock.patch("pathlib.Path.home", return_value=home), \
                     mock.patch("subprocess.run", side_effect=probe), \
                     contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(INSTALLER), run_name="__main__")
                text = autostart.read_text()
                self.assertIn("Hidden=false" if enable else "Hidden=true", text)
                self.assertIn("X-GNOME-Autostart-enabled=true" if enable else "X-GNOME-Autostart-enabled=false", text)
                self.assertIn("OnlyShowIn=KDE;", text)
                self.assertIn("X-GNOME-Autostart-Delay=15", text)

    def test_modified_source_is_rejected_before_running_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            script = source / "scripts" / "install_local.py"
            script.parent.mkdir()
            script.write_bytes(INSTALLER.read_bytes())
            helper = source / "codexbar_linux" / "gtk_runtime.py"
            helper.parent.mkdir()
            helper.write_text("# modified source\n")
            (source / "BUILD.json").write_text(json.dumps({"file_sha256": {
                "codexbar_linux/gtk_runtime.py": hashlib.sha256(b"original source").hexdigest(),
            }}))
            checked = subprocess.CompletedProcess([], 0, stdout='{"appindicator_backend":"AppIndicator3"}')
            with mock.patch("sys.argv", [str(script), "--dry-run"]), \
                 mock.patch("pathlib.Path.home", return_value=source / "home"), \
                 mock.patch("subprocess.run", return_value=checked) as probe:
                with self.assertRaisesRegex(AssertionError, "Source hash differs"):
                    runpy.run_path(str(script), run_name="__main__")
            self.assertEqual(probe.call_count, 0)

    def run_installer(self, distro, returncode):
        def probe(command, **kwargs):
            if returncode and kwargs.get("check"):
                raise subprocess.CalledProcessError(returncode, command)
            return subprocess.CompletedProcess(command, returncode, stdout=json.dumps({
                "gtk3": True, "appindicator": not returncode,
                "appindicator_backend": "AppIndicator3" if not returncode else None,
                "notify": True, "error": "Missing AppIndicator" if returncode else "",
            }), stderr="")

        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with mock.patch("sys.argv", [str(INSTALLER), "--dry-run"]), \
                 mock.patch("pathlib.Path.home", return_value=Path(temporary)), \
                 mock.patch("platform.freedesktop_os_release", return_value={"ID": distro}), \
                 mock.patch("subprocess.run", side_effect=probe), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(INSTALLER), run_name="__main__")
            self.assertEqual(list(Path(temporary).iterdir()), [])
            return stopped.exception.code, output.getvalue()

    def test_missing_fedora_dependencies_show_dnf_command(self):
        code, output = self.run_installer("fedora", 1)
        self.assertNotEqual(code, 0)
        message = output + str(code)
        self.assertIn("sudo dnf install", message)
        self.assertIn("libayatana-appindicator-gtk3", message)
        self.assertNotIn("Traceback", message)

    def test_missing_debian_dependencies_show_apt_command(self):
        code, output = self.run_installer("debian", 1)
        self.assertNotEqual(code, 0)
        self.assertIn("sudo apt install", output + str(code))

    def test_successful_dry_run_accepts_fedora_backend_without_writing(self):
        code, output = self.run_installer("fedora", 0)
        self.assertEqual(code, 0)
        self.assertIn("AppIndicator3", output)
