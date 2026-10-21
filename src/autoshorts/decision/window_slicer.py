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

CURIOSITY_GAP_PATTERNS = (
    "what if",
    "did you know",
    "why does",
    "why do",
    "why is",
    "the real reason",
    "the reason why",
    "what happens when",
    "here's why",
    "heres why",
    "nobody knows",
    "nobody tells you",
    "secret to",
    "secret behind",
    "hidden truth",
    "the mystery of",
    "the single reason",
    "uncovered",
    "have you ever wondered",
    "this changes everything",
)

CONTRARIAN_PATTERNS = (
    "stop doing",
    "you're doing it wrong",
    "you are doing it wrong",
    "everyone thinks",
    "most people think",
    "most people believe",
    "the biggest myth",
    "lies about",
    "actually wrong",
    "waste of time",
    "don't ever",
    "dont ever",
    "forget everything you know",
    "unpopular opinion",
    "the lie we were told",
    "everything you know about",
    "stop making this mistake",
)

EMOTIONAL_STAKES_WORDS = {
    "terrifying",
    "ruined",
    "nightmare",
    "dangerous",
    "heartbreaking",
    "craziest",
    "regret",
    "warning",
    "disaster",
    "shocking",
    "horrifying",
    "trapped",
    "deadly",
    "destroyed",
    "panic",
    "fatal",
    "unbelievable",
    "insane",
}

NARRATIVE_OPEN_LOOPS = (
    "before you scroll",
    "wait until the end",
    "if you only remember one thing",
    "here is the catch",
    "heres the catch",
    "and then it happened",
    "the plot twist",
    "you won't believe what happened",
    "you wont believe what happened",
    "pay close attention",
    "here's what they won't tell you",
    "watch until the end",
    "this is where it gets crazy",
)

SURPRISING_DATA_REGEX = re.compile(
    r"\b(\d+(?:\.\d+)?%|\d+x|\d+\s*(?:million|billion|thousand|hundred|percent|seconds?|minutes?|hours?|days?|years?))\b",
    re.IGNORECASE,
)

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

DANGLING_CONJUNCTIONS = {
    "and",
    "but",
    "or",
    "so",
    "because",
    "although",
    "though",
    "while",
    "if",
    "unless",
    "that",
    "which",
    "since",
    "yet",
    "where",
    "when",
    "then",
}

DANGLING_PREPOSITIONS = {
    "to",
    "of",
    "with",
    "at",
    "from",
    "into",
    "for",
    "on",
    "in",
    "by",
    "about",
    "like",
    "through",
    "as",
}

DANGLING_VERBS = {
    "is",
    "was",
    "are",
    "were",
    "has",
    "have",
    "had",
    "will",
    "would",
    "could",
    "should",
    "can",
    "must",
    "might",
    "am",
    "be",
    "been",
    "being",
}

DANGLING_ARTICLES = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "my",
    "your",
    "his",
    "her",
    "their",
    "our",
}


def cluster_heatmap_spikes(
    heatmap: Sequence[HeatmapPoint] | None,
    min_spike_val: float = 0.35,
) -> list[dict[str, Any]]:
    """Identify true peak retention velocity clusters from YouTube 'Most Replayed' data.

    Returns a list of cluster dicts with start, end, peak_time, peak_val, and velocity.
    """
    if not heatmap:
        return []

    pts = sorted(heatmap, key=lambda p: float(p.start_time))
    clusters: list[dict[str, Any]] = []
    cur_cluster: list[tuple[HeatmapPoint, float]] = []

    for i, pt in enumerate(pts):
        val = max(0.0, min(1.0, float(pt.value)))
        prev_val = max(0.0, min(1.0, float(pts[i - 1].value))) if i > 0 else val
        dt = max(0.2, float(pt.end_time) - float(pt.start_time))
        velocity = (val - prev_val) / dt

        if val >= min_spike_val or velocity > 0.05:
            cur_cluster.append((pt, velocity))
        else:
            if cur_cluster:
                start_t = float(cur_cluster[0][0].start_time)
                end_t = float(cur_cluster[-1][0].end_time)
                peak_pt = max(cur_cluster, key=lambda x: float(x[0].value))[0]
                max_vel = max(x[1] for x in cur_cluster)
                clusters.append(
                    {
                        "start": round(start_t, 2),
                        "end": round(end_t, 2),
                        "peak_time": round((float(peak_pt.start_time) + float(peak_pt.end_time)) / 2.0, 2),
                        "peak_value": round(float(peak_pt.value), 4),
                        "velocity": round(max(0.0, max_vel), 4),
                    }
                )
                cur_cluster = []

    if cur_cluster:
        start_t = float(cur_cluster[0][0].start_time)
        end_t = float(cur_cluster[-1][0].end_time)
        peak_pt = max(cur_cluster, key=lambda x: float(x[0].value))[0]
        max_vel = max(x[1] for x in cur_cluster)
        clusters.append(
            {
                "start": round(start_t, 2),
                "end": round(end_t, 2),
                "peak_time": round((float(peak_pt.start_time) + float(peak_pt.end_time)) / 2.0, 2),
                "peak_value": round(float(peak_pt.value), 4),
                "velocity": round(max(0.0, max_vel), 4),
            }
        )

    return clusters


def compute_window_heatmap_score(
    start: float,
    end: float,
    heatmap: Sequence[HeatmapPoint] | None,
    speech_segments: Sequence[Mapping[str, Any] | Any] | None = None,
) -> float:
    """Compute the blended mean/peak YouTube 'Most Replayed' intensity overlapping [start, end].

    When speech_segments are provided, weights peak velocity clusters that align
    with active, clear speech segments.
    Returns 0.0 if heatmap is None, empty, or no points overlap [start, end].
    """
    if not heatmap or end <= start:
        return 0.0

    weighted_sum = 0.0
    total_weight = 0.0
    peak_val = 0.0
    peak_time = (start + end) / 2.0
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
            if val > peak_val:
                peak_val = val
                peak_time = (st + et) / 2.0
            matched_values.append(val)

    if not matched_values or total_weight <= 0.0:
        return 0.0

    mean_val = weighted_sum / total_weight
    blended = 0.60 * mean_val + 0.40 * peak_val

    # Speech clarity alignment weighting
    if speech_segments and peak_val > 0.40:
        is_speech_aligned = False
        for seg in speech_segments:
            if isinstance(seg, Mapping):
                s_st = float(seg.get("start", 0.0))
                s_et = float(seg.get("end", 0.0))
            else:
                s_st = float(getattr(seg, "start", 0.0) or 0.0)
                s_et = float(getattr(seg, "end", 0.0) or 0.0)
            if s_st <= peak_time <= s_et:
                is_speech_aligned = True
                break

        if is_speech_aligned:
            # Positive boost for aligning peak with spoken speech
            blended = min(1.0, blended + 0.05)
        else:
            # Peak falls outside spoken speech (e.g. silence or background noise)
            blended = max(0.0, blended - 0.03)

    return round(max(0.0, min(1.0, blended)), 4)


def classify_semantic_hook(text: str) -> dict[str, Any]:
    """Advanced semantic hook classifier for viral short-form video identification.

    Evaluates curiosity gaps, contrarian statements, emotional stakes, surprising data
    points, and narrative open loops in the opening sentence.
    """
    cleaned = text.strip() if text else ""
    if not cleaned:
        return {
            "score": 0.0,
            "is_hook": False,
            "primary_category": "none",
            "detected_signals": [],
            "opening_sentence": "",
            "metrics": {},
        }

    sentences = re.split(r"(?<=[.?!])\s+", cleaned, maxsplit=1)
    opening = sentences[0].strip() if sentences else cleaned
    lower_open = opening.lower()
    words = opening.split()
    word_count = len(words)
    clean_words = [re.sub(r"[^a-z0-9%]", "", w.lower()) for w in words]
    word_set = {w for w in clean_words if w}

    signals: list[str] = []
    category_scores: dict[str, float] = {
        "curiosity_gap": 0.0,
        "contrarian": 0.0,
        "emotional_stakes": 0.0,
        "surprising_data": 0.0,
        "narrative_open_loop": 0.0,
    }

    # 1. Curiosity gaps
    curiosity_hits = [p for p in CURIOSITY_GAP_PATTERNS if p in lower_open]
    if curiosity_hits:
        signals.extend([f"curiosity_gap:{h}" for h in curiosity_hits])
        category_scores["curiosity_gap"] = min(0.35, 0.20 + 0.08 * len(curiosity_hits))

    # 2. Contrarian statements
    contrarian_hits = [p for p in CONTRARIAN_PATTERNS if p in lower_open]
    if contrarian_hits:
        signals.extend([f"contrarian:{h}" for h in contrarian_hits])
        category_scores["contrarian"] = min(0.35, 0.22 + 0.08 * len(contrarian_hits))

    # 3. Emotional stakes
    emotional_hits = word_set & EMOTIONAL_STAKES_WORDS
    if emotional_hits:
        signals.extend([f"emotional_stake:{w}" for w in emotional_hits])
        category_scores["emotional_stakes"] = min(0.30, 0.18 + 0.06 * len(emotional_hits))

    # 4. Surprising data points
    has_data = bool(SURPRISING_DATA_REGEX.search(opening))
    has_num_word = bool(word_set & NUMBER_WORDS)
    if has_data or has_num_word:
        signals.append("surprising_data")
        category_scores["surprising_data"] = 0.32 if has_data else 0.18

    # 5. Narrative open loops
    open_loop_hits = [p for p in NARRATIVE_OPEN_LOOPS if p in lower_open]
    if open_loop_hits:
        signals.extend([f"open_loop:{h}" for h in open_loop_hits])
        category_scores["narrative_open_loop"] = min(0.35, 0.25 + 0.08 * len(open_loop_hits))

    # General punctuation markers
    base_score = 0.0
    if "?" in opening:
        base_score += 0.30
        signals.append("question_mark")
    elif "!" in opening:
        base_score += 0.15
        signals.append("exclamation_mark")

    # Hook trigger words/phrases
    trigger_hits = len(word_set & HOOK_TRIGGER_WORDS)
    for phrase in HOOK_TRIGGER_PHRASES:
        if phrase in lower_open and f"curiosity_gap:{phrase}" not in signals:
            trigger_hits += 1
    if trigger_hits >= 1:
        base_score += min(0.25, 0.15 + 0.05 * (trigger_hits - 1))
        signals.append(f"trigger_words:{trigger_hits}")

    # Length bonus: ideal hook is 3 to 18 words
    length_score = 0.0
    if 3 <= word_count <= 18:
        length_score = 0.15
    elif 1 <= word_count <= 25:
        length_score = 0.05

    category_sum = sum(category_scores.values())
    total_score = base_score + category_sum + length_score

    best_cat = "standard"
    if category_scores:
        top_cat, top_val = max(category_scores.items(), key=lambda x: x[1])
        if top_val > 0.0:
            best_cat = top_cat
        elif "?" in opening:
            best_cat = "question_inquiry"

    final_score = round(max(0.0, min(1.0, total_score)), 4)
    return {
        "score": final_score,
        "is_hook": final_score >= 0.40,
        "primary_category": best_cat,
        "detected_signals": signals,
        "opening_sentence": opening,
        "metrics": {
            **category_scores,
            "base_score": round(base_score, 4),
            "length_score": round(length_score, 4),
        },
    }


def compute_hook_curiosity_score(text: str) -> float:
    """Score the opening sentence of a window (0.0 - 1.0) for viral hook markers.

    Evaluates questions ('?'), numbers/statistics, contrarian/curiosity triggers,
    emotional stakes, narrative open loops, and punchy opening sentence length.
    """
    return classify_semantic_hook(text)["score"]


def compute_narrative_completeness_score(text: str) -> float:
    """Evaluate whether candidate window text resolves cleanly or cuts off mid-thought.

    Returns:
        Completeness score in [0.0, 1.0]. 1.0 indicates a fully formed, grammatically
        resolved excerpt. Values < 0.70 indicate truncated phrases or mid-sentence cuts.
    """
    stripped = text.strip() if text else ""
    if not stripped:
        return 0.0

    score = 1.0

    # 1. Check terminal punctuation and trailing ellipsis/dashes
    if stripped.endswith(("...", "…")):
        score -= 0.35  # Trailing unfinished thought
    elif stripped[-1] in {",", ";", ":", "-", "—"}:
        score -= 0.40  # Mid-clause cutoff
    else:
        has_terminal_punct = stripped[-1] in {".", "!", "?"} or (
            len(stripped) > 1
            and stripped[-2] in {".", "!", "?"}
            and stripped[-1] in {'"', "'", "”", "’"}
        )
        if not has_terminal_punct:
            score -= 0.35  # Cut off mid-sentence with no punctuation

    # 2. Check trailing word for dangling conjunctions/prepositions/verbs/articles
    words = stripped.split()
    if words:
        clean_last = re.sub(r"[^\w]", "", words[-1].lower())
        if clean_last in DANGLING_CONJUNCTIONS:
            score -= 0.35
        elif clean_last in (DANGLING_PREPOSITIONS | DANGLING_VERBS | DANGLING_ARTICLES):
            score -= 0.30

    # 3. Check start of text for awkward mid-sentence fragment
    if words:
        clean_first = re.sub(r"[^\w]", "", words[0].lower())
        if len(words) > 1 and clean_first in {"and", "but", "because", "so", "or", "which"}:
            score -= 0.15

    return round(max(0.05, min(1.0, score)), 4)


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


def _is_clause_boundary(text: str) -> bool:
    """Return True if text ends with a clause boundary punctuation mark."""
    stripped = text.strip()
    if not stripped:
        return False
    if _is_sentence_boundary(stripped):
        return True
    return stripped[-1] in {";", ":", "—", "-", ","} or (
        len(stripped) > 1
        and stripped[-2] in {";", ":", "—", "-"}
        and stripped[-1] in {'"', "'", "”", "’"}
    )


def _starts_with_clean_opener(text: str) -> bool:
    """Return True if text begins with a capitalized word that is not a lowercase continuation."""
    words = text.strip().split()
    if not words:
        return False
    first = words[0]
    if not first[0].isupper():
        return False
    lower_first = re.sub(r"[^\w]", "", first.lower())
    continuation_words = {"and", "but", "so", "because", "or", "which", "then", "though"}
    return lower_first not in continuation_words


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
            heatmap_score=compute_window_heatmap_score(s_t, e_t, heatmap, speech_segments=segments),
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

        # 1. Locate start segment snapped strictly to the beginning of a sentence or clause
        nominal_start_idx = 0
        for i, s in enumerate(segments):
            if s["start"] >= current_t:
                nominal_start_idx = i
                break

        # Search candidates around nominal_start_idx within 6.0 seconds for optimal boundary
        best_start_idx = nominal_start_idx
        best_boundary_score = -100.0

        search_min = max(0, nominal_start_idx - 3)
        search_max = min(len(segments), nominal_start_idx + 4)

        for candidate_idx in range(search_min, search_max):
            dist = abs(segments[candidate_idx]["start"] - current_t)
            if dist > 6.0:
                continue

            score = 10.0 - dist * 1.5

            if candidate_idx == 0:
                score += 50.0  # Video start
            else:
                prev_text = segments[candidate_idx - 1]["text"]
                cur_text = segments[candidate_idx]["text"]
                if _is_sentence_boundary(prev_text):
                    score += 45.0
                    if _starts_with_clean_opener(cur_text):
                        score += 15.0
                elif _is_clause_boundary(prev_text):
                    score += 25.0
                elif segments[candidate_idx]["start"] - segments[candidate_idx - 1]["end"] >= 0.35:
                    score += 15.0

            if score > best_boundary_score:
                best_boundary_score = score
                best_start_idx = candidate_idx

        start_idx = best_start_idx
        actual_start = segments[start_idx]["start"]

        # 2. Locate end segment snapped strictly to the end of a sentence or clause
        end_idx = start_idx
        best_end_idx = None
        best_end_score = -100.0

        for j in range(start_idx, len(segments)):
            seg = segments[j]
            seg_dur = seg["end"] - actual_start

            if seg_dur > max_duration_seconds:
                break

            if seg_dur >= min_duration_seconds:
                dist_to_nominal = abs(seg["end"] - nominal_end_t)
                boundary_score = 15.0 - dist_to_nominal * 0.5
                if _is_sentence_boundary(seg["text"]):
                    boundary_score += 40.0
                elif _is_clause_boundary(seg["text"]):
                    boundary_score += 20.0

                if boundary_score > best_end_score:
                    best_end_score = boundary_score
                    best_end_idx = j

                # If sentence boundary reached target nominal end, stop searching
                if _is_sentence_boundary(seg["text"]) and seg["end"] >= nominal_end_t:
                    break

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


__all__ = [
    "classify_semantic_hook",
    "cluster_heatmap_spikes",
    "compute_hook_curiosity_score",
    "compute_narrative_completeness_score",
    "compute_window_heatmap_score",
    "generate_candidate_windows",
    "overlaps_sponsor_segment",
    "select_non_overlapping_candidates",
    "slice_transcript_windows",
]

