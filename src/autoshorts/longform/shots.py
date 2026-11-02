"""Pure planning of visual sub-shots within one narration section.

No I/O, no rendering: given a section duration and word timings (relative to
the section start), split the section into contiguous shots whose lengths lie
in ``[min_shot, max_shot]`` where possible, and assign each a visual kind.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

ShotKind = Literal["wide", "close", "insert", "montage"]

SENTENCE_END_CHARS = (".", "!", "?")
TRAILING_PUNCT_STRIP = "\"'”’)]}"
GAP_CUT_SECONDS = 0.6
MONTAGE_BELOW_SECONDS = 2.5
INSERT_EVERY = 3


@dataclass(frozen=True)
class Shot:
    start: float
    end: float
    kind: ShotKind

    @property
    def length(self) -> float:
        return self.end - self.start


def total_coverage(shots: Sequence[Shot]) -> float:
    """Return the sum of shot lengths."""
    return sum(shot.length for shot in shots)


def _candidate_cuts(duration: float, words: Sequence[Mapping[str, object]]) -> list[float]:
    """Sentence ends and long gaps between words, strictly inside (0, duration)."""
    points: set[float] = set()
    for i, word in enumerate(words):
        end = float(word["end"])  # type: ignore[arg-type]
        text = str(word.get("word", "")).rstrip(TRAILING_PUNCT_STRIP)
        if text.endswith(SENTENCE_END_CHARS):
            points.add(end)
        if i + 1 < len(words):
            next_start = float(words[i + 1]["start"])  # type: ignore[arg-type]
            if next_start - end >= GAP_CUT_SECONDS:
                points.add(end)
    return sorted(p for p in points if 0.0 < p < duration)


def plan_shots(
    duration: float,
    words: Sequence[Mapping[str, object]],
    min_shot: float = 2.0,
    max_shot: float = 4.0,
) -> list[Shot]:
    """Split a section of ``duration`` seconds into visual shots.

    Raises ValueError when ``duration <= 0`` or the shot bounds are invalid.
    """
    if duration <= 0:
        raise ValueError(f"duration must be positive, got {duration}")
    if not 0 < min_shot <= max_shot:
        raise ValueError(f"need 0 < min_shot <= max_shot, got {min_shot}, {max_shot}")

    total = float(duration)
    if not words:
        return [Shot(0.0, total, "wide")]

    cuts = _candidate_cuts(total, words)
    spans: list[tuple[float, float]] = []
    start = 0.0
    while total - start > max_shot:
        lo, hi = start + min_shot, start + max_shot
        in_range = [c for c in cuts if lo <= c <= hi]
        cut = in_range[-1] if in_range else hi
        spans.append((start, cut))
        start = cut
    spans.append((start, total))

    # A short remainder folds into the previous shot. If that would overshoot
    # max_shot, rebalance the last two shots so both fall inside the bounds.
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < min_shot:
        prev_start = spans[-2][0]
        tail_end = spans[-1][1]
        combined = tail_end - prev_start
        if combined <= max_shot:
            spans[-2:] = [(prev_start, tail_end)]
        else:
            split = tail_end - min_shot
            spans[-2:] = [(prev_start, split), (split, tail_end)]

    shots: list[Shot] = []
    close_next = True
    for i, (s, e) in enumerate(spans):
        if i == 0:
            kind: ShotKind = "wide"
        elif i % INSERT_EVERY == INSERT_EVERY - 1:
            kind = "insert"
        elif e - s < MONTAGE_BELOW_SECONDS:
            kind = "montage"
        else:
            kind = "close" if close_next else "wide"
            close_next = not close_next
        shots.append(Shot(s, e, kind))
    return shots
