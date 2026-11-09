"""Regression tests: the Wojak character must actually land on Shorts thumbnails."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from PIL import Image

from autoshorts.longform import wojak_shorts


def test_thumbnail_draws_wojak_character_for_topic(monkeypatch, tmp_path) -> None:
    # Previously the call used an undefined name (`topic_title`); the NameError was
    # swallowed by a bare except, so draw_wojak was never reached.
    spec_sentinel = object()
    spec_mock = MagicMock(return_value=spec_sentinel)
    draw_mock = MagicMock(return_value=Image.new("RGBA", (300, 500), (255, 0, 0, 255)))
    monkeypatch.setattr(wojak_shorts, "spec_from_country_and_context", spec_mock)
    monkeypatch.setattr(wojak_shorts, "draw_wojak", draw_mock)

    out = tmp_path / "thumb.png"
    result = wojak_shorts.make_shorts_thumbnail("Test Topic", top_country="USA", out_path=out)

    assert result == out
    assert out.exists()
    spec_mock.assert_called_once()
    assert spec_mock.call_args.kwargs["scenario"] == "Test Topic"
    draw_mock.assert_called_once_with(spec_sentinel)


def test_thumbnail_logs_warning_when_character_fails(monkeypatch, tmp_path, caplog) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated render failure")

    monkeypatch.setattr(wojak_shorts, "draw_wojak", _boom)

    out = tmp_path / "thumb.png"
    with caplog.at_level(logging.WARNING, logger=wojak_shorts.logger.name):
        wojak_shorts.make_shorts_thumbnail("Topic", out_path=out)

    # The thumbnail still ships without the character, but the failure is no longer silent.
    assert out.exists()
    assert any("Wojak character failed" in r.getMessage() for r in caplog.records)
