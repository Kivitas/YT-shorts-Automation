"""Unit and integration tests for Built-In Viral Research Agent & Autonomous Faceless AI Shorts Studio."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoshorts.core.config import load_config
from autoshorts.core.models import Job, JobStatus
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard.app import create_app
from autoshorts.research_agent import (
    VOICE_CATALOG,
    FacelessShortRequest,
    calculate_projected_payout,
    compute_viral_score,
    create_faceless_short_job,
    discover_trending_sources,
    download_viral_bgm,
    format_duration,
    generate_faceless_script,
    generate_viral_strategy_brief,
    get_niche_payout_matrix,
    get_trending_shorts_audio,
    get_viral_bgm_catalog,
    synthesize_neural_tts,
)

# =============================================================================
# 1. Viral Scoring & Duration Tests
# =============================================================================


def test_format_duration():
    """Verify duration formatting for minutes and hours."""
    assert format_duration(45.0) == "00:45"
    assert format_duration(125.0) == "02:05"
    assert format_duration(3665.0) == "01:01:05"
    assert format_duration(0.0) == "00:00"


def test_compute_viral_score():
    """Verify Viral Potential Score (0-100) and breakdown components."""
    # High-velocity, ideal duration (45 min), high engagement, fresh upload (<24h)
    score, breakdown = compute_viral_score(
        views=500_000,
        likes=25_000,  # 5% like ratio
        duration_sec=2700.0,  # 45 mins
        hours_since_upload=12.0,  # 41.6k views/hr
    )
    assert 85.0 <= score <= 100.0
    assert breakdown.velocity_score == 40.0
    assert breakdown.duration_score == 25.0
    assert breakdown.engagement_score == 20.0
    assert breakdown.freshness_score == 15.0
    assert breakdown.total_score == score

    # Low-velocity, short duration (<3 mins), low engagement, older video
    low_score, low_breakdown = compute_viral_score(
        views=100,
        likes=1,
        duration_sec=60.0,
        hours_since_upload=1000.0,
    )
    assert 0.0 <= low_score < 25.0
    assert low_breakdown.duration_score == 5.0
    assert low_breakdown.freshness_score == 2.0


# =============================================================================
# 2. Trending Sources Discovery & Seeds Tests
# =============================================================================


def test_discover_trending_sources_fallback():
    """Verify curated seed fallback when yt-dlp is offline or fails."""
    # Force failure with timeout or invalid command
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=1.0)):
        results = discover_trending_sources(niche="ai_tech", limit=3)
        assert len(results) >= 1
        top = results[0]
        assert len(top.video_id) == 11
        assert "youtube.com/watch?v=" in top.url
        assert top.thumbnail_url.startswith("https://i.ytimg.com")
        assert top.viral_score > 0.0
        assert top.score_breakdown is not None
        assert top.suggested_focus_prompt != ""


def test_discover_trending_sources_mocked_live():
    """Verify parsing of live yt-dlp JSON lines."""
    fake_json_line = json.dumps(
        {
            "id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up - AI Deep Dive",
            "channel": "RickAstleyVEVO",
            "uploader_url": "https://www.youtube.com/@RickAstley",
            "duration": 213,
            "view_count": 1500000000,
            "like_count": 17000000,
            "upload_date": "20260101",
        }
    )
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = fake_json_line + "\n"

    with patch("subprocess.run", return_value=mock_proc):
        results = discover_trending_sources(custom_query="test query", limit=5)
        assert len(results) == 1
        item = results[0]
        assert item.video_id == "dQw4w9WgXcQ"
        assert item.title == "Never Gonna Give You Up - AI Deep Dive"
        assert item.channel == "RickAstleyVEVO"
        assert item.viral_score > 50.0


# =============================================================================
# 3. Payout Matrix & Revenue Projections Tests
# =============================================================================


def test_get_niche_payout_matrix():
    """Verify niche payout matrix contains 8 sorted profiles with RPM and CPM ranges."""
    matrix = get_niche_payout_matrix()
    assert len(matrix) == 8
    # Verify sorted by effective_blended_rpm descending
    rpms = [p.effective_blended_rpm for p in matrix]
    assert rpms == sorted(rpms, reverse=True)

    niche_ids = [p.niche_id for p in matrix]
    assert "finance" in niche_ids
    assert "ai_tech" in niche_ids
    assert "gaming" in niche_ids

    finance = next(p for p in matrix if p.niche_id == "finance")
    assert finance.shorts_rpm_range == (0.25, 0.45)
    assert finance.funnel_cpm_range == (28.00, 55.00)
    assert len(finance.hook_angles) >= 3
    assert len(finance.best_upload_hours_pt) >= 2


def test_calculate_projected_payout():
    """Verify revenue projection math for direct Shorts ads and related video funnel."""
    payout = calculate_projected_payout(
        niche_id="finance",
        projected_views=500_000,
        related_video_ctr_pct=2.5,
    )
    # Direct = (500k / 1k) * 0.35 = $175.00
    assert payout["direct_shorts_revenue_usd"] == 175.00
    # Funnel clicks = 500k * 2.5% = 12,500
    assert payout["funnel_clicks"] == 12500
    # Funnel rev = (12,500 / 1k) * 41.50 = $518.75
    assert payout["funnel_revenue_usd"] == 518.75
    # Total = 175.00 + 518.75 = $693.75
    assert payout["total_revenue_usd"] == 693.75
    assert payout["effective_blended_rpm"] == round((693.75 / 500_000) * 1000, 3)


# =============================================================================
# 4. Viral BGM & Commercial Shorts Audio Tests
# =============================================================================


def test_viral_bgm_catalog_and_download(tmp_path: Path):
    """Verify BGM catalog discovery and procedural audio synthesis."""
    catalog = get_viral_bgm_catalog(assets_dir=tmp_path)
    assert len(catalog) == 8
    track = catalog[0]
    assert track.track_id == "drift_phonk"
    assert track.bpm == 138
    assert not track.is_installed

    # Download / synthesize procedural track
    res = download_viral_bgm(track_id="drift_phonk", assets_dir=tmp_path)
    assert res["success"] is True
    assert Path(res["file_path"]).is_file()
    assert Path(res["file_path"]).stat().st_size > 0

    # Verify is_installed now reflects True
    updated = get_viral_bgm_catalog(assets_dir=tmp_path)
    installed_track = next(t for t in updated if t.track_id == "drift_phonk")
    assert installed_track.is_installed is True
    assert installed_track.local_path is not None


def test_get_trending_shorts_audio():
    """Verify trending commercial Shorts audio list and filtering."""
    all_sounds = get_trending_shorts_audio(genre="all")
    assert len(all_sounds) >= 5
    for s in all_sounds:
        assert s.sound_id.startswith("sound_")
        assert s.youtube_shorts_sound_url.startswith("https://www.youtube.com")
        assert s.usage_tip != ""

    filtered = get_trending_shorts_audio(genre="gaming")
    assert len(filtered) >= 1
    assert "gaming" in filtered[0].best_fit_niche.lower()


# =============================================================================
# 5. AI Strategy Brief Generator Tests
# =============================================================================


def test_generate_viral_strategy_brief():
    """Verify generation of strategic brief with 3 hook variants and projected earnings."""
    brief = generate_viral_strategy_brief(niche="ai_tech")
    assert brief.niche == "ai_tech"
    assert brief.brief_id.startswith("brief_")
    assert len(brief.three_hook_variants) >= 3
    assert brief.recommended_studio_profile in ("hormozi", "podcast_70_30", "cinematic")
    assert "projected_views" in brief.projected_earnings
    assert brief.reasoning != ""


# =============================================================================
# 6. Faceless AI Studio Script & Neural TTS Tests
# =============================================================================


def test_generate_faceless_script():
    """Verify high-retention script generation and anti-duplication memory."""
    res1 = generate_faceless_script(niche="ai_tech", past_topics=())
    assert res1.topic != ""
    assert res1.hook != ""
    assert len(res1.script_sentences) >= 5
    assert len(res1.hashtags) >= 3
    assert len(res1.search_keywords) >= 3

    # Pass res1.topic into past_topics to test anti-duplication
    res2 = generate_faceless_script(niche="ai_tech", past_topics=[res1.topic])
    assert res2.topic != res1.topic or len(res2.script_sentences) >= 5


def test_synthesize_neural_tts_offline(tmp_path: Path):
    """Verify fallback audio and word alignment synthesis."""
    out_audio = tmp_path / "test_voice.mp3"
    out_words = tmp_path / "aligned_words.json"

    # Test synthesize_neural_tts (falls back gracefully to wave synthesis in offline/test mode)
    audio_path, words, duration = synthesize_neural_tts(
        text="Hello world this is a test of the faceless shorts neural voiceover engine",
        voice="en-US-ChristopherNeural",
        output_audio_path=out_audio,
        output_words_path=out_words,
    )

    assert audio_path.is_file()
    assert audio_path.stat().st_size > 0
    assert out_words.is_file()
    assert len(words) >= 5
    assert words[0]["word"] == "Hello"
    assert words[0]["start"] >= 0.0
    assert words[-1]["end"] > 0.0
    assert duration > 0.0


def test_create_faceless_short_job(tmp_path: Path):
    """Verify complete faceless job creation and stage invocation."""
    mock_pipe = MagicMock()
    mock_pipe.config = load_config()

    mock_state_mgr = MagicMock()
    mock_state_mgr.get_topic_history.return_value = []

    req = FacelessShortRequest(
        niche="finance",
        topic="How To Turn $100 A Month Into $1,000,000",
        voice="en-US-ChristopherNeural",
        style_preset="hormozi",
        layout_mode="split_50_50",
        background_category="minecraft",
    )

    job_data = create_faceless_short_job(req, pipe=mock_pipe, state_mgr=mock_state_mgr)

    assert job_data["id"].startswith("faceless_")
    assert job_data["status"] == JobStatus.DOWNLOADING_SECTION.value
    assert (
        "finance" in job_data["custom_instructions"].lower()
        or "100" in job_data["custom_instructions"]
    )
    assert mock_pipe._run_subtitles_stage.called
    assert mock_pipe._run_compositing_stage.called
    assert mock_state_mgr.save_job.called


# =============================================================================
# 7. Dashboard REST API Endpoint Tests
# =============================================================================


@pytest.fixture
def test_client(tmp_path: Path):
    """Create a FastAPI test client with mocked state and pipeline."""
    cfg = load_config()
    db_path = tmp_path / "test_autoshorts.db"
    state_mgr = StateManager(db_path=db_path)

    mock_pipe = MagicMock()
    mock_pipe.config = cfg
    mock_pipe.state_manager = state_mgr
    mock_pipe.submit_links.return_value = [
        Job(
            id="job_test_123",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            status=JobStatus.QUEUED,
        )
    ]

    app = create_app(
        pipeline=mock_pipe,
        state_manager=state_mgr,
        config=cfg,
        run_scheduler_worker=False,
    )
    return TestClient(app)


def test_api_research_trending(test_client: TestClient):
    """Verify GET /api/research/trending."""
    resp = test_client.get("/api/research/trending?niche=ai_tech&limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["niche"] == "ai_tech"
    assert len(data["videos"]) >= 1


def test_api_research_niches(test_client: TestClient):
    """Verify GET /api/research/niches with and without revenue projection."""
    resp = test_client.get("/api/research/niches")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["niches"]) == 8
    assert data["projection"] is None

    # With projection
    resp_proj = test_client.get("/api/research/niches?views=1000000&ctr=3.0&niche_id=finance")
    assert resp_proj.status_code == 200
    data_proj = resp_proj.json()
    assert data_proj["projection"] is not None
    assert data_proj["projection"]["projected_views"] == 1000000
    assert data_proj["projection"]["total_revenue_usd"] > 0.0


def test_api_research_music(test_client: TestClient):
    """Verify GET /api/research/music."""
    resp = test_client.get("/api/research/music?genre=all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["bgm_catalog"]) == 8
    assert len(data["trending_shorts_audio"]) >= 5


def test_api_research_music_download(test_client: TestClient):
    """Verify POST /api/research/music/download."""
    resp = test_client.post(
        "/api/research/music/download",
        json={"track_id": "dark_synthwave"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["track_id"] == "dark_synthwave"
    assert Path(data["file_path"]).is_file()


def test_api_research_brief(test_client: TestClient):
    """Verify POST /api/research/brief."""
    resp = test_client.post(
        "/api/research/brief",
        json={"niche": "business", "custom_query": "solo founder business"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["brief"]["niche"] == "business"
    assert len(data["brief"]["three_hook_variants"]) >= 3


def test_api_research_pipeline_handoff(test_client: TestClient):
    """Verify POST /api/research/pipeline-handoff."""
    resp = test_client.post(
        "/api/research/pipeline-handoff",
        json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "niche": "ai_tech",
            "recommended_studio_profile": "hormozi",
            "recommended_background": "minecraft",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["job_id"] == "job_test_123"


def test_api_moneyprinter_voices(test_client: TestClient):
    """Verify GET /api/moneyprinter/voices."""
    resp = test_client.get("/api/moneyprinter/voices")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["voices"]) == len(VOICE_CATALOG)


def test_api_moneyprinter_generate_script(test_client: TestClient):
    """Verify POST /api/moneyprinter/generate-script."""
    resp = test_client.post(
        "/api/moneyprinter/generate-script",
        json={"niche": "science", "language": "en"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["script"]["hook"] != ""
    assert len(data["script"]["script_sentences"]) >= 5


def test_api_analytics_endpoints(test_client: TestClient):
    """Verify GET /api/analytics/overview, breakdown, videos, sync, and clone-style."""
    r_overview = test_client.get("/api/analytics/overview?days=30")
    assert r_overview.status_code == 200
    assert r_overview.json()["success"] is True

    r_breakdown = test_client.get("/api/analytics/breakdown")
    assert r_breakdown.status_code == 200
    assert r_breakdown.json()["success"] is True

    r_videos = test_client.get("/api/analytics/videos?limit=10")
    assert r_videos.status_code == 200
    assert r_videos.json()["success"] is True

    r_sync = test_client.post("/api/analytics/sync", json={"force": True})
    assert r_sync.status_code == 200
    assert r_sync.json()["success"] is True
