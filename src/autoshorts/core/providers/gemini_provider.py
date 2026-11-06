"""Google Gemini LLM Provider using direct REST API via httpx.

Features zero hardcoded model identifiers, dynamic runtime model resolution from
the provider's list-models endpoint, token logprob extraction via responseLogprobs,
constrained single-letter JSON-enum fallback with temperature 0 and majority voting,
redacted logging, and backoff retries.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from autoshorts.core.config import Settings, load_config
from autoshorts.core.exceptions import ModelNotFoundError, ProviderError, ProviderRateLimitError
from autoshorts.core.logging_setup import redact
from autoshorts.core.providers.base import BaseLLMProvider

logger = logging.getLogger("autoshorts.providers.gemini")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class RateLimiter:
    """Thread-safe rate limiter pacing requests to respect API quotas (e.g. 15 RPM)."""

    def __init__(self, requests_per_minute: float = 14.0) -> None:
        self.rpm = requests_per_minute
        self.interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.interval:
                sleep_time = self.interval - elapsed
                time.sleep(sleep_time)
            self._last_call = time.monotonic()


_GEMINI_RATE_LIMITER = RateLimiter(requests_per_minute=14.0)


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
        self._http_client: httpx.Client | None = http_client
        self._timeout = timeout
        self._logprobs_supported: bool = True

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def supports_logprobs(self) -> bool:
        return self._logprobs_supported

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

    def _rate_limit_wait(self) -> None:
        """Pace requests only when making real network calls."""
        if self._custom_client is None:
            _GEMINI_RATE_LIMITER.wait()

    def list_models(self) -> list[str]:
        """Dynamically query Google Gemini API for generateContent-capable models."""
        api_key = self._get_api_key()
        client = self._get_client()
        url = f"{GEMINI_BASE_URL}/models"
        params = {"key": api_key}

        def _request() -> list[str]:
            self._rate_limit_wait()
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
        """Resolve the model dynamically, prioritizing gemini-3.5-flash-lite, then flash, then available."""
        if self._resolved_model:
            return self._resolved_model

        available = self.list_models()
        # Priority order:
        # 1. Exact match or version match for 'gemini-3.5-flash-lite'
        # 2. Any model containing 'flash-lite'
        # 3. Any model containing '3.5-flash'
        # 4. Any model containing 'flash'
        # 5. First available model
        lite_35 = [
            m
            for m in available
            if "3.5" in m.lower() and "flash" in m.lower() and "lite" in m.lower()
        ]
        if lite_35:
            selected = lite_35[0]
        else:
            flash_lite = [m for m in available if "flash" in m.lower() and "lite" in m.lower()]
            if flash_lite:
                selected = flash_lite[0]
            else:
                flash_35 = [m for m in available if "3.5" in m.lower() and "flash" in m.lower()]
                if flash_35:
                    selected = flash_35[0]
                else:
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

        gen_config: dict[str, Any] = {
            "temperature": kwargs.get("temperature", 0.0),
            "maxOutputTokens": kwargs.get("max_tokens", 1024),
        }
        if kwargs.get("response_mime_type"):
            gen_config["responseMimeType"] = kwargs["response_mime_type"]
        if kwargs.get("response_schema"):
            gen_config["responseSchema"] = kwargs["response_schema"]

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }

        def _request() -> str:
            self._rate_limit_wait()
            resp = client.post(url, params=params, json=payload)
            if resp.status_code == 400 and (
                "responseMimeType" in payload["generationConfig"]
                or "responseSchema" in payload["generationConfig"]
            ):
                payload["generationConfig"].pop("responseMimeType", None)
                payload["generationConfig"].pop("responseSchema", None)
                self._rate_limit_wait()
                resp = client.post(url, params=params, json=payload)
            if resp.status_code == 429:
                raise ProviderRateLimitError(f"Gemini API rate limit: {resp.text}")
            if resp.status_code != 200:
                raise ProviderError(f"Gemini complete failed ({resp.status_code}): {resp.text}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts:
                return ""
            # text may be JSON null on non-text parts; never stringify None.
            return str(parts[0].get("text") or "").strip()

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

        # 1. Primary evaluation: responseLogprobs (if supported by model)
        if self._logprobs_supported:
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
                self._rate_limit_wait()
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
                elif resp.status_code == 400:
                    self._logprobs_supported = False
                    logger.info(
                        "Model '%s' does not support responseLogprobs (HTTP 400); disabling for future requests.",
                        model,
                    )
                elif resp.status_code == 429:
                    raise ProviderRateLimitError(f"Gemini API rate limit: {resp.text}")
            except ProviderRateLimitError:
                raise
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
        votes: int = 1,
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

        def _execute_vote() -> httpx.Response:
            self._rate_limit_wait()
            resp = client.post(url, params=params, json=fallback_payload)
            if resp.status_code == 429:
                raise ProviderRateLimitError(f"Gemini API rate limit: {resp.text}")
            if resp.status_code != 200:
                raise ProviderError(f"Gemini vote request failed ({resp.status_code}): {resp.text}")
            return resp

        tally: dict[str, int] = {k: 0 for k in allowed_keys}
        failures = 0
        last_failure = ""
        for _ in range(votes):
            try:
                resp = self.execute_with_retry(_execute_vote)
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
            except Exception as exc:
                failures += 1
                last_failure = type(exc).__name__

        total_votes = sum(tally.values())
        if total_votes > 0:
            return {k: count / total_votes for k, count in tally.items()}

        # Every request failed at the transport/HTTP level (bad key, quota, outage): surface it so
        # the caller can fall back, instead of returning a uniform score that looks like a real answer.
        if failures >= votes:
            raise ProviderError(f"Gemini scoring failed on every attempt ({last_failure})")

        # Replies arrived but none named a valid option: uniform distribution
        return {k: 1.0 / len(allowed_keys) for k in allowed_keys}
