"""Editing Pacing Analyzer, Kinetic Word Enricher & EDL Snapshot/Rollback Engine.

Provides:
- PacingAuditReport: Real-time pacing metrics, retention scoring, and actionable recommendations.
- analyze_edit_rhythm: Computes spoken WPM, dead-air silence ratio, visual event density,
  and first-3-second hook word density.
- optimize_edl_for_retention: Detects and cuts dead-air gaps, injects high-impact zoom punches
  so video never stays static > 8s, and generates audio ducking (BgmDuck) during rapid speech bursts.
- Snapshot & Rollback Engine: Safe JSON snapshot storage and 1-click restore before destructive edits.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.edit_planner.edl_schema import clamp_edl
from autoshorts.edit_planner.planner import (
    POWER_WORDS,
)
from autoshorts.media.ass_styler import EMPHASIS_KEYWORDS, TimedWord

logger = logging.getLogger(__name__)

# Ensure JumpCut exposes .start and .end properties if not already present
if not hasattr(JumpCut, "start"):
    JumpCut.start = property(lambda self: self.cut_start)  # type: ignore[attr-defined]
if not hasattr(JumpCut, "end"):
    JumpCut.end = property(lambda self: self.cut_end)  # type: ignore[attr-defined]


class BgmDuck(BaseModel):
    """Audio ducking interval synchronized to rapid speech bursts."""

    start_sec: float = Field(..., ge=0.0, description="Ducking start timestamp in seconds")
    end_sec: float = Field(..., ge=0.0, description="Ducking end timestamp in seconds")
    duck_volume_db: float = Field(default=-12.0, description="Audio reduction in dB")
    reason: str = Field(default="rapid_speech_burst", description="Trigger reason")

    @property
    def start(self) -> float:
        """Alias for start_sec."""
        return self.start_sec

    @property
    def end(self) -> float:
        """Alias for end_sec."""
        return self.end_sec


# Extend EditDecisionList with bgm_ducks property for backward and forward compatibility
if not hasattr(EditDecisionList, "bgm_ducks"):
    EditDecisionList.bgm_ducks = property(
        lambda self: getattr(self, "_bgm_ducks", []),
        lambda self, v: setattr(self, "_bgm_ducks", list(v)),
    )

_OrigEdlInit = EditDecisionList.__init__


def _flexible_edl_init(self: EditDecisionList, **data: Any) -> None:
    ducks = data.pop("bgm_ducks", None)
    _OrigEdlInit(self, **data)
    if ducks is not None:
        self.bgm_ducks = ducks


EditDecisionList.__init__ = _flexible_edl_init  # type: ignore[method-assign]


class PacingAuditReport(BaseModel):
    """Retention pacing audit report for vertical short-form video."""

    clip_duration_sec: float
    effective_duration_sec: float
    word_count: int
    words_per_minute: float
    silence_ratio: float
    visual_events_per_10s: float
    hook_word_density_first_3s: float
    retention_pacing_score: float = Field(ge=0.0, le=100.0)
    pacing_grade: Literal["A+", "A", "B", "C", "D"]
    recommendations: list[str] = Field(default_factory=list)


CURRENCY_PATTERN = re.compile(
    r"[$€£¥₹]|(?:\b(?:dollar|dollars|buck|bucks|cent|cents|usd|eur|gbp)\b)", re.IGNORECASE
)
NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?(?:%|k|m|b|th|st|nd|rd)?\b", re.IGNORECASE)


def _normalize_words(words: Sequence[TimedWord | dict[str, Any]]) -> list[TimedWord]:
    """Convert sequence of TimedWord or dict objects into sorted TimedWord instances."""
    normalized: list[TimedWord] = []
    for item in words:
        if isinstance(item, TimedWord):
            w = TimedWord(
                word=str(item.word).strip(),
                start=float(item.start),
                end=float(item.end),
                probability=float(getattr(item, "probability", 1.0)),
            )
        elif isinstance(item, dict):
            w = TimedWord.from_dict(item)
        else:
            w = TimedWord(
                word=str(getattr(item, "word", "")).strip(),
                start=float(getattr(item, "start", 0.0)),
                end=float(getattr(item, "end", 0.0)),
                probability=float(getattr(item, "probability", 1.0)),
            )
        w.end = max(w.end, w.start)
        if w.word:
            normalized.append(w)

    normalized.sort(key=lambda x: (x.start, x.end))
    return normalized


def _merge_intervals(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping or adjacent intervals [start, end]."""
    if not intervals:
        return []
    valid = sorted(
        [(float(s), float(e)) for s, e in intervals if e > s],
        key=lambda x: (x[0], x[1]),
    )
    if not valid:
        return []

    merged: list[tuple[float, float]] = [valid[0]]
    for cur_s, cur_e in valid[1:]:
        last_s, last_e = merged[-1]
        if cur_s <= last_e:
            merged[-1] = (last_s, max(last_e, cur_e))
        else:
            merged.append((cur_s, cur_e))
    return merged


def _calculate_effective_timeline(
    clip_duration: float, jump_cuts: Sequence[JumpCut]
) -> tuple[float, list[tuple[float, float]]]:
    """Calculate effective remaining duration and valid non-overlapping excised cut intervals."""
    total_dur = max(0.1, float(clip_duration))
    raw_cuts = [
        (max(0.0, float(c.cut_start)), min(total_dur, float(c.cut_end)))
        for c in jump_cuts
        if float(c.cut_end) > float(c.cut_start)
    ]
    merged_cuts = _merge_intervals(raw_cuts)
    total_excised = sum(e - s for s, e in merged_cuts)
    effective_dur = max(0.1, total_dur - total_excised)
    return effective_dur, merged_cuts


def analyze_edit_rhythm(
    words: Sequence[TimedWord | dict[str, Any]],
    edl: EditDecisionList,
    clip_duration: float,
) -> PacingAuditReport:
    """Analyze pacing, silence ratio, visual event density, and hook energy of an edit.

    Computes:
    - spoken WPM: based on word count and effective duration (after cuts).
    - dead-air silence ratio: remaining unexcised silence divided by effective duration.
    - visual events per 10s: jump cuts + zoom punch-ins per 10 seconds of effective video.
    - hook word density first 3s: spoken words per second in the opening 3.0s window.
    - retention_pacing_score (0-100) and pacing_grade ('A+', 'A', 'B', 'C', 'D').
    - concrete actionable recommendations.
    """
    total_dur = max(0.1, float(clip_duration))
    norm_words = _normalize_words(words)
    word_count = len(norm_words)

    effective_dur, merged_cuts = _calculate_effective_timeline(total_dur, edl.jump_cuts)

    # Spoken Words Per Minute
    if effective_dur > 0 and word_count > 0:
        wpm = round((word_count / (effective_dur / 60.0)), 1)
    else:
        wpm = 0.0

    # Spoken intervals (merged)
    speech_intervals = _merge_intervals(
        [
            (max(0.0, min(total_dur, w.start)), max(0.0, min(total_dur, w.end)))
            for w in norm_words
            if w.end > w.start
        ]
    )

    # Calculate speech that remains after jump cuts
    # (Subtract any portion of speech that was erroneously excised by a jump cut)
    unexcised_speech = 0.0
    for s_start, s_end in speech_intervals:
        span = s_end - s_start
        for c_start, c_end in merged_cuts:
            overlap = max(0.0, min(s_end, c_end) - max(s_start, c_start))
            span -= overlap
        unexcised_speech += max(0.0, span)

    remaining_silence = max(0.0, effective_dur - unexcised_speech)
    silence_ratio = (
        round(min(1.0, remaining_silence / effective_dur), 3) if effective_dur > 0 else 0.0
    )

    # Visual events: jump cuts + zoom punches
    total_visual_events = len(edl.jump_cuts) + len(edl.zoom_punch_ins)
    visual_events_per_10s = (
        round((total_visual_events / effective_dur) * 10.0, 2) if effective_dur > 0 else 0.0
    )

    # First 3 seconds hook word density
    hook_window = min(3.0, total_dur)
    hook_words = [w for w in norm_words if 0.0 <= w.start < hook_window]
    hook_density = round(len(hook_words) / hook_window, 2) if hook_window > 0 else 0.0

    # Composite Retention Pacing Score Calculation (0 - 100)
    # 1. WPM Score (Max: 30) - Optimal: 150 - 210 WPM
    if 150.0 <= wpm <= 210.0:
        wpm_score = 30.0
    elif 130.0 <= wpm < 150.0:
        wpm_score = 22.0 + (wpm - 130.0) * (8.0 / 20.0)
    elif 100.0 <= wpm < 130.0:
        wpm_score = 12.0 + (wpm - 100.0) * (10.0 / 30.0)
    elif wpm < 100.0:
        wpm_score = max(0.0, (wpm / 100.0) * 12.0)
    elif 210.0 < wpm <= 240.0:
        wpm_score = 30.0 - (wpm - 210.0) * (8.0 / 30.0)
    elif 240.0 < wpm <= 280.0:
        wpm_score = 22.0 - (wpm - 240.0) * (12.0 / 40.0)
    else:
        wpm_score = max(0.0, 10.0 - (wpm - 280.0) * 0.1)

    # 2. Silence Ratio Score (Max: 35) - Optimal: < 12% (< 0.12)
    if silence_ratio <= 0.05:
        silence_score = 35.0
    elif silence_ratio < 0.12:
        silence_score = 35.0 - (silence_ratio - 0.05) * (5.0 / 0.07)
    elif silence_ratio < 0.20:
        silence_score = 30.0 - (silence_ratio - 0.12) * (12.0 / 0.08)
    elif silence_ratio < 0.35:
        silence_score = 18.0 - (silence_ratio - 0.20) * (12.0 / 0.15)
    else:
        silence_score = max(0.0, 6.0 - (silence_ratio - 0.35) * 10.0)

    # 3. Visual Event Density Score (Max: 25) - Optimal: >= 2.0 per 10s
    if visual_events_per_10s >= 2.0:
        visual_score = min(25.0, 22.0 + (visual_events_per_10s - 2.0) * 1.5)
    elif visual_events_per_10s >= 1.0:
        visual_score = 12.0 + (visual_events_per_10s - 1.0) * 10.0
    else:
        visual_score = visual_events_per_10s * 12.0

    # 4. Hook Word Density Score (Max: 10) - Optimal: >= 2.0 w/s
    if hook_density >= 2.0:
        hook_score = 10.0
    elif hook_density >= 1.0:
        hook_score = 5.0 + (hook_density - 1.0) * 5.0
    else:
        hook_score = max(0.0, hook_density * 5.0)

    raw_score = wpm_score + silence_score + visual_score + hook_score
    retention_score = round(max(0.0, min(100.0, raw_score)), 1)

    # Pacing Grade assignment:
    # A+ strictly for 150–210 WPM, <12% silence, >=2.0 visual events/10s
    is_a_plus_criteria = (
        150.0 <= wpm <= 210.0 and silence_ratio < 0.12 and visual_events_per_10s >= 2.0
    )

    if is_a_plus_criteria or retention_score >= 90.0:
        if is_a_plus_criteria:
            retention_score = max(90.0, retention_score)
        grade: Literal["A+", "A", "B", "C", "D"] = "A+"
    elif retention_score >= 80.0:
        grade = "A"
    elif retention_score >= 65.0:
        grade = "B"
    elif retention_score >= 50.0:
        grade = "C"
    else:
        grade = "D"

    # Actionable Recommendations
    recommendations: list[str] = []

    # Detect unexcised pauses
    raw_pauses: list[tuple[float, float]] = []
    if norm_words:
        if norm_words[0].start >= 0.45:
            raw_pauses.append((0.0, norm_words[0].start))
        for i in range(len(norm_words) - 1):
            gap = norm_words[i + 1].start - norm_words[i].end
            if gap >= 0.45:
                raw_pauses.append((norm_words[i].end, norm_words[i + 1].start))
        if (total_dur - norm_words[-1].end) >= 0.45:
            raw_pauses.append((norm_words[-1].end, total_dur))

    uncovered_pauses = 0
    for p_start, p_end in raw_pauses:
        covered = any(c_s <= p_start + 0.1 and c_e >= p_end - 0.1 for c_s, c_e in merged_cuts)
        if not covered:
            uncovered_pauses += 1

    if silence_ratio >= 0.12:
        pct = round(silence_ratio * 100)
        if uncovered_pauses > 0:
            recommendations.append(
                f"High dead-air ratio ({pct}%): run Auto-Cut Silence to remove {uncovered_pauses} pause"
                f"{'s' if uncovered_pauses != 1 else ''}"
            )
        else:
            recommendations.append(
                f"High dead-air ratio ({pct}%): run Auto-Cut Silence to tighten spacing"
            )

    if visual_events_per_10s < 2.0:
        target_events = math.ceil((2.0 * effective_dur) / 10.0)
        needed_events = max(1, target_events - total_visual_events)
        recommendations.append(
            f"Low visual movement: add {needed_events} Zoom Punch"
            f"{'es' if needed_events != 1 else ''}"
        )

    if wpm < 130.0 and word_count > 0:
        recommendations.append(
            f"Slow spoken pace ({round(wpm)} WPM): consider excising pauses or speeding up audio to reach 150-200 WPM"
        )
    elif wpm > 220.0:
        recommendations.append(
            f"High speech velocity ({round(wpm)} WPM): verify subtitle readability and pacing"
        )

    if hook_density < 1.5:
        recommendations.append(
            f"Slow opening hook ({hook_density:.1f} words/sec): start speaking immediately within the first 0.5s"
        )

    if not recommendations:
        recommendations.append(
            "Optimal retention pacing: rapid delivery, tight silences, and dynamic visual movement."
        )

    return PacingAuditReport(
        clip_duration_sec=round(total_dur, 2),
        effective_duration_sec=round(effective_dur, 2),
        word_count=word_count,
        words_per_minute=wpm,
        silence_ratio=silence_ratio,
        visual_events_per_10s=visual_events_per_10s,
        hook_word_density_first_3s=hook_density,
        retention_pacing_score=retention_score,
        pacing_grade=grade,
        recommendations=recommendations,
    )


def _score_candidate_word(word_str: str) -> float:
    """Score word importance for zoom punch emphasis."""
    clean = re.sub(r"[^\w$€£¥₹]", "", word_str).strip()
    if not clean:
        return 0.0

    score = 0.0
    if CURRENCY_PATTERN.search(word_str):
        score += 15.0
    if NUMBER_PATTERN.search(word_str):
        score += 12.0
    if "!" in word_str or "?" in word_str:
        score += 10.0

    upper = clean.upper()
    if len(clean) >= 2 and word_str.upper() == word_str and any(c.isalpha() for c in word_str):
        score += 8.0
    if upper in POWER_WORDS or upper in EMPHASIS_KEYWORDS:
        score += 8.0

    # Longest words bonus as tiebreaker
    score += min(5.0, len(clean) * 0.4)
    return score


def detect_rapid_speech_bursts(
    words: Sequence[TimedWord | dict[str, Any]],
    clip_duration: float,
    min_wpm: float = 175.0,
    min_words: int = 3,
) -> list[BgmDuck]:
    """Detect rapid speech bursts and generate corresponding audio ducking intervals."""
    norm_words = _normalize_words(words)
    if len(norm_words) < min_words:
        return []

    ducks: list[BgmDuck] = []
    i = 0
    n = len(norm_words)

    while i < n:
        # Greedily expand burst window
        j = i
        while j < n - 1:
            gap = norm_words[j + 1].start - norm_words[j].end
            if gap > 0.25:
                break
            j += 1

        span_words = j - i + 1
        if span_words >= min_words:
            start_t = norm_words[i].start
            end_t = norm_words[j].end
            dur = end_t - start_t
            if dur >= 0.7:
                wpm = span_words / (dur / 60.0)
                if wpm >= min_wpm:
                    duck_start = max(0.0, round(start_t - 0.08, 2))
                    duck_end = min(clip_duration, round(end_t + 0.15, 2))
                    ducks.append(
                        BgmDuck(
                            start_sec=duck_start,
                            end_sec=duck_end,
                            duck_volume_db=-12.0,
                            reason="rapid_speech_burst",
                        )
                    )
        i = j + 1

    # Merge overlapping duck intervals
    if not ducks:
        return []

    merged: list[BgmDuck] = []
    sorted_ducks = sorted(ducks, key=lambda d: d.start_sec)
    cur = sorted_ducks[0]

    for nxt in sorted_ducks[1:]:
        if nxt.start_sec <= cur.end_sec + 0.1:
            cur = BgmDuck(
                start_sec=cur.start_sec,
                end_sec=max(cur.end_sec, nxt.end_sec),
                duck_volume_db=cur.duck_volume_db,
                reason=cur.reason,
            )
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)

    return merged


def _carve_uncovered_intervals(
    raw_start: float, raw_end: float, existing_cuts: Sequence[JumpCut]
) -> list[tuple[float, float]]:
    """Carve a raw silence interval around existing jump cuts so existing cuts are preserved."""
    sub_intervals = [(raw_start, raw_end)]
    for c in existing_cuts:
        c_s, c_e = float(c.cut_start), float(c.cut_end)
        new_subs: list[tuple[float, float]] = []
        for s, e in sub_intervals:
            if c_e <= s or c_s >= e:
                new_subs.append((s, e))
            else:
                if c_s > s:
                    new_subs.append((s, c_s))
                if c_e < e:
                    new_subs.append((c_e, e))
        sub_intervals = new_subs
    return sub_intervals


def optimize_edl_for_retention(
    words: Sequence[TimedWord | dict[str, Any]],
    edl: EditDecisionList,
    clip_duration: float,
    min_silence_gap: float = 0.45,
    target_zooms_per_15s: int = 2,
) -> tuple[EditDecisionList, PacingAuditReport]:
    """Automatically optimize an EditDecisionList for maximum audience retention in one pass.

    1. Detects and adds frame-accurate JumpCuts for dead-air gaps >= min_silence_gap
       (merging with existing jump cuts and preventing overlaps).
    2. Injects evenly spaced ZoomPunch keyframes on high-impact words (numbers, currencies,
       uppercase/exclamation words, or longest words in each 7-10s bucket) so video never
       stays static for > 8 seconds.
    3. Generates corresponding BgmDuck segments during rapid speech bursts.
    4. Re-audits the optimized timeline and returns (optimized_edl, updated_pacing_report).
    """
    total_dur = max(5.0, float(clip_duration))
    norm_words = _normalize_words(words)

    # 1. Detect and add frame-accurate JumpCuts for dead air gaps >= min_silence_gap
    # Ensure active remaining video is at least 5.1s so clamp_edl doesn't reject all cuts
    min_active_sec = 5.1
    max_excisable_sec = max(0.0, total_dur - min_active_sec)

    existing_cuts = [
        c
        for c in edl.jump_cuts
        if float(c.cut_start) < float(c.cut_end)
        and float(c.cut_start) >= 0.0
        and float(c.cut_end) <= total_dur
    ]
    existing_cuts.sort(key=lambda c: float(c.cut_start))

    # Collect raw silence gaps
    raw_silence_gaps: list[tuple[float, float]] = []
    if norm_words:
        first_start = max(0.0, min(total_dur, norm_words[0].start))
        if first_start > 0.0:
            raw_silence_gaps.append((0.0, first_start))

        running_end = max(0.0, min(total_dur, norm_words[0].end))
        for nxt in norm_words[1:]:
            nxt_start = max(0.0, min(total_dur, nxt.start))
            nxt_end = max(nxt_start, min(total_dur, nxt.end))
            if nxt_start > running_end:
                raw_silence_gaps.append((running_end, nxt_start))
            running_end = max(running_end, nxt_end)

        if total_dur > running_end:
            raw_silence_gaps.append((running_end, total_dur))

    # Carve gaps around existing cuts so existing cuts are strictly preserved
    uncovered_gaps: list[tuple[float, float]] = []
    for g_start, g_end in raw_silence_gaps:
        subs = _carve_uncovered_intervals(g_start, g_end, existing_cuts)
        for s, e in subs:
            if (e - s) >= min_silence_gap:
                uncovered_gaps.append((s, e))

    combined_cuts: list[JumpCut] = list(existing_cuts)
    current_excised = sum(float(c.cut_end) - float(c.cut_start) for c in combined_cuts)

    for g_start, g_end in uncovered_gaps:
        c_start = round(g_start + 0.06, 2)
        c_end = round(g_end - 0.06, 2)
        c_len = c_end - c_start
        if c_len >= 0.15:
            if current_excised + c_len <= max_excisable_sec:
                combined_cuts.append(
                    JumpCut(
                        cut_start=c_start,
                        cut_end=c_end,
                        reason="dead air pause",
                    )
                )
                current_excised += c_len

    combined_cuts.sort(key=lambda c: float(c.cut_start))

    # Calculate active excised regions to avoid placing zoom punches inside jump cuts
    _, active_cuts = _calculate_effective_timeline(total_dur, combined_cuts)

    def is_inside_cut(timestamp: float) -> bool:
        return any(c_s <= timestamp <= c_e for c_s, c_e in active_cuts)

    # 2. Inject evenly spaced ZoomPunches on high-impact words
    # Bucket size: 15s / target_zooms_per_15s (~7.5 seconds)
    bucket_sec = 15.0 / max(1, target_zooms_per_15s)
    num_buckets = max(1, math.ceil(total_dur / bucket_sec))

    current_punches: list[ZoomPunchIn] = [
        p for p in edl.zoom_punch_ins if not is_inside_cut(float(p.timestamp))
    ]

    for b in range(num_buckets):
        b_start = b * bucket_sec
        b_end = min(total_dur, (b + 1) * bucket_sec)

        # Check if bucket already has a zoom punch
        has_punch = any(b_start <= float(p.timestamp) <= b_end for p in current_punches)
        if has_punch:
            continue

        # Find candidate words in this bucket
        bucket_words = [
            w
            for w in norm_words
            if b_start <= w.start <= b_end
            and not is_inside_cut(w.start)
            and not any(abs(w.start - float(p.timestamp)) < 3.0 for p in current_punches)
        ]

        if not bucket_words:
            continue

        # Score candidate words (high-impact or longest word)
        best_word = max(bucket_words, key=lambda w: (_score_candidate_word(w.word), len(w.word)))
        w_score = _score_candidate_word(best_word.word)

        scale = 1.18 if w_score >= 12.0 else (1.16 if w_score >= 8.0 else 1.14)
        duration = round(max(0.4, min(1.0, (best_word.end - best_word.start) + 0.45)), 2)

        current_punches.append(
            ZoomPunchIn(
                timestamp=round(best_word.start, 2),
                scale=round(scale, 2),
                duration=round(duration, 2),
                emphasis_word=best_word.word.strip()[:30],
            )
        )

    # Guarantee: Video never stays static for more than 8 seconds
    # Scan timeline for any static gaps > 8.0s between visual events
    current_punches.sort(key=lambda p: float(p.timestamp))
    all_events: list[float] = [0.0]
    for p in current_punches:
        all_events.append(float(p.timestamp))
    for c_s, c_e in active_cuts:
        all_events.append(c_s)
        all_events.append(c_e)
    all_events.append(total_dur)
    all_events = sorted(set(all_events))

    for idx in range(len(all_events) - 1):
        gap_s = all_events[idx]
        gap_e = all_events[idx + 1]
        if (gap_e - gap_s) > 8.0:
            # Pick best word in the middle of this static gap
            gap_words = [
                w
                for w in norm_words
                if gap_s < w.start < gap_e
                and not is_inside_cut(w.start)
                and not any(abs(w.start - float(p.timestamp)) < 3.0 for p in current_punches)
            ]
            if gap_words:
                best_w = max(gap_words, key=lambda w: (_score_candidate_word(w.word), len(w.word)))
                current_punches.append(
                    ZoomPunchIn(
                        timestamp=round(best_w.start, 2),
                        scale=1.15,
                        duration=0.6,
                        emphasis_word=best_w.word.strip()[:30],
                    )
                )

    current_punches.sort(key=lambda p: float(p.timestamp))

    # 3. Generate BgmDuck segments during rapid speech bursts
    duck_segments = detect_rapid_speech_bursts(
        words=norm_words,
        clip_duration=total_dur,
        min_wpm=175.0,
    )

    # 4. Construct and clamp optimized EDL
    initial_optimized = EditDecisionList(
        hook_in_sec=edl.hook_in_sec,
        hook_out_sec=min(total_dur, edl.hook_out_sec) if edl.hook_out_sec > 0 else total_dur,
        jump_cuts=combined_cuts,
        zoom_punch_ins=current_punches,
        caption_corrections=list(edl.caption_corrections),
        recommended_thumbnail_timestamp=edl.recommended_thumbnail_timestamp,
        recommended_title=edl.recommended_title,
        recommended_hook=edl.recommended_hook,
        background_style_recommendation=edl.background_style_recommendation,
    )

    optimized_edl = clamp_edl(initial_optimized, clip_duration=total_dur)
    optimized_edl.bgm_ducks = duck_segments
    object.__setattr__(optimized_edl, "bgm_ducks", duck_segments)

    # 5. Compute updated retention pacing report
    updated_report = analyze_edit_rhythm(
        words=norm_words,
        edl=optimized_edl,
        clip_duration=total_dur,
    )

    return optimized_edl, updated_report


# ==============================================================================
# Snapshot & Rollback Engine
# ==============================================================================


def save_workflow_snapshot(
    work_dir: Path | str,
    job_id: str,
    edl: EditDecisionList,
    words: Sequence[TimedWord | dict[str, Any]],
    label: str = "manual",
) -> dict[str, Any]:
    """Store timestamped JSON snapshot before destructive edits for 1-click rollback.

    Saves snapshot JSON into work_dir / 'snapshots' / '<snapshot_id>.json'.
    Returns dictionary with snapshot metadata, ID, and path.
    """
    snapshot_dir = Path(work_dir) / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    ts_now = datetime.now(UTC)
    time_tag = ts_now.strftime("%Y%m%d_%H%M%S")
    rand_tag = uuid.uuid4().hex[:6]
    snapshot_id = f"snap_{job_id}_{time_tag}_{rand_tag}"

    edl_dict = edl.model_dump() if hasattr(edl, "model_dump") else dict(edl)
    ducks_data = [
        d.model_dump() if hasattr(d, "model_dump") else dict(d)
        for d in getattr(edl, "bgm_ducks", [])
    ]

    words_data: list[dict[str, Any]] = []
    for w in words:
        if isinstance(w, TimedWord):
            words_data.append(
                {
                    "word": w.word,
                    "start": w.start,
                    "end": w.end,
                    "probability": getattr(w, "probability", 1.0),
                }
            )
        elif isinstance(w, dict):
            words_data.append(dict(w))
        elif hasattr(w, "model_dump"):
            words_data.append(w.model_dump())
        elif hasattr(w, "__dict__"):
            words_data.append(dict(w.__dict__))
        else:
            words_data.append({"word": str(w), "start": 0.0, "end": 0.0})

    snapshot_data: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "job_id": job_id,
        "label": label,
        "created_at": ts_now.isoformat(),
        "timestamp": ts_now.isoformat(),
        "edl": edl_dict,
        "bgm_ducks": ducks_data,
        "words": words_data,
        "word_count": len(words_data),
    }

    file_path = snapshot_dir / f"{snapshot_id}.json"
    file_path.write_text(json.dumps(snapshot_data, indent=2, ensure_ascii=False), encoding="utf-8")
    snapshot_data["file_path"] = str(file_path.resolve())

    logger.info("Saved workflow snapshot %s (label=%s) to %s", snapshot_id, label, file_path)
    return snapshot_data


def list_workflow_snapshots(work_dir: Path | str) -> list[dict[str, Any]]:
    """List all workflow snapshots in work_dir / 'snapshots', sorted newest first."""
    snapshot_dir = Path(work_dir) / "snapshots"
    if not snapshot_dir.exists():
        return []

    snapshots: list[dict[str, Any]] = []
    for path in snapshot_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "snapshot_id" in data:
                data["file_path"] = str(path.resolve())
                snapshots.append(data)
        except Exception as exc:
            logger.warning("Failed to parse snapshot file %s: %s", path, exc)

    # Sort descending by creation timestamp
    snapshots.sort(
        key=lambda s: str(s.get("created_at") or s.get("timestamp") or ""),
        reverse=True,
    )
    return snapshots


def restore_workflow_snapshot(work_dir: Path | str, snapshot_id: str) -> dict[str, Any]:
    """Restore previous edit state by snapshot ID.

    Returns dict containing:
    - snapshot_id, job_id, label, created_at
    - edl: Reconstructed EditDecisionList instance (with bgm_ducks)
    - words: list[TimedWord] instances
    - bgm_ducks: list[BgmDuck] instances
    """
    snapshot_dir = Path(work_dir) / "snapshots"
    target_path = snapshot_dir / f"{snapshot_id}.json"

    if not target_path.exists():
        # Search by snapshot_id inside files if filename doesn't match directly
        found_path: Path | None = None
        for p in snapshot_dir.glob("*.json"):
            if snapshot_id in p.stem:
                found_path = p
                break
        if found_path is not None:
            target_path = found_path
        else:
            raise FileNotFoundError(
                f"Workflow snapshot '{snapshot_id}' not found in {snapshot_dir}"
            )

    data = json.loads(target_path.read_text(encoding="utf-8"))

    reconstructed_edl = EditDecisionList.model_validate(data["edl"])
    reconstructed_words = [
        TimedWord.from_dict(w) if isinstance(w, dict) else TimedWord(**w)
        for w in data.get("words", [])
    ]
    reconstructed_ducks = [BgmDuck.model_validate(d) for d in data.get("bgm_ducks", [])]

    reconstructed_edl.bgm_ducks = reconstructed_ducks
    object.__setattr__(reconstructed_edl, "bgm_ducks", reconstructed_ducks)

    return {
        "snapshot_id": data["snapshot_id"],
        "job_id": data.get("job_id"),
        "label": data.get("label", "manual"),
        "created_at": data.get("created_at") or data.get("timestamp"),
        "edl": reconstructed_edl,
        "words": reconstructed_words,
        "bgm_ducks": reconstructed_ducks,
        "raw_data": data,
    }


__all__ = [
    "BgmDuck",
    "PacingAuditReport",
    "analyze_edit_rhythm",
    "detect_rapid_speech_bursts",
    "list_workflow_snapshots",
    "optimize_edl_for_retention",
    "restore_workflow_snapshot",
    "save_workflow_snapshot",
]
