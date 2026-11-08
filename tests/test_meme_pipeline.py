"""Unit tests for Meme Pipeline, Quality Control (QC), and Publishing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from autoshorts.meme.pipeline import render_meme
from autoshorts.meme.publish import (
    meme_remaining_today,
    meme_uploads_today,
    publish_meme_video,
)
from autoshorts.meme.qc import MemeQC
from autoshorts.meme.types import MemeConfig, MemeResult, MemeScript, MemeSegment


def test_qc_script_checks() -> None:
    # 1. Valid script
    seg = MemeSegment(
        id="usa",
        name="USA",
        heading="USA",
        narration="Driving 4 miles takes 2 hours.",
        scenario="Traffic gridlock",
        character_description="Frustrated commuter",
        pose="shocked_facepalm",
        expression="panicked",
        background_theme="street_city",
        animation_type="shake",
        music_mood="suspense",
        sfx_cue="cartoon_impact",
        subtitle_text="Traffic jam again!",
        speech_bubble_text="WHY ME?!",
    )
    seg2 = MemeSegment(
        id="germany",
        name="Germany",
        heading="Germany",
        narration="Cruising at 250 km/h on Autobahn.",
        scenario="Speeding legally",
        character_description="Smug driver",
        pose="smug_crossed_arms",
        expression="smug",
        background_theme="street_city",
        animation_type="zoom_in",
        music_mood="playful",
        sfx_cue="whoosh",
        subtitle_text="Autobahn speed!",
        speech_bubble_text="SCHNELL!!",
    )
    seg3 = MemeSegment(
        id="italy",
        name="Italy",
        heading="Italy",
        narration="Vespa zig-zags between buses.",
        scenario="Chaotic scooter driving",
        character_description="Cool rider",
        pose="dramatic_heroic",
        expression="sparkling_eyes",
        background_theme="street_city",
        animation_type="pan_left",
        music_mood="quirky",
        sfx_cue="rimshot",
        subtitle_text="Mamma Mia!",
        speech_bubble_text="ANDIAMO!",
    )

    valid_script = MemeScript(
        mode="country_meme",
        category="countries",
        topic="Every Country's Driving Culture",
        title="Every Country's Driving Culture Be Like",
        hook="Which driver are you?",
        intro="Let's look at roads around the world!",
        segments=[seg, seg2, seg3],
        ending="Which country has the wildest traffic?",
        hashtags=["#CountryMemes", "#DrivingMemes", "#Shorts"],
        estimated_duration=35.0,
    )

    qc_res = MemeQC.check_script(valid_script)
    assert qc_res.passed is True
    assert len(qc_res.issues) == 0

    # 2. Defective script (missing title, no hook)
    bad_script = MemeScript(
        mode="country_meme",
        category="countries",
        topic="",
        title="",
        hook="",
        intro="",
        segments=[],
        ending="",
        hashtags=[],
        estimated_duration=0.0,
    )
    bad_qc = MemeQC.check_script(bad_script)
    assert bad_qc.passed is False
    assert len(bad_qc.issues) >= 3


def test_qc_audio_sync() -> None:
    # Overlapping audio check
    class MockAudioSeg:
        def __init__(self, start: float, dur: float, words: list) -> None:
            self.start_time = start
            self.duration = dur
            self.words = words

    segs = [
        MockAudioSeg(0.0, 3.0, [{"word": "hello", "start": 0.0, "end": 1.0}]),
        MockAudioSeg(3.2, 3.0, [{"word": "world", "start": 0.0, "end": 1.0}]),
    ]
    res = MemeQC.check_audio_sync(segs)
    assert res.passed is True

    # Overlapping segment
    bad_segs = [
        MockAudioSeg(0.0, 4.0, []),
        MockAudioSeg(2.0, 3.0, []),
    ]
    bad_res = MemeQC.check_audio_sync(bad_segs)
    assert bad_res.passed is False
    assert any("overlaps" in iss for iss in bad_res.issues)


def test_meme_publish_and_quotas(tmp_path: Path) -> None:
    # Test quota functions
    today_count = meme_uploads_today()
    rem = meme_remaining_today(cap=50)
    assert isinstance(today_count, int)
    assert rem <= 50

    # Test publish fallback
    dummy_video = tmp_path / "dummy.mp4"
    dummy_video.write_bytes(b"dummy video bytes" * 100)
    dummy_thumb = tmp_path / "dummy.png"
    dummy_thumb.write_bytes(b"dummy png bytes" * 100)

    res = MemeResult(
        script=MemeScript(
            mode="country_meme",
            category="countries",
            topic="Test Topic",
            title="Test Title",
            hook="Hook",
            intro="Intro",
            segments=[],
            ending="Ending",
            hashtags=["#CountryMemes"],
            estimated_duration=10.0,
        ),
        video_path=dummy_video,
        thumbnail_path=dummy_thumb,
        title="Test Title",
        description="Test Desc",
        hashtags=["#CountryMemes"],
        duration=10.0,
        aspect_ratio="9:16",
        qc_passed=True,
    )

    out = publish_meme_video(res)
    assert out is not None


@patch("autoshorts.meme.pipeline.stream_all_meme_frames")
@patch("autoshorts.meme.pipeline.synthesize_meme_audio")
@patch("autoshorts.meme.pipeline.subprocess.Popen")
def test_render_meme_pipeline_execution(
    mock_popen: MagicMock,
    mock_synth_audio: MagicMock,
    mock_stream_frames: MagicMock,
    tmp_path: Path,
) -> None:
    # Mock audio synthesis
    dummy_audio = tmp_path / "master.wav"
    dummy_audio.write_bytes(b"RIFF" + b"\x00" * 2000)

    class MockAudioSeg:
        duration = 5.0
        start_time = 0.0
        words = [{"word": "test", "start": 0.0, "end": 1.0}]

    mock_synth_audio.return_value = (dummy_audio, [MockAudioSeg()])

    # Mock frame stream
    mock_stream_frames.return_value = [b"\x00" * 1000]

    # Mock FFmpeg Popen process
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = MagicMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_popen.return_value = mock_proc

    config = MemeConfig(
        mode="country_meme",
        category="countries",
        topic="Every Country's Public Transport",
        segment_count=5,
        aspect_ratio="9:16",
        visual_style="anime_color",
        publish=False,
    )

    # Pre-create the expected output video so QC passes
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    result = render_meme(
        config=config,
        work_dir=tmp_path / "work",
        out_dir=out_dir,
        cleanup_frames=False,
        fps=15,
    )

    assert isinstance(result, MemeResult)
    assert result.title is not None
    assert len(result.hashtags) >= 2
    assert result.aspect_ratio == "9:16"
