"""Sliding-window candidate generator for transcript text.

Provides sliding window generation (default window 50s, stride 15s) with
sentence-boundary snapping, duration constraint enforcement, and greedy
non-overlapping selection.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from autoshorts.core.models import (
    ClipCandidate,
    HeatmapPoint,
    SponsorSegment,
    VideoChapter,
)

logger = logging.getLogger("autoshorts.decision.slicer")

SENTENCE_END_PATTERN = re.compile(r"[.?!](\s+|$|\")")

HOOK_TRIGGER_PHRASES = (
    "most people",
    "real reason",
    "what if",
    "did you know",
    "nobody tells you",
    "truth about",
)

HOOK_TRIGGER_WORDS = {
    "secret",
    "secrets",
    "never",
    "mistake",
    "mistakes",
    "truth",
    "why",
    "how",
    "nobody",
    "everyone",
    "stop",
    "wrong",
    "hidden",
    "actually",
    "warning",
    "shocking",
    "lie",
    "lies",
    "hack",
    "weird",
    "crazy",
    "impossible",
    "revealed",
    "proven",
}

NUMBER_WORDS = {
    "one",
    "two",
    "three",
    "four",
    "five",
    "seven",
    "ten",
    "hundred",
    "thousand",
    "million",
    "billion",
    "percent",
    "first",
    "single",
}


def compute_window_heatmap_score(
    start: float,
    end: float,
    heatmap: Sequence[HeatmapPoint] | None,
) -> float:
    """Compute the blended mean/peak YouTube 'Most Replayed' intensity overlapping [start, end].

    Returns 0.0 if heatmap is None, empty, or no points overlap [start, end].
    """
    if not heatmap or end <= start:
        return 0.0

    weighted_sum = 0.0
    total_weight = 0.0
    peak_val = 0.0
    matched_values: list[float] = []

    for pt in heatmap:
        st = float(pt.start_time)
        et = float(pt.end_time)
        val = max(0.0, min(1.0, float(pt.value)))

        overlap_start = max(start, st)
        overlap_end = min(end, et)
        overlap = overlap_end - overlap_start

        if overlap > 0.0 or (st == et and start <= st <= end):
            weight = max(overlap, 0.001)
            weighted_sum += val * weight
            total_weight += weight
            peak_val = max(peak_val, val)
            matched_values.append(val)

    if not matched_values or total_weight <= 0.0:
        return 0.0

    mean_val = weighted_sum / total_weight
    blended = 0.60 * mean_val + 0.40 * peak_val
    return round(max(0.0, min(1.0, blended)), 4)


def compute_hook_curiosity_score(text: str) -> float:
    """Score the opening sentence of a window (0.0 - 1.0) for viral hook markers.

    Evaluates questions ('?'), numbers/statistics, contrarian/curiosity triggers,
    and punchy opening sentence length.
    """
    cleaned = text.strip() if text else ""
    if not cleaned:
        return 0.0

    sentences = re.split(r"(?<=[.?!])\s+", cleaned, maxsplit=1)
    opening = sentences[0].strip() if sentences else cleaned
    if not opening:
        return 0.0

    lower_open = opening.lower()
    words = opening.split()
    word_count = len(words)
    clean_words = [re.sub(r"[^a-z0-9%]", "", w.lower()) for w in words]
    word_set = {w for w in clean_words if w}

    score = 0.0

    # 1. Question or high-energy exclamation in opening
    if "?" in opening:
        score += 0.30
    elif "!" in opening:
        score += 0.15

    # 2. Numbers / stats in opening
    has_digit = bool(re.search(r"\b\d+(?:\.\d+)?%?\b", opening))
    has_num_word = bool(word_set & NUMBER_WORDS)
    if has_digit or has_num_word:
        score += 0.20

    # 3. Contrarian / curiosity triggers
    trigger_hits = len(word_set & HOOK_TRIGGER_WORDS)
    for phrase in HOOK_TRIGGER_PHRASES:
        if phrase in lower_open:
            trigger_hits += 1

    if trigger_hits >= 1:
        score += min(0.45, 0.25 + 0.10 * (trigger_hits - 1))

    # 4. Punchy opening length (ideal hook is 3 to 18 words)
    if 3 <= word_count <= 18:
        score += 0.15
    elif 1 <= word_count <= 25:
        score += 0.05

    return round(max(0.0, min(1.0, score)), 4)


def _sponsor_overlap_ratio(
    start: float,
    end: float,
    sponsor_segments: Sequence[SponsorSegment] | None,
) -> float:
    """Compute fraction of [start, end] overlapping any sponsor segment."""
    if not sponsor_segments or end <= start:
        return 0.0

    win_dur = end - start
    total_overlap = 0.0
    for seg in sponsor_segments:
        ov = max(0.0, min(end, float(seg.end_time)) - max(start, float(seg.start_time)))
        total_overlap += ov

    return min(1.0, total_overlap / win_dur)


def overlaps_sponsor_segment(
    start: float,
    end: float,
    sponsor_segments: Sequence[SponsorSegment] | None,
    max_overlap_ratio: float = 0.25,
) -> bool:
    """Return True if more than max_overlap_ratio of [start, end] overlaps sponsor segments."""
    return _sponsor_overlap_ratio(start, end, sponsor_segments) > max_overlap_ratio


def _resolve_chapter_title(
    start: float,
    end: float,
    chapters: Sequence[VideoChapter] | None,
) -> str | None:
    """Find the chapter title with the largest overlap with [start, end]."""
    if not chapters or end <= start:
        return None

    best_title: str | None = None
    best_overlap = 0.0

    for ch in chapters:
        ov = max(0.0, min(end, float(ch.end_time)) - max(start, float(ch.start_time)))
        if ov > best_overlap and ch.title:
            best_overlap = ov
            best_title = ch.title

    if best_title is None:
        for ch in chapters:
            if float(ch.start_time) <= start <= float(ch.end_time) and ch.title:
                return ch.title

    return best_title


def _is_sentence_boundary(text: str) -> bool:
    """Return True if text ends with a sentence-terminal punctuation mark."""
    stripped = text.strip()
    if not stripped:
        return False
    return stripped[-1] in {".", "!", "?"} or (
        len(stripped) > 1
        and stripped[-2] in {".", "!", "?"}
        and stripped[-1] in {'"', "'", "”", "’"}
    )


def _normalize_segments(
    transcript: Sequence[Mapping[str, Any] | Any] | list[dict[str, Any]] | str,
) -> list[dict[str, Any]]:
    """Normalize transcript input into a list of timed segment dictionaries."""
    if isinstance(transcript, str):
        # Raw text string without timings: split by sentences and estimate timestamps (~2.5 words/sec)
        raw_text = transcript.strip()
        if not raw_text:
            return []
        sentences = re.split(r"(?<=[.?!])\s+", raw_text)
        segments: list[dict[str, Any]] = []
        cur_t = 0.0
        for s in sentences:
            s_clean = s.strip()
            if not s_clean:
                continue
            words = max(1, len(s_clean.split()))
            dur = max(1.5, words / 2.5)
            segments.append(
                {
                    "start": cur_t,
                    "end": cur_t + dur,
                    "text": s_clean,
                }
            )
            cur_t += dur
        return segments

    # List of mappings or objects
    norm_segments: list[dict[str, Any]] = []
    for item in transcript:
        if isinstance(item, Mapping):
            start = float(item.get("start", 0.0))
            end = float(item.get("end", 0.0))
            duration = float(item.get("duration", 0.0))
            text = str(item.get("text", "")).strip()
        else:
            start = float(getattr(item, "start", 0.0) or 0.0)
            end = float(getattr(item, "end", 0.0) or 0.0)
            duration = float(getattr(item, "duration", 0.0) or 0.0)
            text = str(getattr(item, "text", "") or "").strip()
        if end <= start and duration > 0.0:
            end = start + duration
        if text:
            norm_segments.append({"start": start, "end": end, "text": text})

    norm_segments.sort(key=lambda s: s["start"])
    return norm_segments


def slice_transcript_windows(
    transcript: Sequence[Mapping[str, Any] | Any] | list[dict[str, Any]] | str,
    window_duration_seconds: float = 50.0,
    stride_seconds: float = 15.0,
    min_duration_seconds: float = 30.0,
    max_duration_seconds: float = 60.0,
    heatmap: Sequence[HeatmapPoint] | None = None,
    chapters: Sequence[VideoChapter] | None = None,
    sponsor_segments: Sequence[SponsorSegment] | None = None,
    skip_sponsors: bool = True,
) -> list[ClipCandidate]:
    """Generate 30-60 second candidate transcript windows with sentence boundary snapping.

    Populates heatmap_score, hook_strength, and matching chapter_title on each candidate,
    and filters out sponsor-overlapping windows when skip_sponsors=True (preserving the
    cleanest fallback windows if all windows overlap).
    """
    segments = _normalize_segments(transcript)
    if not segments:
        return []

    def _build_candidate(start_t: float, end_t: float, text_str: str) -> ClipCandidate:
        s_t = round(start_t, 2)
        e_t = round(end_t, 2)
        return ClipCandidate(
            start=s_t,
            end=e_t,
            text=text_str,
            score=0.0,
            heatmap_score=compute_window_heatmap_score(s_t, e_t, heatmap),
            hook_strength=compute_hook_curiosity_score(text_str),
            chapter_title=_resolve_chapter_title(s_t, e_t, chapters),
        )

    total_duration = segments[-1]["end"]
    if total_duration < min_duration_seconds:
        # Transcript shorter than min_duration: return single candidate if not empty
        full_text = " ".join(s["text"] for s in segments)
        return [
            _build_candidate(
                segments[0]["start"],
                segments[-1]["end"],
                full_text,
            )
        ]

    candidates: list[ClipCandidate] = []
    seen_intervals: set[tuple[int, int]] = set()

    current_t = 0.0
    while current_t + min_duration_seconds <= total_duration:
        nominal_end_t = current_t + window_duration_seconds

        # 1. Locate start segment snapped to the beginning of a sentence
        start_idx = 0
        for i, s in enumerate(segments):
            if s["start"] >= current_t:
                start_idx = i
                break

        # Check if preceding segment ended a sentence
        if start_idx > 0 and not _is_sentence_boundary(segments[start_idx - 1]["text"]):
            # Look forward/backward for nearest sentence boundary within 5s
            forward_snap = None
            for fi in range(start_idx, min(len(segments), start_idx + 4)):
                if fi > 0 and _is_sentence_boundary(segments[fi - 1]["text"]):
                    if abs(segments[fi]["start"] - current_t) <= 5.0:
                        forward_snap = fi
                        break
            if forward_snap is not None:
                start_idx = forward_snap

        actual_start = segments[start_idx]["start"]

        # 2. Locate end segment snapped to the end of a sentence
        end_idx = start_idx
        best_end_idx = None

        for j in range(start_idx, len(segments)):
            seg = segments[j]
            seg_dur = seg["end"] - actual_start

            if seg_dur > max_duration_seconds:
                break

            if seg_dur >= min_duration_seconds:
                if _is_sentence_boundary(seg["text"]):
                    best_end_idx = j
                    # If we reached or passed nominal target, accept this boundary
                    if seg["end"] >= nominal_end_t:
                        break
                elif best_end_idx is None:
                    best_end_idx = j

        if best_end_idx is not None:
            end_idx = best_end_idx
        else:
            # Fallback to last segment within max_duration
            for j in range(start_idx, len(segments)):
                if segments[j]["end"] - actual_start <= max_duration_seconds:
                    end_idx = j
                else:
                    break

        actual_end = segments[end_idx]["end"]
        duration = actual_end - actual_start

        if min_duration_seconds <= duration <= max_duration_seconds + 0.5:
            # Deduplicate by integer second rounded interval
            key = (round(actual_start), round(actual_end))
            if key not in seen_intervals:
                seen_intervals.add(key)
                window_text = " ".join(
                    segments[k]["text"] for k in range(start_idx, end_idx + 1)
                ).strip()
                candidates.append(_build_candidate(actual_start, actual_end, window_text))

        current_t += stride_seconds

    # If no window met criteria (e.g. edge cases), wrap full text as single candidate
    if not candidates:
        full_text = " ".join(s["text"] for s in segments)
        candidates.append(
            _build_candidate(
                segments[0]["start"],
                min(segments[-1]["end"], segments[0]["start"] + max_duration_seconds),
                full_text,
            )
        )

    if skip_sponsors and sponsor_segments and candidates:
        clean_candidates = [
            c for c in candidates if not overlaps_sponsor_segment(c.start, c.end, sponsor_segments)
        ]
        if clean_candidates:
            candidates = clean_candidates
        else:
            # Fallback: keep the cleanest window(s) if filtering would leave 0 windows
            min_ratio = min(
                _sponsor_overlap_ratio(c.start, c.end, sponsor_segments) for c in candidates
            )
            candidates = [
                c
                for c in candidates
                if _sponsor_overlap_ratio(c.start, c.end, sponsor_segments) <= min_ratio + 1e-6
            ]

    return candidates


def generate_candidate_windows(
    transcript: Sequence[Mapping[str, Any] | Any] | list[dict[str, Any]] | str,
    window_duration_seconds: float = 50.0,
    stride_seconds: float = 15.0,
    min_duration_seconds: float = 30.0,
    max_duration_seconds: float = 60.0,
    heatmap: Sequence[HeatmapPoint] | None = None,
    chapters: Sequence[VideoChapter] | None = None,
    sponsor_segments: Sequence[SponsorSegment] | None = None,
    skip_sponsors: bool = True,
    **kwargs: Any,
) -> list[ClipCandidate]:
    """Alias for slice_transcript_windows with flexible keyword argument support."""
    win_dur = float(
        kwargs.get(
            "target_duration",
            kwargs.get("window_size", kwargs.get("window_seconds", window_duration_seconds)),
        )
    )
    stride_dur = float(kwargs.get("stride", stride_seconds))
    min_dur = float(kwargs.get("min_duration", kwargs.get("min_seconds", min_duration_seconds)))
    max_dur = float(kwargs.get("max_duration", kwargs.get("max_seconds", max_duration_seconds)))
    return slice_transcript_windows(
        transcript=transcript,
        window_duration_seconds=win_dur,
        stride_seconds=stride_dur,
        min_duration_seconds=min_dur,
        max_duration_seconds=max_dur,
        heatmap=heatmap,
        chapters=chapters,
        sponsor_segments=sponsor_segments,
        skip_sponsors=skip_sponsors,
    )


def select_non_overlapping_candidates(
    candidates: Sequence[ClipCandidate],
    count: int = 1,
    min_gap_seconds: float = 0.0,
) -> list[ClipCandidate]:
    """Greedily select top-ranked candidates with zero temporal overlap.

    Args:
        candidates: Sequence of scored ClipCandidates.
        count: Maximum number of clips to select (clips_per_video).
        min_gap_seconds: Minimum required distance between consecutive clips in seconds.

    Returns:
        List of selected candidates sorted chronologically by start timestamp.
    """
    if not candidates or count <= 0:
        return []

    # Sort candidates by retention score descending (highest quality first)
    sorted_by_score = sorted(
        candidates,
        key=lambda c: (c.score, c.duration),
        reverse=True,
    )

    selected: list[ClipCandidate] = []
    for cand in sorted_by_score:
        overlaps = False
        for s in selected:
            # Overlap condition with optional gap margin
            if (cand.start < s.end + min_gap_seconds) and (s.start < cand.end + min_gap_seconds):
                overlaps = True
                break

        if not overlaps:
            selected.append(cand)
            if len(selected) >= count:
                break

    # Return chronologically ordered
    selected.sort(key=lambda c: c.start)
    return selected
