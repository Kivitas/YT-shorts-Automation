"""Comprehensive tests for Multi-Agent SuperBrain Swarm & Dashboard API.

Covers:
  1. Schemas & SEO character constraints (3 A/B titles <100 chars, 3-layer pyramid, <=500-char clean search tags).
  2. Specialist agents (InfoResearcher, MusicScout WPS-to-BPM, ViralSeo, RetentionEditor, HookCritic).
  3. HookCritic 3-tier calibration (>=80 approved, 70-79 fast patch, <70 rejected attempt 1, argmax fallback attempt >=2).
  4. Mode A link repurposing (boundary shifts, candidate selection, never re-drafts spoken human words).
  5. Zero-Phantom-Data credential preflight check (HTTP 422 error on unconfigured credentials).
  6. Full Mode A and Mode B 3-stage DAG orchestration.
  7. Dashboard REST API endpoints:
     - POST /api/brain/orchestrate
     - GET /api/brain/status/{plan_id}
     - POST /api/brain/critic/evaluate
  8. Lazy pipeline runner injection and bounded Semaphore(2) swarm execution.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoshorts.brain.orchestrator import MasterBrainOrchestrator, PreflightCredentialError
from autoshorts.brain.schemas import (
    AgentRole,
    MasterBrainState,
    ResearchIntel,
    SEOMetadata,
    ViralMasterPlan,
)
from autoshorts.brain.specialists import (
    HookCriticAgent,
    InfoResearcherAgent,
    MusicScoutAgent,
    RetentionEditorAgent,
    ViralSeoAgent,
)
from autoshorts.core.config import Settings, load_config
from autoshorts.dashboard.app import create_app

# =============================================================================
# 1. Schemas & SEO Contract Tests
# =============================================================================


def test_agent_role_enum() -> None:
    """Verify all specialist agent roles are registered in the enum."""
    assert AgentRole.ORCHESTRATOR.value == "orchestrator"
    assert AgentRole.INFO_RESEARCHER.value == "info_researcher"
    assert AgentRole.HOOK_CRITIC.value == "hook_critic"
    assert AgentRole.MUSIC_SCOUT.value == "music_scout"
    assert AgentRole.VIRAL_SEO.value == "viral_seo"
    assert AgentRole.RETENTION_EDITOR.value == "retention_editor"


def test_seo_metadata_constraints_and_pyramid() -> None:
    """Verify title <100 chars, 3-layer hashtag pyramid (3-5 with #), and clean search tags (<=500 chars)."""
    long_title = "A" * 150
    seo = SEOMetadata(
        primary_title="Valid Title Under 100",
        titles=[long_title, "Second Title Under 100", "Third Title Under 100"],
        description_hashtags=["Shorts", "#Viral", "Tech", "#Innovation", "AI", "ExcessTag"],
        search_tags=["#clean1", "clean2", "keyword" * 20, "another" * 15],
    )

    # Titles must be <= 100 chars
    for t in seo.titles:
        assert len(t) <= 100

    # Description hashtags must start with '#' and max 5
    assert len(seo.description_hashtags) <= 5
    for tag in seo.description_hashtags:
        assert tag.startswith("#")

    # Search tags must NOT have '#' and total joined string length <= 500 chars
    for tag in seo.search_tags:
        assert not tag.startswith("#")
    assert len(",".join(seo.search_tags)) <= 500


def test_viral_master_plan_lifecycle() -> None:
    """Verify ViralMasterPlan transitions and state models."""
    plan = ViralMasterPlan(
        mode="mode_b",
        niche="finance",
        topic="Index Funds",
    )
    assert plan.status == "pending"
    assert plan.plan_id is not None

    state = MasterBrainState(
        plan_id=plan.plan_id,
        mode="mode_b",
        phase="phase1_context",
        master_plan=plan,
    )
    assert state.phase == "phase1_context"


# =============================================================================
# 2. Specialist Agents Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_info_researcher_agent_fallback_resilience() -> None:
    """InfoResearcherAgent safely gathers research intel without failing offline."""
    agent = InfoResearcherAgent()
    intel = await agent.research(niche="ai_tech", topic="Neural Networks", allow_network=False)
    assert isinstance(intel, ResearchIntel)
    assert intel.niche == "ai_tech"
    assert len(intel.key_insights) > 0
    assert len(intel.viral_angles) > 0
    assert intel.trending_score >= 0.0


@pytest.mark.asyncio
async def test_music_scout_wps_and_rhythm_matching() -> None:
    """MusicScoutAgent calculates WPS from transcript, maps to BPM, and calculates eighth-note release."""
    agent = MusicScoutAgent()

    # Fast speech (150 words in 30s -> 5.0 WPS -> target BPM 140)
    fast_text = "word " * 150
    audio_fast = await agent.scout(script_or_transcript=fast_text, duration_seconds=30.0, niche="gaming")
    assert audio_fast.words_per_second >= 3.0
    assert audio_fast.target_bpm == 140
    assert audio_fast.mood == "high_octane_phonk"
    assert audio_fast.ducking_release_ms == round(30000.0 / 140)

    # Slow speech (40 words in 30s -> 1.33 WPS -> target BPM 100)
    slow_text = "word " * 40
    audio_slow = await agent.scout(script_or_transcript=slow_text, duration_seconds=30.0, niche="science")
    assert audio_slow.words_per_second < 2.2
    assert audio_slow.target_bpm == 100
    assert audio_slow.mood == "contemplative_ambient"
    assert audio_slow.ducking_release_ms == round(30000.0 / 100)


@pytest.mark.asyncio
async def test_viral_seo_agent_generation() -> None:
    """ViralSeoAgent produces 3 distinct A/B titles (<100 chars), 3-5 #-tags, and <=500-char tags."""
    agent = ViralSeoAgent()
    seo = await agent.generate_seo(
        hook="Why 99% of programmers make this fatal memory mistake",
        topic="Python Memory Management",
        niche="ai_tech",
        key_insights=["Garbage collection overhead", "Reference counting leaks"],
    )

    assert len(seo.titles) == 3
    for t in seo.titles:
        assert len(t) < 100
    assert seo.primary_title == seo.titles[0]

    # Hashtag pyramid (3 to 5 tags)
    assert 3 <= len(seo.description_hashtags) <= 5
    assert all(tag.startswith("#") for tag in seo.description_hashtags)

    # Clean search tags <= 500 chars
    assert all(not tag.startswith("#") for tag in seo.search_tags)
    assert len(",".join(seo.search_tags)) <= 500


@pytest.mark.asyncio
async def test_retention_editor_cadence_and_framing() -> None:
    """RetentionEditorAgent sets adaptive silence trimming and optical zoom center."""
    agent = RetentionEditorAgent()

    # Fast cadence -> tight silence threshold -28 dB
    plan_fast = await agent.plan(words_per_second=3.4, speaker_layout_hint="guest on left")
    assert plan_fast.adaptive_silence_thresh_db == -28.0
    assert plan_fast.speaker_zoom_center == "left"
    assert plan_fast.max_interrupts == 4
    assert plan_fast.min_cooldown_seconds == 4.5

    # Slow cadence -> gentle silence threshold -35 dB
    plan_slow = await agent.plan(words_per_second=1.8, speaker_layout_hint="host on right")
    assert plan_slow.adaptive_silence_thresh_db == -35.0
    assert plan_slow.speaker_zoom_center == "right"

    # Default cadence -> standard -32 dB, center
    plan_mid = await agent.plan(words_per_second=2.6)
    assert plan_mid.adaptive_silence_thresh_db == -32.0
    assert plan_mid.speaker_zoom_center == "center"


# =============================================================================
# 3. Hook Critic 3-Tier Calibration & Mode A Guardrails
# =============================================================================


@pytest.mark.asyncio
async def test_hook_critic_mode_b_three_tier_calibration() -> None:
    """Test 3-tier calibration: >=80 approved, 70-79 fast patch, <70 rejected then argmax fallback."""
    critic = HookCriticAgent()

    # Tier 1: Strong hook >= 80 -> Approved
    strong_hook = "The secret truth about money that banks never want you to know."
    review_approved = await critic.evaluate_mode_b(strong_hook, attempt=1)
    assert review_approved.score >= 80.0
    assert review_approved.verdict == "approved"
    assert review_approved.is_fallback is False

    # Tier 2: Moderate hook 70-79 -> Fast Hook Patch
    moderate_hook = "Index funds are generally considered safe for beginners."
    review_patched = await critic.evaluate_mode_b(moderate_hook, attempt=1)
    assert 70.0 <= review_patched.score < 80.0
    assert review_patched.verdict == "fast_patch"
    assert review_patched.patched_hook is not None
    assert review_patched.patched_hook.startswith("Stop scrolling:")

    # Tier 3: Passive weak hook < 70 (Attempt 1) -> Rejected
    weak_hook = "Hello guys welcome back to my channel today we are talking about python."
    review_rejected = await critic.evaluate_mode_b(weak_hook, attempt=1)
    assert review_rejected.score < 70.0
    assert review_rejected.verdict == "rejected"

    # Tier 3: Attempt 2 exhausted -> Argmax fallback
    review_fallback = await critic.evaluate_mode_b(
        weak_hook, attempt=2, prior_reviews=[review_rejected]
    )
    assert review_fallback.verdict == "approved"
    assert review_fallback.is_fallback is True
    assert "argmax fallback" in review_fallback.critique


@pytest.mark.asyncio
async def test_hook_critic_mode_a_never_rewrites_human_words() -> None:
    """Mode A evaluates window completeness and adjusts boundaries without re-drafting words."""
    critic = HookCriticAgent()

    # Incomplete narrative slice (trailing conjunction and no punctuation)
    incomplete_slice = "So the interviewer asked me why I left my previous company and then"
    review = await critic.evaluate_mode_a(
        transcript_slice=incomplete_slice,
        start_sec=10.0,
        end_sec=50.0,
        candidate_index=0,
        candidate_count=2,
    )

    # Must suggest shift or switch candidate, but hook_text retains original words
    assert review.mode == "mode_a"
    assert review.suggested_shift_seconds != 0.0 or review.candidate_index != 0
    # Spoken words in hook_text are extracted from original slice, not rewritten
    assert review.hook_text.startswith("So the interviewer")


# =============================================================================
# 4. Zero-Phantom-Data Invariant & Preflight Tests
# =============================================================================


def test_zero_phantom_data_preflight_check_missing_credentials() -> None:
    """Orchestrator raises PreflightCredentialError when credentials are missing (Mode B)."""
    cfg = Settings()
    cfg.llm.provider = "gemini"
    cfg.gemini_api_key = ""

    with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=True):
        orchestrator = MasterBrainOrchestrator(config=cfg)
        with pytest.raises(PreflightCredentialError) as exc_info:
            orchestrator.preflight_check(mode="mode_b")

        err = exc_info.value
        assert err.service == "llm"
        assert err.missing_key_name == "GEMINI_API_KEY"


# =============================================================================
# 5. Full Multi-Agent DAG Orchestration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_mode_b_full_generative_dag() -> None:
    """Execute 3-stage DAG for Mode B generative synthesis."""
    cfg = load_config()
    orchestrator = MasterBrainOrchestrator(config=cfg)

    plan = await orchestrator.orchestrate(
        mode="mode_b",
        topic="Quantum Computing",
        niche="science",
        skip_preflight=True,  # Invariant tested separately in test_zero_phantom_data_preflight_check
    )

    assert plan.status == "completed"
    assert plan.mode == "mode_b"
    assert plan.research is not None
    assert plan.hook_review is not None
    assert plan.hook_review.verdict in ("approved", "fast_patch")
    assert plan.script_text != ""
    assert plan.audio is not None
    assert plan.audio.words_per_second > 0
    assert plan.seo is not None
    assert len(plan.seo.titles) == 3
    assert plan.retention is not None
    assert plan.retention.adaptive_silence_thresh_db in (-28.0, -32.0, -35.0)

    # Verify retrieval from cache
    cached = orchestrator.get_plan(plan.plan_id)
    assert cached is not None
    assert cached.plan_id == plan.plan_id


@pytest.mark.asyncio
async def test_mode_a_full_repurposing_dag() -> None:
    """Execute 3-stage DAG for Mode A link repurposing (strictly preserves spoken words)."""
    cfg = load_config()
    orchestrator = MasterBrainOrchestrator(config=cfg)

    spoken_slice = (
        "The first time I opened a brokerage account, I had no idea what an ETF was. "
        "I thought you had to pick individual stocks to get rich. "
        "Here is what happened next."
    )

    plan = await orchestrator.orchestrate(
        mode="mode_a",
        video_url="https://youtube.com/watch?v=mock_video_id",
        transcript_slice=spoken_slice,
        candidate_windows=[(10.0, 48.0)],
        niche="finance",
        skip_preflight=True,
    )

    assert plan.status == "completed"
    assert plan.mode == "mode_a"
    assert plan.source_url == "https://youtube.com/watch?v=mock_video_id"
    # Spoken human words are preserved exactly
    assert plan.script_text == spoken_slice
    assert plan.selected_window is not None
    assert plan.audio is not None
    assert plan.seo is not None
    assert plan.retention is not None


# =============================================================================
# 6. Lazy Pipeline Runner Injection & Semaphore Bounding
# =============================================================================


def test_lazy_pipeline_injection() -> None:
    """Verify lazy pipeline runner injection prevents circular imports."""
    orchestrator = MasterBrainOrchestrator()
    mock_runner = MagicMock()
    orchestrator.set_pipeline_runner(mock_runner)
    assert orchestrator.get_pipeline_runner() is mock_runner


@pytest.mark.asyncio
async def test_bounded_swarm_concurrency() -> None:
    """Verify parallel swarm tasks respect the concurrency semaphore."""
    orchestrator = MasterBrainOrchestrator(max_concurrent_swarm=2)
    concurrent_active = 0
    max_observed = 0

    async def mock_swarmed_task():
        nonlocal concurrent_active, max_observed
        async with orchestrator._swarm_semaphore:
            concurrent_active += 1
            max_observed = max(max_observed, concurrent_active)
            await asyncio.sleep(0.01)
            concurrent_active -= 1

    tasks = [mock_swarmed_task() for _ in range(6)]
    await asyncio.gather(*tasks)
    assert max_observed <= 2


# =============================================================================
# 7. Dashboard REST API Route Tests
# =============================================================================


@pytest.fixture
def test_client() -> TestClient:
    app = create_app(run_scheduler_worker=False)
    return TestClient(app)


def test_dashboard_critic_evaluate_route(test_client: TestClient) -> None:
    """POST /api/brain/critic/evaluate executes HookCriticAgent."""
    # Test Mode B
    resp_b = test_client.post(
        "/api/brain/critic/evaluate",
        json={
            "mode": "mode_b",
            "hook_text": "The secret truth about real estate nobody will ever tell you.",
            "attempt": 1,
        },
    )
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["success"] is True
    assert data_b["review"]["score"] >= 80.0
    assert data_b["review"]["verdict"] == "approved"

    # Test Mode A
    resp_a = test_client.post(
        "/api/brain/critic/evaluate",
        json={
            "mode": "mode_a",
            "transcript_slice": "So we were talking about neural networks and then",
            "start_sec": 5.0,
            "end_sec": 40.0,
            "candidate_index": 0,
            "candidate_count": 2,
        },
    )
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["success"] is True
    assert data_a["review"]["mode"] == "mode_a"


def test_dashboard_orchestrate_and_status_routes(test_client: TestClient) -> None:
    """POST /api/brain/orchestrate and GET /api/brain/status/{plan_id}."""
    # 1. Orchestrate Mode B with skip_preflight
    resp = test_client.post(
        "/api/brain/orchestrate",
        json={
            "mode": "mode_b",
            "topic": "Autonomous Agents",
            "niche": "ai_tech",
            "skip_preflight": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    plan_id = data["plan_id"]
    assert plan_id is not None
    assert data["status"] == "completed"

    # 2. Query status for the generated plan
    resp_status = test_client.get(f"/api/brain/status/{plan_id}")
    assert resp_status.status_code == 200
    status_data = resp_status.json()
    assert status_data["success"] is True
    assert status_data["plan_id"] == plan_id
    assert status_data["plan"]["seo"]["primary_title"] is not None

    # 3. Query non-existent plan -> 404
    resp_404 = test_client.get("/api/brain/status/non-existent-uuid")
    assert resp_404.status_code == 404


def test_dashboard_orchestrate_preflight_422_on_missing_credentials(
    test_client: TestClient,
) -> None:
    """POST /api/brain/orchestrate returns HTTP 422 when credentials are missing (Zero-Phantom-Data)."""
    with patch(
        "autoshorts.brain.orchestrator.check_llm_provider_configured",
        return_value=(False, "GEMINI_API_KEY", "Gemini API Key is not configured."),
    ):
        resp = test_client.post(
            "/api/brain/orchestrate",
            json={
                "mode": "mode_b",
                "topic": "Stock Options",
                "niche": "finance",
                "skip_preflight": False,
            },
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data
        assert data["detail"]["missing_key_name"] == "GEMINI_API_KEY"
