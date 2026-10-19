"""AutoShorts master executable runner.

Usage:
    python run.py                   # Launches Web Dashboard (http://localhost:8765)
    python run.py run --links "..." # Forwards to CLI run command
    python run.py --help            # Forwards to CLI help
"""

import sys

from autoshorts.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Default behavior without CLI arguments: launch dashboard
        sys.exit(main(["dashboard"]))
    else:
        sys.exit(main(sys.argv[1:]))
