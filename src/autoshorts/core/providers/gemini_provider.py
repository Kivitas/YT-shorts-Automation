"""Google Gemini LLM Provider using direct REST API via httpx.

Features zero hardcoded model identifiers, dynamic runtime model resolution from
the provider's list-models endpoint, token logprob extraction via responseLogprobs,
constrained single-letter JSON-enum fallback with temperature 0 and majority voting,
redacted logging, and backoff retries.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from autoshorts.core.config import Settings, load_config
from autoshorts.core.exceptions import ModelNotFoundError, ProviderError, ProviderRateLimitError
from autoshorts.core.logging_setup import redact
from autoshorts.core.providers.base import BaseLLMProvider

logger = logging.getLogger("autoshorts.providers.gemini")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(BaseLLMProvider):
    """Google Gemini provider executing direct REST API requests via httpx.

    Dynamically resolves recommended models from the list-models endpoint and extracts
    logprobs when supported, with deterministic majority-vote fallback.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model)
        self.settings = settings or load_config()
        self._api_key = api_key or self.settings.get_gemini_api_key_str()
        self._custom_client = http_client
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def supports_logprobs(self) -> bool:
        return True

    @property
    def is_local(self) -> bool:
        return False

    def _get_api_key(self) -> str:
        """Retrieve and validate the Gemini API key."""
        if not self._api_key:
            key = self.settings.get_gemini_api_key_str()
            if key:
                self._api_key = key
        if not self._api_key:
            raise ProviderError(
                "GEMINI_API_KEY is not configured in environment or settings. "
                "Set GEMINI_API_KEY in .env to use the Gemini provider."
            )
        return self._api_key

    def _get_client(self) -> httpx.Client:
        """Return the active HTTP client."""
        if self._custom_client is not None:
            return self._custom_client
        return httpx.Client(timeout=self._timeout)

    def list_models(self) -> list[str]:
        """Dynamically query Google Gemini API for generateContent-capable models."""
        api_key = self._get_api_key()
        client = self._get_client()
        url = f"{GEMINI_BASE_URL}/models"
        params = {"key": api_key}

        def _request() -> list[str]:
            resp = client.get(url, params=params)
            if resp.status_code == 429:
                raise ProviderRateLimitError(f"Gemini API rate limit: {resp.text}")
            if resp.status_code != 200:
                raise ProviderError(f"Gemini list_models failed ({resp.status_code}): {resp.text}")

            data = resp.json()
            models_list = data.get("models", [])
            valid_models: list[str] = []
            for m in models_list:
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    name = m.get("name", "")
                    name = name.removeprefix("models/")
                    if name:
                        valid_models.append(name)
            return valid_models

        models = self.execute_with_retry(_request)
        if not models:
            raise ModelNotFoundError("Gemini list_models returned no generateContent models.")
        return models

    def resolve_model(self) -> str:
        """Resolve the model dynamically without any hardcoded model identifier string."""
        if self._resolved_model:
            return self._resolved_model

        available = self.list_models()
        # Choose provider's recommended default: prefer models matching 'flash', else first available
        preferred = [m for m in available if "flash" in m.lower()]
        selected = preferred[0] if preferred else available[0]
        self._resolved_model = selected
        logger.info("Gemini resolved dynamic default model: %s", redact(selected))
        return selected

    def complete(self, prompt: str, **kwargs: Any) -> str:
        """Generate text completion from Gemini REST endpoint."""
        api_key = self._get_api_key()
        model = self.resolve_model()
        client = self._get_client()
        url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"
        params = {"key": api_key}

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.0),
                "maxOutputTokens": kwargs.get("max_tokens", 1024),
            },
        }

        def _request() -> str:
            resp = client.post(url, params=params, json=payload)
            if resp.status_code == 429:
                raise ProviderRateLimitError(f"Gemini API rate limit: {resp.text}")
            if resp.status_code != 200:
                raise ProviderError(f"Gemini complete failed ({resp.status_code}): {resp.text}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return ""
            return str(parts[0].get("text", "")).strip()

        return self.execute_with_retry(_request)

    def score_options(
        self,
        prompt: str,
        options: Sequence[str] | Mapping[str, str],
        **kwargs: Any,
    ) -> dict[str, float]:
        """Compute categorical probabilities for discrete option tokens.

        Attempts responseLogprobs scoring first; falls back to single-letter/JSON-enum
        constrained decoding with temperature 0 and majority voting.
        """
        norm_options = self.normalize_options(options)
        if not norm_options:
            return {}

        api_key = self._get_api_key()
        model = self.resolve_model()
        client = self._get_client()
        url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"
        params = {"key": api_key}

        # 1. Primary evaluation: responseLogprobs
        logprob_payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1,
                "responseLogprobs": True,
                "logprobs": max(5, len(norm_options) * 2),
            },
        }

        try:
            resp = client.post(url, params=params, json=logprob_payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    logprobs_res = candidates[0].get("logprobsResult", {})
                    top_cands = logprobs_res.get("topCandidates", [])
                    if top_cands:
                        token_entries = top_cands[0].get("candidates", [])
                        logits_map: dict[str, float] = {}
                        for entry in token_entries:
                            tok = entry.get("token", "").strip().upper()
                            lp = float(entry.get("logProbability", -100.0))
                            if tok in norm_options:
                                logits_map[tok] = max(logits_map.get(tok, -100.0), lp)

                        # If we matched options, compute softmax
                        if any(k in logits_map for k in norm_options):
                            for k in norm_options:
                                if k not in logits_map:
                                    logits_map[k] = -100.0
                            return self.compute_softmax(logits_map)
        except Exception as e:
            logger.debug("Gemini responseLogprobs evaluation fell back: %s", e)

        # 2. Fallback mode: constrained single-letter / JSON-enum with majority vote
        return self._fallback_majority_vote(prompt, norm_options, url, params, client)

    def _fallback_majority_vote(
        self,
        prompt: str,
        options: dict[str, str],
        url: str,
        params: dict[str, str],
        client: httpx.Client,
        votes: int = 3,
    ) -> dict[str, float]:
        """Perform constrained single-letter voting with temperature 0 and majority tallying."""
        allowed_keys = list(options.keys())
        options_prompt = (
            prompt
            + "\n\nAllowed Choices:\n"
            + "\n".join(f"{k}: {v}" for k, v in options.items())
            + f"\nRespond strictly with one of: {', '.join(allowed_keys)}"
        )

        fallback_payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": options_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 3,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "STRING",
                    "enum": allowed_keys,
                },
            },
        }

        tally: dict[str, int] = {k: 0 for k in allowed_keys}
        for _ in range(votes):
            try:
                resp = client.post(url, params=params, json=fallback_payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw = (
                            candidates[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                            .strip()
                            .strip('"')
                            .strip("'")
                            .upper()
                        )
                        for k in allowed_keys:
                            if k in raw:
                                tally[k] += 1
                                break
            except Exception:
                pass

        total_votes = sum(tally.values())
        if total_votes > 0:
            return {k: count / total_votes for k, count in tally.items()}

        # If zero valid votes, return uniform distribution
        return {k: 1.0 / len(allowed_keys) for k in allowed_keys}
