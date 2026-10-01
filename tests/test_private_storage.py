import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codexbar_linux import config, state
from codexbar_linux.core.logging import setup_logging


def permissions(path: Path) -> int:
    return path.stat().st_mode & 0o777


class PrivateStorageTests(unittest.TestCase):
    def test_config_directory_and_file_are_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary) / "config"
            config_path = config_dir / "config.json"
            with (
                mock.patch.object(config, "CONFIG_DIR", config_dir),
                mock.patch.object(config, "CONFIG_PATH", config_path),
            ):
                config.save_config(config.AppConfig(enabled_providers=["codex"]))
            self.assertEqual(permissions(config_dir), 0o700)
            self.assertEqual(permissions(config_path), 0o600)

    def test_state_and_history_are_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            state_path = state_dir / "state.json"
            history_path = state_dir / "history.jsonl"
            with (
                mock.patch.object(state, "STATE_DIR", state_dir),
                mock.patch.object(state, "STATE_PATH", state_path),
                mock.patch.object(state, "HISTORY_PATH", history_path),
            ):
                state.save_state({"windows": {}})
                state.append_history({"providers": []})
            self.assertEqual(permissions(state_dir), 0o700)
            self.assertEqual(permissions(state_path), 0o600)
            self.assertEqual(permissions(history_path), 0o600)

    def test_rotating_log_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_dir = Path(temporary) / "log"
            logger = setup_logging(log_dir=log_dir)
            logger.debug("test")
            for handler in list(logger.handlers):
                handler.flush()
                handler.close()
                logger.removeHandler(handler)
            self.assertEqual(permissions(log_dir), 0o700)
            self.assertEqual(permissions(log_dir / "codexbar-linux.log"), 0o600)


if __name__ == "__main__":
    unittest.main()
