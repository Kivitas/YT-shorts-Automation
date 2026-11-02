"""Phase 3 clip-boundary tests: word-aligned padding, speech-onset snapping, hook-first selection.

All transcripts are synthetic and built in code; no media is read.
"""

from __future__ import annotations

import inspect

import pytest

from autoshorts.decision import window_slicer as ws
from autoshorts.decision.orchestrator import _heuristic_window_score
from autoshorts.decision.semif_engine import evaluate_heuristic
from autoshorts.decision.window_slicer import (
    HOOK_FIRST_SCORE_BONUS,
    classify_semantic_hook,
    generate_candidate_windows,
    slice_transcript_windows,
)

NON_HOOK_TEXT = "The kitchen had a blue table and some chairs."
HOOK_TEXT = "Why does nobody tell you this secret about sleep?"


def _sentence_with_words(text: str, base: float, word_len: float = 0.8, step: float = 1.0) -> dict:
    """Build one transcript segment whose words are spaced `step` seconds apart from `base`."""
    words = []
    t = base
    for token in text.split():
        words.append({"start": round(t, 3), "end": round(t + word_len, 3), "word": token})
        t += step
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": text,
        "words": words,
    }


def _word_transcript(sentences: int = 15, gap: float = 0.7) -> list[dict]:
    """Sentences of 4 words (speech 3.8 s) separated by `gap` seconds of silence."""
    period = 3.8 + gap
    return [
        _sentence_with_words("Alpha beta gamma delta.", base=k * period) for k in range(sentences)
    ]


def _strip_words(transcript: list[dict]) -> list[dict]:
    return [{k: v for k, v in seg.items() if k != "words"} for seg in transcript]


def _hook_transcript() -> list[dict]:
    """15 contiguous 4-second sentences with no word timings; sentence 3 opens with a hook."""
    segments = []
    for k in range(15):
        text = HOOK_TEXT if k == 3 else NON_HOOK_TEXT
        segments.append({"start": 4.0 * k, "end": 4.0 * k + 4.0, "text": text})
    return segments


def _key(candidates) -> list[tuple[float, float, str]]:
    return [(c.start, c.end, c.text) for c in candidates]


# --- Step 1: word-aligned padding -------------------------------------------------


def test_word_padding_applied_to_window_bounds() -> None:
    transcript = _word_transcript()
    period = 4.5
    expected_starts = {round(k * period - 0.30, 2) for k in range(15)}
    expected_ends = {round(k * period + 3.8 + 0.40, 2) for k in range(14)}
    # The final sentence cannot pad past the transcript end (last spoken word end, 66.8 s).
    expected_ends.add(round(14 * period + 3.8, 2))

    candidates = slice_transcript_windows(transcript, hook_first=False)

    assert candidates
    for cand in candidates:
        assert any(cand.start == pytest.approx(s, abs=1e-6) for s in expected_starts), cand.start
        assert any(cand.end == pytest.approx(e, abs=1e-6) for e in expected_ends), cand.end
        assert 30.0 <= cand.end - cand.start <= 60.5
    # At least one window must actually carry the 0.40 s end pad, so padding is exercised.
    padded_ends = {round(k * period + 4.2, 2) for k in range(14)}
    assert any(cand.end == pytest.approx(e, abs=1e-6) for cand in candidates for e in padded_ends)


def test_word_padding_never_reaches_into_previous_sentence() -> None:
    # Only 0.1 s of silence between sentences: the 0.30 s start pad must clamp to the
    # previous sentence's end instead of reaching back into its audio.
    transcript = _word_transcript(gap=0.1)
    normalized = ws._normalize_segments(transcript)

    # Start pad of sentence 1 would reach 0.30 s before its first word, past sentence 0's end.
    assert ws._padded_start(normalized, 1) == pytest.approx(normalized[0]["end"])
    # End pad of sentence 0 (0.40 s) would cross into sentence 1's first word; it must stop there.
    next_word_start = normalized[1]["words"][0]["start"]
    assert ws._padded_end(normalized, 0, 100.0) == pytest.approx(next_word_start)


def test_word_padding_clamped_to_transcript_bounds() -> None:
    transcript = [_sentence_with_words("Alpha beta gamma delta.", base=0.1)]
    normalized = ws._normalize_segments(transcript)

    assert ws._padded_start(normalized, 0) == 0.0  # 0.1 - 0.3 clamps to 0.0
    # Last spoken word ends at 3.9 s; a 0.40 s end pad would give 4.3 s, clamped to transcript end.
    assert ws._padded_end(normalized, 0, transcript_end=3.9) == pytest.approx(3.9)
    assert ws._padded_end(normalized, 0, transcript_end=100.0) == pytest.approx(4.3)


def test_padding_flag_off_matches_plain_segments() -> None:
    with_words = _word_transcript()
    plain = _strip_words(with_words)

    off_with_words = slice_transcript_windows(with_words, word_boundaries=False, hook_first=False)
    off_plain = slice_transcript_windows(plain, word_boundaries=False, hook_first=False)

    assert _key(off_with_words) == _key(off_plain)
    plain_starts = {seg["start"] for seg in plain}
    plain_ends = {seg["end"] for seg in plain}
    for cand in off_with_words:
        assert cand.start in plain_starts
        assert cand.end in plain_ends


def test_padding_flag_changes_output_when_on() -> None:
    with_words = _word_transcript()
    on = slice_transcript_windows(with_words, hook_first=False)
    off = slice_transcript_windows(with_words, word_boundaries=False, hook_first=False)

    assert _key(on) != _key(off)


def test_no_word_timings_means_no_padding_even_when_flag_on() -> None:
    plain = _strip_words(_word_transcript())
    on = slice_transcript_windows(plain, hook_first=False)
    off = slice_transcript_windows(plain, word_boundaries=False, hook_first=False)

    assert _key(on) == _key(off)


def test_default_flags_are_on() -> None:
    params = inspect.signature(slice_transcript_windows).parameters
    assert params["word_boundaries"].default is True
    assert params["hook_first"].default is True
    assert (
        inspect.signature(generate_candidate_windows).parameters["word_boundaries"].default is True
    )


def test_generate_candidate_windows_passes_flags_through() -> None:
    with_words = _word_transcript()
    via_alias = generate_candidate_windows(with_words, word_boundaries=False, hook_first=False)
    direct = slice_transcript_windows(with_words, word_boundaries=False, hook_first=False)

    assert _key(via_alias) == _key(direct)


# --- Step 3: speech-onset snapping -----------------------------------------------


def test_nominal_start_inside_word_snaps_to_word_onset() -> None:
    words = [{"start": 14.8, "end": 15.4, "word": "x"}, {"start": 16.0, "end": 16.5, "word": "y"}]
    starts = [w["start"] for w in words]

    assert ws._snap_to_word_onset(15.2, words, starts) == pytest.approx(14.8)


def test_nominal_start_in_silence_is_not_moved() -> None:
    words = [{"start": 14.8, "end": 15.4, "word": "x"}, {"start": 16.0, "end": 16.5, "word": "y"}]
    starts = [w["start"] for w in words]

    assert ws._snap_to_word_onset(15.6, words, starts) == pytest.approx(15.6)


def test_onset_snap_limited_to_one_second() -> None:
    words = [{"start": 10.0, "end": 12.5, "word": "long"}]
    starts = [w["start"] for w in words]

    # Inside the word, but the onset is 2.0 s away: no snap.
    assert ws._snap_to_word_onset(12.0, words, starts) == pytest.approx(12.0)


# --- Step 2: hook-first selection --------------------------------------------------


def test_hook_classifier_separates_hook_from_plain_opening() -> None:
    assert classify_semantic_hook(HOOK_TEXT)["is_hook"] is True
    assert classify_semantic_hook(NON_HOOK_TEXT)["is_hook"] is False


def test_hook_bonus_changes_start_ranking() -> None:
    transcript = _hook_transcript()

    # Without the bonus the nominal start at 16.0 s wins (closer to the 15 s stride point).
    off = slice_transcript_windows(transcript, word_boundaries=False, hook_first=False)
    # With the bonus, the hook sentence starting at 12.0 s wins despite being 3 s away.
    on = slice_transcript_windows(transcript, word_boundaries=False, hook_first=True)

    off_starts = {c.start for c in off}
    on_starts = {c.start for c in on}
    assert 16.0 in off_starts
    assert 12.0 not in off_starts
    assert 12.0 in on_starts
    assert 16.0 not in on_starts


def test_hook_bonus_is_a_named_constant() -> None:
    assert ws.HOOK_FIRST_START_BONUS > 0.0
    assert ws.HOOK_FIRST_WINDOW_SECONDS in (3.0, 4.0, 5.0)


def test_heuristic_fallback_applies_hook_bonus() -> None:
    hook_block = f"{HOOK_TEXT} The reason is simple."
    base = evaluate_heuristic(hook_block, 45.0)
    boosted = _heuristic_window_score(hook_block, 45.0)

    assert boosted == pytest.approx(min(1.0, base + HOOK_FIRST_SCORE_BONUS), abs=1e-9)
    assert boosted > base


def test_heuristic_fallback_unchanged_for_non_hook() -> None:
    base = evaluate_heuristic(NON_HOOK_TEXT, 45.0)

    assert _heuristic_window_score(NON_HOOK_TEXT, 45.0) == pytest.approx(base, abs=1e-9)
