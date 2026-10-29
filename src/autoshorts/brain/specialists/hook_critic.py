"""Hook Critic Specialist Agent.

Evaluates short-form video hooks with calibrated multi-tier quality gates.
Mode B (Generative):
  - Score >= 80: Approved.
  - Score 70-79: Fast hook patch applied.
  - Score < 70 (attempt 1): Rejected (regenerate).
  - Score < 70 (attempt >= 2): Argmax fallback with quality metadata.
Mode A (Link Repurposing):
  - Evaluates transcript cut curiosity and narrative completeness.
  - Adjusts window boundary (+/- 5.0s) or switches candidate (NEVER re-drafts spoken human words).
"""

from __future__ import annotations

import logging
import re

from autoshorts.brain.schemas import ViralReview

logger = logging.getLogger("autoshorts.brain.hook_critic")

HIGH_VIRAL_TRIGGERS: set[str] = {
    "secret",
    "secrets",
    "never",
    "nobody",
    "stop",
    "mistake",
    "mistakes",
    "truth",
    "lie",
    "lies",
    "hidden",
    "warning",
    "shocking",
    "destroy",
    "destroys",
    "insane",
    "banned",
    "illegal",
    "exposed",
    "worst",
}

PASSIVE_WEAK_PHRASES: list[str] = [
    "hello",
    "hi guys",
    "welcome back",
    "in this video",
    "today i want",
    "today we are",
    "my name is",
    "so basically",
    "let me explain",
    "first of all",
]


class HookCriticAgent:
    """Specialist agent calibrating hook viability and guarding editorial quality."""

    async def evaluate_mode_b(
        self,
        hook_text: str,
        attempt: int = 1,
        prior_reviews: list[ViralReview] | None = None,
    ) -> ViralReview:
        """Evaluate and calibrate a generated hook for Mode B."""
        clean_text = (hook_text or "").strip()
        score = self._compute_mode_b_score(clean_text)

        # Tier 1: Score >= 80 -> Approved
        if score >= 80.0:
            return ViralReview(
                hook_text=clean_text,
                score=score,
                verdict="approved",
                critique="High viral potential with strong curiosity gap and tension.",
                attempt=attempt,
                mode="mode_b",
                is_fallback=False,
            )

        # Tier 2: Score 70-79 -> Fast Hook Patch
        if score >= 70.0:
            patched = self._fast_patch_hook(clean_text)
            return ViralReview(
                hook_text=clean_text,
                score=score,
                verdict="fast_patch",
                critique="Moderate potential; applied fast hook patch to elevate curiosity velocity to 85+.",
                patched_hook=patched,
                attempt=attempt,
                mode="mode_b",
                is_fallback=False,
            )

        # Tier 3: Score < 70
        # If attempt 1 -> Reject & request regeneration
        if attempt < 2:
            return ViralReview(
                hook_text=clean_text,
                score=score,
                verdict="rejected",
                critique=f"Insufficient opening tension (score {score:.1f} < 70). Passive phrasing or weak curiosity.",
                attempt=attempt,
                mode="mode_b",
                is_fallback=False,
            )

        # Attempt >= 2: Argmax fallback across all attempts
        all_candidates: list[tuple[float, str]] = [(score, clean_text)]
        if prior_reviews:
            for pr in prior_reviews:
                all_candidates.append((pr.score, pr.hook_text))

        best_score, best_text = max(all_candidates, key=lambda c: c[0])
        return ViralReview(
            hook_text=best_text,
            score=best_score,
            verdict="approved",
            critique=(
                f"Maximum review attempts ({attempt}) exhausted. "
                f"Selected best candidate via argmax fallback (score {best_score:.1f})."
            ),
            attempt=attempt,
            mode="mode_b",
            is_fallback=True,
            metadata={"argmax_fallback": True, "attempts_evaluated": len(all_candidates)},
        )

    async def evaluate_mode_a(
        self,
        transcript_slice: str,
        start_sec: float = 0.0,
        end_sec: float = 45.0,
        candidate_index: int = 0,
        candidate_count: int = 1,
    ) -> ViralReview:
        """Evaluate candidate window slice for Mode A repurposing without altering words."""
        clean_transcript = (transcript_slice or "").strip()
        duration = end_sec - start_sec

        # Evaluate opening sentence (first 10-15 words)
        words = clean_transcript.split()
        first_sentence = " ".join(words[:12]) if words else ""

        score = self._compute_mode_a_score(clean_transcript, duration)

        # Mode A never re-drafts spoken human words!
        if score >= 75.0 and duration <= 59.5:
            return ViralReview(
                hook_text=first_sentence,
                score=score,
                verdict="approved",
                critique="Candidate slice contains strong opening narrative momentum and clean sentence boundaries.",
                mode="mode_a",
                candidate_index=candidate_index,
                suggested_shift_seconds=0.0,
            )

        # If narrative cut is suboptimal, recommend window shift or candidate switch
        suggested_shift = 5.0
        new_candidate = candidate_index
        if candidate_count > 1 and score < 60.0:
            new_candidate = (candidate_index + 1) % candidate_count
            critique = f"Low narrative completeness ({score:.1f}). Switched to candidate #{new_candidate + 1}."
        else:
            critique = f"Suboptimal opening/boundary ({score:.1f}). Recommended +5.0s window boundary shift."

        return ViralReview(
            hook_text=first_sentence,
            score=score,
            verdict="fast_patch" if score >= 60.0 else "rejected",
            critique=critique,
            mode="mode_a",
            candidate_index=new_candidate,
            suggested_shift_seconds=suggested_shift,
        )

    def _compute_mode_b_score(self, text: str) -> float:
        """Deterministic sub-40ms heuristic hook scoring."""
        if not text:
            return 30.0

        lower = text.lower()
        score = 65.0  # baseline

        # Penalize passive openers
        for weak in PASSIVE_WEAK_PHRASES:
            if lower.startswith(weak) or weak in lower[:30]:
                score -= 30.0
                break

        # Reward trigger words
        words = set(re.findall(r"[a-z]+", lower))
        triggers_found = words.intersection(HIGH_VIRAL_TRIGGERS)
        score += min(25.0, len(triggers_found) * 8.0)

        # Reward curiosity punctuation
        if "?" in text:
            score += 6.0
        if ":" in text or "—" in text:
            score += 4.0
        if any(w in lower for w in ["why", "how", "what", "if you"]):
            score += 8.0

        # Word count sweet spot (7 to 14 words)
        w_len = len(text.split())
        if 7 <= w_len <= 14:
            score += 8.0
        elif w_len > 22 or w_len < 4:
            score -= 12.0

        return max(10.0, min(98.0, round(score, 1)))

    def _compute_mode_a_score(self, transcript: str, duration: float) -> float:
        """Score transcript slice on duration, completeness, and initial energy."""
        if not transcript:
            return 20.0

        score = 70.0
        # Duration safety (< 59.5s)
        if duration > 59.5:
            score -= 25.0
        elif 30.0 <= duration <= 50.0:
            score += 10.0

        # Check ending completeness: does it end with punctuation?
        stripped = transcript.strip()
        if stripped and stripped[-1] in ".!?\"'":
            score += 10.0
        else:
            score -= 15.0

        # Check for weak trailing words
        last_words = stripped.lower().split()[-3:] if stripped else []
        if any(w in last_words for w in ["and", "or", "because", "but", "so"]):
            score -= 15.0

        # Check opening punch
        first_few = " ".join(stripped.split()[:8]).lower()
        if any(trigger in first_few for trigger in HIGH_VIRAL_TRIGGERS):
            score += 10.0

        return max(15.0, min(95.0, round(score, 1)))

    def _fast_patch_hook(self, hook_text: str) -> str:
        """Inject a high-retention pattern interrupt rewrite into a 70-79 tier hook."""
        clean = hook_text.strip()
        # Clean existing leading polite filler
        for weak in PASSIVE_WEAK_PHRASES:
            if clean.lower().startswith(weak):
                clean = clean[len(weak) :].lstrip(" ,:-")
                break

        if not clean:
            return "Stop scrolling: What nobody tells you about this will shock you."

        # Uppercase first letter
        clean = clean[0].upper() + clean[1:] if len(clean) > 1 else clean.upper()

        lower = clean.lower()
        has_curiosity = any(
            k in lower for k in ("secret", "truth", "why", "shock", "never", "nobody", "mistake", "warning", "trap")
        )

        if not has_curiosity:
            if any(term in lower for term in ("safe", "easy", "simple", "best", "common", "regular")):
                # Counter-intuitive truth pattern interrupt
                clean = f"The counter-intuitive truth about why {clean[:1].lower() + clean[1:]}"
            else:
                # Curiosity gap / high-stakes pattern interrupt
                clean = f"What 99% get completely wrong: {clean}"

        # Prepend high-urgency pattern interrupt prefix
        return f"Stop scrolling: {clean}"
