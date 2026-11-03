"""Pure planning of visual sub-shots within one narration section.

No I/O, no rendering: given a section duration and word timings (relative to
the section start), split the section into contiguous shots and assign each a
visual kind.

Pacing rules, in priority order:

* Trigger words open special shots. Montage words ("every", "again", "day",
  "repeat", "minute", "hour", "over and over") start a run of fast 0.6 s
  montage cuts. Impact words ("not", "never", "lazy", "fail*") start a 0.25 s
  ``impact`` shot followed by a 1.2 s ``ecu`` shot.
* Gaps between special shots are filled with regular shots of roughly
  ``TARGET_SHOT_SECONDS``, kept inside ``[min_shot, max_shot]`` where possible.
  Cuts snap to the nearest beat in the window when ``beat_times`` is given,
  otherwise to sentence ends or long pauses.
* Regular shots cycle close/wide, with ``insert`` and ``match`` slots mixed in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

ShotKind = Literal["wide", "close", "insert", "montage", "impact", "ecu", "match"]

SENTENCE_END_CHARS = (".", "!", "?")
TRAILING_PUNCT_STRIP = "\"'”’)]}"
GAP_CUT_SECONDS = 0.6
MONTAGE_BELOW_SECONDS = 2.5
INSERT_EVERY = 3
MATCH_EVERY = 4
TARGET_SHOT_SECONDS = 2.5
IMPACT_SECONDS = 0.25
ECU_SECONDS = 1.2
MONTAGE_RUN_COUNT = 6
MONTAGE_SHOT_SECONDS = 0.6
EPS = 1e-9

_TOKEN_RE = re.compile(r"[a-z]+")
_MONTAGE_TOKENS = frozenset({"every", "again", "day", "days", "minute", "minutes", "hour", "hours"})
_MONTAGE_PREFIXES = ("repeat",)
_IMPACT_TOKENS = frozenset({"not", "never", "lazy"})
_IMPACT_PREFIXES = ("fail",)


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


def _trigger_events(words: Sequence[Mapping[str, object]]) -> list[tuple[float, str]]:
    """Return ``(start, "montage" | "impact")`` for each trigger word, in time order."""
    tokens: list[tuple[str, float]] = []
    for word in words:
        start = float(word["start"])  # type: ignore[arg-type]
        for tok in _TOKEN_RE.findall(str(word.get("word", "")).lower()):
            tokens.append((tok, start))

    events: list[tuple[float, str]] = []
    for i, (tok, start) in enumerate(tokens):
        following = [t for t, _ in tokens[i + 1 : i + 3]]
        if tok == "over" and following == ["and", "over"]:
            events.append((start, "montage"))
        elif tok in _MONTAGE_TOKENS or tok.startswith(_MONTAGE_PREFIXES):
            events.append((start, "montage"))
        elif tok in _IMPACT_TOKENS or tok.startswith(_IMPACT_PREFIXES):
            events.append((start, "impact"))
    return sorted(events)


def _special_items(trigger: str) -> list[tuple[float, ShotKind]]:
    """Contiguous ``(length, kind)`` shots emitted by one trigger."""
    if trigger == "montage":
        return [(MONTAGE_SHOT_SECONDS, "montage")] * MONTAGE_RUN_COUNT
    return [(IMPACT_SECONDS, "impact"), (ECU_SECONDS, "ecu")]


def _pick_cut(
    target: float, lo: float, hi: float, beats: Sequence[float], cuts: Sequence[float]
) -> float:
    """Beat nearest the target in the window, else sentence/pause cut, else the target."""
    for pool in (beats, cuts):
        in_range = [c for c in pool if lo <= c <= hi]
        if in_range:
            return min(in_range, key=lambda c: abs(c - target))
    return target


def _fill_gap(
    gap_start: float,
    gap_end: float,
    cuts: Sequence[float],
    beats: Sequence[float],
    min_shot: float,
    max_shot: float,
) -> list[tuple[float, float]]:
    """Split ``[gap_start, gap_end]`` into contiguous non-empty spans."""
    spans: list[tuple[float, float]] = []
    start = gap_start
    while gap_end - start > max_shot:
        lo, hi = start + min_shot, start + max_shot
        target = min(max(start + TARGET_SHOT_SECONDS, lo), hi)
        cut = _pick_cut(target, lo, hi, beats, cuts)
        spans.append((start, cut))
        start = cut
    spans.append((start, gap_end))

    # A short remainder folds into the previous span. If that would overshoot
    # max_shot, rebalance the last two spans so both fall inside the bounds.
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < min_shot:
        prev_start = spans[-2][0]
        tail_end = spans[-1][1]
        combined = tail_end - prev_start
        if combined <= max_shot:
            spans[-2:] = [(prev_start, tail_end)]
        else:
            split = tail_end - min_shot
            spans[-2:] = [(prev_start, split), (split, tail_end)]
    return spans


def _filtered_sorted(values: Iterable[float], total: float) -> list[float]:
    return sorted(v for v in values if 0.0 < v < total)


def plan_shots(
    duration: float,
    words: Sequence[Mapping[str, object]],
    min_shot: float = 2.0,
    max_shot: float = 4.0,
    beat_times: Sequence[float] | None = None,
) -> list[Shot]:
    """Split a section of ``duration`` seconds into visual shots.

    ``beat_times`` (musical or word beats, seconds relative to the section start)
    makes regular cuts snap to the nearest beat inside each shot window.

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
    beats = _filtered_sorted(beat_times or (), total)

    # placed holds (start, end, special_kind); special_kind None means a regular shot.
    placed: list[tuple[float, float, ShotKind | None]] = []
    cursor = 0.0
    for start, trigger in _trigger_events(words):
        if start >= total or start < cursor:
            # Already covered by an earlier special run, so the trigger is absorbed.
            continue
        if start - cursor > EPS:
            for s, e in _fill_gap(cursor, start, cuts, beats, min_shot, max_shot):
                placed.append((s, e, None))
            cursor = start
        for length, kind in _special_items(trigger):
            end = min(cursor + length, total)
            if end - cursor <= EPS:
                break
            placed.append((cursor, end, kind))
            cursor = end

    if total - cursor > EPS:
        for s, e in _fill_gap(cursor, total, cuts, beats, min_shot, max_shot):
            placed.append((s, e, None))
    if placed:
        s, _, kind = placed[-1]
        placed[-1] = (s, total, kind)

    shots: list[Shot] = []
    close_next = True
    regular_count = 0
    for i, (s, e, special) in enumerate(placed):
        if special is not None:
            kind: ShotKind = special
        elif i == 0:
            kind = "wide"
        elif i % INSERT_EVERY == INSERT_EVERY - 1:
            kind = "insert"
        elif e - s < MONTAGE_BELOW_SECONDS:
            kind = "montage"
        else:
            regular_count += 1
            if regular_count % MATCH_EVERY == 0:
                kind = "match"
            else:
                kind = "close" if close_next else "wide"
                close_next = not close_next
        shots.append(Shot(s, e, kind))
    return shots
