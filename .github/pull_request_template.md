## Description

Briefly describe the purpose of this PR and what problem or feature it addresses for AutoShorts v1.0.

## Related Issues

Fixes #(issue number)

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Affected Components

- [ ] Dashboard Settings Modal (`autoshorts.core.settings_manager`)
- [ ] YouTube Data API v3 OAuth & Chunked Uploader (`autoshorts.uploader.youtube_api`)
- [ ] Playwright MS Edge Studio Fallback (`autoshorts.uploader.studio_playwright`)
- [ ] Gameplay Backgrounds Fetcher (`autoshorts.media.background_fetcher`)
- [ ] Subtitle Style Presets (`neon` / `minimal` / `pill`) (`autoshorts.media.ass_styler`)
- [ ] Spread Scheduler & Quota Tracker (`autoshorts.scheduler`, `autoshorts.quota`)
- [ ] SemIf Decider & Window Slicer (`autoshorts.decision`)
- [ ] FFmpeg Compositor & Dopamine Effects (`autoshorts.media.compositor`)
- [ ] Safety Cleaner & Path Guard (`autoshorts.cleanup.safety_cleaner`)

## Architectural & Verification Checklist

- [ ] My code adheres to the project coding style (Python 3.12, strict type annotations, ruff/black).
- [ ] Lint checks pass cleanly: `uv run ruff check src/ tests/`
- [ ] Code formatting check passes cleanly: `uv run ruff format --check src/ tests/`
- [ ] All unit and integration tests pass locally (`uv run pytest -q`, 333+ passing tests).
- [ ] Tooling constraints respected:
  - Video downloading strictly via `yt-dlp` (`--download-sections` for precision cuts).
  - Video compositing strictly via direct `FFmpeg` and `ffprobe` subprocesses.
  - Strictly ZERO MoviePy.
  - Zero Playwright video stream downloading (Playwright restricted exclusively to YouTube Studio upload and DOM verification).
- [ ] Zero hardcoded model names: LLM providers resolve dynamically from config/env at runtime (`model: null`).
- [ ] Bounded concurrency (`concurrency = 1`) enforced for compute-heavy workloads.
- [ ] Safety path guards (`assert_safe_to_delete`) and `send2trash` respected for all cleanup operations.
- [ ] No secrets, API keys, OAuth tokens (`data/token.json`), or client secrets are committed.
