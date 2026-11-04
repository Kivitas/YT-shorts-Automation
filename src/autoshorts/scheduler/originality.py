"""Originality guardrails: synthetic-content disclosure and repetition checks.

YouTube evaluates whole channels for "inauthentic" (mass-produced, templated) content and
requires an "altered or synthetic content" label for realistic AI media. These helpers are
pure functions so the scheduler and upload stages can share them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_HASHTAG_RE = re.compile(r"#\w+")
_WORD_RE = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "it", "this", "that",
     "shorts", "short"}
)

# Diagnostics keys a pipeline stage can set when realistic generated media was used.
SYNTHETIC_DIAGNOSTIC_KEYS = (
    "synthetic_media",
    "ai_generated_footage",
    "ai_voice_clone",
    "realistic_synthetic_voice",
)


def _tokens(text: str) -> frozenset[str]:
    cleaned = _HASHTAG_RE.sub(" ", (text or "").lower())
    return frozenset(w for w in _WORD_RE.findall(cleaned) if w not in _STOPWORDS)


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity of the content words of two titles (0.0-1.0)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_similar_title(
    title: str, recent_titles: Iterable[str], threshold: float
) -> tuple[str, float] | None:
    """Return the most similar recent title at or above ``threshold``, else None."""
    best: tuple[str, float] | None = None
    for other in recent_titles:
        score = title_similarity(title, other)
        if score >= threshold and (best is None or score > best[1]):
            best = (other, score)
    return best


def resolve_altered_content(job: Any, upload_cfg: Any) -> bool:
    """Decide the ``containsSyntheticMedia`` flag for an upload.

    Modes (``upload.synthetic_disclosure``):
      * ``always``: always disclose.
      * ``never``: only an explicit job/config opt-in discloses.
      * ``auto`` (default): disclose on explicit opt-in or when the job's diagnostics record
        realistic synthetic media.
    """
    mode = getattr(upload_cfg, "synthetic_disclosure", "auto")
    if mode == "always":
        return True
    explicit = bool(getattr(job, "altered_content", False)) or bool(
        getattr(upload_cfg, "altered_content", False)
    )
    if explicit or mode == "never":
        return explicit
    diagnostics = getattr(job, "diagnostics", None) or {}
    return any(bool(diagnostics.get(k)) for k in SYNTHETIC_DIAGNOSTIC_KEYS)
