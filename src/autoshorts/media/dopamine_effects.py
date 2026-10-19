"""High-Dopamine filtergraph builder and effects engine for 9:16 vertical Shorts.

Implements:
- 9:16 vertical split-screen (1080x960 top speaker + 1080x960 bottom gameplay)
- Top dynamic progress bar overlay
- Zoom punch-ins synchronized with emphasis words from EDL
- Jump cuts excising dead air/silence from EDL
- Audio normalization (loudnorm=I=-14:LRA=7:tp=-1)
- Ducked background music and transition SFX
- User-supplied background asset picker with random temporal offsets
- Procedural lavfi fallback background when user assets are empty
- Dopamine presets: low, medium, high
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.core.paths import escape_ffmpeg_filter_path, get_assets_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DopaminePreset:
    """Feature toggles corresponding to low, medium, or high dopamine presets."""

    level: str
    enable_progress_bar: bool
    enable_zoom_punch_ins: bool
    enable_loudnorm: bool
    enable_music_ducking: bool
    enable_sfx: bool
    enable_hook_card: bool


DOPAMINE_PRESETS: dict[str, DopaminePreset] = {
    "low": DopaminePreset(
        level="low",
        enable_progress_bar=False,
        enable_zoom_punch_ins=False,
        enable_loudnorm=False,
        enable_music_ducking=False,
        enable_sfx=False,
        enable_hook_card=False,
    ),
    "medium": DopaminePreset(
        level="medium",
        enable_progress_bar=True,
        enable_zoom_punch_ins=False,
        enable_loudnorm=True,
        enable_music_ducking=False,
        enable_sfx=False,
        enable_hook_card=True,
    ),
    "high": DopaminePreset(
        level="high",
        enable_progress_bar=True,
        enable_zoom_punch_ins=True,
        enable_loudnorm=True,
        enable_music_ducking=True,
        enable_sfx=True,
        enable_hook_card=True,
    ),
}


def get_dopamine_preset(level: str = "medium") -> DopaminePreset:
    """Retrieve the DopaminePreset for the specified level ('low', 'medium', 'high')."""
    normalized = (level or "medium").strip().lower()
    return DOPAMINE_PRESETS.get(normalized, DOPAMINE_PRESETS["medium"])


def build_zoom_punch_filter(
    zoom_punch_ins: Sequence[ZoomPunchIn],
    input_label: str = "top_scaled",
    output_label: str = "top",
    ramp_duration_sec: float = 0.15,
) -> tuple[str, str]:
    """Generate dynamic zoom punch-in crop expressions for the top video stream.

    Applies a centered crop (scaled back to 1080x960) with smooth ease-in / ease-out ramps
    during specified intervals.
    """
    if not zoom_punch_ins:
        # Pass through unchanged
        return f"[{input_label}]copy[{output_label}]", output_label

    # Build scale multiplier expression with smooth ease-in/ease-out ramp:
    # 1.0 + sum((scale - 1.0) * between(t, start, end) * min(min((t - start) / ramp, (end - t) / ramp), 1.0))
    terms = ["1.0"]
    for punch in zoom_punch_ins:
        start_t = punch.timestamp
        end_t = punch.timestamp + punch.duration
        scale_delta = punch.scale - 1.0
        dur = max(0.01, punch.duration)
        ramp = min(ramp_duration_sec, dur / 2.0)
        if ramp > 0.001:
            ramp_factor = f"min(min((t-{start_t:.2f})/{ramp:.2f},({end_t:.2f}-t)/{ramp:.2f}),1.0)"
            terms.append(f"({scale_delta:.2f}*between(t,{start_t:.2f},{end_t:.2f})*{ramp_factor})")
        else:
            terms.append(f"({scale_delta:.2f}*between(t,{start_t:.2f},{end_t:.2f}))")

    scale_expr = "+".join(terms)
    # iw/scale_expr, ih/scale_expr centered
    crop_str = (
        f"[{input_label}]crop=w='iw/({scale_expr})':h='ih/({scale_expr})':"
        f"x='(iw-ow)/2':y='(ih-oh)/2',scale=1080:960[{output_label}]"
    )
    return crop_str, output_label


def build_jumpcut_select_filter(
    jump_cuts: Sequence[JumpCut],
    v_in: str = "v_in",
    v_out: str = "v_cut",
    a_in: str = "a_in",
    a_out: str = "a_cut",
) -> tuple[str, str, str]:
    """Generate select/aselect filter expressions excising dead air intervals from EDL.

    Returns:
        (filter_string, final_v_out, final_a_out)
    """
    if not jump_cuts:
        return "", v_in, a_in

    # Intervals to drop: between(t, cut_start, cut_end)
    drop_conditions = [f"between(t,{cut.cut_start:.2f},{cut.cut_end:.2f})" for cut in jump_cuts]
    cond_str = "+".join(drop_conditions)

    filter_str = (
        f"[{v_in}]select='not({cond_str})',setpts=N/FRAME_RATE/TB[{v_out}]; "
        f"[{a_in}]aselect='not({cond_str})',asetpts=N/SR/TB[{a_out}]"
    )
    return filter_str, v_out, a_out


def build_progress_bar_filter(
    duration_sec: float,
    input_label: str = "stacked",
    output_label: str = "with_bar",
    color: str = "red@0.8",
    height: int = 12,
    width: int = 1080,
) -> tuple[str, str]:
    """Generate dynamic top progress bar overlay moving across the video duration."""
    safe_duration = max(1.0, duration_sec)
    bar_filter = (
        f"color=c={color}:s={width}x{height}:d={safe_duration:.2f}[bar_color]; "
        f"[{input_label}][bar_color]overlay=x='-{width}+({width}*t/{safe_duration:.2f})':y=0[{output_label}]"
    )
    return bar_filter, output_label


def find_user_background_videos(
    category: str = "minecraft",
    assets_dir: Path | None = None,
) -> list[Path]:
    """Find all valid video files in assets/backgrounds/<category>/."""
    base = assets_dir or get_assets_dir()
    bg_dir = base / "backgrounds" / category
    if not bg_dir.is_dir():
        return []

    valid_extensions = {".mp4", ".mov", ".mkv", ".webm"}
    return [p for p in bg_dir.iterdir() if p.is_file() and p.suffix.lower() in valid_extensions]


def pick_background_asset(
    category: str = "minecraft",
    assets_dir: Path | None = None,
    total_clip_duration: float = 45.0,
) -> tuple[Path | None, float]:
    """Pick a user background video and compute a random start offset.

    Returns:
        (video_path, start_offset_seconds)
        If no user video is found, returns (None, 0.0) signaling procedural lavfi fallback.
    """
    candidates = find_user_background_videos(category=category, assets_dir=assets_dir)
    if not candidates:
        logger.warning(
            "No background videos found in assets/backgrounds/%s. Falling back to lavfi background.",
            category,
        )
        return None, 0.0

    chosen = random.choice(candidates)
    # Estimate or probe duration if possible, otherwise use a safe random start
    # Background videos are typically 5 to 30 minutes long.
    # We choose a random start between 0 and 600s, wrapped safely.
    random_offset = random.uniform(0.0, 180.0)
    return chosen, random_offset


def find_user_audio_assets(
    subfolder: str,
    assets_dir: Path | None = None,
) -> list[Path]:
    """Find audio files (.mp3, .wav, .aac, .ogg) in assets/<subfolder>/."""
    base = assets_dir or get_assets_dir()
    target_dir = base / subfolder
    if not target_dir.is_dir():
        return []
    valid_exts = {".mp3", ".wav", ".aac", ".ogg", ".m4a"}
    return [p for p in target_dir.iterdir() if p.is_file() and p.suffix.lower() in valid_exts]


def build_complex_filtergraph(
    source_clip_has_audio: bool,
    subtitles_path: Path | str,
    duration_sec: float,
    dopamine_preset: DopaminePreset,
    edl: EditDecisionList | None = None,
    fonts_dir: Path | str | None = None,
    has_background_video: bool = True,
    music_input_index: int | None = None,
    sfx_input_index: int | None = None,
) -> tuple[str, str, str]:
    """Construct the complete FFmpeg -filter_complex string for 9:16 Shorts compositing.

    Inputs expected:
    - 0:v (and 0:a if source_clip_has_audio): Source video clip
    - 1:v: Background video stream (or lavfi procedural input)
    - Optional music_input_index: Music audio stream
    - Optional sfx_input_index: Transition SFX audio stream

    Returns:
        (filter_complex_string, out_video_label, out_audio_label)
    """
    filter_stages: list[str] = []

    # 1. Top video: Scale & crop to 1080x960, 30fps
    filter_stages.append(
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,"
        "crop=1080:960,setsar=1,fps=30[top_scaled]"
    )

    # 2. Zoom punch-ins on top video
    if dopamine_preset.enable_zoom_punch_ins and edl and edl.zoom_punch_ins:
        zoom_str, top_final = build_zoom_punch_filter(
            edl.zoom_punch_ins,
            input_label="top_scaled",
            output_label="top_zoomed",
        )
        filter_stages.append(zoom_str)
    else:
        top_final = "top_scaled"

    # 3. Jump cuts on both video and audio chains before vertical stack
    current_a = "0:a" if source_clip_has_audio else None
    if edl and edl.jump_cuts:
        valid_cuts = [c for c in edl.jump_cuts if c.cut_end > c.cut_start >= 0]
        if valid_cuts:
            if source_clip_has_audio:
                jc_filter, top_cut, audio_cut = build_jumpcut_select_filter(
                    valid_cuts,
                    v_in=top_final,
                    v_out="top_cut",
                    a_in="0:a",
                    a_out="a_cut",
                )
                filter_stages.append(jc_filter)
                top_final = top_cut
                current_a = audio_cut
            else:
                drop_conditions = [
                    f"between(t,{cut.cut_start:.2f},{cut.cut_end:.2f})" for cut in valid_cuts
                ]
                cond_str = "+".join(drop_conditions)
                filter_stages.append(
                    f"[{top_final}]select='not({cond_str})',setpts=N/FRAME_RATE/TB[top_cut]"
                )
                top_final = "top_cut"

    # 4. Bottom video (background): Scale & crop to 1080x960, 30fps
    filter_stages.append(
        "[1:v]scale=1080:960:force_original_aspect_ratio=increase,"
        "crop=1080:960,setsar=1,fps=30[bottom]"
    )

    # 5. Vertical stack (1080x1920)
    filter_stages.append(f"[{top_final}][bottom]vstack=inputs=2[stacked]")
    current_v = "stacked"

    # 6. Top progress bar
    if dopamine_preset.enable_progress_bar:
        bar_filter, bar_out = build_progress_bar_filter(
            duration_sec=duration_sec,
            input_label=current_v,
            output_label="with_bar",
        )
        filter_stages.append(bar_filter)
        current_v = bar_out

    # 7. Burn-in ASS Subtitles with Windows-safe path escaping
    escaped_subs = escape_ffmpeg_filter_path(subtitles_path)
    subtitles_filter = f"subtitles={escaped_subs}"
    if fonts_dir:
        escaped_fonts = escape_ffmpeg_filter_path(fonts_dir)
        subtitles_filter += f":fontsdir={escaped_fonts}"

    filter_stages.append(f"[{current_v}]{subtitles_filter}[outv]")
    out_video_label = "[outv]"

    # 8. Audio processing: Loudnorm, Ducked Music (sidechain compression), SFX, and Master Limiter
    if source_clip_has_audio:
        if current_a is None:
            current_a = "0:a"

        if dopamine_preset.enable_loudnorm:
            filter_stages.append(f"[{current_a}]loudnorm=I=-14:LRA=7:tp=-1[voice]")
            voice_label = "voice"
        else:
            voice_label = current_a

        # Mix background music if available using sidechain compression
        if dopamine_preset.enable_music_ducking and music_input_index is not None:
            filter_stages.append(
                f"[{voice_label}]asplit=2[v_main][v_side]; "
                f"[{music_input_index}:a]volume=0.25[bg_raw]; "
                f"[bg_raw][v_side]sidechaincompress=threshold=0.1:ratio=4:attack=20:release=300[bg_ducked]; "
                f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[mixed_a]"
            )
            voice_label = "mixed_a"

        # Mix SFX if available
        if dopamine_preset.enable_sfx and sfx_input_index is not None:
            filter_stages.append(
                f"[{sfx_input_index}:a]volume=0.4[sfx_audio]; "
                f"[{voice_label}][sfx_audio]amix=inputs=2:duration=first[sfx_mixed_a]"
            )
            voice_label = "sfx_mixed_a"

        # Master audio limiting: protect against clipping and maintain -1.0dB true peak
        filter_stages.append(f"[{voice_label}]alimiter=limit=-1.0dB[master_a]")
        voice_label = "master_a"

        out_audio_label = f"[{voice_label}]"
    else:
        # Generate silent audio if source has no audio track
        filter_stages.append(
            f"anullsrc=channel_layout=stereo:sample_rate=44100:d={duration_sec:.2f}[outa]"
        )
        out_audio_label = "[outa]"

    filter_complex_str = "; ".join(filter_stages)
    return filter_complex_str, out_video_label, out_audio_label
