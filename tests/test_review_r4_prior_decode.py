"""Review round 4: performance prior robustness and UTF-8 decoding of yt-dlp output."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from autoshorts.core.config import AnalyticsPriorsConfig, Settings
from autoshorts.core.models import ClipCandidate
from autoshorts.decider.performance_prior import (
    AdaptiveThresholds,
    PerformancePrior,
    PriorObservation,
    build_performance_prior,
    length_bucket,
)
from autoshorts.decision.orchestrator import SemIfDecider

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _prior_settings() -> AnalyticsPriorsConfig:
    return AnalyticsPriorsConfig(enabled=True, min_videos=3, min_group_samples=2, shrinkage_m=0.0)


def test_nan_svs_cannot_poison_the_prior() -> None:
    obs = [PriorObservation(svs=80.0, length_bucket="under_35s") for _ in range(4)]
    obs.append(PriorObservation(svs=float("nan"), length_bucket="under_35s"))
    prior = build_performance_prior(obs, _prior_settings())
    assert prior.sample_count == 4
    assert all(abs(v) < 1e-6 for v in prior.effects.values())
    assert prior.channel_mean_svs == 80.0


def test_nan_effect_gives_zero_adjustment_not_max_boost() -> None:
    bucket = length_bucket(20.0)
    prior = PerformancePrior(
        active=True,
        status="x",
        sample_count=5,
        channel_mean_svs=50.0,
        max_score_adjustment=0.05,
        hook_thresholds=AdaptiveThresholds(80.0, 70.0, False, "r"),
        effects={("length", bucket): float("nan")},
    )
    assert prior.score_adjustment(20.0, "text").value == 0.0


def test_select_clips_survives_a_failing_prior() -> None:
    class Exploding:
        active = True

        def apply(self, candidates):
            raise RuntimeError("boom")

        def score_adjustment(self, *args, **kwargs):
            raise RuntimeError("boom")

    decider = SemIfDecider(provider=None, settings=Settings())
    cand = ClipCandidate(start=0.0, end=30.0, text="A plain sentence about things.", score=0.7)
    selections = decider.select_clips([cand], count=1, job_id="j", performance_prior=Exploding())
    assert len(selections) == 1


def test_disabled_prior_is_inactive_and_changes_nothing() -> None:
    prior = build_performance_prior(
        [PriorObservation(svs=90.0, length_bucket="under_35s")] * 20, AnalyticsPriorsConfig()
    )
    cands = [ClipCandidate(start=0.0, end=20.0, text="x", score=0.5)]
    assert prior.active is False
    assert prior.apply(cands)[0] is cands[0]


def test_metadata_fetch_decodes_ytdlp_output_as_utf8() -> None:
    from autoshorts.discovery.metadata_fetcher import MetadataFetcher

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 1, "", "err")

    with patch("subprocess.run", side_effect=fake_run):
        try:
            MetadataFetcher(yt_dlp_binary="yt-dlp").fetch(URL)
        except Exception:
            pass
    assert seen.get("encoding") == "utf-8" and seen.get("errors") == "replace"
