"""Tests for performance priors: historical Shorts analytics as clip-selection signals.

Covers:
1. Feature buckets (length, hook category) and observation extraction from (record, job) pairs.
2. Safe fallbacks: no data, too few videos, disabled config, small feature groups.
3. Score priors: shrinkage, boosts and penalties, clamping, and non-mutation.
4. Advisory hook thresholds: split search, bounded movement, and default fallbacks.
5. Config defaults and the example YAML block.
6. Wiring: SemIfDecider.select_clips and the semif pipeline stage.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from autoshorts.analytics.models import VideoAnalyticsRecord
from autoshorts.core.config import AnalyticsPriorsConfig, Settings
from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
    Job,
    JobStatus,
    UploadResult,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.decider import records
from autoshorts.decider.laya_router import LayaRouter
from autoshorts.decider.performance_prior import (
    PerformancePrior,
    PriorObservation,
    adaptive_hook_thresholds,
    build_performance_prior,
    hook_category,
    length_bucket,
    observations_from_pairs,
)
from autoshorts.decider.records import decisions_for_job
from autoshorts.decision.orchestrator import SemIfDecider
from autoshorts.decision.window_slicer import classify_semantic_hook
from autoshorts.pipeline.stages_ingest import IngestMixin

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_decision_records():
    with records._lock:
        records._records.clear()
    yield
    with records._lock:
        records._records.clear()


def _settings(**overrides) -> AnalyticsPriorsConfig:
    base = {
        "enabled": True,
        "min_videos": 10,
        "min_group_samples": 3,
        "shrinkage_m": 2.0,
        "max_score_adjustment": 0.05,
        "adaptive_thresholds": True,
        "threshold_max_shift": 5.0,
        "threshold_min_lift": 3.0,
    }
    base.update(overrides)
    return AnalyticsPriorsConfig(**base)


def _obs(svs: float, length: str | None = None, hook: str | None = None, hook_score=None):
    return PriorObservation(
        svs=svs, length_bucket=length, hook_category=hook, hook_score=hook_score
    )


def _length_history(short_svs: float = 80.0, long_svs: float = 50.0, n: int = 5):
    """n clips under 35s at short_svs and n clips over 45s at long_svs (channel mean is the midpoint)."""
    return [_obs(short_svs, length="under_35s") for _ in range(n)] + [
        _obs(long_svs, length="over_45s") for _ in range(n)
    ]


def _hook_history(high_hook: float, high_svs: float, low_hook: float, low_svs: float, n: int = 6):
    return [_obs(high_svs, hook_score=high_hook) for _ in range(n)] + [
        _obs(low_svs, hook_score=low_hook) for _ in range(n)
    ]


def _cand(start: float, end: float, text: str = "Plain opening about the topic.", score=0.5):
    return ClipCandidate(start=start, end=end, text=text, score=score)


# -----------------------------------------------------------------------------
# 1. Feature buckets and observation extraction
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "duration, expected",
    [
        (10.0, "under_35s"),
        (34.9, "under_35s"),
        (35.0, "35_to_45s"),
        (44.9, "35_to_45s"),
        (45.0, "over_45s"),
        (60.0, "over_45s"),
    ],
)
def test_length_bucket_edges(duration, expected):
    assert length_bucket(duration) == expected


@pytest.mark.parametrize("duration", [0.0, -3.0, None, "abc", float("nan"), float("inf")])
def test_length_bucket_rejects_unusable_durations(duration):
    assert length_bucket(duration) is None


def test_hook_category_matches_window_classifier():
    text = "Why does nobody talk about this secret?"
    assert hook_category(text) == classify_semantic_hook(text)["primary_category"]
    assert hook_category("   ") is None
    assert hook_category(None) is None


def test_observations_from_pairs_reduce_clip_features():
    record = VideoAnalyticsRecord(
        video_id="vid-1",
        job_id="job-1",
        views=5000,
        likes=300,
        comments=20,
        shares=10,
        avg_view_percentage=70.0,
        viewed_vs_swiped_pct=65.0,
    )
    job = Job(
        id="job-1",
        source_url="https://youtube.com/watch?v=one",
        selected_clips=[
            ClipSelection(
                candidate=ClipCandidate(
                    start=0.0, end=30.0, text="Why does nobody talk about this secret?"
                ),
                hook_text="Why does nobody talk about this?",
            )
        ],
    )

    observations = observations_from_pairs([(record, job)])

    assert len(observations) == 1
    obs = observations[0]
    assert obs.svs == record.svs_score
    assert obs.length_bucket == "under_35s"
    assert obs.hook_category == hook_category("Why does nobody talk about this secret?")
    assert obs.hook_score == LayaRouter().score_hook("Why does nobody talk about this?").score


def test_observations_skip_rows_without_usable_clip_data():
    no_views = VideoAnalyticsRecord(video_id="v0", job_id="j0", views=0)
    with_views = VideoAnalyticsRecord(video_id="v1", job_id="j1", views=900)
    no_clips = Job(id="j1", source_url="https://youtube.com/watch?v=two")

    assert observations_from_pairs([(no_views, None)]) == []
    assert observations_from_pairs([(with_views, None)]) == []
    assert observations_from_pairs([(with_views, no_clips)]) == []


# -----------------------------------------------------------------------------
# 2. Safe fallbacks
# -----------------------------------------------------------------------------


def test_no_history_keeps_defaults_and_identity():
    prior = build_performance_prior([], _settings())

    assert prior.active is False
    cands = [_cand(0.0, 40.0, score=0.8), _cand(50.0, 90.0, score=0.7)]
    applied = prior.apply(cands)
    assert all(a is b for a, b in zip(applied, cands, strict=True))
    assert prior.score_adjustment(40.0, "Any text here.").value == 0.0
    assert prior.hook_thresholds.approve == 80.0
    assert prior.hook_thresholds.patch == 70.0
    assert prior.hook_thresholds.adapted is False


def test_below_min_videos_is_inactive():
    prior = build_performance_prior(_length_history(n=4), _settings(min_videos=10))

    assert prior.active is False
    assert "need 10" in prior.status


def test_disabled_config_ignores_history():
    prior = build_performance_prior(_length_history(), _settings(enabled=False))

    assert prior.active is False
    assert prior.status == "disabled"
    assert prior.hook_thresholds.approve == 80.0


def test_small_feature_groups_are_ignored():
    obs = [_obs(90.0, length="under_35s") for _ in range(2)] + [
        _obs(40.0, length="over_45s") for _ in range(8)
    ]
    prior = build_performance_prior(obs, _settings(min_group_samples=3))

    assert prior.active is True
    assert ("length", "under_35s") not in prior.effects
    assert ("length", "over_45s") in prior.effects


# -----------------------------------------------------------------------------
# 3. Score priors
# -----------------------------------------------------------------------------


def test_group_effect_is_shrunk_toward_channel_mean():
    prior = build_performance_prior(_length_history(80.0, 50.0, n=5), _settings(shrinkage_m=2.0))

    # Channel mean is 65. Each group has n=5, so the shrink factor is 5 / 7.
    assert prior.channel_mean_svs == pytest.approx(65.0)
    assert prior.effects[("length", "under_35s")] == pytest.approx(5 / 7 * 15.0, abs=1e-4)
    assert prior.effects[("length", "over_45s")] == pytest.approx(-5 / 7 * 15.0, abs=1e-4)

    unshrunk = build_performance_prior(_length_history(80.0, 50.0, n=5), _settings(shrinkage_m=0.0))
    assert abs(prior.effects[("length", "under_35s")]) < abs(
        unshrunk.effects[("length", "under_35s")]
    )


def test_matching_candidates_get_boost_and_penalty_others_are_untouched():
    prior = build_performance_prior(_length_history(), _settings())
    short = _cand(0.0, 30.0, score=0.78)  # 30s -> under_35s, boosted
    long_ = _cand(100.0, 150.0, score=0.80)  # 50s -> over_45s, penalised
    mid = _cand(200.0, 240.0, score=0.60)  # 40s -> 35_to_45s, no historical group

    applied = prior.apply([short, long_, mid])

    expected_adj = 0.0268  # 0.05 * (5/7 * 15) / 20, rounded to 4 places
    assert applied[0].score == pytest.approx(0.78 + expected_adj, abs=1e-4)
    assert applied[1].score == pytest.approx(0.80 - expected_adj, abs=1e-4)
    assert applied[2] is mid


def test_adjustment_is_clamped_to_max_score_adjustment():
    prior = build_performance_prior(
        _length_history(short_svs=100.0, long_svs=0.0, n=20),
        _settings(max_score_adjustment=0.05),
    )

    assert prior.score_adjustment(30.0, "Plain text.").value == pytest.approx(0.05)
    assert prior.score_adjustment(50.0, "Plain text.").value == pytest.approx(-0.05)

    tighter = build_performance_prior(
        _length_history(short_svs=100.0, long_svs=0.0, n=20),
        _settings(max_score_adjustment=0.01),
    )
    assert tighter.score_adjustment(30.0, "Plain text.").value == pytest.approx(0.01)


def test_adjusted_scores_stay_in_unit_interval():
    prior = build_performance_prior(_length_history(), _settings())

    applied = prior.apply([_cand(0.0, 30.0, score=0.99)])

    assert applied[0].score == 1.0


def test_apply_does_not_mutate_input_candidates():
    prior = build_performance_prior(_length_history(), _settings())
    cand = _cand(0.0, 30.0, score=0.78)

    applied = prior.apply([cand])

    assert cand.score == 0.78
    assert applied[0] is not cand
    assert applied[0].score != cand.score


# -----------------------------------------------------------------------------
# 4. Advisory hook thresholds
# -----------------------------------------------------------------------------


def test_thresholds_keep_defaults_without_enough_hook_samples():
    thresholds = adaptive_hook_thresholds(
        _hook_history(77.0, 80.0, 70.0, 40.0, n=2), _settings(min_videos=10)
    )

    assert thresholds.approve == 80.0
    assert thresholds.patch == 70.0
    assert thresholds.adapted is False


def test_clear_hook_split_moves_approve_threshold_and_keeps_band():
    thresholds = adaptive_hook_thresholds(_hook_history(77.0, 80.0, 70.0, 40.0), _settings())

    # Every cut in (70, 77] separates the same two groups. The one nearest 80 wins.
    assert thresholds.approve == 77.0
    assert thresholds.patch == 67.0
    assert thresholds.adapted is True
    assert thresholds.sample_count == 12
    assert "lifts SVS" in thresholds.reason


def test_ambiguous_split_around_default_keeps_default():
    thresholds = adaptive_hook_thresholds(_hook_history(85.0, 80.0, 75.0, 40.0), _settings())

    assert thresholds.approve == 80.0
    assert thresholds.patch == 70.0
    assert thresholds.adapted is False


def test_threshold_movement_is_bounded_by_max_shift():
    # The only valid cut inside [75, 85] is 75, so the search lands on the lower bound.
    thresholds = adaptive_hook_thresholds(_hook_history(75.0, 80.0, 70.0, 40.0), _settings())

    assert thresholds.approve == 75.0
    assert thresholds.patch == 65.0
    assert thresholds.adapted is True


def test_split_outside_shift_window_is_not_adopted():
    thresholds = adaptive_hook_thresholds(_hook_history(74.0, 80.0, 60.0, 40.0), _settings())

    assert thresholds.approve == 80.0
    assert thresholds.adapted is False


def test_small_lift_keeps_default():
    thresholds = adaptive_hook_thresholds(
        _hook_history(77.0, 50.0, 70.0, 49.0), _settings(threshold_min_lift=3.0)
    )

    assert thresholds.approve == 80.0
    assert thresholds.adapted is False
    assert "below" in thresholds.reason


def test_adaptive_thresholds_toggle_off_keeps_defaults():
    thresholds = adaptive_hook_thresholds(
        _hook_history(77.0, 80.0, 70.0, 40.0), _settings(adaptive_thresholds=False)
    )

    assert thresholds.approve == 80.0
    assert thresholds.adapted is False


# -----------------------------------------------------------------------------
# 5. Config defaults and example YAML
# -----------------------------------------------------------------------------


def test_analytics_priors_default_to_off():
    cfg = AnalyticsPriorsConfig()

    assert cfg.enabled is False
    assert cfg.min_videos == 10
    assert Settings().analytics_priors.enabled is False


def test_example_config_block_uses_known_keys():
    raw = yaml.safe_load((REPO_ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    block = raw["analytics_priors"]

    assert set(block) <= set(AnalyticsPriorsConfig.model_fields)
    assert AnalyticsPriorsConfig(**block).enabled is False


# -----------------------------------------------------------------------------
# 6. Wiring
# -----------------------------------------------------------------------------


def _ranking_pair():
    short = _cand(0.0, 30.0, score=0.78)  # boosted by the length prior
    long_ = _cand(100.0, 160.0, score=0.80)  # penalised by the length prior
    return short, long_


def test_select_clips_without_prior_is_unchanged():
    decider = SemIfDecider(provider=None, settings=Settings())
    short, long_ = _ranking_pair()

    selections = decider.select_clips([short, long_], count=1, job_id="job-plain")

    assert selections[0].candidate.start == 100.0
    assert selections[0].candidate.score == 0.80


def test_select_clips_applies_prior_before_ranking():
    decider = SemIfDecider(provider=None, settings=Settings())
    short, long_ = _ranking_pair()
    prior = build_performance_prior(_length_history(), _settings())

    selections = decider.select_clips(
        [short, long_], count=1, job_id="job-prior", performance_prior=prior
    )

    assert selections[0].candidate.start == 0.0
    assert selections[0].candidate.score == pytest.approx(0.8068, abs=1e-4)
    prior_records = [r for r in decisions_for_job("job-prior") if r.name == "performance_prior"]
    assert len(prior_records) == 1
    assert prior_records[0].source == "analytics"
    assert prior_records[0].value.startswith("+")


def _seed_history(sm: StateManager, n_per_group: int = 5) -> None:
    """Seed uploaded jobs: short clips perform well, long clips do not."""
    for i in range(n_per_group):
        for kind, perf in (("short", 90.0), ("long", 30.0)):
            clip_len = 30.0 if kind == "short" else 60.0
            job = Job(
                id=f"hist-{kind}-{i}",
                source_url=f"https://youtube.com/watch?v={kind}{i}",
                status=JobStatus.COMPLETED,
                selected_clips=[
                    ClipSelection(
                        candidate=ClipCandidate(
                            start=0.0, end=clip_len, text="Plain opening about the topic."
                        ),
                        hook_text="",
                    )
                ],
                upload_result=UploadResult(video_id=f"vid-{kind}-{i}", url="https://u"),
            )
            sm.save_job(job)
            sm.upsert_video_analytics(
                {
                    "video_id": f"vid-{kind}-{i}",
                    "job_id": job.id,
                    "views": 1000,
                    "likes": 100 if kind == "short" else 5,
                    "comments": 10,
                    "shares": 5,
                    "avg_view_percentage": perf,
                    "viewed_vs_swiped_pct": perf,
                }
            )


def _run_semif_stage(tmp_path: Path, *, enabled: bool):
    sm = StateManager(tmp_path / f"state-{enabled}.db")
    _seed_history(sm)
    settings = Settings(
        **{"analytics_priors": {"enabled": enabled, "min_videos": 5, "min_group_samples": 2}}
    )
    cand = _cand(0.0, 30.0, score=0.78)
    job = Job(
        id=f"job-stage-{enabled}",
        source_url="https://youtube.com/watch?v=stage",
        status=JobStatus.WINDOWS_SLICED,
        candidate_windows=[cand],
    )
    sm.save_job(job)

    decider = MagicMock()
    decider.score_candidates.return_value = [cand]
    decider.select_clips.return_value = [ClipSelection(candidate=cand, hook_text="")]
    fake_pipeline = SimpleNamespace(state_manager=sm, config=settings, decider=decider)

    IngestMixin._run_semif_stage(fake_pipeline, job)
    return job, decider


def test_semif_stage_call_is_unchanged_when_disabled(tmp_path):
    job, decider = _run_semif_stage(tmp_path, enabled=False)

    assert "performance_prior" not in decider.select_clips.call_args.kwargs
    assert set(decider.select_clips.call_args.kwargs) == {"count", "job_id"}
    assert "performance_prior" not in job.diagnostics


def test_semif_stage_passes_active_prior_and_records_advisory_thresholds(tmp_path):
    job, decider = _run_semif_stage(tmp_path, enabled=True)

    prior = decider.select_clips.call_args.kwargs["performance_prior"]
    assert isinstance(prior, PerformancePrior)
    assert prior.active is True
    section = job.diagnostics["performance_prior"]
    assert section["status"].startswith("active")
    assert section["advisory_hook_approve_threshold"] == 80.0
    assert section["advisory_hook_patch_threshold"] == 70.0
    assert section["advisory_hook_threshold_adapted"] is False
