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

from autoshorts.core.models import ClipCandidate

logger = logging.getLogger("autoshorts.decision.slicer")

SENTENCE_END_PATTERN = re.compile(r"[.?!](\s+|$|\")")


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
    transcript: Sequence[Mapping[str, Any]] | list[dict[str, Any]] | str,
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

    # List of mappings
    norm_segments: list[dict[str, Any]] = []
    for item in transcript:
        start = float(item.get("start", 0.0))
        end = float(item.get("end", 0.0))
        duration = float(item.get("duration", 0.0))
        if end <= start and duration > 0.0:
            end = start + duration
        text = str(item.get("text", "")).strip()
        if text:
            norm_segments.append({"start": start, "end": end, "text": text})

    norm_segments.sort(key=lambda s: s["start"])
    return norm_segments


def slice_transcript_windows(
    transcript: Sequence[Mapping[str, Any]] | list[dict[str, Any]] | str,
    window_duration_seconds: float = 50.0,
    stride_seconds: float = 15.0,
    min_duration_seconds: float = 30.0,
    max_duration_seconds: float = 60.0,
) -> list[ClipCandidate]:
    """Generate 30-60 second candidate transcript windows with sentence boundary snapping.

    Args:
        transcript: Timed segments or raw transcript text.
        window_duration_seconds: Target nominal window length (default: 50s).
        stride_seconds: Step interval between consecutive windows (default: 15s).
        min_duration_seconds: Minimum allowable candidate duration (default: 30s).
        max_duration_seconds: Maximum allowable candidate duration (default: 60s).

    Returns:
        List of generated ClipCandidate instances.
    """
    segments = _normalize_segments(transcript)
    if not segments:
        return []

    total_duration = segments[-1]["end"]
    if total_duration < min_duration_seconds:
        # Transcript shorter than min_duration: return single candidate if not empty
        full_text = " ".join(s["text"] for s in segments)
        return [
            ClipCandidate(
                start=segments[0]["start"],
                end=segments[-1]["end"],
                text=full_text,
                score=0.0,
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
                candidates.append(
                    ClipCandidate(
                        start=round(actual_start, 2),
                        end=round(actual_end, 2),
                        text=window_text,
                        score=0.0,
                    )
                )

        current_t += stride_seconds

    # If no window met criteria (e.g. edge cases), wrap full text as single candidate
    if not candidates:
        full_text = " ".join(s["text"] for s in segments)
        candidates.append(
            ClipCandidate(
                start=segments[0]["start"],
                end=min(segments[-1]["end"], segments[0]["start"] + max_duration_seconds),
                text=full_text,
                score=0.0,
            )
        )

    return candidates


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
