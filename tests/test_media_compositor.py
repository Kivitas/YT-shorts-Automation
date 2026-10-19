"""Unit tests for FFmpegCompositor, hardware encoder probing, effects, and protocol compliance."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core.contracts import Compositor, Transcriber
from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.media.compositor import (
    FasterWhisperTranscriber,
    FFmpegCompositor,
    extract_16khz_mono_wav,
)
from autoshorts.media.dopamine_effects import (
    build_complex_filtergraph,
    build_jumpcut_select_filter,
    build_progress_bar_filter,
    build_zoom_punch_filter,
    get_dopamine_preset,
    pick_background_asset,
)
from autoshorts.media.encoder_probe import (
    EncoderInfo,
    probe_encoder,
)

# -----------------------------------------------------------------------------
# 1. Protocol Runtime Check Compliance
# -----------------------------------------------------------------------------


def test_compositor_implements_protocol():
    """Verify that FFmpegCompositor satisfies the Compositor protocol."""
    comp = FFmpegCompositor()
    assert isinstance(comp, Compositor)


def test_transcriber_implements_protocol():
    """Verify that FasterWhisperTranscriber satisfies the Transcriber protocol."""
    transcriber = FasterWhisperTranscriber()
    assert isinstance(transcriber, Transcriber)


def test_faster_whisper_lazy_import_error():
    """Verify that FasterWhisperTranscriber raises clear error when faster_whisper is missing."""
    transcriber = FasterWhisperTranscriber(model_name="tiny.en")
    with (
        patch.dict("sys.modules", {"faster_whisper": None}),
        pytest.raises(RuntimeError, match="faster-whisper is not installed"),
    ):
        transcriber.transcribe(Path("audio.wav"))


# -----------------------------------------------------------------------------
# 2. Hardware Encoder Probe & Fallbacks (Mocked Subprocess)
# -----------------------------------------------------------------------------


def test_probe_encoder_qsv_success():
    """Verify that h264_qsv is selected when the tiny test encode succeeds."""
    with (
        patch("autoshorts.media.encoder_probe.is_ffmpeg_available", return_value=True),
        patch("autoshorts.media.encoder_probe.run_tiny_test_encode", return_value=True),
    ):
        info = probe_encoder(preferred="auto", force_refresh=True)
        assert info.codec == "h264_qsv"
        assert info.is_hardware_accelerated is True
        assert info.verified is True
        assert "-global_quality" in info.extra_args
        assert "-look_ahead" in info.extra_args


def test_probe_encoder_qsv_fails_falls_back_to_libx264():
    """Verify that failure of h264_qsv cleanly falls back to libx264 veryfast."""
    with (
        patch("autoshorts.media.encoder_probe.is_ffmpeg_available", return_value=True),
        patch(
            "autoshorts.media.encoder_probe.run_tiny_test_encode",
            side_effect=[False, True],
        ),
    ):
        info = probe_encoder(preferred="auto", force_refresh=True)
        assert info.codec == "libx264"
        assert info.is_hardware_accelerated is False
        assert info.verified is True
        assert "-preset" in info.extra_args
        assert "veryfast" in info.extra_args


def test_probe_encoder_libx264_forced():
    """Verify that preferred='libx264' bypasses QSV probing entirely."""
    with (
        patch("autoshorts.media.encoder_probe.is_ffmpeg_available", return_value=True),
        patch(
            "autoshorts.media.encoder_probe.run_tiny_test_encode", return_value=True
        ) as mock_encode,
    ):
        info = probe_encoder(preferred="libx264", force_refresh=True)
        assert info.codec == "libx264"
        assert info.verified is True
        assert mock_encode.call_count == 1
        args, _ = mock_encode.call_args
        assert args[0] == "libx264"


def test_probe_encoder_ffmpeg_missing():
    """Verify that missing ffmpeg binary returns default CPU encoder unverified."""
    with patch("autoshorts.media.encoder_probe.is_ffmpeg_available", return_value=False):
        info = probe_encoder(preferred="auto", force_refresh=True)
        assert info.codec == "libx264"
        assert info.verified is False


def test_encoder_info_get_ffmpeg_args():
    """Verify get_ffmpeg_args builds correct command-line flags."""
    info = EncoderInfo(
        codec="libx264",
        extra_args=["-preset", "veryfast", "-crf", "22"],
        is_hardware_accelerated=False,
    )
    args = info.get_ffmpeg_args()
    assert args == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"]


# -----------------------------------------------------------------------------
# 3. Dopamine Presets & Filtergraph Strings
# -----------------------------------------------------------------------------


def test_dopamine_presets():
    """Verify feature flags for low, medium, and high dopamine presets."""
    low = get_dopamine_preset("low")
    med = get_dopamine_preset("medium")
    high = get_dopamine_preset("high")

    assert low.enable_progress_bar is False
    assert low.enable_loudnorm is False

    assert med.enable_progress_bar is True
    assert med.enable_loudnorm is True
    assert med.enable_zoom_punch_ins is False

    assert high.enable_progress_bar is True
    assert high.enable_zoom_punch_ins is True
    assert high.enable_music_ducking is True
    assert high.enable_sfx is True


def test_build_progress_bar_filter():
    """Verify progress bar filter string formatting."""
    bar_str, out_label = build_progress_bar_filter(
        duration_sec=42.0,
        input_label="stacked",
        output_label="with_bar",
        color="red@0.8",
        height=12,
        width=1080,
    )
    assert "color=c=red@0.8:s=1080x12:d=42.00[bar_color]" in bar_str
    assert "overlay=x='-1080+(1080*t/42.00)':y=0[with_bar]" in bar_str
    assert out_label == "with_bar"


def test_build_zoom_punch_filter():
    """Verify zoom punch-in crop expressions for EDL."""
    punches = [
        ZoomPunchIn(timestamp=5.0, scale=1.15, duration=0.8, emphasis_word="INSANE"),
        ZoomPunchIn(timestamp=12.0, scale=1.20, duration=1.0, emphasis_word="NEVER"),
    ]
    crop_str, out_label = build_zoom_punch_filter(
        punches, input_label="top_in", output_label="top_out"
    )
    assert "crop=w='iw/" in crop_str
    assert "between(t,5.00,5.80)" in crop_str
    assert "between(t,12.00,13.00)" in crop_str
    assert "scale=1080:960[top_out]" in crop_str
    assert out_label == "top_out"


def test_build_zoom_punch_filter_smooth_ramp():
    """Verify zoom punch-in expressions include smooth ease-in/ease-out ramps."""
    punches = [
        ZoomPunchIn(timestamp=2.0, scale=1.20, duration=1.0, emphasis_word="BOOM"),
    ]
    crop_str, out_label = build_zoom_punch_filter(
        punches, input_label="v_in", output_label="v_out", ramp_duration_sec=0.15
    )
    # Ramp up: (t - 2.00) / 0.15, Ramp down: (3.00 - t) / 0.15 clamped to 1.0
    assert "min(min((t-2.00)/0.15,(3.00-t)/0.15),1.0)" in crop_str
    assert "between(t,2.00,3.00)" in crop_str
    assert "v_out" == out_label


def test_build_jumpcut_select_filter():
    """Verify jump cut select expressions for dead air removal."""
    cuts = [
        JumpCut(cut_start=2.0, cut_end=3.5, reason="silence"),
        JumpCut(cut_start=10.0, cut_end=11.2, reason="filler"),
    ]
    filter_str, v_out, a_out = build_jumpcut_select_filter(cuts, "v_in", "v_cut", "a_in", "a_cut")
    assert "select='not(between(t,2.00,3.50)+between(t,10.00,11.20))'" in filter_str
    assert "aselect='not(between(t,2.00,3.50)+between(t,10.00,11.20))'" in filter_str
    assert v_out == "v_cut"
    assert a_out == "a_cut"


def test_complex_filtergraph_with_high_dopamine():
    """Verify full complex filtergraph string generation with high dopamine features."""
    edl = EditDecisionList(
        zoom_punch_ins=[
            ZoomPunchIn(timestamp=4.0, scale=1.12, duration=0.6, emphasis_word="TRUTH")
        ],
    )
    preset = get_dopamine_preset("high")

    fg, out_v, out_a = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="work/subs.ass",
        duration_sec=35.0,
        dopamine_preset=preset,
        edl=edl,
        music_input_index=2,
        sfx_input_index=3,
    )

    # Top & bottom scaling
    assert "[0:v]scale=1080:960" in fg
    assert "[1:v]scale=1080:960" in fg
    assert "vstack=inputs=2" in fg
    # Zoom punch
    assert "between(t,4.00,4.60)" in fg
    # Progress bar
    assert "color=c=red@0.8" in fg
    # Subtitles
    assert "subtitles=" in fg
    # Audio loudnorm & ducking & SFX
    assert "loudnorm=I=-14:LRA=7:tp=-1" in fg
    assert "sidechaincompress=" in fg
    assert "alimiter=limit=-1.0dB" in fg
    assert "[3:a]volume=0.4[sfx_audio]" in fg
    assert out_v == "[outv]"
    assert out_a == "[master_a]"


def test_complex_filtergraph_with_jumpcuts():
    """Verify that build_complex_filtergraph wires build_jumpcut_select_filter into audio and video chains before vertical stack."""
    from autoshorts.core.models import JumpCut

    edl = EditDecisionList(
        jump_cuts=[
            JumpCut(cut_start=2.0, cut_end=4.0, reason="Dead air"),
        ],
    )
    preset = get_dopamine_preset("medium")

    fg, out_v, out_a = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="work/subs.ass",
        duration_sec=30.0,
        dopamine_preset=preset,
        edl=edl,
    )

    # Both video select and audio aselect should be in filtergraph before vstack
    assert "select='not(between(t,2.00,4.00))'" in fg
    assert "aselect='not(between(t,2.00,4.00))'" in fg
    assert "[top_cut][bottom]vstack=inputs=2[stacked]" in fg
    assert "[a_cut]loudnorm=I=-14:LRA=7:tp=-1" in fg
    assert out_v == "[outv]"
    assert out_a == "[master_a]"


# -----------------------------------------------------------------------------
# 4. Background Selection & Lavfi Fallback
# -----------------------------------------------------------------------------


def test_pick_background_asset_fallback(tmp_path: Path):
    """Verify pick_background_asset returns None when directory is empty."""
    empty_assets = tmp_path / "assets"
    empty_assets.mkdir(parents=True)
    video_path, offset = pick_background_asset(category="minecraft", assets_dir=empty_assets)
    assert video_path is None
    assert offset == 0.0


# -----------------------------------------------------------------------------
# 5. FFmpegCompositor Execution & Atomic Outputs (Mocked Subprocess)
# -----------------------------------------------------------------------------


def test_compositor_atomic_render(tmp_path: Path):
    """Verify that compose renders atomically to .tmp file then renames."""
    src_video = tmp_path / "source.mp4"
    src_video.write_bytes(b"dummy source video bytes")

    sub_file = tmp_path / "subs.ass"
    sub_file.write_text("[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n", encoding="utf-8")

    out_file = tmp_path / "output_short.mp4"

    comp = FFmpegCompositor()

    def mock_run(cmd, *args, **kwargs):
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"dummy rendered mp4 bytes")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""
        mock_proc.stdout = ""
        return mock_proc

    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=45.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
    ):
        result = comp.compose(
            job_id="job_unit_1",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_file,
            dopamine_level="medium",
        )

    assert result == out_file
    assert out_file.is_file()
    assert out_file.read_bytes() == b"dummy rendered mp4 bytes"


def test_compositor_60s_cap(tmp_path: Path):
    """Verify that final short duration is capped at 60.0 seconds even if source is longer."""
    src_video = tmp_path / "long_source.mp4"
    src_video.write_bytes(b"dummy long source")

    sub_file = tmp_path / "subs.ass"
    sub_file.write_text("[Script Info]\n", encoding="utf-8")

    out_file = tmp_path / "capped.mp4"
    comp = FFmpegCompositor()

    captured_cmd = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"dummy mp4")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=120.0),
    ):
        comp.compose(
            job_id="job_cap",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_file,
        )

    # Assert duration parameter is capped at 60.000
    assert "-t" in captured_cmd
    t_idx = captured_cmd.index("-t")
    duration_val = float(captured_cmd[t_idx + 1])
    assert duration_val == 60.0


def test_compositor_timeout_raises_timeout_error(tmp_path: Path):
    """Verify that FFmpeg execution exceeding timeout raises TimeoutError."""
    src_video = tmp_path / "source.mp4"
    src_video.write_bytes(b"source")
    sub_file = tmp_path / "subs.ass"
    sub_file.write_text("[Script Info]\n", encoding="utf-8")
    out_file = tmp_path / "timeout.mp4"

    comp = FFmpegCompositor()

    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=5.0),
        ),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=30.0),
        pytest.raises(TimeoutError, match="exceeded timeout"),
    ):
        comp.compose(
            job_id="job_timeout",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_file,
        )


def test_generate_preview_atomic(tmp_path: Path):
    """Verify rapid preview generation creates 540x960 15fps output atomically."""
    src_video = tmp_path / "preview_src.mp4"
    src_video.write_bytes(b"source")
    out_preview = tmp_path / "preview.mp4"

    comp = FFmpegCompositor()
    captured_cmd = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"preview mp4")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    with patch("subprocess.run", side_effect=mock_run):
        res = comp.generate_preview(source_clip=src_video, output_path=out_preview)

    assert res == out_preview
    assert out_preview.is_file()
    assert "540:960" in " ".join(captured_cmd)
    assert "fps=15" in " ".join(captured_cmd)
    assert "ultrafast" in captured_cmd


def test_extract_16khz_mono_wav(tmp_path: Path):
    """Verify 16kHz mono WAV extraction command parameters."""
    src_video = tmp_path / "speech.mp4"
    src_video.write_bytes(b"video with speech")
    dest_wav = tmp_path / "audio_16k.wav"

    captured_cmd = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        dest_wav.write_bytes(b"RIFF dummy wav")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    with patch("subprocess.run", side_effect=mock_run):
        res = extract_16khz_mono_wav(src_video, dest_wav, start_sec=2.0, duration_sec=30.0)

    assert res == dest_wav
    cmd_str = " ".join(captured_cmd)
    assert "-ar 16000" in cmd_str
    assert "-ac 1" in cmd_str
    assert "-acodec pcm_s16le" in cmd_str
    assert "-vn" in cmd_str
    assert "-ss 2.000" in cmd_str
    assert "-t 30.000" in cmd_str
