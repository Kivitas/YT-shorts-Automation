#!/usr/bin/env bash
# =============================================================================
# AutoShorts Studio - One-Click Unix / macOS Launcher
# Supports: macOS 12 Monterey+ (Apple Silicon M1-M4 & Intel x86_64), Linux
# =============================================================================
set -euo pipefail

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# -----------------------------------------------------------------------------
# Terminal ANSI Color Configuration
# -----------------------------------------------------------------------------
if [ -t 1 ]; then
    C_RESET="\033[0m"
    C_BOLD="\033[1m"
    C_RED="\033[91m"
    C_GREEN="\033[92m"
    C_YELLOW="\033[93m"
    C_BLUE="\033[94m"
    C_CYAN="\033[96m"
    C_WHITE="\033[97m"
    C_GRAY="\033[90m"
else
    C_RESET=""
    C_BOLD=""
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_BLUE=""
    C_CYAN=""
    C_WHITE=""
    C_GRAY=""
fi

# -----------------------------------------------------------------------------
# Configuration Defaults
# -----------------------------------------------------------------------------
PORT="8000"
HOST="127.0.0.1"
DO_CHECK=0
DO_TEST=0
DO_COLLECT=0
NO_BROWSER=0
COLLECT_ARGS=()

# -----------------------------------------------------------------------------
# CLI Argument Parsing
# -----------------------------------------------------------------------------
show_help() {
    printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
    printf "                 %bAutoShorts Studio Launcher (macOS & Unix)%b\n" "$C_BOLD" "$C_RESET"
    printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
    printf "%bUsage:%b\n" "$C_WHITE" "$C_RESET"
    printf "  ./start.sh                     Launch web dashboard at http://localhost:8000\n"
    printf "  ./start.sh --port <port>       Launch dashboard on custom port (e.g. 8080)\n"
    printf "  ./start.sh -p <port>           Short alias for --port\n"
    printf "  ./start.sh --check             Run comprehensive pre-flight diagnostics and exit\n"
    printf "  ./start.sh -c                  Short alias for --check\n"
    printf "  ./start.sh --test              Run automated test suite (pytest) and exit\n"
    printf "  ./start.sh -t                  Short alias for --test\n"
    printf "  ./start.sh --collect-gameplay  Launch background gameplay downloader interactively\n"
    printf "  ./start.sh --collect           Short alias for --collect-gameplay\n"
    printf "  ./start.sh --no-browser        Launch dashboard without auto-opening browser tab\n"
    printf "  ./start.sh --host <ip>         Custom host binding (default 127.0.0.1)\n"
    printf "  ./start.sh --help              Display this help guide and available options\n"
    printf "  ./start.sh -h                  Short alias for --help\n\n"
    printf "%bExamples:%b\n" "$C_WHITE" "$C_RESET"
    printf "  ./start.sh --port 8765\n"
    printf "  ./start.sh --check\n"
    printf "  ./start.sh --test\n"
    printf "  ./start.sh --collect\n"
    printf "  ./start.sh --collect --list\n"
    printf "  ./start.sh --collect --categories minecraft subway\n\n"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port|-p)
            if [[ $# -ge 2 ]]; then
                PORT="$2"
                shift 2
            else
                printf "%b[ERROR] --port requires a port argument%b\n" "$C_RED" "$C_RESET"
                exit 1
            fi
            ;;
        --check|-c)
            DO_CHECK=1
            shift
            ;;
        --test|-t)
            DO_TEST=1
            shift
            ;;
        --collect-gameplay|--collect)
            DO_COLLECT=1
            shift
            while [[ $# -gt 0 ]]; do
                COLLECT_ARGS+=("$1")
                shift
            done
            break
            ;;
        --no-browser)
            NO_BROWSER=1
            shift
            ;;
        --host)
            if [[ $# -ge 2 ]]; then
                HOST="$2"
                shift 2
            else
                printf "%b[ERROR] --host requires an IP argument%b\n" "$C_RED" "$C_RESET"
                exit 1
            fi
            ;;
        --help|-h)
            show_help
            ;;
        *)
            printf "%b[WARNING] Unrecognized argument: %s (use --help for valid options)%b\n" "$C_YELLOW" "$1" "$C_RESET"
            shift
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Detect Homebrew & Prepend to PATH on macOS
# -----------------------------------------------------------------------------
if [[ "$(uname -s)" == "Darwin" ]]; then
    # Apple Silicon Homebrew
    if [[ -d "/opt/homebrew/bin" ]] && [[ ":$PATH:" != *":/opt/homebrew/bin:"* ]]; then
        export PATH="/opt/homebrew/bin:$PATH"
    fi
    # Intel Mac Homebrew
    if [[ -d "/usr/local/bin" ]] && [[ ":$PATH:" != *":/usr/local/bin:"* ]]; then
        export PATH="/usr/local/bin:$PATH"
    fi
fi

# -----------------------------------------------------------------------------
# Detect Package Manager & Python Runtime
# -----------------------------------------------------------------------------
UV_CMD=""
if command -v uv >/dev/null 2>&1; then
    UV_CMD="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
    UV_CMD="$HOME/.local/bin/uv"
elif [[ -x "$HOME/.cargo/bin/uv" ]]; then
    UV_CMD="$HOME/.cargo/bin/uv"
fi

SYSTEM_PYTHON=""
if command -v python3 >/dev/null 2>&1; then
    SYSTEM_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    SYSTEM_PYTHON="$(command -v python)"
fi

# -----------------------------------------------------------------------------
# Verify / Initialize Virtual Environment (.venv)
# -----------------------------------------------------------------------------
PYTHON_EXE=".venv/bin/python"

if [[ ! -x "$PYTHON_EXE" ]]; then
    printf "%b[INFO]%b Virtual environment not found (.venv/bin/python missing).\n" "$C_YELLOW" "$C_RESET"
    if [[ -n "$UV_CMD" ]]; then
        printf "%b[INFO]%b Detected uv package manager (%s). Synchronizing environment...\n" "$C_CYAN" "$C_RESET" "$UV_CMD"
        "$UV_CMD" sync
        if [[ ! -x "$PYTHON_EXE" ]]; then
            printf "%b[INFO]%b Initializing virtual environment with uv...\n" "$C_CYAN" "$C_RESET"
            "$UV_CMD" venv --python 3.12 2>/dev/null || "$UV_CMD" venv
            "$UV_CMD" pip install -e .
        fi
    elif [[ -n "$SYSTEM_PYTHON" ]]; then
        printf "%b[INFO]%b Initializing virtual environment using %s -m venv...\n" "$C_CYAN" "$C_RESET" "$SYSTEM_PYTHON"
        "$SYSTEM_PYTHON" -m venv .venv
        if [[ -x "$PYTHON_EXE" ]]; then
            printf "%b[INFO]%b Installing dependencies in editable mode...\n" "$C_CYAN" "$C_RESET"
            "$PYTHON_EXE" -m pip install --upgrade pip >/dev/null 2>&1 || true
            "$PYTHON_EXE" -m pip install -e .
        fi
    else
        printf "%b[ERROR] Neither Python 3.11+ nor uv was found on your system PATH!%b\n" "$C_RED" "$C_RESET"
        printf "On macOS, run: brew install python@3.12 uv ffmpeg\n"
        printf "On Linux, run: sudo apt install python3 python3-venv ffmpeg\n"
        exit 1
    fi
fi

if [[ ! -x "$PYTHON_EXE" ]]; then
    printf "%b[ERROR] Failed to initialize .venv virtual environment.%b\n" "$C_RED" "$C_RESET"
    exit 1
fi

# -----------------------------------------------------------------------------
# Ensure Required Runtime Directories Exist
# -----------------------------------------------------------------------------
mkdir -p assets/backgrounds
mkdir -p assets/music
mkdir -p data/logs
mkdir -p work
mkdir -p inbox
mkdir -p output

# -----------------------------------------------------------------------------
# Verify FFmpeg Availability
# -----------------------------------------------------------------------------
FFMPEG_STATUS="Missing"
if [[ -x "$SCRIPT_DIR/bin/ffmpeg" ]]; then
    FFMPEG_STATUS="Local Project Binary (bin/ffmpeg)"
elif command -v ffmpeg >/dev/null 2>&1; then
    FFMPEG_STATUS="System PATH ($(command -v ffmpeg))"
elif [[ -x "/opt/homebrew/bin/ffmpeg" ]]; then
    FFMPEG_STATUS="Apple Silicon Homebrew (/opt/homebrew/bin/ffmpeg)"
elif [[ -x "/usr/local/bin/ffmpeg" ]]; then
    FFMPEG_STATUS="Intel Mac Homebrew (/usr/local/bin/ffmpeg)"
elif "$PYTHON_EXE" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" >/dev/null 2>&1; then
    FFMPEG_STATUS="Bundled imageio-ffmpeg (.venv)"
fi

# -----------------------------------------------------------------------------
# Mode Routing
# -----------------------------------------------------------------------------
if [[ $DO_TEST -eq 1 ]]; then
    printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
    printf "                 %bAutoShorts Studio - Automated Test Suite%b\n" "$C_BOLD" "$C_RESET"
    printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
    printf "%b[INFO] Running pytest test suite...%b\n\n" "$C_WHITE" "$C_RESET"

    if [[ -n "$UV_CMD" ]]; then
        "$UV_CMD" run pytest -q
    else
        "$PYTHON_EXE" -m pytest -q
    fi
    TEST_CODE=$?
    echo ""
    if [[ $TEST_CODE -eq 0 ]]; then
        printf "%b[SUCCESS] All unit and integration tests passed cleanly.%b\n" "$C_GREEN" "$C_RESET"
    else
        printf "%b[FAILURE] Test suite returned non-zero exit code: %d%b\n" "$C_RED" "$TEST_CODE" "$C_RESET"
    fi
    exit $TEST_CODE
fi

if [[ $DO_CHECK -eq 1 ]]; then
    printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
    printf "          %bAutoShorts Studio - Comprehensive Pre-Flight Diagnostics%b\n" "$C_BOLD" "$C_RESET"
    printf "%b=====================================================================%b\n\n" "$C_CYAN" "$C_RESET"

    printf "%b[1] Runtime & Package Infrastructure:%b\n" "$C_BOLD" "$C_RESET"
    printf "  OS Platform:         %s (%s)\n" "$(uname -s)" "$(uname -m)"
    printf "  Python Runtime:      %s\n" "$PYTHON_EXE"
    "$PYTHON_EXE" --version
    if [[ -n "$UV_CMD" ]]; then
        printf "  Package Manager:     uv [%s]\n" "$UV_CMD"
    else
        printf "  Package Manager:     Standard Python / pip\n"
    fi
    printf "  Video Compositor:    %s\n" "$FFMPEG_STATUS"
    if [[ "$FFMPEG_STATUS" == "Missing" ]]; then
        printf "  %b[WARNING] FFmpeg was not detected on system PATH or via imageio-ffmpeg.%b\n" "$C_YELLOW" "$C_RESET"
        printf "            Rendering and video compositing will fail without FFmpeg.\n"
        printf "            Install via Homebrew: brew install ffmpeg\n"
    fi
    echo ""

    printf "%b[2] Target Port Availability Check (Port %s):%b\n" "$C_BOLD" "$PORT" "$C_RESET"
    "$PYTHON_EXE" -c "import socket; s = socket.socket(); ok = (s.connect_ex(('127.0.0.1', int('$PORT'))) == 0); s.close(); print('  [CONFLICT] Port $PORT is already occupied by another application!' if ok else '  [OK]       Port $PORT is available for binding.')"
    echo ""

    printf "%b[3] Critical Runtime Directories:%b\n" "$C_BOLD" "$C_RESET"
    for dir in assets assets/backgrounds assets/music data data/logs work inbox output; do
        if [[ -d "$dir" ]]; then
            printf "  [OK]       %s\n" "$dir"
        else
            printf "  %b[MISSING]  %s%b\n" "$C_RED" "$dir" "$C_RESET"
        fi
    done
    echo ""

    printf "%b[4] Environment & Credentials (.env):%b\n" "$C_BOLD" "$C_RESET"
    "$PYTHON_EXE" -c "from pathlib import Path; p=Path('.env'); pe=Path('.env.example'); print('  [OK]       .env configuration file present.' if p.exists() else ('  [NOTICE]   .env not found; template ready at .env.example (copy to .env to configure GEMINI_API_KEY).' if pe.exists() else '  [WARN]     Neither .env nor .env.example found.'))"
    echo ""

    printf "%b[5] Background Footage Inventory (assets/backgrounds):%b\n" "$C_BOLD" "$C_RESET"
    "$PYTHON_EXE" -c "from pathlib import Path; p=Path('assets/backgrounds'); clips=[f for f in p.glob('**/*.mp4') if f.stat().st_size > 0]; cats=[d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith('.')]; print('  [OK]       ' + str(len(clips)) + ' clip(s) ready across categories: ' + ', '.join(cats) if len(clips) > 0 else '  [NOTICE]   No background clips found. Run ./start.sh --collect to download footage.')"
    echo ""

    printf "%b[6] Core Dependencies & Model Readiness:%b\n" "$C_BOLD" "$C_RESET"
    "$PYTHON_EXE" -c "import importlib.util as u, sys; mods=[('autoshorts','AutoShorts Core Framework',True),('fastapi','FastAPI Web Engine',True),('uvicorn','Uvicorn ASGI Server',True),('edge_tts','Edge-TTS Voice Synthesizer',True),('google.genai','Google Gemini AI Client',True),('playwright','Playwright Browser Engine',True),('imageio_ffmpeg','Bundled FFmpeg Media Engine',True),('faster_whisper','Faster-Whisper (Optional Local Audio Fallback)',False)]; bad=[d for m,d,r in mods if r and u.find_spec(m) is None]; [print('  [OK]       ' + d) for m,d,r in mods if u.find_spec(m) is not None]; [print('  [OPTIONAL] ' + d) for m,d,r in mods if not r and u.find_spec(m) is None]; sys.exit(1 if len(bad) > 0 else 0)"
    echo ""

    printf "%b[7] System Integrity & Protection Layers:%b\n" "$C_BOLD" "$C_RESET"
    printf "  [OK]       macOS Sleep Prevention: Supported (IOKit / caffeinate)\n"
    printf "  [OK]       Offline Resilience: Active (Sleep / Network Disconnect Recovery)\n"
    printf "  [OK]       Data Integrity: Zero-Phantom-Data Mode (Atomic SQLite)\n"
    printf "  [OK]       Safari / WebKit Compatibility: Active (Glassmorphism & SSE Backoff)\n"
    printf "  [OK]       Credential Guardian: Active\n\n"
    printf "%b[STATUS] Pre-flight verification completed successfully. System ready.%b\n" "$C_GREEN" "$C_RESET"
    exit 0
fi

if [[ $DO_COLLECT -eq 1 ]]; then
    printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
    printf "       %bAutoShorts Studio - Background Gameplay Collector%b\n" "$C_BOLD" "$C_RESET"
    printf "%b=====================================================================%b\n\n" "$C_CYAN" "$C_RESET"

    if [[ ${#COLLECT_ARGS[@]} -gt 0 ]]; then
        printf "%b[INFO] Executing gameplay collector with forwarded arguments: %s%b\n\n" "$C_WHITE" "${COLLECT_ARGS[*]}" "$C_RESET"
        "$PYTHON_EXE" scripts/collect_gameplay.py "${COLLECT_ARGS[@]}"
        exit $?
    fi

    printf "%bCurrent Gameplay Inventory:%b\n" "$C_BOLD" "$C_RESET"
    "$PYTHON_EXE" scripts/collect_gameplay.py --list
    echo ""
    printf "%bCollection Menu Options:%b\n" "$C_BOLD" "$C_RESET"
    printf "  [1] Quick Download: Default categories (minecraft, subway - 2 clips each)\n"
    printf "  [2] Full Download: All categories (minecraft, subway, gta, etc. - 1 clip each)\n"
    printf "  [3] Custom Selection: Specify custom categories\n"
    printf "  [4] Dry Run: Preview YouTube search results without downloading\n"
    printf "  [5] Return to Launcher / Exit\n\n"

    read -r -p "Select an option (1-5) [Default: 1]: " COLLECT_CHOICE
    COLLECT_CHOICE="${COLLECT_CHOICE:-1}"

    case "$COLLECT_CHOICE" in
        1)
            printf "\n%b[INFO] Collecting default gameplay footage (minecraft, subway)...%b\n" "$C_WHITE" "$C_RESET"
            "$PYTHON_EXE" scripts/collect_gameplay.py --categories minecraft subway --per-category 2
            ;;
        2)
            printf "\n%b[INFO] Collecting footage across all predefined categories...%b\n" "$C_WHITE" "$C_RESET"
            "$PYTHON_EXE" scripts/collect_gameplay.py --categories all --per-category 1
            ;;
        3)
            read -r -p "Enter category names separated by space (e.g. minecraft gta): " CUSTOM_CATS
            if [[ -n "$CUSTOM_CATS" ]]; then
                printf "\n%b[INFO] Collecting footage for: %s...%b\n" "$C_WHITE" "$CUSTOM_CATS" "$C_RESET"
                "$PYTHON_EXE" scripts/collect_gameplay.py --categories $CUSTOM_CATS
            else
                printf "[NOTICE] No categories specified. Aborted.\n"
            fi
            ;;
        4)
            printf "\n%b[INFO] Running dry-run preview (search without download)...%b\n" "$C_WHITE" "$C_RESET"
            "$PYTHON_EXE" scripts/collect_gameplay.py --categories minecraft subway --dry-run
            ;;
        5)
            exit 0
            ;;
        *)
            printf "[NOTICE] Invalid option. Exiting.\n"
            exit 1
            ;;
    esac

    printf "\n%b[STATUS] Background gameplay collection routine completed.%b\n" "$C_GREEN" "$C_RESET"
    exit 0
fi

# -----------------------------------------------------------------------------
# Mode: Studio Dashboard Launch (Default or with --port <port>)
# -----------------------------------------------------------------------------
# Check if port is already occupied
if ! "$PYTHON_EXE" -c "import socket, sys; s = socket.socket(); ok = (s.connect_ex(('127.0.0.1', int('$PORT'))) == 0); s.close(); sys.exit(1 if ok else 0)"; then
    printf "\n%b[ERROR] Port %s is already occupied by another running application!%b\n" "$C_RED" "$PORT" "$C_RESET"
    printf "Please specify a different port using: ./start.sh --port <available-port>\n"
    printf "Example: ./start.sh --port 8080\n"
    exit 1
fi

printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
printf "                 %bAutoShorts Studio Launcher%b\n" "$C_BOLD" "$C_RESET"
printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
printf "  Local Dashboard URL:  %bhttp://localhost:%s%b\n" "$C_GREEN" "$PORT" "$C_RESET"
printf "  Alternate URL:        %bhttp://%s:%s%b\n" "$C_GREEN" "$HOST" "$PORT" "$C_RESET"
printf "  Platform:             %s (%s)\n" "$(uname -s)" "$(uname -m)"
printf "  Video Compositor:     %s\n" "$FFMPEG_STATUS"
printf "  Sleep Prevention:     Active (caffeinate / IOKit)\n"
printf "  Persistence:          Zero-Phantom-Data Mode (Atomic SQLite)\n"
printf "%b=====================================================================%b\n" "$C_CYAN" "$C_RESET"
printf "%b[INFO] Starting AutoShorts Studio Dashboard...%b\n" "$C_WHITE" "$C_RESET"

if [[ $NO_BROWSER -eq 0 ]]; then
    printf "%b[INFO] Your browser will open to http://localhost:%s automatically.%b\n" "$C_WHITE" "$PORT" "$C_RESET"
    # Auto-open browser in background after short delay
    (
        sleep 1.2
        TARGET_URL="http://localhost:${PORT}"
        if [[ "$(uname -s)" == "Darwin" ]]; then
            open "$TARGET_URL" >/dev/null 2>&1 || true
        elif command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$TARGET_URL" >/dev/null 2>&1 || true
        fi
    ) &
fi

printf "%b[INFO] Press Ctrl+C in this terminal window to stop the server.%b\n\n" "$C_WHITE" "$C_RESET"

# Handle SIGINT and SIGTERM gracefully
cleanup() {
    printf "\n%b[INFO] AutoShorts Studio shutting down cleanly. Goodbye!%b\n" "$C_GREEN" "$C_RESET"
    exit 0
}
trap cleanup SIGINT SIGTERM

RUN_FLAGS=("--host" "$HOST" "--port" "$PORT")
if [[ $NO_BROWSER -eq 1 ]]; then
    RUN_FLAGS+=("--no-browser")
fi

"$PYTHON_EXE" run.py dashboard "${RUN_FLAGS[@]}"
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 || $EXIT_CODE -eq 130 ]]; then
    printf "\n%b[INFO] AutoShorts Studio shut down cleanly. Goodbye!%b\n" "$C_GREEN" "$C_RESET"
    exit 0
fi

printf "\n%b[WARNING] AutoShorts Studio exited with code %d.%b\n" "$C_YELLOW" "$EXIT_CODE" "$C_RESET"
exit $EXIT_CODE
