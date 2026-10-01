"""Preserve legacy refresh without redistributing third-party client secrets."""
from pathlib import Path
import tempfile,unittest
from unittest import mock
from codexbar_linux.providers import gemini

class GeminiClientMetadataTests(unittest.TestCase):
    def test_missing_client_metadata_does_not_request_token(self):
        with mock.patch.object(gemini,'_oauth_client_secret',return_value=None),mock.patch.object(gemini.urllib.request,'urlopen') as request:
            self.assertIsNone(gemini._refresh_access_token({'refresh_token':'synthetic-refresh'}))
        request.assert_not_called()

    def test_explicit_metadata_is_used(self):
        with mock.patch.dict('os.environ',{'GEMINI_OAUTH_CLIENT_SECRET':'synthetic-client-secret'},clear=True):
            self.assertEqual(gemini._oauth_client_secret({}),'synthetic-client-secret')

    def test_legacy_metadata_is_parsed_without_execution_or_copying(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);path=home/'.local/share/codexbar-linux/codexbar_linux/providers/gemini.py';path.parent.mkdir(parents=True)
            path.write_text('raise RuntimeError("must not execute")\nOAUTH_CLIENT_ID = '+repr(gemini.OAUTH_CLIENT_ID)+'\nOAUTH_CLIENT_SECRET = "synthetic-legacy-secret"\n')
            before=path.read_bytes()
            with mock.patch.dict('os.environ',{},clear=True),mock.patch.object(gemini.Path,'home',return_value=home):
                self.assertEqual(gemini._oauth_client_secret({}),'synthetic-legacy-secret')
            self.assertEqual(path.read_bytes(),before)
