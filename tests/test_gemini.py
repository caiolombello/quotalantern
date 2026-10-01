import unittest
from unittest import mock

from codexbar_linux.providers import gemini


class GeminiProviderTests(unittest.TestCase):
    def test_parse_agy_payload(self):
        sample_payload = {
            "conversation_id": "",
            "status": "SUCCESS",
            "command": {
                "name": "usage",
                "data": {
                    "groups": [
                        {
                            "name": "Gemini Models",
                            "buckets": [
                                {
                                    "id": "gemini-weekly",
                                    "name": "Weekly Limit Remaining",
                                    "window": "weekly",
                                    "remaining_fraction": 0.95,
                                    "reset_time": "2026-09-25T14:37:36Z",
                                },
                                {
                                    "id": "gemini-5h",
                                    "name": "Five Hour Limit Remaining",
                                    "window": "5h",
                                    "remaining_fraction": 0.80,
                                    "reset_time": "2026-09-18T19:37:36Z",
                                },
                            ],
                        },
                        {
                            "name": "Claude and GPT models",
                            "buckets": [
                                {
                                    "id": "3p-5h",
                                    "name": "Five Hour Limit Remaining",
                                    "window": "5h",
                                    "remaining_fraction": 1.0,
                                    "reset_time": "2026-09-18T20:30:00Z",
                                },
                                {
                                    "id": "3p-weekly",
                                    "name": "Weekly Limit Remaining",
                                    "window": "weekly",
                                    "remaining_fraction": 1.0,
                                    "reset_time": "2026-09-25T15:30:00Z",
                                },
                            ],
                        },
                    ]
                },
            },
        }
        usage = gemini._parse_agy_payload(sample_payload)
        self.assertIsNotNone(usage)
        self.assertEqual(usage.provider, "gemini")
        self.assertEqual(usage.source, "agy")
        self.assertEqual(usage.login_method, "Antigravity CLI")

        self.assertIsNotNone(usage.primary)
        self.assertEqual(usage.primary.used_percent, 20)
        self.assertEqual(usage.primary.window_minutes, 300)

        self.assertIsNotNone(usage.secondary)
        self.assertEqual(usage.secondary.used_percent, 5)
        self.assertEqual(usage.secondary.window_minutes, 10080)

        self.assertIsNotNone(usage.tertiary)
        self.assertEqual(usage.tertiary.used_percent, 0)

    def test_fetch_gemini_usage_fallback_when_agy_missing(self):
        mock_path = mock.MagicMock()
        mock_path.exists.return_value = False
        with (
            mock.patch("codexbar_linux.providers.gemini.fetch_via_agy_cli", return_value=None),
            mock.patch("codexbar_linux.providers.gemini.CREDENTIALS_PATH", mock_path),
            mock.patch("codexbar_linux.providers.gemini.resolve_agy", return_value=None),
        ):
            results = gemini.fetch_gemini_usage()
            self.assertEqual(len(results), 1)
            self.assertIn("missing", results[0].error.lower())


if __name__ == "__main__":
    unittest.main()
