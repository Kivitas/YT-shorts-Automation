"""Multi-source YouTube Shorts Analytics Engine.

Provides unified telemetry synchronization across 4 waterfall tiers:
- Tier 1: YouTube Data API v3 (batched 50 IDs = 1 general quota unit)
- Tier 2: YouTube Analytics API v2 (retention %, engaged views, revenue)
- Tier 3: Zero-Quota yt-dlp fallback (public stats without API keys)
- Tier 4: Playwright Microsoft Edge Studio DOM scraper (swiped vs viewed % & feed impressions)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoshorts.analytics.models import (
    AnalyticsOverview,
    VideoAnalyticsRecord,
)
from autoshorts.core.models import Job, JobStatus, UploadResult
from autoshorts.core.paths import get_data_dir
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker

logger = logging.getLogger(__name__)

SCOPES: list[str] = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def parse_iso8601_duration(duration_str: str) -> float:
    """Parse an ISO 8601 duration string (e.g., 'PT1M15S', 'PT45S') to seconds."""
    if not duration_str or not duration_str.startswith("PT"):
        return 0.0
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", duration_str)
    if not match:
        return 0.0
    hours = float(match.group(1) or 0)
    minutes = float(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    return hours * 3600.0 + minutes * 60.0 + seconds


class AnalyticsEngine:
    """Engine coordinating multi-tier YouTube Shorts analytics fetching."""

    def __init__(
        self,
        state_manager: StateManager,
        quota_tracker: QuotaTracker | None = None,
        token_path: Path | str | None = None,
        client_secret_path: Path | str | None = None,
        api_key: str | None = None,
        edge_profile_dir: Path | str | None = None,
        youtube_service: Any = None,
        analytics_service: Any = None,
    ) -> None:
        """Initialize AnalyticsEngine with dependencies and optional service overrides."""
        self.state_manager = state_manager
        self.quota_tracker = quota_tracker
        data_dir = get_data_dir()
        self.token_path = Path(token_path) if token_path else (data_dir / "token.json")
        self.client_secret_path = (
            Path(client_secret_path) if client_secret_path else (data_dir / "client_secret.json")
        )
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY")
        self.edge_profile_dir = (
            Path(edge_profile_dir) if edge_profile_dir else (data_dir / "edge_profile")
        )

        self._youtube_service = youtube_service
        self._analytics_service = analytics_service

    # -------------------------------------------------------------------------
    # Service Authentication Helpers
    # -------------------------------------------------------------------------

    def _get_credentials(self) -> Any:
        """Attempt to load Google OAuth credentials from token_path."""
        if not self.token_path.is_file():
            return None
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(self.token_path, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())
                except Exception as e:
                    logger.warning(f"Could not refresh token.json: {e}")
                    return None
            return creds if (creds and creds.valid) else None
        except Exception as e:
            logger.debug(f"OAuth credentials unavailable or invalid: {e}")
            return None

    def _get_youtube_service(self) -> Any:
        """Lazy load or return YouTube Data API v3 service."""
        if self._youtube_service is not None:
            return self._youtube_service

        creds = self._get_credentials()
        try:
            from googleapiclient.discovery import build

            if creds:
                self._youtube_service = build(
                    "youtube", "v3", credentials=creds, cache_discovery=False
                )
                return self._youtube_service
            elif self.api_key:
                self._youtube_service = build(
                    "youtube", "v3", developerKey=self.api_key, cache_discovery=False
                )
                return self._youtube_service
        except Exception as e:
            logger.debug(f"Failed to build YouTube Data API v3 client: {e}")

        return None

    def _get_analytics_service(self) -> Any:
        """Lazy load or return YouTube Analytics API v2 service."""
        if self._analytics_service is not None:
            return self._analytics_service

        creds = self._get_credentials()
        if not creds:
            return None

        try:
            from googleapiclient.discovery import build

            self._analytics_service = build(
                "youtubeAnalytics", "v2", credentials=creds, cache_discovery=False
            )
            return self._analytics_service
        except Exception as e:
            logger.debug(f"Failed to build YouTube Analytics API client: {e}")
            return None

    # -------------------------------------------------------------------------
    # Tier 1: YouTube Data API v3 (50 IDs per call)
    # -------------------------------------------------------------------------

    def fetch_via_youtube_data_api(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch statistics & snippet metadata via YouTube Data API v3 in batches of 50.

        Consumes 1 general quota unit per batch of up to 50 videos.
        """
        results: dict[str, dict[str, Any]] = {}
        if not video_ids:
            return results

        service = self._get_youtube_service()
        if not service:
            return results

        # Deduplicate while preserving order
        unique_ids = list(dict.fromkeys(video_ids))

        for i in range(0, len(unique_ids), 50):
            batch = unique_ids[i : i + 50]
            try:
                req = service.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(batch),
                )
                resp = req.execute()

                if self.quota_tracker is not None:
                    try:
                        self.quota_tracker.record_general(1)
                    except Exception as q_err:
                        logger.warning(f"Failed to record general quota spend: {q_err}")

                for item in resp.get("items", []):
                    vid = item.get("id")
                    if not vid:
                        continue
                    snippet = item.get("snippet", {})
                    stats = item.get("statistics", {})
                    details = item.get("contentDetails", {})
                    dur_sec = parse_iso8601_duration(details.get("duration", ""))

                    results[vid] = {
                        "video_id": vid,
                        "title": snippet.get("title", ""),
                        "published_at": snippet.get("publishedAt"),
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                        "avg_view_duration_sec": dur_sec,
                        "source_tier": "data_api",
                    }
            except Exception as e:
                logger.warning(f"YouTube Data API batch fetch failed for {batch[:3]}...: {e}")

        return results

    # -------------------------------------------------------------------------
    # Tier 2: YouTube Analytics API v2
    # -------------------------------------------------------------------------

    def fetch_via_yt_analytics_api(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch audience retention, engaged views, and revenue via YouTube Analytics API v2."""
        results: dict[str, dict[str, Any]] = {}
        if not video_ids:
            return results

        service = self._get_analytics_service()
        if not service:
            return results

        unique_ids = list(dict.fromkeys(video_ids))
        today_str = datetime.now(UTC).strftime("%Y-%m-%d")

        for i in range(0, len(unique_ids), 25):
            batch = unique_ids[i : i + 25]
            try:
                req = service.reports().query(
                    ids="channel==MINE",
                    startDate="2022-01-01",
                    endDate=today_str,
                    metrics="views,averageViewDuration,averageViewPercentage,subscribersGained,shares,estimatedRevenue",
                    dimensions="video",
                    filters=f"video=={','.join(batch)}",
                )
                resp = req.execute()
                headers = [h.get("name") for h in resp.get("columnHeaders", [])]
                for row in resp.get("rows", []):
                    row_dict = dict(zip(headers, row))
                    vid = row_dict.get("video")
                    if vid:
                        results[vid] = {
                            "video_id": vid,
                            "views": int(row_dict.get("views", 0)),
                            "avg_view_duration_sec": float(
                                row_dict.get("averageViewDuration", 0.0)
                            ),
                            "avg_view_percentage": float(
                                row_dict.get("averageViewPercentage", 0.0)
                            ),
                            "subscribers_gained": int(row_dict.get("subscribersGained", 0)),
                            "shares": int(row_dict.get("shares", 0)),
                            "estimated_revenue_usd": float(row_dict.get("estimatedRevenue", 0.0)),
                            "source_tier": "analytics_api",
                        }
            except Exception as e:
                logger.debug(f"YouTube Analytics API query skipped/failed for batch: {e}")

        return results

    # -------------------------------------------------------------------------
    # Tier 3: Zero-Quota yt-dlp Fallback
    # -------------------------------------------------------------------------

    def fetch_via_ytdlp_fallback(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Zero-quota extraction of public metrics using yt-dlp metadata dump."""
        results: dict[str, dict[str, Any]] = {}
        if not video_ids:
            return results

        for vid in dict.fromkeys(video_ids):
            url = f"https://www.youtube.com/watch?v={vid}"
            data: dict[str, Any] | None = None

            # First attempt Python in-process yt_dlp
            try:
                import yt_dlp

                ydl_opts = {
                    "skip_download": True,
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": False,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    data = ydl.extract_info(url, download=False)
            except Exception:
                # Subprocess fallback
                try:
                    proc = subprocess.run(
                        ["yt-dlp", "--dump-json", "--skip-download", url],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        data = json.loads(proc.stdout)
                except Exception as sub_err:
                    logger.debug(f"yt-dlp subprocess fallback failed for {vid}: {sub_err}")

            if data and isinstance(data, dict):
                results[vid] = {
                    "video_id": vid,
                    "title": data.get("title", ""),
                    "views": int(data.get("view_count") or 0),
                    "likes": int(data.get("like_count") or 0),
                    "comments": int(data.get("comment_count") or 0),
                    "avg_view_duration_sec": float(data.get("duration") or 0.0),
                    "published_at": data.get("upload_date"),
                    "source_tier": "ytdlp",
                }

        return results

    # -------------------------------------------------------------------------
    # Tier 4: Playwright Microsoft Edge Studio DOM Scraper
    # -------------------------------------------------------------------------

    def fetch_via_studio_playwright(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Optional browser-driven scraping of 'Viewed vs. Swiped Away' and 'Shown in Feed' from Studio."""
        results: dict[str, dict[str, Any]] = {}
        if not video_ids:
            return results

        if not self.edge_profile_dir.is_dir():
            logger.debug("Edge profile directory does not exist; skipping Studio DOM scraping.")
            return results

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.edge_profile_dir),
                    channel="msedge",
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                try:
                    page = context.new_page()
                    for vid in dict.fromkeys(video_ids):
                        url = f"https://studio.youtube.com/video/{vid}/analytics"
                        try:
                            page.goto(url, timeout=15000, wait_until="domcontentloaded")
                            if "accounts.google.com" in page.url:
                                logger.info(
                                    "Edge profile not signed in to YouTube Studio; skipping scraping."
                                )
                                break

                            page.wait_for_timeout(2500)
                            content = page.content()

                            viewed_match = re.search(
                                r"(\d+(?:\.\d+)?)\s*%\s*(?:viewed|stayed)", content, re.IGNORECASE
                            )
                            viewed_pct = float(viewed_match.group(1)) if viewed_match else 0.0

                            feed_match = re.search(
                                r"(\d[\d,]*)\s*(?:shown in feed|impressions)",
                                content,
                                re.IGNORECASE,
                            )
                            shown_in_feed = (
                                int(feed_match.group(1).replace(",", "")) if feed_match else 0
                            )

                            if viewed_pct > 0.0 or shown_in_feed > 0:
                                results[vid] = {
                                    "video_id": vid,
                                    "viewed_vs_swiped_pct": viewed_pct,
                                    "shown_in_feed": shown_in_feed,
                                    "source_tier": "studio",
                                }
                        except Exception as e:
                            logger.debug(f"Studio scrape failed for video {vid}: {e}")
                finally:
                    context.close()
        except Exception as e:
            logger.debug(f"Playwright Studio scraper unavailable or failed: {e}")

        return results

    # -------------------------------------------------------------------------
    # Synchronization & Aggregation
    # -------------------------------------------------------------------------

    def sync_all_uploaded_shorts(self, include_studio: bool = False) -> dict[str, Any]:
        """Discover all uploaded Shorts across jobs and local DB, fetch telemetry, and persist records."""
        # 1. Collect target videos: mapping video_id -> {job_id, title}
        target_videos: dict[str, dict[str, str]] = {}

        # Scan jobs in StateManager
        jobs = self.state_manager.list_jobs(limit=500)
        for job in jobs:
            vid = None
            if job.upload_result and job.upload_result.video_id:
                vid = job.upload_result.video_id
            elif (
                job.status in (JobStatus.COMPLETED, JobStatus.CLEANED)
                and hasattr(job, "video_metadata")
                and job.video_metadata
            ):
                vid = job.video_metadata.video_id

            if vid:
                title = ""
                if job.selected_clips and job.selected_clips[0].title:
                    title = job.selected_clips[0].title
                elif job.video_metadata and job.video_metadata.title:
                    title = job.video_metadata.title
                target_videos[vid] = {"job_id": job.id, "title": title}

        # Include previously recorded videos from video_analytics table
        existing_analytics = self.state_manager.list_video_analytics(limit=500)
        for rec in existing_analytics:
            vid = rec.get("video_id")
            if vid and vid not in target_videos:
                target_videos[vid] = {
                    "job_id": rec.get("job_id", ""),
                    "title": rec.get("title", ""),
                }

        video_ids = list(target_videos.keys())
        if not video_ids:
            return {
                "synced_count": 0,
                "video_ids": [],
                "records": [],
                "timestamp": datetime.now(UTC).isoformat(),
            }

        # 2. Tier 1: YouTube Data API v3
        tier1_stats = self.fetch_via_youtube_data_api(video_ids)

        # 3. Tier 3: yt-dlp fallback for missing IDs
        missing_ids = [vid for vid in video_ids if vid not in tier1_stats]
        tier3_stats = self.fetch_via_ytdlp_fallback(missing_ids) if missing_ids else {}

        # 4. Tier 2: YouTube Analytics API v2
        tier2_stats = self.fetch_via_yt_analytics_api(video_ids)

        # 5. Tier 4: Optional Studio DOM Scraper
        tier4_stats = self.fetch_via_studio_playwright(video_ids) if include_studio else {}

        # 6. Merge, calculate revenue/RPM estimates, and upsert
        synced_records: list[VideoAnalyticsRecord] = []
        for vid, meta in target_videos.items():
            base = tier1_stats.get(vid) or tier3_stats.get(vid) or {}
            an_stats = tier2_stats.get(vid) or {}
            st_stats = tier4_stats.get(vid) or {}

            # Fall back to existing DB record if fetch failed
            existing = self.state_manager.get_video_analytics(vid) or {}

            title = base.get("title") or meta.get("title") or existing.get("title") or ""
            views = base.get("views") or an_stats.get("views") or existing.get("views") or 0
            likes = base.get("likes") or existing.get("likes") or 0
            comments = base.get("comments") or existing.get("comments") or 0
            shares = an_stats.get("shares") or existing.get("shares") or 0

            avg_dur = (
                an_stats.get("avg_view_duration_sec")
                or base.get("avg_view_duration_sec")
                or existing.get("avg_view_duration_sec")
                or 0.0
            )
            avg_ret = (
                an_stats.get("avg_view_percentage") or existing.get("avg_view_percentage") or 0.0
            )
            viewed_pct = (
                st_stats.get("viewed_vs_swiped_pct") or existing.get("viewed_vs_swiped_pct") or 0.0
            )
            shown_feed = st_stats.get("shown_in_feed") or existing.get("shown_in_feed") or 0
            subs = an_stats.get("subscribers_gained") or existing.get("subscribers_gained") or 0

            # Revenue estimation ($0.18 baseline RPM for Shorts)
            revenue = (
                an_stats.get("estimated_revenue_usd")
                or existing.get("estimated_revenue_usd")
                or 0.0
            )
            rpm = existing.get("rpm_usd") or 0.18
            if revenue <= 0.0 and views > 0:
                revenue = round((views / 1000.0) * 0.18, 4)
                rpm = 0.18
            elif views > 0 and revenue > 0.0:
                rpm = round((revenue / views) * 1000.0, 2)

            source_tier = (
                an_stats.get("source_tier")
                or base.get("source_tier")
                or st_stats.get("source_tier")
                or existing.get("source_tier")
                or "ytdlp"
            )

            record = VideoAnalyticsRecord(
                video_id=vid,
                job_id=meta.get("job_id") or existing.get("job_id") or "",
                title=title,
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                avg_view_duration_sec=avg_dur,
                avg_view_percentage=avg_ret,
                viewed_vs_swiped_pct=viewed_pct,
                shown_in_feed=shown_feed,
                subscribers_gained=subs,
                estimated_revenue_usd=revenue,
                rpm_usd=rpm,
                source_tier=source_tier,
                published_at=base.get("published_at") or existing.get("published_at"),
                fetched_at=datetime.now(UTC).isoformat(),
            )

            self.state_manager.upsert_video_analytics(record)
            synced_records.append(record)

        return {
            "synced_count": len(synced_records),
            "video_ids": [r.video_id for r in synced_records],
            "records": [r.model_dump() for r in synced_records],
            "timestamp": datetime.now(UTC).isoformat(),
        }

    # -------------------------------------------------------------------------
    # Sample Telemetry & Demo Seeding
    # -------------------------------------------------------------------------

    def generate_sample_telemetry(self) -> list[dict[str, Any]]:
        """Generate rich, realistic sample Shorts telemetry and matching jobs for testing and preview."""
        sample_jobs_data = [
            {
                "video_id": "demo_vid_001",
                "title": "Stoic Morning Rule for Hyper-Focus #Shorts",
                "views": 84200,
                "likes": 5200,
                "comments": 340,
                "shares": 610,
                "avg_view_duration_sec": 41.2,
                "avg_view_percentage": 98.4,
                "viewed_vs_swiped_pct": 78.5,
                "shown_in_feed": 115000,
                "subscribers_gained": 420,
                "estimated_revenue_usd": 15.15,
                "rpm_usd": 0.18,
                "source_tier": "data_api",
                "layout_mode": "split_50_50",
                "style_preset": "hormozi",
                "background_category": "subway",
            },
            {
                "video_id": "demo_vid_002",
                "title": "3 AI Tools That Feel Illegal to Know #Shorts",
                "views": 52800,
                "likes": 3100,
                "comments": 195,
                "shares": 380,
                "avg_view_duration_sec": 36.8,
                "avg_view_percentage": 88.2,
                "viewed_vs_swiped_pct": 71.0,
                "shown_in_feed": 79000,
                "subscribers_gained": 210,
                "estimated_revenue_usd": 9.50,
                "rpm_usd": 0.18,
                "source_tier": "data_api",
                "layout_mode": "split_50_50",
                "style_preset": "hormozi",
                "background_category": "minecraft",
            },
            {
                "video_id": "demo_vid_003",
                "title": "Why 99% Fail in Their First Business #Shorts",
                "views": 18400,
                "likes": 890,
                "comments": 72,
                "shares": 110,
                "avg_view_duration_sec": 28.5,
                "avg_view_percentage": 67.5,
                "viewed_vs_swiped_pct": 59.2,
                "shown_in_feed": 34000,
                "subscribers_gained": 65,
                "estimated_revenue_usd": 3.31,
                "rpm_usd": 0.18,
                "source_tier": "ytdlp",
                "layout_mode": "split_70_30",
                "style_preset": "minimal",
                "background_category": "none",
            },
            {
                "video_id": "demo_vid_004",
                "title": "The Psychological Secret of Steve Jobs #Shorts",
                "views": 31200,
                "likes": 1750,
                "comments": 120,
                "shares": 240,
                "avg_view_duration_sec": 34.0,
                "avg_view_percentage": 81.0,
                "viewed_vs_swiped_pct": 68.4,
                "shown_in_feed": 48000,
                "subscribers_gained": 140,
                "estimated_revenue_usd": 5.61,
                "rpm_usd": 0.18,
                "source_tier": "data_api",
                "layout_mode": "full_blur_bg",
                "style_preset": "cinematic",
                "background_category": "none",
            },
        ]

        seeded: list[dict[str, Any]] = []
        for item in sample_jobs_data:
            job_id = f"demo_job_{item['video_id']}"
            job = Job(
                id=job_id,
                source_url=f"https://www.youtube.com/watch?v={item['video_id']}",
                status=JobStatus.COMPLETED,
                layout_mode=item["layout_mode"],
                style_preset=item["style_preset"],
                upload_result=UploadResult(
                    video_id=item["video_id"],
                    url=f"https://youtube.com/shorts/{item['video_id']}",
                    status="published",
                    verified=True,
                ),
            )
            self.state_manager.save_job(job)

            rec = VideoAnalyticsRecord(
                video_id=item["video_id"],
                job_id=job_id,
                title=item["title"],
                views=item["views"],
                likes=item["likes"],
                comments=item["comments"],
                shares=item["shares"],
                avg_view_duration_sec=item["avg_view_duration_sec"],
                avg_view_percentage=item["avg_view_percentage"],
                viewed_vs_swiped_pct=item["viewed_vs_swiped_pct"],
                shown_in_feed=item["shown_in_feed"],
                subscribers_gained=item["subscribers_gained"],
                estimated_revenue_usd=item["estimated_revenue_usd"],
                rpm_usd=item["rpm_usd"],
                source_tier=item["source_tier"],
                published_at=datetime.now(UTC).isoformat(),
                fetched_at=datetime.now(UTC).isoformat(),
            )
            self.state_manager.upsert_video_analytics(rec)
            seeded.append(rec.model_dump())

        return seeded

    def seed_demo_analytics_if_empty(self) -> list[dict[str, Any]]:
        """Seed demo telemetry if the database currently has zero video analytics records."""
        existing = self.state_manager.list_video_analytics(limit=10)
        if existing:
            return existing
        return self.generate_sample_telemetry()

    def get_overview(self) -> AnalyticsOverview:
        """Compute top-level KPI metrics across all synchronized videos."""
        from autoshorts.analytics.attribution import PerformanceAttributionEngine

        records = self.state_manager.list_video_analytics(limit=500)
        attribution_engine = PerformanceAttributionEngine(self.state_manager)
        winning_recipe = attribution_engine.get_winning_recipe()

        if not records:
            return AnalyticsOverview(
                total_videos=0,
                total_views=0,
                total_likes=0,
                total_comments=0,
                total_shares=0,
                avg_retention_pct=0.0,
                avg_viewed_vs_swiped_pct=0.0,
                total_estimated_revenue_usd=0.0,
                channel_rpm_usd=0.18,
                top_video=None,
                winning_recipe=winning_recipe,
                last_synced_at=None,
            )

        total_videos = len(records)
        total_views = sum(r.get("views", 0) for r in records)
        total_likes = sum(r.get("likes", 0) for r in records)
        total_comments = sum(r.get("comments", 0) for r in records)
        total_shares = sum(r.get("shares", 0) for r in records)
        total_revenue = round(sum(r.get("estimated_revenue_usd", 0.0) for r in records), 2)

        # Average retention across videos that have retention recorded
        ret_records = [
            r.get("avg_view_percentage", 0.0)
            for r in records
            if r.get("avg_view_percentage", 0.0) > 0
        ]
        avg_retention = round(sum(ret_records) / len(ret_records), 1) if ret_records else 0.0

        # Average viewed vs swiped
        swipe_records = [
            r.get("viewed_vs_swiped_pct", 0.0)
            for r in records
            if r.get("viewed_vs_swiped_pct", 0.0) > 0
        ]
        avg_swipe = round(sum(swipe_records) / len(swipe_records), 1) if swipe_records else 0.0

        channel_rpm = round((total_revenue / total_views) * 1000.0, 2) if total_views > 0 else 0.18

        top_video = max(records, key=lambda x: x.get("views", 0))

        return AnalyticsOverview(
            total_videos=total_videos,
            total_views=total_views,
            total_likes=total_likes,
            total_comments=total_comments,
            total_shares=total_shares,
            avg_retention_pct=avg_retention,
            avg_viewed_vs_swiped_pct=avg_swipe,
            total_estimated_revenue_usd=total_revenue,
            channel_rpm_usd=channel_rpm,
            top_video=top_video,
            winning_recipe=winning_recipe,
            last_synced_at=records[0].get("fetched_at") if records else None,
        )


__all__ = ["AnalyticsEngine", "parse_iso8601_duration"]
