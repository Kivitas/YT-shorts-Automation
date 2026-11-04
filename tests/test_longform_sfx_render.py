"""Tests for the narration-aligned SFX track builder in longform render."""

from __future__ import annotations

import wave

from autoshorts.longform.render import (
    _MIRROR_CRACK_WORDS,
    _WHOOSH_WORDS,
    SFX_GAIN,
    build_sfx_track,
)
from autoshorts.longform.tts import SECTION_GAP_SECONDS
from autoshorts.longform.types import Section, SectionAudio


def _section(heading: str, narration: str, speaker: str = "narrator") -> Section:
    return Section(heading=heading, narration=narration, speaker=speaker)


def _audio(section: Section, duration: float, words: list[dict] | None = None) -> SectionAudio:
    return SectionAudio(
        section=section,
        audio_path=None,  # type: ignore[arg-type]  # not read by build_sfx_track
        words=words or [],
        duration=duration,
    )


def test_returns_none_when_no_events_are_needed(tmp_path):
    # Every section gets a punch, so an empty section list is the only true "no events" case.
    assert build_sfx_track([], tmp_path) is None


def test_writes_sfx_wav_spanning_full_timeline(tmp_path):
    sections = [
        _audio(_section("Intro", "Hello there friends"), 2.0),
        _audio(
            _section("The clock", "Time is running out", speaker="skeptic"),
            3.0,
            words=[{"word": "Clock", "start": 0.5, "end": 0.9}],
        ),
    ]

    out = build_sfx_track(sections, tmp_path)

    assert out == tmp_path / "sfx_track.wav"
    assert out.is_file()
    expected_total = 2.0 + SECTION_GAP_SECONDS + 3.0
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 48000
        frames = w.getnframes()
    assert abs(frames - round(expected_total * 48000)) <= 1


def test_sfx_gain_is_low():
    assert SFX_GAIN == 0.35


def test_keyword_tuples_defined():
    assert "mirror" in _MIRROR_CRACK_WORDS
    assert "shatter" in _MIRROR_CRACK_WORDS
    assert "can't" in _MIRROR_CRACK_WORDS
    assert "punch" in _WHOOSH_WORDS
    assert "strike" in _WHOOSH_WORDS
    assert "run" in _WHOOSH_WORDS


def test_schedules_glass_crack_and_whoosh_from_words(tmp_path):
    sections = [
        _audio(
            _section("Look in mirror", "You can't quit now"),
            2.0,
            words=[
                {"word": "Look", "start": 0.1, "end": 0.3},
                {"word": "mirror", "start": 0.5, "end": 0.8},
                {"word": "can't", "start": 1.0, "end": 1.3},
            ],
        ),
        _audio(
            _section("Fight back", "Sprint up the mountain"),
            2.5,
            words=[
                {"word": "Sprint", "start": 0.2, "end": 0.5},
                {"word": "mountain", "start": 0.8, "end": 1.2},
            ],
        ),
    ]

    out = build_sfx_track(sections, tmp_path)
    assert out is not None and out.is_file()

    sfx_files = [f.name for f in (tmp_path / "sfx").glob("*.wav")]
    assert any(f.startswith("glass_crack") for f in sfx_files)
    assert any(f.startswith("whoosh") for f in sfx_files)


def test_schedules_glass_crack_and_whoosh_from_section_text_fallback(tmp_path):
    sections = [
        _audio(_section("Shatter the Excuse", "No excuses today"), 2.0, words=[]),
        _audio(_section("The Fight", "Strike the bag with force"), 2.0, words=[]),
    ]

    out = build_sfx_track(sections, tmp_path)
    assert out is not None and out.is_file()

    sfx_files = [f.name for f in (tmp_path / "sfx").glob("*.wav")]
    assert any(f.startswith("glass_crack") for f in sfx_files)
    assert any(f.startswith("whoosh") for f in sfx_files)
