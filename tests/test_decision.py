"""Unit tests for Track A Decision Layer & SemIf Engine.

Covers:
- SemIf prompt formatting (State / Question / Allowed Options).
- Softmax over discrete option letters with numerical stability.
- Sliding window generator (50s window, 15s stride, sentence boundary snapping).
- Greedy non-overlapping candidate selection.
- Rule-based heuristic fallback evaluator (WPM, questions, exclamations, keywords).
- Semantic-IF action table & state machine per PLAN.md section 5.3.
- SemIfDecider: Approach A binary, Approach B multi-candidate, and clip selection.
- Generative metadata helpers (title, hook, description, hashtags).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from autoshorts.core.contracts import Decider, LLMProvider
from autoshorts.core.models import ClipCandidate, ClipSelection
from autoshorts.decision.orchestrator import SemIfDecider
from autoshorts.decision.semif_engine import (
    SEMIF_ACTION_TABLE,
    build_semif_prompt,
    compute_softmax,
    evaluate_heuristic,
)
from autoshorts.decision.window_slicer import (
    select_non_overlapping_candidates,
    slice_transcript_windows,
)

# -----------------------------------------------------------------------------
# 1. SemIf Prompt Builder & Softmax Tests
# -----------------------------------------------------------------------------


def test_semif_prompt_builder():
    """Verify SemIf prompt builder formats context, question, and options clearly."""
    question = "Does this segment represent a viral hook?"
    options = {"A": "Accept", "B": "Reject"}
    context = {"Speaker": "Alex", "Duration": "45.0s"}

    prompt = build_semif_prompt(question, options, context=context)

    assert "[CONTEXT / STATE]" in prompt
    assert "Speaker: Alex" in prompt
    assert "[QUESTION]" in prompt
    assert "Does this segment represent a viral hook?" in prompt
    assert "[ALLOWED OPTIONS]" in prompt
    assert "A: Accept" in prompt
    assert "B: Reject" in prompt
    assert "Respond strictly with the single uppercase letter (A, B)" in prompt


def test_semif_softmax_math():
    """Verify softmax calculation correctness and sum to 1.0."""
    logits = {"A": 2.5, "B": 1.0, "C": -0.5}
    probs = compute_softmax(logits)

    assert pytest.approx(sum(probs.values()), 0.0001) == 1.0
    assert probs["A"] > probs["B"] > probs["C"]

    # Test empty dict
    assert compute_softmax({}) == {}


# -----------------------------------------------------------------------------
# 2. Sliding Window Slicer & Sentence Snapping
# -----------------------------------------------------------------------------


def test_window_slicer_with_sentence_snapping():
    """Verify window slicer respects 50s target, 15s stride, and snaps to sentence ends."""
    # 3-minute mock transcript with distinct sentences
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "This is the very first sentence."},
        {"start": 10.0, "end": 25.0, "text": "Here is the second thought that is quite long."},
        {"start": 25.0, "end": 48.0, "text": "And here comes the punchline of the first topic!"},
        {"start": 48.0, "end": 65.0, "text": "Now we move on to the second topic."},
        {"start": 65.0, "end": 85.0, "text": "Is this working properly as expected?"},
        {"start": 85.0, "end": 110.0, "text": "Yes, absolutely it is working."},
        {"start": 110.0, "end": 140.0, "text": "Let us conclude with final remarks."},
    ]

    windows = slice_transcript_windows(
        transcript,
        window_duration_seconds=50.0,
        stride_seconds=15.0,
        min_duration_seconds=30.0,
        max_duration_seconds=60.0,
    )

    assert len(windows) >= 2
    for w in windows:
        assert isinstance(w, ClipCandidate)
        # Verify duration constraint
        assert 30.0 <= w.duration <= 60.5
        # Verify snapped boundary ends with punctuation
        assert w.text[-1] in {".", "!", "?"}


def test_window_slicer_from_raw_text():
    """Verify window slicer parses raw text strings without explicit timestamps."""
    raw_text = (
        "Did you know that human memory works by association? "
        "Scientists discovered that recalling information strengthens synaptic connections. "
        "This means practicing retrieval is far more effective than passive re-reading. "
        "If you want to remember something forever, test yourself on it immediately. "
        "That is the single most powerful study secret known to neuroscience."
    )
    windows = slice_transcript_windows(
        raw_text,
        window_duration_seconds=40.0,
        stride_seconds=15.0,
        min_duration_seconds=20.0,
        max_duration_seconds=60.0,
    )
    assert len(windows) >= 1
    assert all(w.text for w in windows)


def test_select_non_overlapping_candidates():
    """Verify greedy selection of top non-overlapping candidates."""
    candidates = [
        ClipCandidate(start=0.0, end=45.0, text="Win 1", score=0.90),
        ClipCandidate(start=15.0, end=60.0, text="Win 2 (overlaps 1)", score=0.95),  # Highest score
        ClipCandidate(start=70.0, end=115.0, text="Win 3 (no overlap)", score=0.80),
        ClipCandidate(start=80.0, end=125.0, text="Win 4 (overlaps 3)", score=0.75),
    ]

    # Select top 2 non-overlapping
    chosen = select_non_overlapping_candidates(candidates, count=2)
    assert len(chosen) == 2
    # Win 2 (0.95) selected first; Win 1 overlaps it so skipped; Win 3 (0.80) selected next.
    # Chronologically ordered: Win 2 (15.0-60.0) then Win 3 (70.0-115.0)
    assert chosen[0].start == 15.0
    assert chosen[1].start == 70.0


# -----------------------------------------------------------------------------
# 3. Rule-Based Heuristic Fallback Evaluator
# -----------------------------------------------------------------------------


def test_heuristic_fallback_evaluator():
    """Verify heuristic scores text based on WPM, question marks, and hook keywords."""
    # High-engagement text with questions, exclamations, and keywords
    high_engagement = (
        "Why does everyone make this crazy mistake? "
        "The shocking truth was finally revealed! "
        "Never ignore this secret rule if you want to succeed."
    )
    score_high = evaluate_heuristic(high_engagement, duration_sec=40.0)

    # Low-engagement dull text
    low_engagement = "Today we will proceed to discuss the standard agenda items as scheduled."
    score_low = evaluate_heuristic(low_engagement, duration_sec=40.0)

    assert 0.05 <= score_low < score_high <= 0.98
    assert score_high > 0.60
    assert score_low < 0.50


# -----------------------------------------------------------------------------
# 4. Semantic-IF Action Table & State Machine
# -----------------------------------------------------------------------------


def test_semif_action_table_mappings():
    """Verify all actions in SEMIF_ACTION_TABLE map correctly per PLAN.md section 5.3."""
    # WINDOW_SELECTION: A -> accept, B -> reject
    win_table = SEMIF_ACTION_TABLE["WINDOW_SELECTION"]
    assert win_table["action"]("A") == "accept"
    assert win_table["action"]("B") == "reject"

    # BACKGROUND_SELECTION: A -> subway, B -> minecraft
    bg_table = SEMIF_ACTION_TABLE["BACKGROUND_SELECTION"]
    assert bg_table["action"]("A") == "subway"
    assert bg_table["action"]("B") == "minecraft"

    # HOOK_STYLE: A -> card, B -> keyword
    hook_table = SEMIF_ACTION_TABLE["HOOK_STYLE"]
    assert hook_table["action"]("A") == "card"
    assert hook_table["action"]("B") == "keyword"

    # UPLOAD_VERIFICATION_GATE: A -> approved, B -> flag_review
    upload_table = SEMIF_ACTION_TABLE["UPLOAD_VERIFICATION_GATE"]
    assert upload_table["action"]("A") == "approved"
    assert upload_table["action"]("B") == "flag_review"


# -----------------------------------------------------------------------------
# 5. SemIfDecider Protocol & Orchestration
# -----------------------------------------------------------------------------


class MockProvider(LLMProvider):
    """Mock LLMProvider implementing contracts.LLMProvider for decider testing."""

    def __init__(self, scores_to_return: dict[str, float] | None = None, completion_text: str = ""):
        self.scores_to_return = scores_to_return or {"A": 0.88, "B": 0.12}
        self.completion_text = completion_text or "Mock Result #Shorts"

    @property
    def name(self) -> str:
        return "mock"

    @property
    def supports_logprobs(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["mock-model-v1"]

    def complete(self, prompt: str, **kwargs: Any) -> str:
        return self.completion_text

    def score_options(
        self,
        prompt: str,
        options: Sequence[str] | Mapping[str, str],
        **kwargs: Any,
    ) -> dict[str, float]:
        return self.scores_to_return


def test_decider_protocol_compliance():
    """Verify SemIfDecider complies with Decider protocol."""
    decider = SemIfDecider()
    assert isinstance(decider, Decider)


def test_decider_scoring_approach_a_binary():
    """Verify Decider Approach A binary scoring using provider."""
    provider = MockProvider(scores_to_return={"A": 0.85, "B": 0.15})
    decider = SemIfDecider(provider=provider, scoring_approach="binary")

    candidates = [
        ClipCandidate(start=0.0, end=45.0, text="First candidate text"),
        ClipCandidate(start=60.0, end=105.0, text="Second candidate text"),
    ]

    scored = decider.score_candidates(candidates)
    assert len(scored) == 2
    assert scored[0].score == 0.85
    assert "SemIf binary score" in scored[0].reason


def test_decider_scoring_approach_b_multi():
    """Verify Decider Approach B multi-candidate comparative scoring."""
    provider = MockProvider(scores_to_return={"A": 0.70, "B": 0.30})
    decider = SemIfDecider(provider=provider, scoring_approach="multi")

    candidates = [
        ClipCandidate(start=0.0, end=45.0, text="Candidate A"),
        ClipCandidate(start=50.0, end=95.0, text="Candidate B"),
    ]

    scored = decider.score_candidates(candidates)
    assert len(scored) == 2
    assert scored[0].score == 0.70
    assert scored[1].score == 0.30


def test_decider_scoring_heuristic_fallback_when_no_provider():
    """Verify Decider falls back cleanly to heuristic scoring when provider is None."""
    decider = SemIfDecider(provider=None)
    candidates = [
        ClipCandidate(start=0.0, end=45.0, text="Why is this shocking truth never spoken?"),
    ]
    scored = decider.score_candidates(candidates)
    assert len(scored) == 1
    assert scored[0].score > 0.0
    assert "Heuristic (no provider)" in scored[0].reason


def test_decider_action_decision():
    """Verify decider executes Semantic-IF action table decisions."""
    # Choice B -> Minecraft
    provider = MockProvider(scores_to_return={"A": 0.20, "B": 0.80})
    decider = SemIfDecider(provider=provider)

    bg_action = decider.decide_action(
        "BACKGROUND_SELECTION",
        {"text": "A deep thought on philosophy and mindfulness."},
    )
    assert bg_action == "minecraft"

    # Choice A -> Approved
    provider.scores_to_return = {"A": 0.95, "B": 0.05}
    gate_action = decider.decide_action("UPLOAD_VERIFICATION_GATE", {})
    assert gate_action == "approved"


def test_decider_select_clips_workflow():
    """Verify full select_clips pipeline builds valid ClipSelection models."""
    provider = MockProvider(
        scores_to_return={"A": 0.90, "B": 0.10},
        completion_text="The Secret Life of Brains #Shorts",
    )
    decider = SemIfDecider(provider=provider)

    candidates = [
        ClipCandidate(
            start=0.0, end=45.0, text="How does human brain plasticity work?", score=0.90
        ),
        ClipCandidate(start=100.0, end=145.0, text="Second distinct segment.", score=0.70),
    ]

    selections = decider.select_clips(candidates, count=1)
    assert len(selections) == 1
    sel = selections[0]
    assert isinstance(sel, ClipSelection)
    assert sel.clip_index == 0
    assert sel.target_duration == 45.0
    assert sel.background_style in {"subway", "minecraft"}
    assert sel.hook_text != ""
    assert "#Shorts" in sel.title


# -----------------------------------------------------------------------------
# 6. Generative LLM Helpers
# -----------------------------------------------------------------------------


def test_generative_helpers():
    """Verify title, hook, description, and hashtags generative helpers."""
    provider = MockProvider(completion_text="Mindset Shift #Shorts")
    decider = SemIfDecider(provider=provider)

    title = decider.generate_title("Why changing your environment alters your habits.")
    assert title.endswith("#Shorts")

    hook = decider.generate_hook("Are you struggling to break bad habits?")
    assert len(hook) > 0

    desc = decider.generate_description_hook("Learn the neuroscience behind habit formation.")
    assert len(desc) > 0

    provider.completion_text = "#Shorts #Mindset #Productivity"
    tags = decider.generate_hashtags("Mindset habits")
    assert "#Shorts" in tags
