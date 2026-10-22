"""Pytest configuration and global offline safety fixtures."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from autoshorts.pipeline import ShortsPipeline


@pytest.fixture(autouse=True)
def _mock_pipeline_network_for_tests(monkeypatch: pytest.MonkeyPatch):
    """Ensure pipeline unit tests use an offline mock LLM provider by default instead of live APIs."""
    orig_init = ShortsPipeline.__init__

    def wrapped_init(self, *args, **kwargs):
        if "llm_provider" not in kwargs or kwargs.get("llm_provider") is None:
            mock_llm = MagicMock()
            mock_llm.score_options.return_value = {"A": 0.95, "B": 0.05}
            mock_llm.complete.return_value = "Viral Hook Moment #Shorts"
            mock_llm.generate.return_value = json.dumps(
                {
                    "hook_in_sec": 0.0,
                    "hook_out_sec": 30.0,
                    "recommended_title": "Test Title #Shorts",
                    "recommended_hook": "Shocking Moment",
                    "jump_cuts": [],
                    "zoom_punch_ins": [],
                    "caption_corrections": [],
                    "background_style_recommendation": "minecraft",
                }
            )
            kwargs["llm_provider"] = mock_llm
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(ShortsPipeline, "__init__", wrapped_init)
