"""Master SuperBrain Orchestrator.

Coordinates multi-agent swarm in a 3-stage DAG:
  Phase 1: Ingestion & Research (InfoResearcherAgent / WindowSlicer)
  Phase 2: Quality Gate (HookCriticAgent with decoupled Mode A / Mode B)
  Phase 3: Bounded parallel swarm (MusicScoutAgent, ViralSeoAgent, RetentionEditorAgent via asyncio.Semaphore(2))

Enforces Zero-Phantom-Data preflight credentials check and lazy pipeline runner injection.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, Literal
from uuid import uuid4

from autoshorts.brain.schemas import (
    AudioPlan,
    MasterBrainState,
    ResearchIntel,
    RetentionPlan,
    SEOMetadata,
    ViralMasterPlan,
)
from autoshorts.brain.specialists.hook_critic import HookCriticAgent
from autoshorts.brain.specialists.info_researcher import InfoResearcherAgent
from autoshorts.brain.specialists.music_scout import MusicScoutAgent
from autoshorts.brain.specialists.retention_editor import RetentionEditorAgent
from autoshorts.brain.specialists.viral_seo import ViralSeoAgent
from autoshorts.core.config import Settings, load_config
from autoshorts.core.settings_manager import check_llm_provider_configured

logger = logging.getLogger("autoshorts.brain.orchestrator")


class PreflightCredentialError(ValueError):
    """Raised when required credentials are missing (Zero-Phantom-Data Invariant)."""

    def __init__(
        self,
        service: str,
        message: str,
        missing_key_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.message = message
        self.missing_key_name = missing_key_name
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "preflight_credential_error",
            "service": self.service,
            "message": self.message,
            "missing_key_name": self.missing_key_name,
            "details": self.details,
        }


class MasterBrainOrchestrator:
    """Master orchestrator running the 3-stage multi-agent DAG."""

    def __init__(
        self,
        config: Settings | None = None,
        pipeline_runner: Any = None,
        max_concurrent_swarm: int = 2,
    ) -> None:
        self.config = config or load_config()
        self._pipeline_runner = pipeline_runner
        self._swarm_semaphore = asyncio.Semaphore(max_concurrent_swarm)

        # Specialist swarm
        self.info_researcher = InfoResearcherAgent(config=self.config)
        self.hook_critic = HookCriticAgent()
        self.music_scout = MusicScoutAgent()
        self.viral_seo = ViralSeoAgent()
        self.retention_editor = RetentionEditorAgent()

        # In-memory plan cache
        self._plans: dict[str, ViralMasterPlan] = {}

    def set_pipeline_runner(self, runner: Any) -> None:
        """Lazily inject pipeline runner callback or execution engine."""
        self._pipeline_runner = runner

    def get_pipeline_runner(self) -> Any:
        """Retrieve lazily injected pipeline runner or instantiate default pipeline."""
        if self._pipeline_runner is not None:
            return self._pipeline_runner
        # Lazy import to avoid circular dependency
        from autoshorts.pipeline import ShortsPipeline

        return ShortsPipeline(self.config)

    def preflight_check(self, mode: str = "mode_b") -> None:
        """Enforce zero-phantom-data invariant: verify valid credentials before synthesis."""
        norm_mode = mode.lower()
        if norm_mode == "mode_b":
            # Generative synthesis strictly requires an active LLM provider
            is_configured, key_name, message = check_llm_provider_configured(self.config)
            if not is_configured:
                logger.error("Preflight check failed for Mode B: %s (%s)", message, key_name)
                raise PreflightCredentialError(
                    service="llm",
                    message=message,
                    missing_key_name=key_name,
                    details={
                        "provider": getattr(
                            getattr(self.config, "llm", None), "provider", "unknown"
                        )
                    },
                )

    def get_plan(self, plan_id: str) -> ViralMasterPlan | None:
        """Retrieve cached master plan by plan_id."""
        return self._plans.get(plan_id)

    async def orchestrate(
        self,
        mode: Literal["mode_a", "mode_b"] | str = "mode_b",
        # Mode B options
        topic: str | None = None,
        niche: str = "general",
        # Mode A options
        video_url: str | None = None,
        transcript_slice: str | None = None,
        candidate_windows: list[tuple[float, float]] | None = None,
        candidate_transcripts: list[str] | None = None,
        # Common options
        custom_instructions: str | None = None,
        skip_preflight: bool = False,
    ) -> ViralMasterPlan:
        """Execute the 3-stage DAG workflow across specialist agents."""
        norm_mode: Literal["mode_a", "mode_b"] = (
            "mode_a" if mode in ("mode_a", "repurpose") else "mode_b"
        )
        plan_id = str(uuid4())

        # Zero-Phantom-Data Preflight Check
        if not skip_preflight:
            self.preflight_check(mode=norm_mode)

        plan = ViralMasterPlan(
            plan_id=plan_id,
            mode=norm_mode,
            niche=niche,
            topic=topic or "",
            source_url=video_url,
            status="processing",
        )
        self._plans[plan_id] = plan

        state = MasterBrainState(
            plan_id=plan_id,
            mode=norm_mode,
            phase="phase1_context",
            master_plan=plan,
            is_credential_verified=True,
        )

        try:
            if norm_mode == "mode_b":
                plan = await self._run_mode_b_dag(
                    plan=plan,
                    state=state,
                    topic=topic,
                    niche=niche,
                    custom_instructions=custom_instructions,
                )
            else:
                plan = await self._run_mode_a_dag(
                    plan=plan,
                    state=state,
                    video_url=video_url,
                    transcript_slice=transcript_slice,
                    candidate_windows=candidate_windows,
                    candidate_transcripts=candidate_transcripts,
                    niche=niche,
                )

            plan.status = "completed"
            self._plans[plan_id] = plan
            return plan

        except Exception as exc:
            logger.exception("MasterBrain orchestration failed for plan %s", plan_id)
            plan.status = "failed"
            plan.error_message = str(exc)
            self._plans[plan_id] = plan
            raise

    async def _run_mode_b_dag(
        self,
        plan: ViralMasterPlan,
        state: MasterBrainState,
        topic: str | None,
        niche: str,
        custom_instructions: str | None,
    ) -> ViralMasterPlan:
        """Phase 1 -> Phase 2 -> Phase 3 execution for Mode B Generative Synthesis."""
        # ---------------------------------------------------------------------
        # Phase 1: Context & Research
        # ---------------------------------------------------------------------
        state.phase = "phase1_context"
        research = await self.info_researcher.research(niche=niche, topic=topic)
        plan.research = research
        effective_topic = research.topic or topic or niche

        # Draft script & candidate hook
        hook_candidate = (
            research.viral_angles[0]
            if research.viral_angles
            else f"What nobody tells you about {effective_topic}..."
        )
        initial_script = self._compose_mode_b_script(hook_candidate, effective_topic, research)
        plan.script_text = initial_script

        # ---------------------------------------------------------------------
        # Phase 2: Quality Gate (Calibrated Hook Critic)
        # ---------------------------------------------------------------------
        state.phase = "phase2_quality_gate"
        review = await self.hook_critic.evaluate_mode_b(hook_candidate, attempt=1)

        approved_hook = hook_candidate
        if review.verdict == "approved":
            approved_hook = review.hook_text
        elif review.verdict == "fast_patch" and review.patched_hook:
            approved_hook = review.patched_hook
        elif review.verdict == "rejected":
            # Attempt 2: Re-generate / pick second viral angle
            alt_hook = (
                research.viral_angles[1]
                if len(research.viral_angles) > 1
                else f"Stop scrolling: The single biggest {effective_topic} mistake will shock you."
            )
            review2 = await self.hook_critic.evaluate_mode_b(
                alt_hook, attempt=2, prior_reviews=[review]
            )
            review = review2
            approved_hook = review2.patched_hook or review2.hook_text

        plan.hook_review = review
        plan.quality_score = review.score
        # Replace script opening line with approved hook
        approved_script = self._replace_hook_in_script(initial_script, approved_hook)
        plan.script_text = approved_script

        # ---------------------------------------------------------------------
        # Phase 3: Bounded Parallel Swarm (asyncio.Semaphore(2))
        # ---------------------------------------------------------------------
        state.phase = "phase3_swarm"
        audio_plan, seo_plan, retention_plan = await self._run_bounded_swarm(
            approved_text=approved_script,
            approved_hook=approved_hook,
            topic=effective_topic,
            niche=niche,
            key_insights=research.key_insights,
            duration=None,
        )

        plan.audio = audio_plan
        plan.seo = seo_plan
        plan.retention = retention_plan

        return plan

    async def _run_mode_a_dag(
        self,
        plan: ViralMasterPlan,
        state: MasterBrainState,
        video_url: str | None,
        transcript_slice: str | None,
        candidate_windows: list[tuple[float, float]] | None,
        candidate_transcripts: list[str] | None,
        niche: str,
    ) -> ViralMasterPlan:
        """Phase 1 -> Phase 2 -> Phase 3 execution for Mode A Link Repurposing."""
        # ---------------------------------------------------------------------
        # Phase 1: Ingestion & Slicing Context
        # ---------------------------------------------------------------------
        state.phase = "phase1_context"
        windows = candidate_windows or [(0.0, 42.0)]
        transcripts = candidate_transcripts or [transcript_slice or ""]
        current_candidate_idx = 0

        active_window = windows[current_candidate_idx]
        active_transcript = (
            transcripts[current_candidate_idx] if transcripts else (transcript_slice or "")
        )
        start_sec, end_sec = active_window

        # ---------------------------------------------------------------------
        # Phase 2: Mode A Quality Gate (Curiosity & Completeness)
        # Invariant: Never re-draft spoken human words!
        # ---------------------------------------------------------------------
        state.phase = "phase2_quality_gate"
        review = await self.hook_critic.evaluate_mode_a(
            transcript_slice=active_transcript,
            start_sec=start_sec,
            end_sec=end_sec,
            candidate_index=current_candidate_idx,
            candidate_count=len(windows),
        )

        if review.candidate_index != current_candidate_idx and review.candidate_index < len(
            windows
        ):
            current_candidate_idx = review.candidate_index
            start_sec, end_sec = windows[current_candidate_idx]
            if current_candidate_idx < len(transcripts):
                active_transcript = transcripts[current_candidate_idx]
        elif review.suggested_shift_seconds != 0.0:
            shift = review.suggested_shift_seconds
            start_sec = max(0.0, start_sec + shift)
            end_sec = min(start_sec + 58.0, end_sec + shift)

        plan.selected_window = (start_sec, end_sec)
        plan.script_text = active_transcript  # Preserved exact spoken words
        plan.hook_review = review
        plan.quality_score = review.score

        # ---------------------------------------------------------------------
        # Phase 3: Bounded Parallel Swarm (asyncio.Semaphore(2))
        # ---------------------------------------------------------------------
        state.phase = "phase3_swarm"
        audio_plan, seo_plan, retention_plan = await self._run_bounded_swarm(
            approved_text=active_transcript,
            approved_hook=review.hook_text or "Viral Insight",
            topic="Repurposed Clip",
            niche=niche,
            key_insights=[],
            duration=end_sec - start_sec,
        )

        plan.audio = audio_plan
        plan.seo = seo_plan
        plan.retention = retention_plan

        return plan

    async def _run_bounded_swarm(
        self,
        approved_text: str,
        approved_hook: str,
        topic: str,
        niche: str,
        key_insights: list[str],
        duration: float | None,
    ) -> tuple[AudioPlan, SEOMetadata, RetentionPlan]:
        """Execute MusicScout, ViralSEO, and RetentionEditor bounded by Semaphore(2)."""

        async def _bounded(coro_fn: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
            async with self._swarm_semaphore:
                return await coro_fn()

        task_audio = _bounded(
            lambda: self.music_scout.scout(
                script_or_transcript=approved_text,
                duration_seconds=duration,
                niche=niche,
            )
        )
        task_seo = _bounded(
            lambda: self.viral_seo.generate_seo(
                hook=approved_hook,
                topic=topic,
                niche=niche,
                key_insights=key_insights,
            )
        )
        task_retention = _bounded(
            lambda: self.retention_editor.plan(
                words_per_second=2.8,  # baseline cadence
                transcript=approved_text,
            )
        )

        res_audio, res_seo, res_retention = await asyncio.gather(
            task_audio, task_seo, task_retention
        )

        # Update retention editor with calculated cadence from music scout
        if isinstance(res_audio, AudioPlan) and res_audio.words_per_second > 0:
            res_retention.adaptive_silence_thresh_db = (
                -28.0
                if res_audio.words_per_second > 3.0
                else (-35.0 if res_audio.words_per_second < 2.2 else -32.0)
            )

        return res_audio, res_seo, res_retention

    def _compose_mode_b_script(
        self,
        hook: str,
        topic: str,
        research: ResearchIntel,
    ) -> str:
        """Compose 40-second viral narrative structure."""
        insights_str = " ".join(research.key_insights[:2]) if research.key_insights else ""
        return (
            f"{hook} "
            f"Most people think {topic} is simple, but here is what actually happens. "
            f"{insights_str} "
            f"If you want to master this, hit follow and save this for later."
        )

    def _replace_hook_in_script(self, original_script: str, approved_hook: str) -> str:
        """Replace the first sentence of the script with the calibrated hook."""
        parts = original_script.split(".", 1)
        if len(parts) > 1:
            rest = parts[1].strip()
            return f"{approved_hook.rstrip('.')} {rest}"
        return approved_hook
