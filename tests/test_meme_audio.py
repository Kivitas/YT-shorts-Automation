"""Unit tests for the Meme Short Audio, Mood Matching & SFX Engine."""

import sys
import wave
from pathlib import Path

# Add src to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autoshorts.meme.audio import (
    SAMPLE_RATE,
    generate_meme_sfx_library,
    resolve_music_mood,
    resolve_sfx_cue,
    synth_procedural_bgm,
    synthesize_meme_audio,
)
from autoshorts.meme.types import MemeScript, MemeSegment, MemeSegmentAudio


def _check_wav(path: Path) -> float:
    assert path.is_file(), f"File {path} does not exist"
    assert path.stat().st_size > 44, f"File {path} too small"
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        duration = w.getnframes() / float(w.getframerate())
        assert duration > 0.05
        return duration


def test_sfx_synthesis(tmp_path: Path):
    out_dir = tmp_path / "sfx"
    lib = generate_meme_sfx_library(out_dir)

    expected_keys = [
        "record_scratch",
        "bass_drop",
        "cartoon_impact",
        "whoosh",
        "rimshot",
        "dramatic_stinger",
        "awkward_tick",
    ]
    for key in expected_keys:
        assert key in lib, f"Missing key {key}"
        dur = _check_wav(lib[key])
        assert dur > 0.1, f"Duration for {key} is too short ({dur}s)"


def test_procedural_bgm(tmp_path: Path):
    bgm_dir = tmp_path / "bgm"
    moods = [
        "playful",
        "quirky",
        "epic_orchestral",
        "suspense",
        "punchline_cut",
        "unexpected_reveal",
        "lofi_chill",
    ]
    for mood in moods:
        p = synth_procedural_bgm(mood, duration=2.5, path=bgm_dir / f"{mood}.wav")
        dur = _check_wav(p)
        assert abs(dur - 2.5) < 0.15, f"Expected ~2.5s for {mood}, got {dur}"


def test_mood_and_cue_resolution():
    seg_epic = MemeSegment.from_dict(
        {
            "id": "1",
            "name": "Chad",
            "heading": "CHAD",
            "narration": "He never misses a day at the gym. Sigma mentality.",
            "character_description": "Gigachad pose",
            "expression": "smug",
        }
    )
    assert resolve_music_mood(seg_epic) == "epic_orchestral"
    assert resolve_sfx_cue(seg_epic) == "bass_drop"

    seg_cringe = MemeSegment.from_dict(
        {
            "id": "2",
            "name": "Awkward Guy",
            "heading": "THE AWKWARD MOMENT",
            "narration": "Said you too to the ticket master. Total silence.",
            "character_description": "Nervous sweat",
            "expression": "nervous",
        }
    )
    assert resolve_music_mood(seg_cringe) == "quirky"
    assert resolve_sfx_cue(seg_cringe) in ("cartoon_impact", "awkward_tick")

    seg_disaster = MemeSegment.from_dict(
        {
            "id": "3",
            "name": "Chaos",
            "heading": "DISASTER",
            "narration": "Everything caught fire and exploded.",
            "expression": "panicked",
        }
    )
    assert resolve_music_mood(seg_disaster) == "suspense"
    assert resolve_sfx_cue(seg_disaster) == "dramatic_stinger"

    seg_punchline = MemeSegment.from_dict(
        {
            "id": "4",
            "name": "Punchline",
            "heading": "WAIT WHAT",
            "narration": "Turns out he was at the wrong house the whole time.",
            "music_mood": "punchline_cut",
        }
    )
    assert resolve_music_mood(seg_punchline) == "punchline_cut"
    assert resolve_sfx_cue(seg_punchline) in ("rimshot", "record_scratch")


def test_synthesize_meme_audio_end_to_end(tmp_path: Path):
    script = MemeScript(
        mode="country_meme",
        category="countries",
        topic="Morning Routines",
        title="Country Morning Routines",
        hook="How every country wakes up in the morning!",
        intro="Let's check out each morning ritual.",
        segments=[
            MemeSegment.from_dict(
                {
                    "id": "japan",
                    "name": "Japan",
                    "heading": "JAPAN",
                    "narration": "Wakes up at 5 AM sharp and drinks matcha.",
                    "expression": "smug",
                    "music_mood": "playful",
                    "sfx_cue": "cartoon_impact",
                }
            ),
            MemeSegment.from_dict(
                {
                    "id": "usa",
                    "name": "USA",
                    "heading": "USA",
                    "narration": "Three energy drinks and an eagle screech.",
                    "expression": "smug",
                    "music_mood": "epic_orchestral",
                    "sfx_cue": "bass_drop",
                }
            ),
            MemeSegment.from_dict(
                {
                    "id": "balkans",
                    "name": "Balkans",
                    "heading": "BALKANS",
                    "narration": "Five cups of Turkish coffee and pure existential dread.",
                    "expression": "deadpan",
                    "music_mood": "punchline_cut",
                    "sfx_cue": "record_scratch",
                }
            ),
        ],
        ending="Which country is most accurate for you?",
    )

    work_dir = tmp_path / "meme_audio_run"
    master_wav, seg_audios = synthesize_meme_audio(script, work_dir)

    assert master_wav.is_file()
    assert master_wav.stat().st_size > 1000
    # Includes intro (hook+intro), 3 main segments, and ending CTA
    assert len(seg_audios) == len(script.segments) + 2

    # Check timing sequence
    prev_end = 0.0
    for sa in seg_audios:
        assert isinstance(sa, MemeSegmentAudio)
        assert sa.audio_path.is_file()
        assert sa.start_time >= prev_end - 0.01
        assert sa.duration > 0.5
        assert sa.sfx_time >= sa.start_time
        prev_end = sa.start_time + sa.duration


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        print("Testing SFX...")
        test_sfx_synthesis(p)
        print("Testing procedural BGM...")
        test_procedural_bgm(p)
        print("Testing mood and cue resolution...")
        test_mood_and_cue_resolution()
        print("Testing end-to-end synthesize_meme_audio...")
        test_synthesize_meme_audio_end_to_end(p)
        print("All tests passed successfully!")
