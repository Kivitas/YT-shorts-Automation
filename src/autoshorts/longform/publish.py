"""Publish rendered long-form videos to YouTube (API, Studio, or Local export), capped at 4 per Pacific-Time day."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autoshorts.core.paths import atomic_write, get_data_dir
from autoshorts.longform.types import LongformResult

logger = logging.getLogger(__name__)

LONGFORM_DAILY_CAP = 4
_PT = ZoneInfo("America/Los_Angeles")
_LOCK = threading.Lock()


def _counter_path() -> Path:
    return get_data_dir() / "longform_uploads.json"


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
            continue  # a corrupt entry must not crash every later publish
    return counts


def longform_uploads_today() -> int:
    """Number of long-form videos already uploaded on the current Pacific-Time day."""
    return _load_counts().get(_today_pt(), 0)


def longform_remaining_today() -> int:
    return max(0, LONGFORM_DAILY_CAP - longform_uploads_today())


def _record_upload() -> None:
    counts = _load_counts()
    today = _today_pt()
    counts[today] = counts.get(today, 0) + 1
    # Keep only the last 7 days so the file stays small.
    for day in sorted(counts)[:-7]:
        counts.pop(day, None)
    path = _counter_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(counts, indent=2), encoding="utf-8")


def publish_longform(result: LongformResult, config=None, state_manager=None) -> str:
    """Upload one rendered long-form video and return its YouTube/export URL.

    Respects `cfg.upload.mode` ('api', 'studio', or 'local') and raises RuntimeError
    when today's cap of 4 is already used or the upload is not verified.
    """
    from autoshorts.core.config import load_config
    from autoshorts.core.state_manager import StateManager
    from autoshorts.quota.quota_tracker import QuotaTracker
    from autoshorts.uploader.local_exporter import LocalExportUploader
    from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
    from autoshorts.uploader.youtube_api import YouTubeApiUploader

    with _LOCK:
        if longform_remaining_today() <= 0:
            raise RuntimeError(
                f"Long-form daily cap reached ({LONGFORM_DAILY_CAP} uploads per Pacific day)."
            )
        cfg = config or load_config(None)
        upload_cfg = getattr(cfg, "upload", None)
        upload_mode = str(getattr(upload_cfg, "mode", "api") or "api").strip().lower()

        if upload_mode == "studio":
            uploader = PlaywrightStudioUploader(config=cfg)
        elif upload_mode == "local":
            uploader = LocalExportUploader()
        else:
            state = state_manager or StateManager()
            uploader = YouTubeApiUploader(
                config=cfg, quota_tracker=QuotaTracker(state_manager=state, config=cfg)
            )

        upload_result = uploader.upload(
            video_path=result.video_path,
            title=result.title,
            description=result.description,
            tags=list(result.hashtags),
            privacy_status=getattr(upload_cfg, "default_privacy_status", "unlisted"),
            category_id=getattr(upload_cfg, "category_id", "27"),
            is_short=False,
            thumbnail_path=getattr(result, "thumbnail_path", None),
        )
        if not getattr(upload_result, "url", None):
            raise RuntimeError("YouTube upload did not return a video URL.")
        _record_upload()
        logger.info("Published long-form video %s (mode=%s)", upload_result.url, upload_mode)
        return str(upload_result.url)
