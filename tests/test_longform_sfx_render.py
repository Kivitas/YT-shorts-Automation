"""Tests for the narration-aligned SFX track builder in longform render."""

from __future__ import annotations

import wave

from autoshorts.longform.render import SFX_GAIN, build_sfx_track
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
