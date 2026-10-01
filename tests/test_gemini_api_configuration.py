"""Publication must not fall back to an embedded key or project."""
import io,json,unittest
from unittest import mock
from codexbar_linux.providers import gemini_api

class GeminiAPIConfigurationTests(unittest.TestCase):
    def test_missing_configuration_does_not_read_cookies_or_send_request(self):
        with mock.patch.dict('os.environ',{},clear=True), mock.patch.object(gemini_api,'_load_google_cookies') as cookies, mock.patch.object(gemini_api.urllib.request,'urlopen') as request:
            result=gemini_api.fetch_gemini_api_usage()
        cookies.assert_not_called()
        request.assert_not_called()
        self.assertIn('requires GEMINI_AISTUDIO_API_KEY',result[0].error)
        self.assertIsNone(result[0].primary)

    def test_explicit_project_and_key_are_used_without_embedded_defaults(self):
        class Response(io.BytesIO):
            def getheader(self,name):return ''
        response=Response(json.dumps(['synthetic-project',['USD','100'],['USD','0',1000000000]]).encode())
        values={'GEMINI_AISTUDIO_API_KEY':'synthetic-api-key','GEMINI_AISTUDIO_PROJECT':'projects/synthetic-project','GOOGLE_COOKIES':'SAPISID=synthetic-cookie'}
        with mock.patch.dict('os.environ',values,clear=True),mock.patch.object(gemini_api.urllib.request,'urlopen',return_value=response) as request:
            result=gemini_api.fetch_gemini_api_usage()
        sent=request.call_args.args[0]
        self.assertEqual(json.loads(sent.data),['projects/synthetic-project'])
        self.assertEqual(sent.get_header('X-goog-api-key'),'synthetic-api-key')
        self.assertIsNone(result[0].error)
