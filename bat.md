# AutoShorts One-Click Launcher (`start.bat`)

> [!NOTE]
> **Why is this provided as a `.md` file instead of a `.bat` in git?**
> Windows SmartScreen, Defender, and browser security routinely flag `.bat` files downloaded directly from git or the web as untrusted or suspicious ("Windows protected your PC"). 
>
> Creating the `.bat` file directly on your own computer avoids these security warnings entirely.

---

## How to Create `start.bat` on Your PC

### Option 1: Create via Notepad (Easiest)
1. Open this project folder in Windows File Explorer.
2. Right-click an empty area $\to$ **New** $\to$ **Text Document**.
3. Name it `start.bat` (make sure file extensions are enabled so it's not named `start.bat.txt`).
4. Right-click `start.bat` $\to$ **Edit** (or open in Notepad).
5. Copy and paste the script below, then save and close Notepad:

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

:: 3. Self-heal missing dependencies on first run
echo [INFO] Verifying installed packages...
".venv\Scripts\python.exe" -c "import autoshorts, playwright, imageio_ffmpeg" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Core dependencies missing or incomplete.
    echo [INFO] Automatically installing requirements (playwright, ffmpeg, autoshorts)...
    if defined UV_CMD (
        "%UV_CMD%" pip install -e .
    ) else (
        ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
        ".venv\Scripts\python.exe" -m pip install -e .
    )
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Dependency installation encountered an issue.
        pause
        exit /b 1
    )
    echo [INFO] All dependencies installed successfully.
)

:: 4. Launch web browser and dashboard
echo [INFO] Opening Microsoft Edge to http://localhost:8765 ...
start "" "http://localhost:8765"

echo [INFO] Launching AutoShorts server...
".venv\Scripts\python.exe" run.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] AutoShorts exited with error code %ERRORLEVEL%.
    pause
)
```

---

### Option 2: Create via PowerShell (1-Second Command)
Open PowerShell in this project folder and run this single command to create `start.bat` automatically:

```powershell
Get-Content bat.md | Select-String -Pattern '^(@echo off|setlocal|title|echo|cd|set|where|if|pause|start|\"\.venv)' | Set-Content start.bat
```

Or copy the block directly into `start.bat`:
```powershell
@'
@echo off
setlocal enabledelayedexpansion
title AutoShorts Launcher
cd /d "%~dp0"
where uv >nul 2>&1 && set "UV_CMD=uv"
if not defined UV_CMD if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
if not exist ".venv\Scripts\python.exe" (
    if defined UV_CMD ( "%UV_CMD%" venv --python 3.12 ) else ( python -m venv .venv )
)
".venv\Scripts\python.exe" -c "import autoshorts, playwright, imageio_ffmpeg" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    if defined UV_CMD ( "%UV_CMD%" pip install -e . ) else ( ".venv\Scripts\python.exe" -m pip install -e . )
)
start "" "http://localhost:8765"
".venv\Scripts\python.exe" run.py %*
pause
'@ | Out-File -FilePath start.bat -Encoding ascii
```

---

## What `start.bat` Does Automatically:
1. **Detects `uv` or system Python** automatically.
2. **Creates `.venv`** if running for the first time.
3. **Installs all dependencies** (`autoshorts`, `playwright`, `imageio-ffmpeg`, etc.) if not already installed.
4. **Launches your web browser** directly to `http://localhost:8765`.
5. **Starts the dashboard server** with live logging.
