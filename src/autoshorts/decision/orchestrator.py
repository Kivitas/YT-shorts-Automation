"""SemIf Orchestrator and Decider implementation.

Implements autoshorts.core.contracts.Decider.
Provides consolidated single-prompt JSON evaluation, cross-agent request bundling
(BatchDecisionCoalescer), binary (Approach A) and multi-candidate (Approach B) window
scoring, Semantic-IF action table execution, non-overlapping clip selection, heuristic
fallback, and generative metadata helpers.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autoshorts.core.config import Settings, load_config
from autoshorts.core.contracts import Decider, LLMProvider
from autoshorts.core.exceptions import DeciderError
from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
)
from autoshorts.decision.semif_engine import (
    SEMIF_ACTION_TABLE,
    build_semif_prompt,
    evaluate_heuristic,
)
from autoshorts.decision.window_slicer import (
    HOOK_FIRST_SCORE_BONUS,
    classify_semantic_hook,
    compute_narrative_completeness_score,
    select_non_overlapping_candidates,
)

if TYPE_CHECKING:
    from autoshorts.decider.performance_prior import PerformancePrior

logger = logging.getLogger("autoshorts.decision.orchestrator")


def _heuristic_window_score(text: str, duration: float) -> float:
    """Rule-based window score with the hook-first bonus for windows that open with a hook."""
    score = evaluate_heuristic(text, duration)
    if classify_semantic_hook(text)["is_hook"]:
        score = min(1.0, score + HOOK_FIRST_SCORE_BONUS)
    return score


def _heuristic_background_style(text: str) -> str:
    """Rule-based background category selection ('subway' or 'minecraft')."""
    lower = text.lower()
    gaming_keywords = {"game", "fast", "speed", "crazy", "funny", "laugh", "meme"}
    if any(k in lower for k in gaming_keywords):
        return "subway"
    return "minecraft"


def _normalize_background_style(raw: Any, fallback_text: str = "") -> str:
    """Normalize background category string from LLM JSON to 'subway' or 'minecraft'."""
    val = str(raw or "").strip().lower()
    if "subway" in val or val == "a":
        return "subway"
    if "minecraft" in val or val == "b":
        return "minecraft"
    return _heuristic_background_style(fallback_text)


def _format_hook_fallback(transcript_text: str) -> str:
    """Rule-based fallback hook question or first sentence (max 8 words)."""
    sentences = re.split(r"(?<=[.?!])\s+", transcript_text.strip())
    for s in sentences:
        if "?" in s:
            return s.strip()
    if sentences and sentences[0].strip():
        words = sentences[0].split()
        return " ".join(words[:8])
    return "Watch Until The End!"


def _clean_hook_text(raw_hook: Any, fallback_text: str) -> str:
    """Sanitize LLM hook text or fall back to deterministic excerpt hook."""
    hook = str(raw_hook or "").replace('"', "").replace("'", "").strip()
    if hook:
        return hook
    return _format_hook_fallback(fallback_text)


def _format_title_fallback(transcript_text: str) -> str:
    """Rule-based fallback title under 60 chars ending with #Shorts."""
    words = transcript_text.strip().split()
    first_chunk = " ".join(words[:6])
    clean_chunk = re.sub(r"[^\w\s]", "", first_chunk).title() or "Viral Moment"
    return f"{clean_chunk[:45]} #Shorts"


def _clean_short_title(raw_title: Any, fallback_text: str) -> str:
    """Sanitize LLM title to <= 60 chars ending with #Shorts."""
    title = str(raw_title or "").replace('"', "").replace("'", "").strip()
    if not title:
        return _format_title_fallback(fallback_text)
    if not title.endswith("#Shorts"):
        title = f"{title[:45].strip()} #Shorts"
    if len(title) > 60:
        title = f"{title[:50].rsplit(' ', 1)[0].strip()} #Shorts"
    return title


@dataclass
class _WindowDecisionMeta:
    """Cached per-window decision outputs from a consolidated LLM prompt."""

    score: float
    reason: str
    background_category: str
    hook_text: str
    title: str
    source: str = "llm"


@dataclass
class _PendingJobDecision:
    """A single job's candidate evaluation request waiting in BatchDecisionCoalescer."""

    request_id: str
    job_id: str
    candidates: list[ClipCandidate]
    instructions: str
    done_event: threading.Event = field(default_factory=threading.Event)
    result: dict[int, _WindowDecisionMeta] = field(default_factory=dict)
    selected_window_index: int | None = None
    error: Exception | None = None


def _extract_json_object(raw_text: str) -> dict[str, Any] | list[Any] | None:
    """Extract and parse a JSON object or array from raw LLM completion text."""
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    # Strip markdown ```json ... ``` fences if present
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, (dict, list)):
            return parsed
    except Exception:
        pass
    # Try locating outermost { ... }
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            parsed = json.loads(cleaned[first_brace : last_brace + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return None


class BatchDecisionCoalescer:
    """Bundles concurrent job decision requests from parallel workers into 1 LLM call.

    Local stages (metadata, transcript, window slicing, heuristic pre-filtering) run first
    on each worker thread. Parallel workers register themselves as inflight while running
    local stages. When workers reach the SemIf LLM stage, the coalescer waits briefly for
    sibling inflight workers to arrive, bundles all jobs' candidate windows into a single
    structured JSON prompt, sends 1 request to Gemini, and splits the response back to each
    waiting worker thread.
    """

    def __init__(
        self,
        debounce_sec: float = 0.06,
        max_wait_sec: float = 8.0,
    ) -> None:
        self.debounce_sec = debounce_sec
        self.max_wait_sec = max_wait_sec
        self._cond = threading.Condition()
        self._inflight_jobs: set[str] = set()
        self._pending_by_provider: dict[int, list[_PendingJobDecision]] = {}
        self._leader_active: set[int] = set()

    def register_inflight_job(self, job_id: str) -> None:
        """Mark a job as currently executing local pre-LLM stages (Stages 1-3)."""
        if not job_id:
            return
        with self._cond:
            self._inflight_jobs.add(job_id)

    def unregister_inflight_job(self, job_id: str) -> None:
        """Remove a job from the local pre-LLM inflight set and wake waiting batch leaders."""
        if not job_id:
            return
        with self._cond:
            if job_id in self._inflight_jobs:
                self._inflight_jobs.discard(job_id)
                self._cond.notify_all()

    def reset(self) -> None:
        """Clear inflight and pending state (useful for isolated tests)."""
        with self._cond:
            self._inflight_jobs.clear()
            self._pending_by_provider.clear()
            self._leader_active.clear()
            self._cond.notify_all()

    def submit_job_decisions(
        self,
        provider: LLMProvider,
        job_id: str | None,
        candidates: Sequence[ClipCandidate],
        instructions: str,
    ) -> dict[int, _WindowDecisionMeta]:
        """Queue a job's candidate windows for consolidated LLM evaluation and wait for its split result."""
        effective_job_id = job_id or f"job_{uuid.uuid4().hex[:8]}"
        req = _PendingJobDecision(
            request_id=uuid.uuid4().hex[:10],
            job_id=effective_job_id,
            candidates=list(candidates),
            instructions=instructions,
        )
        provider_key = id(provider)

        is_leader = False
        with self._cond:
            # This job has finished local work and arrived at the LLM barrier
            self._inflight_jobs.discard(effective_job_id)
            bucket = self._pending_by_provider.setdefault(provider_key, [])
            bucket.append(req)
            if provider_key not in self._leader_active:
                self._leader_active.add(provider_key)
                is_leader = True
            self._cond.notify_all()

        if is_leader:
            self._run_leader_loop(provider, provider_key)
        else:
            req.done_event.wait()

        if req.error is not None:
            raise req.error
        return req.result

    def _run_leader_loop(self, provider: LLMProvider, provider_key: int) -> None:
        """Wait for concurrent/inflight jobs, execute a single bundled LLM call, and dispatch results."""
        while True:
            with self._cond:
                deadline = time.monotonic() + self.max_wait_sec
                # 1. Always wait a tiny debounce window so parallel threads entering together join
                if self.debounce_sec > 0:
                    self._cond.wait(timeout=self.debounce_sec)

                # 2. If other parallel jobs are still in local stages (metadata/transcript/slicing),
                # wait for them to reach the barrier up to max_wait_sec.
                while self._inflight_jobs:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(timeout=min(remaining, 0.25))

                # 3. Brief settle if an inflight job just unregistered and is about to append
                if self.debounce_sec > 0:
                    self._cond.wait(timeout=min(self.debounce_sec * 0.5, 0.03))

                batch = list(self._pending_by_provider.get(provider_key, []))
                self._pending_by_provider[provider_key] = []
                if not batch:
                    self._leader_active.discard(provider_key)
                    return

            try:
                self._execute_batch(provider, batch)
            except Exception as exc:
                for item in batch:
                    item.error = exc
                    item.done_event.set()
            finally:
                for item in batch:
                    if not item.done_event.is_set():
                        item.done_event.set()

            with self._cond:
                if not self._pending_by_provider.get(provider_key):
                    self._leader_active.discard(provider_key)
                    return

    def _execute_batch(self, provider: LLMProvider, batch: list[_PendingJobDecision]) -> None:
        """Build 1 consolidated prompt for all jobs in ``batch``, call ``provider.complete``, and split."""
        if len(batch) > 1:
            logger.info(
                "Bundling LLM decisions for %d parallel jobs (%s) into 1 consolidated request.",
                len(batch),
                ", ".join(req.job_id for req in batch),
            )

        prompt = self._build_consolidated_prompt(batch)
        total_windows = sum(len(req.candidates) for req in batch)
        max_tokens = min(8192, max(1024, 320 * max(1, total_windows)))

        raw_response = provider.complete(
            prompt,
            max_tokens=max_tokens,
            temperature=0.1,
            response_mime_type="application/json",
        )
        parsed = _extract_json_object(raw_response)

        if parsed is None:
            # Handle plain-text MockProvider or non-JSON completion gracefully without extra complete() calls
            self._populate_non_json_fallback(provider, batch, raw_response)
            return

        jobs_map = self._extract_jobs_map(parsed, batch)
        for idx, req in enumerate(batch):
            job_payload = (
                jobs_map.get(req.request_id)
                or jobs_map.get(req.job_id)
                or jobs_map.get(str(idx))
                or {}
            )
            req.result = self._parse_job_decisions(req, job_payload)
            req.done_event.set()

    def _build_consolidated_prompt(self, batch: list[_PendingJobDecision]) -> str:
        """Construct a single structured JSON prompt covering one or multiple jobs."""
        if len(batch) == 1:
            req = batch[0]
            windows_block = []
            for idx, cand in enumerate(req.candidates):
                windows_block.append(
                    f"Window {idx}: [{cand.start:.1f}s - {cand.end:.1f}s] ({cand.duration:.1f}s)\n"
                    f"Transcript: {cand.text[:450]}"
                )
            windows_str = "\n\n".join(windows_block)
            return (
                "You are an expert viral YouTube Shorts director. Evaluate all candidate transcript windows "
                "for this job in a SINGLE pass and return a single structured JSON object.\n\n"
                f"Job ID: {req.job_id}\n"
                f"Operator Instructions: {req.instructions}\n\n"
                f"Candidate Windows:\n{windows_str}\n\n"
                "Return ONLY valid JSON matching this exact structure:\n"
                "{\n"
                f'  "job_id": "{req.job_id}",\n'
                '  "selected_window_index": 0,\n'
                '  "background_category": "subway" or "minecraft",\n'
                '  "hook_text": "1-sentence curiosity hook banner question (max 8 words)",\n'
                '  "title": "Punchy YouTube Short title under 50 chars ending with #Shorts",\n'
                '  "windows": [\n'
                "    {\n"
                '      "index": 0,\n'
                '      "score": 0.85,\n'
                '      "reason": "Brief reason for viral retention score",\n'
                '      "background_category": "subway" or "minecraft",\n'
                '      "hook_text": "Max 8-word hook banner text",\n'
                '      "title": "Punchy title ending with #Shorts"\n'
                "    }\n"
                "  ]\n"
                "}"
            )

        job_sections = []
        for j_idx, req in enumerate(batch):
            win_lines = []
            for w_idx, cand in enumerate(req.candidates):
                win_lines.append(
                    f"  Window {w_idx}: [{cand.start:.1f}s - {cand.end:.1f}s] ({cand.duration:.1f}s) | "
                    f"Transcript: {cand.text[:400]}"
                )
            job_sections.append(
                f"=== JOB {j_idx} (job_id: \"{req.job_id}\", request_id: \"{req.request_id}\") ===\n"
                f"Instructions: {req.instructions}\n"
                + "\n".join(win_lines)
            )

        return (
            "You are an expert viral YouTube Shorts director. Evaluate the candidate transcript windows "
            "for ALL bundled parallel jobs below and return a SINGLE structured JSON object.\n\n"
            + "\n\n".join(job_sections)
            + "\n\nReturn ONLY valid JSON matching this exact structure:\n"
            "{\n"
            '  "jobs": [\n'
            "    {\n"
            '      "job_id": "<job_id>",\n'
            '      "request_id": "<request_id>",\n'
            '      "selected_window_index": 0,\n'
            '      "background_category": "subway" or "minecraft",\n'
            '      "hook_text": "1-sentence curiosity hook banner question (max 8 words)",\n'
            '      "title": "Punchy YouTube Short title under 50 chars ending with #Shorts",\n'
            '      "windows": [\n'
            "        {\n"
            '          "index": 0,\n'
            '          "score": 0.85,\n'
            '          "reason": "Brief reason for viral retention score",\n'
            '          "background_category": "subway" or "minecraft",\n'
            '          "hook_text": "Max 8-word hook banner text",\n'
            '          "title": "Punchy title ending with #Shorts"\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}"
        )

    def _extract_jobs_map(
        self,
        parsed: dict[str, Any] | list[Any],
        batch: list[_PendingJobDecision],
    ) -> dict[str, dict[str, Any]]:
        """Normalize single-job or multi-job JSON response into a lookup map by job_id/request_id/index."""
        result: dict[str, dict[str, Any]] = {}
        if isinstance(parsed, list):
            jobs_list = [item for item in parsed if isinstance(item, dict)]
        elif isinstance(parsed, dict):
            if isinstance(parsed.get("jobs"), list):
                jobs_list = [item for item in parsed["jobs"] if isinstance(item, dict)]
            elif len(batch) == 1:
                jobs_list = [parsed]
            else:
                # Support dict keyed by job_id directly
                for k, v in parsed.items():
                    if isinstance(v, dict):
                        result[str(k)] = v
                jobs_list = []
        else:
            jobs_list = []

        for idx, item in enumerate(jobs_list):
            result[str(idx)] = item
            if item.get("job_id") is not None:
                result[str(item["job_id"])] = item
            if item.get("request_id") is not None:
                result[str(item["request_id"])] = item
        if len(batch) == 1 and "0" not in result and isinstance(parsed, dict):
            result["0"] = parsed
            result[batch[0].job_id] = parsed
            result[batch[0].request_id] = parsed
        return result

    def _parse_job_decisions(
        self,
        req: _PendingJobDecision,
        job_payload: dict[str, Any],
    ) -> dict[int, _WindowDecisionMeta]:
        """Extract per-window scores and metadata for a single job from its parsed JSON block."""
        selected_idx_raw = job_payload.get("selected_window_index")
        selected_idx: int | None = None
        if isinstance(selected_idx_raw, int) and 0 <= selected_idx_raw < len(req.candidates):
            selected_idx = selected_idx_raw
        req.selected_window_index = selected_idx

        top_bg = job_payload.get("background_category")
        top_hook = job_payload.get("hook_text")
        top_title = job_payload.get("title")

        windows_raw = job_payload.get("windows")
        win_by_index: dict[int, dict[str, Any]] = {}
        if isinstance(windows_raw, list):
            for pos, w in enumerate(windows_raw):
                if isinstance(w, dict):
                    w_idx = w.get("index", pos)
                    try:
                        w_idx_int = int(w_idx)
                    except (TypeError, ValueError):
                        w_idx_int = pos
                    win_by_index[w_idx_int] = w

        decisions: dict[int, _WindowDecisionMeta] = {}
        for idx, cand in enumerate(req.candidates):
            w_data = win_by_index.get(idx, {})
            raw_score = w_data.get("score")
            if raw_score is None and idx == selected_idx:
                raw_score = job_payload.get("score", 0.88)

            if raw_score is not None:
                try:
                    score = float(raw_score)
                    # Normalize 0-100 scale if LLM returned percentage
                    if score > 1.0 and score <= 100.0:
                        score = score / 100.0
                    score = max(0.0, min(1.0, score))
                    reason_detail = str(w_data.get("reason") or "selected by consolidated LLM")
                    reason = f"SemIf consolidated score: {score:.3f} ({reason_detail})"
                    source = "llm"
                except (TypeError, ValueError):
                    score = _heuristic_window_score(cand.text, cand.duration)
                    reason = f"Heuristic fallback: {score:.3f}"
                    source = "heuristic"
            else:
                score = _heuristic_window_score(cand.text, cand.duration)
                if selected_idx is not None and idx == selected_idx:
                    score = max(score, 0.85)
                    reason = f"SemIf consolidated selected window: {score:.3f}"
                    source = "llm"
                else:
                    reason = f"Heuristic fallback: {score:.3f}"
                    source = "heuristic"

            bg = _normalize_background_style(
                w_data.get("background_category") or top_bg,
                fallback_text=cand.text,
            )
            hook = _clean_hook_text(
                w_data.get("hook_text") or (top_hook if idx == (selected_idx or 0) else None),
                fallback_text=cand.text,
            )
            title = _clean_short_title(
                w_data.get("title") or (top_title if idx == (selected_idx or 0) else None),
                fallback_text=cand.text,
            )
            decisions[idx] = _WindowDecisionMeta(
                score=score,
                reason=reason,
                background_category=bg,
                hook_text=hook,
                title=title,
                source=source,
            )
        return decisions

    def _populate_non_json_fallback(
        self,
        provider: LLMProvider,
        batch: list[_PendingJobDecision],
        raw_response: str,
    ) -> None:
        """Fallback when provider.complete returns plain text (e.g. MockProvider in unit tests)."""
        cleaned_raw = (raw_response or "").strip()
        for req in batch:
            decisions: dict[int, _WindowDecisionMeta] = {}
            for idx, cand in enumerate(req.candidates):
                score = _heuristic_window_score(cand.text, cand.duration)
                reason = f"Heuristic fallback: {score:.3f}"
                source = "heuristic"
                try:
                    probs = provider.score_options(
                        f"Evaluate window [{cand.start:.1f}s-{cand.end:.1f}s]: {cand.text[:200]}",
                        {"A": "Accept", "B": "Reject"},
                    )
                    if probs and "A" in probs:
                        score = float(probs["A"])
                        reason = f"SemIf binary score: P(Accept)={score:.3f}"
                        source = "llm"
                except Exception:
                    pass

                bg = _heuristic_background_style(cand.text)
                hook = _format_hook_fallback(cand.text)
                title = (
                    _clean_short_title(cleaned_raw, cand.text)
                    if cleaned_raw
                    else _format_title_fallback(cand.text)
                )
                decisions[idx] = _WindowDecisionMeta(
                    score=score,
                    reason=reason,
                    background_category=bg,
                    hook_text=hook,
                    title=title,
                    source=source,
                )
            req.result = decisions
            req.done_event.set()


_GLOBAL_DECISION_COALESCER = BatchDecisionCoalescer()


def get_decision_coalescer() -> BatchDecisionCoalescer:
    """Return the shared process-wide BatchDecisionCoalescer instance."""
    return _GLOBAL_DECISION_COALESCER


def _fuse_candidate_score(
    semif_score: float,
    heatmap_score: float,
    hook_strength: float,
    narrative_completeness: float = 1.0,
) -> float:
    """Fuse LLM SemIf probability score with heatmap, hook curiosity, and narrative completeness signals.

    Applies a score penalty if narrative_completeness indicates an abrupt cutoff (< 0.85).
    Preserves exact deterministic behavior when heatmap_score == 0.0 and hook_strength == 0.0.
    """

    def _unit(value: Any, default: float = 0.0) -> float:
        # min(1.0, nan) is 1.0, so a NaN score would otherwise become a perfect score.
        try:
            number = float(value) if value is not None else default
        except (TypeError, ValueError):
            return default
        if not math.isfinite(number):
            return default
        return max(0.0, min(1.0, number))

    hm = _unit(heatmap_score)
    hk = _unit(hook_strength)
    sf = _unit(semif_score)
    nc = _unit(narrative_completeness, default=1.0)

    if hm > 0.0:
        composite = 0.72 * sf + 0.18 * hm + 0.10 * hk
    elif hk > 0.0:
        composite = 0.90 * sf + 0.10 * hk
    else:
        composite = sf

    # Apply penalty for narrative incompleteness / mid-sentence cutoff
    if nc < 0.85 and (hm > 0.0 or hk > 0.0):
        penalty = (0.85 - nc) * 0.25
        composite = max(0.0, composite - penalty)

    return round(max(0.0, min(1.0, composite)), 4)


class SemIfDecider(Decider):
    """Primary pipeline decision orchestrator driven by SemIf evaluation.

    Complies with Decider protocol. Supports consolidated single-prompt JSON evaluation
    with cross-agent request bundling (default), binary scoring (Approach A),
    multi-candidate scoring (Approach B), state machine action decisions, and fallback heuristics.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        settings: Settings | None = None,
        scoring_approach: str = "consolidated",  # "consolidated", "binary" (Approach A), or "multi" (Approach B)
        coalescer: BatchDecisionCoalescer | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or load_config()
        self.scoring_approach = scoring_approach
        self.coalescer = coalescer or get_decision_coalescer()
        self._cache_lock = threading.Lock()
        self._consolidated_cache: dict[tuple[str | None, float, float], _WindowDecisionMeta] = {}
        self._thread_local = threading.local()

    def set_current_job_context(self, job_id: str | None) -> None:
        """Bind the active job_id to thread-local storage for consolidated batching."""
        self._thread_local.job_id = job_id

    def _get_current_job_id(self, explicit_job_id: str | None = None) -> str | None:
        if explicit_job_id is not None:
            return explicit_job_id
        return getattr(self._thread_local, "job_id", None)

    def _cache_window_meta(
        self,
        job_id: str | None,
        cand: ClipCandidate,
        meta: _WindowDecisionMeta,
    ) -> None:
        key_pair = (round(cand.start, 2), round(cand.end, 2))
        with self._cache_lock:
            if len(self._consolidated_cache) > 2000:
                self._consolidated_cache.clear()
            self._consolidated_cache[(job_id, key_pair[0], key_pair[1])] = meta
            self._consolidated_cache[(None, key_pair[0], key_pair[1])] = meta

    def _lookup_window_meta(
        self,
        job_id: str | None,
        cand: ClipCandidate,
    ) -> _WindowDecisionMeta | None:
        key_pair = (round(cand.start, 2), round(cand.end, 2))
        with self._cache_lock:
            if job_id is not None:
                found = self._consolidated_cache.get((job_id, key_pair[0], key_pair[1]))
                if found is not None:
                    return found
            return self._consolidated_cache.get((None, key_pair[0], key_pair[1]))

    def score_candidates(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
    ) -> list[ClipCandidate]:
        """Evaluate and rank candidate transcript windows.

        Dispatches to consolidated single-prompt JSON evaluation (default), binary Approach A,
        or multi-candidate Approach B. Falls back to rule-based heuristic scoring if no
        provider is configured.
        """
        if not candidates:
            return []

        if self.scoring_approach == "consolidated":
            return self.score_candidates_consolidated(candidates, instructions)
        if self.scoring_approach == "multi" and len(candidates) > 1:
            return self.score_candidates_multicandidate(candidates, instructions)
        return self.score_candidates_binary(candidates, instructions)

    def score_candidates_consolidated(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
        max_llm_candidates: int | None = None,
        *,
        job_id: str | None = None,
    ) -> list[ClipCandidate]:
        """Evaluate candidate windows, background category, hook banner, and title in 1 consolidated prompt."""
        scored: list[ClipCandidate] = []
        user_prompt = instructions or self.settings.job_defaults.focus_prompt
        active_job_id = self._get_current_job_id(job_id)

        if max_llm_candidates is None:
            clips_target = getattr(self.settings.job_defaults, "clips_per_video", 1) or 1
            max_llm_candidates = max(6, int(clips_target) * 2)

        eligible_indices: list[int] = []
        if self.provider is not None and len(candidates) > max_llm_candidates:
            ranked_indices = sorted(
                range(len(candidates)),
                key=lambda idx: (
                    candidates[idx].heatmap_score * 0.5
                    + candidates[idx].hook_strength * 0.3
                    + evaluate_heuristic(candidates[idx].text, candidates[idx].duration) * 0.2
                ),
                reverse=True,
            )
            eligible_indices = sorted(ranked_indices[:max_llm_candidates])
        elif self.provider is not None:
            eligible_indices = list(range(len(candidates)))

        eligible_set = set(eligible_indices)

        if self.provider is None:
            logger.info(
                "Window scoring fallback: no LLM provider, heuristic scores for %d candidate(s)",
                len(candidates),
            )
        elif len(eligible_set) < len(candidates):
            logger.info(
                "Window scoring fallback: %d candidate(s) outside top-%d LLM set scored heuristically",
                len(candidates) - len(eligible_set),
                max_llm_candidates,
            )

        llm_decisions_by_orig_idx: dict[int, _WindowDecisionMeta] = {}
        if self.provider is not None and eligible_indices:
            llm_candidates = [candidates[i] for i in eligible_indices]
            try:
                batch_meta = self.coalescer.submit_job_decisions(
                    provider=self.provider,
                    job_id=active_job_id,
                    candidates=llm_candidates,
                    instructions=user_prompt,
                )
                for sub_idx, orig_idx in enumerate(eligible_indices):
                    if sub_idx in batch_meta:
                        meta = batch_meta[sub_idx]
                        llm_decisions_by_orig_idx[orig_idx] = meta
                        self._cache_window_meta(active_job_id, candidates[orig_idx], meta)
            except Exception as e:
                logger.warning("Consolidated LLM scoring failed, falling back to heuristics: %s", e)

        for idx, cand in enumerate(candidates):
            if idx in llm_decisions_by_orig_idx:
                meta = llm_decisions_by_orig_idx[idx]
                score = meta.score
                reason = meta.reason
            elif self.provider is None:
                score = _heuristic_window_score(cand.text, cand.duration)
                reason = f"Heuristic (no provider): {score:.3f}"
                self._cache_window_meta(
                    active_job_id,
                    cand,
                    _WindowDecisionMeta(
                        score=score,
                        reason=reason,
                        background_category=_heuristic_background_style(cand.text),
                        hook_text=_format_hook_fallback(cand.text),
                        title=_format_title_fallback(cand.text),
                        source="heuristic",
                    ),
                )
            else:
                score = _heuristic_window_score(cand.text, cand.duration)
                reason = f"Heuristic: {score:.3f}"
                self._cache_window_meta(
                    active_job_id,
                    cand,
                    _WindowDecisionMeta(
                        score=score,
                        reason=reason,
                        background_category=_heuristic_background_style(cand.text),
                        hook_text=_format_hook_fallback(cand.text),
                        title=_format_title_fallback(cand.text),
                        source="heuristic",
                    ),
                )

            nc = compute_narrative_completeness_score(cand.text)
            fused_score = _fuse_candidate_score(
                score, cand.heatmap_score, cand.hook_strength, narrative_completeness=nc
            )
            if nc < 0.85 and (cand.heatmap_score > 0.0 or cand.hook_strength > 0.0):
                reason = f"{reason} | narrative cutoff ({nc:.2f})"
            scored.append(
                ClipCandidate(
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=fused_score,
                    reason=reason,
                    heatmap_score=cand.heatmap_score,
                    hook_strength=cand.hook_strength,
                    chapter_title=cand.chapter_title,
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def score_candidates_binary(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
        max_llm_candidates: int = 6,
    ) -> list[ClipCandidate]:
        """Approach A: Evaluate candidate windows using binary SemIf logits with fast pre-filtering."""
        scored: list[ClipCandidate] = []
        user_prompt = instructions or self.settings.job_defaults.focus_prompt

        # Determine which candidates qualify for LLM scoring (top candidates by heatmap & hook)
        eligible_for_llm: set[int] = set()
        if self.provider is not None and len(candidates) > max_llm_candidates:
            ranked_indices = sorted(
                range(len(candidates)),
                key=lambda idx: (
                    candidates[idx].heatmap_score * 0.5
                    + candidates[idx].hook_strength * 0.3
                    + evaluate_heuristic(candidates[idx].text, candidates[idx].duration) * 0.2
                ),
                reverse=True,
            )
            eligible_for_llm = set(ranked_indices[:max_llm_candidates])
        elif self.provider is not None:
            eligible_for_llm = set(range(len(candidates)))

        if self.provider is None:
            logger.info(
                "Window scoring fallback: no LLM provider, heuristic scores for %d candidate(s)",
                len(candidates),
            )
        elif len(eligible_for_llm) < len(candidates):
            logger.info(
                "Window scoring fallback: %d candidate(s) outside top-%d LLM set scored heuristically",
                len(candidates) - len(eligible_for_llm),
                max_llm_candidates,
            )

        for idx, cand in enumerate(candidates):
            score = 0.0
            reason = "Heuristic fallback evaluation"

            if self.provider is not None and idx in eligible_for_llm:
                try:
                    context = {
                        "Candidate Window": f"[{cand.start:.1f}s - {cand.end:.1f}s] ({cand.duration:.1f}s)",
                        "Transcript Excerpt": cand.text,
                        "Operator Instructions": user_prompt,
                    }
                    question = "Does this excerpt represent a high-retention, engaging standalone viral moment?"
                    options = {
                        "A": "High retention, engaging standalone viral moment",
                        "B": "Low retention, dull, or incomplete context",
                    }
                    prompt = build_semif_prompt(question, options, context=context)
                    probs = self.provider.score_options(prompt, options)
                    score = float(probs.get("A", 0.0))
                    reason = f"SemIf binary score: P(Accept)={score:.3f}"
                except Exception as e:
                    logger.warning(
                        "Provider scoring failed for window [%.1f-%.1f]: %s",
                        cand.start,
                        cand.end,
                        e,
                    )
                    score = _heuristic_window_score(cand.text, cand.duration)
                    reason = f"Heuristic fallback: {score:.3f}"
            elif self.provider is None:
                score = _heuristic_window_score(cand.text, cand.duration)
                reason = f"Heuristic (no provider): {score:.3f}"
            else:
                score = _heuristic_window_score(cand.text, cand.duration)
                reason = f"Heuristic: {score:.3f}"

            nc = compute_narrative_completeness_score(cand.text)
            fused_score = _fuse_candidate_score(
                score, cand.heatmap_score, cand.hook_strength, narrative_completeness=nc
            )
            if nc < 0.85 and (cand.heatmap_score > 0.0 or cand.hook_strength > 0.0):
                reason = f"{reason} | narrative cutoff ({nc:.2f})"
            scored.append(
                ClipCandidate(
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=fused_score,
                    reason=reason,
                    heatmap_score=cand.heatmap_score,
                    hook_strength=cand.hook_strength,
                    chapter_title=cand.chapter_title,
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def score_candidates_multicandidate(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
    ) -> list[ClipCandidate]:
        """Approach B: Joint categorical comparative evaluation across candidate windows."""
        user_prompt = instructions or self.settings.job_defaults.focus_prompt
        # Limit comparison batches to at most 5 at a time
        batch = list(candidates[:5])
        letters = [chr(ord("A") + i) for i in range(len(batch))]
        options = {
            letter: f"[{cand.start:.1f}s - {cand.end:.1f}s]: {cand.text[:90]}..."
            for letter, cand in zip(letters, batch, strict=False)
        }

        context = {
            "Operator Instructions": user_prompt,
            "Total Candidates": len(batch),
        }
        question = "Which candidate transcript excerpt has the highest viral potential and strongest retention hook?"

        scores_by_letter: dict[str, float] = {}
        if self.provider is not None:
            try:
                prompt = build_semif_prompt(question, options, context=context)
                scores_by_letter = self.provider.score_options(prompt, options)
            except Exception as e:
                logger.warning("Multi-candidate SemIf scoring failed: %s", e)
        else:
            logger.info("Multi-candidate fallback: no LLM provider, heuristic scores for batch")

        scored: list[ClipCandidate] = []
        for i, cand in enumerate(batch):
            let = letters[i]
            if let in scores_by_letter:
                s = scores_by_letter[let]
                reason = f"SemIf multicandidate score: {s:.3f}"
            else:
                s = evaluate_heuristic(cand.text, cand.duration)
                reason = f"Heuristic fallback: {s:.3f}"

            nc = compute_narrative_completeness_score(cand.text)
            fused_s = _fuse_candidate_score(
                s, cand.heatmap_score, cand.hook_strength, narrative_completeness=nc
            )
            scored.append(
                ClipCandidate(
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=fused_s,
                    reason=reason,
                    heatmap_score=cand.heatmap_score,
                    hook_strength=cand.hook_strength,
                    chapter_title=cand.chapter_title,
                )
            )

        # Append remaining candidates evaluated via heuristic
        if len(candidates) > 5:
            logger.info(
                "Multi-candidate fallback: %d candidate(s) beyond the 5-item batch scored heuristically",
                len(candidates) - 5,
            )
        for cand in candidates[5:]:
            s = evaluate_heuristic(cand.text, cand.duration)
            nc = compute_narrative_completeness_score(cand.text)
            fused_s = _fuse_candidate_score(
                s, cand.heatmap_score, cand.hook_strength, narrative_completeness=nc
            )
            scored.append(
                ClipCandidate(
                    start=cand.start,
                    end=cand.end,
                    text=cand.text,
                    score=fused_s,
                    reason="Heuristic fallback",
                    heatmap_score=cand.heatmap_score,
                    hook_strength=cand.hook_strength,
                    chapter_title=cand.chapter_title,
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored

    def select_clips(
        self,
        candidates: Sequence[ClipCandidate],
        count: int = 1,
        *,
        job_id: str | None = None,
        performance_prior: PerformancePrior | None = None,
    ) -> list[ClipSelection]:
        """Select top non-overlapping candidate segments and attach styling parameters.

        An active ``performance_prior`` adjusts candidate scores from historical analytics
        before ranking. ``None`` or an inactive prior leaves the ranking unchanged.
        """
        if not candidates or count <= 0:
            return []

        # If candidates are unscored (all 0.0), score them first
        if all(c.score == 0.0 for c in candidates):
            candidates = self.score_candidates(candidates)
        else:
            # Ensure heatmap/hook fusion is applied if candidates have pre-populated semif scores + heatmap/hook
            candidates = [
                ClipCandidate(
                    start=c.start,
                    end=c.end,
                    text=c.text,
                    score=_fuse_candidate_score(
                        c.score,
                        c.heatmap_score,
                        c.hook_strength,
                        narrative_completeness=compute_narrative_completeness_score(c.text),
                    ),
                    reason=c.reason,
                    heatmap_score=c.heatmap_score,
                    hook_strength=c.hook_strength,
                    chapter_title=c.chapter_title,
                )
                for c in candidates
            ]

        active_prior: PerformancePrior | None = None
        if performance_prior is not None and performance_prior.active:
            active_prior = performance_prior
        if active_prior is not None:
            try:
                candidates = active_prior.apply(candidates)
            except Exception as err:  # advisory signal: a prior bug must never block selection
                logger.warning("Performance prior skipped: %s", err)
                active_prior = None

        chosen_candidates = select_non_overlapping_candidates(candidates, count=count)
        selections: list[ClipSelection] = []
        active_job_id = self._get_current_job_id(job_id)

        if self.scoring_approach == "consolidated" and self.provider is not None:
            uncached = [
                c
                for c in chosen_candidates
                if self._lookup_window_meta(active_job_id, c) is None
            ]
            if uncached:
                try:
                    batch_meta = self.coalescer.submit_job_decisions(
                        provider=self.provider,
                        job_id=active_job_id,
                        candidates=uncached,
                        instructions=self.settings.job_defaults.focus_prompt,
                    )
                    for idx, c in enumerate(uncached):
                        if idx in batch_meta:
                            self._cache_window_meta(active_job_id, c, batch_meta[idx])
                except Exception as e:
                    logger.warning(
                        "Consolidated metadata evaluation failed for uncached clip(s): %s", e
                    )

        from autoshorts.decider.records import DecisionRecord, record_decision

        for i, cand in enumerate(chosen_candidates):
            cached_meta = (
                self._lookup_window_meta(active_job_id, cand)
                if self.scoring_approach == "consolidated"
                else None
            )

            if cached_meta is not None:
                bg_style = cached_meta.background_category
                hook_text = cached_meta.hook_text
                title = cached_meta.title
                chosen_letter = "A" if bg_style == "subway" else "B"
                record_decision(
                    DecisionRecord(
                        job_id=job_id,
                        name="background_category",
                        value=bg_style,
                        reason=f"choice {chosen_letter} via {cached_meta.source}",
                        source=cached_meta.source,  # type: ignore[arg-type]
                    )
                )
            elif self.scoring_approach == "consolidated" and self.provider is None:
                bg_style = _heuristic_background_style(cand.text)
                hook_text = _format_hook_fallback(cand.text)
                title = _format_title_fallback(cand.text)
                chosen_letter = "A" if bg_style == "subway" else "B"
                record_decision(
                    DecisionRecord(
                        job_id=job_id,
                        name="background_category",
                        value=bg_style,
                        reason=f"choice {chosen_letter} via heuristic",
                        source="heuristic",
                    )
                )
            else:
                # Legacy per-decision path (for explicit binary/multi modes or fallback)
                bg_style = self.decide_action(
                    "BACKGROUND_SELECTION",
                    {"text": cand.text, "duration": cand.duration},
                    job_id=job_id,
                )
                hook_text = self.generate_hook(cand.text)
                title = self.generate_title(cand.text)

            record_decision(
                DecisionRecord(
                    job_id=job_id,
                    name="clip_window",
                    value=f"{cand.start:.2f}-{cand.end:.2f}",
                    reason=cand.reason or "unspecified",
                    source="llm" if self.provider is not None else "heuristic",
                )
            )

            if active_prior is not None:
                try:
                    prior_adj = active_prior.score_adjustment(cand.duration, cand.text)
                    record_decision(
                        DecisionRecord(
                            job_id=job_id,
                            name="performance_prior",
                            value=f"{prior_adj.value:+.4f}",
                            reason=(
                                "matched " + ", ".join(prior_adj.matched)
                                if prior_adj.matched
                                else "no matching historical group"
                            ),
                            source="analytics",
                        )
                    )
                except Exception as err:  # audit trail only; never block the selection
                    logger.debug("Performance prior decision record skipped: %s", err)

            title_variants = self.generate_title_variants(
                cand.text,
                base_title=title,
                chapter_title=cand.chapter_title,
            )

            selections.append(
                ClipSelection(
                    candidate=cand,
                    clip_index=i,
                    target_duration=max(
                        1.0,
                        min(cand.duration, float(self.settings.job_defaults.max_duration_seconds)),
                    ),
                    background_style=bg_style,
                    hook_text=hook_text,
                    title=title,
                    title_variants=title_variants,
                )
            )

        return selections

    def decide_action(
        self,
        action_key: str,
        context: Mapping[str, Any],
        *,
        job_id: str | None = None,
    ) -> str:
        """Execute a Semantic-IF decision prompt and return the chosen action identifier.

        Maps chosen option letters to pipeline state machine transitions per PLAN.md 5.3.
        """
        table_entry = SEMIF_ACTION_TABLE.get(action_key)
        if not table_entry:
            raise DeciderError(f"Unknown Semantic-IF action key: '{action_key}'")

        question = table_entry["question"]
        options = table_entry["options"]
        action_fn = table_entry["action"]

        chosen_letter = "A"
        source = "llm"

        if self.provider is not None:
            try:
                prompt = build_semif_prompt(question, options, context=context)
                probs = self.provider.score_options(prompt, options)
                if probs:
                    chosen_letter = max(probs.items(), key=lambda item: item[1])[0]
                else:
                    source = "default"
                    logger.warning(
                        "Decider fallback for '%s': provider returned no scores, defaulting to 'A'",
                        action_key,
                    )
            except Exception as e:
                logger.warning("Decider failed evaluating '%s': %s", action_key, e)
                source = "heuristic"
                chosen_letter = self._heuristic_action_fallback(action_key, context)
        else:
            source = "heuristic"
            logger.info(
                "Decider fallback for '%s': no LLM provider, using rule-based choice",
                action_key,
            )
            chosen_letter = self._heuristic_action_fallback(action_key, context)

        # Execute action handler to map choice to pipeline string
        result_action = action_fn(chosen_letter, context)
        logger.info(
            "Decider action '%s' -> choice '%s' => '%s'", action_key, chosen_letter, result_action
        )

        from autoshorts.decider.records import DecisionRecord, record_decision

        record_decision(
            DecisionRecord(
                job_id=job_id,
                name="background_category"
                if action_key == "BACKGROUND_SELECTION"
                else action_key.lower(),
                value=str(result_action),
                reason=f"choice {chosen_letter} via {source}",
                source=source,  # type: ignore[arg-type]
            )
        )
        return str(result_action)

    def _heuristic_action_fallback(
        self,
        action_key: str,
        context: Mapping[str, Any],
    ) -> str:
        """Rule-based heuristic choice when no LLM provider is available."""
        if action_key == "BACKGROUND_SELECTION":
            text = str(context.get("text", "")).lower()
            # Fast gaming / humor keywords -> Subway Surfers (A), else Minecraft (B)
            gaming_keywords = {"game", "fast", "speed", "crazy", "funny", "laugh", "meme"}
            if any(k in text for k in gaming_keywords):
                return "A"
            return "B"

        elif action_key == "HOOK_STYLE":
            text = str(context.get("text", ""))
            return "A" if "?" in text else "B"

        elif action_key == "WINDOW_SELECTION":
            text = str(context.get("text", ""))
            dur = float(context.get("duration", 45.0))
            score = _heuristic_window_score(text, dur)
            return "A" if score >= 0.5 else "B"

        elif action_key == "UPLOAD_VERIFICATION_GATE":
            return "A"

        logger.warning(
            "Decider fallback for '%s': no heuristic rule, defaulting to 'A'", action_key
        )
        return "A"

    # -------------------------------------------------------------------------
    # Generative LLM Helpers per PLAN.md section 5.4
    # -------------------------------------------------------------------------

    def generate_title(self, transcript_text: str, channel: str | None = None) -> str:
        """Generate a punchy YouTube Shorts title under 60 characters ending with #Shorts."""
        if self.provider is not None:
            try:
                prompt = (
                    "You are a viral YouTube Shorts editor. "
                    "Write a punchy, highly engaging title under 50 characters for this excerpt. "
                    "End with '#Shorts'. Return ONLY the title text and nothing else.\n\n"
                    f"Excerpt: {transcript_text[:300]}"
                )
                title = self.provider.complete(prompt, max_tokens=32, temperature=0.3).strip()
                # Clean quotes and formatting
                title = title.replace('"', "").replace("'", "").strip()
                if not title.endswith("#Shorts"):
                    title = f"{title[:45]} #Shorts"
                if len(title) > 60:
                    title = f"{title[:50].rsplit(' ', 1)[0]} #Shorts"
                return title
            except Exception as e:
                logger.warning("Generative title failed: %s", e)

        # Rule-based fallback title
        words = transcript_text.strip().split()
        first_chunk = " ".join(words[:6])
        clean_chunk = re.sub(r"[^\w\s]", "", first_chunk).title()
        return f"{clean_chunk[:45]} #Shorts"

    def generate_title_variants(
        self,
        transcript_text: str,
        base_title: str | None = None,
        chapter_title: str | None = None,
    ) -> list[str]:
        """Generate 3 distinct viral title angles: Curiosity, Contrarian, and Direct Punch."""
        words = [
            re.sub(r"[^\w]", "", w)
            for w in transcript_text.strip().split()
            if re.sub(r"[^\w]", "", w)
        ]
        topic = (
            chapter_title.strip()
            if chapter_title and chapter_title.strip()
            else (" ".join(words[:4]).title() if words else "This Secret")
        )
        topic_short = topic[:24].strip()

        curiosity = f"Why Does {topic_short} Matter? #Shorts"
        contrarian = f"The Truth About {topic_short} #Shorts"
        direct_punch = (
            base_title.strip()
            if base_title and base_title.strip()
            else f"{topic_short}: What To Know #Shorts"
        )
        if not direct_punch.endswith("#Shorts"):
            direct_punch = f"{direct_punch[:48].strip()} #Shorts"
        if direct_punch in {curiosity, contrarian}:
            direct_punch = f"{topic_short} Explained Fast #Shorts"

        return [curiosity[:60], contrarian[:60], direct_punch[:60]]

    def generate_hook(self, transcript_text: str) -> str:
        """Generate a bold 3-second hook question or headline."""
        if self.provider is not None:
            try:
                prompt = (
                    "Write a 1-sentence curiosity hook question (max 8 words) "
                    "for the first 3 seconds of a YouTube Short based on this excerpt. "
                    "Return ONLY the hook question.\n\n"
                    f"Excerpt: {transcript_text[:300]}"
                )
                hook = self.provider.complete(prompt, max_tokens=24, temperature=0.3).strip()
                hook = hook.replace('"', "").replace("'", "").strip()
                if hook:
                    return hook
                logger.info("Hook fallback: LLM returned an empty hook, using excerpt text")
            except Exception as e:
                logger.warning("Generative hook failed: %s", e)
        else:
            logger.info("Hook fallback: no LLM provider, using excerpt text as hook")

        # Fallback hook: first sentence or question
        sentences = re.split(r"(?<=[.?!])\s+", transcript_text.strip())
        for s in sentences:
            if "?" in s:
                return s.strip()
        if sentences:
            words = sentences[0].split()
            return " ".join(words[:8])
        logger.warning("Hook fallback: empty excerpt, using default hook text")
        return "Watch Until The End!"

    def generate_description_hook(self, transcript_text: str) -> str:
        """Generate a 2-sentence curiosity-inducing summary."""
        if self.provider is not None:
            try:
                prompt = (
                    "Write a 2-sentence engaging summary hook for this video excerpt. "
                    "Return ONLY the 2 sentences.\n\n"
                    f"Excerpt: {transcript_text[:400]}"
                )
                desc = self.provider.complete(prompt, max_tokens=60, temperature=0.3).strip()
                if desc:
                    return desc
            except Exception as e:
                logger.warning("Generative description failed: %s", e)

        # Fallback: clean first 2 sentences
        sentences = re.split(r"(?<=[.?!])\s+", transcript_text.strip())
        return " ".join(s.strip() for s in sentences[:2] if s.strip())

    def generate_hashtags(self, transcript_text: str, count: int = 4) -> str:
        """Generate semantic hashtags for video description."""
        if self.provider is not None:
            try:
                prompt = (
                    f"Extract {count} trending YouTube Shorts hashtags for this content. "
                    "Include '#Shorts'. Separate tags by a single space. Return ONLY the hashtags.\n\n"
                    f"Content: {transcript_text[:300]}"
                )
                tags = self.provider.complete(prompt, max_tokens=30, temperature=0.3).strip()
                if tags and "#" in tags:
                    return tags
            except Exception as e:
                logger.warning("Generative hashtags failed: %s", e)

        return "#Shorts #Viral #Podcasts #Education"
