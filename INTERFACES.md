# AutoShorts Architecture & Contract Interfaces (`INTERFACES.md`)

This document specifies the contracts, protocols, shared models, and engineering ownership boundaries for **AutoShorts v1.0**. Parallel implementation engineers must build strictly against these interfaces and abide by their module ownership assignments to prevent merge conflicts.

---

## 1. Core Principles & System Invariants

1. **Zero Hardcoded Models (`model: null`)**:
   - `config.yaml` specifies `model: null` by default.
   - Every provider must resolve models dynamically at runtime (querying local `.gguf` files in `data/models/` or provider `list_models` endpoints).
2. **Hardware Constraints (CPU + Intel UHD Graphics)**:
   - Zero CUDA assumption. `faster-whisper` runs `compute_type="int8"` on CPU.
   - `llama-cpp-python` runs CPU inference with bounded threads (`n_threads=4`).
   - Video compositing probes for `h264_qsv` (Intel QuickSync) and falls back to `libx264 -preset veryfast`.
   - Concurrency is strictly bounded (`concurrency = 1`).
3. **Strict Tooling Exclusivity**:
   - Downloading: **Strictly `yt-dlp`** using `--download-sections`. No `pytube`, no HTTP stream scraping.
   - Editing/Compositing: **Strictly `ffmpeg` and `ffprobe` subprocesses**. `MoviePy` is strictly prohibited.
   - Browser Automation: **Strictly Playwright** for YouTube Studio upload fallback and verification DOM polling. Never use Playwright for downloading video streams.
4. **YouTube Quota & Pacing Realities**:
   - Granular quota buckets: `videos.insert` has its own dedicated 100 calls/day upload bucket. General endpoints share 10,000 units/day (`videos.list` = 1 unit).
   - 50 Shorts/day is feasible on default allocations without an extension audit (reserves 50 upload calls for retries).
   - Quotas reset daily at **midnight Pacific Time (`00:00:00 PT`, America/Los_Angeles)**.
   - Enforce max 7 uploads per rolling 60-minute window and uniform random jittered gaps (5–25 min).
5. **Path Safety & Transactional Cleanup**:
   - Purges strictly target files inside `work/` or `output/` via `send2trash` (Recycle Bin).
   - Protected paths (`assets/`, `data/`, `config/`, `src/`, `inbox/`, root) raise `PathSecurityError`.
   - Purges occur only after verified upload status.

---

## 2. Shared Protocol Interfaces (`autoshorts.core.contracts`)

All domain interfaces are defined as `@runtime_checkable` `typing.Protocol` classes in `autoshorts.core.contracts`.

```python
from typing import Protocol, runtime_checkable, Sequence, Mapping, Any
from pathlib import Path
from autoshorts.core.models import (
    ClipCandidate, ClipSelection, EditDecisionList, VideoMetadata, UploadResult
)

@runtime_checkable
class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def supports_logprobs(self) -> bool: ...
    def list_models(self) -> list[str]: ...
    def complete(self, prompt: str, **kwargs: Any) -> str: ...
    def score_options(self, prompt: str, options: Sequence[str] | Mapping[str, str], **kwargs: Any) -> dict[str, float]: ...

@runtime_checkable
class Decider(Protocol):
    def score_candidates(self, candidates: Sequence[ClipCandidate], instructions: str | None = None) -> list[ClipCandidate]: ...
    def select_clips(self, candidates: Sequence[ClipCandidate], count: int = 1) -> list[ClipSelection]: ...
    def decide_action(self, action_key: str, context: Mapping[str, Any]) -> str: ...

@runtime_checkable
class Downloader(Protocol):
    def fetch_metadata(self, url: str) -> VideoMetadata: ...
    def download_section(self, url: str, start_sec: float, end_sec: float, output_path: Path) -> Path: ...
    def download_audio_only(self, url: str, output_path: Path) -> Path: ...

@runtime_checkable
class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]: ...
    def align_words(self, audio_path: Path, text: str | None = None) -> list[dict[str, Any]]: ...

@runtime_checkable
class EditPlanner(Protocol):
    def generate_edl(self, video_clip_path: Path, transcript_text: str | None = None, instructions: str | None = None) -> EditDecisionList: ...

@runtime_checkable
class Compositor(Protocol):
    def compose(self, job_id: str, source_clip: Path, edl: EditDecisionList | None, background_path: Path, subtitles_path: Path, output_path: Path, **kwargs: Any) -> Path: ...
    def generate_preview(self, source_clip: Path, output_path: Path, **kwargs: Any) -> Path: ...

@runtime_checkable
class Uploader(Protocol):
    def upload(self, path: Path, title: str, description: str, tags: list[str], privacy: str = "unlisted") -> UploadResult: ...
    def verify(self, video_id: str) -> bool: ...

@runtime_checkable
class Cleaner(Protocol):
    def cleanup_job(self, job_id: str, force: bool = False) -> list[Path]: ...
    def assert_safe_to_delete(self, path: Path | str) -> None: ...
```

---

## 3. Shared State & Lifecycle Models (`autoshorts.core.models`)

- `JobStatus`: State machine enum:
  `QUEUED` $\to$ `METADATA_FETCHED` $\to$ `TRANSCRIBED` $\to$ `WINDOWS_SLICED` $\to$ `SEMIF_EVALUATED` $\to$ `DOWNLOADING_SECTION` $\to$ `SUBTITLES_GENERATED` $\to$ `COMPOSITING` $\to$ `READY_FOR_REVIEW` $\to$ `SCHEDULED` $\to$ `UPLOADING` $\to$ `VERIFYING` $\to$ `COMPLETED` $\to$ `CLEANED` (or `FAILED`).
- `Job`: Master entity tracking inputs, metadata, candidate windows, selected clips, EDL, rendered paths, and schedule status.
- `ClipCandidate`: Sliced window (`start`, `end`, `text`, `score`).
- `ClipSelection`: Chosen clip (`candidate`, `target_duration`, `background_style`, `hook_text`, `title`).
- `EditDecisionList`: Multimodal EDL schema (`hook_in_sec`, `hook_out_sec`, `jump_cuts`, `zoom_punch_ins`, `caption_corrections`, `recommended_thumbnail_timestamp`, `recommended_title`, `recommended_hook`, `background_style_recommendation`).
- `VideoMetadata`: Non-download metadata (`title`, `channel`, `channel_url`, `video_url`, `duration`, `video_id`).
- `UploadResult`: Upload outcome (`video_id`, `url`, `status`, `verified`, `uploaded_at`, `quota_spent`, `privacy_status`).

---

## 4. Module Ownership & Team Assignments

To allow independent, conflict-free parallel engineering, each engineer owns an exclusive set of files and tests. Do NOT edit files assigned to other engineers without coordination.

| Engineer / Track | Assigned Functional Area | Owned Implementation Files | Owned Test Files |
|---|---|---|---|
| **Foundation (Completed)** | Skeleton, Contracts, Config, SQLite State, Logging, Paths, Settings Manager | `src/autoshorts/core/config.py`<br>`src/autoshorts/core/models.py`<br>`src/autoshorts/core/state_manager.py`<br>`src/autoshorts/core/paths.py`<br>`src/autoshorts/core/exceptions.py`<br>`src/autoshorts/core/logging_setup.py`<br>`src/autoshorts/core/contracts.py`<br>`src/autoshorts/core/settings_manager.py`<br>`pyproject.toml`<br>`config/config.yaml` | `tests/test_foundation.py`<br>`tests/test_settings_api.py` |
| **Track A: Providers & Decision** | LLM Providers, SemIf Logit Softmax, Semantic-IF Orchestrator | `src/autoshorts/core/providers/base.py`<br>`src/autoshorts/core/providers/local_provider.py`<br>`src/autoshorts/core/providers/gemini_provider.py`<br>`src/autoshorts/core/providers/openrouter_provider.py`<br>`src/autoshorts/decision/window_slicer.py`<br>`src/autoshorts/decision/semif_engine.py`<br>`src/autoshorts/decision/orchestrator.py` | `tests/test_provider_abstraction.py`<br>`tests/test_semif_math.py`<br>`tests/test_providers.py`<br>`tests/test_decision.py` |
| **Track B: Media & Compositor** | Hardware Probe, ASS Subtitle Styler, Dopamine Effects, FFmpeg Compositor, Background Fetcher | `src/autoshorts/media/encoder_probe.py`<br>`src/autoshorts/media/ass_styler.py`<br>`src/autoshorts/media/dopamine_effects.py`<br>`src/autoshorts/media/compositor.py`<br>`src/autoshorts/media/background_fetcher.py` | `tests/test_ass_builder.py`<br>`tests/test_path_escaping.py`<br>`tests/test_synthetic_ffmpeg.py`<br>`tests/test_background_fetcher.py`<br>`tests/test_media_compositor.py` |
| **Track C: Scheduler & Upload** | Daily 50 Pacing Scheduler, Dual Quota Tracker, API/Studio Uploader, Studio Fallback, Verifier | `src/autoshorts/quota/quota_tracker.py`<br>`src/autoshorts/scheduler/slot_generator.py`<br>`src/autoshorts/scheduler/power_management.py`<br>`src/autoshorts/scheduler/scheduler.py`<br>`src/autoshorts/uploader/youtube_api.py`<br>`src/autoshorts/uploader/studio_playwright.py`<br>`src/autoshorts/uploader/verifier.py`<br>`src/autoshorts/uploader/local_exporter.py` | `tests/test_quota_tracker.py`<br>`tests/test_scheduler.py`<br>`tests/test_quota.py`<br>`tests/test_uploader.py`<br>`tests/test_studio_fallback.py` |
| **Track D: Ingestion & Downloader** | URL Validation, Metadata Fetching, Transcript Fetcher, Precision yt-dlp, Whisper Aligner, Attribution Template | `src/autoshorts/discovery/url_validator.py`<br>`src/autoshorts/discovery/metadata_fetcher.py`<br>`src/autoshorts/transcript/fetcher.py`<br>`src/autoshorts/media/downloader.py`<br>`src/autoshorts/media/whisper_aligner.py`<br>`src/autoshorts/uploader/description_builder.py` | `tests/test_description_builder.py`<br>`tests/test_ingest.py` |
| **Track E: Cleanup & Edit Planner & Spark** | Path-Guarded Safety Cleaner, Gemini Files EDL Planner, Gemini Spark Inbox Watcher | `src/autoshorts/cleanup/safety_cleaner.py`<br>`src/autoshorts/edit_planner/edl_schema.py`<br>`src/autoshorts/edit_planner/multimodal_planner.py`<br>`src/autoshorts/integrations/spark/inbox_watcher.py`<br>`src/autoshorts/integrations/spark/spark_prompts.md` | `tests/test_cleanup_guard.py`<br>`tests/test_multimodal_planner.py`<br>`tests/test_spark_inbox.py`<br>`tests/test_cleanup.py`<br>`tests/test_edit_planner.py`<br>`tests/test_spark.py` |
| **Track F: Orchestrator, Dashboard & CLI** | Pipeline Coordinator, Web Dashboard (FastAPI+SSE), Master CLI Entrypoint | `src/autoshorts/dashboard/app.py`<br>`src/autoshorts/dashboard/templates/index.html`<br>`src/autoshorts/dashboard/static/style.css`<br>`src/autoshorts/dashboard/static/app.js`<br>`run.py`<br>`src/autoshorts/pipeline.py`<br>`src/autoshorts/cli.py` | `tests/test_dashboard_api.py`<br>`tests/test_e2e_pipeline.py`<br>`tests/test_dashboard_cli.py`<br>`tests/test_pipeline.py` |

---

## 5. Development Guidelines for Engineers

1. **Importing Shared Components**:
   Always import shared contracts, models, state manager, paths, and config from `autoshorts.core`:
   ```python
   from autoshorts.core import (
       Settings, load_config, StateManager, Job, JobStatus,
       ClipCandidate, EditDecisionList, LLMProvider,
       escape_ffmpeg_filter_path, atomic_write, assert_safe_to_delete
   )
   ```
2. **Secrets Protection**:
   Never write raw API keys to log output. Use `from autoshorts.core.logging_setup import redact` or configure the logger via `setup_logging()`.
3. **Database Interactions**:
   Use the `StateManager` instance methods (`save_job`, `update_job_status`, `record_quota_spend`, `enqueue_for_upload`) rather than executing raw SQL queries in feature modules.
4. **Timezones**:
   Always use `get_current_pt_date()` or `get_pt_timezone()` for quota-related calculations. YouTube resets at midnight Pacific Time.
