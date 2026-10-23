"""Tests for the single hardware encoder detector and ffprobe discovery (encoder_probe)."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoshorts.media import compositor as comp
from autoshorts.media import encoder_probe as ep


@pytest.fixture(autouse=True)
def _isolated_probe_cache():
    ep.clear_hardware_encoder_cache()
    yield
    ep.clear_hardware_encoder_cache()


@pytest.fixture
def ffmpeg_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "is_ffmpeg_available", lambda *a, **k: True)


def test_probe_timeout_is_named_cold_start_budget() -> None:
    assert ep.ENCODER_PROBE_TIMEOUT_SEC == 15.0


def test_single_detector_is_shared_by_compositor_and_probe() -> None:
    assert comp.detect_hardware_encoder is ep.detect_hardware_encoder
    assert comp.clear_hardware_encoder_cache is ep.clear_hardware_encoder_cache
    assert comp.get_ffmpeg_path is ep.get_ffmpeg_path


def test_detector_and_probe_agree_and_are_probed_once(
    monkeypatch: pytest.MonkeyPatch, ffmpeg_usable: None
) -> None:
    calls: list[str] = []

    def _encode(codec, extra_args=None, ffmpeg_binary="ffmpeg", timeout_sec=0.0) -> bool:
        calls.append(codec)
        return codec == "h264_qsv"

    monkeypatch.setattr(ep, "run_tiny_test_encode", _encode)

    first = comp.detect_hardware_encoder(preferred="auto", ffmpeg_binary="/fake/ffmpeg")
    second = ep.probe_encoder(preferred="auto", ffmpeg_binary="/fake/ffmpeg")

    assert first.codec == second.codec == "h264_qsv"
    assert first is second
    assert calls == ["h264_nvenc", "hevc_videotoolbox", "h264_videotoolbox", "h264_qsv"]


def test_all_hardware_fail_falls_back_to_cpu(
    monkeypatch: pytest.MonkeyPatch, ffmpeg_usable: None
) -> None:
    monkeypatch.setattr(ep, "run_tiny_test_encode", lambda codec, *a, **k: codec == "libx264")
    info = ep.detect_hardware_encoder(preferred="auto", ffmpeg_binary="/fake/ffmpeg")
    assert info.codec == "libx264"
    assert info.is_hardware_accelerated is False
    assert info.verified is True


def test_unusable_ffmpeg_returns_unverified_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "is_ffmpeg_available", lambda *a, **k: False)
    info = ep.probe_encoder(preferred="auto", ffmpeg_binary="/fake/ffmpeg")
    assert info.codec == "libx264"
    assert info.verified is False


def test_nonzero_exit_marks_encoder_unavailable_with_reason(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Warning line\nUnknown encoder 'h264_nvenc'\n"
        )

    monkeypatch.setattr(ep.subprocess, "run", _run)
    with caplog.at_level(logging.INFO, logger="autoshorts.media.encoder_probe"):
        assert ep.run_tiny_test_encode("h264_nvenc", ffmpeg_binary="/fake/ffmpeg") is False

    messages = [r.getMessage() for r in caplog.records if "h264_nvenc" in r.getMessage()]
    assert len(messages) == 1
    assert "exit code 1" in messages[0]
    assert "Unknown encoder" in messages[0]


def test_timeout_kills_child_and_reports_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A real hung child is killed by subprocess.run on timeout and reported as unavailable."""
    real_run = subprocess.run
    hang_cmd = [sys.executable, "-c", "import time; time.sleep(60)"]

    def _hang(cmd, **kwargs):
        return real_run(hang_cmd, **kwargs)

    monkeypatch.setattr(ep.subprocess, "run", _hang)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="autoshorts.media.encoder_probe"):
        ok = ep.run_tiny_test_encode("h264_nvenc", ffmpeg_binary="/fake/ffmpeg", timeout_sec=1.0)
    elapsed = time.monotonic() - started

    assert ok is False
    assert elapsed < 30.0, "the hung child was not killed at the timeout"
    messages = [r.getMessage() for r in caplog.records if "h264_nvenc" in r.getMessage()]
    assert len(messages) == 1
    assert "timed out" in messages[0]


def test_get_ffprobe_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def _which(name, *a, **k):
        return "C:/tools/ffprobe.exe" if name == "ffprobe" else None

    monkeypatch.setattr(ep.shutil, "which", _which)
    assert ep.get_ffprobe_path() == "C:/tools/ffprobe.exe"


def test_get_ffprobe_finds_sibling_of_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg = tmp_path / "bin" / "ffmpeg.exe"
    ffmpeg.parent.mkdir()
    ffmpeg.write_bytes(b"x")
    ffprobe = ffmpeg.parent / "ffprobe.exe"
    ffprobe.write_bytes(b"x")

    monkeypatch.setattr(ep.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(ep, "get_ffmpeg_path", lambda: str(ffmpeg))
    assert ep.get_ffprobe_path() == str(ffprobe)


def test_get_ffprobe_none_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"x")

    monkeypatch.setattr(ep.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(ep, "get_ffmpeg_path", lambda: str(ffmpeg))
    assert ep.get_ffprobe_path() is None
