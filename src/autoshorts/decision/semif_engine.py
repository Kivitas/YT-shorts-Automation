"""SemIf (Semantic Inference via Logit Scoring) mathematical and orchestration engine.

Provides:
- Structured SemIf prompt builders (State / Question / Allowed Options).
- Numerically stable softmax probability calculation.
- Rule-based heuristic fallback evaluator (WPM, questions, exclamations, hook keywords).
- Semantic-IF action table mapping discrete categorical choices to pipeline state transitions per PLAN.md 5.3.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Mapping

logger = logging.getLogger("autoshorts.decision.semif")

# High-curiosity / retention keywords for rule-based heuristic scoring
HOOK_KEYWORDS = {
    "secret", "never", "why", "how", "must", "truth", "crazy", "warning",
    "revealed", "insane", "stop", "best", "worst", "mistake", "always",
    "hidden", "shocking", "finally", "nobody", "everyone", "weird",
    "impossible", "hack", "trick", "discover", "proof", "exposed",
}


def build_semif_prompt(
    question: str,
    options: Mapping[str, str],
    context: str | Mapping[str, Any] | None = None,
) -> str:
    """Build a structured SemIf classification prompt.

    Enforces clear separation between State/Context, Question, and discrete Options.
    """
    lines: list[str] = []

    if context:
        lines.append("[CONTEXT / STATE]")
        if isinstance(context, Mapping):
            for k, v in context.items():
                lines.append(f"{k}: {v}")
        else:
            lines.append(str(context).strip())
        lines.append("")

    lines.append("[QUESTION]")
    lines.append(question.strip())
    lines.append("")

    lines.append("[ALLOWED OPTIONS]")
    for key, desc in sorted(options.items()):
        lines.append(f"{key}: {desc}")
    lines.append("")

    allowed_str = ", ".join(sorted(options.keys()))
    lines.append(f"Respond strictly with the single uppercase letter ({allowed_str}) corresponding to the best choice:")
    return "\n".join(lines)


def compute_softmax(logits: Mapping[str, float]) -> dict[str, float]:
    """Compute numerically stable Softmax over unnormalized logit values.

    P(i) = exp(z_i - max(z)) / sum(exp(z_j - max(z)))
    """
    if not logits:
        return {}

    keys = list(logits.keys())
    values = [float(logits[k]) for k in keys]

    valid_values = [v for v in values if math.isfinite(v)]
    if not valid_values:
        uniform = 1.0 / len(keys)
        return {k: uniform for k in keys}

    max_val = max(valid_values)
    exp_vals = [math.exp(v - max_val) if math.isfinite(v) else 0.0 for v in values]
    sum_exp = sum(exp_vals)

    if sum_exp <= 0.0 or not math.isfinite(sum_exp):
        uniform = 1.0 / len(keys)
        return {k: uniform for k in keys}

    return {k: exp_v / sum_exp for k, exp_v in zip(keys, exp_vals)}


def evaluate_heuristic(text: str, duration_sec: float | None = None) -> float:
    """Rule-based heuristic fallback evaluator when no LLM provider is available.

    Scores candidate text based on:
    1. Words Per Minute (WPM): Optimal engagement pace is 130-175 WPM.
    2. Question density: Curiosity indicator ('?').
    3. Exclamation density: Energy and emphasis indicator ('!').
    4. Hook and viral keyword frequency.

    Returns:
        Float score in range [0.0, 1.0].
    """
    cleaned = text.strip()
    if not cleaned:
        return 0.0

    words = cleaned.split()
    word_count = len(words)

    # 1. WPM calculation
    dur = duration_sec if duration_sec and duration_sec > 0.0 else max(5.0, word_count / 2.5)
    wpm = (word_count / dur) * 60.0

    # Engagement speaking rate between 100 and 200 WPM
    if 100.0 <= wpm <= 200.0:
        wpm_score = 1.0
    elif wpm < 100.0:
        wpm_score = max(0.5, wpm / 100.0)
    else:
        wpm_score = max(0.5, 1.0 - (wpm - 200.0) / 100.0)

    # 2. Questions count (curiosity / engagement hooks)
    question_count = cleaned.count("?")
    question_score = min(1.0, question_count * 0.5)

    # 3. Exclamations count (energy / emotion)
    exclamation_count = cleaned.count("!")
    exclamation_score = min(1.0, exclamation_count * 0.5)

    # 4. Keyword frequency
    lower_words = {re.sub(r"[^a-z]", "", w.lower()) for w in words}
    keyword_matches = len(lower_words & HOOK_KEYWORDS)
    keyword_score = min(1.0, keyword_matches * 0.35)

    # Composite weighted score
    composite = (
        0.30 * wpm_score
        + 0.40 * keyword_score
        + 0.15 * question_score
        + 0.15 * exclamation_score
    )

    # Clamp to [0.05, 0.98] to maintain smooth probabilistic representation
    final_score = max(0.05, min(0.98, composite))
    return round(final_score, 4)


# Semantic-IF Action Table & State Machine per PLAN.md section 5.3
SEMIF_ACTION_TABLE: dict[str, dict[str, Any]] = {
    "WINDOW_SELECTION": {
        "question": "Does this segment represent a complete, viral standalone hook?",
        "options": {
            "A": "Accept window: High-retention, engaging standalone viral moment",
            "B": "Reject window: Low-retention, dull, or incomplete context",
        },
        "action": lambda choice, context=None: "accept" if choice == "A" else "reject",
    },
    "BACKGROUND_SELECTION": {
        "question": "Which background style best complements this topic?",
        "options": {
            "A": "High-speed Subway Surfers (action, gaming, fast-paced, funny)",
            "B": "Calm Minecraft Parkour (storytelling, deep insight, philosophy, relaxing)",
        },
        "action": lambda choice, context=None: "subway" if choice == "A" else "minecraft",
    },
    "HOOK_STYLE": {
        "question": "What hook visual matches the emotional tone?",
        "options": {
            "A": "Bold Question Card (curiosity, inquiry, mystery)",
            "B": "Shocking Keyword Highlight (fast reveal, energetic assertion)",
        },
        "action": lambda choice, context=None: "card" if choice == "A" else "keyword",
    },
    "UPLOAD_VERIFICATION_GATE": {
        "question": "Is this generated Short clear of rendering defects and within safe guidelines?",
        "options": {
            "A": "Approved for publish: Clear render, compliant, high quality",
            "B": "Flag for manual creator review: Ambiguous, quality warning, or border defect",
        },
        "action": lambda choice, context=None: "approved" if choice == "A" else "flag_review",
    },
}
