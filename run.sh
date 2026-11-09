#!/usr/bin/env bash
# AutoShorts Studio - Quick Launcher Shortcut
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/start.sh" "$@"
