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


def test_decider_heatmap_and_hook_score_fusion_and_title_variants():
    """Verify multi-signal fusion (0.72 * semif + 0.18 * heatmap + 0.10 * hook) and 3 title_variants."""
    from autoshorts.core.models import CandidateClip, SelectedClip
    from autoshorts.decider.semif_decider import SemIfDecider as DeciderCls

    provider = MockProvider(
        scores_to_return={"A": 0.80, "B": 0.20},
        completion_text="Brain Plasticity Secret #Shorts",
    )
    decider = DeciderCls(provider=provider, scoring_approach="binary")

    candidates = [
        CandidateClip(
            start=0.0,
            end=45.0,
            text="Standard segment without heatmap spike.",
            heatmap_score=0.0,
            hook_strength=0.0,
        ),
        CandidateClip(
            start=60.0,
            end=105.0,
            text="Why do 99% of people miss this secret?",
            heatmap_score=1.0,
            hook_strength=0.90,
            chapter_title="Brain Secret",
        ),
    ]

    scored = decider.score_candidates(candidates)
    assert len(scored) == 2

    # Candidate with heatmap=1.0, hook=0.90, semif=0.80 -> 0.72*0.80 + 0.18*1.0 + 0.10*0.90 = 0.846
    top = scored[0]
    assert top.start == 60.0
    assert top.score == pytest.approx(0.72 * 0.80 + 0.18 * 1.0 + 0.10 * 0.90, 0.0001)
    assert top.chapter_title == "Brain Secret"

    # Candidate with heatmap=0.0, hook=0.0 -> exact semif_score = 0.80
    second = scored[1]
    assert second.start == 0.0
    assert second.score == 0.80

    selections = decider.select_clips(scored, count=1)
    assert len(selections) == 1
    sel = selections[0]
    assert isinstance(sel, SelectedClip)
    assert len(sel.title_variants) == 3
    assert len(set(sel.title_variants)) == 3
    assert all(v.endswith("#Shorts") for v in sel.title_variants)


# -----------------------------------------------------------------------------
# 8. Advanced Semantic Hook & Narrative Completeness Tests
# -----------------------------------------------------------------------------


def test_classify_semantic_hook_categories():
    """Verify semantic hook classifier detects curiosity gaps, contrarian statements, emotional stakes, surprising data, and open loops."""
    from autoshorts.decision.window_slicer import classify_semantic_hook

    # Curiosity gap
    res_curiosity = classify_semantic_hook("Here's why nobody tells you the real reason it works.")
    assert res_curiosity["is_hook"] is True
    assert res_curiosity["primary_category"] == "curiosity_gap"
    assert res_curiosity["score"] > 0.50

    # Contrarian statement
    res_contrarian = classify_semantic_hook("Stop doing this exercise because you're doing it wrong!")
    assert res_contrarian["is_hook"] is True
    assert res_contrarian["primary_category"] == "contrarian"
    assert any("contrarian" in sig for sig in res_contrarian["detected_signals"])

    # Emotional stakes
    res_stakes = classify_semantic_hook("This terrifying nightmare ruined my life forever.")
    assert res_stakes["is_hook"] is True
    assert res_stakes["primary_category"] == "emotional_stakes"
    assert res_stakes["score"] > 0.40

    # Surprising data points
    res_data = classify_semantic_hook("99% of people quit in the first 24 hours.")
    assert res_data["is_hook"] is True
    assert "surprising_data" in res_data["detected_signals"]

    # Narrative open loops
    res_loop = classify_semantic_hook("Before you scroll, wait until the end to see the catch.")
    assert res_loop["is_hook"] is True
    assert res_loop["primary_category"] == "narrative_open_loop"

    # Weak statement
    res_weak = classify_semantic_hook("Today we will read the standard corporate document.")
    assert res_weak["is_hook"] is False
    assert res_weak["score"] < 0.30


def test_sentence_and_clause_boundary_snapping_clean():
    """Verify candidate windows strictly start on sentence or clause boundaries rather than mid-sentence."""
    transcript = [
        {"start": 0.0, "end": 4.0, "text": "This is an introductory thought."},
        {"start": 4.1, "end": 7.0, "text": "and then we continued speaking"},  # Lowercase continuation
        {"start": 7.1, "end": 12.0, "text": "Wait until you see this shocking result!"},
        {"start": 12.1, "end": 20.0, "text": "Most people think productivity requires 12 hours a day."},
        {"start": 20.1, "end": 35.0, "text": "In reality, 4 hours of deep focus produces 10x more output."},
        {"start": 35.1, "end": 42.0, "text": "This is the single greatest career secret."},
        {"start": 42.1, "end": 50.0, "text": "Start applying it immediately and see the difference."},
    ]

    windows = slice_transcript_windows(
        transcript,
        window_duration_seconds=40.0,
        stride_seconds=15.0,
        min_duration_seconds=25.0,
        max_duration_seconds=50.0,
    )
    assert len(windows) >= 1
    for w in windows:
        # Candidate text should end on terminal punctuation
        assert w.text[-1] in {".", "!", "?"}
        # Candidate text should not start with an un-capitalized lowercase continuation word
        first_word = w.text.split()[0]
        assert first_word[0].isupper()


def test_cluster_heatmap_spikes():
    """Verify YouTube Most Replayed spike clustering identifies peak velocity regions."""
    from autoshorts.core.models import HeatmapPoint
    from autoshorts.decision.window_slicer import cluster_heatmap_spikes

    heatmap = [
        HeatmapPoint(start_time=0.0, end_time=10.0, value=0.10),
        HeatmapPoint(start_time=10.0, end_time=20.0, value=0.12),
        HeatmapPoint(start_time=20.0, end_time=25.0, value=0.30),
        HeatmapPoint(start_time=25.0, end_time=30.0, value=0.88),  # Sharp spike
        HeatmapPoint(start_time=30.0, end_time=35.0, value=0.92),  # Peak
        HeatmapPoint(start_time=35.0, end_time=45.0, value=0.20),
        HeatmapPoint(start_time=45.0, end_time=60.0, value=0.15),
    ]

    clusters = cluster_heatmap_spikes(heatmap, min_spike_val=0.35)
    assert len(clusters) >= 1
    main_cluster = clusters[0]
    assert 20.0 <= main_cluster["start"] <= 25.0
    assert 30.0 <= main_cluster["end"] <= 36.0
    assert main_cluster["peak_value"] >= 0.88
    assert main_cluster["velocity"] > 0.05


def test_narrative_completeness_evaluation_and_penalty():
    """Verify narrative completeness score flags cutoffs and applies appropriate penalty."""
    from autoshorts.decision.orchestrator import _fuse_candidate_score
    from autoshorts.decision.window_slicer import compute_narrative_completeness_score

    # 1. Complete resolution
    complete_text = "Scientists discovered a simple hack that changes everything. It works every time."
    score_comp = compute_narrative_completeness_score(complete_text)
    assert score_comp == 1.0

    # 2. Cutoff with trailing conjunction
    dangling_conj = "Scientists discovered a simple hack that changes everything, and then"
    score_conj = compute_narrative_completeness_score(dangling_conj)
    assert score_conj < 0.65

    # 3. Cutoff with ellipsis
    trailing_ellipsis = "If you want to know what happened next..."
    score_ellip = compute_narrative_completeness_score(trailing_ellipsis)
    assert score_ellip < 0.70

    # 4. Cutoff with dangling preposition
    dangling_prep = "This is the entire reason why we wanted to go to"
    score_prep = compute_narrative_completeness_score(dangling_prep)
    assert score_prep < 0.65

    # 5. Score penalty in _fuse_candidate_score
    fused_complete = _fuse_candidate_score(0.80, 0.50, 0.60, narrative_completeness=1.0)
    fused_cutoff = _fuse_candidate_score(0.80, 0.50, 0.60, narrative_completeness=0.50)
    assert fused_cutoff < fused_complete
    assert (fused_complete - fused_cutoff) >= 0.05

