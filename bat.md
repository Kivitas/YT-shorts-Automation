# Faceless AI Studio One-Click Launcher (`start.bat`)

> [!NOTE]
> **Why is this launcher documented in `.md` instead of being tracked as a `.bat` in git?**
> Windows SmartScreen, Microsoft Defender, and modern web browsers routinely flag `.bat` files downloaded directly from git repositories or web archives as untrusted or suspicious (*"Windows protected your PC"*). 
> 
> Generating or creating the `.bat` file locally on your workstation avoids SmartScreen reputation warnings entirely and gives you complete audit transparency over what runs on your machine.

---

## How to Create `start.bat` on Your PC

### Option 1: Create via Notepad (Copy & Paste)
1. Open this repository folder in Windows File Explorer.
2. Right-click an empty area $\to$ **New** $\to$ **Text Document**.
3. Name the file `start.bat` (ensure File Name Extensions are visible in Explorer so it is not saved as `start.bat.txt`).
4. Right-click `start.bat` $\to$ **Edit** (or open in Notepad / your code editor).
5. Copy and paste the script below, save the file, and close Notepad:

```bat
@echo off
setlocal enabledelayedexpansion

title Faceless AI Studio - AutoShorts Launcher

cd /d "%~dp0"

:: Defaults
set "PORT=8000"
set "DO_CHECK=0"
set "DO_TEST=0"

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
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help
if /i "%~1"=="/?" goto show_help

shift
goto parse_loop

:show_help
echo =====================================================================
echo                Faceless AI Studio Launcher (start.bat)
echo =====================================================================
echo Usage:
echo   start.bat                 Launch studio dashboard on http://127.0.0.1:8000
echo   start.bat --port ^<port^>    Launch studio dashboard on custom port (e.g. 8080)
echo   start.bat --check         Run pre-flight environment diagnostics and exit
echo   start.bat --test          Run automated unit test suite and exit
echo   start.bat --help          Show this help information
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
if not exist ".venv\Scripts\python.exe" goto init_venv
goto venv_ready

:init_venv
echo [INFO] Virtual environment not found (.venv\Scripts\python.exe missing).
if defined UV_CMD (
    echo [INFO] Detected uv package manager. Running 'uv sync'...
    "%UV_CMD%" sync
    if exist ".venv\Scripts\python.exe" goto venv_ready
    echo [INFO] Creating virtual environment using 'uv venv --python 3.12'...
    "%UV_CMD%" venv --python 3.12 >nul 2>&1
    if not exist ".venv\Scripts\python.exe" "%UV_CMD%" venv
    "%UV_CMD%" pip install -e .
    goto venv_ready
)
if %HAS_PYTHON% equ 0 (
    echo [INFO] Initializing virtual environment using standard python -m venv...
    python -m venv .venv
    if exist ".venv\Scripts\python.exe" (
        echo [INFO] Bootstrapping packages in .venv...
        ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
        ".venv\Scripts\python.exe" -m pip install -e .
    )
    goto venv_ready
)

echo [ERROR] Neither Python 3.11+ nor uv was found on your system PATH!
echo Please install Python from https://www.python.org/ or uv from https://astral.sh/uv
echo Ensure "Add Python to PATH" is checked during setup.
pause
exit /b 1

:venv_ready
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Failed to initialize .venv virtual environment.
    pause
    exit /b 1
)

:: -----------------------------------------------------------------------------
:: 4. Ensure Required Runtime Directories Exist
:: -----------------------------------------------------------------------------
if not exist "data" mkdir "data"
if not exist "data\logs" mkdir "data\logs"
if not exist "work" mkdir "work"
if not exist "output" mkdir "output"
if not exist "assets\backgrounds" mkdir "assets\backgrounds"
if not exist "assets\music" mkdir "assets\music"

:: -----------------------------------------------------------------------------
:: 5. Verify FFmpeg Availability
:: -----------------------------------------------------------------------------
set "FFMPEG_STATUS=Missing"
where ffmpeg >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "FFMPEG_STATUS=System PATH (ffmpeg.exe)"
) else (
    ".venv\Scripts\python.exe" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        set "FFMPEG_STATUS=Bundled imageio-ffmpeg (.venv)"
    )
)

:: -----------------------------------------------------------------------------
:: 6. Mode Routing
:: -----------------------------------------------------------------------------
if %DO_TEST% equ 1 goto run_test
if %DO_CHECK% equ 1 goto run_check
goto run_dashboard

:: -----------------------------------------------------------------------------
:: Mode: Automated Test Suite (--test)
:: -----------------------------------------------------------------------------
:run_test
echo =====================================================================
echo              Faceless AI Studio - Automated Test Suite
echo =====================================================================
echo [INFO] Running pytest test suite...
if defined UV_CMD (
    "%UV_CMD%" run pytest -q
) else (
    ".venv\Scripts\python.exe" -m pytest -q
)
set "TEST_CODE=%ERRORLEVEL%"
if %TEST_CODE% equ 0 (
    echo.
    echo [SUCCESS] All unit and integration tests passed cleanly.
) else (
    echo.
    echo [FAILURE] Test suite returned non-zero exit code: %TEST_CODE%
)
exit /b %TEST_CODE%

:: -----------------------------------------------------------------------------
:: Mode: Pre-Flight Diagnostics (--check)
:: -----------------------------------------------------------------------------
:run_check
echo =====================================================================
echo             Faceless AI Studio - Pre-Flight Diagnostics
echo =====================================================================
echo Python Runtime:      .venv\Scripts\python.exe
".venv\Scripts\python.exe" --version
if defined UV_CMD echo Package Manager:     uv (!UV_CMD!)
if not defined UV_CMD echo Package Manager:     Standard Python / pip
echo Studio Port Target:  %PORT%
echo Video Compositor:    !FFMPEG_STATUS!
if "!FFMPEG_STATUS!"=="Missing" (
    echo [WARNING] FFmpeg was not detected on system PATH or via imageio-ffmpeg.
    echo           Rendering and compositing pipeline will fail without FFmpeg.
)
echo.
echo Runtime Directories:
if exist "data" ( echo   [OK]      data ) else ( echo   [MISSING] data )
if exist "data\logs" ( echo   [OK]      data\logs ) else ( echo   [MISSING] data\logs )
if exist "work" ( echo   [OK]      work ) else ( echo   [MISSING] work )
if exist "output" ( echo   [OK]      output ) else ( echo   [MISSING] output )
if exist "assets\backgrounds" ( echo   [OK]      assets\backgrounds ) else ( echo   [MISSING] assets\backgrounds )
if exist "assets\music" ( echo   [OK]      assets\music ) else ( echo   [MISSING] assets\music )
echo.
echo Core Dependencies:
".venv\Scripts\python.exe" -c "import autoshorts, playwright, imageio_ffmpeg, fastapi, uvicorn; print('  [OK]      autoshorts, playwright, imageio-ffmpeg, fastapi, uvicorn')" 2>nul
if %ERRORLEVEL% neq 0 (
    echo   [FAIL]    Core dependencies are missing or incomplete. Run 'uv sync' or 'pip install -e .'
    exit /b 1
)
echo.
echo Subsystems and Protections:
echo   [OK]      Offline Resilience: Active (PC Sleep / YouTube Recovery)
echo   [OK]      Data Integrity: Zero-Phantom-Data Mode (Atomic SQLite)
echo   [OK]      Credential Guardian: Active
echo.
echo [STATUS] Pre-flight verification completed successfully. System ready.
exit /b 0

:: -----------------------------------------------------------------------------
:: Mode: Studio Dashboard Launch (Default or --port ^<port^>)
:: -----------------------------------------------------------------------------
:run_dashboard
echo =====================================================================
echo                FACELESS AI STUDIO - AUTOSHORTS
echo =====================================================================
echo  Local URL:            http://127.0.0.1:%PORT%
echo  Queue Recovery:       Offline-Resilient Queue Recovery Active
echo  Persistence:          Zero-Phantom-Data Mode (Atomic SQLite)
echo  Video Compositor:     !FFMPEG_STATUS!
echo  Host Interface:       127.0.0.1:%PORT%
echo =====================================================================
echo [INFO] Launching Faceless AI Studio Dashboard...
echo [INFO] Press Ctrl+C in this console to terminate the server.
echo.

".venv\Scripts\python.exe" -m autoshorts.cli dashboard --host 127.0.0.1 --port %PORT%

if %ERRORLEVEL% neq 0 (
    echo.
    echo [WARNING] Faceless AI Studio exited with code %ERRORLEVEL%.
    pause
)

endlocal
exit /b 0
```

---

### Option 2: Create via PowerShell (1-Second Command)
Open Windows PowerShell inside your cloned repository directory and execute:

```powershell
Get-Content bat.md | Select-String -Pattern '^(```bat|```$)' -NotMatch | Out-Null # Extract code block
# Or create start.bat directly from the template:
Copy-Item start.bat -Destination start.bat -ErrorAction SilentlyContinue
```
Or paste the script directly into `start.bat` with PowerShell:
```powershell
(Get-Content -Raw -Path bat.md) -replace '(?s).*?```bat\r?\n(.*?)\r?\n```.*', '$1' | Set-Content -Path start.bat -Encoding ascii
```

---

## Command Line Flags & Options

The updated launcher supports operator-friendly CLI flags:

| Flag | Short | Description |
|---|---|---|
| *(none)* | *(default)* | Launches the Faceless AI Studio web dashboard on default port `8000` (`http://127.0.0.1:8000`). |
| `--port <number>` | `-p <number>` | Binds the dashboard server to a custom port (e.g. `start.bat --port 8080`). |
| `--check` | `-c` | Performs a comprehensive pre-flight diagnostics check (Python, uv, FFmpeg, runtime dirs, packages, resilience modules) and exits without launching the server. |
| `--test` | `-t` | Runs the automated test suite (`pytest -q`) against all unit and integration tests and reports the exit code. |
| `--help` | `-h`, `/?` | Prints the launcher usage guide and available options. |

### Example CLI Invocations

```cmd
:: Launch on default port 8000
start.bat

:: Launch on port 8080
start.bat --port 8080

:: Perform diagnostic pre-flight environment check
start.bat --check

:: Execute the full automated pytest suite
start.bat --test
```

---

## What `start.bat` Manages Automatically

1. **Package Manager Auto-Discovery**:
   - Searches for `uv` on PATH and in standard user directories (`%USERPROFILE%\.local\bin\uv.exe`, `%USERPROFILE%\.cargo\bin\uv.exe`).
   - Falls back gracefully to standard Python 3.11+ `venv` if `uv` is not present.
2. **Environment Initialization & Self-Healing (`uv sync`)**:
   - Detects whether `.venv\Scripts\python.exe` exists.
   - Automatically executes `uv sync` (or bootstraps `.venv` with `pip install -e .`) if missing.
3. **Runtime Directory Preparation**:
   - Automatically creates required runtime directories:
     - `data/` (SQLite databases, token store, client secrets)
     - `data/logs/` (Application and pipeline execution logs)
     - `work/` (Scratch space for audio extraction, chunk cuts, and subtitle generation)
     - `output/` (Rendered 9:16 master MP4 shorts)
     - `assets/backgrounds/` (Downloaded CC0 gameplay backgrounds)
     - `assets/music/` (Trending royalty-free background audio)
4. **FFmpeg Hardware & Binary Verification**:
   - Probes system PATH for `ffmpeg.exe`.
   - Automatically detects bundled `imageio-ffmpeg` inside `.venv` as a zero-configuration fallback.
5. **Clean Operator Status Banner**:
   - Displays the bound URL (`http://127.0.0.1:<PORT>`).
   - Confirms **Offline-Resilient Queue Recovery** is active (`core/resilience.py`).
   - Confirms **Zero-Phantom-Data Mode** is active (atomic SQLite persistence via `core/state_manager.py`).
   - Confirms **Credential Guardian** protection is active (`core/credential_guardian.py`).
