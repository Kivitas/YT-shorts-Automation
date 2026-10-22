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
    build_color_grading_filter,
    build_complex_filtergraph,
    build_jumpcut_select_filter,
    build_progress_bar_filter,
    build_watermark_filter,
    build_zoom_punch_filter,
    get_dopamine_preset,
    pick_background_asset,
)
from autoshorts.media.encoder_probe import (
    EncoderInfo,
    probe_encoder,
)


@pytest.fixture(autouse=True)
def clean_encoder_probe_cache():
    """Ensure mock encoder probe results never leak across tests."""
    from autoshorts.media.compositor import clear_hardware_encoder_cache

    clear_hardware_encoder_cache()
    yield
    clear_hardware_encoder_cache()


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
    """Verify feature flags for none, low, medium, high, ultra, and extreme dopamine presets."""
    none = get_dopamine_preset("none")
    low = get_dopamine_preset("low")
    med = get_dopamine_preset("medium")
    high = get_dopamine_preset("high")
    ultra = get_dopamine_preset("ultra")
    extreme = get_dopamine_preset("extreme")

    assert none.enable_progress_bar is False
    assert none.enable_loudnorm is False
    assert none.enable_zoom_punch_ins is False

    assert low.enable_progress_bar is False
    assert low.enable_loudnorm is False

    assert med.enable_progress_bar is True
    assert med.enable_loudnorm is True
    assert med.enable_zoom_punch_ins is False

    assert high.enable_progress_bar is True
    assert high.enable_zoom_punch_ins is True
    assert high.enable_music_ducking is True
    assert high.enable_sfx is True

    assert ultra.enable_zoom_punch_ins is True
    assert ultra.enable_sfx is True
    assert ultra.enable_music_ducking is True
    assert ultra.enable_split_separator is True

    assert extreme.enable_zoom_punch_ins is True
    assert extreme.enable_sfx is True
    assert extreme.enable_music_ducking is True
    assert extreme.enable_split_separator is True


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
    # Audio voice boost EQ, loudnorm & ducking & event-synced SFX
    assert "highpass=f=85,equalizer=f=3200" in fg
    assert "loudnorm=I=-14:TP=-1.0:LRA=7" in fg
    assert "sidechaincompress=" in fg
    assert "alimiter=limit=-1.0dB" in fg
    assert "[3:a]volume=0.4,adelay=4000|4000[sfx_audio]" in fg
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
    assert (
        "[a_cut]highpass=f=85,equalizer=f=3200:width_type=o:width=1.5:g=3.5,loudnorm=I=-14:TP=-1.0:LRA=7"
        in fg
    )
    assert out_v == "[outv]"
    assert out_a == "[master_a]"


def test_all_five_layout_modes():
    """Verify all 5 layout modes build valid FFmpeg filtergraphs with exact geometries."""
    preset = get_dopamine_preset("medium")

    # 1. split_50_50
    fg_50, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="split_50_50",
    )
    assert "[0:v]scale=1080:960" in fg_50
    assert "[1:v]scale=1080:960" in fg_50
    assert "vstack=inputs=2" in fg_50

    # 2. split_70_30
    fg_70, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="split_70_30",
    )
    assert "[0:v]scale=1080:1344" in fg_70
    assert "[1:v]scale=1080:576" in fg_70
    assert "vstack=inputs=2" in fg_70

    # 3. full_blur_bg (Single source)
    fg_blur, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="full_blur_bg",
    )
    assert "boxblur=40:5" in fg_blur
    assert "scale=1080:608" in fg_blur
    assert "overlay=x=(W-w)/2:y=(H-h)/2" in fg_blur
    assert "[1:v]" not in fg_blur  # Single source!

    # 4. full_center_crop (Single source)
    fg_crop, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="full_center_crop",
    )
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" in fg_crop
    assert "[1:v]" not in fg_crop  # Single source!

    # 5. pip_gameplay
    fg_pip, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="pip_gameplay",
    )
    assert "[0:v]scale=1080:1920" in fg_pip
    assert "[1:v]scale=380:380" in fg_pip
    assert "overlay=x=W-w-48:y=H-h-420" in fg_pip


def test_color_grading_presets():
    """Verify color grading presets return correct filter strings and wire into filtergraph."""
    assert build_color_grading_filter("none") == ""
    assert "eq=saturation=1.28" in build_color_grading_filter("vibrant")
    assert "eq=contrast=1.12" in build_color_grading_filter("cinematic")
    assert "gamma_r=1.08" in build_color_grading_filter("warm")
    assert "unsharp=5:5:1.2" in build_color_grading_filter("crisp")

    # Verify wired in complex filtergraph
    preset = get_dopamine_preset("low")
    fg, _, _ = build_complex_filtergraph(
        source_clip_has_audio=False,
        subtitles_path="subs.ass",
        duration_sec=15.0,
        dopamine_preset=preset,
        color_grading="cinematic",
    )
    assert "[stacked]eq=contrast=1.12" in fg
    assert "[graded]subtitles=" in fg


def test_watermark_drawtext_filter():
    """Verify watermark filter escaping, positions, and opacity."""
    assert build_watermark_filter(None) == ""

    # Escaping special characters: colons, quotes
    wm_str = build_watermark_filter("@Channel:Live's", position="top_right", opacity=0.75)
    assert r"@Channel\:Live\'s" in wm_str
    assert "white@0.75" in wm_str
    assert "x=w-tw-60:y=180" in wm_str

    # Other positions
    wm_left = build_watermark_filter("@Tag", position="top_left")
    assert "x=60:y=180" in wm_left

    wm_br = build_watermark_filter("@Tag", position="bottom_right")
    assert "x=w-tw-160:y=h-th-420" in wm_br

    wm_bc = build_watermark_filter("@Tag", position="bottom_center")
    assert "x=(w-tw)/2:y=h-th-380" in wm_bc

    # Integration in filtergraph
    preset = get_dopamine_preset("low")
    fg, _, _ = build_complex_filtergraph(
        source_clip_has_audio=False,
        subtitles_path="subs.ass",
        duration_sec=15.0,
        dopamine_preset=preset,
        watermark_text="@AutoShorts",
        watermark_position="top_right",
    )
    assert "drawtext=text='@AutoShorts'" in fg
    assert "[watermarked]subtitles=" in fg


def test_progress_bar_positions_all():
    """Verify progress bar positions: top, bottom, seam, and none."""
    # top: y=0
    bar_top, _ = build_progress_bar_filter(30.0, position="top")
    assert ":y=0[with_bar]" in bar_top

    # bottom: y=1908 (1920 - 12)
    bar_bot, _ = build_progress_bar_filter(30.0, position="bottom")
    assert ":y=1908[with_bar]" in bar_bot

    # seam for 50/50: y=954
    bar_seam50, _ = build_progress_bar_filter(30.0, position="seam", layout_mode="split_50_50")
    assert ":y=954[with_bar]" in bar_seam50

    # seam for 70/30: y=1338
    bar_seam70, _ = build_progress_bar_filter(30.0, position="seam", layout_mode="split_70_30")
    assert ":y=1338[with_bar]" in bar_seam70

    # none: passthrough copy
    bar_none, _ = build_progress_bar_filter(30.0, position="none")
    assert "copy[with_bar]" in bar_none


def test_voice_boost_and_audio_mastering():
    """Verify audio mastering with voice boost EQ toggle and custom LUFS."""
    preset = get_dopamine_preset("medium")

    # voice_boost=True (default)
    fg_boost, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=25.0,
        dopamine_preset=preset,
        voice_boost=True,
        target_lufs=-16.0,
        bg_music_volume=0.20,
    )
    assert "highpass=f=85,equalizer=f=3200" in fg_boost
    assert "loudnorm=I=-16:TP=-1.0:LRA=7" in fg_boost
    assert "alimiter=limit=-1.0dB" in fg_boost

    # voice_boost=False
    fg_noboost, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=25.0,
        dopamine_preset=preset,
        voice_boost=False,
    )
    assert "highpass" not in fg_noboost
    assert "equalizer" not in fg_noboost
    assert "loudnorm=I=-14:TP=-1.0:LRA=7" in fg_noboost


def test_event_synced_sfx_adelay():
    """Verify transition SFX is delay-aligned to first zoom punch-in timestamp."""
    edl = EditDecisionList(
        zoom_punch_ins=[ZoomPunchIn(timestamp=2.5, scale=1.15, duration=0.8, emphasis_word="BOOM")]
    )
    preset = get_dopamine_preset("high")

    fg, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=30.0,
        dopamine_preset=preset,
        edl=edl,
        sfx_input_index=3,
    )
    # 2.5s = 2500ms -> adelay=2500|2500
    assert "[3:a]volume=0.4,adelay=2500|2500[sfx_audio]" in fg


def test_compositor_single_source_full_blur_bg(tmp_path: Path):
    """Verify that compose() with layout_mode='full_blur_bg' does not add background input."""
    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"dummy")
    sub_file = tmp_path / "subs.ass"
    sub_file.write_text("[Script Info]\n", encoding="utf-8")
    out_file = tmp_path / "out.mp4"

    comp = FFmpegCompositor()
    captured_cmd = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"rendered")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=30.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
    ):
        comp.compose(
            job_id="job_single_src",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_file,
            layout_mode="full_blur_bg",
            color_grading="vibrant",
            watermark_text="@AutoShorts",
        )

    cmd_str = " ".join(captured_cmd)
    # Background input (e.g. lavfi or background_path) should NOT be present
    assert "color=c=0x16213e" not in cmd_str
    # Filtergraph should contain full_blur_bg elements
    assert "boxblur=40:5" in cmd_str
    assert "eq=saturation=1.28" in cmd_str
    assert "drawtext=text='@AutoShorts'" in cmd_str


# -----------------------------------------------------------------------------
# 4. Background Selection & Lavfi Fallback
# -----------------------------------------------------------------------------


def test_pick_background_asset_fallback(tmp_path: Path):
    """Verify pick_background_asset returns None when directory is empty."""
    empty_assets = tmp_path / "assets"
    empty_assets.mkdir(parents=True)
    video_path, offset = pick_background_asset(
        category="minecraft", assets_dir=empty_assets, auto_fetch=False
    )
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


def test_smart_speaker_crop_focus_modes():
    """Verify crop_focus ('center', 'left', 'right', 'dynamic') in build_complex_filtergraph."""
    preset = get_dopamine_preset("medium")

    fg_center, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="split_50_50",
        crop_focus="center",
    )
    assert "crop=1080:960,setsar=1" in fg_center

    fg_left, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="split_50_50",
        crop_focus="left",
    )
    assert "crop=1080:960:x='(in_w-out_w)*0.22':y='(in_h-out_h)/2'" in fg_left

    fg_right, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="split_70_30",
        crop_focus="right",
    )
    assert "crop=1080:1344:x='(in_w-out_w)*0.78':y='(in_h-out_h)/2'" in fg_right

    fg_dyn, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=preset,
        layout_mode="full_center_crop",
        crop_focus="dynamic",
    )
    assert "crop=1080:1920:x='(in_w-out_w)*(0.5+0.25*sin(t*0.35))':y='(in_h-out_h)/2'" in fg_dyn


def test_compositor_extract_thumbnail_and_short_clip_fallback(tmp_path: Path):
    """Verify FFmpegCompositor.extract_thumbnail extracts at 1.2s and falls back to 0.1s for short clips."""
    video_file = tmp_path / "short.mp4"
    video_file.write_bytes(b"rendered short")
    thumb_out = tmp_path / "thumb.jpg"

    comp = FFmpegCompositor()
    captured_cmds: list[list[str]] = []

    def mock_run(cmd, *args, **kwargs):
        captured_cmds.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"\xff\xd8\xff\xe0JPEG")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    # 1. Normal clip duration (30s) -> extracts at 1.2s
    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=30.0),
    ):
        res = comp.extract_thumbnail(video_file, thumb_out, timestamp_sec=1.2)
    assert res == thumb_out
    assert thumb_out.is_file()
    cmd_str = " ".join(captured_cmds[-1])
    assert "-ss 1.2" in cmd_str
    assert "-frames:v 1" in cmd_str
    assert "-q:v 2" in cmd_str

    # 2. Very short clip (0.8s <= 1.2s) -> falls back to 0.1s
    thumb_short = tmp_path / "thumb_short.jpg"
    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=0.8),
    ):
        res_short = comp.extract_thumbnail(video_file, thumb_short, timestamp_sec=1.2)
    assert res_short == thumb_short
    assert "-ss 0.1" in " ".join(captured_cmds[-1])


# -----------------------------------------------------------------------------
# 10. Intelligent Zoom Optical Center, Split Styling, Momentum & Frame Scoring
# -----------------------------------------------------------------------------


def test_calculate_optical_center_and_zoom_punch_crop():
    """Verify zoom punch-in centers crop on active speaker face based on crop_focus."""
    from autoshorts.core.models import ZoomPunchIn
    from autoshorts.media.dopamine_effects import (
        build_zoom_punch_filter,
        calculate_optical_center,
    )

    x_left, y_left = calculate_optical_center("left")
    assert "0.22" in x_left
    assert "0.40" in y_left

    x_right, y_right = calculate_optical_center("right")
    assert "0.78" in x_right
    assert "0.40" in y_right

    x_center, y_center = calculate_optical_center("center")
    assert x_center == "(iw-ow)/2"
    assert y_center == "(ih-oh)/2"

    punches = [ZoomPunchIn(timestamp=3.0, scale=1.15, duration=0.8, emphasis_word="SPEAKER")]
    crop_left, _ = build_zoom_punch_filter(punches, crop_focus="left")
    assert "x='(iw-ow)*0.22'" in crop_left
    assert "y='(ih-oh)*0.40'" in crop_left

    crop_right, _ = build_zoom_punch_filter(punches, crop_focus="right")
    assert "x='(iw-ow)*0.78'" in crop_right


def test_build_split_separator_filter_glow_and_shadow():
    """Verify split-screen boundary separator styles (glow, shadow, line)."""
    from autoshorts.media.dopamine_effects import build_split_separator_filter

    # 1. Glow style (cyan halo + white core)
    glow_str, glow_out = build_split_separator_filter(
        input_label="stacked", output_label="with_glow", seam_y=960, width=1080, style="glow"
    )
    assert "color=c=0x00E5FF@0.40" in glow_str
    assert "color=c=white@0.90" in glow_str
    assert "with_glow" == glow_out

    # 2. Shadow style
    shadow_str, shadow_out = build_split_separator_filter(
        input_label="stacked", output_label="with_shadow", seam_y=960, width=1080, style="shadow"
    )
    assert "color=c=black@0.45" in shadow_str
    assert "with_shadow" == shadow_out

    # 3. None (pass through)
    none_str, _ = build_split_separator_filter(
        input_label="stacked", output_label="stacked_none", style="none"
    )
    assert "[stacked]copy[stacked_none]" == none_str


def test_build_visual_momentum_filter():
    """Verify micro-transition whip-pan expressions at jump cuts."""
    from autoshorts.core.models import JumpCut
    from autoshorts.media.dopamine_effects import build_visual_momentum_filter

    cuts = [JumpCut(cut_start=4.0, cut_end=4.8, reason="breath")]
    momentum_str, out_label = build_visual_momentum_filter(
        cuts, input_label="v_in", output_label="v_momentum"
    )
    assert "crop=w='iw*0.96':h='ih*0.96'" in momentum_str
    assert "sin(3.14159*(t-" in momentum_str
    assert out_label == "v_momentum"


def test_detect_hardware_encoder_priority_and_fallback():
    """Verify detect_hardware_encoder probes NVENC, VideoToolbox, QSV, and falls back to libx264."""
    from autoshorts.media.compositor import clear_hardware_encoder_cache, detect_hardware_encoder

    clear_hardware_encoder_cache()
    # When all hardware encoders fail, falls back to libx264
    with (
        patch("subprocess.run") as mock_run,
        patch("autoshorts.media.compositor.get_ffmpeg_path", return_value="ffmpeg"),
    ):
        mock_proc = MagicMock()
        mock_proc.returncode = 1  # Hardware fails
        mock_run.return_value = mock_proc

        enc = detect_hardware_encoder(preferred="auto", force_refresh=True)
        assert enc.codec == "libx264"
        assert enc.is_hardware_accelerated is False

    clear_hardware_encoder_cache()
    # When NVENC succeeds, selects h264_nvenc
    with (
        patch("subprocess.run") as mock_run_nvenc,
        patch("autoshorts.media.compositor.get_ffmpeg_path", return_value="ffmpeg"),
    ):
        mock_proc_ok = MagicMock()
        mock_proc_ok.returncode = 0
        mock_run_nvenc.return_value = mock_proc_ok

        enc_nv = detect_hardware_encoder(preferred="h264_nvenc", force_refresh=True)
        assert enc_nv.codec == "h264_nvenc"
        assert enc_nv.is_hardware_accelerated is True

    clear_hardware_encoder_cache()


def test_score_frame_visual_quality_metrics():
    """Verify score_frame_visual_quality calculates sharpness, facial contrast, and hook banner density."""
    from autoshorts.media.compositor import score_frame_visual_quality

    # Synthetic 180x320 PPM with edge patterns
    w, h = 180, 320
    header = f"P6\n{w} {h}\n255\n".encode()
    # High-contrast edge and gradient pattern
    pixels = bytearray()
    for y in range(h):
        for x in range(w):
            val = (x * 17 + y * 23) % 256
            pixels.extend([val, val, val])

    metrics = score_frame_visual_quality(header + bytes(pixels))
    assert 0.0 <= metrics["score"] <= 1.0
    assert metrics["sharpness"] > 0.40
    assert metrics["facial_contrast"] > 0.40


def test_extract_thumbnail_auto_best_frame(tmp_path: Path):
    """Verify extract_thumbnail scans candidate frames in first 2.5s when auto_best_frame=True."""
    video_file = tmp_path / "rendered.mp4"
    video_file.write_bytes(b"mock video")
    thumb_file = tmp_path / "thumb_auto.jpg"

    comp = FFmpegCompositor()

    w, h = 180, 320
    ppm_data = f"P6\n{w} {h}\n255\n".encode() + bytes([(i % 256) for i in range(w * h * 3)])

    captured_cmds: list[list[str]] = []

    def mock_subprocess(cmd, *args, **kwargs):
        captured_cmds.append(list(cmd))
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        # If PPM pipe extraction
        if "image2pipe" in cmd:
            mock_proc.stdout = ppm_data
        else:
            # JPEG file extraction
            Path(cmd[-1]).write_bytes(b"\xff\xd8\xff\xe0JPEG")
        return mock_proc

    with (
        patch("subprocess.run", side_effect=mock_subprocess),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=30.0),
    ):
        res = comp.extract_thumbnail(video_file, thumb_file, auto_best_frame=True, search_window_sec=2.5)

    assert res == thumb_file
    assert thumb_file.is_file()
    # Verified that multiple probe commands were executed before final extraction
    assert len(captured_cmds) >= 3
    final_cmd = " ".join(captured_cmds[-1])
    assert "-frames:v 1" in final_cmd
    assert "-q:v 2" in final_cmd


def test_explicit_bg_music_enables_ducking_on_medium_preset(tmp_path: Path):
    """Verify that passing explicit bg_music_path forces music ducking active

    even on the 'medium' dopamine preset where preset.enable_music_ducking is normally False (CRIT-01).
    """
    medium_preset = get_dopamine_preset("medium")
    assert medium_preset.enable_music_ducking is False

    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"dummy source")
    sub_file = tmp_path / "subs.ass"
    sub_file.write_text("[Script Info]\n", encoding="utf-8")
    bgm_file = tmp_path / "custom_bgm.mp3"
    bgm_file.write_bytes(b"dummy bgm mp3")
    out_file = tmp_path / "output.mp4"

    comp = FFmpegCompositor()
    captured_cmd: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"rendered bytes")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=30.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
    ):
        comp.compose(
            job_id="job_bgm_medium_test",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_file,
            dopamine_level="medium",
            bg_music_path=bgm_file,
            bg_music_volume=0.25,
            rhythm_ducking_release=220,
        )

    cmd_str = " ".join(captured_cmd)
    assert str(bgm_file) in cmd_str
    assert "-stream_loop" in cmd_str

    fg_idx = captured_cmd.index("-filter_complex")
    filtergraph = captured_cmd[fg_idx + 1]

    # Verify sidechain ducking is present in the filtergraph and parameters match
    assert "sidechaincompress=" in filtergraph
    assert "release=220" in filtergraph
    assert "volume=0.25" in filtergraph
    assert "amix=inputs=2:duration=first:dropout_transition=2" in filtergraph


def test_explicit_bgm_path_alias_and_job_ducking(tmp_path: Path):
    """Verify bgm_path alias and job.bgm_path/bgm_volume standardized resolution."""
    from autoshorts.core.models import Job

    src_video = tmp_path / "src2.mp4"
    src_video.write_bytes(b"dummy source")
    sub_file = tmp_path / "subs2.ass"
    sub_file.write_text("[Script Info]\n", encoding="utf-8")
    bgm_file = tmp_path / "job_bgm.mp3"
    bgm_file.write_bytes(b"dummy bgm mp3")
    out_file = tmp_path / "output2.mp4"

    comp = FFmpegCompositor()
    captured_cmd: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"rendered bytes")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    job = Job(
        id="job_bgm_alias",
        source_url="https://youtube.com/watch?v=123",
        bgm_path=str(bgm_file),
        bgm_volume=0.33,
    )

    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=30.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
    ):
        comp.compose(
            job_id=job.id,
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_file,
            dopamine_level="medium",
            job=job,
        )

    fg_idx = captured_cmd.index("-filter_complex")
    filtergraph = captured_cmd[fg_idx + 1]
    assert str(bgm_file) in " ".join(captured_cmd)
    assert "sidechaincompress=" in filtergraph
    assert "volume=0.33" in filtergraph


def test_compose_source_clip_has_video_false(tmp_path: Path):
    """Verify compose() succeeds when source_clip_has_video=False without 'matches no streams' error."""
    src_audio = tmp_path / "audio_only_source.mp3"
    src_audio.write_bytes(b"dummy audio content")

    sub_file = tmp_path / "subs.ass"
    sub_file.write_text("[Script Info]\n", encoding="utf-8")

    bg_video = tmp_path / "gameplay_bg.mp4"
    bg_video.write_bytes(b"dummy bg video content")

    out_file = tmp_path / "output_audio_only.mp4"

    comp = FFmpegCompositor()
    captured_cmd: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"dummy output mp4")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=25.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
        patch(
            "autoshorts.media.compositor.has_video_stream",
            side_effect=lambda p: str(p) == str(bg_video.resolve()),
        ),
    ):
        result = comp.compose(
            job_id="job_audio_only_source",
            source_clip=src_audio,
            edl=None,
            background_path=bg_video,
            subtitles_path=sub_file,
            output_path=out_file,
            layout_mode="split_50_50",
            dopamine_level="high",
        )

    assert result == out_file
    assert out_file.is_file()

    # Verify filtergraph does NOT reference [0:v] and scales [1:v] as full canvas
    fg_idx = captured_cmd.index("-filter_complex")
    filtergraph = captured_cmd[fg_idx + 1]
    assert "[0:v]" not in filtergraph
    assert (
        "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30[bg_full]"
        in filtergraph
    )
    assert "[bg_full]" in filtergraph
    assert "subtitles=" in filtergraph
    assert "[master_a]" in captured_cmd


def test_split_screen_separator_line_rendering():
    """Verify visual separator divider line rendering for split_50_50 and pip_gameplay."""
    from autoshorts.media.dopamine_effects import build_complex_filtergraph, get_dopamine_preset

    # 1. split_50_50 with drawbox neon separator
    fg_drawbox, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=get_dopamine_preset("medium"),
        layout_mode="split_50_50",
        split_separator="drawbox",
    )
    assert "drawbox=x=0:y=958:w=iw:h=4:color=0x00E5FF@0.9:t=fill[stacked_sep]" in fg_drawbox

    # 2. split_50_50 with dark border separator
    fg_dark, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=get_dopamine_preset("medium"),
        layout_mode="split_50_50",
        split_separator="dark",
    )
    assert "drawbox=x=0:y=958:w=iw:h=4:color=black@0.85:t=fill[stacked_sep]" in fg_dark

    # 3. pip_gameplay with border separator divider
    fg_pip_sep, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=get_dopamine_preset("high"),
        layout_mode="pip_gameplay",
        split_separator="drawbox",
    )
    assert (
        "[pip_scaled]drawbox=x=0:y=0:w=iw:h=ih:color=0x00E5FF@0.9:t=4[pip_bordered]"
        in fg_pip_sep
    )
    assert "[pip_bordered]overlay=" in fg_pip_sep

    # 4. Ultra preset automatically enables split separator
    ultra = get_dopamine_preset("ultra")
    fg_ultra, _, _ = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path="subs.ass",
        duration_sec=20.0,
        dopamine_preset=ultra,
        layout_mode="split_50_50",
        split_separator="none",  # Defaults to preset style 'glow'
    )
    assert "[sep_halo]overlay=" in fg_ultra
    assert "[sep_core]overlay=" in fg_ultra


def test_preset_rapid_subtitle_integration(tmp_path: Path):
    """Verify rapid subtitle preset generation and its burn-in integration in FFmpeg compositor."""
    from autoshorts.media.ass_styler import TimedWord, build_ass_script

    words = [
        TimedWord(word="UNBELIEVABLE", start=0.5, end=1.0),
        TimedWord(word="SPEED", start=1.0, end=1.5),
        TimedWord(word="BOOM", start=1.5, end=2.0),
    ]
    # Build rapid subtitle script
    ass_content = build_ass_script(words, style_preset="rapid")
    assert "Montserrat ExtraBold,84" in ass_content
    assert "&H0000FFFF" in ass_content  # Active Electric Yellow

    subs_file = tmp_path / "rapid_subs.ass"
    subs_file.write_text(ass_content, encoding="utf-8")

    src_video = tmp_path / "src.mp4"
    src_video.write_bytes(b"dummy source")
    out_video = tmp_path / "out_rapid.mp4"

    comp = FFmpegCompositor()
    captured_cmd: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal captured_cmd
        captured_cmd = list(cmd)
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"rendered rapid short")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=20.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
        patch("autoshorts.media.compositor.has_video_stream", return_value=True),
    ):
        result = comp.compose(
            job_id="job_rapid_subs",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=subs_file,
            output_path=out_video,
            dopamine_level="high",
        )

    assert result == out_video
    assert out_video.is_file()
    cmd_str = " ".join(captured_cmd)
    assert "subtitles=" in cmd_str
    assert "rapid_subs.ass" in cmd_str


def test_hardware_encoder_format_fallback(tmp_path: Path):
    """Verify pixel format detection fallback (yuv420p -> nv12) and runtime encoder CPU fallback."""
    from autoshorts.media.compositor import detect_encoder_pixel_format
    from autoshorts.media.encoder_probe import EncoderInfo

    # 1. detect_encoder_pixel_format: when yuv420p fails but nv12 succeeds -> returns "nv12"
    def mock_pix_fmt_run(cmd, *args, **kwargs):
        mock_proc = MagicMock()
        # Fail if yuv420p is requested, succeed if nv12 is requested
        if "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p":
            mock_proc.returncode = 1
        else:
            mock_proc.returncode = 0
        return mock_proc

    with patch("subprocess.run", side_effect=mock_pix_fmt_run):
        detected_fmt = detect_encoder_pixel_format("h264_qsv", extra_args=["-look_ahead", "0"])
        assert detected_fmt == "nv12"

    # 2. compose() uses detected nv12 pixel format for hardware encoder
    src_video = tmp_path / "src_hw.mp4"
    src_video.write_bytes(b"dummy source")
    sub_file = tmp_path / "subs_hw.ass"
    sub_file.write_text("[Script Info]\n", encoding="utf-8")
    out_video = tmp_path / "out_hw.mp4"

    qsv_encoder = EncoderInfo(
        codec="h264_qsv",
        extra_args=["-global_quality", "25"],
        is_hardware_accelerated=True,
        verified=True,
    )

    captured_cmds: list[list[str]] = []

    def mock_hw_run(cmd, *args, **kwargs):
        captured_cmds.append(list(cmd))
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"rendered hw short")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        return mock_proc

    comp = FFmpegCompositor()
    with (
        patch.object(comp, "_get_encoder", return_value=qsv_encoder),
        patch("autoshorts.media.compositor.detect_encoder_pixel_format", return_value="nv12"),
        patch("subprocess.run", side_effect=mock_hw_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=20.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
        patch("autoshorts.media.compositor.has_video_stream", return_value=True),
    ):
        res = comp.compose(
            job_id="job_qsv_nv12",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_video,
        )

    assert res == out_video
    final_cmd = captured_cmds[-1]
    assert "-c:v" in final_cmd
    assert "h264_qsv" in final_cmd
    assert "-pix_fmt" in final_cmd
    assert final_cmd[final_cmd.index("-pix_fmt") + 1] == "nv12"

    # 3. compose() retries with CPU fallback when hardware encoder fails at runtime
    out_video_fallback = tmp_path / "out_hw_fallback.mp4"
    calls_count = 0

    called_cmds = []

    def mock_hw_failure_then_cpu_success(cmd, *args, **kwargs):
        nonlocal calls_count
        calls_count += 1
        called_cmds.append(list(cmd))
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        if "h264_qsv" in cmd:
            mock_proc.returncode = -1  # Hardware abort
            mock_proc.stderr = "QSV device init failed"
            return mock_proc
        # Fallback CPU command
        target_tmp = cmd[-1]
        Path(target_tmp).write_bytes(b"rendered cpu short")
        mock_proc.returncode = 0
        return mock_proc

    with (
        patch.object(comp, "_get_encoder", return_value=qsv_encoder),
        patch("autoshorts.media.compositor.detect_encoder_pixel_format", return_value="yuv420p"),
        patch("subprocess.run", side_effect=mock_hw_failure_then_cpu_success),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=20.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
        patch("autoshorts.media.compositor.has_video_stream", return_value=True),
    ):
        res_fb = comp.compose(
            job_id="job_hw_fail",
            source_clip=src_video,
            edl=None,
            background_path=None,
            subtitles_path=sub_file,
            output_path=out_video_fallback,
        )

    assert res_fb == out_video_fallback
    ffmpeg_calls = [c for c in called_cmds if "ffmpeg" in str(c[0]).lower()]
    assert len(ffmpeg_calls) == 2
    assert "h264_qsv" in ffmpeg_calls[0]
    assert "libx264" in ffmpeg_calls[1]


