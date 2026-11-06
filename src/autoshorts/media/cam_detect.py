"""Facecam detection for the streamer-cam + gameplay layout.

``detect_facecam_rect`` samples a few frames of a stream VOD/clip with ffmpeg, finds the face
with OpenCV (optional import: without cv2 it returns None) and expands the face box into the
rectangle of the webcam overlay. The geometry and aggregation are pure functions so they can be
tested with a fake frame provider and a fake detector.
"""

from __future__ import annotations

import logging
import statistics
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from autoshorts.core.render_spec import NormRect

logger = logging.getLogger(__name__)

# (frame_width, frame_height, pixel data). Pixel data is whatever the detector understands
# (a 2-D uint8 numpy array for the OpenCV detector).
Frame = tuple[int, int, Any]
FrameProvider = Callable[[Path, int], Iterable[Frame]]
# Returns face boxes in pixels: (x, y, w, h).
FaceDetector = Callable[[Any], Sequence[tuple[int, int, int, int]]]

SAMPLE_WIDTH = 640
_FACE_TO_CAM_SCALE = 2.4  # webcam overlay box vs. the face box
_FACE_DOWN_SHIFT = 0.12  # face sits slightly above the overlay center (fraction of face h)
_EDGE_SNAP = 0.06  # overlays hugging a frame edge are extended to it
_MIN_SIDE = 0.12
_STABLE_RADIUS = 0.08  # detections farther than this from the median center are noise

_SAMPLE_TIMEOUT_SEC = 20
_cache: dict[tuple[str, int, int], NormRect | None] = {}
_cache_lock = threading.Lock()
_no_cv2_logged = False


def clamp_rect(rect: NormRect) -> NormRect:
    """Clamp a normalized rect into the unit square, keeping a positive area."""
    x, y, w, h = rect
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.0), 1.0 - x)
    h = min(max(h, 0.0), 1.0 - y)
    return (x, y, w, h)


def expand_face_to_cam_rect(face: NormRect) -> NormRect:
    """Grow a normalized face box into the webcam overlay rectangle around it.

    The box is scaled around the face, shifted so the face sits a little above center, shifted
    (not shrunk) back into the frame and extended to any frame edge it nearly touches, since
    webcam overlays are usually pinned to a corner. A face that already fills most of the frame
    (a plain talking-head shot) yields the whole frame.
    """
    fx, fy, fw, fh = face
    w = min(1.0, max(_MIN_SIDE, fw * _FACE_TO_CAM_SCALE))
    h = min(1.0, max(_MIN_SIDE, fh * _FACE_TO_CAM_SCALE))
    cx = fx + fw / 2
    cy = fy + fh / 2 + fh * _FACE_DOWN_SHIFT
    x = min(max(cx - w / 2, 0.0), 1.0 - w)
    y = min(max(cy - h / 2, 0.0), 1.0 - h)
    if x < _EDGE_SNAP:
        w, x = w + x, 0.0
    if x + w > 1.0 - _EDGE_SNAP:
        w = 1.0 - x
    if y < _EDGE_SNAP:
        h, y = h + y, 0.0
    if y + h > 1.0 - _EDGE_SNAP:
        h = 1.0 - y
    if w >= 0.8 and h >= 0.8:
        return (0.0, 0.0, 1.0, 1.0)
    return clamp_rect((round(x, 4), round(y, 4), round(w, 4), round(h, 4)))


def aggregate_face_boxes(boxes: Sequence[NormRect], samples: int) -> NormRect | None:
    """Reduce per-frame face boxes to one stable face box (median), or None if unreliable.

    A webcam overlay does not move, so the detections must agree: boxes whose center strays
    from the median center are dropped, and at least 40% of the sampled frames (minimum 1,
    or 2 when more than 2 frames were sampled) must survive.
    """
    if not boxes:
        return None
    mcx = statistics.median(b[0] + b[2] / 2 for b in boxes)
    mcy = statistics.median(b[1] + b[3] / 2 for b in boxes)
    stable = [
        b
        for b in boxes
        if abs(b[0] + b[2] / 2 - mcx) <= _STABLE_RADIUS
        and abs(b[1] + b[3] / 2 - mcy) <= _STABLE_RADIUS
    ]
    needed = 1 if samples <= 2 else max(2, -(-samples * 2 // 5))
    if len(stable) < needed:
        return None
    return (
        statistics.median(b[0] for b in stable),
        statistics.median(b[1] for b in stable),
        statistics.median(b[2] for b in stable),
        statistics.median(b[3] for b in stable),
    )


def find_facecam_rect(
    frames: Iterable[Frame], detector: FaceDetector, samples: int
) -> NormRect | None:
    """Pure core: detect the largest face per frame, aggregate, expand to the facecam rect."""
    boxes: list[NormRect] = []
    for width, height, data in frames:
        if width <= 0 or height <= 0:
            continue
        faces = [f for f in detector(data) if f[2] > 0 and f[3] > 0]
        if not faces:
            continue
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        boxes.append(clamp_rect((x / width, y / height, w / width, h / height)))
    face = aggregate_face_boxes(boxes, samples)
    return expand_face_to_cam_rect(face) if face is not None else None


def sample_timestamps(duration: float, samples: int) -> list[float]:
    """Evenly spaced sample times inside the clip, avoiding the very first and last 5%."""
    samples = max(1, int(samples))
    if duration <= 0:
        return [0.0]
    lo, hi = duration * 0.05, duration * 0.95
    return [round(lo + (hi - lo) * (i + 0.5) / samples, 3) for i in range(samples)]


def _import_cv2() -> Any | None:
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception:
        return None
    return cv2


def _build_haar_detector(cv2: Any) -> FaceDetector | None:
    try:
        cascade = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        if cascade.empty():
            return None
    except Exception:
        return None

    def _detect(gray: Any) -> list[tuple[int, int, int, int]]:
        found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        return [(int(x), int(y), int(w), int(h)) for x, y, w, h in found]

    return _detect


def _display_size(stream: dict[str, Any]) -> tuple[int, int]:
    """Frame size as ffmpeg decodes it: width/height swapped for 90/270 degree rotation metadata."""
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    rotations: list[Any] = [(stream.get("tags") or {}).get("rotate")]
    rotations.extend(
        sd.get("rotation") for sd in (stream.get("side_data_list") or []) if isinstance(sd, dict)
    )
    for raw in rotations:
        try:
            angle = int(round(float(raw)))
        except (TypeError, ValueError):
            continue
        if angle % 180 != 0:
            return height, width
    return width, height


def _ffmpeg_frame_provider(ffmpeg: str = "ffmpeg") -> FrameProvider:
    """Frame provider sampling grayscale frames through ffmpeg (needs numpy, shipped with cv2)."""

    def _provide(video_path: Path, samples: int) -> Iterable[Frame]:
        import numpy as np

        from autoshorts.media.compositor import _ffprobe_payload, _ffprobe_streams

        payload = _ffprobe_payload(video_path)
        vw = vh = 0
        duration = 0.0
        if payload:
            for stream in _ffprobe_streams(payload):
                if stream.get("codec_type") == "video":
                    vw, vh = _display_size(stream)
                    break
            try:
                duration = float((payload.get("format") or {}).get("duration") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
        if vw <= 0 or vh <= 0:
            return
        w = SAMPLE_WIDTH
        h = max(2, round(vh * w / vw / 2) * 2)
        for ts in sample_timestamps(duration, samples):
            cmd = [
                ffmpeg,
                "-v",
                "error",
                "-ss",
                f"{ts:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={w}:{h}",
                "-pix_fmt",
                "gray",
                "-f",
                "rawvideo",
                "-",
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, timeout=_SAMPLE_TIMEOUT_SEC, check=False
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode != 0 or len(proc.stdout) != w * h:
                continue
            yield (w, h, np.frombuffer(proc.stdout, dtype=np.uint8).reshape(h, w))

    return _provide


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), st.st_mtime_ns, st.st_size)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def detect_facecam_rect(
    video_path: Path | str,
    samples: int = 8,
    *,
    frame_provider: FrameProvider | None = None,
    detector: FaceDetector | None = None,
    ffmpeg_binary: str = "ffmpeg",
    use_cache: bool = True,
) -> NormRect | None:
    """Find the facecam overlay region of a video as a normalized rect, or None.

    Returns None when OpenCV is not installed, no face is found reliably, or ffmpeg fails.
    Results (including misses) are cached per (path, mtime, size). ``frame_provider`` and
    ``detector`` can be injected for tests.
    """
    path = Path(video_path)
    key = _cache_key(path) if use_cache else None
    if key is not None:
        with _cache_lock:
            if key in _cache:
                return _cache[key]

    if detector is None:
        cv2 = _import_cv2()
        if cv2 is None:
            global _no_cv2_logged
            if not _no_cv2_logged:
                _no_cv2_logged = True
                logger.info("Facecam auto-detect unavailable: OpenCV (cv2) is not installed.")
            return None
        detector = _build_haar_detector(cv2)
        if detector is None:
            logger.info("Facecam auto-detect skipped: OpenCV face cascade unavailable.")
            return None
    if frame_provider is None:
        from autoshorts.media.encoder_probe import get_ffmpeg_path

        # Same binary the renders use (the bundled build is preferred over whatever is on PATH).
        provider = _ffmpeg_frame_provider(
            get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
        )
    else:
        provider = frame_provider

    frames_seen = 0

    def _counted(frames: Iterable[Frame]) -> Iterable[Frame]:
        nonlocal frames_seen
        for frame in frames:
            frames_seen += 1
            yield frame

    try:
        rect = find_facecam_rect(
            _counted(provider(path, max(1, samples))), detector, max(1, samples)
        )
    except Exception as exc:
        logger.warning("Facecam auto-detect failed for %s: %s", path.name, exc)
        return None

    # A run that decoded no frame at all (ffmpeg hiccup) is not a verdict about the video.
    if key is not None and frames_seen > 0:
        with _cache_lock:
            _cache[key] = rect
    return rect


__all__ = [
    "aggregate_face_boxes",
    "clamp_rect",
    "clear_cache",
    "detect_facecam_rect",
    "expand_face_to_cam_rect",
    "find_facecam_rect",
    "sample_timestamps",
]
