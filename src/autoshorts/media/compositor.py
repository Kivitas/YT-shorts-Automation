"""Deterministic FFmpeg video compositor, preview generator, and audio extraction engine.

Strictly uses FFmpeg and FFprobe subprocess calls (zero MoviePy):
- Implements autoshorts.core.contracts.Compositor
- 9:16 vertical split-screen (1080x1920)
- Hardware probed encoding (h264_qsv or libx264 veryfast)
- Subtitles with Windows path escaping
- Background loops with random offsets or procedural lavfi fallback
- 60-second duration hard cap
- Progress reporting and timeout protection
- Atomic .tmp write then os.replace
- 540x960 15fps fast preview generator
- 16kHz mono WAV extraction helper
- Lazy-imported faster-whisper wrapper implementing contracts.Transcriber
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from autoshorts.core.config import Settings, load_config
from autoshorts.core.models import EditDecisionList
from autoshorts.core.paths import get_assets_dir
from autoshorts.core.render_spec import CAM_GAMEPLAY_LAYOUT, CamLayoutSpec
from autoshorts.media.cam_layout import cam_layout_from_config, resolve_cam_layout
from autoshorts.media.dopamine_effects import (
    DopaminePreset,
    build_color_grading_filter,
    build_complex_filtergraph,
    build_watermark_filter,
    find_user_audio_assets,
    get_dopamine_preset,
    pick_background_asset,
)
from autoshorts.media.downloader import (
    probe_video_stream_info,
    run_command_with_cleanup,
    terminate_process_tree,
)
from autoshorts.media.encoder_probe import (
    ENCODER_PROBE_TIMEOUT_SEC,
    EncoderInfo,
    clear_hardware_encoder_cache,
    detect_hardware_encoder,
    get_ffmpeg_path,
    get_ffprobe_path,
)

logger = logging.getLogger(__name__)

# ffprobe JSON probes get the same cold-start budget as encoder probes.
FFPROBE_TIMEOUT_SEC = ENCODER_PROBE_TIMEOUT_SEC
_FFPROBE_FALLBACK_WARNED = False


def _stderr_tail(text: str | bytes | None, limit: int = 2000) -> str:
    """Return the last `limit` characters of FFmpeg stderr for readable error messages."""
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    stripped = text.strip()
    return stripped[-limit:] if len(stripped) > limit else stripped


def _warn_ffprobe_missing_once() -> None:
    """Log once per process that stream and duration probes use the less reliable ffmpeg -i parse."""
    global _FFPROBE_FALLBACK_WARNED
    if _FFPROBE_FALLBACK_WARNED:
        return
    _FFPROBE_FALLBACK_WARNED = True
    logger.warning(
        "ffprobe is not installed (not on PATH and not next to the ffmpeg binary). "
        "Media durations and stream checks fall back to parsing `ffmpeg -i` output, "
        "which is less reliable. Install a full FFmpeg build that includes ffprobe."
    )


def _ffprobe_payload(target: Path) -> dict[str, Any] | None:
    """Run ffprobe with JSON output for `target`.

    Returns None when no ffprobe is available (the caller uses the logged ffmpeg fallback).
    Returns {} when ffprobe runs but fails, e.g. on a corrupt file; that means "no streams".
    """
    ffprobe_bin = get_ffprobe_path()
    if ffprobe_bin is None:
        return None
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(target),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FFPROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ffprobe failed to run for %s: %s", target, exc)
        return {}
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        logger.debug("ffprobe rejected %s (exit %s)", target, proc.returncode)
        return {}
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        logger.debug("ffprobe returned invalid JSON for %s: %s", target, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _ffprobe_streams(payload: dict[str, Any]) -> list[dict[str, Any]]:
    streams = payload.get("streams") or []
    return [s for s in streams if isinstance(s, dict)]


def _ffprobe_duration(payload: dict[str, Any]) -> float:
    """Largest finite positive duration reported by the container or any stream."""
    candidates: list[Any] = []
    fmt = payload.get("format")
    if isinstance(fmt, dict):
        candidates.append(fmt.get("duration"))
    candidates.extend(s.get("duration") for s in _ffprobe_streams(payload))
    best = 0.0
    for raw in candidates:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(val) and val > best:
            best = val
    return best


def _ffmpeg_fallback_info(target: Path) -> dict[str, Any]:
    """ffmpeg -i based probe (used only when ffprobe is missing). Logs the fallback once."""
    _warn_ffprobe_missing_once()
    try:
        return probe_video_stream_info(target)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        logger.debug("ffmpeg stream probe fallback failed for %s: %s", target, exc)
        return {}


def probe_media_duration(media_path: Path | str, default: float = 45.0) -> float:
    """Query duration of a media file in seconds.

    Uses ffprobe JSON output when get_ffprobe_path() finds ffprobe. Otherwise falls back
    (with a one-time warning) to parsing `ffmpeg -i` output via probe_video_stream_info.
    Returns `default` only when no positive, finite duration can be read at all.
    """
    target = Path(media_path).resolve()
    if not target.is_file():
        return default

    payload = _ffprobe_payload(target)
    if payload is None:
        dur = _ffmpeg_fallback_info(target).get("duration") or 0.0
    else:
        dur = _ffprobe_duration(payload)
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        dur = 0.0
    if math.isfinite(dur) and dur > 0:
        return dur

    logger.warning("Could not determine duration of %s; using default %.1fs", target, default)
    return default


def has_audio_stream(media_path: Path | str) -> bool:
    """Return True if media file contains at least one audio stream.

    With ffprobe present this reads the JSON stream list. Without it, the logged ffmpeg -i
    fallback decides. A False result is authoritative: silent files are never assumed to
    have audio.
    """
    target = Path(media_path).resolve()
    if not target.is_file():
        return False
    payload = _ffprobe_payload(target)
    if payload is None:
        return bool(_ffmpeg_fallback_info(target).get("has_audio"))
    return any(s.get("codec_type") == "audio" for s in _ffprobe_streams(payload))


def has_video_stream(media_path: Path | str) -> bool:
    """Return True if media file contains at least one video stream.

    Same contract as has_audio_stream: a corrupt or non-video file must not be accepted.
    """
    target = Path(media_path).resolve()
    if not target.is_file():
        return False
    payload = _ffprobe_payload(target)
    if payload is None:
        return bool(_ffmpeg_fallback_info(target).get("has_video"))
    return any(s.get("codec_type") == "video" for s in _ffprobe_streams(payload))


def extract_16khz_mono_wav(
    input_path: Path | str,
    output_wav: Path | str,
    start_sec: float | None = None,
    duration_sec: float | None = None,
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = 60.0,
) -> Path:
    """Extract a 16kHz 16-bit mono PCM WAV file from video or audio using FFmpeg."""
    src = Path(input_path).resolve()
    dest = Path(output_wav).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not src.is_file():
        raise FileNotFoundError(f"Input file not found for WAV extraction: {src}")

    target_bin = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
    cmd = [target_bin, "-y"]
    if start_sec is not None and start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.3f}"])
    cmd.extend(["-i", str(src)])
    if duration_sec is not None and duration_sec > 0:
        cmd.extend(["-t", f"{duration_sec:.3f}"])

    # 16kHz, mono, 16-bit pcm
    cmd.extend(
        [
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(dest),
        ]
    )

    try:
        proc = run_command_with_cleanup(
            cmd,
            timeout=timeout_sec,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"FFmpeg 16kHz WAV extraction failed (code {proc.returncode}):\n{_stderr_tail(proc.stderr)}"
            )
        return dest
    except FileNotFoundError as exc:
        raise RuntimeError(f"FFmpeg binary '{ffmpeg_binary}' not found on system PATH.") from exc


class FasterWhisperTranscriber:
    """Wrapper around faster-whisper int8 CPU transcription implementing contracts.Transcriber.

    Imports faster-whisper lazily so modules can load without strict whisper dependencies.
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        cpu_threads: int | None = None,
    ):
        settings = load_config()
        self.model_name = model_name or settings.media.whisper_model or "base.en"

        # Hardware auto-detection: CUDA GPU -> CPU multi-core
        if device is None:
            has_cuda = False
            try:
                import ctranslate2

                has_cuda = ctranslate2.get_cuda_device_count() > 0
            except Exception:
                try:
                    import torch

                    has_cuda = torch.cuda.is_available()
                except Exception:
                    pass
            device = "cuda" if has_cuda else "cpu"

        if compute_type is None:
            compute_type = "float16" if device == "cuda" else "int8"

        if cpu_threads is None:
            cpu_threads = max(1, (os.cpu_count() or 4) - 1) if device == "cpu" else 4

        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Install it with 'pip install faster-whisper' or 'uv pip install -e .[whisper]'"
            ) from exc

        logger.info(
            "Loading faster-whisper model '%s' (device=%s, compute_type=%s, threads=%d)",
            self.model_name,
            self.device,
            self.compute_type,
            self.cpu_threads,
        )
        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )
        return self._model

    def transcribe(
        self, audio_path: Path, initial_prompt: str | None = None
    ) -> list[dict[str, Any]]:
        """Transcribe an audio file into timed segments with word timestamps."""
        model = self._get_model()
        kwargs: dict[str, Any] = {
            "word_timestamps": True,
            "vad_filter": True,
        }
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        segments, _info = model.transcribe(
            str(audio_path),
            **kwargs,
        )
        results: list[dict[str, Any]] = []
        for seg in segments:
            words_list = []
            if seg.words:
                for w in seg.words:
                    words_list.append(
                        {
                            "word": w.word,
                            "start": float(w.start),
                            "end": float(w.end),
                            "probability": float(w.probability),
                        }
                    )
            results.append(
                {
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": seg.text.strip(),
                    "words": words_list,
                }
            )
        return results

    def align_words(
        self,
        audio_path: Path,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Extract precise word-level start and end timestamps for karaoke subtitle styling."""
        segments = self.transcribe(audio_path, initial_prompt=text if text else None)
        words: list[dict[str, Any]] = []
        for seg in segments:
            words.extend(seg.get("words", []))
        if text and words:
            ref_tokens = text.split()
            if ref_tokens:
                import difflib

                whisper_tokens = [w["word"].strip() for w in words]
                matcher = difflib.SequenceMatcher(
                    None,
                    [t.lower().strip(".,!?:;\"'") for t in whisper_tokens],
                    [r.lower().strip(".,!?:;\"'") for r in ref_tokens],
                )
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag == "equal" and (i2 - i1 == j2 - j1):
                        for idx_w, idx_r in zip(range(i1, i2), range(j1, j2), strict=False):
                            words[idx_w]["word"] = ref_tokens[idx_r]
        return words


def detect_encoder_pixel_format(
    codec: str,
    extra_args: list[str] | None = None,
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = 2.0,
) -> str:
    """Detect whether encoder supports yuv420p or falls back to nv12."""
    if codec == "libx264":
        return "yuv420p"

    resolved_bin = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
    extra = extra_args or []

    # First test standard yuv420p
    cmd_yuv = [
        resolved_bin,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=256x256:d=0.1",
        "-c:v",
        codec,
        *extra,
        "-pix_fmt",
        "yuv420p",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd_yuv, capture_output=True, timeout=timeout_sec, check=False)
        if proc.returncode == 0:
            return "yuv420p"
    except Exception:
        pass

    # If yuv420p fails, test nv12 (widely required/preferred by Intel QSV & NVIDIA NVENC)
    cmd_nv12 = [
        resolved_bin,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=256x256:d=0.1",
        "-c:v",
        codec,
        *extra,
        "-pix_fmt",
        "nv12",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd_nv12, capture_output=True, timeout=timeout_sec, check=False)
        if proc.returncode == 0:
            return "nv12"
    except Exception:
        pass

    return "yuv420p"


def score_frame_visual_quality(ppm_bytes: bytes) -> dict[str, float]:
    """Score frame visual quality from raw 24-bit RGB PPM image bytes.

    Evaluates:
    1. Visual sharpness: high-frequency spatial edge gradients
    2. Facial contrast: luminance dynamic range in upper quadrant (speaker face region)
    3. Hook banner visibility: high-contrast text edge density in subtitle region

    Returns metrics dictionary with blended quality score in [0.0, 1.0].
    """
    if not ppm_bytes or not ppm_bytes.startswith(b"P6"):
        return {
            "score": 0.5,
            "sharpness": 0.5,
            "facial_contrast": 0.5,
            "hook_banner": 0.5,
        }

    parts = ppm_bytes.split(b"\n", 3)
    if len(parts) < 4:
        return {
            "score": 0.5,
            "sharpness": 0.5,
            "facial_contrast": 0.5,
            "hook_banner": 0.5,
        }

    dim_line = parts[1].split()
    if len(dim_line) < 2:
        return {
            "score": 0.5,
            "sharpness": 0.5,
            "facial_contrast": 0.5,
            "hook_banner": 0.5,
        }

    w, h = int(dim_line[0]), int(dim_line[1])
    pixel_data = parts[3]
    stride = w * 3
    step = 2

    grad_sum = 0
    grad_cnt = 0
    face_y_samples: list[int] = []
    banner_edges = 0
    banner_samples = 0

    for y in range(0, h - 1, step):
        row_offset = y * stride
        next_row_offset = (y + 1) * stride
        in_face_band = (0.15 * h) <= y <= (0.60 * h)
        in_banner_band = (0.35 * h) <= y <= (0.75 * h)

        for x in range(0, w - 1, step):
            idx = row_offset + x * 3
            if idx + 5 >= len(pixel_data) or next_row_offset + x * 3 + 2 >= len(pixel_data):
                continue
            r, g, b = pixel_data[idx], pixel_data[idx + 1], pixel_data[idx + 2]
            lum = (r * 77 + g * 150 + b * 29) >> 8

            r_right, g_right, b_right = (
                pixel_data[idx + 3],
                pixel_data[idx + 4],
                pixel_data[idx + 5],
            )
            lum_right = (r_right * 77 + g_right * 150 + b_right * 29) >> 8

            r_down = pixel_data[next_row_offset + x * 3]
            g_down = pixel_data[next_row_offset + x * 3 + 1]
            b_down = pixel_data[next_row_offset + x * 3 + 2]
            lum_down = (r_down * 77 + g_down * 150 + b_down * 29) >> 8

            h_diff = abs(lum - lum_right)
            v_diff = abs(lum - lum_down)
            grad_sum += h_diff + v_diff
            grad_cnt += 2

            if in_face_band:
                face_y_samples.append(lum)
            if in_banner_band:
                banner_samples += 1
                if h_diff > 45:
                    banner_edges += 1

    avg_grad = grad_sum / max(1, grad_cnt)
    sharpness = min(1.0, avg_grad / 20.0)

    if face_y_samples:
        mean_face = sum(face_y_samples) / len(face_y_samples)
        variance = sum((val - mean_face) ** 2 for val in face_y_samples) / len(face_y_samples)
        std_face = variance**0.5
        facial_contrast = min(1.0, std_face / 45.0)
    else:
        facial_contrast = 0.5

    banner_density = banner_edges / max(1, banner_samples)
    hook_banner = min(1.0, banner_density / 0.08)

    total = 0.40 * sharpness + 0.35 * facial_contrast + 0.25 * hook_banner
    return {
        "score": round(total, 4),
        "sharpness": round(sharpness, 4),
        "facial_contrast": round(facial_contrast, 4),
        "hook_banner": round(hook_banner, 4),
    }


def find_best_thumbnail_timestamp(
    video_path: Path | str,
    ffmpeg_binary: str = "ffmpeg",
    search_window_sec: float = 2.5,
    sample_count: int = 5,
    fallback_ts: float = 1.2,
) -> float:
    """Evaluate candidate frames in the first search_window_sec for visual sharpness,

    facial contrast, and hook banner visibility. Returns the optimal timestamp.
    """
    src = Path(video_path).resolve()
    if not src.is_file():
        return fallback_ts

    clip_dur = probe_media_duration(src, default=0.0)
    effective_window = min(clip_dur if clip_dur > 0 else search_window_sec, search_window_sec)
    if effective_window <= 0.3:
        return 0.1

    step = effective_window / (sample_count + 1)
    candidates = [round(step * (i + 1), 2) for i in range(sample_count)]

    best_ts = candidates[len(candidates) // 2] if candidates else fallback_ts
    best_score = -1.0

    target_bin = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary

    for ts in candidates:
        cmd = [
            target_bin,
            "-y",
            "-v",
            "error",
            "-ss",
            f"{ts:.2f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-s",
            "180x320",
            "-pix_fmt",
            "rgb24",
            # image2pipe defaults to MJPEG; score_frame_visual_quality needs raw P6 PPM bytes.
            "-c:v",
            "ppm",
            "-f",
            "image2pipe",
            "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=5.0, check=False)
            if proc.returncode == 0 and len(proc.stdout) > 100:
                metrics = score_frame_visual_quality(proc.stdout)
                score = metrics["score"]
                if score > best_score:
                    best_score = score
                    best_ts = ts
        except Exception as exc:
            logger.debug("Failed probing thumbnail candidate at %.2fs: %s", ts, exc)
            continue

    return best_ts


def stage_subtitles_for_filter(sub_path: Path, dest: Path, job_id: str) -> Path | None:
    """Return a copy of the ASS file whose path is safe inside a subtitles= filter argument.

    escape_ffmpeg_filter_path wraps the path in single quotes and does not escape quotes,
    so an apostrophe in the path (e.g. C:\\Users\\O'Brien\\...) breaks the filtergraph.
    Returns None when the original path can be used as-is; otherwise copies the file to
    the output directory (or the system temp dir) and returns the copy. The caller must
    unlink the returned file.
    """
    if "'" not in str(sub_path):
        return None

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", job_id)[:48] or "job"
    for directory in (dest.parent, Path(tempfile.gettempdir())):
        if "'" in str(directory) or not directory.is_dir():
            continue
        staged = directory / f".tmp_{safe_id}_subs.ass"
        shutil.copyfile(sub_path, staged)
        return staged

    raise RuntimeError(
        f"Subtitles path contains an apostrophe and no quote-free staging directory is "
        f"available: {sub_path}"
    )


def count_decode_errors(media_path: Path | str, timeout_sec: float = 300.0) -> int:
    """Fully decode a rendered file and return how many error lines FFmpeg reports (0 = clean)."""
    proc = subprocess.run(
        [get_ffmpeg_path(), "-v", "error", "-i", str(media_path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )
    return len([line for line in proc.stderr.splitlines() if line.strip()])


def _edl_on_trimmed_timeline(
    edl: EditDecisionList | None, hook_in: float
) -> EditDecisionList | None:
    """Copy of ``edl`` whose jump cuts and zoom punch-ins are relative to a ``-ss hook_in`` input."""
    if edl is None or hook_in <= 0:
        return edl
    from autoshorts.media.ass_styler import shift_jump_cuts_for_hook

    punches = []
    for p in edl.zoom_punch_ins:
        if p.timestamp + p.duration <= hook_in:
            continue
        punches.append(p.model_copy(update={"timestamp": max(0.0, p.timestamp - hook_in)}))
    return edl.model_copy(
        update={
            "jump_cuts": shift_jump_cuts_for_hook(edl.jump_cuts, hook_in),
            "zoom_punch_ins": punches,
        }
    )


def _discard_partial_output(path: Path) -> None:
    """Remove a partial render, ignoring failures so the original error is what surfaces."""
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove partial render %s: %s", path, exc)


# Video arguments for the libx264 CPU retry after a hardware encoder fails at runtime.
_CPU_FALLBACK_VIDEO_ARGS: tuple[str, ...] = (
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-crf",
    "22",
    "-threads",
    "4",
    "-pix_fmt",
    "yuv420p",
)


@dataclass(frozen=True)
class _TrimPlan:
    """Source timing for one render: probed streams, the EDL hook window and the output length."""

    has_src_audio: bool
    has_src_video: bool
    hook_in: float
    hook_out: float
    target_duration: float


@dataclass(frozen=True)
class _ComposeStyle:
    """Effective per-render styling: explicit arguments, then config branding, audio and job."""

    layout: str
    crop_focus: str
    color_grading: str
    watermark_text: str | None
    watermark_position: str
    watermark_opacity: float
    progress_bar_position: str
    progress_bar_color: str
    voice_boost: bool
    target_lufs: float
    bg_music_volume: float
    bgm_path: Path | str | None
    split_separator: str | None
    branding_split_separator: str | None


@dataclass(frozen=True)
class _InputPlan:
    """ffmpeg input arguments in order (source, background, music, sfx) and the stream indices."""

    args: list[str]
    is_single_source: bool
    music_index: int | None
    sfx_index: int | None


@dataclass(frozen=True)
class _ChyronPlan:
    """Resolved broadcast lower-third settings for one render, passed on to the filtergraph."""

    enabled: bool | None
    badge: str | None
    text: str | None
    style: str | None


class FFmpegCompositor:
    """High-efficiency deterministic FFmpeg compositor for vertical 9:16 YouTube Shorts.

    Implements autoshorts.core.contracts.Compositor.
    """

    def __init__(
        self,
        config: Settings | None = None,
        encoder_preferred: str = "auto",
        ffmpeg_binary: str = "ffmpeg",
    ):
        self.config = config or load_config()
        self.ffmpeg_binary = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
        self.encoder_preferred = encoder_preferred or self.config.media.preferred_encoder

    def _get_encoder(self) -> EncoderInfo:
        """Resolve and verify the fastest available hardware or software video encoder."""
        return detect_hardware_encoder(
            preferred=self.encoder_preferred,
            ffmpeg_binary=self.ffmpeg_binary,
        )

    def compose(
        self,
        job_id: str,
        source_clip: Path,
        edl: EditDecisionList | None,
        background_path: Path | str | None,
        subtitles_path: Path | str,
        output_path: Path | str,
        dopamine_level: str | None = None,
        progress_callback: Callable[[float], None] | None = None,
        timeout_sec: float = 300.0,
        layout_mode: str | None = None,
        color_grading: str | None = None,
        watermark_text: str | None = None,
        watermark_position: str | None = None,
        watermark_opacity: float | None = None,
        progress_bar_position: str | None = None,
        progress_bar_color: str | None = None,
        voice_boost: bool | None = None,
        target_lufs: float | None = None,
        bg_music_volume: float | None = None,
        crop_focus: str = "center",
        split_separator: str | None = None,
        bg_music_path: Path | str | None = None,
        bgm_path: Path | str | None = None,
        bgm_volume: float | None = None,
        rhythm_ducking_release: int | None = None,
        background_offset: float | None = None,
        enable_chyron: bool | None = None,
        chyron_badge: str | None = None,
        chyron_text: str | None = None,
        chyron_style: str | None = None,
        **kwargs: Any,
    ) -> Path:
        """Render a vertical 1080x1920 Short MP4 via FFmpeg executing the EDL.

        background_offset: start time in the background clip, as returned by
        select_gameplay_background. A random offset is used when it is None.

        cam_layout (keyword, via **kwargs): a CamLayoutSpec for layout_mode="cam_gameplay"
        (streamer camera on top, gameplay below, no gameplay background). Without one it is
        built from config.branding.cam_layout; if no camera region is usable the render
        degrades to split_50_50 with a logged warning.

        Atomic write: renders to .tmp.mp4 first then renames via os.replace.
        """
        src, sub_path, dest = self._prepare_paths(source_clip, subtitles_path, output_path)
        job = kwargs.get("job")
        style = self._resolve_style(
            job=job,
            layout_mode=layout_mode,
            crop_focus=crop_focus,
            color_grading=color_grading,
            watermark_text=watermark_text,
            watermark_position=watermark_position,
            watermark_opacity=watermark_opacity,
            progress_bar_position=progress_bar_position,
            progress_bar_color=progress_bar_color,
            voice_boost=voice_boost,
            target_lufs=target_lufs,
            bg_music_volume=bg_music_volume,
            split_separator=split_separator,
            bg_music_path=bg_music_path,
            bgm_path=bgm_path,
            bgm_volume=bgm_volume,
        )
        trim = self._plan_trim(src, edl)
        style, cam = self._resolve_cam_layout(style, kwargs.get("cam_layout"), src, trim)
        logger.info(
            "Job %s: Rendering short with target duration %.2fs (trim: %.2fs to %.2fs, layout=%s, has_v=%s, has_a=%s)",
            job_id,
            trim.target_duration,
            trim.hook_in,
            trim.hook_out,
            style.layout,
            trim.has_src_video,
            trim.has_src_audio,
        )

        preset, enable_ducking = self._resolve_preset(dopamine_level, style.bgm_path)
        encoder = self._get_encoder()
        chyron = self._resolve_chyron(
            enable_chyron=enable_chyron,
            chyron_badge=chyron_badge,
            chyron_text=chyron_text,
            chyron_style=chyron_style,
            job=job,
            preset=preset,
        )
        inputs = self._build_inputs(
            src=src,
            edl=edl,
            background_path=background_path,
            background_offset=background_offset,
            trim=trim,
            style=style,
            preset=preset,
            enable_ducking=enable_ducking,
            cam=cam,
        )
        fonts_param = self._fonts_dir_param()
        effective_release = self._resolve_ducking_release(rhythm_ducking_release, job)
        graph_edl = _edl_on_trimmed_timeline(edl, trim.hook_in)

        # The subtitles path is staged (copied) when it contains an apostrophe. The staged copy
        # is removed in the finally block below.
        staged_sub = stage_subtitles_for_filter(sub_path, dest, job_id)
        filtergraph, out_v, out_a = self._build_filtergraph(
            style=style,
            preset=preset,
            trim=trim,
            inputs=inputs,
            graph_edl=graph_edl,
            subtitles_path=staged_sub or sub_path,
            fonts_param=fonts_param,
            split_separator=self._resolve_split_separator(style, preset),
            chyron=chyron,
            rhythm_ducking_release=effective_release,
            cam=cam,
        )

        cmd = [
            self.ffmpeg_binary,
            "-y",
            *inputs.args,
            "-filter_complex",
            filtergraph,
            "-map",
            out_v,
            "-map",
            out_a,
        ]
        cmd.extend(self._video_encoder_args(encoder))
        # Audio encoding parameters
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "44100"])
        # Hard cap at 60s and shortest stream
        cmd.extend(["-t", f"{trim.target_duration:.3f}", "-shortest"])
        cmd.extend(["-max_muxing_queue_size", "1024"])
        # Move the moov atom to the front so the Short starts playing before full download
        cmd.extend(["-movflags", "+faststart"])
        # Temporary atomic destination
        tmp_dest = dest.with_name(f".tmp_{job_id}_{dest.name}")
        cmd.append(str(tmp_dest))

        try:
            return self._render_and_publish(cmd, encoder, tmp_dest, dest, job_id, timeout_sec)
        except subprocess.TimeoutExpired as exc:
            _discard_partial_output(tmp_dest)
            raise TimeoutError(
                f"FFmpeg compositing for job {job_id} exceeded timeout ({timeout_sec}s)"
            ) from exc
        except Exception:
            # Cleanup must never hide the original failure (on Windows the file may be locked).
            _discard_partial_output(tmp_dest)
            raise
        finally:
            if staged_sub is not None:
                staged_sub.unlink(missing_ok=True)

    @staticmethod
    def _prepare_paths(
        source_clip: Path | str, subtitles_path: Path | str, output_path: Path | str
    ) -> tuple[Path, Path, Path]:
        """Resolve the input and output paths, create the output directory, check inputs exist."""
        src = Path(source_clip).resolve()
        sub_path = Path(subtitles_path).resolve()
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not src.is_file():
            raise FileNotFoundError(f"Source clip not found: {src}")
        if not sub_path.is_file():
            raise FileNotFoundError(f"Subtitles file not found: {sub_path}")
        return src, sub_path, dest

    def _resolve_style(
        self,
        *,
        job: Any,
        layout_mode: str | None,
        crop_focus: str,
        color_grading: str | None,
        watermark_text: str | None,
        watermark_position: str | None,
        watermark_opacity: float | None,
        progress_bar_position: str | None,
        progress_bar_color: str | None,
        voice_boost: bool | None,
        target_lufs: float | None,
        bg_music_volume: float | None,
        split_separator: str | None,
        bg_music_path: Path | str | None,
        bgm_path: Path | str | None,
        bgm_volume: float | None,
    ) -> _ComposeStyle:
        """Resolve styling: explicit arguments first, then config branding, audio and job defaults."""
        branding = getattr(self.config, "branding", None)
        audio_cfg = getattr(self.config, "audio_mastering", None)
        job_defaults = getattr(self.config, "job_defaults", None)

        effective_bgm = (
            bg_music_path or bgm_path or (getattr(job, "bgm_path", None) if job else None)
        )
        effective_vol = (
            bg_music_volume
            if bg_music_volume is not None
            else (
                bgm_volume
                if bgm_volume is not None
                else (getattr(job, "bgm_volume", None) if job else None)
            )
        )
        return _ComposeStyle(
            layout=layout_mode or getattr(job_defaults, "layout_mode", None) or "split_50_50",
            crop_focus=crop_focus or getattr(job_defaults, "crop_focus", None) or "center",
            color_grading=color_grading or getattr(branding, "color_grading", None) or "none",
            watermark_text=(
                watermark_text
                if watermark_text is not None
                else getattr(branding, "watermark_text", None)
            ),
            watermark_position=(
                watermark_position or getattr(branding, "watermark_position", None) or "top_right"
            ),
            watermark_opacity=(
                watermark_opacity
                if watermark_opacity is not None
                else getattr(branding, "watermark_opacity", 0.65)
            ),
            progress_bar_position=(
                progress_bar_position
                or getattr(branding, "progress_bar_position", None)
                or "bottom"
            ),
            progress_bar_color=(
                progress_bar_color or getattr(branding, "progress_bar_color", None) or "red@0.8"
            ),
            voice_boost=(
                voice_boost if voice_boost is not None else getattr(audio_cfg, "voice_boost", True)
            ),
            target_lufs=(
                target_lufs if target_lufs is not None else getattr(audio_cfg, "target_lufs", -14.0)
            ),
            bg_music_volume=(
                effective_vol
                if effective_vol is not None
                else getattr(audio_cfg, "bg_music_volume", 0.18)
            ),
            bgm_path=effective_bgm,
            split_separator=split_separator,
            branding_split_separator=getattr(branding, "split_separator", None),
        )

    @staticmethod
    def _plan_trim(src: Path, edl: EditDecisionList | None) -> _TrimPlan:
        """Probe the source streams and duration, then apply the EDL hook window."""
        has_src_audio = has_audio_stream(src)
        has_src_video = has_video_stream(src)

        clip_duration = probe_media_duration(src)
        hook_in = 0.0
        hook_out = clip_duration

        if edl:
            if edl.hook_in_sec > 0:
                hook_in = edl.hook_in_sec
            if edl.hook_out_sec > hook_in:
                hook_out = min(clip_duration, edl.hook_out_sec)

        target_duration = max(1.0, min(60.0, hook_out - hook_in))
        return _TrimPlan(
            has_src_audio=has_src_audio,
            has_src_video=has_src_video,
            hook_in=hook_in,
            hook_out=hook_out,
            target_duration=target_duration,
        )

    def _resolve_preset(
        self, dopamine_level: str | None, bgm_path: Path | str | None
    ) -> tuple[DopaminePreset, bool]:
        """Return the dopamine preset and whether music ducking is on (an explicit BGM forces it)."""
        level = dopamine_level or self.config.job_defaults.dopamine_level
        preset = get_dopamine_preset(level)
        enable_ducking = bool(bgm_path) or getattr(preset, "enable_music_ducking", False)
        if enable_ducking and not getattr(preset, "enable_music_ducking", False):
            preset = replace(preset, enable_music_ducking=True)
        return preset, enable_ducking

    @staticmethod
    def _source_input_args(src: Path, trim: _TrimPlan) -> list[str]:
        """Input 0: the source clip, seeked to the hook start and limited to the target duration."""
        args: list[str] = []
        if trim.hook_in > 0:
            args.extend(["-ss", f"{trim.hook_in:.3f}"])
        args.extend(["-t", f"{trim.target_duration:.3f}", "-i", str(src)])
        return args

    def _background_input_args(
        self,
        background_path: Path | str | None,
        background_offset: float | None,
        edl: EditDecisionList | None,
        trim: _TrimPlan,
    ) -> list[str]:
        """Input 1 for multi-source layouts: a gameplay clip, an asset pick, or a lavfi gradient."""
        target_duration = trim.target_duration
        bg_file: Path | None = None
        bg_offset = 0.0

        bg_reject_reason: str | None = None
        if background_path:
            p = Path(background_path).resolve()
            if not p.is_file():
                bg_reject_reason = "file missing"
            elif not has_video_stream(p):
                bg_reject_reason = "no video stream"
            else:
                bg_file = p
                bg_dur = probe_media_duration(p)
                max_offset = max(0.0, bg_dur - target_duration)
                if background_offset is not None and background_offset >= 0:
                    bg_offset = min(float(background_offset), max_offset)
                else:
                    bg_offset = random.uniform(0.0, min(180.0, max_offset))
        elif background_path is None:
            bg_reject_reason = "no background selected"

        if bg_file is None:
            # Try to pick from assets directory based on EDL or config
            bg_style = (
                (edl.background_style_recommendation if edl else None)
                or self.config.job_defaults.background_category
                or "minecraft"
            )
            cand_file, cand_offset = pick_background_asset(category=bg_style, auto_fetch=False)
            if cand_file and cand_file.is_file() and has_video_stream(cand_file):
                bg_file = cand_file
                bg_offset = cand_offset

        if bg_file and bg_file.is_file():
            return [
                "-ss",
                f"{bg_offset:.3f}",
                "-stream_loop",
                "-1",
                "-t",
                f"{target_duration:.3f}",
                "-i",
                str(bg_file),
            ]

        # Procedural fallback background via lavfi color/gradients
        logger.warning(
            "Gameplay background unavailable (path=%s, reason=%s); "
            "rendering procedural gradient instead.",
            background_path,
            bg_reject_reason or "no usable background in assets",
        )
        lavfi_dims = "1080x1920" if not trim.has_src_video else "1080x960"
        return [
            "-f",
            "lavfi",
            "-t",
            f"{target_duration:.3f}",
            "-i",
            f"color=c=0x16213e:s={lavfi_dims}:r=30:d={target_duration:.3f}",
        ]

    @staticmethod
    def _cam_file_input_args(cam: CamLayoutSpec, trim: _TrimPlan) -> list[str]:
        """Input 1 for cam_source="file": the camera recording, aligned with the source trim."""
        args: list[str] = []
        if trim.hook_in > 0:
            args.extend(["-ss", f"{trim.hook_in:.3f}"])
        args.extend(["-stream_loop", "-1", "-t", f"{trim.target_duration:.3f}"])
        args.extend(["-i", str(cam.cam_file)])
        return args

    def _resolve_cam_layout(
        self,
        style: _ComposeStyle,
        requested: CamLayoutSpec | None,
        src: Path,
        trim: _TrimPlan,
    ) -> tuple[_ComposeStyle, CamLayoutSpec | None]:
        """For layout cam_gameplay: a renderable CamLayoutSpec, else degrade to split_50_50."""
        if style.layout != CAM_GAMEPLAY_LAYOUT:
            return style, None
        cam_cfg = getattr(getattr(self.config, "branding", None), "cam_layout", None)
        spec = requested or (cam_layout_from_config(cam_cfg) if cam_cfg is not None else None)
        resolved = (
            resolve_cam_layout(
                spec,
                source=src,
                auto_detect=bool(getattr(cam_cfg, "auto_detect", True)),
                cam_file_ok=has_video_stream,
            )
            if trim.has_src_video
            else None
        )
        if resolved is None:
            return replace(style, layout="split_50_50"), None
        return style, resolved

    @staticmethod
    def _audio_asset_input_args(
        preset: DopaminePreset,
        enable_ducking: bool,
        bgm_path: Path | str | None,
        target_duration: float,
        first_index: int,
    ) -> tuple[list[str], int | None, int | None]:
        """Optional music bed (when ducking) and sfx inputs. Returns (args, music_idx, sfx_idx)."""
        args: list[str] = []
        music_idx: int | None = None
        sfx_idx: int | None = None
        next_index = first_index

        if enable_ducking:
            chosen_music: Path | str | None = None
            if bgm_path:
                chosen_music = bgm_path
            else:
                music_files = find_user_audio_assets("music")
                if music_files:
                    chosen_music = random.choice(music_files)

            if chosen_music:
                args.extend(
                    ["-stream_loop", "-1", "-t", f"{target_duration:.3f}", "-i", str(chosen_music)]
                )
                music_idx = next_index
                next_index += 1

        if preset.enable_sfx:
            sfx_files = find_user_audio_assets("sfx")
            if sfx_files:
                chosen_sfx = random.choice(sfx_files)
                args.extend(["-i", str(chosen_sfx)])
                sfx_idx = next_index

        return args, music_idx, sfx_idx

    def _build_inputs(
        self,
        *,
        src: Path,
        edl: EditDecisionList | None,
        background_path: Path | str | None,
        background_offset: float | None,
        trim: _TrimPlan,
        style: _ComposeStyle,
        preset: DopaminePreset,
        enable_ducking: bool,
        cam: CamLayoutSpec | None = None,
    ) -> _InputPlan:
        """Assemble ffmpeg inputs in order: source, background (multi-source only), music, sfx.

        With a resolved cam layout there is no gameplay background: a crop-mode camera needs no
        extra input and a file-mode camera recording takes the background's input slot.
        """
        args = self._source_input_args(src, trim)
        cam_from_file = cam is not None and cam.cam_source == "file"
        # Single source vs multi-source layouts (audio-only source cannot be single-source)
        is_single_source = (
            (style.layout in ("full_blur_bg", "full_center_crop")) and trim.has_src_video
        ) or (cam is not None and not cam_from_file)
        next_index = 1 if is_single_source else 2
        if cam_from_file:
            args.extend(self._cam_file_input_args(cam, trim))
        elif not is_single_source:
            args.extend(self._background_input_args(background_path, background_offset, edl, trim))
        audio_args, music_idx, sfx_idx = self._audio_asset_input_args(
            preset, enable_ducking, style.bgm_path, trim.target_duration, next_index
        )
        args.extend(audio_args)
        return _InputPlan(
            args=args,
            is_single_source=is_single_source,
            music_index=music_idx,
            sfx_index=sfx_idx,
        )

    @staticmethod
    def _fonts_dir_param() -> Path | None:
        """The assets fonts dir for the subtitles filter, or None when it is absent or unusable."""
        fonts_dir = get_assets_dir() / "fonts"
        fonts_param = fonts_dir if fonts_dir.is_dir() else None
        if fonts_param is not None and "'" in str(fonts_param):
            logger.warning(
                "Fonts dir %s contains an apostrophe and cannot be used in the subtitles filter; "
                "rendering without fontsdir.",
                fonts_param,
            )
            fonts_param = None
        return fonts_param

    @staticmethod
    def _resolve_split_separator(style: _ComposeStyle, preset: DopaminePreset) -> str:
        """Split separator style: explicit argument, then branding, then the preset."""
        return (
            style.split_separator
            or style.branding_split_separator
            or (
                preset.split_separator_style
                if getattr(preset, "enable_split_separator", False)
                else "none"
            )
        )

    @staticmethod
    def _resolve_ducking_release(rhythm_ducking_release: int | None, job: Any) -> int | None:
        """Ducking release in ms: explicit argument, then the job's rhythm metadata."""
        # A falsy explicit value (including 0) counts as unset, as it always has.
        effective_release = rhythm_ducking_release or None
        if effective_release is None and job:
            r_meta = getattr(job, "rhythm_metadata", None)
            if isinstance(r_meta, dict):
                effective_release = r_meta.get("ducking_release_ms") or r_meta.get("release_ms")
        return effective_release

    def _resolve_chyron(
        self,
        *,
        enable_chyron: bool | None,
        chyron_badge: str | None,
        chyron_text: str | None,
        chyron_style: str | None,
        job: Any,
        preset: DopaminePreset,
    ) -> _ChyronPlan:
        """Resolve lower-third chyron settings: arguments first, then branding, job and preset."""
        branding = getattr(self.config, "branding", None)
        # Resolve broadcast lower-third chyron defaults
        eff_enable_chyron = (
            enable_chyron
            if enable_chyron is not None
            else (
                getattr(branding, "enable_chyron", None)
                if getattr(branding, "enable_chyron", None) is not None
                else getattr(preset, "enable_chyron", False)
            )
        )
        eff_chyron_badge = (
            chyron_badge
            or getattr(branding, "chyron_badge", None)
            or getattr(preset, "chyron_badge", "BREAKING NEWS")
        )
        eff_chyron_text = (
            chyron_text
            if chyron_text is not None
            else (
                getattr(branding, "chyron_text", None)
                if hasattr(branding, "chyron_text")
                and getattr(branding, "chyron_text", None) is not None
                else (
                    getattr(job, "chyron_text", None)
                    if job and hasattr(job, "chyron_text")
                    else (
                        getattr(job, "diagnostics", {}).get("chyron_text")
                        if job and isinstance(getattr(job, "diagnostics", None), dict)
                        else None
                    )
                )
            )
        )
        eff_chyron_style = (
            chyron_style
            or getattr(branding, "chyron_style", None)
            or getattr(preset, "chyron_style", "broadcast_red")
        )
        return _ChyronPlan(
            enabled=eff_enable_chyron,
            badge=eff_chyron_badge,
            text=eff_chyron_text,
            style=eff_chyron_style,
        )

    @staticmethod
    def _build_filtergraph(
        *,
        style: _ComposeStyle,
        preset: DopaminePreset,
        trim: _TrimPlan,
        inputs: _InputPlan,
        graph_edl: EditDecisionList | None,
        subtitles_path: Path,
        fonts_param: Path | None,
        split_separator: str,
        chyron: _ChyronPlan,
        rhythm_ducking_release: int | None,
        cam: CamLayoutSpec | None = None,
    ) -> tuple[str, str, str]:
        """Build the complex filtergraph. Returns (filtergraph, video label, audio label)."""
        filtergraph, out_v, out_a = build_complex_filtergraph(
            source_clip_has_audio=trim.has_src_audio,
            subtitles_path=subtitles_path,
            duration_sec=trim.target_duration,
            dopamine_preset=preset,
            edl=graph_edl,
            fonts_dir=fonts_param,
            has_background_video=not inputs.is_single_source,
            music_input_index=inputs.music_index,
            sfx_input_index=inputs.sfx_index,
            layout_mode=style.layout,
            color_grading=style.color_grading,
            watermark_text=style.watermark_text,
            watermark_position=style.watermark_position,
            watermark_opacity=style.watermark_opacity,
            progress_bar_position=style.progress_bar_position,
            progress_bar_color=style.progress_bar_color,
            voice_boost=style.voice_boost,
            target_lufs=style.target_lufs,
            bg_music_volume=style.bg_music_volume,
            crop_focus=style.crop_focus,
            split_separator=split_separator,
            source_clip_has_video=trim.has_src_video,
            rhythm_ducking_release=rhythm_ducking_release,
            enable_chyron=chyron.enabled,
            chyron_badge=chyron.badge,
            chyron_text=chyron.text,
            chyron_style=chyron.style,
            cam_layout=cam,
        )

        if rhythm_ducking_release is not None and "release=300" in filtergraph:
            filtergraph = filtergraph.replace(
                "release=300", f"release={int(rhythm_ducking_release)}"
            )
        return filtergraph, out_v, out_a

    def _video_encoder_args(self, encoder: EncoderInfo) -> list[str]:
        """Video encoder arguments, with the pixel format swapped to what the encoder accepts."""
        enc_args = list(encoder.get_ffmpeg_args())
        pix_fmt = detect_encoder_pixel_format(
            encoder.codec,
            encoder.extra_args,
            ffmpeg_binary=self.ffmpeg_binary,
        )
        if pix_fmt != "yuv420p" and "-pix_fmt" in enc_args:
            idx = enc_args.index("-pix_fmt")
            if idx + 1 < len(enc_args):
                enc_args[idx + 1] = pix_fmt
        return enc_args

    @staticmethod
    def _run_with_cpu_fallback(
        cmd: list[str], encoder: EncoderInfo, timeout_sec: float
    ) -> tuple[subprocess.CompletedProcess[str], float]:
        """Run ffmpeg. A failed hardware encode is retried once with libx264.

        Returns (process, seconds the first attempt took). The elapsed time excludes the retry.
        """
        start_time = time.time()
        proc = run_command_with_cleanup(cmd, timeout=timeout_sec)
        elapsed = time.time() - start_time
        if proc.returncode != 0 and encoder.is_hardware_accelerated:
            logger.warning(
                "Hardware encoder %s failed (code %d). Retrying with libx264 CPU fallback...",
                encoder.codec,
                proc.returncode,
            )
            if "-c:v" in cmd:
                c_idx = cmd.index("-c:v")
                a_idx = cmd.index("-c:a") if "-c:a" in cmd else len(cmd) - 1
                cmd_fallback = cmd[:c_idx] + list(_CPU_FALLBACK_VIDEO_ARGS) + cmd[a_idx:]
                proc = run_command_with_cleanup(cmd_fallback, timeout=timeout_sec)
        return proc, elapsed

    def _render_and_publish(
        self,
        cmd: list[str],
        encoder: EncoderInfo,
        tmp_dest: Path,
        dest: Path,
        job_id: str,
        timeout_sec: float,
    ) -> Path:
        """Run ffmpeg, check the temporary output, and atomically move it into place."""
        logger.debug("Executing FFmpeg command: %s", " ".join(cmd))
        proc, elapsed = self._run_with_cpu_fallback(cmd, encoder, timeout_sec)

        if proc.returncode != 0:
            if tmp_dest.is_file():
                tmp_dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"FFmpeg compositing failed (exit code {proc.returncode}):\n"
                f"{_stderr_tail(proc.stderr)}"
            )

        # Atomic rename on success
        if not tmp_dest.is_file():
            raise RuntimeError(f"FFmpeg completed but output file was not created: {tmp_dest}")

        os.replace(tmp_dest, dest)
        logger.info("Compositing job %s completed in %.1fs -> %s", job_id, elapsed, dest)
        return dest

    def generate_preview(
        self,
        source_clip: Path | str,
        output_path: Path | str,
        max_duration_sec: float = 30.0,
        timeout_sec: float = 30.0,
        layout_mode: str | None = None,
        color_grading: str | None = None,
        watermark_text: str | None = None,
        watermark_position: str | None = None,
        watermark_opacity: float | None = None,
        **kwargs: Any,
    ) -> Path:
        """Render a rapid 540x960 15fps low-resolution preview MP4 in <6s for dashboard scrub."""
        src = Path(source_clip).resolve()
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not src.is_file():
            raise FileNotFoundError(f"Source clip not found for preview: {src}")

        tmp_dest = dest.with_name(f".tmp_prev_{dest.name}")

        vf_filters = ["scale=540:960:force_original_aspect_ratio=increase,crop=540:960,fps=15"]
        if color_grading and color_grading != "none":
            cg = build_color_grading_filter(color_grading)
            if cg:
                vf_filters.append(cg)
        if watermark_text:
            wm = build_watermark_filter(
                watermark_text=watermark_text,
                position=watermark_position or "top_right",
                opacity=watermark_opacity if watermark_opacity is not None else 0.65,
                width=540,
                height=960,
            )
            if wm:
                vf_filters.append(wm)

        vf_arg = ",".join(vf_filters)

        # Fast 540x960 15fps with ultrafast preset
        cmd = [
            self.ffmpeg_binary,
            "-y",
            "-t",
            f"{max_duration_sec:.2f}",
            "-i",
            str(src),
            "-vf",
            vf_arg,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(tmp_dest),
        ]

        try:
            proc = run_command_with_cleanup(
                cmd,
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                if tmp_dest.is_file():
                    tmp_dest.unlink(missing_ok=True)
                raise RuntimeError(
                    f"FFmpeg preview generation failed (code {proc.returncode}):\n"
                    f"{_stderr_tail(proc.stderr)}"
                )

            os.replace(tmp_dest, dest)
            return dest
        except subprocess.TimeoutExpired as exc:
            if tmp_dest.is_file():
                tmp_dest.unlink(missing_ok=True)
            raise TimeoutError(
                f"FFmpeg preview for {src.name} exceeded timeout ({timeout_sec}s)"
            ) from exc
        except Exception:
            if tmp_dest.is_file():
                tmp_dest.unlink(missing_ok=True)
            raise

    def extract_thumbnail(
        self,
        video_path: Path,
        output_path: Path,
        timestamp_sec: float | None = 1.2,
        auto_best_frame: bool = False,
        search_window_sec: float = 2.5,
        target_ts: float | None = None,
    ) -> Path:
        """Extract a crisp 1080x1920 JPEG frame from the rendered Short.

        When auto_best_frame=True or timestamp_sec is None, intelligently scores
        candidate frames in the first search_window_sec (default 2.5s) for visual sharpness,
        facial contrast, and hook banner visibility rather than relying on a static timestamp.
        Falls back to 0.1s if the clip is very short or initial seek fails.
        """
        if target_ts is not None:
            timestamp_sec = target_ts
        src = Path(video_path).resolve()
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not src.is_file():
            raise FileNotFoundError(f"Video file not found for thumbnail extraction: {src}")

        clip_dur = probe_media_duration(src, default=0.0)

        if auto_best_frame or timestamp_sec is None:
            target_ts = find_best_thumbnail_timestamp(
                video_path=src,
                ffmpeg_binary=self.ffmpeg_binary,
                search_window_sec=search_window_sec,
                fallback_ts=1.2 if timestamp_sec is None else float(timestamp_sec),
            )
        else:
            req_ts = float(timestamp_sec)
            if 0.0 < clip_dur <= req_ts or req_ts < 0.0:
                target_ts = 0.1
            else:
                target_ts = req_ts

        def _build_thumb_cmd(ts: float) -> list[str]:
            return [
                self.ffmpeg_binary,
                "-y",
                "-ss",
                f"{ts}",
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(dest),
            ]

        cmd = _build_thumb_cmd(target_ts)
        proc = run_command_with_cleanup(cmd, timeout=30.0)
        if (
            proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0
        ) and target_ts > 0.1:
            logger.debug(
                "Thumbnail extraction at %.2fs failed or empty; retrying at 0.1s fallback.",
                target_ts,
            )
            fallback_cmd = _build_thumb_cmd(0.1)
            proc = run_command_with_cleanup(fallback_cmd, timeout=30.0)

        if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"FFmpeg produced an empty or missing thumbnail file (code {proc.returncode}):\n"
                f"{_stderr_tail(proc.stderr)}"
            )
        return dest


def extract_thumbnail(
    video_path: Path | str,
    output_path: Path | str,
    timestamp_sec: float | None = 1.2,
    auto_best_frame: bool = False,
    search_window_sec: float = 2.5,
    target_ts: float | None = None,
) -> Path:
    """Extract a crisp JPEG frame from the video using FFmpegCompositor."""
    comp = FFmpegCompositor()
    return comp.extract_thumbnail(
        Path(video_path),
        Path(output_path),
        timestamp_sec=timestamp_sec,
        auto_best_frame=auto_best_frame,
        search_window_sec=search_window_sec,
        target_ts=target_ts,
    )


__all__ = [
    "FFmpegCompositor",
    "FasterWhisperTranscriber",
    "clear_hardware_encoder_cache",
    "detect_encoder_pixel_format",
    "detect_hardware_encoder",
    "extract_16khz_mono_wav",
    "extract_thumbnail",
    "find_best_thumbnail_timestamp",
    "get_ffmpeg_path",
    "has_audio_stream",
    "has_video_stream",
    "probe_media_duration",
    "run_command_with_cleanup",
    "score_frame_visual_quality",
    "terminate_process_tree",
]
