"""Caption timing: hook trim and jump-cut remapping, shared by the ASS and Remotion caption paths."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoshorts.media import ass_styler
from autoshorts.media.ass_styler import (
    TimedWord,
    build_ass_script,
    offset_words_for_hook,
    remap_words_for_jump_cuts,
)
from autoshorts.media.remotion_overlay import build_caption_timeline, write_caption_plan

OVERLAY_FPS = 30


def _cut(start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(cut_start=start, cut_end=end)


def _spans(words: list[TimedWord]) -> list[tuple[str, float, float]]:
    return [(w.word, w.start, w.end) for w in words]


# (a) A word fully inside a removed jump cut is dropped.
def test_word_fully_inside_jump_cut_is_dropped():
    words = [
        TimedWord("BEFORE", 1.0, 1.4),
        TimedWord("INSIDE", 3.2, 3.6),
        TimedWord("AFTER", 5.5, 6.0),
    ]
    out = remap_words_for_jump_cuts(words, [_cut(3.0, 4.0)])
    assert [w.word for w in out] == ["BEFORE", "AFTER"]
    assert out[1].start == pytest.approx(4.5)
    assert out[1].end == pytest.approx(5.0)


# (b) A word straddling a cut boundary is trimmed to its kept part, placed at the right output time.
def test_word_straddling_cut_end_is_trimmed_to_kept_part_before_cut():
    # Word runs from before the cut into it; midpoint is before cut_start, so it is kept and trimmed.
    out = remap_words_for_jump_cuts([TimedWord("EDGE", 2.0, 3.6)], [_cut(3.0, 5.0)])
    assert _spans(out) == [("EDGE", 2.0, 3.0)]


def test_word_straddling_cut_start_is_trimmed_and_shifted_by_full_cut_length():
    # Kept part is [5.0, 5.6] in source. Removed time before it is 2.0 s, so output is [3.0, 3.6].
    out = remap_words_for_jump_cuts([TimedWord("LATE", 4.5, 5.6)], [_cut(3.0, 5.0)])
    assert len(out) == 1
    assert out[0].start == pytest.approx(3.0)
    assert out[0].end == pytest.approx(3.6)


# (c) Shift accumulates across several cuts.
def test_shift_accumulates_across_several_cuts():
    cuts = [_cut(1.0, 2.0), _cut(4.0, 5.0)]
    words = [TimedWord("MID", 2.5, 3.0), TimedWord("LATER", 6.0, 6.5)]
    out = remap_words_for_jump_cuts(words, cuts)
    assert _spans(out) == [("MID", 1.5, 2.0), ("LATER", 4.0, 4.5)]


# (d) Hook trim of 2 s: words before it dropped, words after it shifted by 2 s.
def test_hook_trim_drops_early_words_and_shifts_later_words():
    words = [
        TimedWord("EARLY", 0.5, 1.5),
        TimedWord("STRADDLE", 1.8, 2.4),
        TimedWord("LATE", 3.0, 3.5),
    ]
    out = offset_words_for_hook(words, 2.0)
    assert [w.word for w in out] == ["STRADDLE", "LATE"]
    assert out[0].start == pytest.approx(0.0)
    assert out[0].end == pytest.approx(0.4)
    assert out[1].start == pytest.approx(1.0)
    assert out[1].end == pytest.approx(1.5)


# (e) ASS and Remotion paths produce the same word times for the same inputs.
def test_ass_and_remotion_paths_give_identical_word_times(monkeypatch: pytest.MonkeyPatch):
    words = [
        TimedWord("ONE", 0.5, 1.5),
        TimedWord("TWO", 1.8, 2.4),
        TimedWord("THREE", 2.6, 3.2),
        TimedWord("FOUR", 3.6, 4.2),
        TimedWord("FIVE", 5.5, 6.0),
    ]
    cuts = [_cut(1.0, 1.5), _cut(4.0, 5.0)]

    remotion_words, _ = build_caption_timeline(
        words, hook_in_sec=2.0, jump_cuts=cuts, total_duration_sec=10.0
    )

    captured: dict[str, list[TimedWord]] = {}
    real_chunk_words = ass_styler.chunk_words

    def spy(seq, *args, **kwargs):
        captured["words"] = list(seq)
        return real_chunk_words(seq, *args, **kwargs)

    monkeypatch.setattr(ass_styler, "chunk_words", spy)
    build_ass_script(words, hook_in_sec=2.0, jump_cuts=cuts, total_duration_sec=10.0)

    assert _spans(captured["words"]) == _spans(remotion_words)


# Composition length: frames = ceil(durationSec * fps) must cover the last caption.
def test_composition_frames_cover_last_caption(tmp_path: Path):
    words = [TimedWord("A", 0.5, 1.0), TimedWord("B", 6.0, 7.9)]
    timed, dur = build_caption_timeline(words, jump_cuts=[_cut(3.0, 5.0)], total_duration_sec=10.0)
    assert dur == pytest.approx(8.0)
    plan = write_caption_plan(tmp_path / "plan.json", timed, duration_sec=dur)
    data = json.loads(plan.read_text(encoding="utf-8"))

    frames = math.ceil(data["durationSec"] * data["fps"])
    assert data["fps"] == OVERLAY_FPS
    assert frames == 240
    last_end = max(w["end"] for w in data["words"])
    assert last_end <= data["durationSec"]
    # Last rendered frame time (frames - 1) / fps must reach the last word's end.
    assert (frames - 1) / data["fps"] >= last_end - 1e-9


def test_composition_frames_use_ceil_for_fractional_duration(tmp_path: Path):
    # 2.05 s * 30 fps = 61.5 frames, so the composition needs 62 frames to reach 2.05 s.
    plan = write_caption_plan(tmp_path / "p.json", [], duration_sec=2.05)
    data = json.loads(plan.read_text(encoding="utf-8"))
    assert math.ceil(data["durationSec"] * data["fps"]) == 62


# (f) Jump cuts are stored in source-clip time; the render trims with -ss hook_in first.
def test_jump_cuts_shifted_onto_hook_trimmed_timeline():
    from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
    from autoshorts.media.ass_styler import shift_jump_cuts_for_hook
    from autoshorts.media.compositor import _edl_on_trimmed_timeline

    cuts = [JumpCut(cut_start=1.0, cut_end=1.5), JumpCut(cut_start=4.0, cut_end=5.0)]
    shifted = shift_jump_cuts_for_hook(cuts, 2.0)
    assert [(c.cut_start, c.cut_end) for c in shifted] == [(2.0, 3.0)]

    # Words: the one at clip time 4.2-4.5 sits right after the cut [4,5)? no, inside; 5.5 is after.
    words = [TimedWord("IN", 4.2, 4.6), TimedWord("AFTER", 5.5, 6.0)]
    out, dur = build_caption_timeline(
        words, hook_in_sec=2.0, jump_cuts=cuts, total_duration_sec=10.0
    )
    assert [w.word for w in out] == ["AFTER"]
    assert out[0].start == pytest.approx(5.5 - 2.0 - 1.0)
    assert dur == pytest.approx(9.0)

    edl = EditDecisionList(
        hook_in_sec=2.0,
        hook_out_sec=12.0,
        jump_cuts=cuts,
        zoom_punch_ins=[ZoomPunchIn(timestamp=1.0, duration=0.5), ZoomPunchIn(timestamp=6.0)],
    )
    local = _edl_on_trimmed_timeline(edl, 2.0)
    assert [(c.cut_start, c.cut_end) for c in local.jump_cuts] == [(2.0, 3.0)]
    assert [p.timestamp for p in local.zoom_punch_ins] == [4.0]
    assert _edl_on_trimmed_timeline(edl, 0.0) is edl
