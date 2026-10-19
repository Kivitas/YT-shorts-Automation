"""Abstract base class and utilities for AutoShorts LLM providers.

Implements the LLMProvider contract protocol, providing numerical softmax helpers,
option normalization, dynamic model resolution with zero hardcoded model IDs,
secret-redacted logging, and retry mechanisms.
"""

from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Mapping, Sequence, TypeVar

from autoshorts.core.contracts import LLMProvider
from autoshorts.core.exceptions import ModelNotFoundError, ProviderError, ProviderRateLimitError
from autoshorts.core.logging_setup import redact

logger = logging.getLogger("autoshorts.providers")
T = TypeVar("T")


class BaseLLMProvider(ABC, LLMProvider):
    """Abstract base provider implementing the LLMProvider contract.

    Enforces zero hardcoded model identifiers, provides safe softmax logit normalization,
    and exposes privacy badge status for local vs cloud execution.
    """

    def __init__(self, model: str | None = None, **kwargs: Any) -> None:
        self._model = model
        self._resolved_model: str | None = model

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string (e.g., 'local', 'gemini', 'openrouter')."""
        ...

    @property
    @abstractmethod
    def supports_logprobs(self) -> bool:
        """True if the provider natively evaluates token logprobs / logits."""
        ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True if execution is 100% on-device and offline."""
        ...

    @property
    def privacy_badge(self) -> str:
        """Privacy badge indicator for UI and operator reporting."""
        if self.is_local:
            return "🟢 100% On-Device (Private - Zero Data Leaves Computer)"
        return "🟡 Cloud API Active (Transcript text transmitted to provider)"

    @abstractmethod
    def list_models(self) -> list[str]:
        """Dynamically list available models without hardcoded strings."""
        ...

    def resolve_model(self) -> str:
        """Dynamically resolve the active model if none was explicitly configured.

        Never returns or falls back to a hardcoded model identifier string.
        """
        if self._resolved_model:
            return self._resolved_model

        available = self.list_models()
        if not available:
            raise ModelNotFoundError(
                f"Provider '{self.name}' found zero available models dynamically."
            )

        # Pick the first dynamically discovered model as default
        self._resolved_model = available[0]
        logger.info(
            "Provider '%s' resolved dynamic default model: %s",
            self.name,
            redact(self._resolved_model),
        )
        return self._resolved_model

    @abstractmethod
    def complete(self, prompt: str, **kwargs: Any) -> str:
        """Generate text completion from a prompt string."""
        ...

    @abstractmethod
    def score_options(
        self,
        prompt: str,
        options: Sequence[str] | Mapping[str, str],
        **kwargs: Any,
    ) -> dict[str, float]:
        """Compute categorical probability distribution across discrete option tokens."""
        ...

    @staticmethod
    def normalize_options(options: Sequence[str] | Mapping[str, str]) -> dict[str, str]:
        """Normalize options into a standard mapping of uppercase letters to text.

        Example:
            ["Accept", "Reject"] -> {"A": "Accept", "B": "Reject"}
            {"A": "Accept", "B": "Reject"} -> {"A": "Accept", "B": "Reject"}
        """
        if isinstance(options, Mapping):
            return {str(k).strip().upper(): str(v).strip() for k, v in options.items()}

        labels = [chr(ord("A") + i) for i in range(len(options))]
        return {label: str(opt).strip() for label, opt in zip(labels, options)}

    @staticmethod
    def compute_softmax(logits: Mapping[str, float]) -> dict[str, float]:
        """Compute numerically stable Softmax over unnormalized logits.

        P(i) = exp(z_i - max(z)) / sum(exp(z_j - max(z)))
        """
        if not logits:
            return {}

        keys = list(logits.keys())
        values = [float(logits[k]) for k in keys]

        # Handle inf/nan values
        valid_values = [v for v in values if math.isfinite(v)]
        if not valid_values:
            uniform = 1.0 / len(keys)
            return {k: uniform for k in keys}

        max_val = max(valid_values)
        exp_vals = [math.exp(v - max_val) if math.isfinite(v) else 0.0 for v in values]
        sum_exp = sum(exp_vals)

        if sum_exp <= 0.0 or not math.isfinite(sum_exp):
            uniform = 1.0 / len(keys)
            return {k: uniform for k in keys}

        return {k: exp_v / sum_exp for k, exp_v in zip(keys, exp_vals)}

    def execute_with_retry(
        self,
        action: Callable[[], T],
        max_retries: int = 3,
        initial_backoff: float = 0.5,
        backoff_multiplier: float = 2.0,
    ) -> T:
        """Execute a callable with exponential backoff on transient errors."""
        attempt = 0
        current_delay = initial_backoff

        while attempt < max_retries:
            try:
                return action()
            except ProviderRateLimitError:
                attempt += 1
                if attempt >= max_retries:
                    raise
                logger.warning(
                    "Rate limit encountered on '%s'. Backing off for %.2fs (attempt %d/%d)",
                    self.name,
                    current_delay,
                    attempt,
                    max_retries,
                )
                time.sleep(current_delay)
                current_delay *= backoff_multiplier
            except Exception as exc:
                # Check for 429 status code in exception if present
                status_code = getattr(exc, "status_code", None)
                if status_code == 429:
                    attempt += 1
                    if attempt >= max_retries:
                        raise ProviderRateLimitError(
                            f"Provider '{self.name}' rate limit exhausted: {exc}"
                        ) from exc
                    logger.warning(
                        "HTTP 429 on '%s'. Retrying in %.2fs (attempt %d/%d)",
                        self.name,
                        current_delay,
                        attempt,
                        max_retries,
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff_multiplier
                    continue

                attempt += 1
                if attempt >= max_retries:
                    logger.error(
                        "Provider '%s' failed after %d attempts: %s",
                        self.name,
                        max_retries,
                        redact(str(exc)),
                    )
                    raise ProviderError(f"Provider '{self.name}' failed: {exc}") from exc

                logger.warning(
                    "Provider '%s' encountered error: %s. Retrying in %.2fs...",
                    self.name,
                    redact(str(exc)),
                    current_delay,
                )
                time.sleep(current_delay)
                current_delay *= backoff_multiplier

        raise ProviderError(f"Provider '{self.name}' failed after {max_retries} attempts.")
