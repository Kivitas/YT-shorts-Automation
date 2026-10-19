"""SemIf Orchestrator and Decider implementation.

Implements autoshorts.core.contracts.Decider.
Provides binary (Approach A) and multi-candidate (Approach B) window scoring,
Semantic-IF action table execution, non-overlapping clip selection, heuristic fallback,
and generative metadata helpers.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Sequence

from autoshorts.core.config import Settings, load_config
from autoshorts.core.contracts import Decider, LLMProvider
from autoshorts.core.exceptions import DeciderError
from autoshorts.core.models import ClipCandidate, ClipSelection
from autoshorts.decision.semif_engine import (
    SEMIF_ACTION_TABLE,
    build_semif_prompt,
    evaluate_heuristic,
)
from autoshorts.decision.window_slicer import select_non_overlapping_candidates

logger = logging.getLogger("autoshorts.decision.orchestrator")


class SemIfDecider(Decider):
    """Primary pipeline decision orchestrator driven by SemIf logit evaluation.

    Complies with Decider protocol. Supports binary scoring, multi-candidate scoring,
    state machine action decisions, and fallback heuristics.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        settings: Settings | None = None,
        scoring_approach: str = "binary",  # "binary" (Approach A) or "multi" (Approach B)
    ) -> None:
        self.provider = provider
        self.settings = settings or load_config()
        self.scoring_approach = scoring_approach

    def score_candidates(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
    ) -> list[ClipCandidate]:
        """Evaluate and rank candidate transcript windows using SemIf Softmax probabilities.

        Dispatches to binary Approach A or multi-candidate Approach B.
        Falls back to rule-based heuristic scoring if no provider is configured.
        """
        if not candidates:
            return []

        if self.scoring_approach == "multi" and len(candidates) > 1:
            return self.score_candidates_multicandidate(candidates, instructions)
        return self.score_candidates_binary(candidates, instructions)

    def score_candidates_binary(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
    ) -> list[ClipCandidate]:
        """Approach A: Evaluate each candidate window independently using binary SemIf logits."""
        scored: list[ClipCandidate] = []
        user_prompt = instructions or self.settings.job_defaults.focus_prompt

        for cand in candidates:
            score = 0.0
            reason = "Heuristic fallback evaluation"

            if self.provider is not None:
                try:
                    context = {
                        "Candidate Window": f"[{cand.start:.1f}s - {cand.end:.1f}s] ({cand.duration:.1f}s)",
                        "Transcript Excerpt": cand.text,
                        "Operator Instructions": user_prompt,
                    }
                    question = "Does this excerpt represent a high-retention, engaging standalone viral moment?"
                    options = {
                        "A": "High retention, engaging standalone viral moment",
                        "B": "Low retention, dull, or incomplete context",
                    }
                    prompt = build_semif_prompt(question, options, context=context)
                    probs = self.provider.score_options(prompt, options)
                    score = float(probs.get("A", 0.0))
                    reason = f"SemIf binary score: P(Accept)={score:.3f}"
                except Exception as e:
                    logger.warning("Provider scoring failed for window [%.1f-%.1f]: %s", cand.start, cand.end, e)
                    score = evaluate_heuristic(cand.text, cand.duration)
                    reason = f"Heuristic fallback: {score:.3f}"
            else:
                score = evaluate_heuristic(cand.text, cand.duration)
                reason = f"Heuristic (no provider): {score:.3f}"

            scored.append(
                ClipCandidate(
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=round(score, 4),
                    reason=reason,
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def score_candidates_multicandidate(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
    ) -> list[ClipCandidate]:
        """Approach B: Joint categorical comparative evaluation across candidate windows."""
        user_prompt = instructions or self.settings.job_defaults.focus_prompt
        # Limit comparison batches to at most 5 at a time
        batch = list(candidates[:5])
        letters = [chr(ord("A") + i) for i in range(len(batch))]
        options = {
            letter: f"[{cand.start:.1f}s - {cand.end:.1f}s]: {cand.text[:90]}..."
            for letter, cand in zip(letters, batch)
        }

        context = {
            "Operator Instructions": user_prompt,
            "Total Candidates": len(batch),
        }
        question = "Which candidate transcript excerpt has the highest viral potential and strongest retention hook?"

        scores_by_letter: dict[str, float] = {}
        if self.provider is not None:
            try:
                prompt = build_semif_prompt(question, options, context=context)
                scores_by_letter = self.provider.score_options(prompt, options)
            except Exception as e:
                logger.warning("Multi-candidate SemIf scoring failed: %s", e)

        scored: list[ClipCandidate] = []
        for i, cand in enumerate(batch):
            let = letters[i]
            if let in scores_by_letter:
                s = scores_by_letter[let]
                reason = f"SemIf multicandidate score: {s:.3f}"
            else:
                s = evaluate_heuristic(cand.text, cand.duration)
                reason = f"Heuristic fallback: {s:.3f}"

            scored.append(
                ClipCandidate(
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=round(s, 4),
                    reason=reason,
                )
            )

        # Append remaining candidates evaluated via heuristic
        for cand in candidates[5:]:
            s = evaluate_heuristic(cand.text, cand.duration)
            scored.append(
                ClipCandidate(
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=round(s, 4),
                    reason="Heuristic fallback",
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def select_clips(
        self,
        candidates: Sequence[ClipCandidate],
        count: int = 1,
    ) -> list[ClipSelection]:
        """Select top non-overlapping candidate segments and attach styling parameters."""
        if not candidates or count <= 0:
            return []

        # If candidates are unscored (all 0.0), score them first
        if all(c.score == 0.0 for c in candidates):
            candidates = self.score_candidates(candidates)

        chosen_candidates = select_non_overlapping_candidates(candidates, count=count)
        selections: list[ClipSelection] = []

        for i, cand in enumerate(chosen_candidates):
            # 1. Decide background style
            bg_style = self.decide_action(
                "BACKGROUND_SELECTION",
                {"text": cand.text, "duration": cand.duration},
            )

            # 2. Generate hook text
            hook_text = self.generate_hook(cand.text)

            # 3. Generate title
            title = self.generate_title(cand.text)

            selections.append(
                ClipSelection(
                    candidate=cand,
                    clip_index=i,
                    target_duration=min(cand.duration, float(self.settings.job_defaults.max_duration_seconds)),
                    background_style=bg_style,
                    hook_text=hook_text,
                    title=title,
                )
            )

        return selections

    def decide_action(
        self,
        action_key: str,
        context: Mapping[str, Any],
    ) -> str:
        """Execute a Semantic-IF decision prompt and return the chosen action identifier.

        Maps chosen option letters to pipeline state machine transitions per PLAN.md 5.3.
        """
        table_entry = SEMIF_ACTION_TABLE.get(action_key)
        if not table_entry:
            raise DeciderError(f"Unknown Semantic-IF action key: '{action_key}'")

        question = table_entry["question"]
        options = table_entry["options"]
        action_fn = table_entry["action"]

        chosen_letter = "A"

        if self.provider is not None:
            try:
                prompt = build_semif_prompt(question, options, context=context)
                probs = self.provider.score_options(prompt, options)
                if probs:
                    chosen_letter = max(probs.items(), key=lambda item: item[1])[0]
            except Exception as e:
                logger.warning("Decider failed evaluating '%s': %s", action_key, e)
                chosen_letter = self._heuristic_action_fallback(action_key, context)
        else:
            chosen_letter = self._heuristic_action_fallback(action_key, context)

        # Execute action handler to map choice to pipeline string
        result_action = action_fn(chosen_letter, context)
        logger.info("Decider action '%s' -> choice '%s' => '%s'", action_key, chosen_letter, result_action)
        return str(result_action)

    def _heuristic_action_fallback(
        self,
        action_key: str,
        context: Mapping[str, Any],
    ) -> str:
        """Rule-based heuristic choice when no LLM provider is available."""
        if action_key == "BACKGROUND_SELECTION":
            text = str(context.get("text", "")).lower()
            # Fast gaming / humor keywords -> Subway Surfers (A), else Minecraft (B)
            gaming_keywords = {"game", "fast", "speed", "crazy", "funny", "laugh", "meme"}
            if any(k in text for k in gaming_keywords):
                return "A"
            return "B"

        elif action_key == "HOOK_STYLE":
            text = str(context.get("text", ""))
            return "A" if "?" in text else "B"

        elif action_key == "WINDOW_SELECTION":
            text = str(context.get("text", ""))
            dur = float(context.get("duration", 45.0))
            score = evaluate_heuristic(text, dur)
            return "A" if score >= 0.5 else "B"

        elif action_key == "UPLOAD_VERIFICATION_GATE":
            return "A"

        return "A"

    # -------------------------------------------------------------------------
    # Generative LLM Helpers per PLAN.md section 5.4
    # -------------------------------------------------------------------------

    def generate_title(self, transcript_text: str, channel: str | None = None) -> str:
        """Generate a punchy YouTube Shorts title under 60 characters ending with #Shorts."""
        if self.provider is not None:
            try:
                prompt = (
                    "You are a viral YouTube Shorts editor. "
                    "Write a punchy, highly engaging title under 50 characters for this excerpt. "
                    "End with '#Shorts'. Return ONLY the title text and nothing else.\n\n"
                    f"Excerpt: {transcript_text[:300]}"
                )
                title = self.provider.complete(prompt, max_tokens=32, temperature=0.3).strip()
                # Clean quotes and formatting
                title = title.replace('"', "").replace("'", "").strip()
                if not title.endswith("#Shorts"):
                    title = f"{title[:45]} #Shorts"
                if len(title) > 60:
                    title = f"{title[:50].rsplit(' ', 1)[0]} #Shorts"
                return title
            except Exception as e:
                logger.warning("Generative title failed: %s", e)

        # Rule-based fallback title
        words = transcript_text.strip().split()
        first_chunk = " ".join(words[:6])
        clean_chunk = re.sub(r"[^\w\s]", "", first_chunk).title()
        return f"{clean_chunk[:45]} #Shorts"

    def generate_hook(self, transcript_text: str) -> str:
        """Generate a bold 3-second hook question or headline."""
        if self.provider is not None:
            try:
                prompt = (
                    "Write a 1-sentence curiosity hook question (max 8 words) "
                    "for the first 3 seconds of a YouTube Short based on this excerpt. "
                    "Return ONLY the hook question.\n\n"
                    f"Excerpt: {transcript_text[:300]}"
                )
                hook = self.provider.complete(prompt, max_tokens=24, temperature=0.3).strip()
                hook = hook.replace('"', "").replace("'", "").strip()
                if hook:
                    return hook
            except Exception as e:
                logger.warning("Generative hook failed: %s", e)

        # Fallback hook: first sentence or question
        sentences = re.split(r"(?<=[.?!])\s+", transcript_text.strip())
        for s in sentences:
            if "?" in s:
                return s.strip()
        if sentences:
            words = sentences[0].split()
            return " ".join(words[:8])
        return "Watch Until The End!"

    def generate_description_hook(self, transcript_text: str) -> str:
        """Generate a 2-sentence curiosity-inducing summary."""
        if self.provider is not None:
            try:
                prompt = (
                    "Write a 2-sentence engaging summary hook for this video excerpt. "
                    "Return ONLY the 2 sentences.\n\n"
                    f"Excerpt: {transcript_text[:400]}"
                )
                desc = self.provider.complete(prompt, max_tokens=60, temperature=0.3).strip()
                if desc:
                    return desc
            except Exception as e:
                logger.warning("Generative description failed: %s", e)

        # Fallback: clean first 2 sentences
        sentences = re.split(r"(?<=[.?!])\s+", transcript_text.strip())
        return " ".join(s.strip() for s in sentences[:2] if s.strip())

    def generate_hashtags(self, transcript_text: str, count: int = 4) -> str:
        """Generate semantic hashtags for video description."""
        if self.provider is not None:
            try:
                prompt = (
                    f"Extract {count} trending YouTube Shorts hashtags for this content. "
                    "Include '#Shorts'. Separate tags by a single space. Return ONLY the hashtags.\n\n"
                    f"Content: {transcript_text[:300]}"
                )
                tags = self.provider.complete(prompt, max_tokens=30, temperature=0.3).strip()
                if tags and "#" in tags:
                    return tags
            except Exception as e:
                logger.warning("Generative hashtags failed: %s", e)

        return "#Shorts #Viral #Podcasts #Education"
