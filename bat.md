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
(Get-Content -Raw -Path bat.md) -replace '(?s).*?```bat
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
exit /b 0
```.*', '$1' | Set-Content -Path start.bat -Encoding ascii
```

### Option 2: Manual Setup via Notepad
1. In Windows File Explorer, navigate to this project folder.
2. Right-click an empty area $\to$ **New** $\to$ **Text Document**.
3. Name the file `start.bat` (ensure File Name Extensions are visible).
4. Right-click `start.bat` $\to$ **Edit** (or open in VS Code / Notepad).
5. Copy and paste the script below, save, and exit:

```bat
@echo off
setlocal enabledelayedexpansion

title AutoShorts Studio Launcher

cd /d "%~dp0"

:: -----------------------------------------------------------------------------
:: ANSI Terminal Color Configuration (with graceful plain-text fallback)
:: -----------------------------------------------------------------------------
set "ESC="
for /f "tokens=1,2 delims=#" %%a in ('"prompt #$H#$E# & echo on & for %%b in (1) do rem"') do set "ESC=%%b"

if defined ESC (
    set "C_RESET=%ESC%[0m"
    set "C_BOLD=%ESC%[1m"
    set "C_RED=%ESC%[91m"
    set "C_GREEN=%ESC%[92m"
    set "C_YELLOW=%ESC%[93m"
    set "C_BLUE=%ESC%[94m"
    set "C_MAGENTA=%ESC%[95m"
    set "C_CYAN=%ESC%[96m"
    set "C_WHITE=%ESC%[97m"
    set "C_GRAY=%ESC%[90m"
) else (
    set "C_RESET="
    set "C_BOLD="
    set "C_RED="
    set "C_GREEN="
    set "C_YELLOW="
    set "C_BLUE="
    set "C_MAGENTA="
    set "C_CYAN="
    set "C_WHITE="
    set "C_GRAY="
)

:: -----------------------------------------------------------------------------
:: Configuration Defaults
:: -----------------------------------------------------------------------------
set "PORT=8000"
set "HOST=127.0.0.1"
set "DO_CHECK=0"
set "DO_TEST=0"
set "DO_COLLECT=0"
set "NO_BROWSER=0"
set "COLLECT_ARGS="
if not defined AUTOSHORTS_CONCURRENCY set "AUTOSHORTS_CONCURRENCY=3"

:: -----------------------------------------------------------------------------
:: 1. CLI Argument Parsing
:: -----------------------------------------------------------------------------
:parse_loop
if "%~1"=="" goto after_parse

if /i "%~1"=="--port" (
    if not "%~2"=="" (
        set "PORT=%~2"
        shift
    )
    shift
    goto parse_loop
)
if /i "%~1"=="-p" (
    if not "%~2"=="" (
        set "PORT=%~2"
        shift
    )
    shift
    goto parse_loop
)
if /i "%~1"=="--check" (
    set "DO_CHECK=1"
    shift
    goto parse_loop
)
if /i "%~1"=="-c" (
    set "DO_CHECK=1"
    shift
    goto parse_loop
)
if /i "%~1"=="--test" (
    set "DO_TEST=1"
    shift
    goto parse_loop
)
if /i "%~1"=="-t" (
    set "DO_TEST=1"
    shift
    goto parse_loop
)
if /i "%~1"=="--collect-gameplay" goto handle_collect
if /i "%~1"=="--collect" goto handle_collect
if /i "%~1"=="--no-browser" (
    set "NO_BROWSER=1"
    shift
    goto parse_loop
)
if /i "%~1"=="--host" (
    if not "%~2"=="" (
        set "HOST=%~2"
        shift
    )
    shift
    goto parse_loop
)
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help
if /i "%~1"=="/?" goto show_help

echo [WARNING] Unrecognized argument: %~1 (use --help for valid options)
shift
goto parse_loop

:handle_collect
set "DO_COLLECT=1"
shift
goto parse_collect_args

:parse_collect_args
if "%~1"=="" goto after_parse
set "COLLECT_ARGS=!COLLECT_ARGS! %1"
shift
goto parse_collect_args

:show_help
echo %C_CYAN%=====================================================================%C_RESET%
echo                 %C_BOLD%AutoShorts Studio Launcher%C_RESET%
echo   Rapid Kinetic Captions (84pt pop) ^| Parallel Processing (3 Workers)
echo %C_CYAN%=====================================================================%C_RESET%
echo %C_WHITE%Usage:%C_RESET%
echo   start.bat                     Launch web dashboard at http://localhost:8000
echo   start.bat --port ^<port^>       Launch dashboard on custom port (e.g. 8080)
echo   start.bat -p ^<port^>           Short alias for --port
echo   start.bat --check             Run comprehensive pre-flight diagnostics and exit
echo   start.bat -c                  Short alias for --check
echo   start.bat --test              Run automated test suite (pytest) and exit
echo   start.bat -t                  Short alias for --test
echo   start.bat --collect-gameplay  Launch background gameplay downloader interactively
echo   start.bat --collect           Short alias for --collect-gameplay
echo   start.bat --no-browser        Launch dashboard without auto-opening browser tab
echo   start.bat --host ^<ip^>         Custom host binding (default 127.0.0.1)
echo   start.bat --help              Display this help guide and available options
echo   start.bat -h, /?              Short aliases for --help
echo.
echo %C_WHITE%Examples:%C_RESET%
echo   start.bat --port 8765
echo   start.bat --check
echo   start.bat --test
echo   start.bat --collect
echo   start.bat --collect --list
echo   start.bat --collect --categories minecraft subway
echo.
exit /b 0

:after_parse

:: -----------------------------------------------------------------------------
:: 2. Detect Package Manager ^& Python Runtime
:: -----------------------------------------------------------------------------
set "UV_CMD="
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "UV_CMD=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
    set "UV_CMD=%USERPROFILE%\.cargo\bin\uv.exe"
)

where python >nul 2>&1
set "HAS_PYTHON=%ERRORLEVEL%"

:: -----------------------------------------------------------------------------
:: 3. Verify / Initialize Virtual Environment (.venv)
:: -----------------------------------------------------------------------------
set "PYTHON_EXE=.venv\Scripts\python.exe"

if not exist "!PYTHON_EXE!" goto init_venv
goto venv_ready

:init_venv
echo %C_YELLOW%[INFO]%C_RESET% Virtual environment not found (.venv\Scripts\python.exe missing).
if defined UV_CMD (
    echo %C_CYAN%[INFO]%C_RESET% Detected uv package manager. Synchronizing environment with 'uv sync'...
    "%UV_CMD%" sync
    if exist "!PYTHON_EXE!" goto venv_ready
    echo %C_CYAN%[INFO]%C_RESET% Creating virtual environment with 'uv venv --python 3.12'...
    "%UV_CMD%" venv --python 3.12 >nul 2>&1
    if not exist "!PYTHON_EXE!" "%UV_CMD%" venv
    "%UV_CMD%" pip install -e .
    goto venv_ready
)
if %HAS_PYTHON% equ 0 (
    echo %C_CYAN%[INFO]%C_RESET% Initializing virtual environment using standard python -m venv...
    python -m venv .venv
    if exist "!PYTHON_EXE!" (
        echo %C_CYAN%[INFO]%C_RESET% Installing dependencies in editable mode...
        "!PYTHON_EXE!" -m pip install --upgrade pip >nul 2>&1
        "!PYTHON_EXE!" -m pip install -e .
    )
    goto venv_ready
)

echo %C_RED%[ERROR] Neither Python 3.11+ nor uv was found on your system PATH!%C_RESET%
echo Please install Python from https://www.python.org/ or uv from https://astral.sh/uv
echo Ensure "Add Python to PATH" is checked during setup.
pause
exit /b 1

:venv_ready
if not exist "!PYTHON_EXE!" (
    echo %C_RED%[ERROR] Failed to initialize .venv virtual environment.%C_RESET%
    pause
    exit /b 1
)

:: -----------------------------------------------------------------------------
:: 4. Ensure Required Runtime Directories Exist
:: -----------------------------------------------------------------------------
if not exist "assets" mkdir "assets"
if not exist "assets\backgrounds" mkdir "assets\backgrounds"
if not exist "assets\music" mkdir "assets\music"
if not exist "data" mkdir "data"
if not exist "data\logs" mkdir "data\logs"
if not exist "work" mkdir "work"
if not exist "inbox" mkdir "inbox"
if not exist "output" mkdir "output"

:: -----------------------------------------------------------------------------
:: 5. Verify FFmpeg Availability
:: -----------------------------------------------------------------------------
set "FFMPEG_STATUS=Missing"

if exist "%~dp0bin\ffmpeg.exe" (
    set "FFMPEG_STATUS=Local Project Binary (bin\ffmpeg.exe)"
) else (
    where ffmpeg >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        set "FFMPEG_STATUS=System PATH (ffmpeg.exe)"
    ) else (
        "!PYTHON_EXE!" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set "FFMPEG_STATUS=Bundled imageio-ffmpeg (.venv)"
        )
    )
)

:: -----------------------------------------------------------------------------
:: 6. Mode Routing
:: -----------------------------------------------------------------------------
if %DO_TEST% equ 1 goto run_test
if %DO_CHECK% equ 1 goto run_check
if %DO_COLLECT% equ 1 goto run_collect
goto run_dashboard

:: -----------------------------------------------------------------------------
:: Mode: Automated Test Suite (--test / -t)
:: -----------------------------------------------------------------------------
:run_test
echo %C_CYAN%=====================================================================%C_RESET%
echo                 %C_BOLD%AutoShorts Studio - Automated Test Suite%C_RESET%
echo %C_CYAN%=====================================================================%C_RESET%
echo %C_WHITE%[INFO] Running pytest test suite...%C_RESET%
echo.
if defined UV_CMD (
    "%UV_CMD%" run pytest -q
) else (
    "!PYTHON_EXE!" -m pytest -q
)
set "TEST_CODE=%ERRORLEVEL%"
echo.
if %TEST_CODE% equ 0 (
    echo %C_GREEN%[SUCCESS] All unit and integration tests passed cleanly.%C_RESET%
) else (
    echo %C_RED%[FAILURE] Test suite returned non-zero exit code: %TEST_CODE%%C_RESET%
)
exit /b %TEST_CODE%

:: -----------------------------------------------------------------------------
:: Mode: Pre-Flight Diagnostics (--check / -c)
:: -----------------------------------------------------------------------------
:run_check
echo %C_CYAN%=====================================================================%C_RESET%
echo          %C_BOLD%AutoShorts Studio - Comprehensive Pre-Flight Diagnostics%C_RESET%
echo   Rapid Kinetic Captions (84pt pop) ^| Parallel Multi-Video Engine (3 Workers)
echo %C_CYAN%=====================================================================%C_RESET%
echo.
echo %C_BOLD%[1] Runtime ^& Package Infrastructure:%C_RESET%
echo   Python Runtime:      !PYTHON_EXE!
"!PYTHON_EXE!" --version
if defined UV_CMD echo   Package Manager:     uv [!UV_CMD!]
if not defined UV_CMD echo   Package Manager:     Standard Python / pip
echo   Video Compositor:    !FFMPEG_STATUS!
if "!FFMPEG_STATUS!"=="Missing" (
    echo   %C_YELLOW%[WARNING] FFmpeg was not detected on system PATH or via imageio-ffmpeg.%C_RESET%
    echo             Rendering and video compositing will fail without FFmpeg.
)
echo.
echo %C_BOLD%[2] Target Port Availability Check (Port %PORT%):%C_RESET%
"!PYTHON_EXE!" -c "import socket; s = socket.socket(); ok = (s.connect_ex(('127.0.0.1', int('%PORT%'))) == 0); s.close(); print('  [CONFLICT] Port %PORT% is already occupied by another application!' if ok else '  [OK]       Port %PORT% is available for binding.')"
echo.
echo %C_BOLD%[3] Critical Runtime Directories:%C_RESET%
if exist "assets" ( echo   [OK]       assets ) else ( echo   %C_RED%[MISSING]  assets%C_RESET% )
if exist "assets\backgrounds" ( echo   [OK]       assets\backgrounds ) else ( echo   %C_RED%[MISSING]  assets\backgrounds%C_RESET% )
if exist "assets\music" ( echo   [OK]       assets\music ) else ( echo   %C_RED%[MISSING]  assets\music%C_RESET% )
if exist "data" ( echo   [OK]       data ) else ( echo   %C_RED%[MISSING]  data%C_RESET% )
if exist "data\logs" ( echo   [OK]       data\logs ) else ( echo   %C_RED%[MISSING]  data\logs%C_RESET% )
if exist "work" ( echo   [OK]       work ) else ( echo   %C_RED%[MISSING]  work%C_RESET% )
if exist "inbox" ( echo   [OK]       inbox ) else ( echo   %C_RED%[MISSING]  inbox%C_RESET% )
if exist "output" ( echo   [OK]       output ) else ( echo   %C_RED%[MISSING]  output%C_RESET% )
echo.
echo %C_BOLD%[4] Environment ^& Credentials (.env):%C_RESET%
"!PYTHON_EXE!" -c "from pathlib import Path; p=Path('.env'); pe=Path('.env.example'); print('  [OK]       .env configuration file present.' if p.exists() else ('  [NOTICE]   .env not found; template ready at .env.example (copy to .env to configure GEMINI_API_KEY).' if pe.exists() else '  [WARN]     Neither .env nor .env.example found.'))"
echo.
echo %C_BOLD%[5] Background Footage Inventory (assets/backgrounds):%C_RESET%
"!PYTHON_EXE!" -c "from pathlib import Path; p=Path('assets/backgrounds'); clips=[f for f in p.glob('**/*.mp4') if f.stat().st_size > 0]; cats=[d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith('.')]; print('  [OK]       ' + str(len(clips)) + ' clip(s) ready across categories: ' + ', '.join(cats) if len(clips) > 0 else '  [NOTICE]   No background clips found. Run start.bat --collect to download footage.')"
echo.
echo %C_BOLD%[6] Core Dependencies ^& Model Readiness:%C_RESET%
"!PYTHON_EXE!" -c "import importlib.util as u, sys; from pathlib import Path; b_ok = Path('src/autoshorts/brain').is_dir() and u.find_spec('autoshorts.brain') is not None; r_ok = Path('src/autoshorts/media/rhythm_engine.py').is_file() and Path('src/autoshorts/analytics/forecasting.py').is_file() and Path('src/autoshorts/decider/laya_router.py').is_file() and u.find_spec('autoshorts.media.rhythm_engine') is not None and u.find_spec('autoshorts.analytics.forecasting') is not None and u.find_spec('autoshorts.decider.laya_router') is not None; mods=[('autoshorts','AutoShorts Core Framework',True,u.find_spec('autoshorts') is not None),('brain','Master Brain Orchestrator & Multi-Agent Swarm',True,b_ok),('laya','TimesFM & Laya Rhythmic Decision Engine',True,r_ok),('fastapi','FastAPI Web Engine',True,u.find_spec('fastapi') is not None),('uvicorn','Uvicorn ASGI Server',True,u.find_spec('uvicorn') is not None),('edge_tts','Edge-TTS Voice Synthesizer',True,u.find_spec('edge_tts') is not None),('google.genai','Google Gemini AI Client',True,u.find_spec('google.genai') is not None),('playwright','Playwright Browser Engine',True,u.find_spec('playwright') is not None),('imageio_ffmpeg','Bundled FFmpeg Media Engine',True,u.find_spec('imageio_ffmpeg') is not None),('faster_whisper','Faster-Whisper (Optional Local Audio Fallback)',False,u.find_spec('faster_whisper') is not None)]; bad=[d for k,d,req,ok in mods if req and not ok]; [print('  [OK]       ' + d) for k,d,req,ok in mods if ok]; [print('  [OPTIONAL] ' + d) for k,d,req,ok in mods if not req and not ok]; [print('  [FAIL]     ' + d) for k,d,req,ok in mods if req and not ok]; sys.exit(1 if bad else 0)"
if %ERRORLEVEL% neq 0 (
    echo.
    echo %C_RED%[FAIL] Core dependencies are missing or broken. Please run 'uv sync' or 'pip install -e .'%C_RESET%
    exit /b 1
)
echo.
echo %C_BOLD%[7] System Integrity ^& Protection Layers:%C_RESET%
echo   [OK]       Parallel Multi-Video Processing Engine (3 Workers)
echo   [OK]       Offline Resilience: Active (PC Sleep / Network Disconnect Recovery)
echo   [OK]       Data Integrity: Zero-Phantom-Data Mode (Atomic SQLite)
echo   [OK]       Credential Guardian: Active
echo.
echo %C_GREEN%[STATUS] Pre-flight verification completed successfully. System ready.%C_RESET%
exit /b 0

:: -----------------------------------------------------------------------------
:: Mode: Background Gameplay Collector (--collect-gameplay / --collect)
:: -----------------------------------------------------------------------------
:run_collect
echo %C_CYAN%=====================================================================%C_RESET%
echo        %C_BOLD%AutoShorts Studio - Background Gameplay Collector%C_RESET%
echo %C_CYAN%=====================================================================%C_RESET%
echo.

if not "!COLLECT_ARGS!"=="" (
    echo %C_WHITE%[INFO] Executing gameplay collector with forwarded arguments:!COLLECT_ARGS!%C_RESET%
    echo.
    "!PYTHON_EXE!" scripts\collect_gameplay.py !COLLECT_ARGS!
    exit /b %ERRORLEVEL%
)

echo %C_BOLD%Current Gameplay Inventory:%C_RESET%
"!PYTHON_EXE!" scripts\collect_gameplay.py --list
echo.
echo %C_BOLD%Collection Menu Options:%C_RESET%
echo   [1] Quick Download: Default categories (minecraft, subway - 2 clips each)
echo   [2] Full Download: All categories (minecraft, subway, gta, etc. - 1 clip each)
echo   [3] Custom Selection: Specify custom categories
echo   [4] Dry Run: Preview YouTube search results without downloading
echo   [5] Return to Launcher / Exit
echo.

set "COLLECT_CHOICE=1"
set /p "COLLECT_CHOICE=Select an option (1-5) [Default: 1]: "

if "!COLLECT_CHOICE!"=="1" (
    echo.
    echo %C_WHITE%[INFO] Collecting default gameplay footage (minecraft, subway)...%C_RESET%
    "!PYTHON_EXE!" scripts\collect_gameplay.py --categories minecraft subway --per-category 2
    goto collect_done
)
if "!COLLECT_CHOICE!"=="2" (
    echo.
    echo %C_WHITE%[INFO] Collecting footage across all predefined categories...%C_RESET%
    "!PYTHON_EXE!" scripts\collect_gameplay.py --categories all --per-category 1
    goto collect_done
)
if "!COLLECT_CHOICE!"=="3" (
    echo.
    set "CUSTOM_CATS="
    set /p "CUSTOM_CATS=Enter category names separated by space (e.g. minecraft gta): "
    if not "!CUSTOM_CATS!"=="" (
        echo %C_WHITE%[INFO] Collecting footage for: !CUSTOM_CATS!...%C_RESET%
        "!PYTHON_EXE!" scripts\collect_gameplay.py --categories !CUSTOM_CATS!
    ) else (
        echo [NOTICE] No categories specified. Aborted.
    )
    goto collect_done
)
if "!COLLECT_CHOICE!"=="4" (
    echo.
    echo %C_WHITE%[INFO] Running dry-run preview (search without download)...%C_RESET%
    "!PYTHON_EXE!" scripts\collect_gameplay.py --categories minecraft subway --dry-run
    goto collect_done
)
if "!COLLECT_CHOICE!"=="5" (
    exit /b 0
)

:collect_done
echo.
echo %C_GREEN%[STATUS] Background gameplay collection routine completed.%C_RESET%
exit /b 0

:: -----------------------------------------------------------------------------
:: Mode: Studio Dashboard Launch (Default or with --port <port>)
:: -----------------------------------------------------------------------------
:run_dashboard
"!PYTHON_EXE!" -c "import socket, sys; s = socket.socket(); ok = (s.connect_ex(('127.0.0.1', int('%PORT%'))) == 0); s.close(); sys.exit(1 if ok else 0)"
if %ERRORLEVEL% neq 0 (
    echo.
    echo %C_RED%[ERROR] Port %PORT% is already occupied by another running application!%C_RESET%
    echo Please specify a different port using: start.bat --port ^<available-port^>
    echo Example: start.bat --port 8080
    pause
    exit /b 1
)

echo %C_CYAN%=====================================================================%C_RESET%
echo                 %C_BOLD%AutoShorts Studio Launcher%C_RESET%
echo %C_CYAN%=====================================================================%C_RESET%
echo  Local Dashboard URL:  %C_GREEN%http://localhost:%PORT%%C_RESET%
echo  Alternate URL:        %C_GREEN%http://%HOST%:%PORT%%C_RESET%
echo  Video Compositor:     !FFMPEG_STATUS!
echo  Parallel Engine:      Active (!AUTOSHORTS_CONCURRENCY! Workers via AUTOSHORTS_CONCURRENCY)
echo  Caption Presets:      Active (Rapid Kinetic 84pt, Neon, Bold, Minimal)
echo  Offline Resilience:   Active (Sleep / Disconnect Recovery)
echo  Persistence:          Zero-Phantom-Data Mode (Atomic SQLite)
echo %C_CYAN%=====================================================================%C_RESET%
echo %C_WHITE%[INFO] Starting AutoShorts Studio Dashboard...%C_RESET%
if %NO_BROWSER% equ 0 (
    echo %C_WHITE%[INFO] Your browser will open to http://localhost:%PORT% automatically.%C_RESET%
)
echo %C_WHITE%[INFO] Press Ctrl+C in this terminal window to stop the server.%C_RESET%
echo.

set "RUN_FLAGS=--host %HOST% --port %PORT%"
if %NO_BROWSER% equ 1 (
    set "RUN_FLAGS=!RUN_FLAGS! --no-browser"
)

"!PYTHON_EXE!" run.py dashboard !RUN_FLAGS!
set "EXIT_CODE=%ERRORLEVEL%"

if %EXIT_CODE% equ 0 goto shutdown_clean
if %EXIT_CODE% equ -1073741510 goto shutdown_clean
if %EXIT_CODE% equ 3221225786 goto shutdown_clean

echo.
echo %C_YELLOW%[WARNING] AutoShorts Studio exited with code %EXIT_CODE%.%C_RESET%
pause
exit /b %EXIT_CODE%

:shutdown_clean
echo.
echo %C_GREEN%[INFO] AutoShorts Studio shut down cleanly. Goodbye!%C_RESET%
endlocal
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
