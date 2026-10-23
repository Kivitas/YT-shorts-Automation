"""Regression tests for the FFmpeg compositing pipeline.

Real-FFmpeg tests use the bundled binary resolved by get_ffmpeg_path() (system PATH first,
then imageio-ffmpeg) and skip when no binary is available. ffprobe is deliberately not
required: the imageio bundle ships without it, and the probe helpers must cope.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.media import compositor as comp
from autoshorts.media.ass_styler import (
    build_ass_script,
    escape_ass_text,
    format_ass_timestamp,
    write_ass_file,
)
from autoshorts.media.compositor import (
    FFmpegCompositor,
    find_best_thumbnail_timestamp,
    has_audio_stream,
    has_video_stream,
    probe_media_duration,
    stage_subtitles_for_filter,
)
from autoshorts.media.dopamine_effects import build_watermark_filter
from autoshorts.media.downloader import probe_video_stream_info
from autoshorts.media.encoder_probe import is_ffmpeg_available

FFMPEG = comp.get_ffmpeg_path()
requires_ffmpeg = pytest.mark.skipif(
    not is_ffmpeg_available(FFMPEG),
    reason="No FFmpeg binary available (system PATH or imageio-ffmpeg bundle).",
)
LAYOUTS = ["split_50_50", "split_70_30", "full_blur_bg", "full_center_crop", "pip_gameplay"]


@pytest.fixture
def no_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a machine where ffprobe is not on PATH (only ffmpeg is reachable)."""
    real_which = shutil.which

    def _which(name: str, *args: object, **kwargs: object) -> str | None:
        if name == "ffprobe":
            return None
        return real_which(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(comp.shutil, "which", _which)


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        [FFMPEG, "-y", "-v", "error", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]


def _make_clip(path: Path, seconds: float = 2.0, audio: bool = True) -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc=size=1280x720:rate=30:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=800:duration={seconds}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args.append(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(args)
    return path


def _make_subtitles(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return write_ass_file(path, [{"word": "Hello", "start": 0.1, "end": 0.6}], hook_title="HOOK")


def _compose(
    clip: Path,
    bg: Path,
    sub: Path,
    out: Path,
    layout: str = "split_50_50",
    edl: EditDecisionList | None = None,
) -> Path:
    compositor = FFmpegCompositor(encoder_preferred="libx264", ffmpeg_binary=FFMPEG)
    return compositor.compose(
        job_id="job_pipeline_test",
        source_clip=clip,
        edl=edl,
        background_path=bg,
        subtitles_path=sub,
        output_path=out,
        dopamine_level="high",
        layout_mode=layout,
    )


# -----------------------------------------------------------------------------
# Probe helpers (no ffprobe on PATH)
# -----------------------------------------------------------------------------


@requires_ffmpeg
def test_probes_work_without_ffprobe(tmp_path: Path, no_ffprobe: None) -> None:
    """Duration and stream checks must come from the ffmpeg fallback, not from defaults."""
    with_audio = _make_clip(tmp_path / "with_audio.mp4", seconds=2.0, audio=True)
    silent = _make_clip(tmp_path / "silent.mp4", seconds=2.0, audio=False)

    assert probe_media_duration(with_audio) == pytest.approx(2.0, abs=0.3)
    assert has_audio_stream(with_audio) is True
    assert has_audio_stream(silent) is False  # previously True (optimistic) without ffprobe
    assert has_video_stream(silent) is True


@requires_ffmpeg
def test_corrupt_media_is_rejected_without_ffprobe(tmp_path: Path, no_ffprobe: None) -> None:
    """A truncated or non-video file must not pass as a usable background/source."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video container" * 64)

    assert has_video_stream(broken) is False  # previously True (optimistic) without ffprobe
    assert has_audio_stream(broken) is False
    assert probe_media_duration(broken, default=7.0) == 7.0


_FAKE_FFPROBE = "C:/fake/bin/ffprobe.exe"
_PROBE_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "width": 1280, "height": 720},
            {"codec_type": "audio", "duration": "3.5"},
        ],
        "format": {"duration": "3.520000"},
    }
)


@pytest.fixture
def fake_ffprobe(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Pretend ffprobe lives at _FAKE_FFPROBE. Tests set 'stdout'/'returncode' per case."""
    state: dict[str, Any] = {"stdout": "", "returncode": 0, "cmds": []}
    monkeypatch.setattr(comp, "get_ffprobe_path", lambda: _FAKE_FFPROBE)

    def _fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess:
        if not cmd or cmd[0] != _FAKE_FFPROBE:
            raise AssertionError(f"unexpected subprocess call while ffprobe is present: {cmd}")
        state["cmds"].append(cmd)
        return subprocess.CompletedProcess(
            cmd, state["returncode"], stdout=state["stdout"], stderr=""
        )

    monkeypatch.setattr(comp.subprocess, "run", _fake_run)
    return state


def test_probes_use_ffprobe_json_when_present(
    tmp_path: Path, fake_ffprobe: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ffprobe present, durations and streams come from its JSON output only."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not decoded: the fake ffprobe answers")

    def _no_fallback(path: Path) -> dict[str, object]:
        raise AssertionError("ffmpeg -i fallback must not run when ffprobe is present")

    monkeypatch.setattr(comp, "probe_video_stream_info", _no_fallback)
    fake_ffprobe["stdout"] = _PROBE_JSON

    assert probe_media_duration(clip) == pytest.approx(3.52)
    assert has_audio_stream(clip) is True
    assert has_video_stream(clip) is True
    cmd = fake_ffprobe["cmds"][0]
    assert cmd[0] == _FAKE_FFPROBE
    for flag in ("-print_format", "json", "-show_format", "-show_streams"):
        assert flag in cmd


def test_ffprobe_present_rejects_corrupt_file(tmp_path: Path, fake_ffprobe: dict[str, Any]) -> None:
    """ffprobe failing on a corrupt file means no streams: has_* False, duration default."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video container" * 64)
    fake_ffprobe["returncode"] = 1

    assert has_video_stream(broken) is False
    assert has_audio_stream(broken) is False
    assert probe_media_duration(broken, default=7.0) == 7.0


def test_ffprobe_missing_uses_logged_fallback_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without ffprobe, the ffmpeg -i parse answers and a single warning is logged per process."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 64)
    calls: list[Path] = []

    def _fake_info(path: Path) -> dict[str, object]:
        calls.append(path)
        return {"duration": 2.0, "has_audio": True, "has_video": True}

    monkeypatch.setattr(comp, "get_ffprobe_path", lambda: None)
    monkeypatch.setattr(comp, "probe_video_stream_info", _fake_info)
    monkeypatch.setattr(comp, "_FFPROBE_FALLBACK_WARNED", False)

    with caplog.at_level(logging.WARNING, logger="autoshorts.media.compositor"):
        assert probe_media_duration(clip) == pytest.approx(2.0)
        assert has_audio_stream(clip) is True
        assert has_video_stream(clip) is True

    assert len(calls) == 3
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "ffprobe is not installed" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "ffmpeg -i" in warnings[0]


def test_stderr_tail_keeps_end_of_long_log() -> None:
    """Error messages must carry the end of ffmpeg's stderr, where the failure is."""
    long_log = "x" * 5000 + "Conversion failed!"
    tail = comp._stderr_tail(long_log, limit=100)

    assert tail.endswith("Conversion failed!")
    assert len(tail) <= 100
    assert comp._stderr_tail(None) == ""
    assert comp._stderr_tail(b"bytes ok\n") == "bytes ok"


# -----------------------------------------------------------------------------
# Subtitle path staging (apostrophes break escape_ffmpeg_filter_path)
# -----------------------------------------------------------------------------


def test_stage_subtitles_skips_plain_paths(tmp_path: Path) -> None:
    plain_sub = tmp_path / "plain" / "a.ass"
    plain_sub.parent.mkdir(parents=True)
    plain_sub.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    assert stage_subtitles_for_filter(plain_sub, out_dir / "s.mp4", "job") is None


def test_stage_subtitles_copies_apostrophe_paths(tmp_path: Path) -> None:
    quoted_sub = tmp_path / "O'Brien clips" / "a.ass"
    quoted_sub.parent.mkdir(parents=True)
    quoted_sub.write_text("subtitle body", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    staged = stage_subtitles_for_filter(quoted_sub, out_dir / "s.mp4", "job 1/x")

    assert staged is not None
    assert "'" not in str(staged)
    assert staged.parent == out_dir
    assert staged.read_text(encoding="utf-8") == "subtitle body"
    staged.unlink()


@requires_ffmpeg
def test_compose_renders_with_apostrophe_in_subtitle_path(tmp_path: Path) -> None:
    clip = _make_clip(tmp_path / "src.mp4", seconds=2.0)
    bg = _make_clip(tmp_path / "bg.mp4", seconds=2.0, audio=False)
    sub = _make_subtitles(tmp_path / "O'Brien clips" / "job.ass")
    out_dir = tmp_path / "out"

    result = _compose(clip, bg, sub, out_dir / "short.mp4")

    info = probe_video_stream_info(result)
    assert info["resolution"] == "1080x1920"
    # The staged subtitle copy must be cleaned up after the render.
    assert not [p for p in out_dir.iterdir() if p.name.endswith("_subs.ass")]


# -----------------------------------------------------------------------------
# Real renders: layouts, sync, faststart, silent source
# -----------------------------------------------------------------------------


@requires_ffmpeg
@pytest.mark.parametrize("layout", LAYOUTS)
def test_compose_layouts_render_exact_9x16_with_edits(tmp_path: Path, layout: str) -> None:
    clip = _make_clip(tmp_path / "src.mp4", seconds=2.0, audio=True)
    bg = _make_clip(tmp_path / "bg.mp4", seconds=2.0, audio=False)
    sub = _make_subtitles(tmp_path / "job.ass")
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=2.0,
        jump_cuts=[JumpCut(cut_start=0.6, cut_end=0.9, reason="pause")],
        zoom_punch_ins=[ZoomPunchIn(timestamp=0.1, scale=1.1, duration=0.5)],
    )

    out = _compose(clip, bg, sub, tmp_path / "out" / "short.mp4", layout=layout, edl=edl)

    info = probe_video_stream_info(out)
    assert info["resolution"] == "1080x1920"
    assert info["has_audio"] is True
    assert 1.0 < info["duration"] < 2.5  # 0.3s excised by the jump cut
    data = out.read_bytes()
    assert data.find(b"moov") < data.find(b"mdat"), "moov atom must precede mdat (+faststart)"


@requires_ffmpeg
def test_compose_silent_source_gets_silent_audio_track(tmp_path: Path) -> None:
    clip = _make_clip(tmp_path / "silent.mp4", seconds=2.0, audio=False)
    bg = _make_clip(tmp_path / "bg.mp4", seconds=2.0, audio=False)
    sub = _make_subtitles(tmp_path / "job.ass")

    out = _compose(clip, bg, sub, tmp_path / "out" / "short.mp4", layout="split_50_50")

    info = probe_video_stream_info(out)
    assert info["resolution"] == "1080x1920"
    assert info["has_audio"] is True


@requires_ffmpeg
def test_watermark_percent_and_apostrophe_render_cleanly() -> None:
    """A '%' in the handle must not reach drawtext as a stray token (was 'Stray %')."""
    wm = build_watermark_filter("Bob's 50% off", width=1080, height=1920)
    proc = subprocess.run(
        [
            FFMPEG,
            "-v",
            "warning",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:d=1",
            "-vf",
            wm,
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr[-500:]
    assert "Stray %" not in proc.stderr


# -----------------------------------------------------------------------------
# Thumbnails
# -----------------------------------------------------------------------------


@requires_ffmpeg
def test_auto_thumbnail_scores_real_frames(tmp_path: Path) -> None:
    """Black for the first 1.2s, then detailed content: the best frame must come after that.

    Before the fix, image2pipe emitted JPEG, the P6 parser rejected every frame, and the
    first candidate (0.42s) always won.
    """
    clip = tmp_path / "black_then_test.mp4"
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:r=30:d=1.2",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=30:duration=2.8",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    )

    best = find_best_thumbnail_timestamp(clip, ffmpeg_binary=FFMPEG)

    assert best > 1.2


@requires_ffmpeg
def test_thumbnail_past_end_of_clip_falls_back(tmp_path: Path) -> None:
    clip = _make_clip(tmp_path / "short.mp4", seconds=2.0, audio=False)
    compositor = FFmpegCompositor(encoder_preferred="libx264", ffmpeg_binary=FFMPEG)

    thumb = compositor.extract_thumbnail(clip, tmp_path / "thumb.jpg", timestamp_sec=30.0)

    assert thumb.is_file()
    assert thumb.stat().st_size > 0


# -----------------------------------------------------------------------------
# ASS generation
# -----------------------------------------------------------------------------


def test_escape_ass_text_neutralises_override_syntax() -> None:
    escaped = escape_ass_text("a{b}c\\Nd\ne")

    assert "{" not in escaped and "}" not in escaped
    assert "\\" not in escaped
    assert "\n" not in escaped


def test_ass_dialogue_with_braces_and_backslashes_stays_plain_text() -> None:
    script = build_ass_script(
        [
            {"word": "{evil", "start": 0.0, "end": 0.5},
            {"word": "x\\Ny,z}", "start": 0.5, "end": 1.0},
        ],
        hook_title="Hook {x}",
        text_transform="none",
        enable_emojis=False,
        onscreen_credit="Src {id}",
        total_duration_sec=6.0,
    )
    dialogues = [line for line in script.splitlines() if line.startswith("Dialogue:")]

    assert dialogues
    for line in dialogues:
        text = line.split(",,0,0,0,,", 1)[1]
        # Only the tags the builder itself emits (\fad, \c, \t, \fscx, ...) may open braces.
        for block in re.findall(r"\{[^}]*\}", text):
            assert block.startswith("{\\"), block
        assert "\\N" not in text
    assert "(evil" in script
    assert "x＼Ny,z)" in script


def test_ass_timestamps_and_event_ranges_are_valid() -> None:
    assert format_ass_timestamp(59.999) == "0:01:00.00"
    assert format_ass_timestamp(-1.0) == "0:00:00.00"
    assert format_ass_timestamp(3725.5) == "1:02:05.50"

    words = [
        {"word": "one", "start": 0.0, "end": 0.0},  # zero-length word
        {"word": "two.", "start": 0.0, "end": 0.2},  # overlapping start
        {"word": "three", "start": 0.3, "end": 0.9},
    ]
    script = build_ass_script(words, total_duration_sec=2.0)
    for line in script.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        _, start, end, *_rest = line.split(",", 9)
        h1, m1, rest1 = start.split(":")
        h2, m2, rest2 = end.split(":")
        t1 = int(h1) * 3600 + int(m1) * 60 + float(rest1)
        t2 = int(h2) * 3600 + int(m2) * 60 + float(rest2)
        assert t2 > t1 >= 0.0, line
