"""OpenRouter LLM Provider using direct REST API via httpx.

Features zero hardcoded model identifiers, dynamic runtime model discovery,
auto-routing resolution, logprobs and top_logprobs evaluation with constrained
decoding fallback, redacted logging, and backoff retries.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from autoshorts.core.config import Settings, load_config
from autoshorts.core.exceptions import ProviderError, ProviderRateLimitError
from autoshorts.core.logging_setup import redact
from autoshorts.core.providers.base import BaseLLMProvider

logger = logging.getLogger("autoshorts.providers.openrouter")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter provider executing chat completions with dynamic model resolution.

    Supports logprob extraction on supported models and falls back to constrained
    single-token evaluation with greedy decoding.
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
        self._api_key = api_key or self.settings.get_openrouter_api_key_str()
        self._custom_client = http_client
        self._http_client: httpx.Client | None = http_client
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def supports_logprobs(self) -> bool:
        return True

    @property
    def is_local(self) -> bool:
        return False

    def _get_api_key(self) -> str:
        """Retrieve and validate the OpenRouter API key."""
        if not self._api_key:
            key = self.settings.get_openrouter_api_key_str()
            if key:
                self._api_key = key
        if not self._api_key:
            raise ProviderError(
                "OPENROUTER_API_KEY is not configured in environment or settings. "
                "Set OPENROUTER_API_KEY in .env to use the OpenRouter provider."
            )
        return self._api_key

    def _get_client(self) -> httpx.Client:
        """Return the active shared or custom HTTP client."""
        if self._custom_client is not None:
            return self._custom_client
        if self._http_client is None or getattr(self._http_client, "is_closed", False):
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def close(self) -> None:
        """Close shared HTTP client."""
        if self._http_client is not None and not getattr(self._http_client, "is_closed", False):
            self._http_client.close()

    def _get_headers(self) -> dict[str, str]:
        """Return common headers for OpenRouter API requests."""
        return {
            "Authorization": f"Bearer {self._get_api_key()}",
            "HTTP-Referer": "https://autoshorts.local",
            "X-Title": "AutoShorts",
            "Content-Type": "application/json",
        }

    def list_models(self) -> list[str]:
        """Dynamically query OpenRouter API for available models."""
        client = self._get_client()
        url = f"{OPENROUTER_BASE_URL}/models"
        headers = self._get_headers()

        def _request() -> list[str]:
            resp = client.get(url, headers=headers)
            if resp.status_code == 429:
                raise ProviderRateLimitError(f"OpenRouter rate limit: {resp.text}")
            if resp.status_code != 200:
                raise ProviderError(
                    f"OpenRouter list_models failed ({resp.status_code}): {resp.text}"
                )

            data = resp.json()
            models_data = data.get("data", [])
            valid_models: list[str] = [
                m["id"] for m in models_data if isinstance(m, dict) and "id" in m
            ]
            return valid_models

        models = self.execute_with_retry(_request)
        if not models:
            # Fall back to provider auto router
            return ["openrouter/auto"]
        return models

    def resolve_model(self) -> str:
        """Dynamically resolve model or use the provider's auto-router without hardcoded model strings."""
        if self._resolved_model:
            return self._resolved_model

        try:
            available = self.list_models()
            if available:
                self._resolved_model = available[0]
            else:
                self._resolved_model = "openrouter/auto"
        except Exception:
            self._resolved_model = "openrouter/auto"

        logger.info("OpenRouter resolved model: %s", redact(self._resolved_model))
        return self._resolved_model

    def complete(self, prompt: str, **kwargs: Any) -> str:
        """Generate text completion from OpenRouter completions endpoint."""
        model = self.resolve_model()
        client = self._get_client()
        url = f"{OPENROUTER_BASE_URL}/chat/completions"
        headers = self._get_headers()

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.0),
            "max_tokens": kwargs.get("max_tokens", 1024),
        }

        def _request() -> str:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                raise ProviderRateLimitError(f"OpenRouter rate limit: {resp.text}")
            if resp.status_code != 200:
                raise ProviderError(f"OpenRouter complete failed ({resp.status_code}): {resp.text}")

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            # content may be JSON null (e.g. refusals or tool-only turns); never stringify None.
            return str(msg.get("content") or "").strip()

        return self.execute_with_retry(_request)

    def score_options(
        self,
        prompt: str,
        options: Sequence[str] | Mapping[str, str],
        **kwargs: Any,
    ) -> dict[str, float]:
        """Compute categorical probabilities using logprobs or constrained fallback."""
        norm_options = self.normalize_options(options)
        if not norm_options:
            return {}

        model = self.resolve_model()
        client = self._get_client()
        url = f"{OPENROUTER_BASE_URL}/chat/completions"
        headers = self._get_headers()

        # 1. Primary evaluation: logprobs and top_logprobs
        logprob_payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1,
            "temperature": 0.0,
            "logprobs": True,
            "top_logprobs": max(5, len(norm_options) * 2),
        }

        try:
            resp = client.post(url, headers=headers, json=logprob_payload)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    lp_data = choices[0].get("logprobs")
                    if lp_data and "top_logprobs" in lp_data:
                        top_lps = lp_data["top_logprobs"]
                        if top_lps and isinstance(top_lps, list):
                            first_pos = top_lps[0]
                            logits_map: dict[str, float] = {}

                            # Could be list of dicts [{"token": "A", "logprob": -0.1}] or dict
                            items = first_pos if isinstance(first_pos, list) else first_pos.items()
                            for item in items:
                                if isinstance(item, dict):
                                    tok = item.get("token", "").strip().upper()
                                    lp = float(item.get("logprob", -100.0))
                                else:
                                    tok = str(item[0]).strip().upper()
                                    lp = (
                                        float(item[1])
                                        if isinstance(item[1], (int, float))
                                        else -100.0
                                    )

                                if tok in norm_options:
                                    logits_map[tok] = max(logits_map.get(tok, -100.0), lp)

                            if any(k in logits_map for k in norm_options):
                                for k in norm_options:
                                    if k not in logits_map:
                                        logits_map[k] = -100.0
                                return self.compute_softmax(logits_map)
        except Exception as e:
            logger.debug("OpenRouter logprobs evaluation fell back: %s", e)

        # 2. Fallback mode: single-letter constrained decoding
        return self._fallback_single_letter(prompt, norm_options, url, headers, client, model)

    def _fallback_single_letter(
        self,
        prompt: str,
        options: dict[str, str],
        url: str,
        headers: dict[str, str],
        client: httpx.Client,
        model: str,
    ) -> dict[str, float]:
        """Fallback evaluation using greedy single-token choice."""
        allowed_keys = list(options.keys())
        options_prompt = (
            prompt
            + "\n\nOptions:\n"
            + "\n".join(f"{k}: {v}" for k, v in options.items())
            + f"\nRespond strictly with the single letter choice ({', '.join(allowed_keys)}):"
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": options_prompt}],
            "max_tokens": 3,
            "temperature": 0.0,
        }

        try:
            resp = client.post(url, headers=headers, json=payload)
        except Exception as exc:
            # Transport failure: surface it so the caller can fall back rather than score uniformly.
            raise ProviderError(f"OpenRouter scoring request failed: {type(exc).__name__}") from exc
        if resp.status_code == 429:
            raise ProviderRateLimitError(f"OpenRouter rate limit during scoring: {resp.text}")
        if resp.status_code != 200:
            raise ProviderError(f"OpenRouter scoring failed (HTTP {resp.status_code})")

        try:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                raw = (choices[0].get("message") or {}).get("content") or ""
                raw = str(raw).strip().upper()
                for k in allowed_keys:
                    if k in raw:
                        dist = {opt: 0.05 for opt in allowed_keys}
                        dist[k] = 1.0 - (0.05 * (len(allowed_keys) - 1))
                        return dist
        except Exception:
            pass

        # Reply arrived but named no valid option: uniform distribution
        return {k: 1.0 / len(allowed_keys) for k in allowed_keys}
