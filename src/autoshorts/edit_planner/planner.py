"""Dead-Air Silence Auto-Trimmer, Smart Zoom Punch Generator, and Enhanced Edit Planner.

Provides deterministic word-timestamp gap analysis to excise dead air pauses,
heuristic emphasis word detection for dynamic zoom punch-ins, and an enhanced
MultimodalEditPlanner.create_edl workflow that merges dead-air cuts when the
LLM returns zero jump cuts.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.edit_planner.edl_schema import clamp_edl
from autoshorts.edit_planner.multimodal_planner import (
    PRIVACY_NOTICE,
    build_edl_prompt,
    probe_clip_duration,
)
from autoshorts.edit_planner.multimodal_planner import (
    MultimodalEditPlanner as _BaseMultimodalEditPlanner,
)
from autoshorts.media.ass_styler import EMPHASIS_KEYWORDS, TimedWord

logger = logging.getLogger(__name__)

# Alias ZoomPunch to ZoomPunchIn for ergonomic imports
ZoomPunch = ZoomPunchIn

# Ensure JumpCut also exposes .start and .end properties for compatibility
if not hasattr(JumpCut, "start"):
    JumpCut.start = property(lambda self: self.cut_start)  # type: ignore[attr-defined]
if not hasattr(JumpCut, "end"):
    JumpCut.end = property(lambda self: self.cut_end)  # type: ignore[attr-defined]

_OriginalJumpCutInit = JumpCut.__init__


def _flexible_jump_cut_init(self: JumpCut, **data: Any) -> None:
    if "start" in data and "cut_start" not in data:
        data["cut_start"] = data.pop("start")
    if "end" in data and "cut_end" not in data:
        data["cut_end"] = data.pop("end")
    _OriginalJumpCutInit(self, **data)


JumpCut.__init__ = _flexible_jump_cut_init  # type: ignore[method-assign]

POWER_WORDS: set[str] = set(EMPHASIS_KEYWORDS) | {
    "CRITICAL",
    "MASSIVE",
    "IMPOSSIBLE",
    "EXACTLY",
    "FINALLY",
    "PROVEN",
    "MISTAKE",
    "DESTROY",
    "UNBELIEVABLE",
    "WARNING",
    "WORST",
    "ULTIMATE",
    "INSTANTLY",
    "GUARANTEED",
    "TERRIFYING",
    "GENIUS",
    "BANNED",
    "ILLEGAL",
    "EXPLODED",
    "SECRETLY",
}


def _normalize_words(words: Sequence[TimedWord | dict[str, Any]]) -> list[TimedWord]:
    """Convert a sequence of TimedWord or dict objects into sorted TimedWord instances."""
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
        normalized.append(w)

    normalized.sort(key=lambda x: (x.start, x.end))
    return normalized


def compute_adaptive_silence_threshold(
    words: Sequence[TimedWord | dict[str, Any]],
    clip_duration: float,
    base_min_gap: float = 0.48,
) -> float:
    """Compute adaptive silence trimming threshold based on speaker cadence.

    Analyzes words per second (WPS) / speech tempo:
    - Fast energetic speech (>= 2.8 WPS / >= 170 WPM): tight threshold 0.40s - 0.44s
    - Conversational speech (2.0 - 2.5 WPS / 120 - 150 WPM): standard 0.48s - 0.50s
    - Deliberate / slow speech (<= 1.5 WPS / <= 90 WPM): relaxed threshold 0.55s - 0.60s

    Returns dynamic threshold in [0.40, 0.60].
    """
    sorted_words = _normalize_words(words)
    if not sorted_words or clip_duration <= 1.0:
        return base_min_gap

    word_count = len(sorted_words)
    first_w = sorted_words[0].start
    last_w = sorted_words[-1].end
    spoken_span = max(1.0, last_w - first_w)

    wps = word_count / spoken_span

    if wps >= 3.0:
        threshold = 0.40
    elif wps <= 1.4:
        threshold = 0.60
    else:
        # Linear interpolation between 1.4 wps (0.60s) and 3.0 wps (0.40s)
        ratio = (wps - 1.4) / (3.0 - 1.4)
        threshold = 0.60 - ratio * 0.20

    return round(max(0.40, min(0.60, threshold)), 2)


def detect_dead_air_cuts(
    words: Sequence[TimedWord | dict[str, Any]],
    clip_duration: float = 60.0,
    min_gap_sec: float | None = None,
    padding_sec: float = 0.08,
    max_cuts: int = 12,
    adaptive: bool = True,
) -> list[JumpCut]:
    """Scan consecutive word timestamps and generate safe JumpCuts for dead-air silence gaps.

    Scans:
    - Leading silence before word[0].start (0.0 -> word[0].start)
    - Inter-word gaps (word[i].end -> word[i+1].start)
    - Trailing silence after word[-1].end (word[-1].end -> clip_duration)

    When min_gap_sec is None, adaptively calculates the pause threshold based on speaker
    cadence (0.40s - 0.60s dynamic threshold).
    Whenever a silence gap exceeds the threshold, generates a padded JumpCut
    (start = gap_start + padding_sec, end = gap_end - padding_sec) while ensuring
    at least 3.0s of active video remains and cuts are valid and non-overlapping.
    """
    total_dur = float(clip_duration)
    if not words or total_dur <= 3.0 or max_cuts <= 0:
        return []

    min_active_video_sec = 3.0
    max_excisable_sec = max(0.0, total_dur - min_active_video_sec)
    if max_excisable_sec <= 0.0:
        return []

    sorted_words = _normalize_words(words)
    if not sorted_words:
        return []

    # Determine effective silence threshold
    if min_gap_sec is not None:
        effective_gap = float(min_gap_sec)
    elif adaptive:
        effective_gap = compute_adaptive_silence_threshold(sorted_words, total_dur)
    else:
        effective_gap = 0.48

    # Collect raw silence intervals: (gap_start, gap_end, reason)
    raw_gaps: list[tuple[float, float, str]] = []

    # 1. Leading silence before first word
    first_start = max(0.0, min(total_dur, sorted_words[0].start))
    if first_start > 0.0:
        raw_gaps.append((0.0, first_start, "leading silence"))

    # 2. Internal gaps between consecutive words
    running_end = max(0.0, min(total_dur, sorted_words[0].end))
    for nxt in sorted_words[1:]:
        nxt_start = max(0.0, min(total_dur, nxt.start))
        nxt_end = max(nxt_start, min(total_dur, nxt.end))
        if nxt_start > running_end:
            raw_gaps.append((running_end, nxt_start, "dead air pause"))
        running_end = max(running_end, nxt_end)

    # 3. Trailing silence after last word
    if total_dur > running_end:
        raw_gaps.append((running_end, total_dur, "trailing silence"))

    cuts: list[JumpCut] = []
    excised_so_far = 0.0
    last_cut_end = 0.0

    for gap_start, gap_end, reason in raw_gaps:
        if len(cuts) >= max_cuts:
            break

        gap_len = gap_end - gap_start
        if gap_len <= effective_gap:
            continue

        cut_start = max(0.0, gap_start + max(0.0, padding_sec))
        cut_end = min(total_dur, gap_end - max(0.0, padding_sec))

        cut_start = max(cut_start, last_cut_end)

        cut_len = cut_end - cut_start
        if cut_len < 0.10:
            continue

        # Ensure at least 3.0s of active video remains
        if excised_so_far + cut_len > max_excisable_sec:
            allowed_len = max_excisable_sec - excised_so_far
            if allowed_len >= 0.10:
                cut_end = cut_start + allowed_len
                cut_len = allowed_len
            else:
                break

        cuts.append(
            JumpCut(
                cut_start=round(cut_start, 2),
                cut_end=round(cut_end, 2),
                reason=reason,
            )
        )
        excised_so_far += cut_len
        last_cut_end = cut_end

    return cuts


def _score_word_emphasis(raw_word: str) -> float | None:
    """Return target zoom scale in [1.12, 1.18] if raw_word is an emphasis word, else None."""
    stripped = raw_word.strip()
    if not stripped:
        return None

    clean_alpha = re.sub(r"[^A-Za-z0-9]", "", stripped)
    if not clean_alpha:
        return None

    upper_clean = clean_alpha.upper()
    has_exclamation = "!" in stripped or "?" in stripped
    has_number = any(ch.isdigit() for ch in clean_alpha)
    is_all_caps = (
        len(clean_alpha) >= 2
        and stripped.upper() == stripped
        and any(ch.isalpha() for ch in stripped)
    )
    is_power_keyword = upper_clean in POWER_WORDS
    is_long_word = len(clean_alpha) >= 8

    if is_power_keyword and (has_exclamation or is_all_caps):
        return 1.18
    if is_all_caps or has_exclamation:
        return 1.17
    if is_power_keyword:
        return 1.16
    if has_number:
        return 1.15
    if is_long_word:
        return 1.12

    return None


def generate_smart_zoom_punches(
    words: Sequence[TimedWord | dict[str, Any]],
    clip_duration: float,
    max_punches: int = 5,
    min_spacing_sec: float = 3.5,
) -> list[ZoomPunch]:
    """Place dynamic zoom punches (1.12x-1.18x) on power/emphasis words spaced >= 3.5s apart."""
    total_dur = float(clip_duration)
    if not words or total_dur <= 1.0 or max_punches <= 0:
        return []

    sorted_words = _normalize_words(words)
    punches: list[ZoomPunch] = []
    last_punch_ts: float | None = None

    for w in sorted_words:
        if len(punches) >= max_punches:
            break

        if w.start < 0.0 or w.start > total_dur:
            continue

        scale = _score_word_emphasis(w.word)
        if scale is None:
            continue

        if last_punch_ts is not None and (w.start - last_punch_ts) < min_spacing_sec:
            continue

        clamped_scale = max(1.12, min(1.18, scale))
        word_span = max(0.3, min(1.2, (w.end - w.start) + 0.45))

        punches.append(
            ZoomPunchIn(
                timestamp=round(w.start, 2),
                scale=round(clamped_scale, 2),
                duration=round(word_span, 2),
                emphasis_word=w.word.strip()[:30],
            )
        )
        last_punch_ts = w.start

    return punches


class MultimodalEditPlanner(_BaseMultimodalEditPlanner):
    """Enhanced Multimodal AI Edit Planner with Dead-Air Silence Auto-Trimmer support."""

    def create_edl(
        self,
        video_clip_path: Path | str | None = None,
        transcript_text: str | None = None,
        instructions: str | None = None,
        words: Sequence[TimedWord | dict[str, Any]] | None = None,
        auto_trim_silence: bool = False,
        clip_duration: float | None = None,
    ) -> EditDecisionList:
        """Create an EditDecisionList and optionally merge dead-air cuts when LLM returns 0 jump cuts."""
        target_path = Path(video_clip_path) if video_clip_path is not None else Path("clip.mp4")
        resolved_duration = (
            float(clip_duration)
            if clip_duration is not None and clip_duration > 0
            else probe_clip_duration(target_path, default=self.default_duration)
        )

        edl = super().generate_edl(
            video_clip_path=target_path,
            transcript_text=transcript_text,
            instructions=instructions,
        )

        if auto_trim_silence and words and len(edl.jump_cuts) == 0:
            effective_dur = edl.hook_out_sec if edl.hook_out_sec > 0 else resolved_duration
            auto_cuts = detect_dead_air_cuts(words=words, clip_duration=effective_dur)
            # Filter cuts to lie within [hook_in_sec, hook_out_sec]
            bounded_cuts = [
                c
                for c in auto_cuts
                if c.cut_start >= edl.hook_in_sec and c.cut_end <= edl.hook_out_sec
            ]
            if bounded_cuts:
                edl = edl.model_copy(update={"jump_cuts": bounded_cuts})
                # Clamp if span is long enough for clamp_edl's 5s threshold, else keep bounded_cuts
                if (edl.hook_out_sec - edl.hook_in_sec) >= 8.0:
                    clamped = clamp_edl(edl, clip_duration=resolved_duration)
                    if clamped.jump_cuts:
                        edl = clamped

        return edl

    def generate_edl(
        self,
        video_clip_path: Path,
        transcript_text: str | None = None,
        instructions: str | None = None,
        words: Sequence[TimedWord | dict[str, Any]] | None = None,
        auto_trim_silence: bool = False,
    ) -> EditDecisionList:
        """Generate EDL with optional word-level dead-air auto-trimming."""
        return self.create_edl(
            video_clip_path=video_clip_path,
            transcript_text=transcript_text,
            instructions=instructions,
            words=words,
            auto_trim_silence=auto_trim_silence,
        )


# Attach create_edl onto _BaseMultimodalEditPlanner so existing imports of MultimodalEditPlanner
# from autoshorts.edit_planner.multimodal_planner also gain create_edl() and words/auto_trim_silence support.
_BaseMultimodalEditPlanner.create_edl = MultimodalEditPlanner.create_edl  # type: ignore[attr-defined]


__all__ = [
    "PRIVACY_NOTICE",
    "JumpCut",
    "MultimodalEditPlanner",
    "ZoomPunch",
    "ZoomPunchIn",
    "build_edl_prompt",
    "compute_adaptive_silence_threshold",
    "detect_dead_air_cuts",
    "generate_smart_zoom_punches",
    "probe_clip_duration",
]
