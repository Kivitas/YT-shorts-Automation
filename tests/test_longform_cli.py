"""Tests for the `longform` CLI subcommand. The real renderer is never called."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoshorts import cli


def _install_fake_renderer(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    fake = types.ModuleType("autoshorts.longform.render")
    fake.render_longform = fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "autoshorts.longform.render", fake)


def test_parser_accepts_longform_subcommand() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["longform", "--topic", "discipline"])
    assert args.command == "longform"
    assert args.topic == "discipline"
    assert args.minutes == 2.5
    assert args.provider == "api"


def test_parser_accepts_all_options() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["longform", "--topic", "self_improvement", "--minutes", "3.5", "--provider", "local"]
    )
    assert args.topic == "self_improvement"
    assert args.minutes == 3.5
    assert args.provider == "local"


def test_parser_rejects_unknown_topic(capsys: pytest.CaptureFixture[str]) -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["longform", "--topic", "cooking"])
    assert "invalid choice" in capsys.readouterr().err


def test_cmd_longform_returns_1_on_value_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_render(topic, minutes, provider, config):
        raise ValueError("bad topic")

    _install_fake_renderer(monkeypatch, fake_render)
    args = cli.build_parser().parse_args(["longform", "--topic", "money"])
    rc = cli.cmd_longform(args, MagicMock(), MagicMock())
    assert rc == 1
    assert "bad topic" in capsys.readouterr().err


def test_cmd_longform_returns_1_on_runtime_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_render(topic, minutes, provider, config):
        raise RuntimeError("renderer unavailable")

    _install_fake_renderer(monkeypatch, fake_render)
    args = cli.build_parser().parse_args(["longform", "--topic", "science"])
    rc = cli.cmd_longform(args, MagicMock(), MagicMock())
    assert rc == 1
    assert "renderer unavailable" in capsys.readouterr().err


def test_cmd_longform_passes_args_and_prints_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = []

    def fake_render(topic, minutes, provider, config):
        calls.append((topic, minutes, provider, config))
        return types.SimpleNamespace(
            title="Test Title",
            video_path=Path("out/video.mp4"),
            thumbnail_path=Path("out/thumb.png"),
            duration=150.0,
        )

    _install_fake_renderer(monkeypatch, fake_render)
    config = object()
    args = cli.build_parser().parse_args(
        ["longform", "--topic", "testosterone", "--minutes", "2", "--provider", "local"]
    )
    rc = cli.cmd_longform(args, config, MagicMock())
    assert rc == 0
    assert calls == [("testosterone", 2.0, "local", config)]
    out = capsys.readouterr().out
    assert "Test Title" in out
    assert "video.mp4" in out
    assert "thumb.png" in out
    assert "Duration: 150.0s" in out
