# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
