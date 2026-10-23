"""High-Dopamine filtergraph builder and effects engine for 9:16 vertical Shorts.

Implements:
- 5 Layout Modes: split_50_50, split_70_30, full_blur_bg, full_center_crop, pip_gameplay
- Color grading LUT filters: vibrant, cinematic, warm, crisp, none
- Channel watermark branding filter with configurable position and opacity
- Dynamic progress bar overlay (top, bottom, seam, none)
- Zoom punch-ins synchronized with emphasis words from EDL
- Jump cuts excising dead air/silence from EDL
- Audio normalization (voice boost + loudnorm + EBU R128 mastering)
- Ducked background music and event-synced transition SFX (adelay)
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
    """Feature toggles corresponding to dopamine presets."""

    level: str
    enable_progress_bar: bool
    enable_zoom_punch_ins: bool
    enable_loudnorm: bool
    enable_music_ducking: bool
    enable_sfx: bool
    enable_hook_card: bool
    enable_split_separator: bool = False
    split_separator_style: str = "none"


DOPAMINE_PRESETS: dict[str, DopaminePreset] = {
    "none": DopaminePreset(
        level="none",
        enable_progress_bar=False,
        enable_zoom_punch_ins=False,
        enable_loudnorm=False,
        enable_music_ducking=False,
        enable_sfx=False,
        enable_hook_card=False,
        enable_split_separator=False,
        split_separator_style="none",
    ),
    "low": DopaminePreset(
        level="low",
        enable_progress_bar=False,
        enable_zoom_punch_ins=False,
        enable_loudnorm=False,
        enable_music_ducking=False,
        enable_sfx=False,
        enable_hook_card=False,
        enable_split_separator=False,
        split_separator_style="none",
    ),
    "medium": DopaminePreset(
        level="medium",
        enable_progress_bar=True,
        enable_zoom_punch_ins=False,
        enable_loudnorm=True,
        enable_music_ducking=False,
        enable_sfx=False,
        enable_hook_card=True,
        enable_split_separator=False,
        split_separator_style="none",
    ),
    "high": DopaminePreset(
        level="high",
        enable_progress_bar=True,
        enable_zoom_punch_ins=True,
        enable_loudnorm=True,
        enable_music_ducking=True,
        enable_sfx=True,
        enable_hook_card=True,
        enable_split_separator=False,
        split_separator_style="none",
    ),
    "ultra": DopaminePreset(
        level="ultra",
        enable_progress_bar=True,
        enable_zoom_punch_ins=True,
        enable_loudnorm=True,
        enable_music_ducking=True,
        enable_sfx=True,
        enable_hook_card=True,
        enable_split_separator=True,
        split_separator_style="glow",
    ),
    "extreme": DopaminePreset(
        level="extreme",
        enable_progress_bar=True,
        enable_zoom_punch_ins=True,
        enable_loudnorm=True,
        enable_music_ducking=True,
        enable_sfx=True,
        enable_hook_card=True,
        enable_split_separator=True,
        split_separator_style="glow",
    ),
}


def get_dopamine_preset(level: str = "medium") -> DopaminePreset:
    """Retrieve the DopaminePreset for the specified level ('none', 'low', 'medium', 'high', 'ultra', 'extreme')."""
    normalized = (level or "medium").strip().lower()
    return DOPAMINE_PRESETS.get(normalized, DOPAMINE_PRESETS["medium"])


def build_color_grading_filter(preset: str = "none") -> str:
    """Generate color grading filter expression.

    Supported presets: 'none', 'vibrant', 'cinematic', 'warm', 'crisp'.
    """
    clean_preset = (preset or "none").strip().lower()
    if clean_preset == "none" or not clean_preset:
        return ""
    elif clean_preset == "vibrant":
        return "eq=saturation=1.28:contrast=1.08:brightness=0.02,unsharp=5:5:0.8:5:5:0.0"
    elif clean_preset == "cinematic":
        return "eq=contrast=1.12:saturation=0.92:gamma_r=1.04:gamma_b=0.96,colorbalance=rs=0.04:bs=-0.04:rh=-0.03:bh=0.05"
    elif clean_preset == "warm":
        return "eq=gamma_r=1.08:gamma_g=1.02:gamma_b=0.92:saturation=1.14:contrast=1.04"
    elif clean_preset == "crisp":
        return "unsharp=5:5:1.2:5:5:0.0,eq=contrast=1.06:saturation=1.06"
    return ""


def build_watermark_filter(
    watermark_text: str | None,
    position: str = "top_right",
    opacity: float = 0.65,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """Generate drawtext filter string for channel handle watermark branding."""
    if not watermark_text:
        return ""

    # Escape for the filtergraph and drawtext: backslash, colon, single quote.
    # expansion=none (below) makes '%' literal, so it needs no escape. A "\%" escape was
    # unreliable: it printed "Stray %" and its parity flipped when an apostrophe was present.
    escaped_text = watermark_text.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")

    pos = (position or "top_right").strip().lower()
    if pos == "top_left":
        x_expr = "60" if width == 1080 else f"{int(60 * width / 1080)}"
        y_expr = "180" if height == 1920 else f"{int(180 * height / 1920)}"
    elif pos == "bottom_right":
        x_expr = "w-tw-160" if width == 1080 else f"w-tw-{int(160 * width / 1080)}"
        y_expr = "h-th-420" if height == 1920 else f"h-th-{int(420 * height / 1920)}"
    elif pos == "bottom_center":
        x_expr = "(w-tw)/2"
        y_expr = "h-th-380" if height == 1920 else f"h-th-{int(380 * height / 1920)}"
    else:  # top_right (default)
        x_expr = "w-tw-60" if width == 1080 else f"w-tw-{int(60 * width / 1080)}"
        y_expr = "180" if height == 1920 else f"{int(180 * height / 1920)}"

    fontsize = max(18, int(32 * width / 1080))
    alpha_val = max(0.05, min(1.0, opacity))

    return (
        f"drawtext=text='{escaped_text}':expansion=none:fontcolor=white@{alpha_val:.2f}:"
        f"fontsize={fontsize}:x={x_expr}:y={y_expr}:shadowcolor=black@0.5:shadowx=2:shadowy=2"
    )


def calculate_optical_center(crop_focus: str = "center") -> tuple[str, str]:
    """Calculate optical (x, y) crop expressions for speaker-centered zoom punches.

    Aligns zoom center directly with the active speaker's face based on crop_focus:
    - 'left': speaker framed on left side (x='(iw-ow)*0.22', y='(ih-oh)*0.40')
    - 'right': speaker framed on right side (x='(iw-ow)*0.78', y='(ih-oh)*0.40')
    - 'dynamic': horizontal sinusoidal tracking matching moving speaker
    - 'center': centered framing (x='(iw-ow)/2', y='(ih-oh)/2')
    """
    focus = (crop_focus or "center").strip().lower()
    if focus == "left":
        return "(iw-ow)*0.22", "(ih-oh)*0.40"
    if focus == "right":
        return "(iw-ow)*0.78", "(ih-oh)*0.40"
    if focus == "dynamic":
        return "(iw-ow)*(0.5+0.25*sin(t*0.35))", "(ih-oh)/2"
    return "(iw-ow)/2", "(ih-oh)/2"


def build_zoom_punch_filter(
    zoom_punch_ins: Sequence[ZoomPunchIn],
    input_label: str = "top_scaled",
    output_label: str = "top",
    ramp_duration_sec: float = 0.15,
    target_w: int = 1080,
    target_h: int = 960,
    crop_focus: str = "center",
) -> tuple[str, str]:
    """Generate dynamic zoom punch-in crop expressions for the top video stream.

    Applies an optically centered crop (scaled back to target_w x target_h) with smooth ease-in / ease-out ramps
    during specified intervals, centered directly on the active speaker.
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
    x_opt, y_opt = calculate_optical_center(crop_focus)
    crop_str = (
        f"[{input_label}]crop=w='iw/({scale_expr})':h='ih/({scale_expr})':"
        f"x='{x_opt}':y='{y_opt}',scale={target_w}:{target_h}[{output_label}]"
    )
    return crop_str, output_label


def build_visual_momentum_filter(
    jump_cuts: Sequence[JumpCut],
    input_label: str = "top",
    output_label: str = "top_momentum",
    whip_duration_sec: float = 0.08,
    target_w: int = 1080,
    target_h: int = 960,
) -> tuple[str, str]:
    """Generate subtle whip-pan micro-transitions at jump cuts to maintain visual momentum.

    Applies a dynamic horizontal micro-shift during the transition boundaries of each cut
    to hide audio/visual seams with energetic motion.
    """
    if not jump_cuts:
        return f"[{input_label}]copy[{output_label}]", output_label

    terms = ["0"]
    for cut in jump_cuts:
        st = max(0.0, cut.cut_start - whip_duration_sec)
        et = cut.cut_end + whip_duration_sec
        dur = max(0.01, whip_duration_sec)
        shift_expr = (
            f"(sin(3.14159*(t-{st:.2f})/{dur:.2f})*between(t,{st:.2f},{cut.cut_start:.2f}))"
        )
        shift_expr_post = (
            f"(-sin(3.14159*(t-{cut.cut_end:.2f})/{dur:.2f})*between(t,{cut.cut_end:.2f},{et:.2f}))"
        )
        terms.append(f"({shift_expr}+{shift_expr_post})")

    impulse_expr = "+".join(terms)
    momentum_str = (
        f"[{input_label}]crop=w='iw*0.96':h='ih*0.96':"
        f"x='(iw-ow)/2+(iw-ow)*0.4*({impulse_expr})':y='(ih-oh)/2',"
        f"scale={target_w}:{target_h}[{output_label}]"
    )
    return momentum_str, output_label


def build_split_separator_filter(
    input_label: str = "stacked",
    output_label: str = "stacked_sep",
    seam_y: int = 960,
    width: int = 1080,
    duration_sec: float = 45.0,
    style: str = "glow",
) -> tuple[str, str]:
    """Generate sleek glowing separator line or subtle shadow depth at split-screen seam.

    Supported styles:
    - 'glow': Modern cyan/white glowing separator line with subtle gradient depth
    - 'drawbox' / 'neon': 4px glowing neon divider via drawbox filter
    - 'dark': Clean dark border divider via drawbox filter
    - 'shadow': Soft drop-shadow bar creating visual elevation between top and bottom clips
    - 'line': Clean minimalist neutral line
    - 'none': Pass through unchanged
    """
    clean_style = (style or "glow").strip().lower()
    if clean_style == "none":
        return f"[{input_label}]copy[{output_label}]", output_label

    dur = max(1.0, duration_sec)

    if clean_style in ("drawbox", "neon"):
        line_filter = f"[{input_label}]drawbox=x=0:y={seam_y - 2}:w=iw:h=4:color=0x00E5FF@0.9:t=fill[{output_label}]"
        return line_filter, output_label

    elif clean_style == "dark":
        line_filter = f"[{input_label}]drawbox=x=0:y={seam_y - 2}:w=iw:h=4:color=black@0.85:t=fill[{output_label}]"
        return line_filter, output_label

    elif clean_style == "shadow":
        line_filter = (
            f"color=c=black@0.45:s={width}x8:d={dur:.2f}[shadow_bar]; "
            f"[{input_label}][shadow_bar]overlay=x=0:y={seam_y - 4}[{output_label}]"
        )
        return line_filter, output_label

    elif clean_style == "line":
        line_filter = (
            f"color=c=white@0.80:s={width}x2:d={dur:.2f}[sep_line]; "
            f"[{input_label}][sep_line]overlay=x=0:y={seam_y - 1}[{output_label}]"
        )
        return line_filter, output_label

    else:  # 'glow' (default)
        halo_h = 6
        core_h = 2
        line_filter = (
            f"color=c=0x00E5FF@0.40:s={width}x{halo_h}:d={dur:.2f}[sep_halo]; "
            f"color=c=white@0.90:s={width}x{core_h}:d={dur:.2f}[sep_core]; "
            f"[{input_label}][sep_halo]overlay=x=0:y={seam_y - halo_h // 2}[with_halo]; "
            f"[with_halo][sep_core]overlay=x=0:y={seam_y - core_h // 2}[{output_label}]"
        )
        return line_filter, output_label


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
    position: str = "top",
    total_height: int = 1920,
    layout_mode: str = "split_50_50",
) -> tuple[str, str]:
    """Generate dynamic progress bar overlay moving across the video duration.

    Supports positions: 'top' (y=0), 'bottom' (y=total_height - height),
    'seam' (y at split seam), 'none' (disabled).
    """
    pos = (position or "top").strip().lower()
    if pos == "none":
        return f"[{input_label}]copy[{output_label}]", output_label

    if pos == "bottom":
        y_coord = total_height - height
    elif pos == "seam":
        if layout_mode == "split_70_30":
            y_coord = int(total_height * 0.70) - height // 2
        else:
            y_coord = total_height // 2 - height // 2
    else:  # top (default)
        y_coord = 0

    safe_duration = max(1.0, duration_sec)
    bar_filter = (
        f"color=c={color}:s={width}x{height}:d={safe_duration:.2f}[bar_color]; "
        f"[{input_label}][bar_color]overlay=x='-{width}+({width}*t/{safe_duration:.2f})':y={y_coord}[{output_label}]"
    )
    return bar_filter, output_label


def find_user_background_videos(
    category: str = "minecraft",
    assets_dir: Path | None = None,
) -> list[Path]:
    """Find all valid video files in assets/backgrounds/<category>/."""
    from autoshorts.media.background_fetcher import normalize_category

    base = assets_dir or get_assets_dir()
    clean_cat = normalize_category(category)
    bg_dir = base / "backgrounds" / clean_cat
    if not bg_dir.is_dir():
        literal_dir = base / "backgrounds" / category
        if not literal_dir.is_dir():
            return []
        bg_dir = literal_dir

    valid_extensions = {".mp4", ".mov", ".mkv", ".webm"}
    return [
        p
        for p in bg_dir.iterdir()
        if p.is_file() and p.suffix.lower() in valid_extensions and p.stat().st_size > 0
    ]


def pick_background_asset(
    category: str = "minecraft",
    assets_dir: Path | None = None,
    total_clip_duration: float = 45.0,
    auto_fetch: bool = True,
) -> tuple[Path | None, float]:
    """Pick a user background video and compute a random start offset.

    When category is selected (or 'auto') and no video files exist, automatically
    invokes ensure_backgrounds_available(category) to download a clip on the fly
    before falling back to procedural lavfi gradient.

    Returns:
        (video_path, start_offset_seconds)
        If no user video is found, returns (None, 0.0) signaling procedural lavfi fallback.
    """
    from autoshorts.media.background_fetcher import (
        ensure_backgrounds_available,
        normalize_category,
    )

    raw_cat = (category or "minecraft").strip().lower()

    if raw_cat == "auto":
        # Check if any category already has candidates
        mc = find_user_background_videos(category="minecraft", assets_dir=assets_dir)
        sw = find_user_background_videos(category="subway", assets_dir=assets_dir)
        if mc and not sw:
            target_cat = "minecraft"
            candidates = mc
        elif sw and not mc:
            target_cat = "subway"
            candidates = sw
        elif mc and sw:
            target_cat = random.choice(["minecraft", "subway"])
            candidates = mc if target_cat == "minecraft" else sw
        else:
            target_cat = "minecraft"
            candidates = []
    else:
        target_cat = normalize_category(raw_cat)
        candidates = find_user_background_videos(category=target_cat, assets_dir=assets_dir)

    if not candidates and auto_fetch:
        try:
            logger.info(
                "No background video found for '%s'. Attempting on-demand auto-fetch...",
                target_cat,
            )
            ensure_backgrounds_available(
                category=target_cat,
                min_clips=1,
                assets_dir=assets_dir,
            )
            candidates = find_user_background_videos(category=target_cat, assets_dir=assets_dir)
        except Exception as exc:
            logger.warning(
                "Failed to auto-fetch background clips for category '%s': %s",
                target_cat,
                exc,
            )

    if not candidates:
        logger.warning(
            "No background videos found in assets/backgrounds/%s. Falling back to lavfi background.",
            target_cat,
        )
        return None, 0.0

    chosen = random.choice(candidates)
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


def build_speaker_crop_filter(
    width: int,
    height: int,
    crop_focus: str = "center",
) -> str:
    """Build FFmpeg crop filter expression for speaker framing.

    Supports:
    - 'center': standard centered crop (crop=w:h)
    - 'left': left-speaker focused crop (crop=w:h:x='(in_w-out_w)*0.22':y='(in_h-out_h)/2')
    - 'right': right-speaker focused crop (crop=w:h:x='(in_w-out_w)*0.78':y='(in_h-out_h)/2')
    - 'dynamic': slow sinusoidal horizontal pan across speakers
      (crop=w:h:x='(in_w-out_w)*(0.5+0.25*sin(t*0.35))':y='(in_h-out_h)/2')
    """
    focus = (crop_focus or "center").strip().lower()
    if focus == "left":
        return f"crop={width}:{height}:x='(in_w-out_w)*0.22':y='(in_h-out_h)/2'"
    if focus == "right":
        return f"crop={width}:{height}:x='(in_w-out_w)*0.78':y='(in_h-out_h)/2'"
    if focus == "dynamic":
        return f"crop={width}:{height}:x='(in_w-out_w)*(0.5+0.25*sin(t*0.35))':y='(in_h-out_h)/2'"
    return f"crop={width}:{height}"


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
    layout_mode: str = "split_50_50",
    color_grading: str = "none",
    watermark_text: str | None = None,
    watermark_position: str = "top_right",
    watermark_opacity: float = 0.65,
    progress_bar_position: str = "bottom",
    progress_bar_color: str = "red@0.8",
    voice_boost: bool = True,
    target_lufs: float = -14.0,
    bg_music_volume: float = 0.18,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    crop_focus: str = "center",
    split_separator: str = "none",
    source_clip_has_video: bool = True,
    rhythm_ducking_release: float | None = None,
) -> tuple[str, str, str]:
    """Construct the complete FFmpeg -filter_complex string for 9:16 Shorts compositing.

    Supports 5 layout modes:
    - 'split_50_50': Top 50% speaker + Bottom 50% gameplay
    - 'split_70_30': Top 70% speaker + Bottom 30% gameplay
    - 'full_blur_bg': Single-source blurred background + centered 16:9 foreground
    - 'full_center_crop': Single-source full vertical center crop
    - 'pip_gameplay': Full vertical speaker + Picture-in-Picture gameplay box

    Returns:
        (filter_complex_string, out_video_label, out_audio_label)
    """
    filter_stages: list[str] = []
    mode = (layout_mode or "split_50_50").strip().lower()

    # Pre-extract valid jump cuts
    valid_cuts = (
        [c for c in edl.jump_cuts if c.cut_end > c.cut_start >= 0]
        if (edl and edl.jump_cuts)
        else []
    )
    current_a = "0:a" if source_clip_has_audio else None

    # Determine effective split separator style across layout and preset
    eff_sep = (
        split_separator
        if split_separator != "none"
        else (
            dopamine_preset.split_separator_style
            if getattr(dopamine_preset, "enable_split_separator", False)
            else "none"
        )
    )

    # -------------------------------------------------------------------------
    # 1. Video Layout Composition
    # -------------------------------------------------------------------------
    if not source_clip_has_video:
        # Audio-only source clip: use background video or black canvas as full vertical canvas
        if has_background_video:
            filter_stages.append(
                f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={fps}[bg_full]"
            )
            top_final = "bg_full"
        else:
            filter_stages.append(
                f"color=c=black:s={width}x{height}:r={fps}:d={duration_sec}[bg_full]"
            )
            top_final = "bg_full"

        if valid_cuts:
            if source_clip_has_audio:
                jc_filter, top_cut, audio_cut = build_jumpcut_select_filter(
                    valid_cuts,
                    v_in=top_final,
                    v_out="v_cut",
                    a_in="0:a",
                    a_out="a_cut",
                )
                filter_stages.append(jc_filter)
                current_v = top_cut
                current_a = audio_cut
            else:
                drop_conds = [f"between(t,{c.cut_start:.2f},{c.cut_end:.2f})" for c in valid_cuts]
                filter_stages.append(
                    f"[{top_final}]select='not({'+'.join(drop_conds)})',setpts=N/FRAME_RATE/TB[v_cut]"
                )
                current_v = "v_cut"
        else:
            current_v = top_final

    elif mode == "split_70_30":
        # 70/30 split: top 70% (1344 @ 1080p), bottom 30% (576 @ 1080p)
        top_h = int(height * 0.70) // 2 * 2
        bottom_h = height - top_h
        speaker_crop = build_speaker_crop_filter(width, top_h, crop_focus=crop_focus)

        filter_stages.append(
            f"[0:v]scale={width}:{top_h}:force_original_aspect_ratio=increase,"
            f"{speaker_crop},setsar=1,fps={fps}[top_scaled]"
        )

        if dopamine_preset.enable_zoom_punch_ins and edl and edl.zoom_punch_ins:
            zoom_str, top_final = build_zoom_punch_filter(
                edl.zoom_punch_ins,
                input_label="top_scaled",
                output_label="top_zoomed",
                target_w=width,
                target_h=top_h,
                crop_focus=crop_focus,
            )
            filter_stages.append(zoom_str)
        else:
            top_final = "top_scaled"

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
                drop_conds = [f"between(t,{c.cut_start:.2f},{c.cut_end:.2f})" for c in valid_cuts]
                filter_stages.append(
                    f"[{top_final}]select='not({'+'.join(drop_conds)})',setpts=N/FRAME_RATE/TB[top_cut]"
                )
                top_final = "top_cut"

        filter_stages.append(
            f"[1:v]scale={width}:{bottom_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{bottom_h},setsar=1,fps={fps}[bottom]"
        )
        filter_stages.append(f"[{top_final}][bottom]vstack=inputs=2[stacked]")
        current_v = "stacked"
        if eff_sep != "none":
            sep_str, sep_out = build_split_separator_filter(
                input_label=current_v,
                output_label="stacked_sep",
                seam_y=top_h,
                width=width,
                duration_sec=duration_sec,
                style=eff_sep,
            )
            filter_stages.append(sep_str)
            current_v = sep_out

    elif mode == "full_blur_bg":
        # Single-source: blurred 9:16 background with sharp centered 16:9 foreground
        if valid_cuts:
            if source_clip_has_audio:
                jc_filter, v_base, audio_cut = build_jumpcut_select_filter(
                    valid_cuts,
                    v_in="0:v",
                    v_out="v_base",
                    a_in="0:a",
                    a_out="a_cut",
                )
                filter_stages.append(jc_filter)
                current_a = audio_cut
            else:
                drop_conds = [f"between(t,{c.cut_start:.2f},{c.cut_end:.2f})" for c in valid_cuts]
                filter_stages.append(
                    f"[0:v]select='not({'+'.join(drop_conds)})',setpts=N/FRAME_RATE/TB[v_base]"
                )
                v_base = "v_base"
        else:
            v_base = "0:v"

        filter_stages.append(f"[{v_base}]split=2[bg_src][fg_src]")
        filter_stages.append(
            f"[bg_src]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=40:5,setsar=1,fps={fps}[bg_blur]"
        )

        fg_h = round(width * 9 / 16) // 2 * 2
        filter_stages.append(
            f"[fg_src]scale={width}:{fg_h}:force_original_aspect_ratio=decrease,"
            f"setsar=1,fps={fps}[fg_scaled]"
        )

        if dopamine_preset.enable_zoom_punch_ins and edl and edl.zoom_punch_ins:
            zoom_str, fg_final = build_zoom_punch_filter(
                edl.zoom_punch_ins,
                input_label="fg_scaled",
                output_label="fg_zoomed",
                target_w=width,
                target_h=fg_h,
                crop_focus=crop_focus,
            )
            filter_stages.append(zoom_str)
        else:
            fg_final = "fg_scaled"

        filter_stages.append(f"[bg_blur][{fg_final}]overlay=x=(W-w)/2:y=(H-h)/2[stacked]")
        current_v = "stacked"

    elif mode == "full_center_crop":
        # Single-source: full 9:16 vertical crop with speaker focus
        speaker_crop = build_speaker_crop_filter(width, height, crop_focus=crop_focus)
        filter_stages.append(
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"{speaker_crop},setsar=1,fps={fps}[full_scaled]"
        )

        if dopamine_preset.enable_zoom_punch_ins and edl and edl.zoom_punch_ins:
            zoom_str, full_final = build_zoom_punch_filter(
                edl.zoom_punch_ins,
                input_label="full_scaled",
                output_label="full_zoomed",
                target_w=width,
                target_h=height,
                crop_focus=crop_focus,
            )
            filter_stages.append(zoom_str)
        else:
            full_final = "full_scaled"

        if valid_cuts:
            if source_clip_has_audio:
                jc_filter, full_cut, audio_cut = build_jumpcut_select_filter(
                    valid_cuts,
                    v_in=full_final,
                    v_out="full_cut",
                    a_in="0:a",
                    a_out="a_cut",
                )
                filter_stages.append(jc_filter)
                full_final = full_cut
                current_a = audio_cut
            else:
                drop_conds = [f"between(t,{c.cut_start:.2f},{c.cut_end:.2f})" for c in valid_cuts]
                filter_stages.append(
                    f"[{full_final}]select='not({'+'.join(drop_conds)})',setpts=N/FRAME_RATE/TB[full_cut]"
                )
                full_final = "full_cut"

        current_v = full_final

    elif mode == "pip_gameplay":
        # Full primary video with bordered PiP inset from gameplay [1:v]
        speaker_crop = build_speaker_crop_filter(width, height, crop_focus=crop_focus)
        filter_stages.append(
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"{speaker_crop},setsar=1,fps={fps}[main_scaled]"
        )

        if dopamine_preset.enable_zoom_punch_ins and edl and edl.zoom_punch_ins:
            zoom_str, main_final = build_zoom_punch_filter(
                edl.zoom_punch_ins,
                input_label="main_scaled",
                output_label="main_zoomed",
                target_w=width,
                target_h=height,
                crop_focus=crop_focus,
            )
            filter_stages.append(zoom_str)
        else:
            main_final = "main_scaled"

        if valid_cuts:
            if source_clip_has_audio:
                jc_filter, main_cut, audio_cut = build_jumpcut_select_filter(
                    valid_cuts,
                    v_in=main_final,
                    v_out="main_cut",
                    a_in="0:a",
                    a_out="a_cut",
                )
                filter_stages.append(jc_filter)
                main_final = main_cut
                current_a = audio_cut
            else:
                drop_conds = [f"between(t,{c.cut_start:.2f},{c.cut_end:.2f})" for c in valid_cuts]
                filter_stages.append(
                    f"[{main_final}]select='not({'+'.join(drop_conds)})',setpts=N/FRAME_RATE/TB[main_cut]"
                )
                main_final = "main_cut"

        pip_w = int(380 * width / 1080) // 2 * 2
        pip_h = pip_w
        filter_stages.append(
            f"[1:v]scale={pip_w}:{pip_h}:force_original_aspect_ratio=increase,"
            f"crop={pip_w}:{pip_h},setsar=1,fps={fps}[pip_scaled]"
        )

        if eff_sep != "none":
            box_col = "black@0.85" if eff_sep == "dark" else "0x00E5FF@0.9"
            filter_stages.append(
                f"[pip_scaled]drawbox=x=0:y=0:w=iw:h=ih:color={box_col}:t=4[pip_bordered]"
            )
            pip_in = "pip_bordered"
        else:
            pip_in = "pip_scaled"

        pip_x = "W-w-48" if width == 1080 else f"W-w-{int(48 * width / 1080)}"
        pip_y = "H-h-420" if height == 1920 else f"H-h-{int(420 * height / 1920)}"
        filter_stages.append(f"[{main_final}][{pip_in}]overlay=x={pip_x}:y={pip_y}[stacked]")
        current_v = "stacked"

    else:
        # Default split_50_50
        top_h = height // 2
        bottom_h = height - top_h
        speaker_crop = build_speaker_crop_filter(width, top_h, crop_focus=crop_focus)

        filter_stages.append(
            f"[0:v]scale={width}:{top_h}:force_original_aspect_ratio=increase,"
            f"{speaker_crop},setsar=1,fps={fps}[top_scaled]"
        )

        if dopamine_preset.enable_zoom_punch_ins and edl and edl.zoom_punch_ins:
            zoom_str, top_final = build_zoom_punch_filter(
                edl.zoom_punch_ins,
                input_label="top_scaled",
                output_label="top_zoomed",
                target_w=width,
                target_h=top_h,
                crop_focus=crop_focus,
            )
            filter_stages.append(zoom_str)
        else:
            top_final = "top_scaled"

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
                drop_conds = [f"between(t,{c.cut_start:.2f},{c.cut_end:.2f})" for c in valid_cuts]
                filter_stages.append(
                    f"[{top_final}]select='not({'+'.join(drop_conds)})',setpts=N/FRAME_RATE/TB[top_cut]"
                )
                top_final = "top_cut"

        filter_stages.append(
            f"[1:v]scale={width}:{bottom_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{bottom_h},setsar=1,fps={fps}[bottom]"
        )
        filter_stages.append(f"[{top_final}][bottom]vstack=inputs=2[stacked]")
        current_v = "stacked"
        if eff_sep != "none":
            sep_str, sep_out = build_split_separator_filter(
                input_label=current_v,
                output_label="stacked_sep",
                seam_y=top_h,
                width=width,
                duration_sec=duration_sec,
                style=eff_sep,
            )
            filter_stages.append(sep_str)
            current_v = sep_out

    # -------------------------------------------------------------------------
    # 2. Color Grading LUT
    # -------------------------------------------------------------------------
    if color_grading and color_grading != "none":
        cg_filter = build_color_grading_filter(color_grading)
        if cg_filter:
            filter_stages.append(f"[{current_v}]{cg_filter}[graded]")
            current_v = "graded"

    # -------------------------------------------------------------------------
    # 3. Watermark (@Handle)
    # -------------------------------------------------------------------------
    if watermark_text:
        wm_filter = build_watermark_filter(
            watermark_text=watermark_text,
            position=watermark_position,
            opacity=watermark_opacity,
            width=width,
            height=height,
        )
        if wm_filter:
            filter_stages.append(f"[{current_v}]{wm_filter}[watermarked]")
            current_v = "watermarked"

    # -------------------------------------------------------------------------
    # 4. Progress Bar
    # -------------------------------------------------------------------------
    if dopamine_preset.enable_progress_bar and progress_bar_position != "none":
        bar_filter, bar_out = build_progress_bar_filter(
            duration_sec=duration_sec,
            input_label=current_v,
            output_label="with_bar",
            color=progress_bar_color,
            height=max(6, int(12 * height / 1920)),
            width=width,
            position=progress_bar_position,
            total_height=height,
            layout_mode=mode,
        )
        filter_stages.append(bar_filter)
        current_v = bar_out

    # -------------------------------------------------------------------------
    # 5. ASS Subtitle Burn-In
    # -------------------------------------------------------------------------
    escaped_subs = escape_ffmpeg_filter_path(subtitles_path)
    subtitles_filter = f"subtitles={escaped_subs}"
    if fonts_dir:
        escaped_fonts = escape_ffmpeg_filter_path(fonts_dir)
        subtitles_filter += f":fontsdir={escaped_fonts}"

    filter_stages.append(f"[{current_v}]{subtitles_filter}[outv]")
    out_video_label = "[outv]"

    # -------------------------------------------------------------------------
    # 6. Audio Mastering: Voice Boost EQ, Loudnorm, Ducked Music, Event SFX, Limiter
    # -------------------------------------------------------------------------
    if source_clip_has_audio:
        if current_a is None:
            current_a = "0:a"

        voice_chain: list[str] = []
        if voice_boost:
            voice_chain.append("highpass=f=85,equalizer=f=3200:width_type=o:width=1.5:g=3.5")
        if dopamine_preset.enable_loudnorm:
            lufs_val = (
                f"{target_lufs:g}" if isinstance(target_lufs, (int, float)) else str(target_lufs)
            )
            voice_chain.append(f"loudnorm=I={lufs_val}:TP=-1.0:LRA=7")

        if voice_chain:
            filter_stages.append(f"[{current_a}]{','.join(voice_chain)}[voice]")
            voice_label = "voice"
        else:
            voice_label = current_a

        # Mix background music if available using sidechain compression
        if dopamine_preset.enable_music_ducking and music_input_index is not None:
            duck_release = (
                int(rhythm_ducking_release) if rhythm_ducking_release is not None else 300
            )
            filter_stages.append(
                f"[{voice_label}]asplit=2[v_main][v_side]; "
                f"[{music_input_index}:a]volume={bg_music_volume:.2f}[bg_raw]; "
                f"[bg_raw][v_side]sidechaincompress=threshold=0.1:ratio=4:attack=20:release={duck_release}[bg_ducked]; "
                f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[mixed_a]"
            )
            voice_label = "mixed_a"

        # Mix SFX if available (with event-synced adelay aligned to first punch-in)
        if dopamine_preset.enable_sfx and sfx_input_index is not None:
            if edl and edl.zoom_punch_ins and edl.zoom_punch_ins[0].timestamp > 0:
                delay_ms = int(edl.zoom_punch_ins[0].timestamp * 1000)
                sfx_chain = f"volume=0.4,adelay={delay_ms}|{delay_ms}"
            else:
                sfx_chain = "volume=0.4"
            filter_stages.append(
                f"[{sfx_input_index}:a]{sfx_chain}[sfx_audio]; "
                f"[{voice_label}][sfx_audio]amix=inputs=2:duration=first[sfx_mixed_a]"
            )
            voice_label = "sfx_mixed_a"

        # Master audio limiting: protect against clipping and maintain -1.0dB true peak
        limiter_extra = ""
        if rhythm_ducking_release is not None and not (
            dopamine_preset.enable_music_ducking and music_input_index is not None
        ):
            limiter_rel = min(1000, max(10, int(rhythm_ducking_release)))
            limiter_extra = f":release={limiter_rel}"
        filter_stages.append(f"[{voice_label}]alimiter=limit=-1.0dB{limiter_extra}[master_a]")
        out_audio_label = "[master_a]"
    else:
        # Generate silent audio if source has no audio track
        filter_stages.append(
            f"anullsrc=channel_layout=stereo:sample_rate=44100:d={duration_sec:.2f}[outa]"
        )
        out_audio_label = "[outa]"

    filter_complex_str = "; ".join(filter_stages)
    return filter_complex_str, out_video_label, out_audio_label


__all__ = [
    "DOPAMINE_PRESETS",
    "DopaminePreset",
    "build_color_grading_filter",
    "build_complex_filtergraph",
    "build_jumpcut_select_filter",
    "build_progress_bar_filter",
    "build_speaker_crop_filter",
    "build_split_separator_filter",
    "build_visual_momentum_filter",
    "build_watermark_filter",
    "build_zoom_punch_filter",
    "calculate_optical_center",
    "find_user_audio_assets",
    "find_user_background_videos",
    "get_dopamine_preset",
    "pick_background_asset",
]
