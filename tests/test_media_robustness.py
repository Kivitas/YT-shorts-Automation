"""Loudness-skip, measurement/probe caches and subprocess-bound tests (no real ffmpeg needed)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from autoshorts.media import audio_master, ffmpeg_tools
from autoshorts.media.audio_master import clear_measurement_cache, normalize_two_pass


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_measurement_cache()
    ffmpeg_tools.clear_probe_cache()
    yield
    clear_measurement_cache()
    ffmpeg_tools.clear_probe_cache()


def _measurement(i: float, tp: float = -3.0) -> dict:
    return {
        "input_i": i,
        "input_tp": tp,
        "input_lra": 5.0,
        "input_thresh": i - 10,
        "target_offset": 0.0,
    }


def test_already_loud_enough_skips_reencode(tmp_path: Path) -> None:
    src, dst = tmp_path / "a.mp4", tmp_path / "b.mp4"
    src.write_bytes(b"video")
    with (
        patch.object(audio_master, "_measure_loudness_uncached", return_value=_measurement(-14.4)),
        patch.object(audio_master, "run_command_with_cleanup") as enc,
    ):
        out = normalize_two_pass(src, dst, -14.0)
    enc.assert_not_called()
    assert out == dst and dst.read_bytes() == b"video"


def test_in_place_skip_leaves_file_untouched(tmp_path: Path) -> None:
    src = tmp_path / "a.mp4"
    src.write_bytes(b"video")
    with (
        patch.object(audio_master, "_measure_loudness_uncached", return_value=_measurement(-13.8)),
        patch.object(audio_master, "run_command_with_cleanup") as enc,
    ):
        assert normalize_two_pass(src, src, -14.0) == src
    enc.assert_not_called()
    assert src.read_bytes() == b"video"
    assert not [p for p in tmp_path.iterdir() if p != src]


def test_outside_tolerance_or_hot_peak_still_encodes(tmp_path: Path) -> None:
    for meas in (_measurement(-20.0), _measurement(-14.0, tp=0.5)):
        clear_measurement_cache()
        src, dst = tmp_path / "a.mp4", tmp_path / "b.mp4"
        src.write_bytes(b"video")

        def fake_encode(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"mastered")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            patch.object(audio_master, "_measure_loudness_uncached", return_value=meas),
            patch.object(audio_master, "run_command_with_cleanup", side_effect=fake_encode) as enc,
        ):
            normalize_two_pass(src, dst, -14.0)
        enc.assert_called_once()
        assert dst.read_bytes() == b"mastered"
        assert "measured_I=" in " ".join(enc.call_args.args[0])
        assert enc.call_args.kwargs["timeout"] > 0  # bounded


def test_encode_failure_keeps_original_and_does_not_raise(tmp_path: Path) -> None:
    src, dst = tmp_path / "a.mp4", tmp_path / "b.mp4"
    src.write_bytes(b"video")
    bad = subprocess.CompletedProcess([], 1, "", "boom")
    with (
        patch.object(audio_master, "_measure_loudness_uncached", return_value=_measurement(-25)),
        patch.object(audio_master, "run_command_with_cleanup", return_value=bad),
    ):
        out = normalize_two_pass(src, dst, -14.0)
    assert out == dst and dst.read_bytes() == b"video"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_encode_timeout_keeps_original_in_place(tmp_path: Path) -> None:
    src = tmp_path / "a.mp4"
    src.write_bytes(b"video")
    with (
        patch.object(audio_master, "_measure_loudness_uncached", return_value=_measurement(-25)),
        patch.object(
            audio_master,
            "run_command_with_cleanup",
            side_effect=subprocess.TimeoutExpired("ffmpeg", 1),
        ),
    ):
        assert normalize_two_pass(src, src, -14.0) == src
    assert src.read_bytes() == b"video"
    assert [p.name for p in tmp_path.iterdir()] == ["a.mp4"]


def test_measurement_is_cached_until_file_changes(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"1234")
    with patch.object(
        audio_master, "_measure_loudness_uncached", return_value=_measurement(-14)
    ) as m:
        audio_master.measure_loudness(f)
        audio_master.measure_loudness(f)
        assert m.call_count == 1
        audio_master.measure_loudness(f, target_lufs=-16.0)  # different target: distinct key
        assert m.call_count == 2
        f.write_bytes(b"12345678")  # size change invalidates
        audio_master.measure_loudness(f)
        assert m.call_count == 3


def test_failed_measurement_is_not_cached(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"1234")
    with patch.object(audio_master, "_measure_loudness_uncached", return_value={}) as m:
        audio_master.measure_loudness(f)
        audio_master.measure_loudness(f)
    assert m.call_count == 2


# --- probe cache ------------------------------------------------------------------------


def test_probe_cache_hits_and_invalidates(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"abc")
    info = {"has_video": True, "has_audio": True, "duration": 12.5}
    with patch.object(ffmpeg_tools, "probe_video_stream_info", return_value=info) as probe:
        assert ffmpeg_tools.probe_duration_cached(f) == 12.5
        assert ffmpeg_tools.has_audio_stream_cached(f) is True
        assert probe.call_count == 1
        f.write_bytes(b"abcdef")
        ffmpeg_tools.probe_media_cached(f)
        assert probe.call_count == 2


def test_probe_cache_returns_private_copy_and_skips_failures(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"abc")
    good = {"has_video": True, "has_audio": False, "duration": 3.0}
    with patch.object(ffmpeg_tools, "probe_video_stream_info", return_value=good):
        first = ffmpeg_tools.probe_media_cached(f)
        first["duration"] = 99
        assert ffmpeg_tools.probe_media_cached(f)["duration"] == 3.0
    ffmpeg_tools.clear_probe_cache()
    empty = {"has_video": False, "has_audio": False, "duration": 0.0}
    with patch.object(ffmpeg_tools, "probe_video_stream_info", return_value=empty) as probe:
        ffmpeg_tools.probe_media_cached(f)
        ffmpeg_tools.probe_media_cached(f)
    assert probe.call_count == 2


def test_probe_cache_is_thread_safe(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"abc")
    info = {"has_video": True, "has_audio": True, "duration": 1.0}
    errors: list[Exception] = []

    def work() -> None:
        try:
            for _ in range(50):
                assert ffmpeg_tools.probe_duration_cached(f) == 1.0
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    with patch.object(ffmpeg_tools, "probe_video_stream_info", return_value=info):
        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
    assert not errors


# --- safe-zone preview bounded ----------------------------------------------------------


def test_safe_zone_preview_timeout_removes_partial_output(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out = tmp_path / "prev.mp4"

    def hang(cmd, **kw):
        assert kw["timeout"] > 0
        Path(cmd[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    with (
        patch.object(ffmpeg_tools, "probe_duration_cached", return_value=10.0),
        patch.object(ffmpeg_tools.subprocess, "run", side_effect=hang),
        pytest.raises(ffmpeg_tools.FfmpegToolError, match="timed out"),
    ):
        ffmpeg_tools.render_safe_zone_preview(video, out, platform="shorts")
    assert not out.exists()


def test_shared_transcript_cache_roundtrip(tmp_path: Path, monkeypatch) -> None:
    import autoshorts.pipeline as pipeline_mod
    from autoshorts.pipeline import stages_ingest as si

    monkeypatch.setattr(pipeline_mod, "get_work_dir", lambda: tmp_path)
    path = si._shared_transcript_path("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert path is not None and path.name == "dQw4w9WgXcQ.json"
    assert si._load_shared_transcript(path) == []  # miss
    si._store_shared_transcript(path, [{"start": 0.0, "end": 1.0, "text": "hi"}])
    assert si._load_shared_transcript(path)[0]["text"] == "hi"
    path.write_text("{not json", encoding="utf-8")
    assert si._load_shared_transcript(path) == []  # corrupt -> miss
    assert si._shared_transcript_path("not a url at all") is None
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
