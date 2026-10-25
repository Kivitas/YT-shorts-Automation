"""Tests for the vendored ffmpeg-skill bridge (autoshorts.media.ffmpeg_tools)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoshorts.media import ffmpeg_tools
from autoshorts.media.encoder_probe import get_ffmpeg_path
from autoshorts.media.ffmpeg_tools import FfmpegToolError, check_delivery, run_tool


def _make_clean_clip(path: Path) -> Path:
    """Render a clean 2-second 320x568 mp4 with the same ffmpeg the app uses (H.264 if built in)."""
    base = [
        get_ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x568:rate=30:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
    ]
    tail = ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)]
    try:
        subprocess.run(
            [*base, "-c:v", "libx264", *tail], check=True, capture_output=True, timeout=120
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        subprocess.run(
            [*base, "-c:v", "mpeg4", "-q:v", "5", *tail],
            check=True,
            capture_output=True,
            timeout=120,
        )
    return path


def test_run_tool_rejects_name_outside_allow_list() -> None:
    with pytest.raises(FfmpegToolError):
        run_tool("verify", [])
    with pytest.raises(FfmpegToolError):
        run_tool("../check", [])


def test_check_delivery_on_clean_clip_returns_structured_dict(tmp_path: Path) -> None:
    clip = _make_clean_clip(tmp_path / "clean.mp4")

    result = check_delivery(clip)

    assert set(result) >= {"ok", "issues", "raw"}
    assert isinstance(result["ok"], bool)
    assert isinstance(result["issues"], list)
    assert isinstance(result["raw"], dict)


def test_check_delivery_on_text_file_fails_softly(tmp_path: Path) -> None:
    text_file = tmp_path / "not_a_video.mp4"
    text_file.write_text("this is plain text, not a video\n", encoding="utf-8")

    result = check_delivery(text_file)

    assert result["ok"] is False
    assert isinstance(result["issues"], list) and result["issues"]
    assert isinstance(result["raw"], dict)


def test_loudness_help_exits_zero() -> None:
    script = ffmpeg_tools.TOOLS_DIR / "loudness.py"
    if not script.is_file():
        pytest.skip("vendored loudness.py is not present")

    result = run_tool("loudness", ["--help"], timeout_sec=60)

    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()
