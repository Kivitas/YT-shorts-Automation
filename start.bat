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

rem ----------------------------------------------------------------------------- Remotion (optional captions engine)
set "REMOTION_BIN=%~dp0remotion\node_modules\.bin\remotion.cmd"
if not exist "%~dp0remotion\package.json" goto remotion_done
if exist "!REMOTION_BIN!" goto remotion_done
where npm >nul 2>&1
if !ERRORLEVEL! neq 0 goto remotion_no_node
echo %C_CYAN%[INFO]%C_RESET% Installing the Remotion caption renderer - one time...
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
echo   Python:     & "%PYTHON_EXE%" --version
where ffprobe >nul 2>&1
if !ERRORLEVEL! equ 0 (echo   ffprobe:    found) else (echo   ffprobe:    missing ^(slower durations^))
if exist ".env" (echo   .env:       present) else (echo   .env:       missing)
echo   Port %PORT%: checked at launch
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
exit /b 0
