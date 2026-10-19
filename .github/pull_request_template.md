## Description

Briefly describe the purpose of this PR and what problem it solves.

## Related Issues

Fixes #(issue number)

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Architectural Checklist

- [ ] My code adheres to the project coding style (Python 3.12, typing, ruff/black).
- [ ] Tooling constraints respected: Video downloading strictly via `yt-dlp`; rendering strictly via `FFmpeg`/`ffprobe`; NO MoviePy; NO Playwright video downloads.
- [ ] Zero hardcoded model names: LLM providers resolve dynamically from config/env at runtime.
- [ ] Concurrency = 1 enforced for compute-intensive tasks.
- [ ] Safety path guards (`assert_safe_to_delete`) and `send2trash` respected for all cleanup operations.
- [ ] All new and existing tests pass locally (`pytest`).
- [ ] No secrets, API keys, OAuth tokens, or client secrets are committed.
