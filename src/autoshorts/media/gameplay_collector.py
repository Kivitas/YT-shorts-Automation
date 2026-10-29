"""Automated high-dopamine background gameplay clip collector and manager.

Provides:
- Search via yt-dlp across high-dopamine categories (Minecraft parkour, Subway Surfers,
  GTA ramps, hydraulic press, slime/kinetic sand, Temple Run, car drift).
- Quality, watermark, and copyright heuristic filtering.
- Automated downloading capped at 1080p.
- Automated cutting into 30-90s vertical-friendly background segments using FFmpeg.
- Idempotent execution (skips already downloaded clips).
- Network failure tolerance with subprocess timeouts.
- Category manifest writing (path, source title, source URL, channel, duration, license note).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoshorts.core.paths import get_assets_dir
from autoshorts.media.background_fetcher import normalize_category, resolve_yt_dlp_binary
from autoshorts.media.compositor import probe_media_duration
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

# Default search queries tailored for royalty-free / Creative Commons high-dopamine gameplay
DEFAULT_CATEGORY_QUERIES: dict[str, str] = {
    "minecraft": "minecraft parkour gameplay no copyright no commentary free to use",
    "subway": "subway surfers gameplay no copyright no commentary vertical free to use",
    "gta": "gta 5 mega ramp stunts gameplay no copyright no commentary",
    "hydraulic_press": "hydraulic press satisfying crushing no copyright no commentary",
    "slime": "slime satisfying kinetic sand asmr no copyright no commentary",
    "temple_run": "temple run gameplay no commentary no copyright",
    "car_drift": "car drift satisfying cinematic gameplay no copyright no commentary",
}

# Category name aliases mapped to standard directory keys
CATEGORY_ALIASES: dict[str, str] = {
    "mc": "minecraft",
    "minecraft_parkour": "minecraft",
    "subway_surfers": "subway",
    "subwaysurfers": "subway",
    "gta5": "gta",
    "gtav": "gta",
    "gta_ramps": "gta",
    "hydraulic": "hydraulic_press",
    "press": "hydraulic_press",
    "kinetic_sand": "slime",
    "sand": "slime",
    "temple": "temple_run",
    "drift": "car_drift",
    "cars": "car_drift",
}


def normalize_category_name(category: str | None) -> str:
    """Normalize background category alias into standard folder name."""
    raw = (category or "minecraft").strip().lower()
    if raw in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[raw]
    return normalize_category(raw)


@dataclass
class ClipManifestEntry:
    """Metadata record for an individual background segment in manifest.json."""

    id: str
    filename: str
    path: str
    source_id: str
    source_title: str
    source_url: str
    channel: str
    duration: float
    license_note: str
    segment_index: int = 1
    collected_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to JSON-serializable dictionary."""
        return asdict(self)


def get_manifest_path(category: str, assets_dir: Path | None = None) -> Path:
    """Return path to manifest.json for the specified category."""
    clean_cat = normalize_category_name(category)
    base = assets_dir or get_assets_dir()
    return base / "backgrounds" / clean_cat / "manifest.json"


def load_manifest(category: str, assets_dir: Path | None = None) -> dict[str, Any]:
    """Load category manifest.json, returning empty structure if not found."""
    clean_cat = normalize_category_name(category)
    manifest_path = get_manifest_path(clean_cat, assets_dir)
    default_structure: dict[str, Any] = {
        "category": clean_cat,
        "updated_at": "",
        "clips": [],
    }

    if not manifest_path.is_file():
        return default_structure

    try:
        content = manifest_path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, list):
            return {
                "category": clean_cat,
                "updated_at": "",
                "clips": data,
            }
        if isinstance(data, dict):
            if "clips" not in data or not isinstance(data["clips"], list):
                data["clips"] = []
            data.setdefault("category", clean_cat)
            return data
    except Exception as exc:
        logger.warning("Failed to parse manifest at %s: %s", manifest_path, exc)

    return default_structure


def save_manifest(
    manifest_data: dict[str, Any],
    category: str,
    assets_dir: Path | None = None,
) -> Path:
    """Write category manifest.json atomically with ISO UTC timestamp."""
    clean_cat = normalize_category_name(category)
    manifest_path = get_manifest_path(clean_cat, assets_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_data["category"] = clean_cat
    manifest_data["updated_at"] = datetime.now(UTC).isoformat()

    tmp_path = manifest_path.with_name(f".tmp_{manifest_path.name}")
    try:
        tmp_path.write_text(
            json.dumps(manifest_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, manifest_path)
    finally:
        if tmp_path.is_file():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return manifest_path


def probe_video_duration(media_path: Path | str, default: float = 0.0) -> float:
    """Probe media duration in seconds using ffprobe or ffmpeg banner output.

    Falls back to invoking FFmpeg directly if ffprobe is not available on PATH.
    """
    target = Path(media_path).resolve()
    if not target.is_file():
        return default

    # 1. Try compositor's probe_media_duration (works if ffprobe is on PATH)
    dur = probe_media_duration(target, default=0.0)
    if dur > 0.0:
        return dur

    # 2. Fall back to FFmpeg directly (which is bundled with imageio_ffmpeg)
    ffmpeg_bin = get_ffmpeg_path()
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-i", str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
        )
        text = proc.stderr or proc.stdout
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        if match:
            h = float(match.group(1))
            m = float(match.group(2))
            s = float(match.group(3))
            val = h * 3600.0 + m * 60.0 + s
            if val > 0.0:
                return val
    except Exception as exc:
        logger.debug("FFmpeg duration probe failed for %s: %s", target, exc)

    return default


def is_acceptable_candidate(
    title: str,
    description: str = "",
    duration: float | None = None,
    min_duration: float = 60.0,
) -> tuple[bool, str]:
    """Check if a video candidate is acceptable and free of obvious watermarks/music.

    Args:
        title: Video title string.
        description: Video description text.
        duration: Video duration in seconds.
        min_duration: Minimum required duration in seconds (default 60s).

    Returns:
        (is_acceptable, reason_description)
    """
    if duration is not None and duration > 0 and duration < min_duration:
        return False, f"Duration {duration:.1f}s is less than minimum {min_duration:.1f}s"

    text = f"{title} {description}".lower()

    # Negative patterns indicating unwanted overlays, strikes, or restrictions
    negative_patterns = [
        r"\bwatermarked\b",
        r"\b(with|has|contains)\s+watermark\b",
        r"\b(with|has|contains)\s+copyrighted?\s+music\b",
        r"\bcopyright\s+strike\b",
        r"\bdo\s+not\s+(reupload|reuse|use)\b",
        r"\ball\s+rights\s+reserved\b",
    ]
    for pat in negative_patterns:
        if re.search(pat, text):
            # Check for negation e.g. 'no watermark', 'without watermark'
            negated = re.search(r"\b(no|without|zero|free\s+of)\s+watermark\b", text)
            if "watermark" in pat and negated:
                continue
            return False, f"Matched negative keyword pattern: {pat}"

    return True, "OK"


def score_candidate(title: str, description: str = "", duration: float | None = None) -> int:
    """Calculate ranking preference score for candidate sorting."""
    text = f"{title} {description}".lower()
    score = 0

    if duration and duration >= 60.0:
        score += 10
    if duration and 120.0 <= duration <= 1800.0:
        score += 15

    # Positive copyright-free indicators
    if "no copyright" in text or "copyright free" in text:
        score += 25
    if "royalty free" in text:
        score += 20
    if "free to use" in text:
        score += 20
    if "cc0" in text or "creative commons" in text:
        score += 20
    if "no commentary" in text:
        score += 15
    if "gameplay" in text:
        score += 10
    if "vertical" in text or "9:16" in text or "9x16" in text or "shorts" in text:
        score += 15

    return score


def search_category_videos(
    query: str,
    max_results: int = 5,
    yt_dlp_binary: str = "yt-dlp",
    timeout_sec: float = 45.0,
    min_duration: float = 60.0,
) -> list[dict[str, Any]]:
    """Search YouTube via yt-dlp flat-playlist search.

    Args:
        query: YouTube search query.
        max_results: Maximum candidates to return (default 5).
        yt_dlp_binary: Path or command name for yt-dlp binary.
        timeout_sec: Search timeout in seconds.
        min_duration: Minimum acceptable duration in seconds.

    Returns:
        List of candidate dictionaries sorted by preference score.
    """
    bin_name = resolve_yt_dlp_binary(yt_dlp_binary)
    # Fetch a few extra items so we can filter and still meet max_results
    search_count = max(max_results * 2, 5)
    cmd = [
        bin_name,
        f"ytsearch{search_count}:{query}",
        "--dump-json",
        "--flat-playlist",
        "--no-warnings",
        "--no-playlist",
    ]

    logger.info("Searching YouTube via yt-dlp: '%s' (limit=%d)", query, search_count)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        if proc.returncode != 0:
            logger.warning(
                "yt-dlp search exited with code %d: %s",
                proc.returncode,
                (proc.stderr or proc.stdout).strip()[:300],
            )
            return []
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp search timed out after %.1fs for query '%s'", timeout_sec, query)
        return []
    except Exception as exc:
        logger.warning("Error running yt-dlp search: %s", exc)
        return []

    candidates: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        vid_id = entry.get("id")
        if not vid_id:
            continue

        title = entry.get("title") or ""
        desc = entry.get("description") or ""
        raw_dur = entry.get("duration")
        try:
            dur = float(raw_dur) if raw_dur is not None else 0.0
        except (ValueError, TypeError):
            dur = 0.0

        acceptable, reason = is_acceptable_candidate(title, desc, dur, min_duration=min_duration)
        if not acceptable:
            logger.debug("Candidate %s rejected: %s", vid_id, reason)
            continue

        url = (
            entry.get("url")
            or entry.get("webpage_url")
            or f"https://www.youtube.com/watch?v={vid_id}"
        )
        channel = entry.get("channel") or entry.get("uploader") or "Unknown"
        score = score_candidate(title, desc, dur)

        candidates.append(
            {
                "id": vid_id,
                "title": title,
                "url": url,
                "duration": dur,
                "channel": channel,
                "score": score,
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:max_results]


def download_source_video(
    url_or_id: str,
    output_path: Path,
    max_duration_sec: float = 360.0,
    yt_dlp_binary: str = "yt-dlp",
    ffmpeg_binary: str | None = None,
    timeout_sec: float = 240.0,
) -> Path | None:
    """Download source video (or leading slice) via yt-dlp with <=1080p format cap.

    Args:
        url_or_id: Source URL or video id.
        output_path: Destination path for downloaded video.
        max_duration_sec: Maximum leading duration to download in seconds.
        yt_dlp_binary: yt-dlp binary name or path.
        ffmpeg_binary: FFmpeg binary path for yt-dlp slice cuts/merging.
        timeout_sec: Subprocess timeout in seconds.

    Returns:
        Path to downloaded video file, or None if download failed.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    bin_name = resolve_yt_dlp_binary(yt_dlp_binary)
    ffmpeg_bin = ffmpeg_binary or get_ffmpeg_path()
    url = (
        url_or_id
        if url_or_id.startswith("http")
        else f"https://www.youtube.com/watch?v={url_or_id}"
    )

    m, s = divmod(int(max_duration_sec), 60)
    section_arg = f"*00:00-{m:02d}:{s:02d}"

    format_spec = (
        "bestvideo[vcodec^=avc1][height<=1080]/bestvideo[height<=1080][ext=mp4]/"
        "bestvideo[height<=1080]/best[height<=1080]/best"
    )

    tmp_path = output_path.with_name(f".dl_{uuid.uuid4().hex[:8]}_{output_path.name}")
    cmd = [
        bin_name,
        "--ffmpeg-location",
        ffmpeg_bin,
        "--no-playlist",
        "--no-warnings",
        "-f",
        format_spec,
        "--download-sections",
        section_arg,
        "--force-keyframes-at-cuts",
        "--merge-output-format",
        "mp4",
        "-o",
        str(tmp_path),
        url,
    ]

    def _cleanup() -> None:
        for leftover in output_path.parent.glob(f"{glob.escape(tmp_path.stem)}*"):
            try:
                leftover.unlink()
            except OSError:
                pass

    def _produced() -> Path | None:
        if tmp_path.is_file() and tmp_path.stat().st_size > 0:
            return tmp_path
        for cand in output_path.parent.glob(f"{glob.escape(tmp_path.stem)}*"):
            if (
                cand.is_file()
                and cand.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
                and not re.search(r"\.f\d+\.", cand.name)
                and cand.stat().st_size > 0
            ):
                return cand
        return None

    logger.info("Downloading source video with yt-dlp: %s -> %s", url, output_path.name)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        if proc.returncode != 0:
            logger.warning(
                "yt-dlp download failed (rc=%d): %s",
                proc.returncode,
                (proc.stderr or proc.stdout).strip()[:300],
            )
            _cleanup()  # never keep a partial slice from the failed attempt
            cmd_fallback = [
                bin_name,
                "--ffmpeg-location",
                ffmpeg_bin,
                "--no-playlist",
                "--no-warnings",
                "-f",
                format_spec,
                "--merge-output-format",
                "mp4",
                "-o",
                str(tmp_path),
                url,
            ]
            logger.info("Retrying without --download-sections for %s", url)
            proc = subprocess.run(
                cmd_fallback,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                _cleanup()
                return None

        produced = _produced()
        if produced is None:
            _cleanup()
            return None
        final = (
            output_path
            if produced.suffix.lower() == output_path.suffix.lower()
            else output_path.with_suffix(produced.suffix.lower())
        )
        os.replace(produced, final)
        _cleanup()
        return final
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp download timed out (%.1fs) for %s", timeout_sec, url)
        _cleanup()
        return None
    except Exception as exc:
        logger.warning("Error downloading video %s: %s", url, exc)
        _cleanup()
        return None


def cut_video_segments(
    source_path: Path,
    category_dir: Path,
    video_id: str,
    source_title: str,
    source_url: str,
    channel: str,
    segment_duration_sec: float = 60.0,
    max_segments: int = 3,
    min_segment_sec: float = 30.0,
    ffmpeg_binary: str | None = None,
    timeout_sec: float = 120.0,
    license_note: str = (
        "Personal/educational royalty-free gameplay background. Verify license prior to commercial monetization."
    ),
) -> list[dict[str, Any]]:
    """Cut source video into 30-90s vertical-friendly segments using FFmpeg.

    Saves segments to:
        category_dir / f"{video_id}_{idx:02d}.mp4"

    Args:
        source_path: Path to the downloaded source video.
        category_dir: Destination folder (e.g. assets/backgrounds/<category>).
        video_id: Source video ID string.
        source_title: Title of the source video.
        source_url: URL of the source video.
        channel: Channel or creator name.
        segment_duration_sec: Duration in seconds for each cut (clamped 30-90s).
        max_segments: Maximum number of segments to generate from this video.
        min_segment_sec: Minimum length in seconds for a segment to be kept.
        ffmpeg_binary: Optional override for FFmpeg binary path.
        timeout_sec: FFmpeg subprocess timeout in seconds.
        license_note: Notice regarding footage licensing.

    Returns:
        List of created or existing clip metadata dictionaries.
    """
    source_path = Path(source_path).resolve()
    category_dir = Path(category_dir).resolve()
    category_dir.mkdir(parents=True, exist_ok=True)

    total_dur = probe_video_duration(source_path, default=0.0)
    if total_dur < min_segment_sec:
        logger.warning(
            "Source video %s duration %.1fs is below min segment %.1fs",
            source_path.name,
            total_dur,
            min_segment_sec,
        )
        return []

    ffmpeg_bin = ffmpeg_binary or get_ffmpeg_path()
    clip_dur = max(30.0, min(90.0, float(segment_duration_sec)))

    segments: list[dict[str, Any]] = []
    start_sec = 0.0
    seg_idx = 1

    while start_sec < total_dur and seg_idx <= max_segments:
        remaining = total_dur - start_sec
        if remaining < min_segment_sec:
            break

        actual_seg_dur = min(clip_dur, remaining)
        dest_filename = f"{video_id}_{seg_idx:02d}.mp4"
        dest_path = category_dir / dest_filename
        relative_path = f"assets/backgrounds/{category_dir.name}/{dest_filename}"

        clip_meta: dict[str, Any] = {
            "id": f"{video_id}_{seg_idx:02d}",
            "filename": dest_filename,
            "path": relative_path,
            "source_id": video_id,
            "source_title": source_title,
            "source_url": source_url,
            "channel": channel,
            "duration": round(actual_seg_dur, 2),
            "license_note": license_note,
            "segment_index": seg_idx,
            "collected_at": datetime.now(UTC).isoformat(),
        }

        # Idempotency check: skip cut if destination file already exists
        if dest_path.is_file() and dest_path.stat().st_size > 1024:
            logger.info("Segment %s already exists, skipping cut", dest_path.name)
            existing_dur = probe_video_duration(dest_path, default=actual_seg_dur)
            if existing_dur > 0:
                clip_meta["duration"] = round(existing_dur, 2)
            segments.append(clip_meta)
            start_sec += actual_seg_dur
            seg_idx += 1
            continue

        tmp_dest = dest_path.with_name(f".tmp_{dest_filename}")
        cmd_copy = [
            ffmpeg_bin,
            "-y",
            "-ss",
            f"{start_sec:.2f}",
            "-i",
            str(source_path),
            "-t",
            f"{actual_seg_dur:.2f}",
            "-c:v",
            "copy",
            "-an",
            "-avoid_negative_ts",
            "make_zero",
            str(tmp_dest),
        ]

        success = False
        try:
            proc = subprocess.run(
                cmd_copy,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
            )
            if proc.returncode == 0 and tmp_dest.is_file() and tmp_dest.stat().st_size > 1024:
                os.replace(tmp_dest, dest_path)
                success = True
            else:
                logger.debug(
                    "Stream copy failed (rc=%d), falling back to re-encode", proc.returncode
                )
        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg stream copy timed out for segment %d", seg_idx)
            if tmp_dest.is_file():
                tmp_dest.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug("FFmpeg copy exception: %s", exc)

        if not success:
            cmd_reencode = [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{start_sec:.2f}",
                "-i",
                str(source_path),
                "-t",
                f"{actual_seg_dur:.2f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(tmp_dest),
            ]
            try:
                proc = subprocess.run(
                    cmd_reencode,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_sec,
                )
                if proc.returncode == 0 and tmp_dest.is_file() and tmp_dest.stat().st_size > 1024:
                    os.replace(tmp_dest, dest_path)
                    success = True
                else:
                    if tmp_dest.is_file():
                        tmp_dest.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("FFmpeg re-encode failed for segment %d: %s", seg_idx, exc)
                if tmp_dest.is_file():
                    tmp_dest.unlink(missing_ok=True)

        if success and dest_path.is_file():
            real_dur = probe_video_duration(dest_path, default=actual_seg_dur)
            clip_meta["duration"] = round(real_dur, 2)
            segments.append(clip_meta)
            logger.info("Successfully cut segment: %s (%.1fs)", dest_path.name, real_dur)
        else:
            logger.warning("Failed to create segment %s", dest_filename)

        start_sec += actual_seg_dur
        seg_idx += 1

    return segments


def collect_category_gameplay(
    category: str = "minecraft",
    query: str | None = None,
    max_videos: int = 2,
    segment_duration_sec: float = 60.0,
    max_segments_per_video: int = 3,
    assets_dir: Path | None = None,
    yt_dlp_binary: str = "yt-dlp",
    ffmpeg_binary: str | None = None,
    timeout_sec: float = 240.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Search, download, segment, and index gameplay footage for a category.

    Args:
        category: Category identifier (e.g. 'minecraft', 'subway', 'gta').
        query: Optional custom search query (defaults to preset query).
        max_videos: Number of search result videos to process (default 2).
        segment_duration_sec: Duration in seconds for each cut (default 60s).
        max_segments_per_video: Max segments per video (default 3).
        assets_dir: Optional override for assets root directory.
        yt_dlp_binary: yt-dlp binary path or command name.
        ffmpeg_binary: FFmpeg binary path.
        timeout_sec: Download/cut timeout in seconds.
        dry_run: If True, only search and report without downloading.

    Returns:
        Summary dict containing category, counts, and clips list.
    """
    clean_cat = normalize_category_name(category)
    search_query = query or DEFAULT_CATEGORY_QUERIES.get(
        clean_cat, f"{clean_cat} gameplay no copyright no commentary"
    )

    base = assets_dir or get_assets_dir()
    category_dir = base / "backgrounds" / clean_cat
    category_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Collecting gameplay for category '%s' (query='%s', max_videos=%d, dry_run=%s)",
        clean_cat,
        search_query,
        max_videos,
        dry_run,
    )

    # Ask for a few extra results so failed downloads can be replaced.
    candidates = search_category_videos(
        query=search_query,
        max_results=max_videos + 3,
        yt_dlp_binary=yt_dlp_binary,
        timeout_sec=45.0,
    )

    if not candidates:
        logger.warning("No search candidates found for category '%s'", clean_cat)
        manifest = load_manifest(clean_cat, assets_dir=base)
        return {
            "category": clean_cat,
            "query": search_query,
            "dry_run": dry_run,
            "candidates_found": 0,
            "videos_processed": 0,
            "segments_created": 0,
            "total_clips": len(manifest.get("clips", [])),
            "clips": manifest.get("clips", []),
        }

    if dry_run:
        logger.info("[DRY-RUN] Found %d candidates for '%s'", len(candidates), clean_cat)
        return {
            "category": clean_cat,
            "query": search_query,
            "dry_run": True,
            "candidates_found": len(candidates),
            "candidates": candidates,
            "videos_processed": 0,
            "segments_created": 0,
            "new_clips": [],
        }

    manifest = load_manifest(clean_cat, assets_dir=base)
    existing_clip_map: dict[str, dict[str, Any]] = {
        c["filename"]: c for c in manifest.get("clips", []) if "filename" in c
    }

    cache_dir = category_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    videos_processed = 0
    new_clips: list[dict[str, Any]] = []

    for cand in candidates:
        # Failed downloads do not count, so keep going until max_videos are collected.
        if videos_processed >= max_videos:
            break
        vid_id = cand["id"]
        first_segment = category_dir / f"{vid_id}_01.mp4"

        # Check if already processed
        if first_segment.is_file() and first_segment.stat().st_size > 1024:
            logger.info("Video %s already collected, checking existing segments", vid_id)
            for p in sorted(category_dir.glob(f"{vid_id}_*.mp4")):
                if p.name not in existing_clip_map:
                    entry = {
                        "id": p.stem,
                        "filename": p.name,
                        "path": f"assets/backgrounds/{clean_cat}/{p.name}",
                        "source_id": vid_id,
                        "source_title": cand["title"],
                        "source_url": cand["url"],
                        "channel": cand["channel"],
                        "duration": round(probe_video_duration(p, default=segment_duration_sec), 2),
                        "license_note": (
                            "Royalty-free / Creative Commons gameplay. User is responsible for verifying "
                            "source license before commercial monetization."
                        ),
                    }
                    existing_clip_map[p.name] = entry
                    new_clips.append(entry)
            videos_processed += 1
            continue

        # Download source video to cache
        source_dest = cache_dir / f"{vid_id}_source.mp4"
        max_source_dur = float(max_segments_per_video * segment_duration_sec + 30.0)
        dl_path = None
        # One retry: a slow connection often times out once and then succeeds.
        for attempt in range(2):
            dl_path = download_source_video(
                url_or_id=cand["url"],
                output_path=source_dest,
                max_duration_sec=max_source_dur,
                yt_dlp_binary=yt_dlp_binary,
                ffmpeg_binary=ffmpeg_binary,
                timeout_sec=timeout_sec,
            )
            if dl_path and dl_path.is_file():
                break
            logger.info("Download attempt %d failed for %s", attempt + 1, vid_id)

        if not dl_path or not dl_path.is_file():
            logger.warning("Failed to download source video %s, skipping", vid_id)
            continue

        # Cut segments with FFmpeg
        cut_clips = cut_video_segments(
            source_path=dl_path,
            category_dir=category_dir,
            video_id=vid_id,
            source_title=cand["title"],
            source_url=cand["url"],
            channel=cand["channel"],
            segment_duration_sec=segment_duration_sec,
            max_segments=max_segments_per_video,
            ffmpeg_binary=ffmpeg_binary,
        )

        # Clean up temporary source file
        try:
            dl_path.unlink(missing_ok=True)
        except OSError:
            pass

        for clip in cut_clips:
            existing_clip_map[clip["filename"]] = clip
            new_clips.append(clip)

        videos_processed += 1

    # Clean up empty cache dir
    try:
        if cache_dir.is_dir() and not any(cache_dir.iterdir()):
            cache_dir.rmdir()
    except OSError:
        pass

    # Save manifest
    manifest["clips"] = list(existing_clip_map.values())
    manifest["category"] = clean_cat
    save_manifest(manifest, clean_cat, assets_dir=base)

    logger.info(
        "Category '%s' collection complete: %d videos processed, %d total clips in manifest",
        clean_cat,
        videos_processed,
        len(manifest["clips"]),
    )

    return {
        "category": clean_cat,
        "query": search_query,
        "dry_run": False,
        "candidates_found": len(candidates),
        "videos_processed": videos_processed,
        "segments_created": len(new_clips),
        "total_clips": len(manifest["clips"]),
        "clips": manifest["clips"],
    }


def collect_gameplay(
    categories: Sequence[str] = ("minecraft", "subway"),
    per_category: int = 2,
    segment_duration_sec: float = 60.0,
    assets_dir: Path | None = None,
    dry_run: bool = False,
    timeout_sec: float = 240.0,
) -> dict[str, Any]:
    """Collect gameplay videos across multiple categories with failure tolerance.

    Args:
        categories: Sequence of category names to process.
        per_category: Number of videos per category.
        segment_duration_sec: Duration in seconds for each cut.
        assets_dir: Optional override for assets root.
        dry_run: If True, perform search only without downloading.
        timeout_sec: Subprocess timeout in seconds.

    Returns:
        Dict mapping category name to result summary dictionary.
    """
    results: dict[str, Any] = {}
    for cat in categories:
        clean_cat = normalize_category_name(cat)
        try:
            res = collect_category_gameplay(
                category=clean_cat,
                max_videos=per_category,
                segment_duration_sec=segment_duration_sec,
                assets_dir=assets_dir,
                dry_run=dry_run,
                timeout_sec=timeout_sec,
            )
            results[clean_cat] = res
        except Exception as exc:
            logger.error("Error collecting gameplay for category '%s': %s", clean_cat, exc)
            results[clean_cat] = {
                "category": clean_cat,
                "error": str(exc),
                "segments_created": 0,
            }
    return results


def get_categories_summary(assets_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return inventory and manifest summary for all known background categories."""
    base = assets_dir or get_assets_dir()
    bg_root = base / "backgrounds"

    all_cats = list(DEFAULT_CATEGORY_QUERIES.keys())
    if bg_root.is_dir():
        for d in sorted(bg_root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in all_cats:
                all_cats.append(d.name)

    valid_exts = {".mp4", ".mov", ".mkv", ".webm"}
    summary: list[dict[str, Any]] = []

    for cat in all_cats:
        cat_dir = bg_root / cat
        clip_count = 0
        if cat_dir.is_dir():
            clip_count = sum(
                1
                for p in cat_dir.iterdir()
                if p.is_file() and p.suffix.lower() in valid_exts and p.stat().st_size > 0
            )
        manifest_file = cat_dir / "manifest.json"
        manifest_exists = manifest_file.is_file() and manifest_file.stat().st_size > 0
        manifest_clips_count = 0
        if manifest_exists:
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                manifest_clips_count = (
                    len(data.get("clips", [])) if isinstance(data, dict) else len(data)
                )
            except Exception:
                pass

        summary.append(
            {
                "category": cat,
                "clips_count": clip_count,
                "manifest_clips_count": manifest_clips_count,
                "has_manifest": manifest_exists,
                "default_query": DEFAULT_CATEGORY_QUERIES.get(cat, ""),
            }
        )

    return summary
