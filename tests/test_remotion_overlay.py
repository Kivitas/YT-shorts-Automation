"""Tests for the Remotion caption overlay. The real-render test runs only when Remotion is installed."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoshorts.media.ass_styler import TimedWord
from autoshorts.media.remotion_overlay import (
    REMOTION_PROJECT_DIR,
    RemotionRenderError,
    RemotionUnavailableError,
    build_caption_timeline,
    is_remotion_ready,
    overlay_captions,
    render_caption_overlay,
    write_caption_plan,
)


def _cut(start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(cut_start=start, cut_end=end)


def test_timeline_without_edits_keeps_timings_and_duration():
    words = [TimedWord("A", 1.0, 1.5), TimedWord("B", 2.0, 2.5)]
    timed, dur = build_caption_timeline(words, total_duration_sec=10.0)
    assert [(w.word, w.start, w.end) for w in timed] == [("A", 1.0, 1.5), ("B", 2.0, 2.5)]
    assert dur == 10.0


def test_timeline_shifts_words_after_a_jump_cut_and_shortens_duration():
    words = [TimedWord("BEFORE", 1.0, 1.5), TimedWord("AFTER", 6.0, 6.5)]
    timed, dur = build_caption_timeline(words, jump_cuts=[_cut(3.0, 5.0)], total_duration_sec=10.0)
    assert [w.word for w in timed] == ["BEFORE", "AFTER"]
    assert timed[1].start == pytest.approx(4.0)
    assert dur == pytest.approx(8.0)


def test_timeline_drops_words_inside_a_jump_cut():
    words = [TimedWord("KEEP", 1.0, 1.4), TimedWord("GONE", 3.2, 3.6)]
    timed, _ = build_caption_timeline(words, jump_cuts=[_cut(3.0, 4.0)])
    assert [w.word for w in timed] == ["KEEP"]


def test_plan_file_has_props_the_composition_expects(tmp_path: Path):
    words = [TimedWord("HI", 0.0, 0.5), TimedWord("THERE", 0.6, 1.0)]
    plan = write_caption_plan(
        tmp_path / "plan.json", words, duration_sec=12.0, style_preset="rapid"
    )
    data = json.loads(plan.read_text(encoding="utf-8"))
    assert data["fps"] == 30
    assert data["durationSec"] == 12.0
    assert data["wordsPerPage"] == 2
    assert data["words"] == [
        {"text": "HI", "start": 0.0, "end": 0.5},
        {"text": "THERE", "start": 0.6, "end": 1.0},
    ]


def test_non_rapid_presets_show_three_words_per_page(tmp_path: Path):
    plan = write_caption_plan(tmp_path / "p.json", [], duration_sec=5.0, style_preset="neon")
    assert json.loads(plan.read_text(encoding="utf-8"))["wordsPerPage"] == 3


def test_render_fails_clearly_when_remotion_is_not_installed(tmp_path: Path):
    assert is_remotion_ready(tmp_path) is False
    with pytest.raises(RemotionUnavailableError, match="npm install"):
        render_caption_overlay(tmp_path / "plan.json", tmp_path / "out.webm", project_dir=tmp_path)


def test_overlay_raises_on_missing_inputs(tmp_path: Path):
    from autoshorts.media.encoder_probe import get_ffmpeg_path

    with pytest.raises(RemotionRenderError):
        overlay_captions(
            tmp_path / "missing.mp4",
            tmp_path / "missing.webm",
            tmp_path / "out.mp4",
            ffmpeg_path=get_ffmpeg_path(),
        )


def test_config_defaults_to_ass_captions():
    from autoshorts.core.config import SubtitlesConfig

    assert SubtitlesConfig().caption_engine == "ass"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_active_word_stays_on_last_spoken_word_between_words():
    """Regression: in a gap between words the highlight must stay on the last spoken word.

    The old findIndex(t in [start, end)) fell back to index 0, so the first word of the page
    was highlighted during every gap.
    """
    helper = REMOTION_PROJECT_DIR / "src" / "captionTiming.ts"
    script = (
        f"import {{ activeWordIndex }} from {json.dumps(helper.as_uri())};"
        "const page = [{start:0},{start:1},{start:2}];"
        "console.log(JSON.stringify(["
        "activeWordIndex(page, 0.5), activeWordIndex(page, 1.5),"
        "activeWordIndex(page, 2.9), activeWordIndex(page, 0.0)]));"
    )
    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--no-warnings",
            "--input-type=module",
            "-e",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(proc.stdout.strip()) == [0, 1, 2, 0]


@pytest.mark.skipif(not is_remotion_ready(REMOTION_PROJECT_DIR), reason="Remotion not installed")
def test_real_remotion_render_and_overlay(tmp_path: Path):
    import subprocess

    from autoshorts.media.encoder_probe import get_ffmpeg_path

    ff = get_ffmpeg_path()
    base = tmp_path / "base.mp4"
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=1080x1920:rate=30",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(base),
        ],
        check=True,
    )
    words = [TimedWord("HELLO", 0.1, 0.6), TimedWord("WORLD", 0.7, 1.2)]
    plan = write_caption_plan(tmp_path / "plan.json", words, duration_sec=2.0)
    track = render_caption_overlay(plan, tmp_path / "captions.webm")
    out = overlay_captions(base, track, tmp_path / "captioned.mp4", ffmpeg_path=ff)
    assert out.is_file() and out.stat().st_size > 0
    # The caption track must be transparent outside the text: a corner pixel keeps the red base.
    raw = subprocess.run(
        [
            ff,
            "-loglevel",
            "error",
            "-i",
            str(out),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    r, g, b = raw[0], raw[1], raw[2]
    assert r >= 245 and g <= 10 and b <= 10, f"corner pixel is {(r, g, b)}, expected red"
