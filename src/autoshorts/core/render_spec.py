"""Shared render contract: render methods, layout spec, request/result, backend protocol.

This module is the single agreed interface between the independent render workstreams
(raea backend, streamer-cam layout, parallel multi-method batches, media improvements).
Extend it additively; do not rename or remove existing names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

# Normalized rectangle inside a frame: (x, y, width, height), each in 0..1.
NormRect = tuple[float, float, float, float]

CAM_GAMEPLAY_LAYOUT = "cam_gameplay"
"""layout_mode value for: streamer camera on the top panel, gameplay on the bottom panel."""


class RenderMethod(StrEnum):
    """How a short is rendered. AUTO walks FALLBACK_CHAIN until one backend succeeds."""

    AUTO = "auto"
    RAEA = "raea"
    REMOTION = "remotion"
    FFMPEG = "ffmpeg"


# AUTO order. raea is primary, Remotion is its fallback, plain FFmpeg is the last resort
# (the pipeline must keep working on machines with neither raea nor Node installed).
FALLBACK_CHAIN: tuple[RenderMethod, ...] = (
    RenderMethod.RAEA,
    RenderMethod.REMOTION,
    RenderMethod.FFMPEG,
)


@dataclass(frozen=True)
class CamLayoutSpec:
    """Geometry for the streamer-cam (top) + gameplay (bottom) vertical layout.

    The output canvas is split horizontally: the top ``top_ratio`` of the height shows the
    camera, the rest shows gameplay. ``cam_source="crop"`` cuts the facecam out of the
    source video itself (typical for Twitch/YouTube stream VODs where the facecam is an
    overlay); ``"file"`` uses a separate camera recording in ``cam_file``.
    """

    top_ratio: float = 0.5
    cam_source: Literal["crop", "file"] = "crop"
    cam_rect: NormRect | None = None  # region of the source holding the facecam; None = auto-detect
    cam_file: Path | None = None
    gameplay_rect: NormRect | None = (
        None  # region of the source holding gameplay; None = whole frame
    )
    mirror_cam: bool = False
    # Crop mode only: when gameplay_rect is None, use the largest part of the frame that does not
    # contain the camera, so the facecam overlay is not shown twice (top panel and gameplay).
    exclude_cam_from_gameplay: bool = False


@dataclass
class RenderRequest:
    """Everything a backend needs to produce one finished 9:16 short."""

    job_id: str
    source_video: Path
    output_path: Path
    width: int = 1080
    height: int = 1920
    fps: int = 30
    layout_mode: str = "split_50_50"
    cam_layout: CamLayoutSpec | None = None
    background_video: Path | None = None
    subtitle_ass: Path | None = None
    caption_track: Path | None = None  # JSON word timings (see media/remotion_overlay.py)
    edl: dict[str, Any] | None = None
    branding: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)  # backend-specific, additive


@dataclass
class RenderResult:
    """Outcome of a render, including which methods were tried on the way."""

    output_path: Path
    method_used: RenderMethod
    attempts: list[tuple[RenderMethod, str | None]] = field(default_factory=list)  # (method, error)
    duration_sec: float = 0.0


class RenderUnavailableError(RuntimeError):
    """The backend cannot run here (binary/deps missing). Triggers fallback, never a job failure."""


class RenderFailedError(RuntimeError):
    """The backend ran but failed. Triggers fallback to the next method."""


@runtime_checkable
class RenderBackend(Protocol):
    """One way of turning a RenderRequest into a finished video."""

    method: RenderMethod

    def is_available(self) -> bool:
        """Cheap check (no rendering) that this backend can run on this machine."""
        ...

    def render(self, request: RenderRequest) -> RenderResult:
        """Render the request. Raise RenderUnavailableError or RenderFailedError on failure."""
        ...
