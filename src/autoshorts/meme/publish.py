"""Publish rendered meme videos to YouTube (API, Studio, or Local export).

Manages daily upload caps, YouTube metadata (title <= 100 chars, description with 3-5 hashtags
from the 25-hashtag library, thumbnail), and quota failover.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autoshorts.core.config import Settings
from autoshorts.core.paths import atomic_write, get_data_dir
from autoshorts.core.state_manager import StateManager
from autoshorts.meme.types import MemeResult

logger = logging.getLogger(__name__)

MEME_DAILY_CAP = 25
_PT = ZoneInfo("America/Los_Angeles")
_LOCK = threading.Lock()


def _counter_path() -> Path:
    return get_data_dir() / "meme_uploads.json"


def _today_pt() -> str:
    return datetime.now(_PT).date().isoformat()


def _load_counts() -> dict[str, int]:
    path = _counter_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    counts: dict[str, int] = {}
    for k, v in data.items():
        try:
            counts[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return counts


def meme_uploads_today() -> int:
    """Number of meme videos uploaded on the current Pacific-Time day."""
    return _load_counts().get(_today_pt(), 0)


def meme_remaining_today(cap: int | None = None) -> int:
    eff_cap = cap if cap is not None else MEME_DAILY_CAP
    return max(0, eff_cap - meme_uploads_today())


def _record_meme_upload() -> int:
    with _LOCK:
        counts = _load_counts()
        today = _today_pt()
        counts[today] = counts.get(today, 0) + 1
        for day in sorted(counts)[:-7]:
            counts.pop(day, None)
        path = _counter_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(counts, indent=2), encoding="utf-8")
        return counts[today]


def publish_meme_video(
    result: MemeResult,
    config: Settings | None = None,
    state_manager: StateManager | None = None,
) -> str:
    """Publish a meme video to YouTube or export locally based on configured upload mode."""
    upload_mode = "local"
    if config and hasattr(config, "upload") and hasattr(config.upload, "mode"):
        upload_mode = config.upload.mode.lower()

    # Title clamping
    title = result.title[:95] if result.title else "Country Meme Animation"
    description = result.description or ""
    tags = [t.lstrip("#") for t in result.hashtags]

    logger.info("Publishing meme video (%s) in mode=%s", title, upload_mode)

    if upload_mode == "local":
        from autoshorts.uploader.local_exporter import LocalExportUploader

        uploader = LocalExportUploader()
        pub_result = uploader.upload(
            video_path=result.video_path,
            title=title,
            description=description,
            tags=tags,
            thumbnail_path=result.thumbnail_path,
        )
        _record_meme_upload()
        return str(pub_result)

    privacy = "public"
    if config and hasattr(config, "upload") and hasattr(config.upload, "privacy_status"):
        privacy = config.upload.privacy_status or "public"

    if upload_mode == "youtube_api" or upload_mode == "api":
        try:
            from autoshorts.uploader.youtube_api import YouTubeApiUploader

            uploader = YouTubeApiUploader(config=config)
            upload_res = uploader.upload(
                video_path=result.video_path,
                title=title,
                description=description,
                tags=tags,
                privacy=privacy,
                privacy_status=privacy,
                thumbnail_path=result.thumbnail_path,
                is_short=(result.aspect_ratio == "9:16"),
            )
            video_id = getattr(upload_res, "video_id", str(upload_res))
            youtube_url = (
                getattr(upload_res, "url", None)
                or getattr(upload_res, "youtube_url", None)
                or f"https://youtu.be/{video_id}"
            )
            _record_meme_upload()
            return youtube_url
        except Exception as e:
            logger.warning("YouTube API upload failed, falling back to local export: %s", e)
            from autoshorts.uploader.local_exporter import LocalExportUploader

            uploader = LocalExportUploader()
            pub_result = uploader.upload(
                video_path=result.video_path,
                title=title,
                description=description,
                tags=tags,
                thumbnail_path=result.thumbnail_path,
            )
            _record_meme_upload()
            return str(pub_result)

    # Fallback to local
    from autoshorts.uploader.local_exporter import LocalExportUploader

    uploader = LocalExportUploader()
    pub_result = uploader.upload(
        video_path=result.video_path,
        title=title,
        description=description,
        tags=tags,
        thumbnail_path=result.thumbnail_path,
    )
    _record_meme_upload()
    return str(pub_result)
