"""Performance priors: historical Shorts analytics as clip-selection signals.

Turns (analytics record, originating job) pairs into two bounded signals:

(a) Candidate score priors. Each historical clip is reduced to features that are
    also known when candidate windows are ranked: a length bucket (clip duration)
    and a hook category (``classify_semantic_hook`` on the clip text). For every
    feature group with enough samples, the group's mean Shorts Viral Score (SVS)
    is compared with the channel mean, shrunk toward zero by its sample count, and
    mapped to a small additive adjustment on the candidate score.

(b) Advisory hook thresholds. A one-split search over historical hook scores
    (``LayaRouter``) finds the approve cutoff that best separates above- and
    below-cutoff SVS. The cutoff may move only within ``threshold_max_shift`` of the
    configured default, and only when the split lifts SVS by at least
    ``threshold_min_lift`` points.

Safety rules:
- Disabled, or fewer than ``min_videos`` usable observations: the prior is inactive
  and ``apply`` returns the candidates unchanged. Thresholds keep the defaults.
- Feature groups with fewer than ``min_group_samples`` observations are ignored.
- Group effects are shrunk: ``n / (n + m) * (group_mean - channel_mean)``.
- The total score adjustment is clamped to +/- ``max_score_adjustment``.

The module does no I/O. Callers pass in the (record, job) pairs they already read.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autoshorts.decider.laya_router import LayaRouter
from autoshorts.decision.window_slicer import classify_semantic_hook

if TYPE_CHECKING:
    from autoshorts.core.config import AnalyticsPriorsConfig
    from autoshorts.core.models import ClipCandidate

logger = logging.getLogger("autoshorts.decider.performance_prior")

LENGTH_FEATURE = "length"
HOOK_FEATURE = "hook"

# Length buckets: edges match SHORT_CLIP_SEC in decider/style.py (35s) and a 45s upper edge.
SHORT_LENGTH_SEC = 35.0
LONG_LENGTH_SEC = 45.0

# An average SVS deviation of this many points saturates the score adjustment.
SVS_POINTS_FOR_MAX_ADJUSTMENT = 20.0

# Resolution of the hook-threshold search grid.
THRESHOLD_SEARCH_STEP = 0.5

FeatureKey = tuple[str, str]


@dataclass(frozen=True)
class PriorObservation:
    """One historical clip reduced to the features known at selection time."""

    svs: float
    length_bucket: str | None = None
    hook_category: str | None = None
    hook_score: float | None = None

    def feature_keys(self) -> tuple[FeatureKey, ...]:
        """Return the (feature, value) groups this observation belongs to."""
        keys: list[FeatureKey] = []
        if self.length_bucket:
            keys.append((LENGTH_FEATURE, self.length_bucket))
        if self.hook_category:
            keys.append((HOOK_FEATURE, self.hook_category))
        return tuple(keys)


@dataclass(frozen=True)
class AdaptiveThresholds:
    """Approve and patch hook thresholds, with the reason they were chosen."""

    approve: float
    patch: float
    adapted: bool
    reason: str
    sample_count: int = 0


@dataclass(frozen=True)
class PriorAdjustment:
    """Score adjustment for one candidate, and the feature groups that produced it."""

    value: float = 0.0
    matched: tuple[str, ...] = ()


@dataclass(frozen=True)
class PerformancePrior:
    """Historical priors ready to be applied to candidate windows."""

    active: bool
    status: str
    sample_count: int
    channel_mean_svs: float
    max_score_adjustment: float
    hook_thresholds: AdaptiveThresholds
    effects: dict[FeatureKey, float] = field(default_factory=dict)

    def score_adjustment(self, duration_sec: Any, text: str | None) -> PriorAdjustment:
        """Return the bounded score adjustment for a candidate with these features."""
        if not self.active or not self.effects:
            return PriorAdjustment()

        keys: list[FeatureKey] = []
        bucket = length_bucket(duration_sec)
        if bucket:
            keys.append((LENGTH_FEATURE, bucket))
        category = hook_category(text)
        if category:
            keys.append((HOOK_FEATURE, category))

        matched = [(key, self.effects[key]) for key in keys if key in self.effects]
        if not matched:
            return PriorAdjustment()

        raw = sum(effect for _, effect in matched)
        if not math.isfinite(raw):
            return PriorAdjustment()  # NaN would otherwise clamp to the maximum boost
        scaled = max(-1.0, min(1.0, raw / SVS_POINTS_FOR_MAX_ADJUSTMENT))
        value = round(self.max_score_adjustment * scaled, 4)
        labels = tuple(f"{feature}:{group}" for (feature, group), _ in matched)
        return PriorAdjustment(value=value, matched=labels)

    def apply(self, candidates: Sequence[ClipCandidate]) -> list[ClipCandidate]:
        """Return candidates with priors applied to their scores.

        Inputs are not mutated. A candidate without a matching group is returned as the
        same object, so an inactive prior leaves the list unchanged.
        """
        if not self.active or not self.effects:
            return list(candidates)

        adjusted: list[ClipCandidate] = []
        for cand in candidates:
            adj = self.score_adjustment(cand.duration, cand.text).value
            if adj == 0.0:
                adjusted.append(cand)
                continue
            if not math.isfinite(cand.score):
                adjusted.append(cand)
                continue
            new_score = round(max(0.0, min(1.0, cand.score + adj)), 4)
            adjusted.append(cand.model_copy(update={"score": new_score}))
        return adjusted


def length_bucket(duration_sec: Any) -> str | None:
    """Map a clip duration in seconds to a length bucket, or None when unusable."""
    try:
        value = float(duration_sec)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0.0:
        return None
    if value < SHORT_LENGTH_SEC:
        return f"under_{SHORT_LENGTH_SEC:g}s"
    if value < LONG_LENGTH_SEC:
        return f"{SHORT_LENGTH_SEC:g}_to_{LONG_LENGTH_SEC:g}s"
    return f"over_{LONG_LENGTH_SEC:g}s"


def hook_category(text: str | None) -> str | None:
    """Return the primary hook category of a clip's opening sentence, or None."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    return str(classify_semantic_hook(cleaned)["primary_category"])


def hook_score(hook_text: str | None) -> float | None:
    """Return the LayaRouter hook score (0-100) for a hook sentence, or None."""
    cleaned = (hook_text or "").strip()
    if not cleaned:
        return None
    return float(LayaRouter().score_hook(cleaned).score)


def observations_from_pairs(pairs: Iterable[tuple[Any, Any]]) -> list[PriorObservation]:
    """Reduce (VideoAnalyticsRecord, Job | None) pairs to prior observations.

    Rows are skipped when there is no job, no selected clip, no views yet (the SVS of
    an unseen video is not a performance signal), or no usable feature at all.
    """
    observations: list[PriorObservation] = []
    for record, job in pairs:
        try:
            views = int(getattr(record, "views", 0) or 0)
            clips = list(getattr(job, "selected_clips", None) or []) if job is not None else []
            if views <= 0 or not clips:
                continue

            svs = float(record.svs_score)
            if not math.isfinite(svs):
                continue  # one NaN/inf SVS would poison the channel mean and every group effect
            clip = clips[0]
            candidate = clip.candidate
            obs = PriorObservation(
                svs=svs,
                length_bucket=length_bucket(candidate.duration),
                hook_category=hook_category(candidate.text),
                hook_score=hook_score(getattr(clip, "hook_text", None)),
            )
        except Exception as err:  # one malformed row must not sink the whole prior
            logger.debug("Skipped analytics row for performance prior: %s", err)
            continue

        if obs.length_bucket or obs.hook_category or obs.hook_score is not None:
            observations.append(obs)
    return observations


def adaptive_hook_thresholds(
    observations: Sequence[PriorObservation],
    settings: AnalyticsPriorsConfig,
    *,
    base_approve: float | None = None,
    base_patch: float | None = None,
) -> AdaptiveThresholds:
    """Fit advisory approve/patch hook thresholds from historical hook scores.

    The search tries approve cutoffs within ``threshold_max_shift`` of the default and
    keeps the cutoff with the largest SVS lift between hooks at or above it and hooks
    below it. Each side needs ``min_group_samples`` observations. The patch threshold
    keeps the default approve-to-patch band.
    """
    defaults = LayaRouter()
    approve0 = float(defaults.score_threshold_approved if base_approve is None else base_approve)
    patch0 = float(defaults.score_threshold_patch if base_patch is None else base_patch)
    scored = [(o.hook_score, o.svs) for o in observations if o.hook_score is not None]
    n = len(scored)

    def fallback(reason: str) -> AdaptiveThresholds:
        return AdaptiveThresholds(
            approve=approve0,
            patch=patch0,
            adapted=False,
            reason=reason,
            sample_count=n,
        )

    if not settings.enabled:
        return fallback("performance priors disabled")
    if not settings.adaptive_thresholds:
        return fallback("adaptive thresholds disabled")

    band = approve0 - patch0
    if band <= 0.0:
        return fallback("default approve threshold must be above the patch threshold")
    if n < settings.min_videos:
        return fallback(f"{n} hook-scored sample(s), need {settings.min_videos}")

    shift = settings.threshold_max_shift
    low, high = approve0 - shift, approve0 + shift
    steps = int(round((high - low) / THRESHOLD_SEARCH_STEP))

    best: tuple[tuple[float, float], float, float] | None = None
    for i in range(steps + 1):
        cut = low + i * THRESHOLD_SEARCH_STEP
        above = [svs for hook, svs in scored if hook >= cut]
        below = [svs for hook, svs in scored if hook < cut]
        if len(above) < settings.min_group_samples or len(below) < settings.min_group_samples:
            continue
        lift = sum(above) / len(above) - sum(below) / len(below)
        # Ties go to the cutoff nearest the default, so an ambiguous split does not move it.
        key = (lift, -abs(cut - approve0))
        if best is None or key > best[0]:
            best = (key, cut, lift)

    if best is None:
        return fallback("no hook split has enough samples on both sides")

    _, cut, lift = best
    if lift < settings.threshold_min_lift:
        return fallback(
            f"best hook split lifts SVS by {lift:+.1f}, below {settings.threshold_min_lift:g}"
        )

    approve = round(cut, 2)
    return AdaptiveThresholds(
        approve=approve,
        patch=round(approve - band, 2),
        adapted=abs(approve - approve0) > 1e-9,
        reason=f"hook score >= {approve:g} lifts SVS by {lift:+.1f} over {n} sample(s)",
        sample_count=n,
    )


def _group_effects(
    observations: Sequence[PriorObservation],
    channel_mean: float,
    shrinkage_m: float,
    min_group_samples: int,
) -> dict[FeatureKey, float]:
    groups: dict[FeatureKey, list[float]] = {}
    for obs in observations:
        for key in obs.feature_keys():
            groups.setdefault(key, []).append(obs.svs)

    effects: dict[FeatureKey, float] = {}
    for key, values in groups.items():
        n = len(values)
        if n < min_group_samples:
            continue
        shrink = n / (n + shrinkage_m) if (n + shrinkage_m) > 0.0 else 1.0
        effects[key] = round(shrink * (sum(values) / n - channel_mean), 4)
    return effects


def build_performance_prior(
    observations: Iterable[PriorObservation],
    settings: AnalyticsPriorsConfig,
) -> PerformancePrior:
    """Build the prior and advisory thresholds from historical observations."""
    obs = [o for o in observations if math.isfinite(o.svs)]
    n = len(obs)
    thresholds = adaptive_hook_thresholds(obs, settings)

    if not settings.enabled:
        return PerformancePrior(
            active=False,
            status="disabled",
            sample_count=n,
            channel_mean_svs=0.0,
            max_score_adjustment=settings.max_score_adjustment,
            hook_thresholds=thresholds,
        )
    if n < settings.min_videos:
        return PerformancePrior(
            active=False,
            status=f"inactive: {n} sample(s), need {settings.min_videos}",
            sample_count=n,
            channel_mean_svs=0.0,
            max_score_adjustment=settings.max_score_adjustment,
            hook_thresholds=thresholds,
        )

    channel_mean = sum(o.svs for o in obs) / n
    effects = _group_effects(obs, channel_mean, settings.shrinkage_m, settings.min_group_samples)
    return PerformancePrior(
        active=True,
        status=f"active: {n} sample(s), {len(effects)} feature group(s)",
        sample_count=n,
        channel_mean_svs=round(channel_mean, 2),
        max_score_adjustment=settings.max_score_adjustment,
        hook_thresholds=thresholds,
        effects=effects,
    )


__all__ = [
    "AdaptiveThresholds",
    "PerformancePrior",
    "PriorAdjustment",
    "PriorObservation",
    "adaptive_hook_thresholds",
    "build_performance_prior",
    "hook_category",
    "hook_score",
    "length_bucket",
    "observations_from_pairs",
]
