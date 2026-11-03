"""Tests for the longform visual shot planner."""

from __future__ import annotations

import pytest

from autoshorts.longform.shots import Shot, plan_shots, total_coverage


def _dense_words(count: int, step: float = 0.5) -> list[dict]:
    """Words every `step` seconds; every 5th word ends a sentence."""
    words = []
    for i in range(count):
        word = f"w{i}." if (i + 1) % 5 == 0 else f"w{i}"
        start = i * step
        words.append({"word": word, "start": start, "end": start + step * 0.9})
    return words


def test_coverage_equals_duration():
    duration = 37.3
    shots = plan_shots(duration, _dense_words(75))
    assert total_coverage(shots) == pytest.approx(duration)


def test_shots_are_contiguous_and_cover_range():
    duration = 37.3
    shots = plan_shots(duration, _dense_words(75))
    assert shots[0].start == 0.0
    assert shots[-1].end == pytest.approx(duration)
    for prev, nxt in zip(shots, shots[1:], strict=False):
        assert nxt.start == prev.end


def test_no_empty_shot():
    shots = plan_shots(37.3, _dense_words(75))
    assert all(shot.length > 0 for shot in shots)


def test_first_shot_is_wide():
    shots = plan_shots(20.0, _dense_words(40))
    assert shots[0].kind == "wide"


def test_every_third_shot_is_insert():
    shots = plan_shots(37.3, _dense_words(75))
    assert len(shots) >= 3
    assert shots[2].kind == "insert"


def test_empty_words_gives_single_wide_shot():
    shots = plan_shots(12.5, [])
    assert shots == [Shot(0.0, 12.5, "wide")]


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_non_positive_duration_raises(duration):
    with pytest.raises(ValueError):
        plan_shots(duration, _dense_words(10))


def test_shot_lengths_within_bounds_for_dense_transcript():
    shots = plan_shots(37.3, _dense_words(75), min_shot=2.0, max_shot=4.0)
    for shot in shots:
        assert 2.0 <= shot.length <= 4.0 + 1e-9


def test_short_shot_is_montage():
    # Sentence end at 2.2s splits 4.4s into 2.2s + 2.2s; the second is under 2.5s.
    words = [{"word": "A.", "start": 0.0, "end": 2.2}]
    shots = plan_shots(4.4, words, min_shot=2.0, max_shot=4.0)
    assert [s.kind for s in shots] == ["wide", "montage"]
    assert total_coverage(shots) == pytest.approx(4.4)


def _word(text: str, start: float, length: float = 0.3) -> dict:
    return {"word": text, "start": start, "end": start + length}


def test_coverage_exact_and_contiguous_with_triggers_and_beats():
    words = [
        _word("We", 0.0),
        _word("tried", 0.5),
        _word("every", 5.0),
        _word("day.", 5.4),
        _word("It", 12.0),
        _word("was", 12.4),
        _word("lazy", 12.8),
        _word("never", 20.0),
    ]
    beats = [1.1, 2.7, 3.9, 6.3, 9.0, 13.5, 17.2]
    duration = 27.4
    shots = plan_shots(duration, words, beat_times=beats)
    assert total_coverage(shots) == pytest.approx(duration)
    assert shots[0].start == 0.0
    assert shots[-1].end == pytest.approx(duration)
    for prev, nxt in zip(shots, shots[1:], strict=False):
        assert nxt.start == prev.end
    assert all(shot.length > 0 for shot in shots)


def test_montage_run_for_every_day():
    words = [
        _word("We", 0.0),
        _word("did", 0.4),
        _word("this", 0.8),
        _word("every", 5.0),
        _word("day", 5.4),
    ]
    shots = plan_shots(30.0, words)
    run = [s for s in shots if s.start >= 5.0 - 1e-9 and s.end <= 8.6 + 1e-6]
    assert 4 <= len(run) <= 8
    assert run[0].start == pytest.approx(5.0)
    for shot in run:
        assert shot.kind == "montage"
        assert 0.4 <= shot.length <= 0.8


def test_impact_then_ecu_for_lazy():
    words = [_word("It", 0.0), _word("was", 0.4), _word("lazy", 6.0)]
    shots = plan_shots(20.0, words)
    idx = next(i for i, s in enumerate(shots) if s.kind == "impact")
    assert shots[idx].start == pytest.approx(6.0)
    assert shots[idx].length == pytest.approx(0.25)
    assert shots[idx + 1].kind == "ecu"
    assert shots[idx + 1].start == pytest.approx(6.25)


def test_beat_snapping_moves_cut_to_nearest_beat():
    # No sentence ends or pauses, so without beats the cut falls at the 2.5 s target.
    words = [_word("a", 0.0, 0.5)]
    without = plan_shots(10.0, words, min_shot=2.0, max_shot=4.0)
    assert without[0].end == pytest.approx(2.5)
    with_beats = plan_shots(10.0, words, min_shot=2.0, max_shot=4.0, beat_times=[3.1, 7.0])
    assert with_beats[0].end == pytest.approx(3.1)
    assert total_coverage(with_beats) == pytest.approx(10.0)


def test_empty_words_with_beats_gives_single_wide_shot():
    assert plan_shots(8.0, [], beat_times=[1.0, 2.0]) == [Shot(0.0, 8.0, "wide")]
