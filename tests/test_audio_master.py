"""Tests for audio_master: loudnorm filter builders and the two-pass audio normalisation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoshorts.media.audio_master import (
    build_loudnorm_filter,
    measure_loudness,
    normalize_two_pass,
)
from autoshorts.media.encoder_probe import get_ffmpeg_path

MEASURED = {
    "input_i": -23.5,
    "input_tp": -2.1,
    "input_lra": 6.4,
    "input_thresh": -34.0,
    "target_offset": 9.5,
}


def test_two_pass_filter_is_linear_and_ends_with_limiter() -> None:
    fg = build_loudnorm_filter(MEASURED, -14.0)

    assert "linear=true" in fg
    assert "measured_TP=" in fg
    assert "measured_I=-23.50" in fg
    assert "offset=9.50" in fg
    assert fg.startswith("loudnorm=I=-14:TP=-1.0:LRA=7:")
    assert fg.split(",")[-1].startswith("alimiter=")


def test_single_pass_filter_has_no_measured_values() -> None:
    fg = build_loudnorm_filter(None, -14.0)

    assert fg.startswith("loudnorm=")
    assert "measured_" not in fg
    assert "linear=true" not in fg
    assert (
        fg
        == "loudnorm=I=-14:TP=-1.0:LRA=7,alimiter=limit=0.8414:attack=5:release=50:level=disabled"
    )


def test_limiter_ceiling_for_minus_one_dbtp() -> None:
    limit = float(build_loudnorm_filter(None, -14.0).rsplit("limit=", 1)[1].split(":")[0])

    # -1.5 dBTP ceiling (-1 dBTP minus the 0.5 dB margin).
    assert limit == pytest.approx(0.841, abs=1e-3)


def test_measure_loudness_returns_empty_on_missing_file(tmp_path: Path) -> None:
    assert measure_loudness(tmp_path / "missing.mp4") == {}


def _make_clip(path: Path) -> None:
    """2-second mp4: a loud 1 kHz sine (near 0 dBFS peaks) with a test pattern."""
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=25:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=2",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-af",
        "volume=18dB",
        "-c:v",
        "mpeg4",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_normalize_two_pass_writes_valid_file_under_true_peak_ceiling(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    dst = tmp_path / "clip_mastered.mp4"
    _make_clip(src)
    before = measure_loudness(src)
    assert before, "synthetic clip must be measurable"

    result = normalize_two_pass(src, dst, target_lufs=-14.0)

    assert result == dst
    assert dst.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".clip_mastered.")]

    decode = subprocess.run(
        [
            get_ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(dst),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert decode.returncode == 0
    assert decode.stderr.strip() == ""

    after = measure_loudness(dst)
    assert after, "mastered clip must be measurable"
    assert after["input_tp"] <= -0.9
