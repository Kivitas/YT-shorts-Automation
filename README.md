# Shorts Automation

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Status: Planning Stage](https://img.shields.io/badge/Status-Planning--Stage--v3.2-orange.svg)](PLAN.md)

An on-device, CPU-optimized YouTube clip-to-Shorts automation pipeline designed for consumer Windows hardware (Intel UHD / CPU execution). Transforms long-form YouTube videos into high-retention 9:16 vertical Shorts using Semantic Inference (SemIf) orchestration, word-level animated karaoke captions, and scheduled pacing.

For the exhaustive engineering specification and architecture, see **[PLAN.md](PLAN.md)**.

---

## Key Features

- **Strict Tooling Exclusivity**:
  - Video downloading strictly via `yt-dlp` (`--download-sections` for precision cuts).
  - Video rendering, audio ducking, and compositing strictly via direct `FFmpeg` and `ffprobe` subprocesses.
  - Zero MoviePy (prohibited due to memory leaks and CPU overhead); zero Playwright video downloads.
- **Persistent Daily Spread Scheduler (Up to 50 Shorts/Day)**:
  - Hard daily ceiling of **50 uploads per day** (`daily_upload_cap: 50`).
  - Strict rolling rate limiting of **maximum 7 uploads per rolling hour**.
  - Non-fixed uniform random jittered gaps (5–25 minutes) distributed across daytime active windows (e.g. 08:00–23:59).
  - Pre-renders ahead of time in background; persists queue in SQLite (`data/state.db`).
  - Intelligent catch-up behavior after PC sleep/shutdown (skips/reschedules missed slots without bursting).
  - Daily counters and quota reset synchronized to midnight Pacific Time (00:00 PT).
  - System sleep prevention via Windows Win32 `SetThreadExecutionState` during active windows.
- **Granular YouTube Data API v3 Quota Management**:
  - Transparent quota accounting based on YouTube's granular bucket architecture: `videos.insert` operates within its own dedicated bucket (default 100 calls/day, 1 unit/call) separate from the 10,000 units/day general pool (`videos.list` = 1 unit).
  - Pacing 50 uploads/day consumes 50 of the 100 upload-bucket calls and is **fully feasible on standard default quotas without requiring a quota extension audit**, while reserving 50 calls as headroom for retries.
  - Reconciles conflicting official documentation regarding unverified project visibility with empirical verification on first upload, defaulting privacy status to `unlisted`/`private` for creator review.
  - Dual-bucket SQLite quota tracker with auto-throttling and 3 fallback modes: `queue_next_day`, Playwright Studio fallback, or local export.
- **Multimodal AI Edit Planning (`gemini_video`)**:
  - Optional AI edit planner leveraging Google's Gemini Files API video understanding to output a structured **Edit Decision List (EDL JSON)**: hook trims, dead-air jump-cuts, and camera zoom punch-ins.
  - Executed deterministically by FFmpeg; falls back cleanly to transcript-only SemIf planning if disabled or unavailable.
- **Gemini Spark Companion Integration**:
  - Loosely coupled companion workflow for Google's 24/7 personal background AI agent (Gemini Spark).
  - Includes ready-to-use Spark 'Skills' research prompt templates, a dashboard JSON/Markdown import modal, and a watched `inbox/` file drop folder.
- **Decoupled Provider Abstraction**:
  - Seamlessly switch between Local GGUF (`llama-cpp-python` on CPU), Google Gemini, and OpenRouter with **zero hardcoded model names** (`model: null` dynamically resolved at runtime).
- **High-Dopamine Compositor Engine**:
  - Dual vertical split-screen (speaker on top, gameplay on bottom with random start offsets).
  - SubStation Alpha (`.ass`) karaoke subtitles with active word pop animations and mobile safe-zone margins.
  - First-3s hook title card, dynamic top progress bar, and dialogue volume normalization (`loudnorm`).
- **Interactive Local Dashboard & Headless CLI**:
  - FastAPI web UI running on `http://localhost:8765` with HTML5 video preview player, queue management, quota meter, and scheduler controls.

---

## Important Legal & Compliance Disclaimer

> [!CAUTION]
> **Attribution Does Not Equal Copyright Permission**:
> Including an attribution disclaimer or original creator link in video descriptions does **not** grant legal copyright immunity under US Fair Use or international copyright law. Original rights-holders retain the full right to issue YouTube Content ID claims or DMCA strikes.
> 
> Furthermore, publishing up to 50 near-duplicate-format Shorts per day carries risk under YouTube's spam, repetitive content, and inauthentic activity policies. Additionally, separate channel-level daily upload limits apply to newer or unverified channels. Creators are strongly advised to inject editorial variety, rotate backgrounds and hooks, and prioritize curated quality over mechanical volume.
