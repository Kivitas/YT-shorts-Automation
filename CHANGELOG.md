# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.3.0] - 2026-10-21

### Added
- **Master Brain Orchestrator & Specialist Swarm (`src/autoshorts/brain/`)**:
  - **The Boss 3-Stage DAG Orchestrator**: Multi-agent pipeline coordinating Phase 1 (Ingestion & Research via `InfoResearcherAgent`), Phase 2 (Quality Gate via `HookCriticAgent`), and Phase 3 (Bounded parallel swarm via `asyncio.Semaphore(2)` coordinating `MusicScoutAgent`, `ViralSeoAgent`, and `RetentionEditorAgent`).
  - **Decoupled Operation Modes**:
    - **Mode A (Video Link Repurposing)**: Analyzes video transcript slices and candidate cuts, adjusts cut boundaries ($\pm 5.0$s) or switches candidate windows based on narrative tension without ever rewriting original human speech.
    - **Mode B (Generative Faceless AI)**: Autonomous end-to-end generation from topic/niche to full production package with multi-tier iterative hook refinement.
  - **Calibrated 3-Tier Quality Gate (`HookCriticAgent`)**:
    - Score $\ge 80$: Instant approval for production.
    - Score $70\text{--}79$: Fast hook sentence patching applied to inject curiosity gaps and high-velocity tension triggers.
    - Score $< 70$: Rejection and prompt re-drafting (Attempt 1), with calibrated argmax fallback and quality metadata tracking on subsequent attempts.
  - **3-Layer Hashtag Pyramid (`ViralSeoAgent`)**: Generates 3 A/B title variants ($<100$ characters), 3-layer hashtag pyramid (Layer 1 broad: `#Shorts`, `#Viral`; Layer 2 category: `#Tech`, `#Finance`; Layer 3 micro-niche topic tag), and search keywords ($\le 500$ characters).
  - **WPS-to-BPM Musical Matching (`MusicScoutAgent`)**: Automatically measures words per second (WPS) and maps cadence to optimal BGM tempo: $<2.2$ WPS $\to$ 100 BPM ambient, $2.2\text{--}3.0$ WPS $\to$ 120 BPM synthwave, $>3.0$ WPS $\to$ 140 BPM phonk. Dynamically sets rhythmic eighth-note ducking release time ($\text{round}(30000 / \text{BPM})$ ms).
  - **Retention & Pacing Calibration (`RetentionEditorAgent`)**: Speech-adaptive silence excision ($-28$ dB for rapid speech down to $-35$ dB for dramatic pauses), optical speaker zoom centering (`center`, `left`, `right`), and pattern interrupt limits ($\le 4$ interrupts per 60s Short with $\ge 4.5$s refractory cooldown).
  - **Zero-Phantom-Data Preflight Checks & Structured 422 Contract**: Strict credential verification prior to synthesis; raises `PreflightCredentialError` mapping directly to structured HTTP 422 responses with error details and missing credential keys.
- **Google Research TimesFM Time-Series Forecasting (`src/autoshorts/analytics/forecasting.py`)**:
  - **3-Tier Fallback Architecture**:
    - *Tier 1*: Google Research TimesFM foundation model (zero-shot time-series forecasting).
    - *Tier 2*: NumPy/SciPy parametric curve fitting fallback.
    - *Tier 3*: Pure Python 3.11+ standard library (`math`, `statistics`) with zero external dependency overhead.
  - **Preemptive Retention Cliff Detection**: 5-point moving average low-pass filter smoothing on audience retention curve $R(t)$, flagging drop cliffs where slope $\nabla R(t) \le -0.035$/s, enforcing $\ge 4.5$s refractory cooldown between cuts and a hard ceiling of $\le 4$ pattern interrupts per 60s Short.
  - **48-Hour Early View Velocity Trajectory Forecasting**: Projects 48 hourly view data points from early publish telemetry and predicts viral breakout velocity against channel benchmarks.
- **Laya Rhythm, Meter & Decision Engine (`src/autoshorts/media/rhythm_engine.py` & `src/autoshorts/decider/laya_router.py`)**:
  - **Pure Python Transient & Beat Grid Extraction**: Spectral/temporal flux onset detection and autocorrelation lag peak finding to detect BPM and musical beats.
  - **Ambient Confidence Floor ($C \ge 0.35$)**: Automatically bypasses musical beat snapping when background tracks lack percussive transient definitions, falling back cleanly to speech cadence alignment.
  - **Musical Beat Snapping & Word-Guarded Cut Clamping**:
    - Visual zoom punches snap to nearest musical beat within $\le 100$ms.
    - Jump cuts are clamped $\ge 50$ms away from speech boundaries ($t_{\text{cut\_start}} \ge t_{\text{word}}[i].\text{end} + 0.05$s, $t_{\text{cut\_end}} \le t_{\text{word}}[i+1].\text{start} - 0.05$s), guaranteeing spoken words are never truncated.
  - **Rhythmic Sidechain Ducking**: Exact musical eighth-note release timing ($T_{\text{release}} = 60000 / (\text{BPM} \times 2)$ ms, or $30 / \text{BPM}$ seconds).
  - **Sub-40ms System 1 Hook Triage (`LayaRouter`)**: Fast heuristic hook scoring bridge (<40ms latency) evaluating power words, curiosity tension, and weak passive opening penalties.
- **Compositor & Audio Ducking Hardening (`src/autoshorts/media/compositor.py`)**:
  - Explicit `bgm_path` and `bgm_volume` parameter handling dynamically activating sidechain ducking across all video presets, overriding preset defaults when background music is supplied.
- **Comprehensive Test Suite & CI Validation**:
  - Test suite expanded to **657+ passing tests** across all modules with 100% test pass rate.

---

## [1.2.0] - 2026-10-21

### Added
- **Cross-Platform Launcher & Verification Suite**:
  - **Windows `start.bat`**: Full parameter parsing supporting `--check` / `-c` (7-stage pre-flight diagnostics), `--test` / `-t` (automated pytest execution), `--port` / `-p` (custom port binding), `--collect` / `--collect-gameplay` (interactive CC0 gameplay asset downloader), `--no-browser`, and `--host`.
  - **macOS & Linux `start.sh`**: Unified POSIX launcher supporting macOS 12+ Monterey (Apple Silicon M1-M4 & Intel x86_64) and Linux distributions.
  - **Homebrew One-Line Setup**: `brew install python@3.12 uv ffmpeg` followed by `./start.sh`.
  - **Native Power Management**: Windows Win32 `SetThreadExecutionState` and macOS `caffeinate` / IOKit assertions preventing machine sleep during long-running render batches.
- **Certified Safari 15+ WebKit Compatibility**:
  - Dual `-webkit-backdrop-filter` and `backdrop-filter: blur(...)` for flawless frosted glassmorphism on macOS.
  - Form control resetting via `-webkit-appearance: none;`.
  - Mobile/desktop viewport height normalization with `100dvh` and `-webkit-fill-available`.
  - Strict ISO-8601 date parsing preventing `Invalid Date` rendering in JavaScriptCore.
  - HTML5 video playback compatibility (`playsinline`, `webkit-playsinline`, initial muted state).

---

## [1.1.0] - 2026-10-20

### Added
- **Smart Video Identification & Resilient Clipping Pipeline**:
  - **YouTube "Most Replayed" Heatmap Peak Fusion**: Ingests YouTube retention curve heatmap telemetry (`0.0–1.0`), fusing audience replay density with SemIf token-probability scoring, hook curiosity ratings, and chapter boundaries for high-confidence viral clip identification.
  - **SponsorBlock Ad/Intro/Outro Auto-Skipping**: Integrated SponsorBlock API segment querying during window slicing to automatically detect and discard non-content sponsor spots, self-promos, previews, intros, and outros (`skip_sponsors: true`).
  - **Chapter Boundary Alignment**: Extracts native video chapters and snaps candidate slice windows to logical thematic boundaries, preventing abrupt topic truncation or mid-sentence cuts.
  - **Hook Curiosity Scoring**: Multi-signal scoring engine prioritizing narrative tension, curiosity gaps, and opening 3–5 second viewer retention hooks.
  - **Multi-Client `yt-dlp` Resilience**: Resilient fallback cascade (`default` &rarr; `android` &rarr; `web` player clients) to circumvent YouTube bot-detection blocks and format restrictions, combined with keyframe-accurate section extraction.
- **Advanced Video Editing & Smart Publishing**:
  - **Dead-Air Silence Auto-Trimmer (`detect_dead_air_cuts`)**: Whisper-powered word-gap pause detection that identifies dead-air intervals (`> 0.45s`) and automatically converts them into frame-accurate `JumpCut` ranges, injecting dynamic `ZoomPunch` camera emphasis on high-energy speech bursts (`auto_trim_silence: true`).
  - **Speaker Crop Focus Reframing (`crop_focus`)**: Added support for horizontal crop reframing in vertical 9:16 canvas with `center`, `left`, `right`, and `dynamic_pan` speaker tracking.
  - **Auto-Extracted Peak Hook Thumbnail**: FFmpeg-powered frame extraction of high-retention hook frames (`thumbnail.jpg`) and direct integration with YouTube Data API v3 `thumbnails().set` for automated custom thumbnail uploads.
  - **3-Way A/B Viral Title Variants (`generate_title_variants`)**: Generates 3 distinct viral angle variants for every clip—*Curiosity Hook*, *Contrarian Contrasting*, and *Stat/Number Shock*.
- **Analytics 2.0 & Closed-Loop Auto-Tuning**:
  - **Viral Outlier Multiplier Badges (`outlier_multiplier`)**: Calculates view velocity against channel median benchmarks to highlight breakout hits (`1.5x`, `3.0x+` Outlier badges).
  - **Best Publish Hour & Day Insights**: Computes historical velocity heatmaps and recommends optimal publishing windows tailored to the channel's specific audience.
  - **1-Click Closed-Loop Auto-Tune (`POST /api/analytics/auto-tune`)**: Automatically adopts winning attributes (crop focus, caption preset, background category, dopamine pacing) from top-performing Shorts directly into pipeline defaults.
  - **CSV & JSON Telemetry Exporter (`GET /api/analytics/export?format=csv|json`)**: Full programmatic export of all channel Shorts performance, engagement ratios, and attribution metadata.
  - **Strict Zero-Phantom Data Policy**: Guarantees all analytics and telemetry strictly stem from real credentials and official endpoints (`YOUTUBE_API_KEY`, OAuth `token.json`, `yt-dlp`, Playwright Studio scraper), with zero synthetic or placeholder metrics.
- **Glassmorphic & Claymorphic Studio UI**:
  - Complete dashboard aesthetic evolution featuring frosted glass panels (`backdrop-filter: blur`), semi-translucent borders, claymorphic card elevation, and tactile interactive elements.
  - **Candidate Inspector & Editor**: Interactive clip selection modal equipped with Heatmap/SponsorBlock badges, one-click `✂️ Auto-Remove Silence` trimmer, Speaker Crop Focus dropdown, and A/B title variant selector.
  - **Analytics 2.0 Command Center**: Live outlier badges (`🔥 3.2x Outlier`), Best Upload Hour insights panel, `⚡ Auto-Tune Studio from Analytics` action button, and 1-click CSV/JSON telemetry downloaders.

---

## [1.0.0] - 2026-10-20

### Added
- **End-to-End Autonomous Pipeline**:
  - Full clip-to-Shorts automation pipeline managed by a 9-stage state machine (`QUEUED` &rarr; `METADATA_FETCHED` &rarr; `TRANSCRIPT_FETCHED` &rarr; `WINDOWS_SLICED` &rarr; `CLIPS_SELECTED` &rarr; `EDL_GENERATED` &rarr; `SUBTITLES_GENERATED` &rarr; `COMPOSITING` &rarr; `READY_FOR_REVIEW` &rarr; `SCHEDULED` &rarr; `UPLOADING` &rarr; `VERIFYING` &rarr; `COMPLETED` &rarr; `CLEANED`).
  - Strict tooling exclusivity: precision video segment extraction via `yt-dlp` (`--download-sections`), direct `FFmpeg` subprocesses for compositing, and Playwright for Studio uploads.
  - Zero MoviePy dependency to eliminate memory leaks and ensure CPU efficiency.
- **Semantic-IF (SemIf) Token-Probability Decider**:
  - LLM decider engine evaluating candidate speech windows using next-token logit probabilities and softmax scoring.
  - Decoupled provider abstraction supporting Google Gemini, OpenRouter, and Local GGUF (`llama-cpp-python` on CPU).
  - Strict **Zero Hardcoded Model Policy** (`model: null` dynamically resolved at runtime).
- **9:16 High-Dopamine Compositor & Subtitle Sync**:
  - Vertical split-screen layout (speaker cropped 9:16 on top, gameplay on bottom with random start offsets).
  - Word-level timestamp alignment powered by `faster-whisper` (`compute_type="int8"` on CPU).
  - Dynamic retention effects: first-3s hook title card, dynamic top progress bar, punch-in camera zooms, jump-cuts, and EBU R128 dialogue volume normalization (`loudnorm`).
- **Caption Style Presets (`neon`, `minimal`, `pill`)**:
  - SubStation Alpha (`.ass`) subtitle generation engine with three switchable retention styles:
    - `neon` (Default): High-energy yellow karaoke active pop animation with micro-tilt bounce, black outline, and keyword color accents.
    - `minimal`: Clean, elegant sans-serif typography with subtle outline for commentary and podcasts.
    - `pill`: Modern high-contrast translucent rounded box backdrop behind words for maximum legibility over vibrant gameplay.
- **Gameplay Backgrounds Auto-Fetcher (`background_fetcher.py`)**:
  - Automatic download and caching of royalty-free CC0 Minecraft parkour and Subway Surfers background gameplay videos.
  - Video validation, automatic fallback selection, and random temporal offset slicing for visual diversity.
- **Dual-Bucket YouTube Quota Tracker & Spread Scheduler**:
  - Quota management adhering to YouTube Data API v3 granular architecture: dedicated 100 calls/day upload bucket (`videos.insert`) and 10,000 units/day general pool.
  - Supports up to 50 Shorts/day on default quotas without requiring a quota extension audit, preserving 50 calls as headroom for retries.
  - Quota resets synchronized to midnight Pacific Time (00:00 PT).
  - Anti-spam rolling rate limiter enforcing maximum 7 uploads per rolling 60-minute window.
  - Publishing slot generator distributing uploads across daytime active windows (08:00–23:59) with uniform random jittered gaps (5–25 minutes).
  - Catch-up engine that intelligently reschedules missed slots after system sleep without bursting.
  - Windows Win32 `SetThreadExecutionState` integration to prevent PC sleep during active scheduling windows.
- **Playwright MS Edge YouTube Studio Uploader & Auto-Fallback**:
  - Browser automation uploader using Microsoft Edge (`msedge` channel) with persistent user profile (`data/edge_profile`).
  - Seamless mid-flight failover: automatically catches API quota exhaustion (HTTP 403 `quotaExceeded`) and switches to Playwright Studio mode to ensure 100% upload delivery continuity with 0 quota cost.
- **Interactive Web Dashboard & In-App Settings Modal**:
  - Modern FastAPI local web dashboard running on `http://localhost:8765` with vanilla HTML5/CSS/JS frontend.
  - In-app `⚙️ Settings` modal for live configuration of LLM providers, API keys, caption styles, and upload visibility without editing YAML files or restarting the server.
  - In-app YouTube Data API v3 OAuth flow: upload `client_secret.json` and trigger one-click Google OAuth consent flow (`token.json`).
  - Live Link Preview card showing video thumbnail, title, channel name, and duration upon pasting YouTube URLs.
  - Real-time Server-Sent Events (SSE) log streaming (`/api/logs/stream`) with log pausing, search filtering, and buffer clearing.
  - HTML5 video preview player for reviewing rendered Shorts prior to scheduling or upload.
- **Path-Guarded Post-Upload Auto-Cleanup (`SafetyCleaner`)**:
  - Transactional cleanup mechanism with `assert_safe_to_delete` path guard to protect source files, configuration, and data directories.
  - Purges intermediate raw downloads, extracted WAVs, and sliced clips via `send2trash` (OS Recycle Bin) upon verified upload.
- **4-View Studio Command Center & Navigation**:
  - Modernized navigation bar providing rapid switching between 4 dedicated studio workspaces:
    - `🎬 Shorts Studio & Queue`: Dynamic job creation, parameter overrides, live video previews, and execution progress.
    - `🔥 Viral Research & Trending Finder`: Live trending discovery, niche payout matrices, viral audio downloader, and AI strategy briefs.
    - `🎙️ Autonomous Faceless AI Shorts Studio`: 100% on-device faceless Shorts generator with Microsoft Neural TTS, word-level subtitle sync, and stock/gameplay backgrounds.
    - `📊 YouTube Shorts Analytics`: In-app multi-source telemetry, A/B performance tracking, Shorts Viral Score (SVS), and 1-click winning style cloning.
- **5 Vertical 9:16 Layout Modes & Live Phone Mockup Previewer**:
  - Five distinct 9:16 aspect ratio compositing layouts:
    - `split_50_50`: Classic dual vertical split with 50% speaker top and 50% gameplay bottom.
    - `split_70_30`: Dominant 70% primary video with 30% background gameplay anchor.
    - `full_blur_bg`: 16:9 widescreen video centered over an aesthetic 9:16 blurred background.
    - `full_center_crop`: Seamless 9:16 center-cropped immersive full-frame view.
    - `pip_gameplay`: Picture-in-picture speaker bubble overlay atop 9:16 gameplay footage.
  - **9:16 Live Phone Mockup Previewer**: Interactive mobile viewport featuring a toggleable YouTube Shorts Mobile UI Safe-Zone HUD (channel icon, title, like/comment buttons, audio ticker) to verify caption legibility and prevent UI element occlusion.
- **7 Studio Caption Presets & Fast 1-Click Re-Burn**:
  - SubStation Alpha (`.ass`) caption rendering with 7 distinct aesthetic presets:
    - `neon`: High-energy yellow karaoke active pop animation with micro-tilt bounce.
    - `hormozi`: Bold yellow-green punchy active word pop with heavy black stroke.
    - `mrbeast`: Orange and lime bounce with playful active scale wobble.
    - `pill`: High-contrast translucent dark box backdrop for 100% legibility over any footage.
    - `minimal`: Clean, understated sans-serif typography for interviews and podcasts.
    - `cyberpunk`: Vibrant neon cyan and hot pink dual glow pulses.
    - `cinematic`: Elegant warm cream and gold typography with subtle drop shadow.
  - Full customization suite: custom font sizes, text casing (UPPERCASE, Title Case, lowercase), color pickers (primary text, karaoke highlight, outline, shadow), and vertical margin placement.
  - **⚡ Fast 1-Click Re-Burn**: Edit transcripts and restyle captions in seconds via `/api/jobs/{id}/re-burn` without re-downloading or re-rendering base audio and visual tracks.
- **📊 In-App YouTube Shorts Analytics & Closed-Loop A/B Telemetry**:
  - Multi-source waterfall telemetry:
    1. YouTube Data API v3 batch `videos.list` for official view, like, comment, and tag telemetry.
    2. YouTube Analytics API v2 for average percentage viewed (APV) and audience retention curves.
    3. Zero-quota `yt-dlp` public metrics fallback collector.
    4. Playwright headless Microsoft Edge YouTube Studio scraper for verified subscriber gains and impression CTR.
  - **Shorts Viral Score (SVS)**: Weighted 0–100 performance algorithm evaluating view velocity, audience retention rate, engagement ratio, and watch-time completion.
  - **👑 Winning Style Recipe 1-Click Cloning**: Analyzes top-performing Shorts on your channel, extracts their winning configuration (layout mode, caption preset, background, pacing), and applies it directly to new queue submissions with a single click.
- **🔥 Built-In Autonomous Viral Research Agent**:
  - **Live Trending Videos & Podcasts Discovery**: Scrapes and analyzes trending YouTube Shorts, viral clips, and podcasts with direct clickable YouTube links and `⚡ 1-Click Send to Pipeline`.
  - **Highest Payout Niche & RPM/CPM Matrix**: In-depth intelligence on high-CPM content pillars (Finance, AI & Tech, Business, Stoicism, Psychology, Science) with built-in interactive Revenue Calculator.
  - **Trending Viral Songs & 1-Click BGM Downloader**: Built-in library of trending royalty-free background tracks (Drift Phonk, Dark Synthwave, High-Retention Lo-Fi, Fast Tension Ticks) with 1-click downloading into `assets/music/`.
  - **AI Viral Strategy Brief Copilot**: Generates data-driven viral content briefs with psychological hooks, pacing suggestions, and thumbnail recommendations.
- **🎙️ Autonomous Faceless AI Shorts Studio**:
  - Generates viral faceless Shorts from scratch without manual clip editing.
  - Integrated topic generator across 7 high-RPM niches with SQLite `topic_history` de-duplication memory to prevent topic reuse.
  - 100% free Microsoft Edge Neural TTS (`edge-tts`) voiceover synthesis across multiple languages (English, Spanish, Hindi, German, French, Portuguese, Japanese) with word-boundary timing events.
  - Automatic SubStation Alpha ASS karaoke subtitle generation synced to exact audio boundaries.
  - Pure FFmpeg 1080×1920 compositing with gameplay or stock video backgrounds and background music ducking.
- **Comprehensive Test Suite & CI Automation**:
  - 396 unit and integration tests passing cleanly across all modules (`uv run pytest -q`).
  - GitHub Actions CI matrix workflow (`.github/workflows/ci.yml`) testing on `ubuntu-latest` and `windows-latest` with Python 3.12, `uv`, and `ruff`.
