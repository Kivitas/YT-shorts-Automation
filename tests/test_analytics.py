"""Comprehensive tests for YouTube Shorts In-App Analytics and Closed-Loop Attribution Hub.

Covers:
1. StateManager video_analytics table CRUD, history snapshots, and 90-entry ceiling.
2. StateManager topic_history table CRUD and niche-filtered retrieval.
3. VideoAnalyticsRecord model, engagement rate property, and SVS scoring.
4. AnalyticsEngine multi-source waterfall:
   - Data API v3 batching (50 IDs per call, quota tracking)
   - Duration parsing (ISO 8601)
   - yt-dlp zero-quota fallback
   - sync_all_uploaded_shorts job synchronization and RPM/revenue estimation
   - Demo telemetry seeding and AnalyticsOverview aggregation
5. PerformanceAttributionEngine:
   - Dimension performance breakdowns (layout, caption presets, backgrounds)
   - Bayesian shrinkage WinningRecipe selection and uplift summary
   - 1-Click clone_job_style
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoshorts.analytics.attribution import PerformanceAttributionEngine
from autoshorts.analytics.engine import AnalyticsEngine, parse_iso8601_duration
from autoshorts.analytics.models import (
    PublishWindowInsight,
    VideoAnalyticsRecord,
)
from autoshorts.core.config import Settings
from autoshorts.core.models import ClipSelection, Job, JobStatus, UploadResult
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker

# -----------------------------------------------------------------------------
# 1. StateManager Video Analytics & Topic History CRUD
# -----------------------------------------------------------------------------


def test_state_manager_video_analytics_upsert_and_history(tmp_path: Path):
    """Verify video_analytics upsert, history snapshot appending, and 90-entry cap."""
    db_path = tmp_path / "analytics_test.db"
    sm = StateManager(db_path)

    # Initial upsert
    record_1 = {
        "video_id": "vid_abc_123",
        "job_id": "job_001",
        "title": "Stoic Rule #Shorts",
        "views": 1000,
        "likes": 80,
        "comments": 10,
        "shares": 5,
        "avg_view_percentage": 75.0,
        "viewed_vs_swiped_pct": 70.0,
        "estimated_revenue_usd": 0.18,
        "rpm_usd": 0.18,
        "source_tier": "data_api",
    }
    sm.upsert_video_analytics(record_1)

    fetched = sm.get_video_analytics("vid_abc_123")
    assert fetched is not None
    assert fetched["video_id"] == "vid_abc_123"
    assert fetched["views"] == 1000
    assert fetched["likes"] == 80
    assert len(fetched["history"]) == 1
    assert fetched["history"][0]["views"] == 1000

    # Second upsert (simulating periodic sync): updates stats and appends history
    record_2 = dict(record_1)
    record_2["views"] = 2500
    record_2["likes"] = 190
    sm.upsert_video_analytics(record_2)

    fetched_2 = sm.get_video_analytics("vid_abc_123")
    assert fetched_2 is not None
    assert fetched_2["views"] == 2500
    assert fetched_2["likes"] == 190
    assert len(fetched_2["history"]) == 2
    assert fetched_2["history"][0]["views"] == 1000
    assert fetched_2["history"][1]["views"] == 2500

    # Test capping at 90 history entries
    for i in range(100):
        rec_i = dict(record_2)
        rec_i["views"] = 3000 + i
        sm.upsert_video_analytics(rec_i)

    fetched_capped = sm.get_video_analytics("vid_abc_123")
    assert fetched_capped is not None
    assert len(fetched_capped["history"]) == 90
    assert fetched_capped["history"][-1]["views"] == 3099

    # Test list_video_analytics
    rec_b = {
        "video_id": "vid_def_456",
        "job_id": "job_002",
        "title": "AI Tools #Shorts",
        "views": 5000,
    }
    sm.upsert_video_analytics(rec_b)

    all_records = sm.list_video_analytics(limit=10)
    assert len(all_records) == 2
    video_ids = [r["video_id"] for r in all_records]
    assert "vid_abc_123" in video_ids
    assert "vid_def_456" in video_ids


def test_state_manager_topic_history(tmp_path: Path):
    """Verify topic_history recording and niche-filtered retrieval."""
    db_path = tmp_path / "topic_test.db"
    sm = StateManager(db_path)

    sm.record_topic_history("stoicism", "The dichotomy of control", "job_1")
    sm.record_topic_history("stoicism", "Amor fati and resilience", "job_2")
    sm.record_topic_history("finance", "Compound interest breakdown", "job_3")

    stoic_topics = sm.get_topic_history(niche="stoicism")
    assert len(stoic_topics) == 2
    assert stoic_topics[0] == "Amor fati and resilience"  # Newest first
    assert stoic_topics[1] == "The dichotomy of control"

    finance_topics = sm.get_topic_history(niche="FINANCE")  # Case-insensitive
    assert len(finance_topics) == 1
    assert finance_topics[0] == "Compound interest breakdown"

    all_topics = sm.get_topic_history(niche=None)
    assert len(all_topics) == 3


# -----------------------------------------------------------------------------
# 2. Analytics Models & SVS Scoring
# -----------------------------------------------------------------------------


def test_video_analytics_record_properties():
    """Verify engagement rate property and Shorts Viral Score (SVS) calculation."""
    # Zero views case
    rec_zero = VideoAnalyticsRecord(video_id="test_zero", views=0, likes=0)
    assert rec_zero.engagement_rate == 0.0
    assert rec_zero.svs_score == 0.0

    # High-retention viral Short
    rec_viral = VideoAnalyticsRecord(
        video_id="test_viral",
        views=150000,
        likes=12000,
        comments=900,
        shares=1500,
        avg_view_percentage=110.5,
        viewed_vs_swiped_pct=79.2,
    )
    # (12000 + 900 + 1500) / 150000 = 14400 / 150000 = 9.6%
    assert rec_viral.engagement_rate == 9.6
    assert 75.0 <= rec_viral.svs_score <= 100.0

    # Low-engagement Short with missing retention/swipe data (tier 3 fallback)
    rec_low = VideoAnalyticsRecord(
        video_id="test_low",
        views=500,
        likes=10,
        comments=1,
        shares=0,
    )
    assert rec_low.engagement_rate == 2.2
    assert rec_low.svs_score < 40.0


def test_parse_iso8601_duration():
    """Verify ISO 8601 duration parser handles YouTube format strings."""
    assert parse_iso8601_duration("PT45S") == 45.0
    assert parse_iso8601_duration("PT1M15S") == 75.0
    assert parse_iso8601_duration("PT1H2M3S") == 3723.0
    assert parse_iso8601_duration("PT59.5S") == 59.5
    assert parse_iso8601_duration("") == 0.0
    assert parse_iso8601_duration("INVALID") == 0.0


# -----------------------------------------------------------------------------
# 3. AnalyticsEngine Waterfall & Synchronization
# -----------------------------------------------------------------------------


def test_analytics_engine_data_api_batching(tmp_path: Path):
    """Verify YouTube Data API v3 batches 50 IDs per call and records general quota spend."""
    sm = StateManager(tmp_path / "batch_test.db")
    qt = QuotaTracker(state_manager=sm)

    # Generate 65 dummy video IDs
    video_ids = [f"vid_{i:03d}" for i in range(65)]

    # Mock YouTube Data API service
    mock_service = MagicMock()
    MagicMock()

    def side_effect_list(part, id):
        requested_ids = id.split(",")
        items = []
        for vid in requested_ids:
            items.append(
                {
                    "id": vid,
                    "snippet": {"title": f"Title for {vid}", "publishedAt": "2026-03-01T12:00:00Z"},
                    "statistics": {"viewCount": "1200", "likeCount": "85", "commentCount": "12"},
                    "contentDetails": {"duration": "PT42S"},
                }
            )
        exec_mock = MagicMock()
        exec_mock.execute.return_value = {"items": items}
        return exec_mock

    mock_service.videos().list.side_effect = side_effect_list

    engine = AnalyticsEngine(
        state_manager=sm,
        quota_tracker=qt,
        youtube_service=mock_service,
    )

    results = engine.fetch_via_youtube_data_api(video_ids)

    # 65 IDs must trigger exactly 2 batches: 50 + 15
    assert mock_service.videos().list.call_count == 2
    assert len(results) == 65
    assert results["vid_000"]["views"] == 1200
    assert results["vid_000"]["avg_view_duration_sec"] == 42.0
    assert results["vid_000"]["source_tier"] == "data_api"

    # Verify quota tracker recorded 2 general quota units (1 per 50-ID call)
    assert qt.get_general_spend() == 2


def test_analytics_engine_ytdlp_fallback(tmp_path: Path):
    """Verify yt-dlp zero-quota fallback extracts stats via mocked subprocess/yt_dlp."""
    sm = StateManager(tmp_path / "ytdlp_test.db")
    engine = AnalyticsEngine(state_manager=sm)

    # Mock yt_dlp in engine.py
    mock_ytdlp_data = {
        "title": "Stoic Habit #Shorts",
        "view_count": 48200,
        "like_count": 3200,
        "comment_count": 210,
        "duration": 38.0,
        "upload_date": "20260315",
    }

    with patch("subprocess.run") as mock_subproc:
        mock_subproc.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(mock_ytdlp_data),
        )
        # Force subprocess path by breaking yt_dlp import
        with patch.dict("sys.modules", {"yt_dlp": None}):
            res = engine.fetch_via_ytdlp_fallback(["fallback_vid_1"])

    assert "fallback_vid_1" in res
    assert res["fallback_vid_1"]["views"] == 48200
    assert res["fallback_vid_1"]["likes"] == 3200
    assert res["fallback_vid_1"]["avg_view_duration_sec"] == 38.0
    assert res["fallback_vid_1"]["source_tier"] == "ytdlp"


def test_sync_all_uploaded_shorts(tmp_path: Path):
    """Verify sync_all_uploaded_shorts discovers jobs, fetches stats, and estimates RPM/revenue."""
    sm = StateManager(tmp_path / "sync_test.db")

    # Create job with upload_result
    job_1 = Job(
        id="job_sync_1",
        source_url="https://youtube.com/watch?v=src1",
        status=JobStatus.COMPLETED,
        layout_mode="split_50_50",
        style_preset="hormozi",
        upload_result=UploadResult(
            video_id="yt_vid_sync_1",
            url="https://youtube.com/shorts/yt_vid_sync_1",
            status="published",
            verified=True,
        ),
    )
    sm.save_job(job_1)

    mock_service = MagicMock()
    mock_service.videos().list().execute.return_value = {
        "items": [
            {
                "id": "yt_vid_sync_1",
                "snippet": {"title": "Synced Title #Shorts", "publishedAt": "2026-03-20T00:00:00Z"},
                "statistics": {"viewCount": "10000", "likeCount": "700", "commentCount": "50"},
                "contentDetails": {"duration": "PT45S"},
            }
        ]
    }

    engine = AnalyticsEngine(state_manager=sm, youtube_service=mock_service)
    sync_result = engine.sync_all_uploaded_shorts()

    assert sync_result["synced_count"] == 1
    assert "yt_vid_sync_1" in sync_result["video_ids"]

    # Verify record in StateManager
    rec = sm.get_video_analytics("yt_vid_sync_1")
    assert rec is not None
    assert rec["views"] == 10000
    assert rec["likes"] == 700
    assert rec["estimated_revenue_usd"] == 1.80  # 10,000 views * $0.18 / 1,000
    assert rec["rpm_usd"] == 0.18


# -----------------------------------------------------------------------------
# 4. Performance Attribution & Closed-Loop Winning Recipe
# -----------------------------------------------------------------------------


def test_performance_attribution_engine(tmp_path: Path):
    """Verify attribution breakdown, Bayesian WinningRecipe selection, and style cloning."""
    sm = StateManager(tmp_path / "attribution_test.db")

    # Seed 3 jobs with contrasting styles
    # Winner: split_50_50 + hormozi + subway
    j1 = Job(
        id="j_winner_1",
        source_url="https://youtube.com/watch?v=1",
        status=JobStatus.COMPLETED,
        layout_mode="split_50_50",
        style_preset="hormozi",
        selected_clips=[
            ClipSelection(
                candidate={"start": 0, "end": 45, "text": "hello"},
                background_style="subway",
            )
        ],
        upload_result=UploadResult(video_id="v_win_1", url="url"),
    )
    j2 = Job(
        id="j_winner_2",
        source_url="https://youtube.com/watch?v=2",
        status=JobStatus.COMPLETED,
        layout_mode="split_50_50",
        style_preset="hormozi",
        selected_clips=[
            ClipSelection(
                candidate={"start": 0, "end": 45, "text": "hello2"},
                background_style="subway",
            )
        ],
        upload_result=UploadResult(video_id="v_win_2", url="url"),
    )
    # Runner up: split_70_30 + minimal + none
    j3 = Job(
        id="j_runner_3",
        source_url="https://youtube.com/watch?v=3",
        status=JobStatus.COMPLETED,
        layout_mode="split_70_30",
        style_preset="minimal",
        selected_clips=[
            ClipSelection(
                candidate={"start": 0, "end": 40, "text": "hello3"},
                background_style="none",
            )
        ],
        upload_result=UploadResult(video_id="v_run_3", url="url"),
    )
    sm.save_job(j1)
    sm.save_job(j2)
    sm.save_job(j3)

    # Upsert analytics records
    sm.upsert_video_analytics(
        {
            "video_id": "v_win_1",
            "job_id": j1.id,
            "views": 95000,
            "likes": 7800,
            "comments": 420,
            "shares": 850,
            "avg_view_percentage": 105.0,
            "viewed_vs_swiped_pct": 79.5,
        }
    )
    sm.upsert_video_analytics(
        {
            "video_id": "v_win_2",
            "job_id": j2.id,
            "views": 82000,
            "likes": 6400,
            "comments": 310,
            "shares": 720,
            "avg_view_percentage": 98.2,
            "viewed_vs_swiped_pct": 76.0,
        }
    )
    sm.upsert_video_analytics(
        {
            "video_id": "v_run_3",
            "job_id": j3.id,
            "views": 12000,
            "likes": 600,
            "comments": 35,
            "shares": 40,
            "avg_view_percentage": 58.0,
            "viewed_vs_swiped_pct": 52.0,
        }
    )

    attr_engine = PerformanceAttributionEngine(state_manager=sm)

    # 1. Breakdowns
    breakdowns = attr_engine.get_breakdowns()
    assert "layout_mode" in breakdowns
    assert "style_preset" in breakdowns
    assert "background_category" in breakdowns

    layout_items = breakdowns["layout_mode"]
    assert len(layout_items) == 2
    # split_50_50 should rank higher than split_70_30
    assert layout_items[0].key == "split_50_50"
    assert layout_items[0].svs_score > layout_items[1].svs_score
    assert layout_items[0].sample_count == 2

    # 2. Winning Recipe
    recipe = attr_engine.get_winning_recipe()
    assert recipe.layout_mode == "split_50_50"
    assert recipe.style_preset == "hormozi"
    assert recipe.background_category == "subway"
    assert recipe.sample_size == 2
    assert recipe.confidence == "preliminary"
    assert recipe.avg_retention_pct > 90.0
    assert recipe.source_job_id in (j1.id, j2.id)
    assert len(recipe.uplift_summary) > 0
    assert len(recipe.rationale) > 0

    # 3. Clone Job Style
    cloned_style = attr_engine.clone_job_style(j1.id)
    assert cloned_style["layout_mode"] == "split_50_50"
    assert cloned_style["style_preset"] == "hormozi"
    assert cloned_style["background_category"] == "subway"


def test_seed_demo_analytics_and_overview(tmp_path: Path):
    """Verify demo seeding when DB is empty and AnalyticsOverview calculation."""
    sm = StateManager(tmp_path / "demo_test.db")
    engine = AnalyticsEngine(state_manager=sm)

    # Overview when empty returns clean defaults (zero phantom data)
    overview_empty = engine.get_overview()
    assert overview_empty.total_videos == 0
    assert overview_empty.total_views == 0
    assert overview_empty.channel_rpm_usd == 0.0
    assert overview_empty.winning_recipe.layout_mode == "split_50_50"
    assert overview_empty.winning_recipe.confidence in ("none", "default")

    # Seed demo telemetry
    seeded = engine.seed_demo_analytics_if_empty()
    assert len(seeded) == 4

    # Overview after seeding
    overview_seeded = engine.get_overview()
    assert overview_seeded.total_videos == 4
    assert overview_seeded.total_views > 100000
    assert overview_seeded.total_likes > 5000
    assert overview_seeded.total_estimated_revenue_usd > 20.0
    assert overview_seeded.top_video is not None
    assert overview_seeded.top_video["views"] >= 80000
    assert overview_seeded.winning_recipe.layout_mode == "split_50_50"


# -----------------------------------------------------------------------------
# 5. Analytics 2.0: Outliers, Best Publish Windows, Auto-Tune & Export
# -----------------------------------------------------------------------------


def test_analytics_2_0_outliers_publish_windows_autotune_and_export(tmp_path: Path):
    """Verify compute_outlier_scores, get_best_publish_windows, auto_tune_config_from_winners, and export_telemetry."""
    sm = StateManager(tmp_path / "analytics_v2.db")
    attr = PerformanceAttributionEngine(state_manager=sm)
    engine = AnalyticsEngine(state_manager=sm)
    cfg = Settings()

    # Zero-phantom data check on empty DB
    assert attr.compute_outlier_scores() == []
    assert attr.get_best_publish_windows() == []
    empty_tune = attr.auto_tune_config_from_winners(cfg)
    assert empty_tune["applied"] is False
    assert "Sync live YouTube analytics first" in empty_tune["reason"]

    # Check new model fields and PublishWindowInsight
    rec_default = VideoAnalyticsRecord(video_id="v_def")
    assert rec_default.outlier_multiplier == 1.0
    assert rec_default.velocity_views_per_hour == 0.0
    pwi = PublishWindowInsight(
        hour_utc=15,
        day_of_week="Wednesday",
        sample_count=2,
        avg_views=45000.0,
        avg_retention_pct=92.5,
    )
    assert pwi.hour_utc == 15

    # Seed 3 real uploaded video records with jobs
    j1 = Job(
        id="j_v2_1",
        source_url="https://youtube.com/watch?v=v2_1",
        status=JobStatus.COMPLETED,
        layout_mode="full_blur_bg",
        style_preset="cinematic",
        color_grading="vibrant",
        selected_clips=[
            ClipSelection(
                candidate={"start": 0, "end": 40, "text": "clip 1"},
                background_style="subway",
            )
        ],
        upload_result=UploadResult(video_id="vid_v2_1", url="https://youtube.com/shorts/vid_v2_1"),
    )
    j2 = Job(
        id="j_v2_2",
        source_url="https://youtube.com/watch?v=v2_2",
        status=JobStatus.COMPLETED,
        layout_mode="full_blur_bg",
        style_preset="cinematic",
        color_grading="vibrant",
        selected_clips=[
            ClipSelection(
                candidate={"start": 0, "end": 40, "text": "clip 2"},
                background_style="subway",
            )
        ],
        upload_result=UploadResult(video_id="vid_v2_2", url="https://youtube.com/shorts/vid_v2_2"),
    )
    sm.save_job(j1)
    sm.save_job(j2)

    sm.upsert_video_analytics(
        {
            "video_id": "vid_v2_1",
            "job_id": j1.id,
            "title": "Outlier Smash #Shorts",
            "views": 60000,
            "likes": 5000,
            "comments": 300,
            "shares": 400,
            "avg_view_percentage": 96.0,
            "published_at": "2026-04-01T14:00:00Z",
            "fetched_at": "2026-04-02T14:00:00Z",
        }
    )
    sm.upsert_video_analytics(
        {
            "video_id": "vid_v2_2",
            "job_id": j2.id,
            "title": "Median Baseline #Shorts",
            "views": 20000,
            "likes": 1200,
            "comments": 80,
            "shares": 100,
            "avg_view_percentage": 84.0,
            "published_at": "2026-04-01T14:30:00Z",
            "fetched_at": "2026-04-02T14:30:00Z",
        }
    )
    sm.upsert_video_analytics(
        {
            "video_id": "vid_v2_3",
            "job_id": "",
            "title": "Low Views #Shorts",
            "views": 10000,
            "likes": 400,
            "comments": 20,
            "shares": 15,
            "avg_view_percentage": 62.0,
            "published_at": "2026-04-03T08:00:00Z",
            "fetched_at": "2026-04-04T08:00:00Z",
        }
    )

    # 1. Outlier scores: median of [60000, 20000, 10000] is 20000
    outliers = attr.compute_outlier_scores()
    assert len(outliers) == 3
    assert outliers[0]["video_id"] == "vid_v2_1"
    assert outliers[0]["outlier_multiplier"] == 3.0
    assert outliers[0]["is_outlier"] is True
    assert outliers[1]["video_id"] == "vid_v2_2"
    assert outliers[1]["outlier_multiplier"] == 1.0
    assert outliers[1]["is_outlier"] is False

    # 2. Best publish windows: two videos at 14:00 UTC Wednesday averaging 40,000 views
    windows = attr.get_best_publish_windows()
    assert len(windows) == 2
    assert windows[0]["hour_utc"] == 14
    assert windows[0]["day_of_week"] == "Wednesday"
    assert windows[0]["sample_count"] == 2
    assert windows[0]["avg_views"] == 40000.0

    # 3. Auto-tune config from winners
    tune_res = attr.auto_tune_config_from_winners(cfg)
    assert tune_res["applied"] is True
    assert cfg.branding.layout_mode == "full_blur_bg"
    assert cfg.subtitles.style_preset == "cinematic"
    assert cfg.job_defaults.background_category == "subway"
    assert cfg.branding.color_grading == "vibrant"

    # 4. Telemetry export (JSON and CSV)
    json_payload, json_mime = engine.export_telemetry("json")
    assert json_mime == "application/json"
    parsed_json = json.loads(json_payload)
    assert len(parsed_json) == 3

    csv_payload, csv_mime = engine.export_telemetry("csv")
    assert csv_mime == "text/csv"
    assert "video_id,job_id,title,views" in csv_payload
    assert "vid_v2_1" in csv_payload
    assert "Outlier Smash #Shorts" in csv_payload


def test_dashboard_analytics_v2_endpoints(tmp_path: Path):
    """Verify /api/analytics/insights, /api/analytics/auto-tune, and /api/analytics/export endpoints."""
    from fastapi.testclient import TestClient

    from autoshorts.dashboard.app import create_app
    from autoshorts.pipeline import ShortsPipeline

    sm = StateManager(tmp_path / "dash_analytics_v2.db")
    cfg = Settings()
    pipe = ShortsPipeline(config=cfg, state_manager=sm)
    app = create_app(pipeline=pipe, state_manager=sm, config=cfg, run_scheduler_worker=False)
    client = TestClient(app)

    # Empty state: auto-tune returns applied=False
    r_tune_empty = client.post("/api/analytics/auto-tune")
    assert r_tune_empty.status_code == 200
    assert r_tune_empty.json()["applied"] is False

    # Seed a winning job and analytics row
    job = Job(
        id="j_dash_1",
        source_url="https://youtube.com/watch?v=dash1",
        status=JobStatus.COMPLETED,
        layout_mode="pip_gameplay",
        style_preset="mrbeast",
        color_grading="crisp",
        selected_clips=[
            ClipSelection(
                candidate={"start": 0, "end": 35, "text": "test"},
                background_style="minecraft",
            )
        ],
        upload_result=UploadResult(
            video_id="vid_dash_1", url="https://youtube.com/shorts/vid_dash_1"
        ),
    )
    sm.save_job(job)
    sm.upsert_video_analytics(
        {
            "video_id": "vid_dash_1",
            "job_id": job.id,
            "title": "Dashboard Winner #Shorts",
            "views": 75000,
            "likes": 6200,
            "avg_view_percentage": 95.0,
            "published_at": "2026-04-05T18:00:00Z",
        }
    )

    r_insights = client.get("/api/analytics/insights")
    assert r_insights.status_code == 200
    ins_data = r_insights.json()
    assert ins_data["success"] is True
    assert len(ins_data["outliers"]) == 1
    assert len(ins_data["best_publish_windows"]) == 1
    assert ins_data["best_publish_windows"][0]["hour_utc"] == 18

    r_tune = client.post("/api/analytics/auto-tune")
    assert r_tune.status_code == 200
    tune_data = r_tune.json()
    assert tune_data["applied"] is True
    assert pipe.config.branding.layout_mode == "pip_gameplay"
    assert pipe.config.subtitles.style_preset == "mrbeast"

    r_csv = client.get("/api/analytics/export?format=csv")
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert "vid_dash_1" in r_csv.text

    r_json = client.get("/api/analytics/export?format=json")
    assert r_json.status_code == 200
    assert "application/json" in r_json.headers["content-type"]
    assert json.loads(r_json.text)[0]["video_id"] == "vid_dash_1"
