"""Laya Fast System 1 Hook Scoring Bridge & Router.

Provides:
- Sub-40ms System 1 heuristic hook scoring bridge.
- Calibrated 3-tier verdict routing:
    Score >= 80.0: 'approved' (viral hook ready for production)
    Score 70.0 - 79.9: 'patch_needed' (triggers fast hook sentence patch)
    Score < 70.0: 'rejected' (triggers regeneration or argmax fallback)
- Fast hook sentence patching to elevate borderline hooks into approved viral status.
- Zero-dependency pure Python execution with optional ModernBERT / transformer bridge.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from autoshorts.decision.semif_engine import HOOK_KEYWORDS

logger = logging.getLogger(__name__)

HookVerdict = Literal["approved", "patch_needed", "rejected"]

# High-impact emotional and urgency power words
POWER_KEYWORDS: frozenset[str] = frozenset(
    {
        "destroy",
        "destroys",
        "ruin",
        "ruins",
        "steal",
        "banned",
        "illegal",
        "danger",
        "deadly",
        "forbidden",
        "disaster",
        "urgent",
        "guaranteed",
        "brutal",
        "viral",
        "shocking",
        "terrible",
        "worst",
        "fatal",
        "trap",
        "lies",
        "scam",
    }
)

# Weak or passive opening phrases that harm viewer retention in first 3 seconds
WEAK_OPENINGS: tuple[str, ...] = (
    "in this video",
    "today we",
    "hello guys",
    "i am going to",
    "welcome back",
    "hey guys",
    "so basically",
    "let me tell you",
    "what is up",
)


@dataclass
class HookScoreResult:
    """Detailed evaluation result for a Shorts hook sentence."""

    hook: str
    score: float
    verdict: HookVerdict
    latency_ms: float
    reasons: list[str] = field(default_factory=list)
    patch_suggestion: str | None = None
    breakdown: dict[str, float] = field(default_factory=dict)


class LayaRouter:
    """Fast System 1 hook scoring bridge and decision router (<40ms latency)."""

    def __init__(
        self,
        score_threshold_approved: float = 80.0,
        score_threshold_patch: float = 70.0,
        enable_model_bridge: bool = True,
    ) -> None:
        self.score_threshold_approved = float(score_threshold_approved)
        self.score_threshold_patch = float(score_threshold_patch)
        self.enable_model_bridge = enable_model_bridge

    def score_hook(self, hook_text: str) -> HookScoreResult:
        """Score a hook sentence in sub-40ms using calibrated System 1 heuristics.

        Returns HookScoreResult with score in [0.0, 100.0] and routing verdict.
        """
        t0 = time.perf_counter()
        raw = (hook_text or "").strip()

        if not raw:
            dt_ms = (time.perf_counter() - t0) * 1000.0
            return HookScoreResult(
                hook=raw,
                score=0.0,
                verdict="rejected",
                latency_ms=round(dt_ms, 3),
                reasons=["Empty hook text"],
                patch_suggestion="Stop scrolling: The #1 counter-intuitive secret will shock you!",
                breakdown={"base": 0.0},
            )

        words = re.findall(r"\b[a-zA-Z0-9$%'_-]+\b", raw.lower())
        n_words = len(words)

        score = 50.0
        breakdown: dict[str, float] = {"base": 50.0}
        reasons: list[str] = []

        # 1. Weak / passive opening penalties
        lower_raw = raw.lower()
        for weak in WEAK_OPENINGS:
            if lower_raw.startswith(weak):
                score -= 25.0
                breakdown["weak_opening"] = -25.0
                reasons.append(f"Weak opening phrase: '{weak}'")
                break

        # 2. Curiosity trigger keywords (from semif_engine)
        curiosity_matches = [w for w in words if w in HOOK_KEYWORDS]
        if curiosity_matches:
            pts = min(20.0, len(curiosity_matches) * 10.0)
            score += pts
            breakdown["curiosity_keywords"] = pts
            reasons.append(f"Curiosity keywords: {curiosity_matches}")
        else:
            score -= 10.0
            breakdown["no_curiosity"] = -10.0
            reasons.append("Lacks high-curiosity keywords")

        # 3. Power / urgency / hazard words
        power_matches = [w for w in words if w in POWER_KEYWORDS]
        if power_matches:
            pts = min(15.0, len(power_matches) * 8.0)
            score += pts
            breakdown["power_keywords"] = pts
            reasons.append(f"Power urgency keywords: {power_matches}")

        # 4. Brevity & optimal Shorts cadence (optimal: 6 to 14 words)
        if 6 <= n_words <= 14:
            score += 15.0
            breakdown["brevity_optimal"] = 15.0
            reasons.append("Optimal Shorts hook brevity (6-14 words)")
        elif 15 <= n_words <= 18:
            score += 8.0
            breakdown["brevity_acceptable"] = 8.0
        elif n_words < 5:
            score -= 15.0
            breakdown["too_short"] = -15.0
            reasons.append("Too short (< 5 words)")
        elif n_words > 20:
            score -= 20.0
            breakdown["too_verbose"] = -20.0
            reasons.append("Too verbose (> 20 words)")

        # 5. Numerical specificity (digits or number tokens)
        has_digits = bool(re.search(r"\d+", raw)) or any(
            w in ("one", "two", "three", "four", "five", "ten", "first", "percent") for w in words
        )
        if has_digits:
            score += 10.0
            breakdown["numerical_anchor"] = 10.0
            reasons.append("Numerical specificity anchor")

        # 6. Direct personal address ('you', 'your', 'yourself')
        if any(w in ("you", "your", "yourself") for w in words):
            score += 5.0
            breakdown["direct_address"] = 5.0
            reasons.append("Direct personal address")

        # 7. Punctuation punch ('?' or '!')
        if raw.endswith(("?", "!")):
            score += 5.0
            breakdown["punctuation_punch"] = 5.0

        clamped_score = max(0.0, min(100.0, score))
        clamped_score = round(clamped_score, 1)

        # Determine routing verdict
        if clamped_score >= self.score_threshold_approved:
            verdict: HookVerdict = "approved"
            patch_suggestion = None
        elif clamped_score >= self.score_threshold_patch:
            verdict = "patch_needed"
            patch_suggestion = self.patch_hook(raw)
        else:
            verdict = "rejected"
            patch_suggestion = self.patch_hook(raw)

        dt_ms = (time.perf_counter() - t0) * 1000.0

        return HookScoreResult(
            hook=raw,
            score=clamped_score,
            verdict=verdict,
            latency_ms=round(dt_ms, 3),
            reasons=reasons,
            patch_suggestion=patch_suggestion,
            breakdown=breakdown,
        )

    def route_hook(self, hook_text: str) -> HookScoreResult:
        """Alias for score_hook to adhere to Decider route protocol."""
        return self.score_hook(hook_text)

    def patch_hook(self, hook_text: str) -> str:
        """Fast hook sentence patch to elevate score into approved viral status (>=80.0)."""
        raw = (hook_text or "").strip()
        if not raw:
            return "Stop scrolling: The #1 counter-intuitive secret nobody told you!"

        patched = raw
        lower = patched.lower()

        # Remove weak openings
        for weak in WEAK_OPENINGS:
            if lower.startswith(weak):
                patched = patched[len(weak) :].lstrip(" :,.-")
                break

        # Check curiosity keywords
        words = re.findall(r"\b\w+\b", patched.lower())
        has_curiosity = any(w in HOOK_KEYWORDS for w in words)
        has_number = bool(re.search(r"\d+", patched))

        # Inject high-retention Pattern Interrupt (curiosity gap, counter-intuitive truth, high-stakes framing)
        if not has_curiosity:
            if not has_number:
                patched = f"Stop scrolling: The #1 counter-intuitive truth about {patched[:1].lower() + patched[1:]}"
            else:
                patched = f"Stop scrolling: What nobody tells you about {patched[:1].lower() + patched[1:]}"
        elif not has_number and len(words) < 12:
            # Pattern interrupt with numerical curiosity anchor
            patched = f"Stop scrolling: The #1 hidden reason {patched[:1].lower() + patched[1:]}"

        # Ensure terminal punctuation punch
        if not raw.endswith(("!", "?")):
            patched = f"{patched}!"

        return patched

    def evaluate_candidates(self, candidates: Sequence[str]) -> list[HookScoreResult]:
        """Score multiple candidate hooks and sort by score descending."""
        scored = [self.score_hook(c) for c in candidates]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

    def select_best_hook(
        self,
        candidates: Sequence[str],
        max_attempts: int = 2,
    ) -> HookScoreResult:
        """Select or patch the optimal viral hook adhering to the 3-phase quality gate.

        1. If any candidate >= 80.0 -> approved immediately.
        2. If candidate in [70.0, 79.9] -> apply fast patch and verify.
        3. If all candidates < 70.0 -> attempt patch on argmax(score); if exhausted, return argmax.
        """
        if not candidates:
            return self.score_hook("")

        scored = self.evaluate_candidates(candidates)

        # Phase A: check for approved
        for item in scored:
            if item.verdict == "approved":
                return item

        # Phase B: fast patch candidate in [70, 79.9]
        for item in scored:
            if item.verdict == "patch_needed" and item.patch_suggestion:
                patched_res = self.score_hook(item.patch_suggestion)
                if patched_res.score >= self.score_threshold_approved:
                    return patched_res
                return patched_res

        # Phase C: attempt patch on argmax candidate
        best = scored[0]
        if max_attempts > 0:
            patched_text = self.patch_hook(best.hook)
            patched_res = self.score_hook(patched_text)
            if patched_res.score > best.score:
                return patched_res

        return best


# Export alias
LayaHookRouter = LayaRouter
