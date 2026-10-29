"""Tests for the vendored ffmpeg-skill bridge (autoshorts.media.ffmpeg_tools)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoshorts.media import ffmpeg_tools
from autoshorts.media.encoder_probe import get_ffmpeg_path
from autoshorts.media.ffmpeg_tools import (
    FfmpegToolError,
    apply_speed_ramp,
    check_delivery,
    detect_black_bars,
    detect_scenes_and_highlights,
    detect_speech_silences,
    insert_broll_cutaways,
    render_safe_zone_preview,
    run_tool,
)


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


def test_allowed_tools_expansion() -> None:
    expected = {
        "check",
        "loudness",
        "silence",
        "caption",
        "probe",
        "metadata",
        "report",
        "broll",
        "speedramp",
        "cropdetect",
        "scenes",
        "look",
        "graphics",
        "stabilize",
        "cut",
    }
    assert expected.issubset(ffmpeg_tools.ALLOWED_TOOLS)


def test_check_delivery_on_clean_clip_returns_structured_dict(tmp_path: Path) -> None:
    clip = _make_clean_clip(tmp_path / "clean.mp4")

    result = check_delivery(clip, platform="shorts")

    assert set(result) >= {"ok", "issues", "raw"}
    assert isinstance(result["ok"], bool)
    assert isinstance(result["issues"], list)
    assert isinstance(result["raw"], dict)

    res_yt = check_delivery(clip, platform="youtube")
    assert set(res_yt) >= {"ok", "issues", "raw"}


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


def test_detect_black_bars_wrapper(tmp_path: Path) -> None:
    clip = _make_clean_clip(tmp_path / "crop_test.mp4")
    res = detect_black_bars(clip, seconds=1.0, samples=1)
    assert "ok" in res
    assert "crop" in res


def test_detect_scenes_and_highlights_wrapper(tmp_path: Path) -> None:
    clip = _make_clean_clip(tmp_path / "scenes_test.mp4")
    res = detect_scenes_and_highlights(clip, highlights=2)
    assert "ok" in res
    assert "scenes" in res or "raw" in res


def test_detect_speech_silences_wrapper(tmp_path: Path) -> None:
    clip = _make_clean_clip(tmp_path / "silence_test.mp4")
    res = detect_speech_silences(clip, threshold_db=-40)
    assert "ok" in res
    assert "silences" in res or "raw" in res


def test_apply_speed_ramp_wrapper(tmp_path: Path) -> None:
    clip = _make_clean_clip(tmp_path / "ramp_src.mp4")
    out = tmp_path / "ramp_out.mp4"
    # Clip is 2s long; cover 0..2
    res = apply_speed_ramp(clip, out, segments=["0-1:1.0", "1-2:1.5"])
    assert res.is_file()
    assert res == out.resolve()


def test_insert_broll_cutaways_wrapper(tmp_path: Path) -> None:
    clip_a = _make_clean_clip(tmp_path / "a_roll.mp4")
    clip_b = _make_clean_clip(tmp_path / "b_roll.mp4")
    out = tmp_path / "broll_out.mp4"
    res = insert_broll_cutaways(
        clip_a,
        out,
        insertions=[{"clip": clip_b, "at": 0.5, "duration": 0.8}],
    )
    assert res.is_file()
    assert res == out.resolve()


def test_render_safe_zone_preview_wrapper(tmp_path: Path) -> None:
    clip = _make_clean_clip(tmp_path / "preview_src.mp4")
    out_png = tmp_path / "preview.png"
    res_png = render_safe_zone_preview(clip, out_png, platform="shorts")
    assert res_png.is_file()

    out_mp4 = tmp_path / "preview.mp4"
    res_mp4 = render_safe_zone_preview(clip, out_mp4, platform="tiktok")
    assert res_mp4.is_file()
