"""Review round 4: transcript cache keying, audio_master edge cases, ffmpeg_tools caches."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SEGS = [{"start": 0.0, "end": 5.0, "text": "hello"}, {"start": 5.0, "end": 9.0, "text": "world"}]


@pytest.fixture
def si(tmp_path: Path, monkeypatch):
    import autoshorts.pipeline as pipeline_mod
    from autoshorts.pipeline import stages_ingest

    monkeypatch.setattr(pipeline_mod, "get_work_dir", lambda: tmp_path)
    return stages_ingest


# --- transcript cache key -----------------------------------------------------------------


def test_transcript_cache_key_depends_on_language_and_model(si) -> None:
    base = si._shared_transcript_path(URL, languages=("en",), model="base.en")
    assert base == si._shared_transcript_path(URL, languages=("en",), model="base.en")
    assert base != si._shared_transcript_path(URL, languages=("de",), model="base.en")
    assert base != si._shared_transcript_path(URL, languages=("en",), model="large-v3")
    other_video = si._shared_transcript_path(
        "https://www.youtube.com/watch?v=aqz-KE-bpKQ", languages=("en",), model="base.en"
    )
    assert base != other_video


def test_transcript_cache_path_is_confined_to_cache_dir(si, tmp_path: Path) -> None:
    path = si._shared_transcript_path(URL, languages=("en",), model="../../evil")
    assert path is not None
    assert path.parent == tmp_path / "_transcript_cache"
    assert os.sep not in path.name and ".." not in path.name


def test_transcript_longer_than_video_is_rejected(si, tmp_path: Path) -> None:
    path = si._shared_transcript_path(URL)
    si._store_shared_transcript(path, [{"start": 0.0, "end": 3600.0, "text": "x"}])
    assert si._load_shared_transcript(path, duration=60.0) == []  # wrong video / wrong section
    assert si._load_shared_transcript(path, duration=3600.0)[0]["text"] == "x"
    assert si._load_shared_transcript(path)[0]["text"] == "x"  # unknown duration: no check


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"start": "a", "end": 1}],
        [{"start": 5, "end": 1}],
        [{"start": float("nan"), "end": 1}],
        [{"end": 1}],
        {"start": 0, "end": 1},
        [1, 2],
    ],
)
def test_malformed_cached_transcript_is_a_miss(si, payload) -> None:
    path = si._shared_transcript_path(URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert si._load_shared_transcript(path) == []


def test_oversized_cached_transcript_is_a_miss(si, monkeypatch) -> None:
    path = si._shared_transcript_path(URL)
    si._store_shared_transcript(path, SEGS)
    monkeypatch.setattr(si, "_TRANSCRIPT_CACHE_MAX_BYTES", 10)
    assert si._load_shared_transcript(path) == []


def test_concurrent_writers_never_leave_a_torn_or_temp_file(si) -> None:
    path = si._shared_transcript_path(URL)
    big = [{"start": float(i), "end": float(i + 1), "text": "x" * 50} for i in range(2000)]

    def writer() -> None:
        for _ in range(10):
            si._store_shared_transcript(path, big)

    threads = [threading.Thread(target=writer) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(si._load_shared_transcript(path)) == 2000
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_transcript_cache_is_bounded(si, monkeypatch) -> None:
    monkeypatch.setattr(si, "_TRANSCRIPT_CACHE_MAX_FILES", 3)
    directory = si._shared_transcript_path(URL).parent
    for i in range(6):
        vid = f"abcdefghi{i:02d}"
        si._store_shared_transcript(directory / f"{vid}.deadbeef00.json", SEGS)
    assert len(list(directory.glob("*.json"))) <= 3


# --- audio_master -----------------------------------------------------------------------------


def test_normalize_skips_sources_without_audio(tmp_path: Path) -> None:
    from autoshorts.media import audio_master

    src = tmp_path / "silent.mp4"
    src.write_bytes(b"video-only")
    dst = tmp_path / "out" / "result.mp4"
    with (
        patch(
            "autoshorts.media.ffmpeg_tools.probe_media_cached",
            return_value={"has_video": True, "has_audio": False},
        ),
        patch.object(audio_master, "measure_loudness") as measure,
        patch.object(audio_master, "run_command_with_cleanup") as run,
    ):
        result = audio_master.normalize_two_pass(src, dst, -14.0)
    assert result == dst and dst.read_bytes() == b"video-only"
    measure.assert_not_called()
    run.assert_not_called()


def test_normalize_creates_missing_destination_directory(tmp_path: Path) -> None:
    from autoshorts.media import audio_master

    src = tmp_path / "in.mp4"
    src.write_bytes(b"data")
    dst = tmp_path / "does" / "not" / "exist" / "out.mp4"
    measured = {
        "input_i": -14.2,
        "input_tp": -2.0,
        "input_lra": 5.0,
        "input_thresh": -24.0,
        "target_offset": 0.1,
    }
    with (
        patch(
            "autoshorts.media.ffmpeg_tools.probe_media_cached",
            return_value={"has_video": True, "has_audio": True},
        ),
        patch.object(audio_master, "measure_loudness", return_value=measured),
    ):
        assert audio_master.normalize_two_pass(src, dst, -14.0) == dst
    assert dst.read_bytes() == b"data"


def test_normalize_never_raises_on_ffmpeg_failure_and_cleans_temp(tmp_path: Path) -> None:
    from autoshorts.media import audio_master

    src = tmp_path / "in.mp4"
    src.write_bytes(b"data")
    dst = tmp_path / "out.mp4"
    measured = {
        "input_i": -30.0,
        "input_tp": -9.0,
        "input_lra": 5.0,
        "input_thresh": -40.0,
        "target_offset": 0.1,
    }
    failed = subprocess.CompletedProcess([], 1, "", "boom")
    with (
        patch(
            "autoshorts.media.ffmpeg_tools.probe_media_cached",
            return_value={"has_video": True, "has_audio": True},
        ),
        patch.object(audio_master, "measure_loudness", return_value=measured),
        patch.object(audio_master, "run_command_with_cleanup", return_value=failed),
    ):
        assert audio_master.normalize_two_pass(src, dst, -14.0) == dst
    assert dst.read_bytes() == b"data"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_keep_original_leaves_no_temp_files(tmp_path: Path) -> None:
    from autoshorts.media import audio_master

    src = tmp_path / "in.mp4"
    src.write_bytes(b"data")
    dst = tmp_path / "out.mp4"
    for _ in range(3):
        audio_master._keep_original(src, dst)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.mp4", "out.mp4"]


# --- ffmpeg_tools probe cache -------------------------------------------------------------------


def test_probe_cache_returns_independent_copies(tmp_path: Path) -> None:
    from autoshorts.media import ffmpeg_tools

    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    info = {"has_video": True, "has_audio": True, "duration": 3.0}
    ffmpeg_tools.clear_probe_cache()
    with patch.object(ffmpeg_tools, "probe_video_stream_info", return_value=dict(info)) as probe:
        first = ffmpeg_tools.probe_media_cached(media)
        first["duration"] = 999.0  # a caller mutating its copy must not poison the cache
        second = ffmpeg_tools.probe_media_cached(media)
    assert second["duration"] == 3.0
    assert probe.call_count == 1
    ffmpeg_tools.clear_probe_cache()


def test_probe_cache_invalidates_when_file_is_replaced(tmp_path: Path) -> None:
    from autoshorts.media import ffmpeg_tools

    media = tmp_path / "a.mp4"
    media.write_bytes(b"aaaa")
    stamp = media.stat().st_mtime_ns
    ffmpeg_tools.clear_probe_cache()
    results = iter([{"has_video": True, "duration": 1.0}, {"has_video": True, "duration": 2.0}])
    with patch.object(ffmpeg_tools, "probe_video_stream_info", side_effect=lambda p: next(results)):
        assert ffmpeg_tools.probe_media_cached(media)["duration"] == 1.0
        replacement = tmp_path / "b.mp4"
        replacement.write_bytes(b"bbbb")  # same size...
        os.utime(replacement, ns=(stamp, stamp))  # ...and same mtime
        os.replace(replacement, media)
        assert ffmpeg_tools.probe_media_cached(media)["duration"] == 2.0
    ffmpeg_tools.clear_probe_cache()


def test_safe_zone_png_fallback_renders_a_single_frame(tmp_path: Path) -> None:
    from autoshorts.media import ffmpeg_tools

    video = tmp_path / "in.mp4"
    video.write_bytes(b"x")
    out = tmp_path / "prev.png"
    seen: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    failed_look = subprocess.CompletedProcess([], 1, "", "no")
    with (
        patch.object(ffmpeg_tools, "run_tool", return_value=failed_look),
        patch.object(ffmpeg_tools, "probe_duration_cached", return_value=5.0),
        patch.object(ffmpeg_tools.subprocess, "run", side_effect=fake_run),
    ):
        ffmpeg_tools.render_safe_zone_preview(video, out, platform="shorts")
    cmd = seen["cmd"]
    assert "-frames:v" in cmd and "libx264" not in cmd
