# AutoShorts v1.0

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Version: v1.0](https://img.shields.io/badge/Version-v1.0-brightgreen.svg)](pyproject.toml)
[![Tests: 396 passed](https://img.shields.io/badge/Tests-396%20passed-brightgreen.svg)](tests/)
[![Status: Production Release](https://img.shields.io/badge/Status-v1.0--Production-success.svg)](PLAN.md)

An on-device, CPU-optimized YouTube clip-to-Shorts and autonomous faceless video creation pipeline designed for consumer Windows hardware (Intel UHD / CPU execution). Transforms long-form YouTube videos and neural text scripts into high-retention 9:16 vertical Shorts using Semantic Inference (SemIf) orchestration, word-level animated karaoke captions, multi-layout compositing, live viral research, and closed-loop YouTube Shorts analytics.

For the exhaustive engineering specification and architecture, see **[PLAN.md](PLAN.md)**.

---

## What's New in v1.0

- 🎛️ **4-View Studio Command Center**: Unified web workspace featuring `🎬 Shorts Studio & Queue`, `🔥 Viral Research & Trending Finder`, `🎙️ Faceless AI Studio`, and `📊 YouTube Shorts Analytics`.
- 📐 **5 Vertical 9:16 Layout Modes**: Seamlessly switch between `split_50_50`, `split_70_30`, `full_blur_bg`, `full_center_crop`, and `pip_gameplay` layouts with custom background selection.
- 📱 **9:16 Live Phone Mockup Previewer**: Real-time smartphone viewport with a toggleable YouTube Shorts Mobile UI Safe-Zone HUD (title, channel pill, right-side action buttons, sound ticker) to guarantee 100% caption legibility.
- 🎨 **7 Studio Caption Presets & ⚡ Fast 1-Click Re-Burn**: Choose between `neon`, `hormozi`, `mrbeast`, `pill`, `minimal`, `cyberpunk`, and `cinematic` SubStation Alpha (`.ass`) caption presets. Adjust casing, colors, and margins, and re-burn subtitles in seconds without re-downloading or re-rendering base audio/video.
- 📊 **In-App YouTube Shorts Analytics & Closed-Loop A/B Telemetry**: Multi-source waterfall telemetry (YouTube Data API v3 batch `videos.list`, YouTube Analytics API v2, zero-quota `yt-dlp` fallback, and Playwright Studio scraper), Shorts Viral Score (SVS), and **👑 Winning Style Recipe** 1-click cloning.
- 🔥 **Built-In Autonomous Viral Research Agent**: Live trending YouTube Shorts & podcasts discovery with direct clickable links and `⚡ 1-Click Send to Pipeline`, Highest Payout Niche RPM/CPM Matrix with interactive Revenue Calculator, Trending Viral Songs library with 1-click background music downloader (`assets/music/`), and AI Viral Strategy Brief Copilot.
- 🎙️ **Autonomous Faceless AI Shorts Studio**: Generate viral Shorts from scratch using Microsoft Edge Neural TTS (`edge-tts`) voiceovers with word-boundary timing, SQLite `topic_history` de-duplication memory, and 1080×1920 pure FFmpeg composition.
- ⚙️ **In-App Settings Modal & YouTube Data API v3 In-App OAuth**: Configure LLM providers, API keys, caption defaults, and YouTube Data API v3 credentials live from the web dashboard header without manual file editing.
- 🛡️ **Playwright MS Edge Studio Auto-Fallback**: Automatic mid-flight failover to headless Microsoft Edge Studio browser automation whenever YouTube API quotas are exhausted (HTTP 403 / `quotaExceeded`), ensuring 100% upload delivery continuity.

---

## 4-View Studio Command Center

AutoShorts provides an all-in-one desktop studio operating on `http://localhost:8765`, divided into four dedicated views:

```mermaid
flowchart TD
    subgraph "AutoShorts Command Center"
        V1["🎬 Shorts Studio & Queue<br/>• Live Link Preview<br/>• 5 Layout Modes<br/>• 7 Caption Presets<br/>• 9:16 Phone Mockup HUD<br/>• Fast 1-Click Re-Burn"]
        V2["🔥 Viral Research Agent<br/>• Live Trending Finder<br/>• Clickable Video/Podcast Links<br/>• Niche RPM/CPM Matrix<br/>• 1-Click Viral BGM Downloader<br/>• AI Strategy Briefs"]
        V3["🎙️ Faceless AI Studio<br/>• 7 High-RPM Niches<br/>• SQLite Topic Memory<br/>• Free Edge Neural TTS<br/>• Word-Boundary Subtitles<br/>• 1080x1920 FFmpeg Master"]
        V4["📊 YouTube Shorts Analytics<br/>• Multi-Source Waterfall<br/>• Shorts Viral Score (SVS)<br/>• Retention Curves (APV)<br/>• Winning Style Recipe Cloning"]
    end
```

### 1. 🎬 Shorts Studio & Queue
- **Live Link Preview**: Real-time thumbnail, title, channel name, and duration probe upon pasting YouTube URLs (`yt-dlp --dump-single-json`).
- **Layout & Aesthetic Customization**: Select from 5 vertical 9:16 layouts and 7 caption presets, customize primary and highlight colors, and choose background gameplay footage (Minecraft, Subway Surfers, GTA stunts, or Pexels stock).
- **9:16 Live Phone Mockup Previewer**: Test video framing inside an interactive mobile viewport with YouTube Shorts UI overlay simulation to avoid caption clipping behind mobile buttons.
- **Transcript Editor & ⚡ Fast 1-Click Re-Burn**: Edit transcribed sentences and regenerate SubStation Alpha subtitles within seconds via `/api/jobs/{id}/re-burn` without re-downloading or re-rendering video.
- **Real-Time Job Queue & SSE Stepper**: Monitor job progression across 9 pipeline states with live Server-Sent Events (SSE) log streaming.

### 2. 🔥 Viral Research & Trending Finder
- **Live Trending Videos & Podcasts**: Discovers high-velocity YouTube Shorts and podcast moments across major niches. Every card features a **direct clickable YouTube link** to inspect the source and a **`⚡ 1-Click Send to Pipeline`** button that instantly populates the Studio submission form.
- **Highest Payout Niche Matrix & Revenue Calculator**: Explores CPM/RPM dynamics across 7 lucrative niches (Finance & Wealth, AI & Tech, Business & Startups, Dark Psychology, Stoicism, Space & Physics, Health & Biohacking). Interactive calculator projects monthly revenue based on daily views and niche RPM.
- **Trending Viral Songs & 1-Click BGM Downloader**: Curated catalog of copyright-safe viral background tracks (Drift Phonk 138 BPM, Dark Synthwave 120 BPM, Lo-Fi Chill 80 BPM, Fast Tension Ticks 132 BPM) with one-click automated downloads into `assets/music/`.
- **AI Viral Strategy Brief Copilot**: Generates tailored strategy briefs detailing psychological hook triggers, pacing strategies, visual background recommendations, and thumbnail concepts for any target topic.

### 3. 🎙️ Autonomous Faceless AI Shorts Studio
- **End-to-End Autonomous Generation**: Produces complete faceless Shorts without requiring long-form video input.
- **SQLite Topic De-Duplication Memory**: Tracks every generated topic in `data/state.db` (`topic_history` table) so your channels never repeat content ideas.
- **Free High-Fidelity Neural TTS**: Leverages Microsoft Edge Neural TTS (`edge-tts`) across male and female voice personas in English, Spanish, Hindi, German, French, Portuguese, and Japanese.
- **Exact Word-Boundary Karaoke Synchronization**: Captures exact word timing boundaries during neural audio synthesis to generate sample-accurate SubStation Alpha (`.ass`) karaoke subtitles.
- **Pure FFmpeg 1080×1920 Compositor**: Composites neural audio, gameplay/stock footage, background music, and animated subtitles strictly via direct FFmpeg subprocesses with zero MoviePy overhead.

### 4. 📊 YouTube Shorts Analytics & Closed-Loop A/B Telemetry
- **Multi-Source Waterfall Telemetry**:
  1. *YouTube Data API v3*: Batch `videos.list` fetching official view counts, likes, comments, and tags.
  2. *YouTube Analytics API v2*: Channel and video-level audience retention curves and average percentage viewed (APV).
  3. *Zero-Quota yt-dlp Fallback*: Public metadata scraping when API quota units are conserved.
  4. *Playwright YouTube Studio Scraper*: Automated headless browser scraping of private Studio metrics (subscriber conversion, impression click-through rate [CTR]).
- **Shorts Viral Score (SVS)**: Proprietary 0–100 algorithm weighting view velocity (40 pts), duration sweet-spot (25 pts), engagement ratio (20 pts), and title hook sentiment (15 pts).
- **👑 Winning Style Recipe Cloning**: Analyzes your channel's top-performing Shorts, extracts their winning configuration (aspect layout, subtitle preset, background type, duration), and provides a one-click button to apply that exact winning recipe to future pipeline jobs.

---

## 5 Vertical 9:16 Layout Modes

AutoShorts supports five distinct vertical composition layouts:

| Layout Mode | Structure & Visual Hierarchy | Best Suited For |
| :--- | :--- | :--- |
| **`split_50_50`** *(Default)* | Dual vertical split: speaker cropped 9:16 on the top 50% (`540×960`), background gameplay or B-roll anchored on the bottom 50% (`540×960`). | High-energy podcasts, reaction videos, gaming commentary |
| **`split_70_30`** | Primary video commands the top 70% (`756×1344`) of the frame with a compact 30% (`324×576`) gameplay anchor at the base. | Technical demonstrations, presentations, software walkthroughs |
| **`full_blur_bg`** | 16:9 widescreen source centered horizontally and vertically over an aesthetically blurred, scaled 9:16 background layer. | Cinematic interviews, landscape documentaries, widescreen movie clips |
| **`full_center_crop`** | Source video cropped and scaled directly to fill the full 1080×1920 canvas (`crop=ih*9/16:ih`). | Face-to-camera monologue, single-speaker interviews, mobile-shot footage |
| **`pip_gameplay`** | Full-screen 9:16 gameplay footage with a circular or rounded-corner picture-in-picture speaker bubble overlay. | Commentary streams, storytime videos, educational gaming |

### 9:16 Live Phone Mockup Previewer & Safe-Zone HUD

Mobile video platforms place UI overlays (channel icon, title, like, comment, share, and sound ticker) over vertical videos. The AutoShorts Live Phone Mockup Previewer renders an interactive **YouTube Shorts Mobile UI Safe-Zone HUD** overlay:

```
┌─────────────────────────────────┐
│ [Top Progress Bar / Safe Zone]  │
│                                 │
│                                 │
│         [PRIMARY CONTENT]       │
│                                 │
│                                 │
│     ┌─────────────────────┐     │  [Like Button]
│     │   ACTIVE SUBTITLES  │     │  [Dislike]
│     │   (Safe Center Zone)│     │  [Comments]
│     └─────────────────────┘     │  [Share]
│                                 │  [Remix]
│ [Channel Pill & Subscribe]      │
│ [Video Title & Hashtags]        │  [Sound Disc]
└─────────────────────────────────┘
```

The HUD ensures subtitle lines and key visual subjects remain centered and completely unobstructed by native mobile platform controls.

---

## 7 Studio Caption Presets & ⚡ Fast Re-Burn

AutoShorts includes 7 high-retention SubStation Alpha (`.ass`) caption rendering presets:

| Preset | Visual Style & Behavior | Typical Use Case |
| :--- | :--- | :--- |
| **`neon`** *(Default)* | Vibrant yellow active karaoke pop (`#FFFF00`), dynamic scaling bounce (100% &rarr; 118% &rarr; 100%), micro-tilt angle pop (`\frz-2`), deep black outline, and keyword color accents. | High-retention clips, viral moments, gaming, fast dialogue |
| **`hormozi`** | Ultra-bold punchy text (The Bold Font / Montserrat Black) with bright yellow active words, deep green keyword accents, and heavy black stroke. | Motivation, business, direct-response advice, personal branding |
| **`mrbeast`** | Energetic pop font with dynamic orange, lime, and yellow highlights, playful active scale wobble, and thick drop shadows. | Challenges, surprising facts, viral entertainment |
| **`pill`** | High-contrast bounded dark translucent rounded box backdrop behind active words (`\bord10\shad0`). 100% legibility over any footage. | Busy Minecraft parkour, subway surfers, motion-heavy footage |
| **`minimal`** | Clean, modern sans-serif typography with subtle outline and soft shadow. Zero distracting animations; crisp word-by-word active focus. | Educational podcasts, technical commentary, interviews |
| **`cyberpunk`** | Neon cyan text with hot pink glow pulses, futuristic monospace aesthetic, and digital chromatic accenting. | AI & technology, gaming lore, crypto, sci-fi facts |
| **`cinematic`** | Warm cream and gold serif/sans typography with elegant kerning, subtle fade transitions, and cinematic letterboxing. | History, stoicism philosophy, deep storytelling, audiobooks |

### ⚡ Fast 1-Click Re-Burn Engine
When tweaking caption text, font sizes, colors, or styles:
- AutoShorts bypasses the entire download and audio alignment pipeline.
- Calls `/api/jobs/{id}/re-burn` to re-generate the SubStation Alpha `.ass` file and burn it into the pre-composited base video in seconds.
- Allows rapid transcript corrections and aesthetic experimentation without wasting compute or waiting for re-renders.

---

## Core Architecture & Engine Invariants

```
Long-Form YouTube URL / Topic Prompt
  │
  ├── [Ingestion & Discovery] (yt-dlp metadata probe / live link preview)
  ├── [Transcription & Alignment] (faster-whisper CPU int8 / edge-tts word events)
  ├── [SemIf Decision Engine] (Logit softmax scoring / Gemini video EDL planning)
  ├── [Compositor Engine] (Pure FFmpeg 1080x1920: 5 layouts + 7 caption presets)
  ├── [Interactive Review & Re-Burn] (9:16 Live phone previewer + safe-zone HUD)
  ├── [Daily Spread Scheduler] (50/day cap, 7/hr rolling limit, Pacific Time reset)
  ├── [Upload & Fallback Engine] (YouTube Data API v3 -> Playwright MS Edge Studio fallback)
  └── [Closed-Loop Analytics] (Waterfall telemetry -> SVS scoring -> Winning style cloning)
```

1. **Strict Tooling Exclusivity**:
   - Video downloading strictly via `yt-dlp` (`--download-sections` for precision cuts).
   - Video rendering, audio ducking, and compositing strictly via direct `FFmpeg` and `ffprobe` subprocesses.
   - Zero MoviePy (prohibited due to memory leaks and CPU overhead); zero Playwright video downloads.
2. **Persistent Daily Spread Scheduler (Up to 50 Shorts/Day)**:
   - Hard daily ceiling of **50 uploads per day** (`daily_upload_cap: 50`).
   - Strict rolling rate limiting of **maximum 7 uploads per rolling hour**.
   - Non-fixed uniform random jittered gaps (5–25 minutes) distributed across daytime active windows (e.g. 08:00–23:59).
   - System sleep prevention via Windows Win32 `SetThreadExecutionState` during active windows.
   - Daily quota resets synchronized to midnight Pacific Time (00:00 PT).
3. **Granular Quota Accounting & Studio Fallback**:
   - Pacing 50 uploads/day consumes 50 of the 100 dedicated `videos.insert` bucket calls, requiring **zero quota extension audits**.
   - Mid-flight failover catches `403 QuotaExceeded` and automatically deploys headless Microsoft Edge browser automation (`msedge`) to complete uploads with 0 quota cost.
4. **Hardware Optimized for Consumer Windows PCs**:
   - 100% executable on consumer multi-core CPUs with integrated Intel UHD / Iris Xe Graphics.
   - Bounded concurrency (`concurrency = 1`) to eliminate CPU thrashing and memory exhaustion.

---

## Quick Start (Windows One-Click Launcher)

> [!NOTE]
> **Why `start.bat` is created locally:**
> Windows SmartScreen often flags downloaded `.bat` files from the internet. To avoid security prompts, create your own local `start.bat` file in this directory.

### Creating Your Local `start.bat` (Step-by-Step)
*(Full standalone copy-paste guide also available in [bat.md](file:///c:/Users/Sirisha%20Rajana/Desktop/New%20folder/Automation/bat.md))*

1. In this project folder, right-click an empty space and choose **New** &rarr; **Text Document**.
2. Rename the file to **`start.bat`** (ensure file name extensions are visible in File Explorer view settings so it is named `start.bat` rather than `start.bat.txt`).
3. Right-click **`start.bat`** and click **Edit** (or open it with Notepad).
4. Copy and paste the following self-healing launcher script into the file and save:

```bat
@echo off
setlocal enabledelayedexpansion

title AutoShorts Launcher

echo =====================================================================
echo                Starting AutoShorts Web Dashboard
echo =====================================================================
echo Dashboard URL: http://localhost:8765
echo Preferred Browser: Microsoft Edge
echo.

cd /d "%~dp0"

:: 1. Detect uv or python
set "UV_CMD="
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "UV_CMD=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
)

where python >nul 2>&1
set "HAS_PYTHON=%ERRORLEVEL%"

:: 2. Check if .venv exists. If not, create it
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Virtual environment not found. Initializing .venv...
    if defined UV_CMD (
        echo [INFO] Creating virtual environment using uv...
        "%UV_CMD%" venv --python 3.12 >nul 2>&1
        if not exist ".venv\Scripts\python.exe" (
            "%UV_CMD%" venv
        )
    ) else if %HAS_PYTHON% equ 0 (
        echo [INFO] Creating virtual environment using Python venv...
        python -m venv .venv
    ) else (
        echo [ERROR] Neither Python 3.11+ nor uv was found on your system PATH!
        echo Please install Python from https://www.python.org/ or uv from https://astral.sh/uv
        echo Ensure "Add Python to PATH" is checked during setup.
        pause
        exit /b 1
    )

    if not exist ".venv\Scripts\python.exe" (
        echo [ERROR] Failed to initialize .venv virtual environment.
        pause
        exit /b 1
    )
    echo [INFO] Virtual environment created successfully.
)

:: 3. Check if core dependencies are installed
".venv\Scripts\python.exe" -c "import autoshorts, playwright, imageio_ffmpeg" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Core dependencies missing or incomplete. Installing packages...
    if defined UV_CMD (
        "%UV_CMD%" pip install -e .
    ) else (
        if not exist ".venv\Scripts\pip.exe" (
            echo [INFO] Bootstrapping pip...
            ".venv\Scripts\python.exe" -m ensurepip --default-pip >nul 2>&1
        )
        ".venv\Scripts\python.exe" -m pip install -e .
    )

    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Dependency installation failed. Please check your network connection.
        pause
        exit /b 1
    )
    echo [INFO] Dependencies installed successfully.
)

:: 4. Open browser to http://localhost:8765
echo [INFO] Opening browser to http://localhost:8765 ...
start "" "http://localhost:8765"

:: 5. Launch AutoShorts
echo [INFO] Launching AutoShorts server...
".venv\Scripts\python.exe" run.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [WARNING] AutoShorts exited with error code %ERRORLEVEL%.
    pause
)

endlocal
```

### Running AutoShorts

1. **One-Click Launch**:
   - Double-click **`start.bat`**.
   - Automatically bootstraps virtual environments, opens **`http://localhost:8765`** in your default browser, and launches the web dashboard.

2. **One-Time YouTube Studio Browser Sign-In**:
   - For zero-quota browser automation uploads (`upload.mode: studio`), run:
     ```bash
     python run.py edge-login
     ```
     (or click **"Sign In with Edge"** in the web dashboard header).
   - This opens an isolated Microsoft Edge session with user profile persistence in `data/edge_profile`. Once signed into YouTube Studio, sessions persist across system reboots.

---

## Configuration & Settings Modal (⚙️)

AutoShorts features an interactive **Settings Modal** accessible from the dashboard header (click **⚙️ Settings**):

- **AI Engine / LLM Provider**:
  - Switch between `local` (CPU GGUF via `llama-cpp-python`), `gemini` (Google Gemini 1.5 Flash / Pro), and `openrouter`.
  - Enter or update `GEMINI_API_KEY` and `OPENROUTER_API_KEY` with automatic secret masking in logs.
  - Set local model paths (default: `data/models/Qwen3-0.6B-Instruct-Q8_0.gguf`).
- **Caption Style & Layout Defaults**:
  - Select default caption preset (`neon`, `hormozi`, `mrbeast`, `pill`, `minimal`, `cyberpunk`, `cinematic`).
  - Set default layout mode (`split_50_50`, `split_70_30`, `full_blur_bg`, `full_center_crop`, `pip_gameplay`).
- **Upload & Privacy Defaults**:
  - Set default Shorts visibility: `public`, `unlisted`, or `private`.
  - Toggle between `api`, `studio`, and `local` upload modes.
  - Set quota exhaustion fallback behavior (`studio`, `queue_next_day`, or `local`).
- **YouTube OAuth Credentials**:
  - Upload `client_secret.json` directly through the modal.
  - Initiate one-click in-app OAuth 2.0 authorization saving tokens to `config/credentials.json`.

---

## REST API Reference

The FastAPI server exposes clean REST endpoints covering all pipeline capabilities:

### Jobs & Pipeline (`/api/jobs`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/jobs` | List all jobs with status, metadata, and schedule info. |
| `POST` | `/api/jobs` | Submit a new clip job with URL, layout mode, caption preset, and instructions. |
| `GET` | `/api/jobs/{id}` | Retrieve comprehensive job status, candidates, EDL, and rendered paths. |
| `POST` | `/api/jobs/{id}/re-burn` | ⚡ Fast 1-click re-burn of ASS subtitles into base video with modified styling. |
| `DELETE` | `/api/jobs/{id}` | Cancel job and purge intermediate files via `SafetyCleaner`. |

### Built-In Viral Research Agent (`/api/research`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/research/trending` | Fetch live trending YouTube Shorts and podcasts with direct clickable links. |
| `GET` | `/api/research/payout-matrix` | Retrieve RPM/CPM intelligence and calculate projected revenue across niches. |
| `GET` | `/api/research/viral-music` | List trending royalty-free viral background audio tracks. |
| `POST` | `/api/research/download-music` | 1-click download of selected viral audio track into `assets/music/`. |
| `POST` | `/api/research/generate-brief` | Generate an AI Viral Strategy Brief Copilot recommendation for a given topic. |

### Autonomous Faceless AI Shorts Studio (`/api/faceless`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/faceless/voices` | List available Microsoft Edge Neural TTS voices by language and historical topics. |
| `POST` | `/api/faceless/generate-script` | Generate high-retention hook, script, and SEO tags with anti-duplication memory. |
| `POST` | `/api/faceless/create` | Synthesize neural TTS audio, build subtitles, composite 1080×1920 video, and queue. |
| `GET` | `/api/faceless/topics` | Retrieve SQLite `topic_history` de-duplication log across channels. |

### YouTube Shorts Analytics (`/api/analytics`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/analytics/overview` | Summary KPI cards (total views, avg retention, avg SVS, top Shorts). |
| `GET` | `/api/analytics/videos` | Video performance table with views, APV, likes, SVS, and layout attribution. |
| `POST` | `/api/analytics/refresh` | Trigger multi-source waterfall refresh (API v3 &rarr; Analytics v2 &rarr; yt-dlp &rarr; Studio). |
| `GET` | `/api/analytics/attribution/winning-style` | Extract the winning layout/caption/background recipe from top-performing Shorts. |

### Settings & Quota (`/api/settings`, `/api/quota`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/settings` | Get current settings with masked API keys. |
| `POST` | `/api/settings` | Atomically update configuration keys and `.env` variables. |
| `POST` | `/api/settings/upload-client-secret` | Upload Google OAuth `client_secret.json`. |
| `POST` | `/api/settings/oauth/authorize` | Launch in-app Google OAuth consent flow for YouTube Data API v3. |
| `GET` | `/api/quota` | Current daily upload count, rolling limit status, and Pacific Time reset timer. |

---

## CLI Reference

```bash
# Launch web dashboard in Microsoft Edge (default)
python run.py

# Process links directly from command line
python run.py run --links "https://www.youtube.com/watch?v=..." --auto-upload

# One-time Edge authentication for YouTube Studio upload mode
python run.py edge-login

# Run upload scheduler tick
python run.py scheduler --once

# Inspect current daily quota allocation and rolling limits
python run.py quota

# Clean up stale temporary files older than 24 hours
python run.py clean --older-than 24
```

---

## Important Legal & Compliance Disclaimer

> [!CAUTION]
> **Attribution Does Not Equal Copyright Permission**:
> Including an attribution disclaimer or original creator link in video descriptions does **not** grant legal copyright immunity under US Fair Use or international copyright law. Original rights-holders retain the full right to issue YouTube Content ID claims or DMCA strikes.
> 
> Furthermore, publishing up to 50 near-duplicate-format Shorts per day carries risk under YouTube's spam, repetitive content, and inauthentic activity policies. Additionally, separate channel-level daily upload limits apply to newer or unverified channels. Creators are strongly advised to inject editorial variety, rotate backgrounds and hooks, and prioritize curated quality over mechanical volume.
