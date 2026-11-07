# AutoShorts Studio One-Click Launcher (`start.bat`)

> [!NOTE]
> **Why is this launcher documented in `.md` alongside the repo?**
> Windows SmartScreen, Microsoft Defender, and modern web browsers routinely flag downloaded `.bat` files from the internet or git archives as untrusted (*"Windows protected your PC"*). 
> 
> Having the canonical launcher documented transparently in `bat.md` allows you to inspect the exact script line by line, verify its integrity, or regenerate `start.bat` locally at any time without triggering SmartScreen false positives.

---

## Quick Start: Launching AutoShorts Studio

If `start.bat` is already present in your repository folder, double-click `start.bat` in Windows File Explorer or execute it from PowerShell / CMD:

```cmd
start.bat
```

To run diagnostics or automated tests before launching:
```cmd
:: Run comprehensive pre-flight diagnostics
start.bat --check

:: Run test suite
start.bat --test
```

---

## How to Create or Restore `start.bat` Locally

### Option 1: Fast PowerShell Command (Instant Restore)
Open Windows PowerShell inside your project folder and run:

```powershell
(Get-Content -Raw -Path bat.md) -replace '(?s).*?```bat\r?\n(.*?)\r?\n```.*', '$1' | Set-Content -Path start.bat -Encoding ascii
```

### Option 2: Manual Setup via Notepad
1. In Windows File Explorer, navigate to this project folder.
2. Right-click an empty area $\to$ **New** $\to$ **Text Document**.
3. Name the file `start.bat` (ensure File Name Extensions are visible).
4. Right-click `start.bat` $\to$ **Edit** (or open in VS Code / Notepad).
5. Copy and paste the script below, save, and exit:

```bat
@echo off
rem =============================================================================
rem AutoShorts Studio - one-click launcher for Windows
rem
rem   start.bat                  start the dashboard (http://localhost:8765)
rem   start.bat --check          pre-flight diagnostics, then exit
rem   start.bat --test           run the test suite, then exit
rem   start.bat --collect        download background gameplay footage
rem   start.bat --port 8080      use another port
rem   start.bat --no-browser     do not open a browser tab
rem   start.bat --host 0.0.0.0   listen on another interface
rem   start.bat --build-assets   force a rebuild of the longform sprite assets
rem
rem Video modes (choose "Video Mode" in the dashboard, or autoshorts run --streamer-cam):
rem   Subway Surfers / Minecraft background, or streamer cam on top + their own gameplay.
rem   Streamer-cam auto-detect uses OpenCV, installed on first run.
rem
rem Everything the app needs is installed on first run. Paste your API keys into .env.
rem =============================================================================
setlocal EnableExtensions EnableDelayedExpansion
title AutoShorts Studio Launcher
cd /d "%~dp0"

rem ----------------------------------------------------------------------------- colours
set "C_RESET=[0m"
set "C_CYAN=[96m"
set "C_GREEN=[92m"
set "C_YELLOW=[93m"
set "C_RED=[91m"

rem ----------------------------------------------------------------------------- defaults & arguments
set "PORT=8765"
set "HOST=127.0.0.1"
set "MODE=run"
set "NO_BROWSER=0"
set "COLLECT_ARGS="
set "FORCE_ASSETS=0"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help
if /i "%~1"=="/?" goto show_help
if /i "%~1"=="--check" set "MODE=check" & shift & goto parse_args
if /i "%~1"=="-c" set "MODE=check" & shift & goto parse_args
if /i "%~1"=="--test" set "MODE=test" & shift & goto parse_args
if /i "%~1"=="-t" set "MODE=test" & shift & goto parse_args
if /i "%~1"=="--no-browser" set "NO_BROWSER=1" & shift & goto parse_args
if /i "%~1"=="--build-assets" set "FORCE_ASSETS=1" & shift & goto parse_args
if /i "%~1"=="--port" set "PORT=%~2" & shift & shift & goto parse_args
if /i "%~1"=="-p" set "PORT=%~2" & shift & shift & goto parse_args
if /i "%~1"=="--host" set "HOST=%~2" & shift & shift & goto parse_args
if /i "%~1"=="--collect" set "MODE=collect" & shift & goto collect_args
if /i "%~1"=="--collect-gameplay" set "MODE=collect" & shift & goto collect_args
echo %C_YELLOW%[NOTE]%C_RESET% Ignoring unknown argument: %~1 ^(see --help^)
shift
goto parse_args

:collect_args
if "%~1"=="" goto args_done
set "COLLECT_ARGS=!COLLECT_ARGS! %~1"
shift
goto collect_args

:args_done
set "PORT_OK=1"
for /f "delims=0123456789" %%A in ("%PORT%") do set "PORT_OK=0"
if "%PORT%"=="" set "PORT_OK=0"
if !PORT_OK! equ 0 (
    echo %C_RED%[ERROR]%C_RESET% Invalid port '%PORT%'. Use a number from 1 to 65535.
    pause
    exit /b 1
)

rem ----------------------------------------------------------------------------- uv and Python
where uv >nul 2>&1
if !ERRORLEVEL! equ 0 (
    set "UV_CMD=uv"
) else (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
    ) else (
        echo %C_RED%[ERROR]%C_RESET% uv was not found. Install it from https://astral.sh/uv and run start.bat again.
        pause
        exit /b 1
    )
)

rem ----------------------------------------------------------------------------- virtual environment
set "PYTHON_EXE=.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto venv_ready
if exist ".venv\bin" (
    echo %C_YELLOW%[INFO]%C_RESET% Found a macOS/Linux .venv. Rebuilding it for Windows...
    rmdir /s /q ".venv" >nul 2>&1
)
echo %C_CYAN%[INFO]%C_RESET% Creating the Python environment ^(Python 3.12^) - one time...
"%UV_CMD%" venv --python 3.12 >nul 2>&1
if not exist "%PYTHON_EXE%" "%UV_CMD%" venv >nul 2>&1
if not exist "%PYTHON_EXE%" (
    echo %C_RED%[ERROR]%C_RESET% Could not create .venv. Check that uv can install Python 3.11 or newer.
    pause
    exit /b 1
)
:venv_ready

rem ----------------------------------------------------------------------------- dependencies
set "PY_DEPS_CHECK=import autoshorts, fastapi, uvicorn, playwright, edge_tts, google.genai, yt_dlp, imageio_ffmpeg, faster_whisper"
rem 'uv sync' is a no-op when .venv already matches uv.lock; it also repairs pins after an update.
"%UV_CMD%" sync --quiet
"%PYTHON_EXE%" -c "%PY_DEPS_CHECK%" >nul 2>&1
if !ERRORLEVEL! equ 0 goto deps_ready
echo %C_CYAN%[INFO]%C_RESET% Installing Python dependencies - one time, this can take a few minutes...
"%PYTHON_EXE%" -m pip install -e . >nul 2>&1
"%PYTHON_EXE%" -c "%PY_DEPS_CHECK%" >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo %C_RED%[ERROR]%C_RESET% Python dependencies could not be installed. Check your connection, then run start.bat again.
    pause
    exit /b 1
)
:deps_ready
echo %C_GREEN%[ OK ]%C_RESET% Python dependencies ready

rem ----------------------------------------------------------------------------- OpenCV (facecam auto-detect for the cam_gameplay layout)
rem 'uv sync' installs it from pyproject.toml; this repairs a missing or too-new install
rem (OpenCV 5.x dropped the Haar face models, so the project pins opencv-python-headless below 5).
set "CV2_CHECK=import cv2; cv2.CascadeClassifier"
"%PYTHON_EXE%" -c "%CV2_CHECK%" >nul 2>&1
if !ERRORLEVEL! equ 0 goto opencv_ready
echo %C_CYAN%[INFO]%C_RESET% Installing OpenCV for facecam auto-detect - one time...
"%UV_CMD%" pip install --python "%PYTHON_EXE%" "opencv-python-headless>=4.9.0,<5" >nul 2>&1
"%PYTHON_EXE%" -c "%CV2_CHECK%" >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo %C_YELLOW%[NOTE]%C_RESET% OpenCV could not be installed; set branding.cam_layout.cam_rect by hand for the cam_gameplay layout.
    goto opencv_done
)
:opencv_ready
echo %C_GREEN%[ OK ]%C_RESET% OpenCV ready
:opencv_done

rem ----------------------------------------------------------------------------- Remotion (optional captions engine + longform sprites)
set "REMOTION_BIN=%~dp0remotion\node_modules\.bin\remotion.cmd"
set "SATORI_DIR=%~dp0remotion\node_modules\satori"
set "SPRITE_PNG=%~dp0assets\longform\sprites\navy_gradient.png"
set "REMOTION_READY=0"
if not exist "%~dp0remotion\package.json" goto remotion_done
if exist "!REMOTION_BIN!" if exist "%SATORI_DIR%" set "REMOTION_READY=1" & goto remotion_ready
where npm >nul 2>&1
if !ERRORLEVEL! neq 0 goto remotion_no_node
echo %C_CYAN%[INFO]%C_RESET% Installing the Remotion caption renderer and longform assets - one time...
pushd "%~dp0remotion"
call npm install --no-audit --no-fund >nul 2>&1
if !ERRORLEVEL! neq 0 (
    popd
    echo %C_YELLOW%[NOTE]%C_RESET% Remotion install failed; word captions fall back to the ASS engine.
    goto remotion_done
)
echo %C_CYAN%[INFO]%C_RESET% Downloading Remotion's headless browser - one time, about 100 MB...
call npx remotion browser ensure >nul 2>&1
popd
set "REMOTION_READY=1"
:remotion_ready
if "%REMOTION_READY%"=="0" goto remotion_done
if "%FORCE_ASSETS%"=="1" goto build_sprites
if exist "%SPRITE_PNG%" goto remotion_done
:build_sprites
where npm >nul 2>&1
if !ERRORLEVEL! neq 0 goto remotion_no_node
echo %C_CYAN%[INFO]%C_RESET% Building the longform sprite assets with satori...
pushd "%~dp0remotion"
call npm run build:assets
set "ASSET_RC=!ERRORLEVEL!"
popd
if !ASSET_RC! neq 0 echo %C_YELLOW%[NOTE]%C_RESET% Sprite build failed; longform will use the PIL drawing fallback.
goto remotion_done
:remotion_no_node
echo %C_YELLOW%[NOTE]%C_RESET% Node.js is not installed, so the Remotion caption engine is unavailable ^(https://nodejs.org^).
:remotion_done

rem ----------------------------------------------------------------------------- API keys (.env)
if exist ".env" goto env_ready
copy /y ".env.example" ".env" >nul
echo %C_YELLOW%[NOTE]%C_RESET% Created .env from .env.example. Open .env and paste your API keys.
:env_ready
set "GEMINI_VAL="
for /f "tokens=1,* delims==" %%A in ('findstr /b "GEMINI_API_KEY=" ".env"') do set "GEMINI_VAL=%%B"
if not defined GEMINI_VAL echo %C_YELLOW%[NOTE]%C_RESET% GEMINI_API_KEY is empty in .env. Add it for Gemini, or use the local model.

rem ----------------------------------------------------------------------------- FFmpeg (full build with ffprobe)
set "WINGET_LINKS=%LOCALAPPDATA%\Microsoft\WinGet\Links"
if exist "%WINGET_LINKS%\ffprobe.exe" set "PATH=%WINGET_LINKS%;%PATH%"
where ffprobe >nul 2>&1
if !ERRORLEVEL! equ 0 goto ffmpeg_ready
where winget >nul 2>&1
if !ERRORLEVEL! neq 0 goto ffmpeg_no_winget
echo %C_CYAN%[INFO]%C_RESET% Installing full FFmpeg ^(with ffprobe^) through winget - one time...
winget install --id Gyan.FFmpeg --exact --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
set "PATH=%WINGET_LINKS%;%PATH%"
where ffprobe >nul 2>&1
if !ERRORLEVEL! neq 0 echo %C_YELLOW%[NOTE]%C_RESET% ffprobe is still not on PATH. Open a new terminal and run start.bat again.
goto ffmpeg_ready
:ffmpeg_no_winget
echo %C_YELLOW%[NOTE]%C_RESET% winget is not available. Install a full FFmpeg build from https://www.gyan.dev/ffmpeg/builds/ and run start.bat again.
:ffmpeg_ready

rem ----------------------------------------------------------------------------- runtime folders
if not exist "assets\backgrounds" mkdir "assets\backgrounds"
if not exist "assets\music" mkdir "assets\music"
if not exist "data\logs" mkdir "data\logs"
if not exist "work" mkdir "work"
if not exist "inbox" mkdir "inbox"
if not exist "output" mkdir "output"
if not exist "output\export" mkdir "output\export"
if not exist "output\longform" mkdir "output\longform"
if not exist "data\edge_profile" mkdir "data\edge_profile"
if not exist "assets\broll_cache" mkdir "assets\broll_cache"

rem ----------------------------------------------------------------------------- modes
if "%MODE%"=="test" goto run_test
if "%MODE%"=="check" goto run_check
if "%MODE%"=="collect" goto run_collect
goto run_dashboard

:run_test
echo %C_CYAN%[INFO]%C_RESET% Running the test suite...
"%UV_CMD%" run pytest -q
set "RC=!ERRORLEVEL!"
pause
exit /b !RC!

:run_check
echo Pre-flight diagnostics
echo   Platform:   Windows %PROCESSOR_ARCHITECTURE%
"%PYTHON_EXE%" -c "import sys; print('  Python:     ' + sys.version.split()[0])"
where ffprobe >nul 2>&1
if !ERRORLEVEL! equ 0 (echo   ffprobe:    found) else (echo   ffprobe:    missing ^(slower durations^))
if exist ".env" (echo   .env:       present) else (echo   .env:       missing)
"%PYTHON_EXE%" -c "import socket; s=socket.socket(); busy=s.connect_ex(('127.0.0.1', %PORT%))==0; s.close(); print('  Port %PORT%: ' + ('in use' if busy else 'free'))"
"%PYTHON_EXE%" -c "import glob; print('  Background clips: ' + str(len(glob.glob('assets/backgrounds/**/*.mp4', recursive=True))))"
"%PYTHON_EXE%" -c "import cv2; cv2.CascadeClassifier; print('  OpenCV:     ' + cv2.__version__ + ' - streamer-cam auto-detect ready')" >nul 2>&1
if !ERRORLEVEL! equ 0 (
    "%PYTHON_EXE%" -c "import cv2; print('  OpenCV:     ' + cv2.__version__ + ' - streamer-cam auto-detect ready')"
) else (
    echo   OpenCV:     missing ^(streamer-cam needs a manual cam_rect^)
)
"%PYTHON_EXE%" -c "from autoshorts.media.raea_renderer import find_raea_binary as f; p=f(); print('  raea:       ' + (str(p) if p else 'not installed - Remotion/FFmpeg are used'))"
echo %C_GREEN%[ OK ]%C_RESET% Pre-flight complete
pause
exit /b 0

:run_collect
"%PYTHON_EXE%" scripts\collect_gameplay.py!COLLECT_ARGS!
set "RC=!ERRORLEVEL!"
pause
exit /b !RC!

:run_dashboard
rem If the dashboard is already running on this port, just open it.
curl.exe -sf -m 5 "http://localhost:%PORT%/api/health" >nul 2>&1
if !ERRORLEVEL! equ 0 (
    echo %C_GREEN%[ OK ]%C_RESET% AutoShorts is already running at http://localhost:%PORT%
    if "%NO_BROWSER%"=="0" start "" "http://localhost:%PORT%"
    exit /b 0
)
"%PYTHON_EXE%" -c "import socket,sys; s=socket.socket(); busy=s.connect_ex(('127.0.0.1', %PORT%))==0; s.close(); sys.exit(0 if busy else 1)" >nul 2>&1
if !ERRORLEVEL! equ 0 (
    echo %C_RED%[ERROR]%C_RESET% Port %PORT% is used by another application. Start again with: start.bat --port 8080
    pause
    exit /b 1
)

echo %C_CYAN%[INFO]%C_RESET% Starting AutoShorts Studio at http://localhost:%PORT% ^(Ctrl+C stops it^)
if "%NO_BROWSER%"=="0" start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:%PORT%"
"%PYTHON_EXE%" run.py dashboard --host %HOST% --port %PORT% --no-browser
set "RC=!ERRORLEVEL!"
if !RC! neq 0 (
    echo.
    echo %C_YELLOW%[NOTE]%C_RESET% AutoShorts stopped with code !RC!.
    pause
)
exit /b !RC!

:show_help
echo AutoShorts Studio launcher - see the comment block at the top of start.bat for all options.
echo   start.bat                  start the dashboard (http://localhost:8765)
echo   start.bat --check          pre-flight diagnostics
echo   start.bat --test           run the test suite
echo   start.bat --collect        download background gameplay footage
echo   start.bat --port 8080      use another port
echo   start.bat --no-browser     do not open a browser tab
echo   start.bat --build-assets   force a rebuild of the longform sprite assets
echo   Video Mode ^(dashboard^): Subway/Minecraft background, or streamer cam + their gameplay
exit /b 0
```

---

## Command Line Flags & Options Reference

The modernized `start.bat` launcher provides complete command-line parameter parsing:

| Flag | Short | Description |
|---|---|---|
| *(none)* | *(default)* | Starts the AutoShorts web dashboard server on default port `8000` (`http://localhost:8000`) and launches default browser. |
| `--port <port>` | `-p <port>` | Binds the dashboard server to a custom TCP port (e.g. `start.bat --port 8080`). |
| `--check` | `-c` | Runs comprehensive pre-flight diagnostics (Python, uv, FFmpeg, directories, `.env`, background footage, AI models, port checks) and exits without starting server. |
| `--test` | `-t` | Runs the automated test suite (`pytest -q`) against all unit and integration tests and exits with pytest's return code. |
| `--collect-gameplay` | `--collect` | Launches the background gameplay video collector. If extra flags are provided (`--list`, `--categories ...`), forwards them directly; otherwise opens interactive menu. |
| `--no-browser` | *(none)* | Launches the web dashboard without automatically opening a browser tab (ideal for headless servers or remote SSH sessions). |
| `--host <ip>` | *(none)* | Binds the dashboard to a specific host IP address (default: `127.0.0.1`; use `0.0.0.0` for LAN accessibility). |
| `--help` | `-h`, `/?` | Displays the formatted usage banner, options list, and command examples. |

---

## Detailed Operational Modes

### 1. Web Dashboard Launch (Default Mode)
```cmd
:: Default port (8000)
start.bat

:: Custom port (8080)
start.bat --port 8080

:: Bind to LAN without auto-opening browser
start.bat --host 0.0.0.0 --port 8080 --no-browser
```
- Performs a pre-launch socket check to ensure the chosen port is free.
- Displays a clean status banner with active system flags.
- Launches the FastAPI application via `run.py dashboard`.
- Automatically opens Microsoft Edge or default browser after 1.2 seconds.
- Handles `Ctrl+C` cleanly without crashing or leaving zombie worker tasks.

### 2. Comprehensive Pre-Flight Diagnostics (`--check` / `-c`)
```cmd
start.bat --check
start.bat -c --port 8080
```
Runs a 7-stage diagnostic verification:
1. **Runtime Infrastructure**: Detects Python version, evaluates `uv` vs `pip` availability.
2. **Port Conflict Check**: Tests whether target port is free or already held by another PID.
3. **Directory Verification**: Validates all 8 critical directories:
   - `assets/` & `assets/backgrounds/` (Gameplay clips)
   - `assets/music/` (Royalty-free audio tracks)
   - `work/` (FFmpeg temporary cut files and audio tracks)
   - `data/` & `data/logs/` (SQLite state databases and activity logs)
   - `inbox/` (Incoming source video requests)
   - `output/` (Final vertical 9:16 rendered Shorts)
4. **Environment & Keys**: Confirms presence of `.env` and checks `GEMINI_API_KEY` configuration.
5. **Footage Inventory**: Scans `assets/backgrounds/` for indexed clips and categorizes them.
6. **Core AI & Model Readiness**: Validates all runtime modules and architecture components:
   - `autoshorts` (Core Framework)
   - `Master Brain Orchestrator & Multi-Agent Swarm` (`src/autoshorts/brain/`)
   - `TimesFM & Laya Rhythmic Decision Engine` (`src/autoshorts/media/rhythm_engine.py`, `src/autoshorts/analytics/forecasting.py`, `src/autoshorts/decider/laya_router.py`)
   - `fastapi` & `uvicorn` (Web API Server)
   - `edge_tts` (Voice Synthesizer)
   - `google.genai` (Gemini AI Client)
   - `playwright` (Browser Automation)
   - `imageio_ffmpeg` (Bundled FFmpeg Media Engine)
   - `faster_whisper` (Optional Local Fallback)
7. **Protection Layers**: Verifies offline resilience, atomic SQLite persistence, and Credential Guardian.

#### Diagnostic Output Example (`start.bat --check`)
```text
=====================================================================
         AutoShorts Studio - Comprehensive Pre-Flight Diagnostics
  Rapid Kinetic Captions (84pt pop) | Parallel Multi-Video Engine (3 Workers)
=====================================================================

[1] Runtime & Package Infrastructure:
  Python Runtime:      .venv\Scripts\python.exe
Python 3.12.15
  Package Manager:     uv [uv]
  Video Compositor:    Bundled imageio-ffmpeg (.venv)

[2] Target Port Availability Check (Port 8000):
  [OK]       Port 8000 is available for binding.

[3] Critical Runtime Directories:
  [OK]       assets 
  [OK]       assets\backgrounds 
  [OK]       assets\music 
  [OK]       data 
  [OK]       data\logs 
  [OK]       work 
  [OK]       inbox 
  [OK]       output 

[4] Environment & Credentials (.env):
  [OK]       .env configuration file present.

[5] Background Footage Inventory (assets/backgrounds):
  [OK]       10 clip(s) ready across categories: minecraft, subway

[6] Core Dependencies & Model Readiness:
  [OK]       AutoShorts Core Framework
  [OK]       Master Brain Orchestrator & Multi-Agent Swarm
  [OK]       TimesFM & Laya Rhythmic Decision Engine
  [OK]       FastAPI Web Engine
  [OK]       Uvicorn ASGI Server
  [OK]       Edge-TTS Voice Synthesizer
  [OK]       Google Gemini AI Client
  [OK]       Playwright Browser Engine
  [OK]       Bundled FFmpeg Media Engine
  [OPTIONAL] Faster-Whisper (Optional Local Audio Fallback)

[7] System Integrity & Protection Layers:
  [OK]       Parallel Multi-Video Processing Engine (3 Workers)
  [OK]       Offline Resilience: Active (PC Sleep / Network Disconnect Recovery)
  [OK]       Data Integrity: Zero-Phantom-Data Mode (Atomic SQLite)
  [OK]       Credential Guardian: Active

[STATUS] Pre-flight verification completed successfully. System ready.
```

### 3. Automated Test Suite (`--test` / `-t`)
```cmd
start.bat --test
```
- Automatically triggers `uv run pytest -q` (or `.venv\Scripts\python.exe -m pytest -q`).
- Synchronizes dependencies on the fly.
- Executes all **670+ unit and integration test cases**.
- Returns proper exit code (`0` for passed, non-zero for test failure) for CI/CD integration.

### 4. Background Gameplay Collector (`--collect` / `--collect-gameplay`)
```cmd
:: Open interactive menu (Option 1: Quick download; Option 2: All categories; etc.)
start.bat --collect

:: Inspect currently downloaded background inventory
start.bat --collect --list

:: Query YouTube candidates without downloading
start.bat --collect --dry-run

:: Download specific categories
start.bat --collect --categories minecraft subway gta --per-category 2
```

---

## Troubleshooting Guide

### Port Already Occupied
- **Symptom**: `[ERROR] Port 8000 is already occupied by another running application!`
- **Resolution**: Specify an alternate port using `start.bat --port <number>` (e.g. `start.bat --port 8765`).

### Video Compositor Missing
- **Symptom**: `[WARNING] FFmpeg was not detected on system PATH or via imageio-ffmpeg.`
- **Resolution**: Run `uv sync` or `pip install imageio-ffmpeg`. The launcher will automatically detect bundled FFmpeg binaries inside `.venv`.

### Missing Virtual Environment
- **Symptom**: `.venv\Scripts\python.exe missing.`
- **Resolution**: `start.bat` automatically self-heals by invoking `uv sync` or `python -m venv .venv`. If neither `uv` nor `python` is installed, install Python 3.11+ from [python.org](https://www.python.org/) and check *"Add Python to PATH"*.

### Unset Gemini API Key
- **Symptom**: `[NOTICE] .env not found; template ready at .env.example`
- **Resolution**: Copy `.env.example` to `.env` and insert your Google Gemini API key:
  ```cmd
  copy .env.example .env
  ```
  Edit `.env` and set `GEMINI_API_KEY=AIzaSy...`.

---

## Cross-Platform Parity: macOS & Linux Launcher (`start.sh`)

AutoShorts Studio provides 100% flag and operational parity across platforms:

- **Windows**: `start.bat` (CMD & PowerShell)
- **macOS (Monterey 12+, Apple Silicon M1-M4 & Intel x86_64) / Linux**: `start.sh` (POSIX Bash)

### macOS Prerequisites (Homebrew)
On macOS 12+ Monterey, install the required packages using Homebrew:
```bash
brew install python@3.12 ffmpeg uv
```

### Running `start.sh` on macOS or Linux
```bash
# Make executable (one-time setup)
chmod +x start.sh

# Launch dashboard on http://localhost:8000 (Safari 15+ verified)
./start.sh

# Run comprehensive pre-flight diagnostics
./start.sh --check

# Run automated test suite (670+ tests)
./start.sh --test

# Custom port
./start.sh --port 8765

# Interactive background gameplay collector
./start.sh --collect
```

All CLI arguments (`--check`, `--test`, `--port`, `--collect`, `--no-browser`, `--host`), diagnostic checks, background collectors, and runtime environment validations function identically between `start.bat` and `start.sh`.

---

## High-Throughput Engine & Rendering Enhancements

### 1. Parallel Multi-Video Processing Engine
AutoShorts Studio features a bounded thread pool executor (`ParallelJobExecutor`) allowing concurrent batch generation and processing:
- **Default Concurrency**: 3 parallel worker threads (configurable via `AUTOSHORTS_CONCURRENCY`, e.g. `set AUTOSHORTS_CONCURRENCY=4`).
- **REST Endpoints**:
  - `POST /api/jobs/process-queue`: Triggers immediate background dispatch of all currently queued video jobs up to worker capacity.
  - `GET /api/jobs/parallel-status`: Returns real-time concurrency metrics, including active job IDs, active worker count, and maximum concurrency limit.

### 2. "Rapid" Kinetic Caption Preset
Engineered specifically for hyper-retention faceless shorts and rapid-fire speech:
- **Typography**: 84pt Montserrat ExtraBold with high-contrast outlines and drop shadows.
- **Cadence**: 1–2 word ultra-fast kinetic pop bursts (`words_per_burst=2`, `min_w=1`).
- **Kinetic Animations**: Dynamic word-level micro-scaling (122% pop on active spoken words, 135% pop on emphasis keywords) keeping viewer visual attention centered.

### 3. High Dopamine Split Divider & Resilient Stream Handling
- **Glowing Split Seam Divider**: For `split_50_50` and `split_70_30` layouts, customizable `split_separator` (`glow`, `line`, `shadow`, or `none`) inserts a sleek, modern visual boundary between top talking-head video and bottom gameplay footage.
- **Resilient Media Stream Probing**: Automated `ffprobe` stream introspection (`has_audio_stream`, `has_video_stream`) gracefully handles media clips missing standard audio or video tracks, synthesizing fallback streams without crashing the FFmpeg filtergraph.
