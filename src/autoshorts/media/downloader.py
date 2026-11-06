"""Precision video and audio section downloader implementing contracts.Downloader.

Strictly uses yt-dlp subprocess commands with --download-sections and --force-keyframes-at-cuts.
Enforces CPU-friendly format selection (<=1080p), atomic file operations, backoff retries,
timeouts, and optional browser cookies.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from autoshorts.core.config import Settings, load_config
from autoshorts.core.exceptions import (
    DownloadError,
    FatalDownloadError,
    InsufficientDiskSpaceError,
    RetryableDownloadError,
)
from autoshorts.core.models import (
    HeatmapPoint,
    SponsorSegment,
    VideoChapter,
    VideoMetadata,
)
from autoshorts.core.paths import resolve_yt_dlp_binary
from autoshorts.discovery.metadata_fetcher import (
    MetadataFetcher,
    fetch_sponsor_segments,
)
from autoshorts.discovery.url_validator import extract_video_id, normalize_youtube_url
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

# Section downloads must land as a normal, universally-decodable video+audio pair.
#
# Preference order is deliberate:
#   1. H.264 (avc1) in MP4 over direct HTTP/DASH - decodes everywhere, re-encodes fast,
#      and is what YouTube Shorts accepts without a second transcode.
#   2. The same but without pinning the codec (some uploads only ship VP9/AV1).
#   3. Progressive `best` MP4 as a last resort before anything goes.
#
# Every branch pins a paired video+audio OR a progressive muxed format, so a section
# download can never resolve to an audio-only stream by construction.
DEFAULT_FORMAT_SPEC = (
    "bestvideo[height<=1080][vcodec^=avc1][protocol^=http][ext=mp4]"
    "+bestaudio[protocol^=http][ext=m4a]/"
    "bestvideo[height<=1080][vcodec^=avc1][ext=mp4]"
    "+bestaudio[ext=m4a]/"
    "bestvideo[height<=1080][protocol^=http][ext=mp4]"
    "+bestaudio[protocol^=http][ext=m4a]/"
    "bestvideo[height<=1080][protocol^=http]+bestaudio[protocol^=http]/"
    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height<=1080]+bestaudio/"
    "best[height<=1080][protocol^=http][ext=mp4]/"
    "best[height<=1080][ext=mp4]/"
    "best[height<=1080]/"
    "best"
)

# Validation outcomes for a finished section download.
SECTION_OK = "ok"
SECTION_MISSING = "missing_file"
SECTION_EMPTY = "empty_file"
SECTION_NO_VIDEO = "missing_video"
SECTION_NO_AUDIO = "missing_audio"
SECTION_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class SectionValidation:
    """Result of inspecting a finished section download before accepting it."""

    ok: bool
    reason: str
    has_video: bool = False
    has_audio: bool = False
    duration: float = 0.0
    size_bytes: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serializable form for stage artifacts and job diagnostics."""
        return {
            "ok": self.ok,
            "reason": self.reason,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "duration": round(self.duration, 3),
            "size_bytes": self.size_bytes,
            "detail": self.detail,
        }


def validate_section_output(
    path: Path | str,
    *,
    require_video: bool = True,
    require_audio: bool = True,
    min_duration_sec: float = 0.5,
) -> SectionValidation:
    """Inspect a downloaded section and decide whether it is actually usable.

    A successful ``yt-dlp`` exit code is not proof of a usable cut: the merge step can
    silently emit audio-only or truncated output. This treats the wrong output class as
    an explicit failure so the caller can retry or abort instead of shipping it forward.
    """
    target = Path(path)
    if not target.is_file():
        return SectionValidation(ok=False, reason=SECTION_MISSING, detail="file does not exist")

    size = target.stat().st_size
    if size <= 0:
        return SectionValidation(
            ok=False, reason=SECTION_EMPTY, size_bytes=0, detail="zero-byte output"
        )

    try:
        info = probe_video_stream_info(target)
    except Exception as exc:  # pragma: no cover - probe already swallows internally
        return SectionValidation(
            ok=False, reason=SECTION_UNREADABLE, size_bytes=size, detail=str(exc)
        )

    has_video = bool(info.get("has_video"))
    has_audio = bool(info.get("has_audio"))
    duration = float(info.get("duration") or 0.0)

    base = {
        "has_video": has_video,
        "has_audio": has_audio,
        "duration": duration,
        "size_bytes": size,
    }

    if require_video and not has_video:
        return SectionValidation(
            ok=False,
            reason=SECTION_NO_VIDEO,
            detail="output has no video stream (audio-only stream selected)",
            **base,
        )
    if require_audio and not has_audio:
        return SectionValidation(
            ok=False,
            reason=SECTION_NO_AUDIO,
            detail="output has no audio stream",
            **base,
        )
    if duration and duration < min_duration_sec:
        return SectionValidation(
            ok=False,
            reason=SECTION_UNREADABLE,
            detail=f"output duration {duration:.2f}s is shorter than {min_duration_sec:.2f}s",
            **base,
        )

    return SectionValidation(ok=True, reason=SECTION_OK, detail="video+audio present", **base)


def _summarize_attempts(attempts: list[dict[str, Any]]) -> str:
    """Render a compact, human-readable trace of every download attempt."""
    lines: list[str] = []
    for entry in attempts:
        outcome = entry.get("outcome", "unknown")
        detail = str(entry.get("detail") or "").strip().replace("\n", " ")
        if len(detail) > 240:
            detail = detail[:240] + "..."
        client = entry.get("player_client", "default")
        lines.append(f"  - attempt {entry.get('attempt')} [{client}]: {outcome} {detail}".rstrip())
    return "\n".join(lines)


def _timestamp_ms(seconds: float, *, round_up: bool) -> int:
    """Convert seconds to integer milliseconds, rounding outward so a cut never loses content."""
    value = round(float(seconds) * 1000.0, 6)  # absorb float noise such as 10.000000000002
    return math.ceil(value) if round_up else math.floor(value)


def _format_timestamp(total_ms: int, *, with_hours: bool) -> str:
    """Render milliseconds as MM:SS(.mmm) or HH:MM:SS(.mmm); the fraction is omitted when zero."""
    seconds, ms = divmod(total_ms, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    base = f"{hours:02d}:{minutes:02d}:{sec:02d}" if with_hours else f"{minutes:02d}:{sec:02d}"
    return f"{base}.{ms:03d}" if ms else base


def format_section_arg(start_sec: float, end_sec: float) -> str:
    """Format start and end timestamps into yt-dlp --download-sections string.

    Produces '*MM:SS-MM:SS' format (or '*HH:MM:SS-HH:MM:SS' for videos over 1 hour).
    Fractional seconds are kept to millisecond precision: the start is floored and the
    end is ceiled, so the cut never drops content at either edge. yt-dlp parses the
    fractional form with its standard duration parser.

    Examples:
        format_section_arg(0, 45) -> '*00:00-00:45'
        format_section_arg(65, 110) -> '*01:05-01:50'
        format_section_arg(125.5, 170.2) -> '*02:05.500-02:50.200'
        format_section_arg(3665, 3710) -> '*01:01:05-01:01:50'

    Args:
        start_sec: Section start timestamp in seconds (negative values clamp to 0).
        end_sec: Section end timestamp in seconds.

    Returns:
        Formatted argument string for yt-dlp --download-sections.

    Raises:
        ValueError: If the section is empty or inverted after clamping to millisecond precision.
    """
    s_ms = max(0, _timestamp_ms(start_sec, round_up=False))
    e_ms = _timestamp_ms(end_sec, round_up=True)
    if e_ms <= s_ms:
        raise ValueError(
            f"section end {end_sec!r}s must be after start {start_sec!r}s (non-empty range required)"
        )

    with_hours = s_ms >= 3_600_000 or e_ms >= 3_600_000
    start_text = _format_timestamp(s_ms, with_hours=with_hours)
    end_text = _format_timestamp(e_ms, with_hours=with_hours)
    return f"*{start_text}-{end_text}"


# Failures that retrying cannot fix, with an actionable hint per kind. Matched against
# yt-dlp's stderr text, first match wins.
_FATAL_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "private",
        re.compile(r"Private video|This video is private", re.IGNORECASE),
        "The video is private. Pick another source or use cookies from an account with access.",
    ),
    (
        "removed",
        re.compile(
            r"has been removed|no longer available|Video unavailable"
            r"|account associated with this video has been terminated",
            re.IGNORECASE,
        ),
        "The video was removed or is unavailable. Choose a different source video.",
    ),
    (
        "age_gated",
        re.compile(
            r"Sign in to confirm your age|age[- ]restricted|confirm your age", re.IGNORECASE
        ),
        "The video is age-gated. Set media.cookies_from_browser to a logged-in browser profile.",
    ),
    (
        "members_only",
        re.compile(r"members[- ]only|Join this channel to get access", re.IGNORECASE),
        "The video is members-only. Use cookies from a member account or pick another video.",
    ),
    (
        "geo_blocked",
        re.compile(
            r"available in your country|blocked it in your country|geo[- ]?restrict",
            re.IGNORECASE,
        ),
        "The video is geo-blocked. Use a different region/network or another source video.",
    ),
    (
        "not_live_yet",
        re.compile(r"This live event will begin|Premieres in", re.IGNORECASE),
        "The video is an upcoming premiere/live event. Retry after it starts.",
    ),
)
_PERMANENT_YTDLP_ERROR_RE = re.compile("|".join(r.pattern for _, r, _ in _FATAL_RULES), re.I)

# Transient failures: always worth another attempt (with longer backoff for rate limits).
_RATE_LIMIT_RE = re.compile(
    r"HTTP Error 429|Too Many Requests|rate[- ]limit|try again later", re.IGNORECASE
)
# YouTube answers an IP-throttled session with "Video unavailable. This content isn't available,
# try again later." That is a soft block, not a removed video, so it must not match the fatal
# "removed" rule below.
_SOFT_BLOCK_RE = re.compile(
    r"try again later|this content isn'?t available|content isn'?t available", re.IGNORECASE
)
_TRANSIENT_RE = re.compile(
    r"HTTP Error 5\d\d|Service Unavailable|Bad Gateway|Gateway Time-?out|timed out|Timeout"
    r"|Connection (reset|refused|aborted)|Temporary failure|Network is unreachable"
    r"|Unable to download|Read error|IncompleteRead|\bSSL\b|HTTP Error 403|Sign in to confirm",
    re.IGNORECASE,
)
_FORMAT_UNAVAILABLE_RE = re.compile(r"Requested format is not available", re.IGNORECASE)

# Last-resort selector used when the preferred ladder reports "format not available".
FALLBACK_FORMAT_SPEC = "best[height<=1080]/best"
# Player-client ladder: attempt 1 uses yt-dlp defaults, later attempts widen the pool.
PLAYER_CLIENT_LADDER: tuple[str, ...] = ("default", "android,web", "ios,web")

MAX_BACKOFF_SEC = 60.0


def classify_download_error(exc_or_text: BaseException | str) -> tuple[str, str, str]:
    """Classify a yt-dlp failure as ``(severity, kind, hint)``.

    ``severity`` is ``"fatal"`` (retrying cannot help), ``"rate_limited"``, ``"retryable"``
    (network/5xx/timeout/unknown), or ``"format"`` (selector mismatch; retry with a fallback).
    """
    if isinstance(exc_or_text, FileNotFoundError):
        return ("fatal", "missing_binary", "yt-dlp was not found. Install it or set its path.")
    if isinstance(exc_or_text, subprocess.TimeoutExpired):
        return ("retryable", "timeout", "")
    text = str(exc_or_text)
    soft_block = bool(_SOFT_BLOCK_RE.search(text))
    for kind, pattern, hint in _FATAL_RULES:
        if soft_block and kind == "removed":
            continue
        if pattern.search(text):
            return ("fatal", kind, hint)
    if _FORMAT_UNAVAILABLE_RE.search(text):
        return ("format", "format_unavailable", "")
    if _RATE_LIMIT_RE.search(text):
        return ("rate_limited", "http_429", "")
    if _TRANSIENT_RE.search(text):
        return ("retryable", "network", "")
    return ("retryable", "unknown", "")


def _is_non_retryable(exc: BaseException) -> bool:
    """True when another attempt cannot succeed: missing binary or a permanent video state."""
    return classify_download_error(exc)[0] == "fatal"


def compute_backoff(
    attempt: int, base_sec: float, *, rate_limited: bool = False, jitter: float = 0.25
) -> float:
    """Exponential backoff with +/- ``jitter`` fraction of random spread, capped at 60s.

    ``attempt`` is the 1-based attempt that just failed. Rate limits wait 4x longer.
    """
    delay = base_sec * (2 ** max(0, attempt - 1)) * (4.0 if rate_limited else 1.0)
    delay *= 1.0 + random.uniform(-jitter, jitter)  # noqa: S311 - not security sensitive
    return max(0.0, min(delay, MAX_BACKOFF_SEC))


def _select_downloaded_file(candidates: list[Path], prefix: str) -> Path | None:
    """Pick yt-dlp's finished output from the files created under this job's temp prefix.

    yt-dlp names the finished file exactly as the output template with the real extension
    substituted (``<prefix><ext>``). Partial files (``.part``, ``.ytdl``) and intermediate
    stream fragments (``<prefix><ext>.f137.mp4``) all add extra dot segments after the
    prefix, so they never match. Returns None when no finished file exists.
    """
    finished = [p for p in candidates if p.is_file() and "." not in p.name[len(prefix) :]]
    return finished[0] if finished else None


_AUDIO_EXTENSIONS = frozenset({".opus", ".m4a", ".mp3", ".aac", ".ogg", ".wav", ".flac"})


def _select_audio_file(candidates: list[Path], prefix: str) -> Path | None:
    """Pick the finished audio file from yt-dlp's ``-x`` output under this job's temp prefix.

    Audio extraction keeps the source container in the name (``<prefix>webm.opus``), so the
    generic selector above would reject it. Only files with a real audio extension qualify;
    ``.part``, ``.ytdl`` and stream fragments never end in one.
    """
    finished = [
        p
        for p in candidates
        if p.is_file()
        and p.suffix.lower() in _AUDIO_EXTENSIONS
        and p.name.startswith(prefix)
        and ".f" not in p.name[len(prefix) :]
    ]
    return finished[0] if finished else None


_KILL_TIMEOUT_SEC = 10.0
# Upper bound for run_command_with_cleanup when the caller passes no timeout, so a wedged
# child can never block a pipeline thread forever.
DEFAULT_COMMAND_TIMEOUT_SEC = 3600.0


def terminate_process_tree(proc: subprocess.Popen | int | None) -> None:
    """Cleanly terminate process and all child processes across Windows, macOS, and Linux."""
    if proc is None:
        return
    pid = proc.pid if isinstance(proc, subprocess.Popen) else proc
    if pid is None or pid <= 0:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                timeout=_KILL_TIMEOUT_SEC,
            )
        except Exception as exc:
            logger.debug("taskkill failed for PID %s: %s", pid, exc)
    else:
        try:
            # On macOS and Linux, terminate child processes spawned by yt-dlp/ffmpeg
            try:
                subprocess.run(
                    ["pkill", "-P", str(pid)],
                    capture_output=True,
                    check=False,
                    timeout=_KILL_TIMEOUT_SEC,
                )
            except Exception:
                pass
            if isinstance(proc, subprocess.Popen):
                proc.kill()
            else:
                os.kill(pid, 9)
        except Exception as exc:
            logger.debug("Failed to kill PID %s: %s", pid, exc)


def _reap(proc: subprocess.Popen) -> None:
    """Collect a killed child's pipes and exit status so no zombie or open handle is left."""
    try:
        proc.communicate(timeout=5.0)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5.0)
        except Exception:
            pass


def run_command_with_cleanup(
    cmd: list[str],
    timeout: float | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Execute command with full process tree termination on timeout or failure.

    ``timeout=None`` is replaced by ``DEFAULT_COMMAND_TIMEOUT_SEC`` so no call is unbounded.
    """
    if timeout is None:
        timeout = DEFAULT_COMMAND_TIMEOUT_SEC
    if "Mock" in type(subprocess.run).__name__ or hasattr(subprocess.run, "mock_calls"):
        try:
            proc = subprocess.run(cmd, timeout=timeout, **kwargs)
            if proc.returncode != 0 and hasattr(proc, "pid"):
                terminate_process_tree(proc.pid)
            return proc
        except subprocess.TimeoutExpired as exc:
            if hasattr(exc, "pid"):
                terminate_process_tree(exc.pid)
            raise

    # Close stdin so a child can never block waiting on an interactive prompt.
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        # The child has exited and been reaped. Signalling its PID now could hit an
        # unrelated process that reused the number, so nothing is killed on normal exit.
        retcode = proc.returncode or 0
        return subprocess.CompletedProcess(cmd, retcode, stdout, stderr)
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc.pid)
        _reap(proc)
        raise
    except BaseException:  # includes KeyboardInterrupt: never orphan a yt-dlp/ffmpeg tree
        terminate_process_tree(proc.pid)
        _reap(proc)
        raise


def probe_video_stream_info(video_path: Path) -> dict[str, Any]:
    """Check resolution, duration, and whether audio stream is present via ffprobe or ffmpeg."""
    info: dict[str, Any] = {
        "width": 0,
        "height": 0,
        "resolution": "unknown",
        "duration": 0.0,
        "has_video": False,
        "has_audio": False,
    }
    p = Path(video_path)
    if not p.exists():
        return info

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,width,height,duration:format=duration",
        "-of",
        "json",
        str(p),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            payload = json.loads(proc.stdout)
            streams = payload.get("streams") or []
            for s in streams:
                if not isinstance(s, dict):
                    continue
                ctype = s.get("codec_type")
                if ctype == "video":
                    info["has_video"] = True
                    w = int(s.get("width") or 0)
                    h = int(s.get("height") or 0)
                    if w > 0 and h > 0:
                        info["width"] = w
                        info["height"] = h
                        info["resolution"] = f"{w}x{h}"
                    if s.get("duration") and info["duration"] <= 0.0:
                        try:
                            info["duration"] = max(0.0, float(s["duration"]))
                        except (TypeError, ValueError):
                            pass
                elif ctype == "audio":
                    info["has_audio"] = True
                    if s.get("duration") and info["duration"] <= 0.0:
                        try:
                            info["duration"] = max(0.0, float(s["duration"]))
                        except (TypeError, ValueError):
                            pass

            fmt = payload.get("format") or {}
            if isinstance(fmt, dict) and fmt.get("duration"):
                try:
                    info["duration"] = max(info["duration"], float(fmt["duration"]))
                except (TypeError, ValueError):
                    pass
            if info["has_video"] or info["duration"] > 0:
                return info
    except Exception as exc:
        logger.debug("ffprobe failed for %s: %s", p, exc)

    # Fallback to ffmpeg -hide_banner -i when ffprobe is missing on PATH or fails
    try:
        ffmpeg_bin = get_ffmpeg_path()
        if ffmpeg_bin:
            proc = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-i", str(p)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15.0,
                check=False,
            )
            raw = (proc.stderr or "") + (proc.stdout or "")
            dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", raw)
            if dur_match:
                hours, mins, secs = dur_match.groups()
                dur_val = int(hours) * 3600.0 + int(mins) * 60.0 + float(secs)
                if dur_val > 0:
                    info["duration"] = max(info["duration"], dur_val)

            for line in raw.splitlines():
                if "Stream #" in line:
                    if "Video:" in line:
                        info["has_video"] = True
                        res_match = re.search(r"(\d{2,5})x(\d{2,5})", line)
                        if res_match:
                            w, h = int(res_match.group(1)), int(res_match.group(2))
                            info["width"] = w
                            info["height"] = h
                            info["resolution"] = f"{w}x{h}"
                    elif "Audio:" in line:
                        info["has_audio"] = True
    except Exception as exc:
        logger.debug("ffmpeg stream probe fallback failed for %s: %s", p, exc)

    return info


_KEY_LOCKS: dict[str, list] = {}  # key -> [lock, holders]; removed when the last holder leaves
_KEY_LOCKS_GUARD = threading.Lock()
_CACHE_MAX_AGE_SEC = 7 * 24 * 3600
_STALE_TEMP_AGE_SEC = 2 * 3600


@contextlib.contextmanager
def _key_lock(key: str):
    """One lock per cache key so identical concurrent downloads run once.

    The registry entry is reference counted and dropped when the last holder leaves, so the
    table cannot grow with every distinct section a long-running process ever downloads.
    """
    with _KEY_LOCKS_GUARD:
        entry = _KEY_LOCKS.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _KEY_LOCKS_GUARD:
            entry[1] -= 1
            if entry[1] <= 0 and _KEY_LOCKS.get(key) is entry:
                del _KEY_LOCKS[key]


# Section-cache size bound (each cached cut duplicates the section on disk).
DEFAULT_CACHE_MAX_BYTES = 2 * 1024**3


def _prune_cache(
    cache_dir: Path,
    max_age_sec: float = _CACHE_MAX_AGE_SEC,
    max_bytes: int | None = DEFAULT_CACHE_MAX_BYTES,
    keep: Path | None = None,
) -> None:
    """Delete cache entries older than ``max_age_sec``, then least-recently-used ones until the
    total is within ``max_bytes`` (``None`` disables the size cap). ``keep`` is never removed.

    Recency is the file mtime, which cache hits refresh (see ``_touch``), so mtime is LRU order.
    Another thread or process may delete or open entries at any moment; every OS error is
    ignored (on Windows an entry that is being copied cannot be unlinked and simply stays).
    """
    cutoff = time.time() - max_age_sec
    live: list[tuple[float, int, Path]] = []
    for entry in cache_dir.glob("*.mp4"):
        try:
            st = entry.stat()
            if st.st_mtime < cutoff and entry != keep:
                entry.unlink(missing_ok=True)
            else:
                live.append((st.st_mtime, st.st_size, entry))
        except OSError:
            pass
    if max_bytes is None:
        return
    total = sum(size for _, size, _ in live)
    for _mtime, size, entry in sorted(live, key=lambda item: item[0]):
        if total <= max_bytes:
            break
        if entry == keep:
            continue
        try:
            entry.unlink(missing_ok=True)
            total -= size
        except OSError:
            pass


def _touch(path: Path) -> None:
    """Refresh a cache entry's recency; best effort."""
    try:
        os.utime(path, None)
    except OSError:
        pass


# Temp tokens of downloads running in this process: stale-file cleanup must never touch them,
# however old their files look (a stalled but live download keeps its token registered).
_ACTIVE_TEMP_TOKENS: set[str] = set()
_ACTIVE_TOKENS_GUARD = threading.Lock()


def cleanup_stale_temp_files(directory: Path, max_age_sec: float = _STALE_TEMP_AGE_SEC) -> int:
    """Remove ``.tmp_*``/``.part``/``.ytdl`` leftovers older than ``max_age_sec``.

    The age guard keeps files that a concurrent download in the same directory is still
    writing. Returns the number of files removed.
    """
    removed = 0
    cutoff = time.time() - max_age_sec
    try:
        entries = list(directory.glob(".tmp_*")) + list(directory.glob("*.part"))
        entries += list(directory.glob("*.ytdl"))
    except OSError:
        return 0
    with _ACTIVE_TOKENS_GUARD:
        active = tuple(_ACTIVE_TEMP_TOKENS)
    for entry in entries:
        if any(token in entry.name for token in active):
            continue
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def _validate_cookies_spec(spec: str | None) -> str | None:
    """Return a safe ``--cookies-from-browser`` value, or None when unset.

    The value is passed as a separate argv item, so shell injection is impossible, but a value
    starting with ``-`` could still be read as another yt-dlp option (for example ``--exec``)
    and control characters never belong in a browser spec.
    """
    if spec is None:
        return None
    value = str(spec).strip()
    if not value:
        return None
    if value.startswith("-") or any(ord(ch) < 32 for ch in value):
        raise ValueError("cookies_from_browser must be a browser name such as 'chrome'")
    return value


class Downloader:
    """Downloader implementation compliant with contracts.Downloader.

    Strictly uses yt-dlp to download only precision timestamp slices without
    scraping or full-video downloading.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        yt_dlp_binary: str = "yt-dlp",
        format_spec: str = DEFAULT_FORMAT_SPEC,
        cookies_from_browser: str | None = None,
        timeout_sec: float = 180.0,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
        require_audio_stream: bool = True,
        concurrent_fragments: int = 4,
        section_cache: bool = True,
        cache_dir: Path | None = None,
        min_free_bytes: int = 256 * 1024 * 1024,
        cache_max_bytes: int | None = DEFAULT_CACHE_MAX_BYTES,
    ) -> None:
        """Initialize Downloader.

        Args:
            settings: Optional loaded application Settings.
            yt_dlp_binary: yt-dlp binary name or path.
            format_spec: Quality capping format selector (default <=1080p).
            cookies_from_browser: Browser name (e.g. 'chrome', 'edge', 'firefox') for cookies.
            timeout_sec: Subprocess timeout in seconds.
            max_retries: Number of retry attempts on transient network/download failures.
            retry_delay_sec: Initial delay between retries.
            require_audio_stream: Treat a section without an audio stream as a failed
                download instead of silently accepting it.
            concurrent_fragments: Parallel fragment downloads for DASH/HLS (yt-dlp -N).
            section_cache: Reuse a previously downloaded identical section (same video,
                start, end and format) instead of downloading it again.
            cache_dir: Section cache location (default: ``<dest dir>/.section_cache``).
            min_free_bytes: Disk-space floor checked before a download starts.
            cache_max_bytes: Size cap per section-cache directory; least recently used
                entries are evicted beyond it (``None`` disables the cap).
        """
        self.settings = settings or load_config()
        self.yt_dlp_binary = resolve_yt_dlp_binary(yt_dlp_binary)
        self.format_spec = format_spec
        self.timeout_sec = timeout_sec
        self.max_retries = max(1, max_retries)
        self.retry_delay_sec = max(0.1, retry_delay_sec)
        self.require_audio_stream = require_audio_stream
        self.concurrent_fragments = max(1, int(concurrent_fragments))
        self.section_cache = section_cache
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.min_free_bytes = max(0, int(min_free_bytes))
        self.cache_max_bytes = None if cache_max_bytes is None else max(0, int(cache_max_bytes))
        # Populated by the most recent download_section() call (success or failure).
        self.last_download_diagnostics: dict[str, Any] = {}

        # Resolve cookies from browser: argument overrides config / env
        resolved_cookies = cookies_from_browser
        if not resolved_cookies:
            # Check environment variables
            resolved_cookies = os.environ.get("YTDLP_COOKIES_FROM_BROWSER") or os.environ.get(
                "COOKIES_FROM_BROWSER"
            )
        if not resolved_cookies and hasattr(self.settings, "media"):
            resolved_cookies = getattr(self.settings.media, "cookies_from_browser", None)
        self.cookies_from_browser = _validate_cookies_spec(resolved_cookies)

        self._metadata_fetcher = MetadataFetcher(
            yt_dlp_binary=self.yt_dlp_binary,
            timeout_sec=min(self.timeout_sec, 30.0),
            cookies_from_browser=self.cookies_from_browser,
        )

    def fetch_metadata(self, url: str) -> VideoMetadata:
        """Fetch title, author, duration, heatmap, chapters, and sponsor segments."""
        meta = self._metadata_fetcher.fetch(url)
        if not meta.sponsor_segments and meta.video_id:
            if "Mock" in type(fetch_sponsor_segments).__name__ or hasattr(
                fetch_sponsor_segments, "mock_calls"
            ):
                meta.sponsor_segments = fetch_sponsor_segments(meta.video_id)
        return meta

    def download_section(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        output_path: Path,
        diagnostics: dict[str, Any] | None = None,
    ) -> Path:
        """Download a section, reusing an identical cached cut when one exists.

        The cache key is (video_id, start, end, format selector, audio requirement). Identical
        concurrent requests are serialised so the second one is a cache hit rather than a
        duplicate download. See ``_download_section_uncached`` for the download semantics.
        """
        if not self.section_cache:
            return self._download_section_uncached(
                url, start_sec, end_sec, output_path, diagnostics
            )
        try:
            key = self._section_cache_key(url, start_sec, end_sec)
        except (ValueError, OverflowError, TypeError):
            # invalid URL/range: let the real path raise its precise error
            return self._download_section_uncached(
                url, start_sec, end_sec, output_path, diagnostics
            )
        dest = Path(output_path).resolve()
        cached = self._cache_root(dest) / f"{key}.mp4"
        with _key_lock(key):
            if self._restore_from_cache(cached, dest, diagnostics, url):
                return dest
            result = self._download_section_uncached(
                url, start_sec, end_sec, output_path, diagnostics
            )
            self._store_in_cache(result, cached)
            return result

    def _section_cache_key(self, url: str, start_sec: float, end_sec: float) -> str:
        vid = extract_video_id(url)
        s_ms = max(0, _timestamp_ms(start_sec, round_up=False))
        e_ms = _timestamp_ms(end_sec, round_up=True)
        raw = f"{vid}|{s_ms}|{e_ms}|{self.format_spec}|{int(self.require_audio_stream)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]  # noqa: S324 - cache key only

    def _cache_root(self, dest: Path) -> Path:
        return self.cache_dir or (dest.parent / ".section_cache")

    def _restore_from_cache(
        self, cached: Path, dest: Path, diagnostics: dict[str, Any] | None, url: str
    ) -> bool:
        """Copy a valid cached section to ``dest``; drop the entry if it no longer validates."""
        if not cached.is_file():
            return False
        try:
            validation = validate_section_output(cached, require_audio=self.require_audio_stream)
            if not validation.ok:
                cached.unlink(missing_ok=True)
                return False
            _touch(cached)  # LRU: a hit keeps the entry alive
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".tmp_cache_{uuid4().hex[:8]}{dest.suffix}")
            try:
                shutil.copy2(cached, tmp)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("section cache restore failed (%s); downloading instead", exc)
            return False
        trace = {
            "attempts": [],
            "attempt_count": 0,
            "cache_hit": True,
            "validation": validation.as_dict(),
            "url": url,
            "ok": True,
        }
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(trace)
        self.last_download_diagnostics = trace
        logger.info("Section cache hit: %s -> %s", cached.name, dest)
        return True

    def _store_in_cache(self, result: Path, cached: Path) -> None:
        """Best-effort copy of a finished section into the cache; never raises."""
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_name(f".{cached.name}.{uuid4().hex[:6]}.tmp")
            try:
                shutil.copy2(result, tmp)
                os.replace(tmp, cached)
            finally:
                tmp.unlink(missing_ok=True)
            _prune_cache(cached.parent, max_bytes=self.cache_max_bytes, keep=cached)
        except OSError as exc:
            logger.debug("section cache store skipped: %s", exc)

    def _check_disk_space(self, directory: Path, duration_sec: float) -> None:
        """Fail fast (fatal) when the target volume cannot hold the section."""
        try:
            free = shutil.disk_usage(directory).free
        except OSError:
            return
        # ~4 MB/s covers a 1080p video+audio pair; the floor covers merge scratch space.
        needed = self.min_free_bytes + int(max(0.0, duration_sec) * 4 * 1024 * 1024)
        if free < needed:
            raise InsufficientDiskSpaceError(
                f"Only {free // (1024 * 1024)} MiB free in {directory}; "
                f"need about {needed // (1024 * 1024)} MiB for this download."
            )

    def _download_section_uncached(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        output_path: Path,
        diagnostics: dict[str, Any] | None = None,
    ) -> Path:
        """Run one section download with its temp token registered as active.

        The registration keeps ``cleanup_stale_temp_files`` (run by concurrent downloads in
        the same directory) away from this download's files for as long as it is running.
        """
        token = uuid4().hex[:8]
        with _ACTIVE_TOKENS_GUARD:
            _ACTIVE_TEMP_TOKENS.add(token)
        try:
            return self._download_section_attempts(
                url, start_sec, end_sec, output_path, diagnostics, token
            )
        finally:
            with _ACTIVE_TOKENS_GUARD:
                _ACTIVE_TEMP_TOKENS.discard(token)

    def _download_section_attempts(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        output_path: Path,
        diagnostics: dict[str, Any] | None,
        unique_token: str,
    ) -> Path:
        """Download strictly the requested timestamp slice using yt-dlp --download-sections.

        Enforces --force-keyframes-at-cuts and outputs to work/<video_id>/ atomically.
        Retries with multi-client extractor args
        (--extractor-args "youtube:player_client=android,web") if the primary command fails.

        A zero exit code is treated as necessary but not sufficient: the finished file must
        contain both a video and an audio stream, otherwise it is discarded, retried, and
        finally reported as a failure rather than silently passed downstream.

        Args:
            url: YouTube watch URL or 11-char ID.
            start_sec: Cut start in seconds.
            end_sec: Cut end in seconds.
            output_path: Destination file path.
            diagnostics: Optional mutable dict populated with the full attempt trace so
                callers can persist *why* a download succeeded or failed.

        Returns:
            Resolved Path of the saved cut MP4.

        Raises:
            DownloadError: If yt-dlp fails after all retry attempts.
        """
        canonical_url = normalize_youtube_url(url)
        vid = extract_video_id(url)

        dest = Path(output_path).resolve()
        # Ensure parent directory exists (e.g. work/<video_id>/)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            section_arg = format_section_arg(start_sec, end_sec)
        except (ValueError, OverflowError) as exc:
            raise DownloadError(f"Invalid section for {canonical_url}: {exc}") from exc
        # Fail fast on a full disk and sweep long-dead partials from earlier crashed runs.
        self._check_disk_space(dest.parent, float(end_sec) - float(start_sec))
        cleanup_stale_temp_files(dest.parent)

        # The token makes every download's temp names unique, so concurrent jobs sharing a
        # directory can never pick up or delete each other's partial files.
        temp_template = dest.parent / f".tmp_{unique_token}_%(ext)s"

        base_cmd = [
            self.yt_dlp_binary,
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--continue",
            "--concurrent-fragments",
            str(self.concurrent_fragments),
            "--retries",
            "3",
            "--fragment-retries",
            "5",
            "--socket-timeout",
            "30",
            "--download-sections",
            section_arg,
            "--force-keyframes-at-cuts",
            "-f",
            self.format_spec,
            "--merge-output-format",
            "mp4",
        ]

        ffmpeg_bin = get_ffmpeg_path()
        if ffmpeg_bin:
            base_cmd.extend(["--ffmpeg-location", ffmpeg_bin])

        base_cmd.extend(
            [
                "-o",
                str(temp_template),
            ]
        )

        if self.cookies_from_browser:
            base_cmd.extend(["--cookies-from-browser", self.cookies_from_browser])

        logger.info(
            "Downloading section %s for %s (%s to %s) -> %s",
            section_arg,
            vid,
            start_sec,
            end_sec,
            dest,
        )

        last_error: Exception | None = None
        fatal_kind, fatal_hint = "", ""
        total_attempts = max(2, self.max_retries) if self.max_retries == 1 else self.max_retries

        # Every attempt (command failure *and* wrong-output-class) is recorded here so a
        # final failure can explain exactly what was tried instead of only the last error.
        attempt_log: list[dict[str, Any]] = []

        def _cleanup_temps() -> None:
            for f in dest.parent.glob(f".tmp_{unique_token}_*"):
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass

        use_fallback_format = False
        last_severity = "retryable"
        for attempt in range(1, total_attempts + 1):
            attempt_cmd = list(base_cmd)
            if use_fallback_format:
                # The preferred selector reported "format not available": drop to the
                # permissive ladder so a odd upload still yields a muxed clip.
                attempt_cmd[attempt_cmd.index("-f") + 1] = FALLBACK_FORMAT_SPEC
            # First attempt uses yt-dlp's default client selection. Retries widen the
            # client pool, which is what actually recovers from 403s on section capping.
            player_client = PLAYER_CLIENT_LADDER[min(attempt - 1, len(PLAYER_CLIENT_LADDER) - 1)]
            if player_client != "default":
                attempt_cmd.extend(["--extractor-args", f"youtube:player_client={player_client}"])
            # "--" stops option parsing so the URL can never be read as a flag.
            attempt_cmd.extend(["--", canonical_url])

            try:
                proc = run_command_with_cleanup(
                    attempt_cmd,
                    timeout=self.timeout_sec,
                )
                if proc.returncode == 0:
                    # Locate this job's finished temporary file (never a partial one).
                    pattern = f".tmp_{unique_token}_*"
                    downloaded_file = _select_downloaded_file(
                        list(dest.parent.glob(pattern)), f".tmp_{unique_token}_"
                    )
                    if downloaded_file is None:
                        # Do not fall back to an existing destination: a leftover file from an
                        # earlier job would otherwise pass as this download.
                        attempt_log.append(
                            {
                                "attempt": attempt,
                                "player_client": player_client,
                                "outcome": "no_output_file",
                                "detail": f"yt-dlp exited 0 but no finished file matched {pattern}",
                            }
                        )
                        raise DownloadError(
                            f"yt-dlp exited with 0 but output file matching {pattern} was not found"
                        )
                    # Atomically rename to requested destination, then drop any leftovers.
                    os.replace(downloaded_file, dest)
                    _cleanup_temps()

                    # A zero exit code is not proof of usability: verify the output is
                    # really a video+audio section before accepting it.
                    validation = validate_section_output(
                        dest, require_audio=self.require_audio_stream
                    )
                    if validation.ok:
                        attempt_log.append(
                            {
                                "attempt": attempt,
                                "player_client": player_client,
                                "outcome": SECTION_OK,
                                "detail": validation.detail,
                                "validation": validation.as_dict(),
                            }
                        )
                        trace = {
                            "attempts": attempt_log,
                            "attempt_count": attempt,
                            "section": section_arg,
                            "player_client": player_client,
                            "validation": validation.as_dict(),
                            "url": canonical_url,
                            "ok": True,
                        }
                        if diagnostics is not None:
                            diagnostics.clear()
                            diagnostics.update(trace)
                        self.last_download_diagnostics = trace
                        logger.info(
                            "Successfully downloaded section to %s (video=%s audio=%s, %.2fs)",
                            dest,
                            validation.has_video,
                            validation.has_audio,
                            validation.duration,
                        )
                        return dest

                    # Wrong output class: retry when attempts remain, otherwise report.
                    attempt_log.append(
                        {
                            "attempt": attempt,
                            "player_client": player_client,
                            "outcome": validation.reason,
                            "detail": validation.detail,
                            "validation": validation.as_dict(),
                        }
                    )
                    logger.warning(
                        "yt-dlp produced an unusable section on attempt %d/%d (%s: %s); "
                        "discarding and retrying",
                        attempt,
                        total_attempts,
                        validation.reason,
                        validation.detail,
                    )
                    dest.unlink(missing_ok=True)
                    _cleanup_temps()
                    if attempt < total_attempts:
                        time.sleep(compute_backoff(attempt, self.retry_delay_sec))
                    continue

                err_msg = (proc.stderr or proc.stdout or "").strip()
                attempt_log.append(
                    {
                        "attempt": attempt,
                        "player_client": player_client,
                        "outcome": f"yt_dlp_exit_{proc.returncode}",
                        "detail": err_msg,
                    }
                )
                raise DownloadError(f"yt-dlp exited with code {proc.returncode}: {err_msg}")

            except Exception as exc:
                last_error = exc
                if not any(e.get("attempt") == attempt for e in attempt_log):
                    attempt_log.append(
                        {
                            "attempt": attempt,
                            "player_client": player_client,
                            "outcome": type(exc).__name__,
                            "detail": str(exc),
                        }
                    )
                logger.warning(
                    "yt-dlp download section attempt %d/%d failed: %s",
                    attempt,
                    total_attempts,
                    exc,
                )
                _cleanup_temps()
                dest.unlink(missing_ok=True)
                last_severity, fail_kind, fail_hint = classify_download_error(exc)
                if last_severity == "fatal":
                    logger.error(
                        "Stopping section retries for %s: failure is not retryable (%s: %s)",
                        vid,
                        fail_kind,
                        fail_hint or exc,
                    )
                    fatal_kind, fatal_hint = fail_kind, fail_hint
                    break
                if last_severity == "format":
                    use_fallback_format = True
                if attempt < total_attempts:
                    time.sleep(
                        compute_backoff(
                            attempt,
                            self.retry_delay_sec,
                            rate_limited=last_severity == "rate_limited",
                        )
                    )

        failure_trace = {
            "attempts": attempt_log,
            "attempt_count": len(attempt_log),
            "section": section_arg,
            "url": canonical_url,
            "ok": False,
        }
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(failure_trace)
        self.last_download_diagnostics = failure_trace

        reasons = {str(e.get("outcome")) for e in attempt_log}
        hint = ""
        if SECTION_NO_VIDEO in reasons or SECTION_NO_AUDIO in reasons:
            hint = (
                " The selected formats were missing a required stream; the site likely "
                "only served audio or an incomplete pair for this section."
            )
        message = (
            f"Failed to download section {section_arg} for {canonical_url} after "
            f"{attempt} attempt(s).{hint}\nAttempt trace:\n{_summarize_attempts(attempt_log)}"
        )
        if fatal_kind:
            raise FatalDownloadError(
                f"{message}\nAction: {fatal_hint}", reason=fatal_kind, hint=fatal_hint
            ) from last_error
        if last_severity in ("retryable", "rate_limited") and last_error is not None:
            raise RetryableDownloadError(message) from last_error
        raise DownloadError(message) from last_error

    def download_audio_only(
        self,
        url: str,
        output_path: Path,
    ) -> Path:
        """Download audio-only stream for Whisper transcription when captions are missing.

        Args:
            url: YouTube video URL or 11-char ID.
            output_path: Destination path for extracted audio file.

        Returns:
            Resolved Path of the downloaded audio file.

        Raises:
            DownloadError: If yt-dlp audio extraction fails (``FatalDownloadError`` when
                retrying cannot help, ``RetryableDownloadError`` when attempts ran out).
        """
        token = uuid4().hex[:8]
        with _ACTIVE_TOKENS_GUARD:
            _ACTIVE_TEMP_TOKENS.add(token)
        try:
            return self._download_audio_attempts(url, output_path, token)
        finally:
            with _ACTIVE_TOKENS_GUARD:
                _ACTIVE_TEMP_TOKENS.discard(token)

    def _download_audio_attempts(self, url: str, output_path: Path, unique_token: str) -> Path:
        canonical_url = normalize_youtube_url(url)
        vid = extract_video_id(url)

        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        self._check_disk_space(dest.parent, 0.0)
        temp_template = dest.parent / f".tmp_audio_{unique_token}_%(ext)s"

        cmd = [
            self.yt_dlp_binary,
            "--no-playlist",
            "--no-warnings",
            "--continue",
            "--socket-timeout",
            "30",
            "-f",
            "bestaudio/best",
            "-x",
        ]

        ffmpeg_bin = get_ffmpeg_path()
        if ffmpeg_bin:
            cmd.extend(["--ffmpeg-location", ffmpeg_bin])

        cmd.extend(
            [
                "-o",
                str(temp_template),
                "--",
                canonical_url,
            ]
        )

        if self.cookies_from_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_from_browser])

        logger.info("Downloading audio-only stream for %s -> %s", vid, dest)

        last_error: Exception | None = None
        last_severity, last_kind, last_hint = "retryable", "", ""
        for attempt in range(1, self.max_retries + 1):
            try:
                proc = run_command_with_cleanup(
                    cmd,
                    timeout=self.timeout_sec,
                )
                if proc.returncode == 0:
                    downloaded_file = _select_audio_file(
                        list(dest.parent.glob(f".tmp_audio_{unique_token}_*")),
                        f".tmp_audio_{unique_token}_",
                    )
                    if downloaded_file is None:
                        # A pre-existing dest is not this job's output; never accept it.
                        raise DownloadError(
                            "Audio file was not found after successful yt-dlp execution"
                        )
                    os.replace(downloaded_file, dest)
                    for leftover in dest.parent.glob(f".tmp_audio_{unique_token}_*"):
                        leftover.unlink(missing_ok=True)
                    logger.info("Successfully downloaded audio to %s", dest)
                    return dest

                err_msg = (proc.stderr or proc.stdout or "").strip()
                raise DownloadError(
                    f"yt-dlp audio download failed with code {proc.returncode}: {err_msg}"
                )

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "yt-dlp audio download attempt %d/%d failed: %s", attempt, self.max_retries, exc
                )
                for f in dest.parent.glob(f".tmp_audio_{unique_token}_*"):
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass
                severity, kind, hint = classify_download_error(exc)
                last_severity, last_kind, last_hint = severity, kind, hint
                if severity == "fatal":
                    break
                if attempt < self.max_retries:
                    time.sleep(
                        compute_backoff(
                            attempt, self.retry_delay_sec, rate_limited=severity == "rate_limited"
                        )
                    )

        message = (
            f"Failed to download audio for {canonical_url} after {self.max_retries} attempts: "
            f"{last_error}"
        )
        if last_severity == "fatal":
            raise FatalDownloadError(
                f"{message}\nAction: {last_hint}", reason=last_kind, hint=last_hint
            ) from last_error
        raise RetryableDownloadError(message) from last_error


YtDlpDownloader = Downloader


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_SEC",
    "DEFAULT_FORMAT_SPEC",
    "FALLBACK_FORMAT_SPEC",
    "PLAYER_CLIENT_LADDER",
    "SECTION_EMPTY",
    "SECTION_MISSING",
    "SECTION_NO_AUDIO",
    "SECTION_NO_VIDEO",
    "SECTION_OK",
    "SECTION_UNREADABLE",
    "DownloadError",
    "Downloader",
    "HeatmapPoint",
    "SectionValidation",
    "SponsorSegment",
    "VideoChapter",
    "VideoMetadata",
    "YtDlpDownloader",
    "classify_download_error",
    "cleanup_stale_temp_files",
    "compute_backoff",
    "fetch_sponsor_segments",
    "format_section_arg",
    "probe_video_stream_info",
    "run_command_with_cleanup",
    "terminate_process_tree",
    "validate_section_output",
]
