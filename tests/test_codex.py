import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from codexbar_linux.providers import codex


class CodexProviderSecurityTests(unittest.TestCase):
    def test_auth_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            auth_path = Path(temporary) / "auth.json"
            auth_path.write_text(json.dumps({"tokens": {"access_token": "test-token"}}))
            with mock.patch.object(
                codex.os.path, "expanduser", return_value=str(auth_path)
            ):
                auth_path.chmod(0o644)
                self.assertIsNone(codex._load_auth_token())
                auth_path.chmod(0o600)
                self.assertEqual(codex._load_auth_token(), "test-token")

    def test_http_error_body_is_not_persisted(self):
        error = urllib.error.HTTPError(
            "https://chatgpt.com/backend-api/wham/usage",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b"private-account-marker"),
        )
        with (
            mock.patch.object(codex, "_load_auth_token", return_value="test-token"),
            mock.patch.object(codex.urllib.request, "urlopen", side_effect=error),
        ):
            result = codex.fetch_codex_usage()
        self.assertEqual(result[0].error, "OpenAI usage endpoint HTTP 401")
        self.assertNotIn("private-account-marker", result[0].error)
        self.assertTrue(error.closed)


if __name__ == "__main__":
    unittest.main()
