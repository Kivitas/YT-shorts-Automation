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
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from autoshorts.media.downloader import run_command_with_cleanup
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


# Measurements are cached by (path, mtime_ns, size, target, ceiling, lra): the same file is often
# measured by the pre-check and again by a later stage. A rewritten file changes mtime/size and
# therefore misses. Only non-empty results are cached.
_MEASURE_CACHE: dict[tuple, dict] = {}
_MEASURE_LOCK = threading.Lock()
_MEASURE_CACHE_MAX = 256

# Skip the audio re-encode when the file is already this close (in LU) to the target and its
# true peak is under the ceiling: a second lossy AAC encode would cost more than it gains.
DEFAULT_TOLERANCE_LU = 1.0


def clear_measurement_cache() -> None:
    """Forget cached loudness measurements (test isolation)."""
    with _MEASURE_LOCK:
        _MEASURE_CACHE.clear()


def _measure_timeout(path: Path) -> float:
    """Bound for the analysis pass: scales with media length, never unbounded."""
    try:
        from autoshorts.media.ffmpeg_tools import probe_duration_cached

        duration = probe_duration_cached(path)
    except Exception:
        duration = 0.0
    return min(1800.0, max(120.0, duration * 4.0 + 60.0))


def measure_loudness(
    path: Path, target_lufs: float = -14.0, true_peak_db: float = -1.0, lra: float = 7.0
) -> dict:
    """Cached wrapper around :func:`_measure_loudness_uncached` (see its docstring)."""
    try:
        st = Path(path).stat()
        key = (
            str(Path(path).resolve()),
            st.st_mtime_ns,
            st.st_size,
            target_lufs,
            true_peak_db,
            lra,
        )
    except OSError:
        return {}
    with _MEASURE_LOCK:
        hit = _MEASURE_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    result = _measure_loudness_uncached(Path(path), target_lufs, true_peak_db, lra)
    if result:
        with _MEASURE_LOCK:
            if len(_MEASURE_CACHE) >= _MEASURE_CACHE_MAX:
                _MEASURE_CACHE.pop(next(iter(_MEASURE_CACHE)), None)
            _MEASURE_CACHE[key] = dict(result)
    return result


def _measure_loudness_uncached(
    path: Path, target_lufs: float, true_peak_db: float, lra: float
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
            timeout=_measure_timeout(path),
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


def _has_audio(path: Path) -> bool:
    """False only when the file is positively known to carry no audio stream.

    An unknown probe result (missing ffprobe, unreadable file) answers True so the normal
    measure/encode path decides, and its own never-raise handling applies.
    """
    try:
        from autoshorts.media.ffmpeg_tools import probe_media_cached

        info = probe_media_cached(path)
    except Exception:
        return True
    if info.get("has_audio"):
        return True
    return not info.get("has_video")  # video-only => no audio; nothing recognised => unknown


def _within_tolerance(
    measured: dict, target_lufs: float, true_peak_db: float, tolerance_lu: float
) -> bool:
    """True when the measured file already meets the target and needs no re-encode."""
    if not measured:
        return False
    return (
        abs(measured["input_i"] - target_lufs) <= tolerance_lu
        and measured["input_tp"] <= true_peak_db + 0.05
    )


def _keep_original(src: Path, dst: Path) -> Path:
    """Make ``dst`` hold the untouched ``src`` content (no-op when they are the same file)."""
    if src == dst:
        return dst
    # pid + thread + random: two threads keeping the same dst must not share a temp name
    tmp = dst.with_name(f".{dst.stem}.keep.{os.getpid()}.{uuid.uuid4().hex[:8]}{dst.suffix}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


def normalize_two_pass(
    src: Path,
    dst: Path,
    target_lufs: float,
    *,
    tolerance_lu: float = DEFAULT_TOLERANCE_LU,
    true_peak_db: float = -1.0,
) -> Path:
    """Two-pass EBU R128 normalise ``src`` into ``dst``; the video stream is copied.

    Measures ``src`` (cached). When the integrated loudness is already within ``tolerance_lu``
    of ``target_lufs`` and the true peak is under the ceiling, the audio is NOT re-encoded:
    ``src`` is kept as is. Otherwise only the audio is re-encoded with the measured linear
    loudnorm plus the limiter, written to a temp file beside ``dst`` and ``os.replace``d into
    place. If measurement fails, it falls back to the single-pass filter.

    Never fails the job: any error (including an ffmpeg failure or timeout) is logged as a
    warning and the original audio is kept (copied to ``dst`` when ``dst`` differs from ``src``).
    """
    src, dst = Path(src), Path(dst)
    tmp: Path | None = None
    try:
        if not _has_audio(src):
            # Nothing to normalise (silent/no-audio source): keep the file as is.
            logger.info("no audio stream in %s; skipping loudness normalisation", src)
            return _keep_original(src, dst)
        measured = measure_loudness(src, target_lufs=target_lufs, true_peak_db=true_peak_db)
        if _within_tolerance(measured, target_lufs, true_peak_db, tolerance_lu):
            logger.info(
                "loudness %.1f LUFS already within %.1f LU of %.1f target; skipping re-encode",
                measured["input_i"],
                tolerance_lu,
                target_lufs,
            )
            return _keep_original(src, dst)
        if not measured:
            logger.warning(
                "two-pass measurement unavailable for %s; using single-pass loudnorm", src
            )
        af = build_loudnorm_filter(measured or None, target_lufs, true_peak_db)

        suffix = dst.suffix.lower()
        cmd = [get_ffmpeg_path(), "-y", "-hide_banner", "-nostdin", "-i", str(src)]
        if suffix in _VIDEO_SUFFIXES:
            cmd += ["-map", "0:v?", "-c:v", "copy"]
        cmd += ["-map", "0:a:0", "-af", af, *_AUDIO_CODECS.get(suffix, _DEFAULT_AUDIO_CODEC)]
        cmd += ["-ar", "48000"]
        if suffix in (".mp4", ".mov", ".m4v"):
            cmd += ["-movflags", "+faststart"]

        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.stem}.", suffix=dst.suffix, dir=dst.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        # The whole process tree is killed on timeout; the encode is audio-only so it is fast.
        proc = run_command_with_cleanup([*cmd, str(tmp)], timeout=_measure_timeout(src))
        if proc.returncode != 0:
            raise RuntimeError(f"audio normalisation failed: {(proc.stderr or '').strip()[-800:]}")
        os.replace(tmp, dst)
        return dst
    except Exception as exc:  # loudness is polish: keep the original rather than fail the job
        logger.warning("audio normalisation failed for %s; keeping original audio: %s", src, exc)
        try:
            return _keep_original(src, dst)
        except OSError as copy_exc:
            logger.warning("could not preserve original audio at %s: %s", dst, copy_exc)
            return src
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
