"""Unit tests for Track A LLM Provider Abstraction.

Covers:
- LocalProvider: lazy imports, dynamic GGUF resolution, single-forward-pass logit extraction,
  and tokenizer-resolved token IDs without hardcoded models.
- GeminiProvider: mocked HTTP via httpx.MockTransport, dynamic list_models discovery,
  responseLogprobs scoring, and constrained majority-voting fallback.
- OpenRouterProvider: mocked HTTP via httpx.MockTransport, dynamic model listing,
  auto-routing resolution, logprobs extraction, and constrained single-token fallback.
- Zero network calls and zero hardcoded model strings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from autoshorts.core.config import Settings
from autoshorts.core.contracts import LLMProvider
from autoshorts.core.exceptions import ProviderError
from autoshorts.core.providers.base import BaseLLMProvider
from autoshorts.core.providers.gemini_provider import GeminiProvider
from autoshorts.core.providers.local_provider import LocalProvider
from autoshorts.core.providers.openrouter_provider import OpenRouterProvider

# -----------------------------------------------------------------------------
# BaseLLMProvider & Softmax Tests
# -----------------------------------------------------------------------------


def test_softmax_numerical_stability():
    """Verify softmax handles large logits, identical logits, and negative infinities."""
    # Large logits that would overflow standard exp(x)
    large_logits = {"A": 1000.0, "B": 1000.0}
    probs = BaseLLMProvider.compute_softmax(large_logits)
    assert pytest.approx(probs["A"], 0.001) == 0.5
    assert pytest.approx(probs["B"], 0.001) == 0.5
    assert pytest.approx(sum(probs.values()), 0.001) == 1.0

    # Dominant logit
    dominant = {"A": 100.0, "B": 0.0}
    probs_dom = BaseLLMProvider.compute_softmax(dominant)
    assert probs_dom["A"] > 0.999
    assert probs_dom["B"] < 0.001
    assert pytest.approx(sum(probs_dom.values()), 0.001) == 1.0

    # Negative infinity / unchosen tokens
    with_inf = {"A": 5.0, "B": float("-inf")}
    probs_inf = BaseLLMProvider.compute_softmax(with_inf)
    assert pytest.approx(probs_inf["A"], 0.001) == 1.0
    assert pytest.approx(probs_inf["B"], 0.001) == 0.0


def test_option_normalization():
    """Verify options are standardized to uppercase letters."""
    list_opts = ["Accept candidate", "Reject candidate"]
    norm = BaseLLMProvider.normalize_options(list_opts)
    assert norm == {"A": "Accept candidate", "B": "Reject candidate"}

    dict_opts = {"a": "Option One", "b": "Option Two"}
    norm_dict = BaseLLMProvider.normalize_options(dict_opts)
    assert norm_dict == {"A": "Option One", "B": "Option Two"}


# -----------------------------------------------------------------------------
# Local Provider Tests (CPU GGUF)
# -----------------------------------------------------------------------------


def test_local_provider_lazy_import_error(monkeypatch: pytest.MonkeyPatch):
    """Verify LocalProvider raises informative ProviderError if llama-cpp-python is missing."""
    import sys

    monkeypatch.setitem(sys.modules, "llama_cpp", None)

    provider = LocalProvider()
    with pytest.raises(ProviderError) as exc_info:
        provider.complete("Test prompt")
    assert "llama-cpp-python is not installed" in str(exc_info.value)


def test_local_provider_contract_and_privacy():
    """Verify LocalProvider implements LLMProvider and marks privacy badge correctly."""
    provider = LocalProvider()
    assert isinstance(provider, LLMProvider)
    assert provider.name == "local"
    assert provider.is_local is True
    assert provider.supports_logprobs is True
    assert "100% On-Device" in provider.privacy_badge


def test_local_provider_dynamic_model_listing(tmp_path: Path):
    """Verify LocalProvider discovers .gguf files dynamically without hardcoding."""
    mock_models_dir = tmp_path / "data" / "models"
    mock_models_dir.mkdir(parents=True)
    fake_model = mock_models_dir / "custom_test_model.gguf"
    fake_model.write_text("synthetic gguf binary header")

    # Point data_dir to mock
    import autoshorts.core.providers.local_provider as lp_mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(lp_mod, "get_data_dir", lambda: tmp_path / "data")

    try:
        provider = LocalProvider()
        models = provider.list_models()
        assert len(models) == 1
        assert "custom_test_model.gguf" in models[0]
    finally:
        monkeypatch.undo()


def test_local_provider_single_forward_pass_scoring():
    """Verify LocalProvider single-forward-pass logit extraction using tokenizer."""
    # Create mock model instance with tokenizer and scores
    mock_model = MagicMock()

    # Mock tokenizer mapping "A" -> [101], "B" -> [102]
    def mock_tokenize(b_text: bytes, **kwargs: Any) -> list[int]:
        text = b_text.decode("utf-8").strip()
        if text == "A":
            return [101]
        elif text == "B":
            return [102]
        return [1, 2, 3]

    mock_model.tokenize.side_effect = mock_tokenize
    # Mock scores with higher logit for token 101 ("A") than 102 ("B")
    logits_array = [0.0] * 200
    logits_array[101] = 4.0
    logits_array[102] = 2.0
    mock_model.scores = [logits_array]

    provider = LocalProvider(model_instance=mock_model)
    result = provider.score_options("Evaluate this window", ["A", "B"])

    assert "A" in result and "B" in result
    assert result["A"] > result["B"]
    assert pytest.approx(sum(result.values()), 0.001) == 1.0
    # Softmax of [4.0, 2.0]: exp(2)/(exp(2)+1) ~ 0.8808
    assert result["A"] > 0.85
    mock_model.eval.assert_called_once()


# -----------------------------------------------------------------------------
# Google Gemini Provider Tests (Mocked HTTP)
# -----------------------------------------------------------------------------


def test_gemini_provider_missing_key(monkeypatch):
    """Verify GeminiProvider raises when API key is missing.

    The key must be neutralised at the settings accessor, not just in os.environ: Settings
    is backed by a .env file, so a developer machine with a real GEMINI_API_KEY in .env
    would otherwise make this test assert nothing.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = Settings(gemini_api_key=None)
    monkeypatch.setattr(Settings, "get_gemini_api_key_str", lambda self: None)
    provider = GeminiProvider(api_key=None, settings=settings)
    with pytest.raises(ProviderError) as exc_info:
        provider.complete("Hello")
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


def test_gemini_dynamic_model_resolution():
    """Verify GeminiProvider resolves default model dynamically from API list."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models":
            data = {
                "models": [
                    {
                        "name": "models/dynamic-test-pro-model",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/dynamic-test-flash-model",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ]
            }
            return httpx.Response(200, json=data)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="mock_key", http_client=client)

    # Must prioritize model matching 'flash' dynamically
    resolved = provider.resolve_model()
    assert resolved == "dynamic-test-flash-model"
    assert isinstance(provider, LLMProvider)
    assert provider.is_local is False
    assert "Cloud API Active" in provider.privacy_badge


def test_gemini_score_options_with_response_logprobs():
    """Verify GeminiProvider score_options with responseLogprobs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/mock-model",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        if ":generateContent" in request.url.path:
            body = json.loads(request.content)
            # Verify temperature 0 and responseLogprobs requested
            gen_cfg = body.get("generationConfig", {})
            assert gen_cfg.get("responseLogprobs") is True
            assert gen_cfg.get("temperature") == 0.0

            # Mock logprobs response
            data = {
                "candidates": [
                    {
                        "logprobsResult": {
                            "topCandidates": [
                                {
                                    "candidates": [
                                        {"token": "A", "logProbability": -0.223},
                                        {"token": "B", "logProbability": -1.609},
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
            return httpx.Response(200, json=data)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="mock_key", http_client=client)

    probs = provider.score_options("Score this candidate", {"A": "Accept", "B": "Reject"})
    assert "A" in probs and "B" in probs
    assert probs["A"] > probs["B"]
    assert pytest.approx(sum(probs.values()), 0.001) == 1.0


def test_gemini_score_options_fallback_voting():
    """Verify GeminiProvider falls back to constrained voting when logprobs is absent."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/mock-model",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        if ":generateContent" in request.url.path:
            body = json.loads(request.content)
            gen_cfg = body.get("generationConfig", {})
            # If responseLogprobs requested, return empty logprobsResult to trigger fallback
            if gen_cfg.get("responseLogprobs"):
                return httpx.Response(
                    200, json={"candidates": [{"content": {"parts": [{"text": "A"}]}}]}
                )

            # Constrained fallback JSON request
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": '"A"'}]}}]}
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="mock_key", http_client=client)

    probs = provider.score_options("Rate window", {"A": "Keep", "B": "Drop"})
    assert probs["A"] == 1.0
    assert probs["B"] == 0.0


def test_gemini_complete():
    """Verify GeminiProvider text completion."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/mock-model",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        if ":generateContent" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "Generated Viral Title #Shorts"}]}}
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="mock_key", http_client=client)
    res = provider.complete("Generate a title")
    assert res == "Generated Viral Title #Shorts"


# -----------------------------------------------------------------------------
# OpenRouter Provider Tests (Mocked HTTP)
# -----------------------------------------------------------------------------


def test_openrouter_missing_key():
    """Verify OpenRouterProvider raises when API key is missing."""
    settings = Settings()
    provider = OpenRouterProvider(api_key=None, settings=settings)
    with pytest.raises(ProviderError) as exc_info:
        provider.complete("Hello")
    assert "OPENROUTER_API_KEY is not configured" in str(exc_info.value)


def test_openrouter_dynamic_models_and_auto_route():
    """Verify OpenRouterProvider queries models list and supports auto routing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "provider-auto-discovered-model"}]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(api_key="mock_or_key", http_client=client)

    models = provider.list_models()
    assert models == ["provider-auto-discovered-model"]
    resolved = provider.resolve_model()
    assert resolved == "provider-auto-discovered-model"


def test_openrouter_score_options_with_logprobs():
    """Verify OpenRouterProvider score_options with top_logprobs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "mock-model"}]})
        if request.url.path == "/api/v1/chat/completions":
            data = {
                "choices": [
                    {
                        "message": {"content": "A"},
                        "logprobs": {
                            "top_logprobs": [
                                [
                                    {"token": "A", "logprob": -0.15},
                                    {"token": "B", "logprob": -2.10},
                                ]
                            ]
                        },
                    }
                ]
            }
            return httpx.Response(200, json=data)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(api_key="mock_or_key", http_client=client)

    probs = provider.score_options("Rate candidate", {"A": "Viral", "B": "Dull"})
    assert "A" in probs and "B" in probs
    assert probs["A"] > probs["B"]
    assert pytest.approx(sum(probs.values()), 0.001) == 1.0


def test_openrouter_score_options_fallback():
    """Verify OpenRouterProvider fallback when logprobs is absent."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "mock-model"}]})
        if request.url.path == "/api/v1/chat/completions":
            # Returns no logprobs
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "B"}, "logprobs": None}]}
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(api_key="mock_or_key", http_client=client)

    probs = provider.score_options("Rate candidate", {"A": "Viral", "B": "Dull"})
    assert probs["B"] > probs["A"]
    assert pytest.approx(sum(probs.values()), 0.001) == 1.0


def test_openrouter_complete():
    """Verify OpenRouterProvider text completion."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "mock-model"}]})
        if request.url.path == "/api/v1/chat/completions":
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Shorts Title #Shorts"}}]}
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(api_key="mock_or_key", http_client=client)
    res = provider.complete("Create a title")
    assert res == "Shorts Title #Shorts"


# -----------------------------------------------------------------------------
# Regression: null / empty model output must never surface as the string "None"
# -----------------------------------------------------------------------------


def test_openrouter_complete_null_content_returns_empty_string():
    """JSON null message content (refusal / tool-only turn) yields '', not 'None'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "mock-model"}]})
        if request.url.path == "/api/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(api_key="mock_or_key", http_client=client)
    assert provider.complete("Create a title") == ""


def test_gemini_complete_null_text_returns_empty_string():
    """A part with JSON null text yields '', not 'None'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/mock-flash",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        if request.url.path == "/v1beta/models/mock-flash:generateContent":
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": None}]}}]},
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="mock_key", http_client=client)
    assert provider.complete("Create a title") == ""


def test_local_provider_empty_top_logprobs_does_not_crash():
    """An empty top_logprobs list in the completion fallback yields uniform scores."""

    class _FakeLlama:
        def create_completion(self, prompt, **kwargs):
            return {"choices": [{"text": "", "logprobs": {"top_logprobs": []}}]}

    provider = LocalProvider(model_instance=_FakeLlama())
    dist = provider.score_options("Pick one", ["Yes", "No"])
    assert set(dist) == {"A", "B"}
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    assert dist["A"] == pytest.approx(dist["B"])


# -----------------------------------------------------------------------------
# Regression: scoring must not look like a real answer when the API call failed
# -----------------------------------------------------------------------------


def test_gemini_score_options_raises_when_every_request_fails():
    """A revoked/rate-limited key must raise, not return a uniform (fake) distribution."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/mock-model",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        if ":generateContent" in request.url.path:
            return httpx.Response(401, json={"error": {"message": "API key not valid"}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="mock_key", http_client=client)
    with pytest.raises(ProviderError):
        provider.score_options("Rate window", {"A": "Keep", "B": "Drop"})


def test_gemini_score_options_unparseable_reply_stays_uniform():
    """A reply that names no option is still a uniform distribution, not an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/mock-model",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        if ":generateContent" in request.url.path:
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "zzz"}]}}]}
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="mock_key", http_client=client)
    probs = provider.score_options("Rate window", {"A": "Keep", "B": "Drop"})
    assert probs == {"A": 0.5, "B": 0.5}


def test_openrouter_score_options_raises_on_http_failure():
    """An HTTP failure during OpenRouter scoring must surface as ProviderError."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "mock-model"}]})
        if request.url.path == "/api/v1/chat/completions":
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(api_key="mock_or_key", http_client=client)
    with pytest.raises(ProviderError):
        provider.score_options("Rate candidate", {"A": "Viral", "B": "Dull"})
