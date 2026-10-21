"""Installed launcher behavior without GTK, providers or real processes."""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class LauncherTests(unittest.TestCase):
    def test_default_launch_detaches_and_preserves_the_environment(self):
        from codexbar_linux import launcher

        child = mock.Mock(pid=1234)
        child.wait.side_effect = subprocess.TimeoutExpired("app", 1)
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch("pathlib.Path.home", return_value=Path(temporary)), \
             mock.patch.object(launcher.subprocess, "Popen", return_value=child) as spawn, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.launch_background(verbose=True), 0)
            args, kwargs = spawn.call_args
            self.assertIn("--foreground", args[0])
            self.assertIn("--verbose", args[0])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["env"]["QUOTALANTERN_BACKGROUND"], "1")
            self.assertEqual(kwargs["env"]["PATH"], os.environ["PATH"])
            log = Path(temporary) / ".local/share/codexbar-linux/log/launcher.log"
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_existing_instance_is_not_spawned_again(self):
        from codexbar_linux import launcher

        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch("pathlib.Path.home", return_value=Path(temporary)), \
             mock.patch.object(launcher.subprocess, "Popen") as spawn, \
             contextlib.redirect_stdout(io.StringIO()):
            with launcher.instance_lock() as first:
                self.assertIsNotNone(first)
                self.assertEqual(launcher.launch_background(), 0)
                self.assertEqual(spawn.call_count, 0)
            with launcher.instance_lock() as released:
                self.assertIsNotNone(released)

    def test_early_startup_failure_is_reported(self):
        from codexbar_linux import launcher

        child = mock.Mock()
        child.wait.return_value = 1
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch("pathlib.Path.home", return_value=Path(temporary)), \
             mock.patch.object(launcher.subprocess, "Popen", return_value=child), \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(launcher.launch_background(), 1)
            self.assertIn("launcher.log", errors.getvalue())

    def test_json_and_check_stay_synchronous_regardless_of_argument_order(self):
        from codexbar_linux import launcher

        for arguments in (["--verbose", "--check"], ["--json", "--verbose"], ["--foreground"]):
            with self.subTest(arguments=arguments), \
                 mock.patch("sys.argv", ["quotalantern", *arguments]), \
                 mock.patch.object(launcher, "launch_background") as background, \
                 mock.patch("codexbar_linux.__main__.main") as application, \
                 tempfile.TemporaryDirectory() as temporary, \
                 mock.patch("pathlib.Path.home", return_value=Path(temporary)):
                launcher.main()
                self.assertEqual(background.call_count, 0)
                self.assertEqual(application.call_count, 1)
