from autoshorts.core.config import UploadConfig
from autoshorts.core.providers.gemini_provider import GeminiProvider


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


class _FakeClient:
    def __init__(self):
        self.payloads = []

    def post(self, url, params=None, json=None):
        self.payloads.append(json)
        return _FakeResponse()


def _provider(client):
    provider = GeminiProvider(api_key="test-key", settings=object(), http_client=client)
    provider.resolve_model = lambda: "test-model"
    return provider


def test_longform_daily_cap_defaults_to_20():
    assert UploadConfig().longform_daily_cap == 20


def test_gemini_default_max_output_tokens_is_1024():
    client = _FakeClient()
    _provider(client).complete("hello")
    assert client.payloads[-1]["generationConfig"]["maxOutputTokens"] == 1024


def test_gemini_explicit_max_tokens_is_respected():
    client = _FakeClient()
    _provider(client).complete("hello", max_tokens=4096)
    assert client.payloads[-1]["generationConfig"]["maxOutputTokens"] == 4096
