"""Hardware and software video encoder probe for FFmpeg on Windows / Intel UHD.

Detects Intel QuickSync Video (h264_qsv) via a tiny test encode, falling back to
CPU software encoding (libx264 -preset veryfast). Caches results in-memory.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

EncoderType = Literal["auto", "h264_qsv", "libx264"]


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


# Fallback encoder configuration when ffmpeg is missing or probing fails
DEFAULT_CPU_ENCODER = EncoderInfo(
    codec="libx264",
    extra_args=["-preset", "veryfast", "-crf", "22", "-threads", "4"],
    is_hardware_accelerated=False,
    verified=False,
    description="libx264 software encoder fallback (CPU)",
)

DEFAULT_QSV_ENCODER = EncoderInfo(
    codec="h264_qsv",
    extra_args=["-global_quality", "25", "-look_ahead", "0"],
    is_hardware_accelerated=True,
    verified=False,
    description="Intel QuickSync Video (h264_qsv)",
)

_PROBE_CACHE: dict[str, EncoderInfo] = {}


def is_ffmpeg_available(ffmpeg_binary: str = "ffmpeg") -> bool:
    """Return True if the FFmpeg binary is found and executable."""
    resolved = shutil.which(ffmpeg_binary)
    if not resolved:
        return False
    try:
        proc = subprocess.run(
            [resolved, "-version"],
            capture_output=True,
            timeout=3.0,
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
            timeout=3.0,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_tiny_test_encode(
    codec: str,
    extra_args: list[str] | None = None,
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = 5.0,
) -> bool:
    """Verify an encoder by executing a tiny 0.1-second synthetic test encode.

    Uses a 256x256 lavfi test pattern to verify that the codec initializes and
    encodes without error on the host graphics driver/hardware.
    """
    cmd = [
        ffmpeg_binary,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=256x256:d=0.1",
        "-c:v",
        codec,
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(["-f", "null", "-"])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Test encode for %s failed with exception: %s", codec, exc)
        return False


def probe_encoder(
    preferred: EncoderType = "auto",
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = 5.0,
    force_refresh: bool = False,
) -> EncoderInfo:
    """Probe for available hardware/software H.264 video encoders.

    Checks:
    1. If ffmpeg is not available, returns DEFAULT_CPU_ENCODER unverified.
    2. If preferred is 'h264_qsv' or 'auto', tests h264_qsv with a tiny synthetic encode.
       If successful, returns verified h264_qsv EncoderInfo.
    3. If h264_qsv fails or preferred is 'libx264', tests and returns libx264 EncoderInfo.

    Results are cached per preferred setting unless force_refresh is True.
    """
    cache_key = f"{preferred}:{ffmpeg_binary}"
    if not force_refresh and cache_key in _PROBE_CACHE:
        return _PROBE_CACHE[cache_key]

    if not is_ffmpeg_available(ffmpeg_binary):
        logger.warning(
            "FFmpeg executable not found on PATH. Falling back to default CPU encoder configuration."
        )
        _PROBE_CACHE[cache_key] = DEFAULT_CPU_ENCODER
        return DEFAULT_CPU_ENCODER

    # If preferred is specifically libx264, test and return CPU encoder
    if preferred == "libx264":
        verified = run_tiny_test_encode(
            DEFAULT_CPU_ENCODER.codec,
            DEFAULT_CPU_ENCODER.extra_args,
            ffmpeg_binary=ffmpeg_binary,
            timeout_sec=timeout_sec,
        )
        info = EncoderInfo(
            codec=DEFAULT_CPU_ENCODER.codec,
            extra_args=DEFAULT_CPU_ENCODER.extra_args,
            is_hardware_accelerated=False,
            verified=verified,
            description="libx264 software encoder (CPU, preset=veryfast)",
        )
        _PROBE_CACHE[cache_key] = info
        return info

    # Try Intel QuickSync (h264_qsv)
    qsv_success = run_tiny_test_encode(
        DEFAULT_QSV_ENCODER.codec,
        DEFAULT_QSV_ENCODER.extra_args,
        ffmpeg_binary=ffmpeg_binary,
        timeout_sec=timeout_sec,
    )
    if qsv_success:
        logger.info("Intel QuickSync Video (h264_qsv) successfully verified.")
        info = EncoderInfo(
            codec=DEFAULT_QSV_ENCODER.codec,
            extra_args=DEFAULT_QSV_ENCODER.extra_args,
            is_hardware_accelerated=True,
            verified=True,
            description="Intel QuickSync Video hardware encoder (h264_qsv)",
        )
        _PROBE_CACHE[cache_key] = info
        return info

    if preferred == "h264_qsv":
        logger.warning("Requested encoder 'h264_qsv' failed verification. Falling back to libx264.")

    # Fallback to libx264
    x264_verified = run_tiny_test_encode(
        DEFAULT_CPU_ENCODER.codec,
        DEFAULT_CPU_ENCODER.extra_args,
        ffmpeg_binary=ffmpeg_binary,
        timeout_sec=timeout_sec,
    )
    info = EncoderInfo(
        codec=DEFAULT_CPU_ENCODER.codec,
        extra_args=DEFAULT_CPU_ENCODER.extra_args,
        is_hardware_accelerated=False,
        verified=x264_verified,
        description="libx264 software encoder fallback (CPU, preset=veryfast)",
    )
    _PROBE_CACHE[cache_key] = info
    return info
