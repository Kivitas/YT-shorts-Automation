# Contributing to AutoShorts & Faceless AI Studio v1.0

Thank you for your interest in contributing to AutoShorts & Faceless AI Studio!

## 1. Engineering Guidelines & Core Constraints

Before submitting contributions, please review our strict architecture principles:

1. **Python Runtime & Quality Standards**:
   - AutoShorts targets **Python 3.12** exclusively (using `uv`).
   - Use strict type annotations throughout the codebase (`typing` module / standard generic syntax).
   - Format and lint code with `ruff` (`ruff check` and `ruff format`).
2. **Hardware Constraints**:
   - All components must run efficiently on consumer multi-core CPUs with integrated Intel UHD / Iris Xe Graphics.
   - Enforce bounded concurrency (`concurrency = 1`) on video processing and transcription workloads to avoid memory spikes and thrashing.
3. **Strict Tooling Exclusivity**:
   - **Downloading**: Exclusively `yt-dlp` (`--download-sections` for precision cuts). Zero external or untrusted downloaders.
   - **Rendering & Compositing**: Exclusively direct `FFmpeg` and `ffprobe` subprocesses. **MoviePy is strictly forbidden**.
   - **Browser Automation**: Exclusively `Playwright` for YouTube Studio fallback upload and verification DOM polling. Never use Playwright for downloading video streams.
4. **Zero Hardcoded Model Names**:
   - Never hardcode LLM model names or filesystem paths in source code.
   - Configuration defaults must specify `model: null` to be dynamically resolved to provider defaults or local discovered weights at runtime.
5. **Security, Secrets & Credential Guardian**:
   - Never commit API keys, client secrets, tokens, or personal identifiers.
   - Load credentials exclusively via `.env` or system environment variables, managed atomically via `settings_manager.py`.
   - All console and file logging automatically masks sensitive credentials via `logging_setup.py`.
   - Validate API and OAuth tokens through `core/credential_guardian.py` to circuit-break calls before quota waste or unhandled auth exceptions.
6. **Offline Resilience & Zero-Phantom-Data Guarantee**:
   - Long-running pipeline operations must leverage `core/resilience.py` and `scheduler/power_management.py` (`SetThreadExecutionState`) to prevent OS sleep.
   - SQLite state transitions in `core/state_manager.py` are atomic to prevent phantom tasks, orphaned jobs, or duplicated render batches during unexpected power loss or network drops.
7. **Automated Testing**:
   - All PRs must include network-independent unit tests under `tests/` leveraging `pytest` and `pytest-mock`.
   - The test suite must maintain 100% passing status across all unit and integration tests.

---

## 2. Codebase Module Layout

AutoShorts is structured into modular domain packages:

```
src/autoshorts/
├── core/
│   ├── config.py                 # Pydantic Settings & configuration models
│   ├── contracts.py              # Shared Protocol interfaces (@runtime_checkable)
│   ├── credential_guardian.py    # Pre-flight invalid/revoked API key & OAuth circuit breaker
│   ├── exceptions.py             # Domain error hierarchy (PathSecurityError, QuotaExceededError, etc.)
│   ├── logging_setup.py          # Secret-redacting logger & formatting
│   ├── models.py                 # Shared dataclasses (Job, ClipCandidate, EDL, UploadResult)
│   ├── paths.py                  # Directory layout helpers, path sanitization, atomic_write
│   ├── resilience.py             # PC sleep / YouTube offline recovery & queue catch-up
│   ├── settings_manager.py       # Atomic .env/config persistence, OAuth consent, masked settings API
│   ├── state_manager.py          # SQLite job persistence & query layer (data/state.db)
│   └── providers/                # LLM abstraction (Gemini, OpenRouter, Local GGUF)
├── cleanup/
│   └── safety_cleaner.py         # Path-guarded safe deletion with send2trash
├── dashboard/
│   ├── app.py                    # FastAPI web service with SSE log streaming & REST APIs
│   ├── static/                   # CSS styles and frontend vanilla JavaScript
│   └── templates/                # HTML5 dashboard templates
├── decision/
│   ├── orchestrator.py           # Candidate evaluation pipeline
│   ├── semif_engine.py           # Semantic-IF token probability decider
│   └── window_slicer.py          # Transcript candidate window generator
├── discovery/
│   ├── metadata_fetcher.py       # Lightweight non-download yt-dlp metadata probe
│   └── url_validator.py          # YouTube URL validation and normalization
├── edit_planner/
│   ├── edl_schema.py             # Multimodal Edit Decision List schema
│   ├── multimodal_planner.py     # Gemini Files API video EDL planner
│   ├── planner.py                # Heatmap & SponsorBlock precision clipping integration
│   └── workflow_optimizer.py     # Retention pacing curve optimizer & EDL snapshot manager
├── integrations/
│   └── spark/                    # Gemini Spark companion inbox watcher and prompt skills
├── media/
│   ├── ass_styler.py             # Advanced SubStation Alpha (.ass) subtitle renderer (neon, minimal, pill)
│   ├── background_fetcher.py     # Auto-downloaded CC0 Minecraft & Subway Surfers gameplay backgrounds
│   ├── compositor.py             # Dual 9:16 vertical FFmpeg compositor & audio ducking
│   ├── dopamine_effects.py       # Jump-cuts, zoom punch-ins, progress bars, hook cards
│   ├── downloader.py             # yt-dlp precision section downloader
│   ├── encoder_probe.py          # Hardware encoder detection (h264_qsv fallback to libx264)
│   └── whisper_aligner.py        # faster-whisper word-level timestamp aligner
├── quota/
│   └── quota_tracker.py          # Dual-bucket Pacific Time YouTube quota accountant
├── scheduler/
│   ├── power_management.py       # Win32 SetThreadExecutionState sleep prevention
│   ├── scheduler.py              # Daily 50 spread scheduler & catch-up engine
│   └── slot_generator.py         # Jittered publishing slot generator (5–25 min intervals)
├── transcript/
│   └── fetcher.py                # Timed transcript fetcher (YouTube transcript API)
├── uploader/
│   ├── description_builder.py    # Attribution & description generator
│   ├── local_exporter.py         # Offline disk export
│   ├── studio_playwright.py      # Headless/headed MS Edge YouTube Studio uploader & auto-fallback
│   ├── verifier.py               # Upload status and processing verification
│   └── youtube_api.py            # YouTube Data API v3 chunked resumable uploader
├── analytics/
│   ├── attribution.py            # Multi-touch attribution & retention decay modeling (Analytics 2.0)
│   ├── engine.py                 # Closed-loop telemetry & winning recipe detector (Analytics 2.0)
│   ├── models.py                 # Shorts Viral Score (SVS) algorithm and performance metrics
│   ├── collector.py              # Unified multi-source analytics collector (API v3 + Analytics v2 + yt-dlp)
│   ├── scoring.py                # Viral score scoring helpers
│   ├── studio_scraper.py         # Playwright YouTube Studio analytics scraper fallback
│   └── yt_dlp_collector.py       # Zero-quota public metrics fallback collector
└── research_agent/
    ├── discovery.py              # Live trending videos, podcasts, and viral clips discovery
    ├── models.py                 # Viral research, payout matrix, BGM, and faceless short models
    ├── faceless_studio.py        # Autonomous Faceless AI Shorts generator (edge-tts + ASS + FFmpeg)
    ├── music.py                  # Trending viral background music catalog & downloader
    ├── payout_matrix.py          # Highest payout niche matrix (RPM/CPM) & revenue calculator
    └── research_brief_agent.py   # AI Viral Strategy Brief Copilot
```

---

## 3. Development Workflow

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/autoshorts.git
   cd autoshorts
   ```

2. **Initialize Python 3.12 environment with `uv`**:
   ```powershell
   uv sync
   # Or manually bootstrap:
   uv venv --python 3.12
   .venv\Scripts\activate
   uv pip install -e ".[dev]"
   ```

3. **Verify environment health (Diagnostics)**:
   ```cmd
   start.bat --check
   ```
   Or via `uv`:
   ```powershell
   uv run python -c "import autoshorts; print('Installed version:', autoshorts.__version__)"
   ```

4. **Verify linting and code formatting**:
   ```powershell
   uv run ruff check src/ tests/
   uv run ruff format --check src/ tests/
   ```

5. **Run the automated test suite**:
   ```cmd
   start.bat --test
   ```
   Or directly with `uv`:
   ```powershell
   uv run pytest -q
   ```
   All tests must pass cleanly without regressions.

6. **Create a topic branch**:
   ```powershell
   git checkout -b feature/my-enhancement
   ```

7. **Submit a Pull Request**:
   - Ensure all items in `.github/pull_request_template.md` are checked.
   - Adhere to Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`.
