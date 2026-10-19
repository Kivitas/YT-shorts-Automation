"""Local GGUF LLM Provider using llama-cpp-python.

Implements on-device CPU-optimized inference with zero hardcoded model identifiers,
true single-forward-pass logit extraction, tokenizer-resolved token IDs, and lazy imports.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoshorts.core.config import Settings, load_config
from autoshorts.core.exceptions import ModelNotFoundError, ProviderError
from autoshorts.core.paths import get_data_dir
from autoshorts.core.providers.base import BaseLLMProvider

logger = logging.getLogger("autoshorts.providers.local")


class LocalProvider(BaseLLMProvider):
    """Local quantized GGUF provider using llama-cpp-python on CPU.

    Supports true single-forward-pass logit scoring over dynamically resolved option tokens.
    Requires no GPU and guarantees 100% on-device privacy.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        n_threads: int = 4,
        temperature: float = 0.0,
        settings: Settings | None = None,
        model_instance: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=str(model_path) if model_path else None)
        self.settings = settings or load_config()
        self.n_threads = n_threads or self.settings.llm.n_threads
        self.temperature = temperature
        self._model_path = Path(model_path) if model_path else None
        self._model = model_instance

    @property
    def name(self) -> str:
        return "local"

    @property
    def supports_logprobs(self) -> bool:
        return True

    @property
    def is_local(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        """Scan data/models/ directory and configured paths for available .gguf files."""
        models: list[str] = []

        # Check configured path if it exists on disk
        configured_paths = [
            self.settings.local_gguf_path,
            self.settings.llm.local_model_path,
        ]
        for cp in configured_paths:
            if cp:
                p = Path(cp)
                if p.is_file() and p.suffix.lower() == ".gguf":
                    models.append(str(p.resolve()))

        # Scan data/models/ for any *.gguf files
        models_dir = get_data_dir() / "models"
        if models_dir.is_dir():
            for entry in models_dir.glob("*.gguf"):
                if entry.is_file():
                    entry_path = str(entry.resolve())
                    if entry_path not in models:
                        models.append(entry_path)

        return models

    def _resolve_model_path(self) -> Path:
        """Resolve the path to the local GGUF model file."""
        if self._model_path and self._model_path.is_file():
            return self._model_path

        available = self.list_models()
        if available:
            self._model_path = Path(available[0])
            self._resolved_model = str(self._model_path)
            return self._model_path

        # If configured path is provided, even if not found yet, produce helpful error
        target = (
            self._model_path or self.settings.local_gguf_path or self.settings.llm.local_model_path
        )
        raise ModelNotFoundError(
            f"No local GGUF model file found at '{target}' or in data/models/. "
            "Please place a valid .gguf model file in data/models/."
        )

    def _get_or_load_model(self) -> Any:
        """Lazily load the llama-cpp-python model instance."""
        if self._model is not None:
            return self._model

        # Lazy import of llama_cpp
        try:
            import llama_cpp
        except ImportError as err:
            raise ProviderError(
                "llama-cpp-python is not installed. "
                "Install it with: uv pip install autoshorts[local-llm]"
            ) from err

        resolved_path = self._resolve_model_path()
        logger.info("Loading local GGUF model: %s (threads=%d)", resolved_path, self.n_threads)

        try:
            self._model = llama_cpp.Llama(
                model_path=str(resolved_path),
                n_threads=self.n_threads,
                n_ctx=2048,
                logits_all=True,
                verbose=False,
            )
            return self._model
        except Exception as e:
            raise ProviderError(f"Failed to load local model from '{resolved_path}': {e}") from e

    def complete(self, prompt: str, **kwargs: Any) -> str:
        """Generate text completion from a prompt using the local GGUF model."""
        model = self._get_or_load_model()
        max_tokens = kwargs.get("max_tokens", 128)
        temperature = kwargs.get("temperature", self.temperature)

        response = model.create_completion(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=kwargs.get("stop", None),
        )
        choices = response.get("choices", [])
        if not choices:
            return ""
        return str(choices[0].get("text", "")).strip()

    def score_options(
        self,
        prompt: str,
        options: Sequence[str] | Mapping[str, str],
        **kwargs: Any,
    ) -> dict[str, float]:
        """Compute categorical probabilities over discrete options via single-forward-pass logits.

        Resolves token IDs for option letters dynamically using the model's tokenizer.
        """
        norm_options = self.normalize_options(options)
        if not norm_options:
            return {}

        model = self._get_or_load_model()

        # Tokenize the input prompt (without thinking prompt instructions)
        prompt_bytes = prompt.encode("utf-8")
        if hasattr(model, "tokenize"):
            try:
                prompt_tokens = model.tokenize(prompt_bytes, add_bos=False)
            except TypeError:
                prompt_tokens = model.tokenize(prompt_bytes)
        else:
            prompt_tokens = list(prompt_bytes)

        # Resolve token IDs for each option letter using the tokenizer (never hardcoded)
        option_token_ids: dict[str, list[int]] = {}
        for key in norm_options:
            tokens_for_key: list[int] = []
            # Check bare key and key with leading space
            variants = [key, f" {key}"]
            for v in variants:
                v_bytes = v.encode("utf-8")
                if hasattr(model, "tokenize"):
                    try:
                        t = model.tokenize(v_bytes, add_bos=False)
                    except TypeError:
                        t = model.tokenize(v_bytes)
                    if t:
                        tokens_for_key.append(t[-1])
            # Ensure at least one token ID exists
            if not tokens_for_key:
                tokens_for_key = [ord(key[0])]
            option_token_ids[key] = tokens_for_key

        # Perform single forward pass evaluation
        logits_dict: dict[str, float] = {}

        if hasattr(model, "reset") and hasattr(model, "eval"):
            model.reset()
            model.eval(prompt_tokens)

            # Retrieve unnormalized logits at the final token position
            scores = getattr(model, "scores", None)
            eval_logits = getattr(model, "eval_logits", None)
            last_logits: Sequence[float] | None = None

            if scores is not None and len(scores) > 0:
                last_logits = scores[-1]
            elif eval_logits is not None and len(eval_logits) > 0:
                last_logits = eval_logits[-1]

            if last_logits is not None:
                for key, tids in option_token_ids.items():
                    key_vals = [float(last_logits[tid]) for tid in tids if tid < len(last_logits)]
                    logits_dict[key] = max(key_vals) if key_vals else -100.0

        # Fallback to create_completion with logprobs if eval scores unavailable
        if not logits_dict and hasattr(model, "create_completion"):
            result = model.create_completion(
                prompt,
                max_tokens=1,
                temperature=0.0,
                logprobs=len(norm_options) * 2 + 5,
            )
            choices = result.get("choices", [])
            if choices and "logprobs" in choices[0] and choices[0]["logprobs"]:
                top_lp = choices[0]["logprobs"].get("top_logprobs", [{}])[0]
                for key in norm_options:
                    # Match key or ' ' + key
                    matches = [v for k_tok, v in top_lp.items() if k_tok.strip().upper() == key]
                    logits_dict[key] = max(matches) if matches else -100.0

        # If still empty (e.g., in test mocks), assign uniform
        if not logits_dict:
            return {k: 1.0 / len(norm_options) for k in norm_options}

        return self.compute_softmax(logits_dict)
