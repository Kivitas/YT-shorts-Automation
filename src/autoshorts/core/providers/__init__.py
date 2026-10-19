"""LLM Providers package providing decoupled on-device and cloud LLM execution.

Exports:
- BaseLLMProvider: Abstract protocol-compliant provider base class.
- LocalProvider: On-device quantized GGUF execution via llama-cpp-python.
- GeminiProvider: Direct Google Gemini REST API client via httpx.
- OpenRouterProvider: Direct OpenRouter REST API client via httpx.
- get_provider: Factory helper to instantiate configured provider.
"""

from __future__ import annotations

from typing import Any

from autoshorts.core.config import Settings, load_config
from autoshorts.core.exceptions import ConfigError
from autoshorts.core.providers.base import BaseLLMProvider
from autoshorts.core.providers.gemini_provider import GeminiProvider
from autoshorts.core.providers.local_provider import LocalProvider
from autoshorts.core.providers.openrouter_provider import OpenRouterProvider


def get_provider(
    name: str | None = None,
    settings: Settings | None = None,
    **kwargs: Any,
) -> BaseLLMProvider:
    """Factory function to instantiate an LLM provider based on name or settings.

    Args:
        name: Optional provider name ('local', 'gemini', 'openrouter'). Defaults to config.
        settings: Optional Settings instance. Defaults to load_config().
        **kwargs: Extra parameters passed to the provider constructor.

    Returns:
        Instantiated BaseLLMProvider.
    """
    cfg = settings or load_config()
    provider_name = (name or cfg.llm.provider or "local").lower().strip()

    if provider_name == "local":
        return LocalProvider(settings=cfg, **kwargs)
    elif provider_name == "gemini":
        return GeminiProvider(settings=cfg, **kwargs)
    elif provider_name == "openrouter":
        return OpenRouterProvider(settings=cfg, **kwargs)
    else:
        raise ConfigError(
            f"Unsupported LLM provider: '{provider_name}'. Must be 'local', 'gemini', or 'openrouter'."
        )


__all__ = [
    "BaseLLMProvider",
    "GeminiProvider",
    "LocalProvider",
    "OpenRouterProvider",
    "get_provider",
]
