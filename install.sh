#!/usr/bin/env bash
# =============================================================================
# AutoShorts Studio - One-Click Installer for macOS / Linux
#
# Installs:
#   - Python virtual environment (.venv)
#   - Core dependencies (FastAPI, Playwright, Whisper, edge-tts, etc.)
#   - AI Image Generation dependencies (Diffusers, Transformers, Torch, Safetensors)
#   - Stable Diffusion 1.5 weights and Civitai LoRAs (Wojak Girls, Memetic Difjak, Wojak Style)
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================================================"
echo " AutoShorts Studio - Complete Installation & Dependency Setup"
echo "========================================================================"
echo ""
exec "$SCRIPT_DIR/start.sh" --install "$@"
