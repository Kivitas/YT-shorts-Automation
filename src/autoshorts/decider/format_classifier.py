"""Clip format classifier: "watching" vs "ranking".

Decides how a clip should be edited:
- "watching": the viewer just watches the content (no ranked list). Keeps the
  gameplay-background (subway / minecraft) split layout.
- "ranking": the clip counts 3-10 items (target 5-6) from last to first, e.g.
  "number 5 ... number 1", "worst to best", "top 5". Routed to the ranking edit
  (captions plus animated rank cards, no gameplay panel).

Pure, deterministic and dependency-free. Rank numbers are extracted from explicit
cues only, and a clip is "ranking" only with >= 3 distinct ranks in a mostly
descending (countdown) order and a rank_count within 3..10.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

FormatKind = Literal["watching", "ranking"]

MIN_RANKS = 3
MAX_RANKS = 10
_DESC_FRACTION = 0.6

_NUM_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip
_ORD_WORDS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}  # fmt: skip

_NUM = r"(?:10|[1-9]|" + "|".join(_NUM_WORDS) + r")"
_ORD = "|".join(_ORD_WORDS)

# Each pattern captures the rank in group "n" (digit/word) or "o" (ordinal word).
_RANK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\b(?:at\s+)?(?:number|no\.?|num)\s*(?P<n>{_NUM})\b", re.IGNORECASE),
    re.compile(rf"#\s*(?P<n>{_NUM})\b", re.IGNORECASE),
    re.compile(rf"\bcoming\s+in\s+at\s+(?:number\s+)?(?P<n>{_NUM})\b", re.IGNORECASE),
    re.compile(rf"\b(?P<o>{_ORD})\s+place\b", re.IGNORECASE),
    re.compile(r"(?:^|[\n.!?]\s*)(?P<n>10|[1-9])\.\s+(?=\S)", re.MULTILINE),
]
_TOP_RE = re.compile(rf"\btop\s+(?P<n>{_NUM})\b", re.IGNORECASE)
_LAST_PLACE_RE = re.compile(r"\blast\s+place\b", re.IGNORECASE)

_CUE_RES: list[tuple[str, re.Pattern[str]]] = [
    ("ranked", re.compile(r"\branked?\b|\branking\b", re.IGNORECASE)),
    ("worst_to_best", re.compile(r"\bworst\s+to\s+best\b|\blast\s+to\s+first\b", re.IGNORECASE)),
    ("best_to_worst", re.compile(r"\bbest\s+to\s+worst\b|\bfirst\s+to\s+last\b", re.IGNORECASE)),
    ("countdown", re.compile(r"\bcount(?:ing)?\s*down\b", re.IGNORECASE)),
    ("last_place", _LAST_PLACE_RE),
    ("coming_in_at", re.compile(r"\bcoming\s+in\s+at\b", re.IGNORECASE)),
    ("at_number", re.compile(r"\bat\s+number\b", re.IGNORECASE)),
]


@dataclass
class FormatDecision:
    """Result of format classification."""

    kind: FormatKind = "watching"
    confidence: float = 0.5
    rank_count: int | None = None
    reasons: list[str] = field(default_factory=list)


def _to_int(token: str) -> int:
    t = token.lower()
    return int(t) if t.isdigit() else _NUM_WORDS[t]


def _extract_ranks(text: str) -> list[int]:
    """Rank numbers in order of appearance (with repeats)."""
    hits: list[tuple[int, int]] = []
    for pat in _RANK_PATTERNS:
        for m in pat.finditer(text):
            gd = m.groupdict()
            if gd.get("n"):
                hits.append((m.start(), _to_int(gd["n"])))
            elif gd.get("o"):
                hits.append((m.start(), _ORD_WORDS[gd["o"].lower()]))
    hits.sort()
    ranks: list[int] = []
    last_pos = -10
    for pos, val in hits:
        # Drop overlapping duplicate matches of the same mention.
        if ranks and pos - last_pos < 3 and ranks[-1] == val:
            continue
        ranks.append(val)
        last_pos = pos
    return ranks


def _descending_fraction(seq: list[int]) -> float:
    """Fraction of consecutive changes that go down (ignoring repeats)."""
    dedup = [v for i, v in enumerate(seq) if i == 0 or v != seq[i - 1]]
    if len(dedup) < 2:
        return 0.0
    downs = sum(1 for a, b in itertools.pairwise(dedup) if b < a)
    return downs / (len(dedup) - 1)


def _longest_descending_run(seq: list[int]) -> int:
    """Length of the longest strictly descending consecutive run (repeats collapsed)."""
    dedup = [v for i, v in enumerate(seq) if i == 0 or v != seq[i - 1]]
    best = cur = 1 if dedup else 0
    for a, b in itertools.pairwise(dedup):
        cur = cur + 1 if b < a else 1
        best = max(best, cur)
    return best


def classify_format(
    text: str,
    duration_sec: float,
    words: list[dict[str, Any]] | None = None,
) -> FormatDecision:
    """Classify a clip as "watching" or "ranking".

    Args:
        text: Clip transcript.
        duration_sec: Clip length in seconds (very short clips cannot hold a list).
        words: Optional word dicts (``{"word"|"text": str, ...}``); used as the
            transcript when ``text`` is empty.
    """
    src = text or ""
    if not src.strip() and words:
        src = " ".join(str(w.get("word") or w.get("text") or "") for w in words)
    src = src.strip()
    reasons: list[str] = []
    if not src:
        return FormatDecision("watching", 0.9, None, ["empty transcript"])

    ranks = _extract_ranks(src)
    distinct = sorted(set(ranks))
    cues = [name for name, pat in _CUE_RES if pat.search(src)]
    top_m = _TOP_RE.search(src)
    top_n = _to_int(top_m.group("n")) if top_m else None
    if top_n:
        cues.append(f"top_{top_n}")

    if len(distinct) < MIN_RANKS:
        reasons.append(f"only {len(distinct)} distinct rank(s) mentioned (need >= {MIN_RANKS})")
        if cues:
            reasons.append("cues without enough ranks: " + ", ".join(cues))
        return FormatDecision("watching", 0.85 if not cues else 0.7, None, reasons)

    desc = _descending_fraction(ranks)
    run = _longest_descending_run(ranks)
    reasons.append(f"ranks mentioned in order: {ranks}")
    reasons.append(f"longest descending run {run}, descending fraction {desc:.2f}")
    if desc < _DESC_FRACTION:
        reasons.append(f"not a countdown pattern (descending fraction {desc:.2f})")
        return FormatDecision("watching", 0.7, None, reasons)
    if not cues and not (run >= 3 and desc >= 0.8):
        reasons.append("no ranking cue phrase and no clear countdown run")
        return FormatDecision("watching", 0.65, None, reasons)
    if cues and run < 3 and len(distinct) < 4:
        reasons.append("cue phrase present but no descending run of 3")
        return FormatDecision("watching", 0.65, None, reasons)

    rank_count = max(len(distinct), top_n or 0)
    if not (MIN_RANKS <= rank_count <= MAX_RANKS):
        reasons.append(f"rank_count {rank_count} outside {MIN_RANKS}..{MAX_RANKS}")
        return FormatDecision("watching", 0.7, None, reasons)

    conf = 0.55 + 0.07 * min(len(distinct), 6) + 0.05 * min(len(cues), 3)
    conf += 0.1 * (desc - _DESC_FRACTION)
    if 5 <= rank_count <= 6:
        conf += 0.05
    if run >= 4:
        conf += 0.05
    if not cues:
        reasons.append("countdown inferred from rank sequence alone")
    if duration_sec and duration_sec < 12:
        conf -= 0.15
        reasons.append("clip is very short for a ranked list")
    conf = round(max(0.0, min(0.99, conf)), 2)
    reasons.append(f"{len(distinct)} distinct ranks")
    if cues:
        reasons.append("cues: " + ", ".join(cues))
    decision = FormatDecision("ranking", conf, rank_count, reasons)
    logger.info("Format classified as ranking (count=%d, conf=%.2f)", rank_count, conf)
    return decision
