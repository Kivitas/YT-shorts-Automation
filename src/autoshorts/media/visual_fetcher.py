"""Visual B-Roll and Stock Asset Fetcher for AutoShorts pipeline.

Provides discovery, downloading, and local caching of vertical (9:16) stock video clips
and images from stock providers (Pexels) and local synthetic fallbacks.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from autoshorts.core.config import load_config
from autoshorts.core.paths import PROJECT_ROOT
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = PROJECT_ROOT / "output" / "broll_cache"
PEXELS_VIDEOS_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTOS_SEARCH_URL = "https://api.pexels.com/v1/search"


@dataclass
class VisualAsset:
    """Descriptor for a fetched visual asset (video clip or stock image)."""

    path: Path
    url: str = ""
    duration: float = 0.0
    width: int = 1080
    height: int = 1920
    aspect: str = "9:16"
    source: str = "pexels"
    keyword: str = ""
    media_type: str = "video"  # "video" or "image"

    def exists(self) -> bool:
        """True if the local file exists and is non-empty."""
        return self.path.is_file() and self.path.stat().st_size > 0


class VisualFetcher:
    """Fetches vertical B-roll clips and stock photos with automatic fallback and caching."""

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
        timeout_sec: float = 20.0,
    ) -> None:
        self.api_key = api_key or self._resolve_api_key()
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_sec = timeout_sec

    @staticmethod
    def _resolve_api_key() -> str | None:
        """Resolve Pexels API key from settings or environment."""
        try:
            cfg = load_config()
            if hasattr(cfg, "get_pexels_api_key_str"):
                key = cfg.get_pexels_api_key_str()
                if key:
                    return key
        except Exception:
            pass
        return os.environ.get("PEXELS_API_KEY")

    def _cache_path(self, identifier: str, ext: str = ".mp4") -> Path:
        """Generate a deterministic cache path for an asset URL or query."""
        h = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"broll_{h}{ext}"

    def fetch_broll_clips(
        self,
        queries: list[str] | str,
        target_duration: float = 3.0,
        max_results: int = 3,
        orientation: str = "portrait",
    ) -> list[VisualAsset]:
        """Search and download vertical stock video clips matching queries.

        If queries is a single string, it is treated as a single query.
        Falls back cleanly when no API key is available or network fails.
        """
        if isinstance(queries, str):
            query_list = [queries]
        else:
            query_list = list(queries)

        assets: list[VisualAsset] = []
        if not self.api_key:
            logger.info("No PEXELS_API_KEY available; returning synthetic/empty B-roll assets.")
            return assets

        headers = {"Authorization": self.api_key}
        for q in query_list:
            if len(assets) >= max_results:
                break
            try:
                params = {
                    "query": q,
                    "orientation": orientation,
                    "per_page": min(10, max_results * 2),
                }
                with httpx.Client(timeout=self.timeout_sec) as client:
                    resp = client.get(PEXELS_VIDEOS_SEARCH_URL, headers=headers, params=params)
                    if resp.status_code != 200:
                        logger.warning("Pexels video query %r failed: HTTP %d", q, resp.status_code)
                        continue
                    data = resp.json()

                videos = data.get("videos", [])
                for vid in videos:
                    if len(assets) >= max_results:
                        break
                    duration = float(vid.get("duration", 0.0))
                    # Pick best portrait file (height > width)
                    files = vid.get("video_files", [])
                    chosen_file = None
                    for vf in files:
                        w = vf.get("width") or 0
                        h = vf.get("height") or 0
                        link = vf.get("link")
                        if link and h > w:
                            chosen_file = vf
                            if w >= 720:
                                break
                    if not chosen_file and files:
                        chosen_file = files[0]

                    if not chosen_file or not chosen_file.get("link"):
                        continue

                    url = chosen_file["link"]
                    w = chosen_file.get("width", 1080)
                    h = chosen_file.get("height", 1920)
                    dest = self._cache_path(url, ext=".mp4")

                    # Download if not already cached
                    if not dest.is_file() or dest.stat().st_size == 0:
                        self._download_file(url, dest)

                    if dest.is_file() and dest.stat().st_size > 0:
                        assets.append(
                            VisualAsset(
                                path=dest,
                                url=url,
                                duration=duration,
                                width=w,
                                height=h,
                                aspect="9:16" if h > w else "16:9",
                                source="pexels",
                                keyword=q,
                                media_type="video",
                            )
                        )
            except Exception as exc:
                logger.warning("Error fetching B-roll for query %r: %s", q, exc)

        return assets

    def fetch_stock_images(
        self,
        queries: list[str] | str,
        max_results: int = 3,
        orientation: str = "portrait",
    ) -> list[VisualAsset]:
        """Search and download stock images matching queries."""
        if isinstance(queries, str):
            query_list = [queries]
        else:
            query_list = list(queries)

        assets: list[VisualAsset] = []
        if not self.api_key:
            return assets

        headers = {"Authorization": self.api_key}
        for q in query_list:
            if len(assets) >= max_results:
                break
            try:
                params = {
                    "query": q,
                    "orientation": orientation,
                    "per_page": min(10, max_results * 2),
                }
                with httpx.Client(timeout=self.timeout_sec) as client:
                    resp = client.get(PEXELS_PHOTOS_SEARCH_URL, headers=headers, params=params)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()

                photos = data.get("photos", [])
                for photo in photos:
                    if len(assets) >= max_results:
                        break
                    srcs = photo.get("src", {})
                    url = srcs.get("portrait") or srcs.get("large") or srcs.get("original")
                    if not url:
                        continue
                    dest = self._cache_path(url, ext=".jpg")
                    if not dest.is_file() or dest.stat().st_size == 0:
                        self._download_file(url, dest)

                    if dest.is_file() and dest.stat().st_size > 0:
                        assets.append(
                            VisualAsset(
                                path=dest,
                                url=url,
                                duration=0.0,
                                width=photo.get("width", 1080),
                                height=photo.get("height", 1920),
                                aspect="9:16",
                                source="pexels",
                                keyword=q,
                                media_type="image",
                            )
                        )
            except Exception as exc:
                logger.warning("Error fetching stock image for %r: %s", q, exc)

        return assets

    def _download_file(self, url: str, dest: Path) -> Path:
        """Download remote URL to dest atomically."""
        tmp = dest.with_suffix(f"{dest.suffix}.tmp")
        try:
            with (
                httpx.Client(timeout=60.0, follow_redirects=True) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                with open(tmp, "wb") as f:
                    f.writelines(response.iter_bytes(chunk_size=65536))
            shutil.move(str(tmp), str(dest))
            return dest
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def generate_synthetic_broll(
        self,
        output_path: Path | str,
        duration: float = 3.0,
        width: int = 1080,
        height: int = 1920,
        color: str = "0x1A1E2E",
        pattern: str = "testsrc",
    ) -> VisualAsset:
        """Render a synthetic vertical video clip with FFmpeg for offline/test environments."""
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_bin = get_ffmpeg_path()
        if pattern == "color":
            source_filter = f"color=c={color}:s={width}x{height}:r=30:d={duration:g}"
        else:
            source_filter = f"testsrc=size={width}x{height}:rate=30:duration={duration:g}"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            source_filter,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-t",
            f"{duration:g}",
            str(dest),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60.0)
        except Exception:
            dest.unlink(missing_ok=True)
            raise

        return VisualAsset(
            path=dest,
            url="",
            duration=duration,
            width=width,
            height=height,
            aspect="9:16",
            source="synthetic",
            keyword="fallback",
            media_type="video",
        )


__all__ = ["DEFAULT_CACHE_DIR", "VisualAsset", "VisualFetcher"]
