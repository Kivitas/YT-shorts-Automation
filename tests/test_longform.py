"""Comprehensive tests for the Track 5 Longform Video & Animated Faceless Studio Overhaul."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from autoshorts.longform.drawer import (
    POSES,
    draw_character,
    render_scene_frames,
    scenes_for_topic,
    viseme_at,
)
from autoshorts.longform.engines import (
    draw_comparison_canvas,
    draw_finance_canvas,
    draw_history_canvas,
    draw_motivation_canvas,
    draw_science_canvas,
)
from autoshorts.longform.render import _concat_audio, _synthesize_transition_pulse
from autoshorts.longform.script import _build_prompt, build_script, description_for
from autoshorts.longform.thumbnail import make_thumbnail
from autoshorts.longform.topics import infer_genre, resolve_topic_spec
from autoshorts.longform.tts import NARRATOR_VOICE, SECONDARY_VOICE, voice_for_section
from autoshorts.longform.types import MAX_VIDEO_SECONDS, Section, SectionAudio


def test_max_video_seconds_is_twenty_minutes() -> None:
    assert MAX_VIDEO_SECONDS == 1200.0


@pytest.mark.parametrize(
    ("custom_topic", "expected_genre"),
    [
        ("The Fall of Constantinople", "history"),
        ("Quantum Entanglement vs Relativity", "science"),
        ("Why Stoicism Builds Wealth", "finance"),
        ("Spartans vs Samurai", "comparison"),
        ("Programming Languages Tier List", "comparison"),
        ("Bohr Atomic Model and Molecular Bonds", "science"),
        ("Black Hole Accretion Disks in Astrophysics", "science"),
        ("DNA Double Helix and Neuroplasticity", "science"),
        ("Office Meetings Explained Badly Parody", "parody"),
        ("Dan Koe Dopamine Habit Loop", "motivation"),
    ],
)
def test_custom_open_ended_topics_route_to_genre(custom_topic: str, expected_genre: str) -> None:
    spec = resolve_topic_spec(custom_topic)
    assert spec.genre == expected_genre
    script = build_script(custom_topic, minutes=10.0)
    assert script.genre == expected_genre
    assert len(script.sections) >= 4
    assert 0 < len(script.title) <= 60
    desc = description_for(script)
    assert spec.safety_note in desc


def test_parody_script_alternates_speakers_and_prompt_rules() -> None:
    script = build_script("parody", minutes=3.0)
    speakers = [s.speaker for s in script.sections]
    assert "narrator" in speakers
    assert "skeptic" in speakers
    assert all(s.visual_mode == "parody_duet" for s in script.sections)

    spec = resolve_topic_spec("parody")
    prompt = _build_prompt(spec, 3.0)
    assert "Cold Open Hook" in prompt
    assert "Open-Loop Chapter Chaining" in prompt
    assert "Multi-Character Parody Dialogue" in prompt


def test_viseme_lip_sync_active_during_speech_and_rest_when_silent() -> None:
    words = [
        {"word": "mama", "start": 0.2, "end": 0.6},
        {"word": "open", "start": 0.8, "end": 1.2},
        {"word": "see", "start": 1.4, "end": 1.8},
    ]
    assert viseme_at(words, 0.05) == "REST"
    assert viseme_at(words, 0.70) == "REST"
    assert viseme_at(words, 2.10) == "REST"

    active_shapes = {
        viseme_at(words, 0.22),
        viseme_at(words, 0.35),
        viseme_at(words, 0.82),
        viseme_at(words, 1.42),
    }
    assert "REST" not in active_shapes
    assert len(active_shapes) >= 2


def test_character_mouth_pixels_change_with_viseme() -> None:
    frames = {}
    for v in ("REST", "AI", "O", "E", "MBP"):
        img = Image.new("RGB", (400, 400), (30, 30, 40))
        d = ImageDraw.Draw(img)
        draw_character(
            d,
            POSES["present"],
            (200, 280),
            scale=1.5,
            mood="happy",
            viseme=v,
            pointer_stick=True,
            is_speaking=(v != "REST"),
        )
        frames[v] = img.tobytes()
    assert frames["REST"] != frames["AI"]
    assert frames["AI"] != frames["O"]
    assert frames["O"] != frames["MBP"]


@pytest.mark.parametrize("sub_domain", ["chemistry", "physics", "biology", "astrophysics"])
def test_all_five_genre_engines_render_cleanly(sub_domain: str) -> None:
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_history_canvas(img, d, 1.5, 0, "The Siege of 1453", "1453 AD")
    draw_science_canvas(img, d, 1.5, 0, f"Science {sub_domain}", sub_domain=sub_domain)
    draw_comparison_canvas(img, d, 1.5, 0, "Spartans vs Samurai", sub_domain="vs_battle")
    draw_comparison_canvas(img, d, 1.5, 4, "Tier Verdict", sub_domain="tier_list")
    draw_finance_canvas(img, d, 1.5, 0, "Compounding Curve", "$100K")
    draw_motivation_canvas(img, d, 1.5, 0, "Habit Loop", sub_domain="habit_loop")
    draw_motivation_canvas(img, d, 1.5, 1, "Value Equation", sub_domain="value_equation")
    assert img.size == (1920, 1080)


def test_render_scene_frames_across_new_genres(tmp_path: Path) -> None:
    wav_path = tmp_path / "sec.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)

    words = [{"word": "alpha", "start": 0.0, "end": 0.25}]
    sections = [
        SectionAudio(
            Section("History Map", "alpha", visual_mode="history_map", sub_domain="timeline"),
            wav_path,
            words,
            0.1,
        ),
        SectionAudio(
            Section("Parody Banter", "alpha", visual_mode="parody_duet", speaker="skeptic"),
            wav_path,
            words,
            0.1,
        ),
    ]
    scenes = scenes_for_topic("The Fall of Constantinople")
    frames = list(render_scene_frames(sections, "Custom History", max_seconds_per_section=0.1, scenes=scenes))
    assert len(frames) == 6
    assert all(len(f) == 1920 * 1080 * 3 for f in frames)


def test_consistent_narrator_and_cast_voice_selection() -> None:
    s1 = Section("Intro", "Hello world", speaker="narrator")
    s2 = Section("Next", "More details", speaker="narrator")
    s3 = Section("Counter", "Wait really?", speaker="skeptic")
    assert voice_for_section(s1) == NARRATOR_VOICE
    assert voice_for_section(s2) == NARRATOR_VOICE
    assert voice_for_section(s3) == SECONDARY_VOICE


def test_multi_stem_audio_with_music_bed_ducking(tmp_path: Path) -> None:
    sec_wav = tmp_path / "speech.wav"
    with wave.open(str(sec_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 12000)

    music_wav = _synthesize_transition_pulse(tmp_path / "bed.wav", duration=1.0)
    assert music_wav.is_file() and music_wav.stat().st_size > 0

    sa = SectionAudio(Section("H", "text"), sec_wav, [], 0.5)
    master = _concat_audio([sa], tmp_path, music_bed=music_wav)
    assert master.is_file() and master.stat().st_size > 0


def test_infer_genre_subdomains() -> None:
    assert infer_genre("Bohr Atom and Electrons") == ("science", "chemistry")
    assert infer_genre("Black Hole Orbit in Astrophysics") == ("science", "astrophysics")
    assert infer_genre("DNA Helix in Biology") == ("science", "biology")
    assert infer_genre("Force Vectors in Physics") == ("science", "physics")
    assert infer_genre("Spartans vs Samurai") == ("comparison", "vs_battle")


def test_thumbnail_genres(tmp_path: Path) -> None:
    for g in ("history", "science", "comparison", "finance", "parody", "motivation"):
        out = make_thumbnail(f"{g.title()}: Deep Dive Breakdown", tmp_path / f"{g}.png", genre=g)
        with Image.open(out) as im:
            assert im.size == (1280, 720)
            assert im.format == "PNG"
