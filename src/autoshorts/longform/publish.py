"""Publish rendered long-form videos to YouTube (API, Studio, or Local export).

The per-Pacific-Time-day cap comes from ``upload.longform_daily_cap`` in the config.
``LONGFORM_DAILY_CAP`` is only the fallback used when no config value is available.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autoshorts.core.paths import atomic_write, get_data_dir
from autoshorts.longform.types import LongformResult
from autoshorts.uploader.description_builder import (
    MAX_DESCRIPTION_BYTES,
    MAX_TAGS_LONGFORM,
    MAX_TITLE_LENGTH,
    build_longform_description,
    dedupe_tags,
    truncate_on_word,
)

logger = logging.getLogger(__name__)

# Fallback only: matches the UploadConfig.longform_daily_cap default. The live cap is read from config.
LONGFORM_DAILY_CAP = 20
MIN_LONGFORM_TAGS = 3
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


def longform_remaining_today(cap: int | None = None) -> int:
    eff_cap = cap if cap is not None else LONGFORM_DAILY_CAP
    return max(0, eff_cap - longform_uploads_today())


def _write_counts(counts: dict[str, int]) -> None:
    # Keep only the last 7 days so the file stays small.
    for day in sorted(counts)[:-7]:
        counts.pop(day, None)
    path = _counter_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(counts, indent=2), encoding="utf-8")


def _reserve_slot(cap: int) -> None:
    """Check the cap and count one upload for today, atomically. Raises when the cap is reached."""
    with _LOCK:
        counts = _load_counts()
        today = _today_pt()
        used = counts.get(today, 0)
        if max(0, cap - used) <= 0:
            raise RuntimeError(f"Long-form daily cap reached ({cap} uploads per Pacific day).")
        counts[today] = used + 1
        _write_counts(counts)


def _release_slot() -> None:
    """Give back a reservation whose upload did not succeed."""
    with _LOCK:
        counts = _load_counts()
        today = _today_pt()
        if counts.get(today, 0) > 0:
            counts[today] -= 1
            _write_counts(counts)


def _record_upload() -> None:
    """Count a successful upload (kept for callers outside this module)."""
    with _LOCK:
        counts = _load_counts()
        today = _today_pt()
        counts[today] = counts.get(today, 0) + 1
        _write_counts(counts)


def _topic_safety_note(topic: str) -> str:
    """Safety note of the topic spec, used as the protected sources/disclaimer footer."""
    try:
        from autoshorts.longform.topics import resolve_topic_spec

        return str(resolve_topic_spec(topic, allow_custom=True).safety_note or "")
    except Exception:  # metadata enrichment must never block an upload
        logger.debug("No topic safety note for %r", topic, exc_info=True)
        return ""


def validate_longform_upload(
    result: LongformResult, title: str, description: str, tags: list[str]
) -> tuple[str, str, list[str]]:
    """Check and fix title, description and tags before a longform upload.

    - title: never empty (falls back to the topic name); trimmed to 100 characters.
    - description: at most 5000 UTF-8 bytes; the chapters block is cut first and the
      sources/disclaimer footer is kept.
    - tags: deduplicated case-insensitively and capped at 15; topic words fill the list
      up to 3 when it is short. A still-short list is logged, not blocked.
    """
    clean_title = (title or "").strip()
    if not clean_title:
        clean_title = (str(result.topic or "").replace("_", " ").strip().title()) or (
            "Long-form Video"
        )
        logger.warning("Longform upload title is empty; using fallback title %r.", clean_title)
    if len(clean_title) > MAX_TITLE_LENGTH:
        head = truncate_on_word(clean_title, MAX_TITLE_LENGTH - 3).rstrip(" -|:,.!?")
        clean_title = f"{head or clean_title[: MAX_TITLE_LENGTH - 3]}..."[:MAX_TITLE_LENGTH]
        logger.warning(
            "Longform title (field: title) exceeded %d chars; truncated.", MAX_TITLE_LENGTH
        )

    original_desc = description or ""
    if not original_desc.strip():
        logger.warning("Longform description (field: description) is empty.")
    fixed_desc = build_longform_description(
        original_desc,
        sections=getattr(result, "chapter_sections", None),
        safety_note=_topic_safety_note(getattr(result, "topic", "")),
        max_bytes=MAX_DESCRIPTION_BYTES,
    )
    if fixed_desc != original_desc.strip() and original_desc.strip():
        logger.warning(
            "Longform description (field: description) changed to fit %d bytes (chapters cut first).",
            MAX_DESCRIPTION_BYTES,
        )

    clean_tags = dedupe_tags(tags, MAX_TAGS_LONGFORM)
    if len(clean_tags) < MIN_LONGFORM_TAGS:
        seen = {t.casefold() for t in clean_tags}
        for word in re.findall(r"[A-Za-z]{4,}", clean_title):
            if len(clean_tags) >= MIN_LONGFORM_TAGS:
                break
            if word.casefold() not in seen:
                seen.add(word.casefold())
                clean_tags.append(word.lower())
    if len(clean_tags) < MIN_LONGFORM_TAGS:
        logger.warning(
            "Longform tags (field: tags) have %d entries after dedupe; YouTube needs at least %d.",
            len(clean_tags),
            MIN_LONGFORM_TAGS,
        )
    return clean_title, fixed_desc, clean_tags


def publish_longform(result: LongformResult, config=None, state_manager=None) -> str:
    """Upload one rendered long-form video and return its YouTube/export URL.

    Respects `cfg.upload.mode` ('api', 'studio', or 'local') and raises RuntimeError
    when today's cap (`upload.longform_daily_cap`) is already used or no URL is returned.
    The network upload runs outside the lock; the lock covers only the cap check and the
    count. A slot is reserved before the upload and released if the upload fails.
    """
    from autoshorts.core.config import load_config
    from autoshorts.core.state_manager import StateManager
    from autoshorts.quota.quota_tracker import QuotaTracker
    from autoshorts.uploader.local_exporter import LocalExportUploader
    from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
    from autoshorts.uploader.youtube_api import YouTubeApiUploader

    cfg = config or load_config(None)
    upload_cfg = getattr(cfg, "upload", None)
    cap = getattr(upload_cfg, "longform_daily_cap", None)
    eff_cap = int(cap) if cap is not None else LONGFORM_DAILY_CAP
    upload_mode = str(getattr(upload_cfg, "mode", "api") or "api").strip().lower()

    title, description, tags = validate_longform_upload(
        result, result.title, result.description, list(result.hashtags)
    )

    # Scheduled publish: the uploader sets publishAt and YouTube requires private until then.
    publish_at = getattr(upload_cfg, "publish_at", None)
    scheduled = bool(publish_at) and upload_mode in ("api", "studio")
    privacy = "private" if scheduled else getattr(upload_cfg, "default_privacy_status", "unlisted")
    schedule_kwargs = {"publish_at": str(publish_at)} if scheduled else {}

    _reserve_slot(eff_cap)
    try:
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
            title=title,
            description=description,
            tags=tags,
            privacy_status=privacy,
            category_id=getattr(upload_cfg, "category_id", "27"),
            is_short=False,
            thumbnail_path=getattr(result, "thumbnail_path", None),
            **schedule_kwargs,
        )
        if not getattr(upload_result, "url", None):
            raise RuntimeError("YouTube upload did not return a video URL.")
    except BaseException:
        _release_slot()
        raise

    logger.info("Published long-form video %s (mode=%s)", upload_result.url, upload_mode)

    # Automatic post-upload cleanup: delete video from PC once uploaded
    keep_master = getattr(getattr(cfg, "cleanup", None), "keep_master_mp4", False)
    if not keep_master:
        try:
            if result.video_path and Path(result.video_path).is_file():
                Path(result.video_path).unlink(missing_ok=True)
                logger.info("Purged uploaded longform master MP4: %s", result.video_path)
        except Exception as del_err:
            logger.warning("Could not delete uploaded longform video: %s", del_err)

    return str(upload_result.url)
