@echo off
rem =============================================================================
rem AutoShorts Studio - One-Click Installer for Windows
rem
rem Installs:
rem   - Python virtual environment (.venv)
rem   - Core dependencies (FastAPI, Playwright, Whisper, edge-tts, etc.)
rem   - AI Image Generation dependencies (Diffusers, Transformers, Torch, Safetensors)
rem   - Stable Diffusion 1.5 weights and Civitai LoRAs (Wojak Girls, Memetic Difjak, Wojak Style)
rem =============================================================================
setlocal EnableExtensions EnableDelayedExpansion
title AutoShorts Studio Installer
cd /d "%~dp0"

echo ========================================================================
echo  AutoShorts Studio - Complete Installation ^& Dependency Setup
echo ========================================================================
echo.
call "%~dp0start.bat" --install %*
exit /b %ERRORLEVEL%
