"""Pytest configuration and global offline safety fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoshorts.pipeline import ShortsPipeline


@pytest.fixture(autouse=True)
def _isolate_repo_asset_and_output_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point writable repo folders (assets/, output/) at tmp_path so no test writes into the repo.

    Each importing module binds the helper by name, so every binding is patched, not only
    autoshorts.core.paths.
    """
    assets_dir = tmp_path / "isolated_assets"
    output_dir = tmp_path / "isolated_output"

    def _assets() -> Path:
        assets_dir.mkdir(parents=True, exist_ok=True)
        return assets_dir

    def _output() -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    monkeypatch.setattr("autoshorts.core.paths.get_assets_dir", _assets)
    monkeypatch.setattr("autoshorts.core.paths.get_output_dir", _output)
    monkeypatch.setattr("autoshorts.dashboard.app.get_assets_dir", _assets)
    monkeypatch.setattr("autoshorts.media.background_fetcher.get_assets_dir", _assets)


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
