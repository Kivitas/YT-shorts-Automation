#!/usr/bin/env bash
# =============================================================================
# AutoShorts Studio - one-click launcher for macOS 12+ and Linux (also Git Bash)
#
#   ./start.sh                 start the dashboard (http://localhost:8765)
#   ./start.sh --check         pre-flight diagnostics, then exit
#   ./start.sh --test          run the test suite, then exit
#   ./start.sh --install       install dependencies, then exit
#   ./start.sh --collect       download background gameplay footage
#   ./start.sh --port 8080     use another port
#   ./start.sh --no-browser    do not open a browser tab
#   ./start.sh --host 0.0.0.0  listen on another interface
#   ./start.sh --build-assets  force a rebuild of the longform sprite assets
#
# Video modes (choose "Video Mode" in the dashboard, or autoshorts run --streamer-cam):
#   Subway Surfers / Minecraft background, or streamer cam on top + their own gameplay.
#   Streamer-cam auto-detect uses OpenCV, installed on first run.
#
# Everything the app needs is installed on first run. Paste your API keys into .env.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ----------------------------------------------------------------------------- colours
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_RED=$'\033[91m'; C_GREEN=$'\033[92m'
    C_YELLOW=$'\033[93m'; C_CYAN=$'\033[96m'; C_WHITE=$'\033[97m'
else
    C_RESET=""; C_BOLD=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_WHITE=""
fi
info()   { printf "%b[INFO]%b %s\n" "$C_CYAN" "$C_RESET" "$*"; }
ok()     { printf "%b[ OK ]%b %s\n" "$C_GREEN" "$C_RESET" "$*"; }
notice() { printf "%b[NOTE]%b %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
fail()   { printf "%b[ERROR]%b %s\n" "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ----------------------------------------------------------------------------- defaults & arguments
PORT="8765"
HOST="127.0.0.1"
MODE="run"
NO_BROWSER=0
FORCE_ASSETS=0
COLLECT_ARGS=()

show_help() {
    sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --port|-p)     [ $# -ge 2 ] || fail "--port needs a number"; PORT="$2"; shift 2 ;;
        --host)        [ $# -ge 2 ] || fail "--host needs an address"; HOST="$2"; shift 2 ;;
        --install)     MODE="install"; shift ;;
        --check|-c)    MODE="check"; shift ;;
        --test|-t)     MODE="test"; shift ;;
        --collect|--collect-gameplay) MODE="collect"; shift
                       while [ $# -gt 0 ]; do COLLECT_ARGS+=("$1"); shift; done ;;
        --no-browser)  NO_BROWSER=1; shift ;;
        --build-assets) FORCE_ASSETS=1; shift ;;
        --help|-h)     show_help ;;
        *)             notice "Ignoring unknown argument: $1 (see --help)"; shift ;;
    esac
done

if ! [[ "$PORT" =~ ^[0-9]{1,5}$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    fail "Invalid port '$PORT'. Use a number from 1 to 65535."
fi

# ----------------------------------------------------------------------------- platform & PATH
OS="$(uname -s)"
IS_MAC=0; IS_GITBASH=0
[ "$OS" = "Darwin" ] && IS_MAC=1
case "$OS" in MINGW*|MSYS*|CYGWIN*) IS_GITBASH=1 ;; esac

if [ "$IS_MAC" -eq 1 ]; then
    for brew_bin in /opt/homebrew/bin /usr/local/bin; do
        if [ -x "$brew_bin/brew" ]; then
            eval "$("$brew_bin/brew" shellenv)"
            break
        fi
    done
fi
for extra in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    case ":$PATH:" in *":$extra:"*) ;; *) [ -d "$extra" ] && export PATH="$extra:$PATH" ;; esac
done
if [ "$IS_GITBASH" -eq 1 ] && [ -n "${LOCALAPPDATA:-}" ]; then
    winget_links="$(cygpath -u "$LOCALAPPDATA")/Microsoft/WinGet/Links"
    [ -d "$winget_links" ] && export PATH="$winget_links:$PATH"
fi

# ----------------------------------------------------------------------------- uv and Python
UV_CMD=""
if command -v uv >/dev/null 2>&1; then
    UV_CMD="$(command -v uv)"
fi

if [ -z "$UV_CMD" ]; then
    info "uv (Python package manager) not found. Installing it - one time..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
        for extra in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
            if [ -x "$extra/uv" ]; then UV_CMD="$extra/uv"; export PATH="$extra:$PATH"; break; fi
        done
    fi
fi

if [ -z "$UV_CMD" ]; then
    fail "uv could not be installed. Install it from https://astral.sh/uv and run ./start.sh again."
fi

# ----------------------------------------------------------------------------- virtual environment
if [ "$IS_GITBASH" -eq 1 ]; then
    PYTHON_EXE=".venv/Scripts/python.exe"
else
    PYTHON_EXE=".venv/bin/python"
fi

# A .venv created on Windows cannot be used on macOS/Linux, so rebuild it there.
# On Git Bash the Scripts/ layout is the valid one, so it is never touched.
if [ "$IS_GITBASH" -eq 0 ] && [ -d ".venv" ] && [ ! -x "$PYTHON_EXE" ] && [ -d ".venv/Scripts" ]; then
    info "Found a Windows-made .venv. Rebuilding it for this system..."
    rm -rf .venv
fi

if [ ! -x "$PYTHON_EXE" ]; then
    info "Creating the Python environment (Python 3.12) - one time..."
    "$UV_CMD" venv --python 3.12 >/dev/null 2>&1 || "$UV_CMD" venv >/dev/null 2>&1 \
        || fail "Could not create .venv. Check that uv can install Python 3.11 or newer."
fi
[ -x "$PYTHON_EXE" ] || fail "Python environment missing at $PYTHON_EXE."

# ----------------------------------------------------------------------------- core dependencies
PY_CORE_CHECK="import autoshorts, fastapi, uvicorn, playwright, edge_tts, google.genai, yt_dlp, imageio_ffmpeg, faster_whisper"
"$UV_CMD" sync --quiet || notice "uv sync reported a problem; checking imports anyway."
if ! "$PYTHON_EXE" -c "$PY_CORE_CHECK" >/dev/null 2>&1; then
    info "Installing Python core dependencies - one time, this can take a few minutes..."
    "$UV_CMD" pip install -e . >/dev/null 2>&1 || "$PYTHON_EXE" -m pip install -e . >/dev/null 2>&1 || true
    "$PYTHON_EXE" -c "$PY_CORE_CHECK" >/dev/null 2>&1 \
        || fail "Python core dependencies could not be installed. Check your connection, then run ./start.sh again."
fi
ok "Python core dependencies ready"

# ----------------------------------------------------------------------------- OpenCV (facecam auto-detect for the cam_gameplay layout)
CV2_CHECK="import cv2; cv2.CascadeClassifier"
if ! "$PYTHON_EXE" -c "$CV2_CHECK" >/dev/null 2>&1; then
    info "Installing OpenCV for facecam auto-detect - one time..."
    "$UV_CMD" pip install --python "$PYTHON_EXE" "opencv-python-headless>=4.9.0,<5" >/dev/null 2>&1 \
        || "$PYTHON_EXE" -m pip install "opencv-python-headless>=4.9.0,<5" >/dev/null 2>&1 || true
fi
if "$PYTHON_EXE" -c "$CV2_CHECK" >/dev/null 2>&1; then
    ok "OpenCV ready"
else
    notice "OpenCV could not be installed; set branding.cam_layout.cam_rect by hand for the cam_gameplay layout."
fi

# ----------------------------------------------------------------------------- Remotion (optional captions engine + longform sprites)
REMOTION_DIR="$SCRIPT_DIR/remotion"
SATORI_DIR="$REMOTION_DIR/node_modules/satori"
SPRITE_PNG="$SCRIPT_DIR/assets/longform/sprites/navy_gradient.png"
if [ -f "$REMOTION_DIR/package.json" ]; then
    REMOTION_READY=0
    if [ -x "$REMOTION_DIR/node_modules/.bin/remotion" ] && [ -d "$SATORI_DIR" ]; then
        REMOTION_READY=1
    elif command -v npm >/dev/null 2>&1; then
        info "Installing the Remotion caption renderer and longform assets - one time..."
        if (cd "$REMOTION_DIR" && npm install --no-audit --no-fund >/dev/null 2>&1); then
            REMOTION_READY=1
            info "Downloading Remotion's headless browser - one time, about 100 MB..."
            (cd "$REMOTION_DIR" && npx remotion browser ensure >/dev/null 2>&1) \
                || notice "Browser download failed; it will be retried on the first render."
        else
            notice "Remotion install failed; word captions fall back to the ASS engine."
        fi
    else
        notice "Node.js is not installed, so the Remotion caption engine is unavailable (https://nodejs.org)."
    fi
    if [ "$REMOTION_READY" -eq 1 ] && { [ "$FORCE_ASSETS" -eq 1 ] || [ ! -f "$SPRITE_PNG" ]; }; then
        info "Building the longform sprite assets with satori..."
        if (cd "$REMOTION_DIR" && npm run build:assets); then
            ok "Longform sprite assets built"
        else
            notice "Sprite build failed; longform will use the PIL drawing fallback."
        fi
    fi
fi

# ----------------------------------------------------------------------------- API keys (.env)
if [ ! -f .env ]; then
    cp .env.example .env
    notice "Created .env from .env.example. Open .env and paste your API keys."
fi
if grep -qE '^GEMINI_API_KEY=[[:space:]]*$' .env; then
    notice "GEMINI_API_KEY is empty in .env. Add it for Gemini, or use the local model."
fi

# ----------------------------------------------------------------------------- FFmpeg (full build with ffprobe)
if ! command -v ffprobe >/dev/null 2>&1; then
    if [ "$IS_GITBASH" -eq 1 ] && command -v winget.exe >/dev/null 2>&1; then
        info "Installing full FFmpeg (with ffprobe) through winget - one time..."
        winget.exe install --id Gyan.FFmpeg --exact --silent --accept-package-agreements --accept-source-agreements >/dev/null 2>&1 \
            || notice "winget could not install FFmpeg; durations use a slower fallback."
        winget_links="$(cygpath -u "$LOCALAPPDATA")/Microsoft/WinGet/Links"
        [ -d "$winget_links" ] && export PATH="$winget_links:$PATH"
    elif command -v brew >/dev/null 2>&1; then
        info "Installing full FFmpeg (with ffprobe) through Homebrew - one time..."
        brew install ffmpeg >/dev/null 2>&1 || notice "Homebrew could not install ffmpeg; durations use a slower fallback."
    else
        notice "Full FFmpeg (with ffprobe) is missing. Install Homebrew (https://brew.sh), then run: brew install ffmpeg"
    fi
fi

# ----------------------------------------------------------------------------- runtime folders
mkdir -p assets/backgrounds assets/music assets/broll_cache data/logs data/edge_profile work inbox output output/export output/longform

# ----------------------------------------------------------------------------- target modes dispatch
case "$MODE" in
    test)
        info "Running the test suite..."
        exec "$UV_CMD" run pytest -q
        ;;
    check)
        info "Pre-flight diagnostics"
        echo "  Platform:   $OS ($(uname -m))"
        echo "  Python:     $("$PYTHON_EXE" --version 2>&1)"
        echo "  Port $PORT: $("$PYTHON_EXE" -c "import socket; s=socket.socket(); busy=s.connect_ex(('127.0.0.1', $PORT))==0; s.close(); print('in use' if busy else 'free')")"
        "$PYTHON_EXE" -c "import shutil; print('  ffprobe:    ' + (shutil.which('ffprobe') or 'missing (slower durations)'))"
        "$PYTHON_EXE" -c "from pathlib import Path; print('  .env:       ' + ('present' if Path('.env').exists() else 'missing'))"
        "$PYTHON_EXE" -c "import glob; print('  Background clips: ' + str(len(glob.glob('assets/backgrounds/**/*.mp4', recursive=True))))"
        "$PYTHON_EXE" -c "import cv2; cv2.CascadeClassifier; print('  OpenCV:     ' + cv2.__version__ + ' - streamer-cam auto-detect ready')" 2>/dev/null \
            || echo "  OpenCV:     missing (streamer-cam needs a manual cam_rect)"
        "$PYTHON_EXE" -c "from autoshorts.media.raea_renderer import find_raea_binary as f; p=f(); print('  raea:       ' + (str(p) if p else 'not installed - Remotion/FFmpeg are used'))"
        echo ""
        ok "Pre-flight complete"
        exit 0
        ;;
    collect)
        exec "$PYTHON_EXE" scripts/collect_gameplay.py "${COLLECT_ARGS[@]+"${COLLECT_ARGS[@]}"}"
        ;;
    install)
        info "Running complete installation..."
        ok "AutoShorts Studio installation complete!"
        exit 0
        ;;
esac

# ----------------------------------------------------------------------------- dashboard
dashboard_url="http://localhost:${PORT}"
open_browser() {
    if [ "$IS_MAC" -eq 1 ]; then
        open "$dashboard_url" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$dashboard_url" >/dev/null 2>&1 || true
    fi
}

if "$PYTHON_EXE" -c "import socket,sys; s=socket.socket(); busy=s.connect_ex(('127.0.0.1', $PORT))==0; s.close(); sys.exit(0 if busy else 1)" 2>/dev/null; then
    if curl -sf -m 5 "$dashboard_url/api/health" >/dev/null 2>&1; then
        ok "AutoShorts is already running at $dashboard_url"
        [ "$NO_BROWSER" -eq 0 ] && open_browser
        exit 0
    fi
    fail "Port $PORT is used by another application. Start again with: ./start.sh --port 8080"
fi

if [ "$NO_BROWSER" -eq 0 ]; then
    ( sleep 1.5; open_browser ) &
fi

info "Starting AutoShorts Studio at $dashboard_url (Ctrl+C stops it)"
RUN_ARGS=(run.py dashboard --host "$HOST" --port "$PORT" --no-browser)
if [ "$IS_MAC" -eq 1 ] && command -v caffeinate >/dev/null 2>&1; then
    # Keep the Mac awake while jobs render.
    exec caffeinate -i "$PYTHON_EXE" "${RUN_ARGS[@]}"
fi
exec "$PYTHON_EXE" "${RUN_ARGS[@]}"
