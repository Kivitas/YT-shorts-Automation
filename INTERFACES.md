# AutoShorts Architecture & Contract Interfaces (`INTERFACES.md`)

This document specifies the contracts, protocols, shared models, and engineering ownership boundaries for **AutoShorts v1.1 (Studio 2.0)**. Parallel implementation engineers must build strictly against these interfaces and abide by their module ownership assignments to prevent merge conflicts.

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
   - Downloading: **Strictly `yt-dlp`** using `--download-sections`. No `pytube`, no HTTP stream scraping. Multi-client resilient fallback (`default` &rarr; `android` &rarr; `web`).
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
6. **Strict Zero-Phantom Data Policy**:
   - Every analytics metric, research finding, and telemetry signal strictly stems from real configured API credentials (`YOUTUBE_API_KEY`, OAuth `token.json`, `yt-dlp`, Playwright Studio scraper).
   - No mock, synthetic, or placeholder numbers are ever surfaced in production UI or stored in state.
7. **Smart Video Identification & Resilient Clipping Invariants**:
   - YouTube "Most Replayed" heatmap peak curves (`0.0–1.0`), SponsorBlock ad/intro/outro segments, and video chapters must be extracted prior to window slicing.
   - SponsorBlock segments are excluded from candidate generation when `skip_sponsors: true`.
   - Candidate clip boundaries snap to chapter intervals when available.
8. **Precision Dead-Air Trimming & Dynamic Crop Framing**:
   - Silence gaps between transcribed words exceeding `0.45s` are converted to tight `JumpCut` ranges when `auto_trim_silence: true`.
   - Speaker reframing in 9:16 vertical canvas supports `crop_focus`: `center`, `left`, `right`, and `dynamic_pan`.
9. **Automated Hook Thumbnails & 3-Way A/B Viral Titles**:
   - The highest-retention hook frame (`thumbnail.jpg`) is extracted via FFmpeg and uploaded via YouTube API `thumbnails().set`.
   - 3-way viral title variants (*Curiosity*, *Contrarian*, *Stat/Number Shock*) are generated for every clip.

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
    def score_candidates(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
        heatmap_points: Sequence[dict[str, float]] | None = None,
    ) -> list[ClipCandidate]: ...
    def select_clips(self, candidates: Sequence[ClipCandidate], count: int = 1) -> list[ClipSelection]: ...
    def decide_action(self, action_key: str, context: Mapping[str, Any]) -> str: ...

@runtime_checkable
class Downloader(Protocol):
    def fetch_metadata(self, url: str) -> VideoMetadata: ...
    def fetch_heatmap(self, url: str) -> list[dict[str, float]]: ...
    def fetch_sponsor_segments(self, video_id: str) -> list[dict[str, Any]]: ...
    def download_section(self, url: str, start_sec: float, end_sec: float, output_path: Path) -> Path: ...
    def download_audio_only(self, url: str, output_path: Path) -> Path: ...

@runtime_checkable
class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]: ...
    def align_words(self, audio_path: Path, text: str | None = None) -> list[dict[str, Any]]: ...

@runtime_checkable
class EditPlanner(Protocol):
    def generate_edl(self, video_clip_path: Path, transcript_text: str | None = None, instructions: str | None = None) -> EditDecisionList: ...
    def detect_dead_air_cuts(self, transcript_words: Sequence[dict[str, Any]], threshold_sec: float = 0.45) -> list[dict[str, float]]: ...

@runtime_checkable
class Compositor(Protocol):
    def compose(
        self,
        job_id: str,
        source_clip: Path,
        edl: EditDecisionList | None,
        background_path: Path,
        subtitles_path: Path,
        output_path: Path,
        crop_focus: str = "center",
        **kwargs: Any,
    ) -> Path: ...
    def generate_preview(self, source_clip: Path, output_path: Path, **kwargs: Any) -> Path: ...
    def extract_hook_thumbnail(self, video_clip_path: Path, timestamp_sec: float, output_path: Path) -> Path: ...

@runtime_checkable
class Uploader(Protocol):
    def upload(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted",
        thumbnail_path: Path | None = None,
    ) -> UploadResult: ...
    def upload_thumbnail(self, video_id: str, thumbnail_path: Path) -> bool: ...
    def generate_title_variants(self, base_title: str, hook_text: str) -> dict[str, str]: ...
    def verify(self, video_id: str) -> bool: ...

@runtime_checkable
class AnalyticsEngine(Protocol):
    def calculate_outlier_multiplier(self, views: int, channel_median_views: float) -> float: ...
    def get_best_publish_times(self) -> dict[str, Any]: ...
    def auto_tune_from_winners(self) -> dict[str, Any]: ...
    def export_telemetry(self, format: str = "json") -> str: ...

@runtime_checkable
class Cleaner(Protocol):
    def cleanup_job(self, job_id: str, force: bool = False) -> list[Path]: ...
    def assert_safe_to_delete(self, path: Path | str) -> None: ...
```

---

## 3. Shared State & Lifecycle Models (`autoshorts.core.models`)

- `JobStatus`: State machine enum:
  `QUEUED` $\to$ `METADATA_FETCHED` $\to$ `TRANSCRIBED` $\to$ `WINDOWS_SLICED` $\to$ `SEMIF_EVALUATED` $\to$ `DOWNLOADING_SECTION` $\to$ `SUBTITLES_GENERATED` $\to$ `COMPOSITING` $\to$ `READY_FOR_REVIEW` $\to$ `SCHEDULED` $\to$ `UPLOADING` $\to$ `VERIFYING` $\to$ `COMPLETED` $\to$ `CLEANED` (or `FAILED`).
- `Job`: Master entity tracking inputs, metadata, candidate windows, selected clips, EDL, rendered paths, crop focus, title variants, and schedule status.
- `ClipCandidate`: Sliced window (`start`, `end`, `text`, `score`, `heatmap_score`, `curiosity_score`, `chapter_title`, `is_sponsor_free`).
- `ClipSelection`: Chosen clip (`candidate`, `target_duration`, `background_style`, `hook_text`, `title`, `title_variants`, `crop_focus`).
- `EditDecisionList`: Multimodal EDL schema (`hook_in_sec`, `hook_out_sec`, `jump_cuts`, `zoom_punch_ins`, `crop_focus`, `caption_corrections`, `recommended_thumbnail_timestamp`, `recommended_title`, `recommended_hook`, `background_style_recommendation`).
- `VideoMetadata`: Non-download metadata (`title`, `channel`, `channel_url`, `video_url`, `duration`, `video_id`, `chapters`, `heatmap`, `sponsor_segments`).
- `UploadResult`: Upload outcome (`video_id`, `url`, `status`, `verified`, `uploaded_at`, `quota_spent`, `privacy_status`, `thumbnail_uploaded`).
- `TitleVariants`: 3-way viral title set (`curiosity`, `contrarian`, `stat_shock`).
- `AnalyticsVideo`: Telemetry record (`video_id`, `title`, `views`, `likes`, `comments`, `apv`, `svs`, `outlier_multiplier`, `is_outlier`, `publish_hour`, `publish_day`).

---

## 4. Module Ownership & Team Assignments (Studio 2.0 Overhaul)

To allow independent, conflict-free parallel engineering, each engineer owns an exclusive set of files and tests. Do NOT edit files assigned to other engineers without coordination.

| Engineer / Track | Assigned Functional Area | Owned Implementation Files | Owned Test Files |
|---|---|---|---|
| **Track 1** | **Smart Identification, Downloader & Heatmap/SponsorBlock Clipping Engineer** | `src/autoshorts/core/models.py`<br>`src/autoshorts/ingestion/downloader.py`<br>`src/autoshorts/slicer/window_generator.py`<br>`src/autoshorts/decider/semif_decider.py` | `tests/test_ingestion_slicer.py`<br>`tests/test_decider.py` |
| **Track 2** | **Advanced Editing, Dead-Air Trimmer, Smart Crop & Uploader Engineer** | `src/autoshorts/edit_planner/planner.py`<br>`src/autoshorts/media/dopamine_effects.py`<br>`src/autoshorts/media/compositor.py`<br>`src/autoshorts/uploader/metadata_builder.py`<br>`src/autoshorts/uploader/youtube_api.py` | `tests/test_edit_planner.py`<br>`tests/test_media_compositor.py`<br>`tests/test_uploader.py` |
| **Track 3** | **Analytics 2.0, Outlier Engine, Auto-Tune & Pipeline APIs Engineer** | `src/autoshorts/analytics/models.py`<br>`src/autoshorts/analytics/engine.py`<br>`src/autoshorts/analytics/attribution.py`<br>`src/autoshorts/pipeline.py`<br>`src/autoshorts/dashboard/app.py` | `tests/test_analytics.py`<br>`tests/test_pipeline.py` |
| **Track 4** | **Glass & Clay Studio UI 2.0 Engineer** | `src/autoshorts/dashboard/templates/index.html`<br>`src/autoshorts/dashboard/static/style.css`<br>`src/autoshorts/dashboard/static/app.js` | `tests/test_dashboard_cli.py` |
| **Track 5** | **Config & Documentation Synchronization Engineer** | `config/config.yaml`<br>`config/config.example.yaml`<br>`README.md`<br>`CHANGELOG.md`<br>`INTERFACES.md` | *Self-contained configuration & markdown verification* |

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
5. **Zero-Phantom Data Integrity**:
   Under no circumstances should placeholder or random numbers be inserted into analytics models or exported telemetry. If data is unavailable from an API or scraper, return `0` or `null` explicitly with appropriate error flags.

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
