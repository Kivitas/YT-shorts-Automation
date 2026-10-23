"""FFmpeg / ffprobe discovery and the single hardware video encoder detector.

This module is the one source of truth for encoder selection. `detect_hardware_encoder`
(re-exported by autoshorts.media.compositor) and `probe_encoder` (used by the dashboard)
both resolve to the same cached implementation, so they cannot disagree on one machine.

Each candidate encoder is verified with a tiny synthetic lavfi encode. A non-zero exit or
a timeout marks the encoder unavailable and logs one message naming it and the reason.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

EncoderType = Literal["auto", "h264_qsv", "libx264"]

# Per-probe timeout. Cold starts of NVENC / QSV drivers routinely take several seconds on
# Windows (driver init, AV scanning), so the old 3-5 s budget caused silent CPU fallback.
ENCODER_PROBE_TIMEOUT_SEC = 15.0

# Candidate hardware encoders in priority order: (codec, extra args, description).
HW_ENCODERS: list[tuple[str, list[str], str]] = [
    (
        "h264_nvenc",
        ["-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "24"],
        "NVIDIA NVENC H.264 Hardware Encoder",
    ),
    ("hevc_videotoolbox", ["-q:v", "65"], "Apple VideoToolbox HEVC Hardware Encoder"),
    ("h264_videotoolbox", ["-q:v", "65"], "Apple VideoToolbox H.264 Hardware Encoder"),
    ("h264_qsv", ["-global_quality", "25", "-look_ahead", "0"], "Intel QuickSync Video (h264_qsv)"),
]
_HW_CODECS = {codec for codec, _, _ in HW_ENCODERS}


@dataclass(frozen=True)
class EncoderInfo:
    """Descriptor for the resolved FFmpeg video encoder and its tuning flags."""

    codec: str
    extra_args: list[str] = field(default_factory=list)
    is_hardware_accelerated: bool = False
    verified: bool = False
    description: str = ""

    def get_ffmpeg_args(self) -> list[str]:
        """Return the FFmpeg command line arguments for video encoding."""
        return ["-c:v", self.codec, *self.extra_args, "-pix_fmt", "yuv420p"]


# CPU fallback used when no hardware encoder verifies (or ffmpeg is unusable).
DEFAULT_CPU_ENCODER = EncoderInfo(
    codec="libx264",
    extra_args=["-preset", "veryfast", "-crf", "22", "-threads", "4"],
    is_hardware_accelerated=False,
    verified=False,
    description="libx264 software encoder fallback (CPU)",
)

_PROBE_CACHE: dict[str, EncoderInfo] = {}


def clear_hardware_encoder_cache() -> None:
    """Clear cached encoder probe results (useful for test isolation)."""
    _PROBE_CACHE.clear()


def get_ffmpeg_path() -> str:
    """Resolve FFmpeg executable path.

    Checks system PATH first; if not found, checks for bundled executable from
    the imageio_ffmpeg package. Falls back to 'ffmpeg'.
    """
    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:
        pass
    return "ffmpeg"


def get_ffprobe_path() -> str | None:
    """Return a usable ffprobe executable, or None when none can be found.

    Order: (a) `ffprobe` on PATH; (b) `ffprobe` / `ffprobe.exe` in the same directory as the
    resolved ffmpeg binary (a full FFmpeg build ships both); (c) None. The imageio-ffmpeg
    bundle ships only ffmpeg, so on such machines this returns None.
    """
    on_path = shutil.which("ffprobe")
    if on_path:
        return on_path

    ffmpeg_resolved = get_ffmpeg_path()
    ffmpeg_file = Path(ffmpeg_resolved)
    if not ffmpeg_file.is_absolute():
        located = shutil.which(ffmpeg_resolved)
        if not located:
            return None
        ffmpeg_file = Path(located)
    if not ffmpeg_file.is_file():
        return None

    for name in ("ffprobe", "ffprobe.exe"):
        candidate = ffmpeg_file.parent / name
        if candidate.is_file():
            return str(candidate)
    return None


def is_ffmpeg_available(ffmpeg_binary: str = "ffmpeg") -> bool:
    """Return True if the FFmpeg binary is found and executable."""
    target = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
    resolved = shutil.which(target) or (target if Path(target).is_file() else None)
    if not resolved:
        return False
    try:
        proc = subprocess.run(
            [resolved, "-version"],
            capture_output=True,
            timeout=ENCODER_PROBE_TIMEOUT_SEC,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_ffprobe_available(ffprobe_binary: str = "ffprobe") -> bool:
    """Return True if the FFprobe binary is found and executable."""
    resolved = shutil.which(ffprobe_binary)
    if not resolved:
        return False
    try:
        proc = subprocess.run(
            [resolved, "-version"],
            capture_output=True,
            timeout=ENCODER_PROBE_TIMEOUT_SEC,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _last_stderr_line(stderr: object) -> str:
    """Return the last non-empty stderr line for a one-line failure reason."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    if not isinstance(stderr, str):
        return "no stderr output"
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no stderr output"


def run_tiny_test_encode(
    codec: str,
    extra_args: list[str] | None = None,
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = ENCODER_PROBE_TIMEOUT_SEC,
) -> bool:
    """Verify an encoder by encoding one synthetic lavfi testsrc frame.

    Returns False (and logs one message naming the codec and reason) on a non-zero exit,
    a start failure, or a timeout. subprocess.run kills the child on timeout and reaps it,
    so a timed-out probe leaves no process or zombie behind.
    """
    target_bin = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
    cmd = [
        target_bin,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=256x256:rate=25",
        "-frames:v",
        "1",
        "-c:v",
        codec,
        *(extra_args or []),
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Encoder %s unavailable: probe timed out after %.0fs (ffmpeg was killed)",
            codec,
            timeout_sec,
        )
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("Encoder %s unavailable: ffmpeg could not start (%s)", codec, exc)
        return False

    if proc.returncode != 0:
        logger.info(
            "Encoder %s unavailable: ffmpeg exit code %s (%s)",
            codec,
            proc.returncode,
            _last_stderr_line(proc.stderr),
        )
        return False
    return True


def _probe_uncached(target_pref: str, resolved_bin: str, timeout_sec: float) -> EncoderInfo:
    """Run the candidate sequence once. Callers go through detect_hardware_encoder."""
    if not is_ffmpeg_available(resolved_bin):
        logger.warning(
            "FFmpeg executable is not usable (%s). Using the unverified CPU encoder configuration.",
            resolved_bin,
        )
        return DEFAULT_CPU_ENCODER

    if target_pref != "libx264":
        # A specifically requested hardware encoder is tried first, then the priority order.
        candidates = sorted(HW_ENCODERS, key=lambda entry: entry[0] != target_pref)
        for codec, args, desc in candidates:
            if run_tiny_test_encode(
                codec, args, ffmpeg_binary=resolved_bin, timeout_sec=timeout_sec
            ):
                logger.info("Hardware encoder verified: %s (%s)", codec, desc)
                return EncoderInfo(
                    codec=codec,
                    extra_args=list(args),
                    is_hardware_accelerated=True,
                    verified=True,
                    description=desc,
                )
        if target_pref in _HW_CODECS:
            logger.warning(
                "Requested encoder '%s' failed verification. Falling back to libx264.",
                target_pref,
            )

    cpu_ok = run_tiny_test_encode(
        DEFAULT_CPU_ENCODER.codec,
        DEFAULT_CPU_ENCODER.extra_args,
        ffmpeg_binary=resolved_bin,
        timeout_sec=timeout_sec,
    )
    return EncoderInfo(
        codec=DEFAULT_CPU_ENCODER.codec,
        extra_args=list(DEFAULT_CPU_ENCODER.extra_args),
        is_hardware_accelerated=False,
        verified=cpu_ok,
        description=DEFAULT_CPU_ENCODER.description,
    )


def detect_hardware_encoder(
    preferred: str = "auto",
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = ENCODER_PROBE_TIMEOUT_SEC,
    force_refresh: bool = False,
) -> EncoderInfo:
    """Detect and verify the fastest usable video encoder. Single source of truth.

    Priority (when preferred is 'auto'): h264_nvenc, hevc_videotoolbox, h264_videotoolbox,
    h264_qsv, then libx264 (CPU). A specific hardware codec in `preferred` is tried first.
    Results are cached per process per (preferred, ffmpeg binary); force_refresh re-probes.
    """
    resolved_bin = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
    target_pref = (preferred or "auto").strip().lower()
    cache_key = f"{target_pref}:{resolved_bin}"
    if not force_refresh and cache_key in _PROBE_CACHE:
        return _PROBE_CACHE[cache_key]

    info = _probe_uncached(target_pref, resolved_bin, timeout_sec)
    _PROBE_CACHE[cache_key] = info
    return info


def probe_encoder(
    preferred: EncoderType = "auto",
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = ENCODER_PROBE_TIMEOUT_SEC,
    force_refresh: bool = False,
) -> EncoderInfo:
    """Public alias kept for the dashboard. Delegates to detect_hardware_encoder."""
    return detect_hardware_encoder(
        preferred=preferred,
        ffmpeg_binary=ffmpeg_binary,
        timeout_sec=timeout_sec,
        force_refresh=force_refresh,
    )
