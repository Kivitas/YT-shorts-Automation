"""Synthetic integration tests for FFmpeg 1080x1920 Short compositing using lavfi testsrc and sine.

Skips gracefully with an informative message if FFmpeg/ffprobe is not found on PATH.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from autoshorts.media.ass_styler import write_ass_file
from autoshorts.media.compositor import FFmpegCompositor
from autoshorts.media.encoder_probe import is_ffmpeg_available, is_ffprobe_available


def test_synthetic_ffmpeg_skip_or_render(tmp_path: Path):
    """Synthetic integration test rendering a 1080x1920 Short with audio.

    If FFmpeg is not installed, skips cleanly with an informative message.
    If FFmpeg is installed, synthesizes a test clip using lavfi testsrc & sine,
    composites with procedural lavfi background, burns subtitles, and asserts:
    - 1080x1920 resolution
    - Video stream is present
    - Audio stream is present
    """
    if not is_ffmpeg_available():
        pytest.skip(
            "FFmpeg executable not found on system PATH ('ffmpeg -version' failed). "
            "Skipping synthetic integration test. To run, install FFmpeg via winget install Gyan.FFmpeg."
        )

    if not is_ffprobe_available():
        pytest.skip(
            "FFprobe executable not found on system PATH ('ffprobe -version' failed). "
            "Skipping synthetic integration test. To run, install FFmpeg/ffprobe."
        )

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    assert ffmpeg_bin is not None
    assert ffprobe_bin is not None

    # 1. Generate synthetic source clip: 3 seconds of 1280x720 video with 1000Hz sine wave audio
    raw_source = tmp_path / "synthetic_raw.mp4"
    gen_cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1280x720:rate=30:duration=3.0",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:duration=3.0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-shortest",
        str(raw_source),
    ]
    proc = subprocess.run(gen_cmd, capture_output=True, check=False)
    assert proc.returncode == 0, f"Failed to synthesize raw clip: {proc.stderr}"
    assert raw_source.is_file()

    # 2. Generate subtitles
    sub_file = tmp_path / "synthetic_subs.ass"
    words = [
        {"word": "Welcome", "start": 0.2, "end": 0.8},
        {"word": "to", "start": 0.85, "end": 1.1},
        {"word": "AutoShorts!", "start": 1.15, "end": 2.2},
    ]
    write_ass_file(sub_file, words, hook_title="SYNTHETIC TEST")
    assert sub_file.is_file()

    # 3. Composite 1080x1920 Short with procedural lavfi background
    master_short = tmp_path / "synthetic_master_short.mp4"
    compositor = FFmpegCompositor(ffmpeg_binary=ffmpeg_bin)

    rendered = compositor.compose(
        job_id="job_synth_001",
        source_clip=raw_source,
        edl=None,
        background_path=None,  # triggers procedural lavfi background fallback
        subtitles_path=sub_file,
        output_path=master_short,
        dopamine_level="medium",
    )

    assert rendered == master_short
    assert master_short.is_file()
    assert master_short.stat().st_size > 0

    # 4. Probe master Short with ffprobe to verify 1080x1920 and audio presence
    probe_cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_streams",
        "-of",
        "json",
        str(master_short),
    ]
    probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
    assert probe_proc.returncode == 0
    probe_data = json.loads(probe_proc.stdout)

    video_streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"]
    audio_streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]

    assert len(video_streams) >= 1, "Expected at least one video stream"
    assert len(audio_streams) >= 1, "Expected at least one audio stream"

    v_stream = video_streams[0]
    assert int(v_stream["width"]) == 1080
    assert int(v_stream["height"]) == 1920
