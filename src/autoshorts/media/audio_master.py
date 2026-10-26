"""Audio mastering: EBU R128 loudnorm (single-pass filter or two-pass measured) plus a true-peak limiter.

``build_loudnorm_filter`` is the in-graph chain used by ``dopamine_effects``. ``normalize_two_pass``
is an optional second pass for a finished file: it measures the file, then re-encodes only the
audio with the measured values, copying the video stream.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

_MEASURE_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
_JSON_BLOCK = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)
_VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".mkv")
_AUDIO_CODECS = {
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
}
_DEFAULT_AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k"]


def _limiter(true_peak_db: float) -> str:
    """Brick-wall limiter whose ceiling is ``true_peak_db`` dBTP, as a linear gain."""
    # 0.5 dB margin: the limiter lets short overshoots through, so aim below the ceiling.
    limit = 10 ** ((true_peak_db - 0.5) / 20)
    # level=disabled: alimiter otherwise rescales its output and pushes the peak above the ceiling.
    return f"alimiter=limit={limit:.4f}:attack=5:release=50:level=disabled"


def measure_loudness(
    path: Path, target_lufs: float = -14.0, true_peak_db: float = -1.0, lra: float = 7.0
) -> dict:
    """Measure integrated loudness (``input_i``), true peak (``input_tp``), LRA, threshold.

    Runs one loudnorm analysis pass (``print_format=json``) into a null muxer and parses the JSON
    block from stderr. ``target_offset`` depends on ``target_lufs``, so measure with the same target
    that the second pass will use. Returns ``{}`` on any failure, including silent audio.
    """
    try:
        cmd = [
            get_ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"loudnorm=I={target_lufs:g}:TP={true_peak_db:.1f}:LRA={lra:g}:print_format=json",
            "-f",
            "null",
            "-",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=600,
        )
        match = _JSON_BLOCK.search(proc.stderr or "")
        if proc.returncode != 0 or match is None:
            return {}
        raw = json.loads(match.group(0))
        values = {key: float(raw[key]) for key in _MEASURE_KEYS}
        if not all(math.isfinite(v) for v in values.values()):
            return {}  # silent input reports -inf; a measured filter would be invalid
        return values
    except Exception as exc:  # measurement is advisory: never raise
        logger.warning("loudness measurement failed for %s: %s", path, exc)
        return {}


def build_loudnorm_filter(
    measured: dict | None, target_lufs: float, true_peak_db: float = -1.0, lra: float = 7.0
) -> str:
    """Return the loudnorm + alimiter filter chain.

    With ``measured`` (from ``measure_loudness`` at the same target) this is the linear two-pass
    form. Without it, it is the single-pass form. The chain always ends with an alimiter at the
    ``true_peak_db`` ceiling.
    """
    chain = f"loudnorm=I={target_lufs:g}:TP={true_peak_db:.1f}:LRA={lra:g}"
    if measured and all(key in measured for key in _MEASURE_KEYS):
        m = {key: float(measured[key]) for key in _MEASURE_KEYS}
        chain += (
            f":measured_I={m['input_i']:.2f}:measured_TP={m['input_tp']:.2f}"
            f":measured_LRA={m['input_lra']:.2f}:measured_thresh={m['input_thresh']:.2f}"
            f":offset={m['target_offset']:.2f}:linear=true:print_format=summary"
        )
    return f"{chain},{_limiter(true_peak_db)}"


def normalize_two_pass(src: Path, dst: Path, target_lufs: float) -> Path:
    """Two-pass EBU R128 normalise ``src`` into ``dst``; the video stream is copied.

    Measures ``src``, re-encodes only the audio with the measured linear loudnorm and the limiter,
    writes to a temp file beside ``dst`` and then ``os.replace``s it into place. If measurement
    fails, it falls back to the single-pass filter. Raises ``RuntimeError`` if ffmpeg fails.
    """
    src, dst = Path(src), Path(dst)
    measured = measure_loudness(src, target_lufs=target_lufs)
    if not measured:
        logger.warning("two-pass measurement unavailable for %s; using single-pass loudnorm", src)
    af = build_loudnorm_filter(measured or None, target_lufs)

    suffix = dst.suffix.lower()
    cmd = [get_ffmpeg_path(), "-y", "-hide_banner", "-nostdin", "-i", str(src)]
    if suffix in _VIDEO_SUFFIXES:
        cmd += ["-map", "0:v?", "-c:v", "copy"]
    cmd += ["-map", "0:a:0", "-af", af, *_AUDIO_CODECS.get(suffix, _DEFAULT_AUDIO_CODEC)]
    cmd += ["-ar", "48000"]
    if suffix in (".mp4", ".mov", ".m4v"):
        cmd += ["-movflags", "+faststart"]

    fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.stem}.", suffix=dst.suffix, dir=dst.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        proc = subprocess.run(
            [*cmd, str(tmp)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"audio normalisation failed: {proc.stderr.strip()[-800:]}")
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst
