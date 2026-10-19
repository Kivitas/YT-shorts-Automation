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

:: 1. Check for dedicated project virtual environment
if exist ".venv\Scripts\python.exe" (
    echo [INFO] Using virtual environment python (.venv\Scripts\python.exe)
    ".venv\Scripts\python.exe" run.py %*
    goto :end
)

:: 2. Fall back to uv run python
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [INFO] Using uv runner (uv run python)
    uv run python run.py %*
    goto :end
)

:: 3. Fall back to system python
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [INFO] Using system python
    python run.py %*
    goto :end
)

echo [ERROR] No Python interpreter found!
echo Please install Python 3.11+ or initialize .venv.
pause

:end
endlocal
