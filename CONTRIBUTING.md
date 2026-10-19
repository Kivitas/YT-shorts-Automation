# Contributing to AutoShorts

Thank you for your interest in contributing to AutoShorts!

## Engineering Guidelines & Architectural Constraints

Before submitting contributions, please review our strict architecture principles:

1. **Python Runtime & Standards**:
   - AutoShorts targets **Python 3.12** exclusively (using `uv`).
   - Use strict type annotations throughout the codebase (`typing` module / standard generic syntax).
   - Format and lint code with `ruff` and `black`.
2. **Hardware Constraints**:
   - All components must run efficiently on consumer multi-core CPUs with integrated Intel UHD Graphics.
   - Enforce bounded concurrency (`concurrency = 1`) on video processing and transcription.
3. **Strict Tooling Exclusivity**:
   - **Downloading**: Exclusively `yt-dlp` (`--download-sections` for precision cuts). Zero external downloaders.
   - **Rendering & Compositing**: Exclusively direct `FFmpeg` and `ffprobe` subprocesses. **MoviePy is strictly forbidden**.
   - **Browser Automation**: Exclusively `Playwright` for YouTube Studio fallback upload and verification. Never use Playwright for downloading videos.
4. **Zero Hardcoded Model Names**:
   - Never hardcode LLM model names or paths in source code.
   - Configuration defaults must specify `model: null` to be dynamically resolved to provider defaults at runtime.
5. **Security & Secrets**:
   - Never commit API keys, client secrets, tokens, or personal identifiers.
   - Load credentials exclusively via `.env` or system environment variables.
6. **Automated Testing**:
   - All PRs must include network-independent unit tests under `tests/` leveraging `pytest` and `pytest-mock`.

## Development Workflow

1. Fork and clone the repository.
2. Initialize environment using `uv`:
   ```powershell
   uv venv --python 3.12
   .venv\Scripts\activate
   uv pip install -e ".[dev]"
   ```
3. Run the test suite:
   ```powershell
   pytest
   ```
4. Create a topic branch: `git checkout -b feature/my-feature`.
5. Commit clean, atomic commits following the Conventional Commits specification.
6. Submit a Pull Request.
