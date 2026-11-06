"""Streamer-cam (top) + gameplay (bottom) layout: spec resolution and filtergraph stages.

Pure helpers used by ``dopamine_effects.build_complex_filtergraph`` (geometry, filter strings)
and ``FFmpegCompositor`` (turning config/auto-detection into a usable ``CamLayoutSpec`` or
degrading to the split layout).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from autoshorts.core.render_spec import CAM_GAMEPLAY_LAYOUT, CamLayoutSpec, NormRect
from autoshorts.media.cam_detect import clamp_rect

logger = logging.getLogger(__name__)

MIN_TOP_RATIO = 0.2
MAX_TOP_RATIO = 0.8
MIN_RECT_SIDE = 0.02


def panel_heights(height: int, top_ratio: float) -> tuple[int, int]:
    """Exact (top, bottom) panel heights: both even, summing to ``height``."""
    ratio = min(max(float(top_ratio), MIN_TOP_RATIO), MAX_TOP_RATIO)
    top_h = int(round(height * ratio / 2)) * 2
    return top_h, height - top_h


def valid_rect(rect: NormRect | None) -> NormRect | None:
    """A clamped copy of ``rect``, or None when it is missing, malformed or too small."""
    if rect is None:
        return None
    try:
        x, y, w, h = (float(v) for v in rect)
    except (TypeError, ValueError):
        return None
    clamped = clamp_rect((x, y, w, h))
    if clamped[2] < MIN_RECT_SIDE or clamped[3] < MIN_RECT_SIDE:
        return None
    return clamped


def _is_full_frame(rect: NormRect) -> bool:
    return rect[0] <= 0.0 and rect[1] <= 0.0 and rect[2] >= 1.0 and rect[3] >= 1.0


def crop_rect_filter(rect: NormRect) -> str:
    """Crop filter cutting a normalized rect out of the input (empty for the whole frame)."""
    if _is_full_frame(rect):
        return ""
    x, y, w, h = rect
    return f"crop=w=iw*{w:.4f}:h=ih*{h:.4f}:x=iw*{x:.4f}:y=ih*{y:.4f},"


def _panel_chain(rect: NormRect | None, w: int, h: int, fps: int, mirror: bool) -> str:
    """crop region -> scale to cover -> center crop to exact panel size -> SAR 1 -> fps."""
    pre = crop_rect_filter(rect) if rect is not None else ""
    flip = "hflip," if mirror else ""
    return (
        f"{pre}scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},{flip}setsar=1,fps={fps}"
    )


def build_cam_gameplay_stages(
    spec: CamLayoutSpec,
    *,
    source_label: str,
    cam_label: str | None,
    width: int,
    height: int,
    fps: int,
) -> tuple[list[str], str]:
    """Filter stages composing camera (top) over gameplay (bottom). Returns (stages, label).

    ``source_label`` is the (possibly jump-cut) source video. ``cam_label`` is the separate
    camera input for ``cam_source="file"``; None means the camera is cropped out of the source.
    """
    top_h, bottom_h = panel_heights(height, spec.top_ratio)
    game_rect = valid_rect(spec.gameplay_rect)
    stages: list[str] = []
    if cam_label is None:
        cam_rect = valid_rect(spec.cam_rect)
        if cam_rect is None:
            raise ValueError("cam_gameplay with cam_source='crop' needs a valid cam_rect")
        stages.append(f"[{source_label}]split=2[cam_src][game_src]")
        cam_in, game_in = "cam_src", "game_src"
    else:
        cam_rect = None
        cam_in, game_in = cam_label, source_label
    stages.append(
        f"[{cam_in}]{_panel_chain(cam_rect, width, top_h, fps, spec.mirror_cam)}[cam_panel]"
    )
    stages.append(f"[{game_in}]{_panel_chain(game_rect, width, bottom_h, fps, False)}[game_panel]")
    stages.append("[cam_panel][game_panel]vstack=inputs=2[stacked]")
    return stages, "stacked"


def cam_layout_from_config(cfg: Any) -> CamLayoutSpec:
    """Build the render-contract spec from a ``CamLayoutConfig`` (or any object with its fields)."""
    cam_file = getattr(cfg, "cam_file", None)
    return CamLayoutSpec(
        top_ratio=float(getattr(cfg, "top_ratio", 0.5)),
        cam_source=getattr(cfg, "cam_source", "crop"),
        cam_rect=_as_rect(getattr(cfg, "cam_rect", None)),
        cam_file=Path(cam_file) if cam_file else None,
        gameplay_rect=_as_rect(getattr(cfg, "gameplay_rect", None)),
        mirror_cam=bool(getattr(cfg, "mirror_cam", False)),
    )


def _as_rect(value: Any) -> NormRect | None:
    if value is None:
        return None
    x, y, w, h = (float(v) for v in value)
    return (x, y, w, h)


def resolve_cam_layout(
    spec: CamLayoutSpec | None,
    *,
    source: Path,
    auto_detect: bool = True,
    detect: Callable[[Path], NormRect | None] | None = None,
    cam_file_ok: Callable[[Path], bool] | None = None,
) -> CamLayoutSpec | None:
    """Return a spec the filtergraph can render, or None to fall back to the split layout.

    Camera region: explicit ``cam_rect``; else (when ``auto_detect``) a detected facecam; else
    None. A ``file`` source needs an existing video ``cam_file``; if it is unusable the
    ``cam_rect`` crop path is tried before giving up. Never raises.
    """
    if spec is None:
        logger.warning("cam_gameplay requested without a cam layout; using split layout.")
        return None
    try:
        top = min(max(float(spec.top_ratio), MIN_TOP_RATIO), MAX_TOP_RATIO)
        if top != spec.top_ratio:
            logger.warning("cam_layout top_ratio %.2f clamped to %.2f", spec.top_ratio, top)
        spec = replace(spec, top_ratio=top, gameplay_rect=valid_rect(spec.gameplay_rect))
        if spec.cam_source == "file":
            cam_file = Path(spec.cam_file) if spec.cam_file else None
            usable = cam_file is not None and cam_file.is_file()
            if usable and cam_file_ok is not None:
                usable = cam_file_ok(cam_file)
            if usable:
                return spec
            logger.warning("cam_layout cam_file %s is unusable; trying crop mode.", spec.cam_file)
            spec = replace(spec, cam_source="crop", cam_file=None)
        rect = valid_rect(spec.cam_rect)
        if rect is None and auto_detect:
            if detect is None:
                from autoshorts.media.cam_detect import detect_facecam_rect as detect
            rect = valid_rect(detect(source))
            if rect is not None:
                logger.info("cam_layout: auto-detected facecam rect %s", rect)
        if rect is None:
            logger.warning(
                "cam_gameplay: no facecam region (set branding.cam_layout.cam_rect or install "
                "opencv for auto-detect); falling back to split layout."
            )
            return None
        return replace(spec, cam_source="crop", cam_rect=rect)
    except Exception as exc:  # never crash a render over layout resolution
        logger.warning("cam_gameplay resolution failed (%s); using split layout.", exc)
        return None


__all__ = [
    "CAM_GAMEPLAY_LAYOUT",
    "build_cam_gameplay_stages",
    "cam_layout_from_config",
    "crop_rect_filter",
    "panel_heights",
    "resolve_cam_layout",
    "valid_rect",
]
