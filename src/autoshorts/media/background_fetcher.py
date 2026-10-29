"""Curated royalty-free background asset auto-fetcher and inventory manager.

Provides:
- Curated lists of CC0 / royalty-free vertical Minecraft Parkour and Subway Surfers gameplay.
- Automatic on-demand downloading of sample background clips using yt-dlp.
- Inventory queries reporting clip counts, filenames, durations, and sizes.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoshorts.core.paths import get_assets_dir, resolve_yt_dlp_binary
from autoshorts.media.compositor import has_video_stream, probe_media_duration

logger = logging.getLogger(__name__)

# Curated royalty-free / Creative Commons gameplay clips
CURATED_BACKGROUND_SOURCES: dict[str, list[dict[str, str]]] = {
    "minecraft": [
        {
            "id": "mc_parkour_01",
            "title": "Minecraft Parkour Gameplay (Relaxing / CC / No Copyright)",
            "url": "https://www.youtube.com/watch?v=n_Dv4JMiwK8",
            "section_start": "00:00",
            "section_end": "01:30",
        },
        {
            "id": "mc_parkour_02",
            "title": "Minecraft Parkour Background 60fps (Free To Use)",
            "url": "https://www.youtube.com/watch?v=intRX7BRGa0",
            "section_start": "00:00",
            "section_end": "01:30",
        },
        {
            "id": "mc_parkour_03",
            "title": "Minecraft Parkour Creative Commons Clip",
            "url": "https://www.youtube.com/watch?v=qWbHSOv6ojQ",
            "section_start": "00:00",
            "section_end": "01:30",
        },
    ],
    "subway": [
        {
            "id": "subway_01",
            "title": "Subway Surfers Vertical Gameplay (No Copyright / Free To Use)",
            "url": "https://www.youtube.com/watch?v=zZ7AimPACzc",
            "section_start": "00:00",
            "section_end": "01:30",
        },
        {
            "id": "subway_02",
            "title": "Subway Surfers Free To Use Background Footage",
            "url": "https://www.youtube.com/watch?v=u7kdmn333EE",
            "section_start": "00:00",
            "section_end": "01:30",
        },
        {
            "id": "subway_03",
            "title": "Subway Surfers Vertical Gameplay CC",
            "url": "https://www.youtube.com/watch?v=7uKj4bIqL_8",
            "section_start": "00:00",
            "section_end": "01:30",
        },
    ],
    "gta": [
        {
            "id": "gta_01",
            "title": "GTA V Mega Ramp Stunts (Royalty Free / CC)",
            "url": "https://www.youtube.com/watch?v=FqV18683X0U",
            "section_start": "00:00",
            "section_end": "01:30",
        },
    ],
}


def normalize_category(category: str | None) -> str:
    """Normalize background category alias into standard directory key."""
    cat = (category or "minecraft").strip().lower()
    if cat in ("minecraft", "mc"):
        return "minecraft"
    if cat in ("subway", "subwaysurfers", "subway_surfers"):
        return "subway"
    if cat in ("gta", "gtav", "gta5"):
        return "gta"
    return cat


def download_clip_with_ytdlp(
    url: str,
    output_path: Path,
    section_start: str = "00:00",
    section_end: str = "01:30",
    yt_dlp_binary: str = "yt-dlp",
    timeout_sec: float = 180.0,
) -> Path | None:
    """Download a short sample slice of video using yt-dlp to output_path.

    Args:
        url: Video source URL.
        output_path: Destination MP4 file path.
        section_start: Start timestamp string ('MM:SS').
        section_end: End timestamp string ('MM:SS').
        yt_dlp_binary: yt-dlp binary command name or path.
        timeout_sec: Maximum timeout in seconds.

    Returns:
        Path of the downloaded video file if successful, otherwise None.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    bin_name = resolve_yt_dlp_binary(yt_dlp_binary)
    section_arg = f"*{section_start}-{section_end}"

    cmd = [
        bin_name,
        "--no-playlist",
        "--no-warnings",
        "--format",
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[ext=mp4]/best",
        "--download-sections",
        section_arg,
        "--force-keyframes-at-cuts",
        "--merge-output-format",
        "mp4",
    ]

    try:
        from autoshorts.media.encoder_probe import get_ffmpeg_path

        ffmpeg_bin = get_ffmpeg_path()
        if ffmpeg_bin:
            cmd.extend(["--ffmpeg-location", ffmpeg_bin])
    except Exception:
        pass

    # Download beside the target under a temp name and only publish a validated file, so a
    # timeout, crash or truncated download can never be cached as a good background.
    tmp_path = output_path.with_name(f".dl_{uuid.uuid4().hex[:8]}_{output_path.name}")
    cmd.extend(
        [
            "-o",
            str(tmp_path),
            url,
        ]
    )

    def _cleanup() -> None:
        for leftover in output_path.parent.glob(f"{glob.escape(tmp_path.stem)}*"):
            try:
                leftover.unlink()
            except OSError:
                pass

    logger.info("Downloading background sample: %s -> %s", url, output_path.name)
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
                "yt-dlp exited with code %d when downloading background sample %s: %s",
                proc.returncode,
                url,
                proc.stderr.strip() if proc.stderr else proc.stdout.strip(),
            )
            _cleanup()
            return None

        produced: Path | None = None
        if tmp_path.is_file() and tmp_path.stat().st_size > 0:
            produced = tmp_path
        else:
            for candidate in output_path.parent.glob(f"{glob.escape(tmp_path.stem)}*"):
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
                    and not re.search(r"\.f\d+\.", candidate.name)
                    and candidate.stat().st_size > 0
                ):
                    produced = candidate
                    break

        if produced is None:
            logger.warning("yt-dlp exited with 0 but output file %s not found", output_path)
            _cleanup()
            return None

        if not has_video_stream(produced):
            logger.warning("Downloaded background %s is not a readable video; discarding", url)
            _cleanup()
            return None

        final = (
            output_path
            if produced.suffix.lower() == output_path.suffix.lower()
            else (output_path.with_suffix(produced.suffix.lower()))
        )
        os.replace(produced, final)
        _cleanup()
        logger.info("Successfully fetched background sample: %s", final)
        return final
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp download timed out for %s (timeout=%.1fs)", url, timeout_sec)
        _cleanup()
        return None
    except Exception as exc:
        logger.warning("Error invoking yt-dlp for %s: %s", url, exc)
        _cleanup()
        return None


def ensure_backgrounds_available(
    category: str = "minecraft",
    min_clips: int = 1,
    assets_dir: Path | None = None,
    timeout_sec: float = 180.0,
) -> list[Path]:
    """Check if assets/backgrounds/{category} has video files.

    If empty or fewer than min_clips, automatically downloads sample clips from
    curated copyright-free sources into the category folder (e.g. assets/backgrounds/{category}/{category}_bg_01.mp4).

    Args:
        category: Category name (e.g. 'minecraft', 'subway').
        min_clips: Minimum number of clips required.
        assets_dir: Optional override for assets root directory.
        timeout_sec: Subprocess download timeout in seconds.

    Returns:
        List of Path objects for available background videos in that category.
    """
    clean_cat = normalize_category(category)
    base = assets_dir or get_assets_dir()
    category_dir = base / "backgrounds" / clean_cat
    category_dir.mkdir(parents=True, exist_ok=True)

    valid_extensions = {".mp4", ".mov", ".mkv", ".webm"}
    existing = [
        p
        for p in category_dir.iterdir()
        if p.is_file() and p.suffix.lower() in valid_extensions and p.stat().st_size > 0
    ]
    existing.sort(key=lambda p: p.name)

    if len(existing) >= min_clips:
        return existing

    sources = CURATED_BACKGROUND_SOURCES.get(clean_cat, [])
    if not sources:
        logger.warning("No curated sources found for category '%s'", clean_cat)
        return existing

    logger.info(
        "Backgrounds folder '%s' has %d clips, need %d. Auto-fetching samples...",
        clean_cat,
        len(existing),
        min_clips,
    )

    for idx, src in enumerate(sources, start=1):
        if len(existing) >= min_clips:
            break

        dest_file = category_dir / f"{clean_cat}_bg_{idx:02d}.mp4"
        if dest_file.is_file() and dest_file.stat().st_size > 0:
            if dest_file not in existing:
                existing.append(dest_file)
            continue

        res = download_clip_with_ytdlp(
            url=src["url"],
            output_path=dest_file,
            section_start=src.get("section_start", "00:00"),
            section_end=src.get("section_end", "01:30"),
            timeout_sec=timeout_sec,
        )
        if res and res.is_file() and res not in existing:
            existing.append(res)

    existing.sort(key=lambda p: p.name)
    return existing


def download_preset_backgrounds(
    categories: Sequence[str] = ("minecraft", "subway"),
    assets_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Download sample clips for specified categories.

    Args:
        categories: Sequence of category names to ensure clips for.
        assets_dir: Optional override for assets root directory.

    Returns:
        dict mapping category names to lists of resolved clip paths as strings.
    """
    result: dict[str, list[str]] = {}
    for cat in categories:
        clean_cat = normalize_category(cat)
        paths = ensure_backgrounds_available(
            category=clean_cat,
            min_clips=1,
            assets_dir=assets_dir,
        )
        result[clean_cat] = [str(p.resolve()) for p in paths]
    return result


def get_backgrounds_inventory(
    assets_dir: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return count, file names, durations, and sizes of clips in assets/backgrounds/.

    Args:
        assets_dir: Optional override for assets root directory.

    Returns:
        dict mapping category name to list of clip metadata dictionaries:
        {
            "category": [
                {
                    "filename": str,
                    "path": str,
                    "duration": float,
                    "size_bytes": int,
                    "size_mb": float,
                    "category": str,
                }, ...
            ]
        }
    """
    base = assets_dir or get_assets_dir()
    bg_root = base / "backgrounds"
    bg_root.mkdir(parents=True, exist_ok=True)

    # Base categories guaranteed to be reported
    category_set: list[str] = ["minecraft", "subway"]
    if bg_root.is_dir():
        for entry in sorted(bg_root.iterdir()):
            if entry.is_dir() and entry.name not in category_set and not entry.name.startswith("."):
                category_set.append(entry.name)

    valid_extensions = {".mp4", ".mov", ".mkv", ".webm"}
    inventory: dict[str, list[dict[str, Any]]] = {}

    for cat in category_set:
        cat_dir = bg_root / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        clips: list[dict[str, Any]] = []

        if cat_dir.is_dir():
            for p in sorted(cat_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in valid_extensions:
                    try:
                        sz = p.stat().st_size
                    except OSError:
                        sz = 0
                    dur = probe_media_duration(p, default=0.0)
                    clips.append(
                        {
                            "filename": p.name,
                            "path": str(p.resolve()),
                            "duration": round(dur, 2),
                            "size_bytes": sz,
                            "size_mb": round(sz / (1024 * 1024), 2),
                            "category": cat,
                        }
                    )
        inventory[cat] = clips

    return inventory
